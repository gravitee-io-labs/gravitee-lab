"""The images the project runs, read from each service's `image:`: a variable in the tag makes a component, whose
version (and registry, when a variable chooses it) the page sets, and what saving them would have Compose make again.
Also the tags a registry lists, and its login."""

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

from . import compose, envfiles, files
from ..library import layers
from .. import paths
from .jobs import JOBS

TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
REGISTRY = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::\d+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*")
AZURE = re.compile(r"[a-z0-9]+\.azurecr\.io")
DOCKER_HUB = ("docker.io", "index.docker.io")
ITEM = re.compile(rf"(?:{'|'.join(paths.KINDS)}):{paths.ID}")   # the key of an item's row: "playground:<id>"
AZ = shutil.which("az") or "az"   # the Azure CLI, found once


# ---------------------------------------------------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------------------------------------------------

def _reference(text):
    """(variable, default) when the text is exactly ${VARIABLE}, ${VARIABLE:-default} or ${VARIABLE-default}."""
    for key, default, _ in compose.references(text)[:1]:
        if text in ("${%s}" % key, "${%s:-%s}" % (key, default), "${%s-%s}" % (key, default)):
            return key, default
    return None


def _parts(raw):
    """An image as written: (registry, repository, version). Registry and version are (variable, default), or None
    when no variable chooses them."""
    slashes, colons = compose.outside(raw, "/"), compose.outside(raw, ":")
    cut = colons[-1] if colons and colons[-1] > (slashes or [-1])[-1] else len(raw)
    name, version = raw[:cut], _reference(raw[cut + 1:])
    registry = _reference(name[:slashes[0]]) if slashes else None
    return registry, (name[slashes[0] + 1:] if registry else name), version


def _bare(default):
    """A default as a .env holds it, references without their own defaults: ${OTHER_VERSION:-1.2}-debian becomes
    ${OTHER_VERSION}-debian."""
    for key, inner, _ in compose.references(default or ""):
        for form in ("${%s:-%s}", "${%s-%s}"):
            default = default.replace(form % (key, inner), "${%s}" % key)
    return default


def _locate(name):
    """Where Docker finds an image: (registry host, repository). A first segment with a dot or a colon is a host;
    without one the image is on Docker Hub (host None), under library/ when it has no namespace."""
    first, _, rest = name.partition("/")
    host = first if rest and ("." in first or ":" in first or first == "localhost") else None
    path = rest if host else name
    if host is None or host in DOCKER_HUB:
        return None, path if "/" in path else f"library/{path}"
    return host, path


def _component(services, names):
    """The component a version variable makes of the services that share it, described by the first of them."""
    first = services[names[0]]
    registry, repository, version = _parts(first["raw_image"])
    # A Gravitee image is listed where its registry is; another one where it is pulled from, or where a label says
    pin = next((services[name]["image_tags"] for name in names if services[name]["image_tags"]), repository)
    host, listed = (None, repository) if registry else _locate(pin)
    owner = first["owner"]
    return {"id": version[0], "label": ", ".join(dict.fromkeys(services[name]["label"] for name in names)),
            "detail": owner["name"], "kind": "gravitee" if registry else "third-party",
            "version_key": version[0], "version_default": _bare(version[1]),
            "registry_key": registry[0] if registry else None,
            "registry_default": _bare(registry[1]) if registry else None,
            "host": host, "repository": listed,
            "source": None if registry else _locate(repository)[0] or "Docker Hub",
            "services": names, "owner": owner}


def _follows(component, others):
    """The components this one runs the version of unless it is given its own, its default being their variable:
    [{id, version}], with `registry` when it takes theirs too; a value is the reference that follows them."""
    used = {what: {key for key, _, _ in compose.references(component[f"{what}_default"] or "")}
            for what in ("version", "registry")}
    follows = []
    for other in others:
        if other is not component and other["id"] in used["version"]:
            follow = {"id": other["id"], "version": component["version_default"]}
            if other["registry_key"] in used["registry"]:
                follow["registry"] = component["registry_default"]
            follows.append(follow)
    return follows


