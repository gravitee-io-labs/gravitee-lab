"""The images the project runs, read from each service's `image:`: a variable in the tag makes a component, whose
version (and registry, when a variable chooses it) the page sets, and what saving them would have Compose make again.
Also the tags a registry lists, and its login."""

import http.client
import json
import re
import shutil
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import compose, envfiles
from .. import paths
from ..products import AZURE, GHCR
from .jobs import JOBS, JobFailed

TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
REGISTRY = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::\d+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*")
DOCKER_HUB = ("docker.io", "index.docker.io")
ITEM = re.compile(rf"(?:{'|'.join(paths.KINDS)}):{paths.ID}")   # the key of an item's row: "playground:<id>"
AZ = shutil.which("az") or "az"   # the Azure CLI, found once
GH = shutil.which("gh") or "gh"   # the GitHub CLI, found once
OCI_PAGES = 5   # pages of a thousand tags read of a registry's list, which comes by name: the newest are near its end


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


def _bare(default, unset=()):
    """A default as a .env holds it, references without their own defaults: ${OTHER_VERSION:-1.2}-debian becomes
    ${OTHER_VERSION}-debian. A variable of `unset` does not reach the file the default is in: in its place is what
    Compose makes of it there, its own default (nothing without one)."""
    if not default:
        return default
    text = re.sub(r"\$\{(\w+)\}", lambda m: "" if m.group(1) in unset else m.group(0), default)
    return compose.interpolated(text, lambda key: None if key in unset else "${%s}" % key)


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
            "version_key": version[0], "version_default": version[1],
            "registry_key": registry[0] if registry else None,
            "registry_default": registry[1] if registry else None,
            "host": host, "repository": listed,
            "source": None if registry else _locate(repository)[0] or "Docker Hub",
            "services": names, "owner": owner}


def _env_files(services, component):
    """The .env files a component's variables are written to: the core's when it runs one of the core's services, as it
    reaches every file, and comes before what a unit's own says; else the .env of each unit it runs a service of, as a
    unit's .env reaches only that unit's file."""
    found = {paths.unit_env(services[name]["owner"]["kind"], services[name]["owner"]["id"]) or paths.ENV_FILE
             for name in component["services"]}
    return [paths.ENV_FILE] if paths.ENV_FILE in found else sorted(found)


def _unset(services, component, found):
    """The variables of the other components that do not reach this one's file: those written to a unit's .env that is
    not the one of each of its services."""
    mine = set(_env_files(services, component))
    unset = set()
    for other in found:
        theirs = _env_files(services, other)
        if other is not component and paths.ENV_FILE not in theirs and not mine <= set(theirs):
            unset |= {other["version_key"], other["registry_key"]} - {None}
    return unset


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
    version_default, registry_key, registry_default, host, repository, source, services, owner, group, follows}]. The
    services that share a variable are one component, whose value is written to the .env of each (_env_files); a
    default follows another component only where that one's variable reaches its file.
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
    # What a default refers to of another component is that component only where its variable reaches
    unset = {component["id"]: _unset(services, component, found) for component in found}
    for component in found:
        for what in ("version", "registry"):
            component[f"{what}_default"] = _bare(component[f"{what}_default"], unset[component["id"]])
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