def components(project):
    """The images whose version is a variable: [{id (the version variable), label, detail, kind, version_key,
    version_default, registry_key, registry_default, host, repository, source, services, owner, group, follows}].
    `group` is the group of the platform a component belongs to: its owner's, or for the gateway of an add-on the
    group that runs its image (project._gateway_groups); None for the others. The platform's come first, in the order
    of its groups, each followed by the add-ons' gateways of it, then the others by the name of what owns them; under
    one owner, the component most services share comes first."""
    services, sharing = project["services"], {}
    for name in sorted(services):
        version = _parts(services[name]["raw_image"])[2]
        if version:
            sharing.setdefault(version[0], []).append(name)
    found = [_component(services, names) for names in sharing.values()]
    for component in found:
        component["follows"] = _follows(component, found)
    groups = [group["id"] for group in project["groups"]]
    named = {group["id"]: group.get("name") for group in project["groups"]}
    # The gateway of an add-on is a gateway of the group that runs its image (project._gateway_groups): its component
    # comes after that group's own, and says the group when the add-on is named for it ("Hybrid gateway", API Management)
    joined = {addon["gateway"]["service"]: addon["gateway"].get("group") for addon in project["addons"].values()
              if addon.get("gateway")}
    for component in found:
        owner = component["owner"]
        platform = owner["kind"] == "platform" and owner["id"] in named
        component["group"] = owner["id"] if platform else next(
            (joined[name] for name in component["services"] if joined.get(name) in named), None)
        if not platform and named.get(component["group"]) and component["detail"] == component["label"]:
            component["detail"] = named[component["group"]]

    def rank(component):
        owner, group = component["owner"], component["group"]
        return (group is None, groups.index(group) if group else 0, owner["kind"] != "platform", owner["name"].lower(),
                -len(component["services"]), component["services"][0])
    return sorted(found, key=rank)


# ---------------------------------------------------------------------------------------------------------------------
# State and saving
# ---------------------------------------------------------------------------------------------------------------------

def _env_file(component):
    """The .env that holds a component's variables: the add-on's own, else the core's."""
    owner = component["owner"]
    return paths.unit_env(owner["kind"], owner["id"]) or paths.ENV_FILE


def _registries(project, found):
    """The registries Gravitee images come from, the defaults first: the ones the page offers."""
    gravitee = [c for c in found if c["registry_key"]]
    defaults = [c["registry_default"] for c in gravitee]
    current = [project["services"][c["services"][0]]["registry"] for c in gravitee]
    # A default that follows another component is a reference, not a registry
    return list(dict.fromkeys(registry for registry in [*defaults, *current] if registry and "$" not in registry))


def registry_host(registry):
    """Where a registry is: its host, None for Docker Hub (a value that is only a namespace is there too)."""
    return _locate(f"{registry}/image")[0]


def registry_label(registry):
    """What the page calls a registry: where it is hosted when the value is only a namespace, else its host."""
    host = registry_host(registry)
    return "Docker Hub" if host is None else "Azure" if AZURE.fullmatch(host) else host


def local_images():
    """The images Docker holds, as repository:tag; None when Docker does not answer."""
    def ask():
        rc, out, _ = compose.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
        return set(out.split()) if rc == 0 else None
    return compose.CACHE.get("local:images", 10, ask)


def state(project):
    """What the Images page shows: every component with the value its .env holds (or its default), the image that
    value gives, what runs and what is pulled (None when Docker does not answer); the registries in use; and
    whether a component that is on takes its images from an Azure registry (`azure_in_use`): pulling them takes a
    login. A component that runs without its owner being on has its note (`outside`, layers.outside)."""
    services, found = project["services"], components(project)
    on, local = layers.current(project), local_images()
    containers, _ = compose.containers()   # none when Docker does not answer: the versions still show
    envs = {path: envfiles.read(path) for path in {_env_file(c) for c in found}}
    listed = []
    for c in found:
        env, first, owner = envs[_env_file(c)], services[c["services"][0]], c["owner"]
        configured = sorted({services[name]["image"] for name in c["services"]})
        running = {containers[name]["image"] for name in c["services"]
                   if name in containers and containers[name]["state"] == "running"}
        # docker images lists Docker Hub images without docker.io/ and library/
        pulled = None if local is None else all(re.sub(r"^docker\.io/(library/)?", "", image) in local
                                                for image in configured)
        enabled = layers.is_on(owner["kind"], owner["id"], on["playground"], on["effective"])
        listed.append({**c, "version": env.get(c["version_key"]) or c["version_default"] or "",
                       "registry": env.get(c["registry_key"]) or c["registry_default"],
                       "version_resolved": first["tag"], "registry_resolved": first["registry"],
                       "configured": configured, "running": sorted(running), "pulled": pulled, "enabled": enabled,
                       # what runs without its owner being on says so, in the words of its owner's row
                       "outside": layers.outside(owner["kind"]) if running and not enabled else None})
    return {"components": listed,
            "registries": [{"value": r, "label": registry_label(r)} for r in _registries(project, found)],
            "azure_in_use": any(c["enabled"] and _azure(c["registry_resolved"]) for c in listed if c["registry_key"])}


def _azure(registry):
    return bool(registry) and bool(AZURE.fullmatch(registry_host(registry) or ""))


def _setting(found, key):
    """What a variable sets: (component, "version" or "registry")."""
    for component in found:
        for what in ("version", "registry"):
            if key and component[f"{what}_key"] == key:
                return component, what
    raise ValueError(f"{key} cannot be changed here")


def _changes(project, values):
    """Versions and registries as the .env that owns each holds them, ({.env: {variable: value, None for the
    default}}, {variable: value}): the core's, or the add-on's. A value may be a tag, a registry, or the reference
    that follows another component; anything else is refused."""
    found, changes, given = components(project), {}, {}
    for key, value in values.items():
        component, what = _setting(found, key)
        default = component[f"{what}_default"]
        references = {default} | {follow[what] for follow in component["follows"] if what in follow}
        pattern = TAG if what == "version" else REGISTRY
        if not isinstance(value, str) or (value not in references and not pattern.fullmatch(value)):
            raise ValueError(f"Invalid {'tag' if what == 'version' else 'registry'} for {key}")
        changes.setdefault(_env_file(component), {})[key] = None if value == default else value
        given[key] = value
    # A line that follows another component as its default does, ${APIM_REGISTRY}, goes too: the default needs no line,
    # and Compose would read it blank, with a warning, once the variable it follows has no line either
    envs = {}
    for component in found:
        for what in ("version", "registry"):
            key, default, path = component[f"{what}_key"], component[f"{what}_default"], _env_file(component)
            env = envs.setdefault(path, envfiles.read(path))
            if key and key not in given and default and "${" in default and env.get(key) == default:
                changes.setdefault(path, {})[key] = None
                given[key] = default
    return changes, given


def save(project, values):
    """Writes versions and registries to the .env that owns each: the core's, or the add-on's. A value may be a tag,
    a registry, or the reference that follows another component. The default needs no line."""
    for path, change in _changes(project, values)[0].items():
        envfiles.write(path, change)


def drafted(project, values):
    """What Compose would make each service of what is on from, were these versions and registries saved as save()
    writes them: (hashes, exact), as files.drafted() says it."""
    changes, given = _changes(project, values)
    core = changes.pop(paths.ENV_FILE, None)
    return files.drafted(project, core, {path: {key: given[key] for key in change} for path, change in changes.items()})


def changing(project, values):
    """The services these versions and registries are for, and those of the components that follow theirs: what
    saving them may have Compose make again."""
    found = components(project)
    touched = {_setting(found, key)[0]["id"] for key in values}
    touched |= {c["id"] for c in found if any(follow["id"] in touched for follow in c["follows"])}
    return sorted({name for c in found if c["id"] in touched for name in c["services"]})


# ---------------------------------------------------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------------------------------------------------