def state(project, is_on, outside):
    """What the Images page shows: every component with the value its .env holds (or its default), the image that
    value gives, what runs and what is pulled (None when Docker does not answer); the registries in use; and
    whether a component that is on takes its images from an Azure registry (`azure_in_use`): pulling them takes a
    login. What is on is the caller's to say: `is_on(owner)`, whether the owner of a component runs with it, and
    `outside`, the note of a component that runs without its owner being on (layers.is_on, layers.outside)."""
    services, found = project["services"], components(project)
    local = compose.image_ids()
    containers, _ = compose.containers()   # none when Docker does not answer: the versions still show
    targets = {c["id"]: _env_files(services, c) for c in found}
    envs = {path: envfiles.read(path) for written in targets.values() for path in written}
    listed = []
    for c in found:
        env, first, owner = envs[targets[c["id"]][0]], services[c["services"][0]], c["owner"]
        # A value saved that refers to a variable that does not reach the file is what Compose makes of it there
        unset = _unset(services, c, found)
        configured = sorted({services[name]["image"] for name in c["services"]})
        running = {containers[name]["image"] for name in c["services"]
                   if name in containers and containers[name]["state"] == "running"}
        pulled = None if local is None else all(compose.reference(image) in local for image in configured)
        enabled = is_on(owner)
        listed.append({**c, "version": _bare(env.get(c["version_key"]), unset) or c["version_default"] or "",
                       "registry": _bare(env.get(c["registry_key"]), unset) or c["registry_default"],
                       "version_resolved": first["tag"], "registry_resolved": first["registry"],
                       "configured": configured, "running": sorted(running), "pulled": pulled, "enabled": enabled,
                       # what runs without its owner being on says so, in the words of its owner's row
                       "outside": outside if running and not enabled else None})
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
    """Versions and registries as the .env files that hold each write them, ({.env: {variable: value, None for the
    default}}, {variable: value}): the core's, or each unit's whose services share it (_env_files). A value may be a
    tag, a registry, or the reference that follows another component; anything else is refused."""
    services, found, changes, given = project["services"], components(project), {}, {}
    targets = {c["id"]: _env_files(services, c) for c in found}
    for key, value in values.items():
        component, what = _setting(found, key)
        default = component[f"{what}_default"]
        references = {default} | {follow[what] for follow in component["follows"] if what in follow}
        pattern = TAG if what == "version" else REGISTRY
        if not isinstance(value, str) or (value not in references and not pattern.fullmatch(value)):
            raise ValueError(f"Invalid {'tag' if what == 'version' else 'registry'} for {key}")
        for path in targets[component["id"]]:
            changes.setdefault(path, {})[key] = None if value == default else value
        given[key] = value
    # A line that follows another component as its default does, ${APIM_REGISTRY}, goes too: the default needs no line,
    # and Compose would read it blank, with a warning, once the variable it follows has no line either
    envs, cleared = {}, {}
    for component in found:
        for what in ("version", "registry"):
            key, default = component[f"{what}_key"], component[f"{what}_default"]
            if not key or key in given or not default or "${" not in default:
                continue
            for path in targets[component["id"]]:
                if envs.setdefault(path, envfiles.read(path)).get(key) == default:
                    changes.setdefault(path, {})[key] = None
                    cleared[key] = default
    return changes, {**given, **cleared}


def save(project, values):
    """Writes versions and registries to the .env files that hold each: the core's, or each unit's whose services share
    it. A value may be a tag, a registry, or the reference that follows another component. The default needs no
    line."""
    for path, change in _changes(project, values)[0].items():
        envfiles.write(path, change)


def as_saved(project, values):
    """These versions and registries as save() would write them, for what Compose would make of them (files.drafted):
    (the change of the core's .env or None, {an add-on's .env: its values}), each add-on's by its own .env, which Compose
    reads for every file and which another add-on's must not reach."""
    changes, given = _changes(project, values)
    core = changes.pop(paths.ENV_FILE, None)
    return core, {path: {key: given[key] for key in change} for path, change in changes.items()}


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

def web_error(error, host):
    """Why a request to `host` got no answer it could use, in plain words: the network, a rate limit, a refusal, an
    error of the site. Where registries and the download site are asked, so that what fails never shows urllib's
    words."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429:
            return f"Too many requests to {host}: try again later"
        if error.code in (401, 403):
            return f"{host} refuses the request"
        if error.code == 404:
            return f"Not found on {host}"
        return f"{host} answered an error (HTTP {error.code})"
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"{host} does not answer in time"
    if isinstance(reason, ssl.SSLError):
        return f"The certificate of {host} is not trusted"
    return f"Cannot reach {host}"


def _json(url):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as response:
        return json.load(response)


def _hub_tags(repository):
    """Docker Hub's own API: the latest 300 tags, with their dates."""
    tags, url = [], f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100&ordering=last_updated"
    for _ in range(3):
        page = _json(url)
        tags += [{"name": tag["name"], "updated": tag.get("last_updated")} for tag in page["results"]]
        url = page.get("next")
        if not url:
            break
    return tags


def _realm(said):
    """The address where anyone gets a token, from the WWW-Authenticate a registry answers a request without one with;
    None when it names none."""
    asked = dict(re.findall(r'(\w+)="([^"]*)"', said or ""))
    if not asked.get("realm", "").startswith("https://"):
        return None
    query = urllib.parse.urlencode({key: asked[key] for key in ("service", "scope") if key in asked})
    return f"{asked['realm']}?{query}"