def _json(url, token=None):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def _hub_tags(repository):
    """Docker Hub's own API: the latest 300 tags, with their dates."""
    tags, url = [], f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100&ordering=last_updated"
    for _ in range(3):
        try:
            page = _json(url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise RuntimeError("Not published on Docker Hub") from error
            raise
        tags += [{"name": tag["name"], "updated": tag.get("last_updated")} for tag in page["results"]]
        url = page.get("next")
        if not url:
            break
    return tags


def _oci_tags(host, repository):
    """The registry API every registry speaks: names only, oldest first. A registry that wants a token says where
    anyone gets one."""
    url = f"https://{host}/v2/{repository}/tags/list?n=1000"
    try:
        names = _json(url).get("tags") or []
    except urllib.error.HTTPError as error:
        asked = dict(re.findall(r'(\w+)="([^"]*)"', error.headers.get("WWW-Authenticate") or ""))
        if error.code != 401 or not asked.get("realm", "").startswith("https://"):
            raise
        query = urllib.parse.urlencode({key: asked[key] for key in ("service", "scope") if key in asked})
        granted = _json(f"{asked['realm']}?{query}")
        names = _json(url, granted.get("token") or granted.get("access_token")).get("tags") or []
    return [{"name": name, "updated": None} for name in reversed(names) if not name.startswith("sha256-")]


def _azure_tags(host, repository):
    """An Azure registry lists its tags to the Azure CLI's session, newest first."""
    rc, out, err = compose.run([AZ, "acr", "repository", "show-tags", "--name", host.split(".")[0],
                                "--repository", repository, "--orderby", "time_desc", "--top", "300", "--detail",
                                "-o", "json"], timeout=90)
    if rc != 0:
        if "login" in err.lower() or "credential" in err.lower():
            raise RuntimeError("Azure session expired: use Log in")
        raise RuntimeError(err.strip().splitlines()[-1][:200] if err.strip() else "Azure registry not reachable")
    # Builds of dependency updates are not versions anyone picks
    return [{"name": tag["name"], "updated": tag.get("lastUpdateTime")} for tag in json.loads(out)
            if not tag["name"].startswith("renovate")]


def tags(project, component, registry=None):
    """The tags a component can run: [{name, updated}], newest first when the registry tells. A Gravitee component
    is listed on `registry`, the one it runs from when none is given."""
    c = next((found for found in components(project) if found["id"] == component), None)
    if c is None:
        raise paths.NotFound(f"No such component: {component}")
    host, repository = c["host"], c["repository"]
    if c["registry_key"]:
        registry = registry or project["services"][c["services"][0]]["registry"] or ""
        if not REGISTRY.fullmatch(registry):
            raise ValueError("Invalid registry")
        host, repository = _locate(f"{registry}/{repository}")

    def ask():
        if host is None:
            return _hub_tags(repository)
        return _azure_tags(host, repository) if AZURE.fullmatch(host) else _oci_tags(host, repository)
    return compose.CACHE.get(f"tags:{host}:{repository}", 600, ask)


MANIFESTS = ("application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,"
             "application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json")


def _split(image):
    """An image as (name, reference): its tag or digest, latest when it says none."""
    if "@" in image:
        name, digest = image.split("@", 1)
        return name, digest
    slash, colon = image.rfind("/"), image.rfind(":")
    return (image[:colon], image[colon + 1:]) if colon > slash else (image, "latest")


def _status(url, token=None):
    """The HTTP status of a HEAD, and the response's WWW-Authenticate; (None, "") when the registry does not answer."""
    headers = {"Accept": MANIFESTS, **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="HEAD"), timeout=8) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("WWW-Authenticate") or ""
    except (urllib.error.URLError, OSError, ValueError):
        return None, ""