def _tags_page(url, token=None):
    """A page of a registry's list of tags: (its names, the address of the next page, None after the last)."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=15) as response:
        following = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', response.headers.get("Link") or "")
        listed = json.load(response).get("tags") or []
    return listed, urllib.parse.urljoin(url, following.group(1)) if following else None


def _oci_tags(host, repository):
    """The registry API every registry speaks: names only, in the order it lists them, by name (the page orders the
    releases). A long list comes in pages, of which OCI_PAGES are read; a registry that wants a token says where anyone
    gets one."""
    url, token, names, pages = f"https://{host}/v2/{repository}/tags/list?n=1000", None, [], 0
    while url and pages < OCI_PAGES:
        try:
            listed, url = _tags_page(url, token)
        except urllib.error.HTTPError as error:
            realm = _realm(error.headers.get("WWW-Authenticate")) if error.code == 401 and token is None else None
            if not realm:
                raise
            granted = _json(realm)
            token = granted.get("token") or granted.get("access_token") or ""
            continue
        names += listed
        pages += 1
    return [{"name": name, "updated": None} for name in names if not name.startswith("sha256-")]


SUBSCRIPTIONS = {}   # the Azure subscription that holds each registry, by name, as found once per run of the Lab UI


def _az():
    """The Azure CLI; an error that says to install it when this machine has none."""
    if not shutil.which(AZ):
        raise RuntimeError("Install the Azure CLI (az)")
    return AZ


def _subscription(name):
    """The flags that name the Azure subscription holding registry `name`: none when the session's own subscription has
    it, else the first of the account's subscriptions that does (a registry is often in another one than the default).
    Kept once found: not found, it is looked for again, as an expired session finds none."""
    if name not in SUBSCRIPTIONS:
        rc, _, _ = compose.run([_az(), "acr", "show", "--name", name, "--query", "id", "-o", "tsv"], timeout=60)
        if rc == 0:
            SUBSCRIPTIONS[name] = None
        else:
            rc, out, _ = compose.run([AZ, "account", "list", "--query", "[].id", "-o", "tsv"], timeout=60)
            for subscription in out.split() if rc == 0 else []:
                if compose.run([AZ, "acr", "show", "--name", name, "--subscription", subscription, "--query", "id",
                                "-o", "tsv"], timeout=60)[0] == 0:
                    SUBSCRIPTIONS[name] = subscription
                    break
    return ["--subscription", SUBSCRIPTIONS[name]] if SUBSCRIPTIONS.get(name) else []


AZ_OFFLINE = ("Failed to establish a new connection", "Max retries exceeded", "Cannot connect to proxy",
              "Failed to resolve", "NameResolutionError", "Connection refused")   # what the Azure CLI says offline


def _azure_error(host, rc, err):
    """Why the Azure CLI could not list the tags of `host`, in the words web_error() has for the network: its own text
    never shows. An expired session is said once the network is not the cause: a token renewed offline fails at
    login.microsoftonline.com."""
    if any(marker in err for marker in AZ_OFFLINE):
        return f"Cannot reach {host}"
    if rc == 124 or "timed out" in err.lower():
        return f"{host} does not answer in time"
    if "az login" in err or "AADSTS" in err:
        return "Azure session expired: run az login in a terminal"
    return f"The Azure CLI could not list the tags of {host}"


def _azure_tags(host, repository):
    """An Azure registry lists its tags to the Azure CLI's session, newest first. That session is the Azure CLI's own:
    when it expired, `az login` renews it, which the page's Log in (Docker's login to the registry) cannot."""
    name = host.split(".")[0]
    rc, out, err = compose.run([_az(), "acr", "repository", "show-tags", "--name", name, *_subscription(name),
                                "--repository", repository, "--orderby", "time_desc", "--top", "300", "--detail",
                                "-o", "json"], timeout=90)
    if rc != 0:
        raise RuntimeError(_azure_error(host, rc, err))
    # Builds of dependency updates are not versions anyone picks
    return [{"name": tag["name"], "updated": tag.get("lastUpdateTime")} for tag in json.loads(out)
            if not tag["name"].startswith("renovate")]


def tags(project, component, registry=None):
    """The tags a component can run: [{name, updated}], newest first when the registry tells. A Gravitee component
    is listed on `registry`, the one it runs from when none is given. A registry that does not answer says why in
    plain words (web_error)."""
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
        try:
            if host is None:
                return _hub_tags(repository)
            return _azure_tags(host, repository) if AZURE.fullmatch(host) else _oci_tags(host, repository)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise RuntimeError(web_error(error, host or "Docker Hub")) from error
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
        code, _ = _status(f"https://hub.docker.com/v2/repositories/{repository}/")
        return True if code == 200 else False if code == 404 else None
    url = f"https://{host}/v2/{repository}/manifests/{reference}"
    code, asked = _status(url)
    if code in (200, 404):   # a manifest it does not have is no login it asks for
        return True
    if code != 401:
        return False if code == 403 else None
    realm = _realm(asked)
    if not realm:
        return False
    try:
        granted = _json(realm)
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, OSError, ValueError):
        return None
    code, _ = _status(url, granted.get("token") or granted.get("access_token"))
    return True if code in (200, 404) else False if code in (401, 403) else None


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

        def public():
            found = _anonymous(*where)
            if found is None:   # the registry cannot tell: asked again the next time
                raise LookupError(host)
            return found
        try:
            return compose.CACHE.get(f"anonymous:{host}:{repository}" + (f":{reference}" if host else ""), 3600, public)
        except LookupError:
            return None
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
    """Logs Docker in to a registry with the session of its CLI: an Azure registry with the Azure CLI's, in one command
    that says nothing of how far it is, GitHub's with the GitHub CLI's (_github_login). Its job is the registry's
    (`registry:<host>`, Images' Log in), or the item's whose pull the registry refused (`key`, "<kind>:<id>": the Log
    in of its row's menu and of its failed start), which its row's button shows."""
    if host != GHCR and not AZURE.fullmatch(host or ""):
        raise ValueError("Not a registry the Lab UI logs in to")
    if key is not None and not ITEM.fullmatch(str(key)):
        raise ValueError("Not an add-on, a setup or a playground")
    if host == GHCR:
        return JOBS.start("Log in to GitHub registry", [_github_login(_gh())], key=key or f"registry:{host}", verb="login")
    name = host.split(".")[0]
    return JOBS.start("Log in to Azure registry", [[_az(), "acr", "login", "--name", name, *_subscription(name)]],
                      key=key or f"registry:{host}", verb="login")


# ---------------------------------------------------------------------------------------------------------------------
# GitHub's registry
# ---------------------------------------------------------------------------------------------------------------------

GITHUB = "github.com"   # where the GitHub CLI's session is
PACKAGES = re.compile(r"\b(read|write):packages\b")   # the scope that lets a token pull from ghcr.io; write holds read
SCOPES = re.compile(r"^X-Oauth-Scopes:(.*)$", re.M | re.I)   # the scopes of a classic token, which GitHub's API says


def _gh():
    """The GitHub CLI; an error that says to install it when this machine has none."""
    if not shutil.which(GH):
        raise RuntimeError("Install the GitHub CLI (gh)")
    return GH


def _github_login(gh):
    """The step of a login to ghcr.io. The GitHub CLI signs in, or adds the right to read packages to a session that
    lacks it, as `gh auth login` does it without a terminal: it copies a one-time code, opens github.com/login/device
    in the browser, where the person pastes it, and waits. A fine-grained token says no scopes: it is tried as it is.
    Its token then goes to `docker login` on standard input, in no argument and no line of the job."""
    def step(job):
        rc, out, _ = compose.run([gh, "api", "--include", "user"], timeout=30)
        scopes = SCOPES.search(out)
        if rc != 0:
            job.exec([gh, "auth", "login", "--hostname", GITHUB, "--web", "--scopes", "read:packages"])
        elif scopes and not PACKAGES.search(scopes.group(1)):
            job.exec([gh, "auth", "refresh", "--hostname", GITHUB, "--scopes", "read:packages"])
        rc, user, err = compose.run([gh, "api", "user", "--jq", ".login"], timeout=30)
        if rc == 0:
            rc, token, err = compose.run([gh, "auth", "token", "--hostname", GITHUB], timeout=30)
        if rc != 0:
            raise JobFailed(compose._last_line(err, "The GitHub CLI has no session"))
        cmd = ["docker", "login", GHCR, "--username", user.strip(), "--password-stdin"]
        job.lines.append(f"$ {' '.join(cmd)}")
        rc, out, err = compose.run(cmd, timeout=60, given=token.strip())
        job.lines.extend(line for line in (out + err).splitlines() if line.strip())
        if rc != 0:
            raise JobFailed(compose._last_line(err, f"docker login failed with exit code {rc}"))
    return step