def _anonymous(host, repository, reference="latest"):
    """Whether anyone may pull the image: True, False, or None when the registry cannot tell (unreachable, an answer this does
    not know). Docker Hub says it of a repository on its own API; another registry is asked as `docker pull` asks it, for the
    manifest of that very image, without a login and then with the token it gives to anyone who asks. A registry may let anyone
    pull and still keep its list of tags to itself: the list is never what is asked."""
    if host is None:
        code, _ = _status_get(f"https://hub.docker.com/v2/repositories/{repository}/")
        return True if code == 200 else False if code == 404 else None
    url = f"https://{host}/v2/{repository}/manifests/{reference}"
    code, asked = _status(url)
    if code in (200, 404):   # a manifest it does not have is no login it asks for
        return True
    if code != 401:
        return False if code == 403 else None
    said = dict(re.findall(r'(\w+)="([^"]*)"', asked))
    if not said.get("realm", "").startswith("https://"):
        return False
    query = urllib.parse.urlencode({key: said[key] for key in ("service", "scope") if key in said})
    try:
        granted = _json(f"{said['realm']}?{query}")
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, OSError, ValueError):
        return None
    code, _ = _status(url, granted.get("token") or granted.get("access_token"))
    return True if code in (200, 404) else False if code in (401, 403) else None


def _status_get(url):
    """The HTTP status of a GET of Docker Hub's API; None when it does not answer."""
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return None, ""


def private_images(images):
    """{registry: [images]} of the images, among `images`, that a person with no login cannot pull: the registry as the page
    names it (its host, or "Docker Hub"). An image a registry does not answer about is left out: nothing is said that is not
    known. Each image is asked once an hour, all at once."""
    from concurrent.futures import ThreadPoolExecutor
    asked = {}
    for image in dict.fromkeys(images):
        if image:
            name, reference = _split(image)
            asked[image] = (*_locate(name), reference)

    def ask(where):
        host, repository, reference = where
        key = f"anonymous:{host}:{repository}" + (f":{reference}" if host else "")
        return compose.CACHE.get(key, 3600, lambda: _anonymous(*where))
    places = list(dict.fromkeys(asked.values()))
    with ThreadPoolExecutor(max_workers=8) as pool:
        public = dict(zip(places, pool.map(ask, places)))
    found = {}
    for image, where in asked.items():
        if public[where] is False:
            found.setdefault("Docker Hub" if where[0] is None else where[0], []).append(image)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Azure registries
# ---------------------------------------------------------------------------------------------------------------------

def _can_pull(image):
    """Asks the registry about an image with Docker's credentials. A tag it does not have still proves the login."""
    rc, _, err = compose.run(["docker", "buildx", "imagetools", "inspect", "--raw", image], timeout=25)
    return rc == 0 or "not found" in err.lower()


def registry_status(project):
    """Whether Docker can pull from the Azure registries in use, and the Azure account that lists their tags:
    {host, label, account, registry_login}. `host` is the registry to log in to; None when none is in use."""
    found = components(project)
    hosts = [registry for registry in _registries(project, found) if AZURE.fullmatch(registry)]
    if not hosts:
        return {"host": None, "label": None, "account": None, "registry_login": False}
    probe = next(c for c in found if c["registry_key"])   # any Gravitee image: the first one

    def ask():
        rc, out, _ = compose.run([AZ, "account", "show", "--query", "user.name", "-o", "tsv"], timeout=20)
        tag = project["services"][probe["services"][0]]["tag"]
        logins = {host: _can_pull(f"{host}/{probe['repository']}:{tag}") for host in hosts}
        host = next((host for host in hosts if not logins[host]), hosts[0])
        return {"host": host, "label": registry_label(host), "account": out.strip() if rc == 0 else None,
                "registry_login": all(logins.values())}
    key = "local:registry:" + ",".join(hosts)   # a job may be a login: asked again after every job
    return compose.CACHE.get(key, 300, ask)


def login(host, key=None):
    """Logs Docker in to an Azure registry with the Azure CLI's session: one command that says nothing of how far it
    is. Its job is the registry's (`registry:<host>`, Images' Log in), or the item's whose pull the registry refused
    (`key`, "<kind>:<id>": the Log in of its row's menu), which its row's button shows."""
    if not AZURE.fullmatch(host or ""):
        raise ValueError("Not an Azure registry")
    if key is not None and not ITEM.fullmatch(str(key)):
        raise ValueError("Not an add-on, a setup or a playground")
    return JOBS.start("Log in to Azure registry", [[AZ, "acr", "login", "--name", host.split(".")[0]]],
                      key=key or f"registry:{host}", verb="login")
