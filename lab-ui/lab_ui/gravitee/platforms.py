"""The remote Gravitee platforms this machine knows: a Gravitee Cloud with the Account Token to reach it, the Cloud
Token of each of its environments, and the hybrid gateways the Lab creates there. A record (name, kind, address, account) is
kept in .lab/platforms.json, out of git. The tokens are settings of the core's .env, where the secrets of this machine
are, and no answer holds one: PLATFORM_<ID>_TOKEN (the Account Token), PLATFORM_<ID>_ENV_<ENVIRONMENT>_TOKEN (a Cloud
Token) and PLATFORM_<ID>_GATEWAY_<GATEWAY>_TOKEN (the token of a gateway the Lab created). The gateway that runs here is
the one whose token the add-on holds. An Account Token reaches only its own account, by the Account ID that the person
gives."""

import hashlib
import json
import re
import shutil
import threading
import time
from urllib.parse import urlsplit

from . import cloud, license
from ..docker import envfiles
from .. import paths
from ..catalogs import resolve

FILE = ".lab/platforms.json"   # [record], relative to the project
STATE = ".state"   # in a playground's folder, what it recorded; a platform has a folder of its id in it
KINDS = {"cloud": "https://eu.cloud.gravitee.io"}   # a kind of platform and its address, the EU region of the Cloud
# The scope of a gateway, as the Cloud says it, and the id the project gives that product (its plugins folder, the
# `product` of a group of the core), whose group names it
SCOPES = {"APIM": "apim", "AM": "am"}
GAMMA = "Gamma"   # the product the plan of an account enables (its subscription says gammaEnabled), which no gateway scope names
CLOUD_HOST = ".cloud.gravitee.io"   # a platform whose address ends so is Gravitee Cloud: its Cloud Gate is by region
GATE_URL = "https://{}.cloudgate.gravitee.io"
ACCOUNT_ID = re.compile(r"[A-Za-z0-9-]{1,64}")   # one segment of a path; the Cloud's are UUIDs
# What the Cloud calls an environment or a gateway is text that reaches a path, a setting name and a folder: it is one
# segment of letters, digits and dashes, or it is left out
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}")
NAME_LIMIT = 60
_lock = threading.Lock()
_refused = set()   # (platform, environment) of the Cloud Tokens that Cloud Gate refused when it was last asked
KEEP = 300         # seconds that what the Cloud says of what it offers (its plan, its license, its plugins) is kept
_kept = {}         # what was read, by what it is: (when, the answer)


# ---------------------------------------------------------------------------------------------------------------------
# What is kept
# ---------------------------------------------------------------------------------------------------------------------

def _valid(item):
    account = item.get("account") if isinstance(item, dict) else None
    return isinstance(account, dict) and isinstance(account.get("id"), str) \
        and all(isinstance(item.get(key), str) for key in ("id", "name", "kind", "url"))


def read():
    """The records kept, none when the file is missing or does not read as such."""
    try:
        found = json.loads((paths.ROOT / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = []
    return [{**{key: item[key] for key in ("id", "name", "kind", "url")},
             "account": {key: str(item["account"].get(key) or "") for key in ("id", "hrid", "name")}}
            for item in (found if isinstance(found, list) else []) if _valid(item)]


def _write(records):
    target = paths.ROOT / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(target, json.dumps(records, indent=2) + "\n")


def _id(value):
    """An id of the Cloud, or the words that refuse it."""
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Not an id of Gravitee Cloud")
    return value


def _part(text):
    return text.upper().replace("-", "_")


def _key(uid, what=None, item=None):
    """The setting that keeps a token of a platform: its Account Token, a Cloud Token (what is ENV and the item an
    environment) or the token of a gateway (GATEWAY)."""
    return "_".join(["PLATFORM", _part(uid), *([what, _part(_id(item))] if what else []), "TOKEN"])


def _owned(uid, env, ids):
    """The settings of a .env that keep something of a platform. A platform whose id begins with this one's
    (gravitee-cloud-2 after gravitee-cloud) has its own."""
    def owns(mine, key):
        return re.fullmatch(rf"PLATFORM_{_part(mine)}_(?:TOKEN|(?:ENV|GATEWAY)_[A-Z0-9_]+_TOKEN)", key)
    return [key for key in env if owns(uid, key)
            and not any(len(other) > len(uid) and owns(other, key) for other in ids)]


def find(uid):
    """The record of a platform."""
    for record in read():
        if record["id"] == uid:
            return record
    raise paths.NotFound(f"No platform {uid}")


def _client(record):
    """The Cloud of a record, with the Account Token kept for it."""
    token = envfiles.read(paths.ENV_FILE).get(_key(record["id"]), "")
    if not token:
        raise cloud.CloudError(f"No token is kept for {record['name']}: register it again", "invalid")
    try:
        return cloud.Cloud(record["url"], token)
    except ValueError as error:
        raise cloud.CloudError(str(error), "invalid") from None


def listing():
    """The records, each saying whether its token is kept. Nothing is asked of the platform."""
    env = envfiles.read(paths.ENV_FILE)
    return {"platforms": [{**record, "tokenSet": bool(env.get(_key(record["id"])))} for record in read()]}


# ---------------------------------------------------------------------------------------------------------------------
# Register and forget
# ---------------------------------------------------------------------------------------------------------------------

def _why(error):
    """What failed in a few words, never its message, which could hold anything: the system's reason, else the type."""
    return (error.strerror if isinstance(error, OSError) else None) or type(error).__name__


def _account(client, wanted):
    """The account with this id, as the Cloud says it: {id, hrid, name}. A token that works only on the paths of its
    account is proven by asking for that account. The Cloud refuses with a 401, 403 or 404 without saying whether the
    token or the id is wrong, and neither does this."""
    try:
        found = client.account(wanted)
    except cloud.CloudError as error:
        if error.status in (401, 403, 404):
            raise cloud.CloudError("The token or the Account ID is not right", "invalid", error.status) from None
        raise
    hrid = str(found.get("hrid") or "")
    return {"id": wanted, "hrid": hrid, "name": str(found.get("name") or hrid or wanted)}


def _unique(name, records):
    """An id made of the name, and a number when another platform has it."""
    base = paths.slug(name)
    if not base:
        raise ValueError("The name needs a letter or a digit")
    uid, number = base, 1
    while any(record["id"] == uid for record in records):
        number += 1
        uid = f"{base}-{number}"
    return uid


def add(name, kind, url, token, account):
    """Registers a platform once it accepts the token for this account: answers its record. `name` is the account's
    name when empty. `account` is the Account ID, the UUID that Gravitee Cloud shows next to its Account Tokens. The
    token is kept as a setting, never answered."""
    name = name.strip() if isinstance(name, str) else name
    if name is not None and (not isinstance(name, str) or len(name) > NAME_LIMIT):
        raise ValueError(f"The name is 1 to {NAME_LIMIT} characters")
    kind = kind or "cloud"
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Only Gravitee Cloud can be registered")
    account = account.strip() if isinstance(account, str) else ""
    if not account:
        raise ValueError("Enter the Account ID")
    if not ACCOUNT_ID.fullmatch(account):
        raise ValueError("The Account ID is made of letters, digits and dashes")
    # An account that is connected already is known by its Account ID: nothing is asked of the Cloud for it
    known = next((record for record in read() if record["account"]["id"].casefold() == account.casefold()), None)
    if known:
        raise ValueError(f"This account is already connected as {known['name']}")
    client = cloud.Cloud(url or KINDS[kind], token)
    mine = _account(client, account)
    name = name or mine["name"][:NAME_LIMIT].strip()
    with _lock:
        records = read()
        if any(record["name"].casefold() == name.casefold() for record in records):
            raise ValueError(f"An account named {name} is already connected")
        record = {"id": _unique(name, records), "name": name, "kind": kind, "url": client.url, "account": mine}
        key = _key(record["id"])
        envfiles.write(paths.ENV_FILE, {key: envfiles.quote(token.strip())})
        try:
            _write([*records, record])
        except Exception as error:  # noqa: BLE001 - whatever it fails with, no record means no token either
            try:
                envfiles.write(paths.ENV_FILE, {key: None})
                left = "nothing was kept"
            except Exception:  # noqa: BLE001 - said in the answer
                left = f"remove {key} from the .env"
            raise RuntimeError(f"The platform could not be saved ({_why(error)}): {left}") from None
    return {**record, "tokenSet": True}


def replace_account_token(uid, token):
    """Keeps another Account Token for a platform once the Cloud accepts it for the account of the record. The record,
    the Cloud Tokens and the tokens of the gateways stay. Answers the record."""
    record = find(uid)
    _account(cloud.Cloud(record["url"], token), record["account"]["id"])
    with _lock:
        envfiles.write(paths.ENV_FILE, {_key(uid): envfiles.quote(token.strip())})
    return {**record, "tokenSet": True}


def rename(uid, name):
    """Gives a platform the name the Lab shows; its id, its account and its tokens stay. Answers the record."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > NAME_LIMIT:
        raise ValueError(f"The name is 1 to {NAME_LIMIT} characters")
    with _lock:
        records = read()
        record = next((item for item in records if item["id"] == uid), None)
        if record is None:
            raise paths.NotFound(f"No platform {uid}")
        if any(item["id"] != uid and item["name"].casefold() == name.casefold() for item in records):
            raise ValueError(f"Another account is named {name}")
        record["name"] = name
        _write(records)
    return {**record, "tokenSet": bool(envfiles.read(paths.ENV_FILE).get(_key(uid)))}


def _forget_records(uid):
    """Deletes what the playgrounds of every catalog recorded of what they deployed to a platform: the folder of its id in
    the state folder of each playground (.lab/catalogs/*/playgrounds/*/.state/<platform id>/). A folder that leads out of the
    playground's own, or sits behind a link a container planted, is left alone and said."""
    failed = []
    for pid in paths.ids("playground"):
        folder = paths.ROOT / paths.unit_folder("playground", pid) / STATE / uid
        if folder.is_dir() and not folder.is_symlink():
            if not paths.contained(folder, paths.ROOT / paths.unit_folder("playground", pid)):
                failed.append(f"{folder.relative_to(paths.ROOT).as_posix()}: it leads out of the playground's folder")
                continue
            try:
                shutil.rmtree(folder)
            except OSError as error:
                failed.append(f"{folder.relative_to(paths.ROOT).as_posix()}: {_why(error)}")
    if failed:
        raise RuntimeError("The platform was forgotten, but what the playgrounds recorded of it could not be deleted, "
                           "delete these yourself: " + ", ".join(failed))


def runs_here(project, uid, gateway=None):
    """Whether the gateway that runs here is one of this platform's, which the Lab created and kept the token of; with a
    `gateway`, whether it is that one. The claims of the token the add-on holds name it."""
    record = find(uid)
    held = gateway_claims(project, record["kind"])
    if not held:
        return False
    if gateway is not None:
        return held["gateway"] == gateway
    try:
        return bool(envfiles.read(paths.ENV_FILE).get(_key(uid, "GATEWAY", held["gateway"])))
    except ValueError:   # a gateway whose id is no segment is none the Lab keeps
        return False


def remove(uid, project=None):
    """Forgets a platform, every token kept for it and what the playgrounds recorded of their deploys there. Its
    gateways, and what was deployed there, stay in the platform that holds them. With the project, the token the add-on
    holds is emptied when it is the one of a gateway of this platform: no gateway runs here any more, and the add-on is
    turned off by whoever calls (runs_here says it first)."""
    with _lock:
        records = read()
        record = next((item for item in records if item["id"] == uid), None)
        if not record:
            raise paths.NotFound(f"No platform {uid}")
        ran = bool(project) and runs_here(project, uid)   # before its keys go
        _write([item for item in records if item["id"] != uid])
        gone = _owned(uid, envfiles.read(paths.ENV_FILE), [item["id"] for item in records])
        if gone:
            envfiles.write(paths.ENV_FILE, dict.fromkeys(gone))
        if ran:
            addon = gateway_addon(project, record["kind"])
            envfiles.write(addon["env_file"], {addon["gateway"]["token"]: None})
    _refused.difference_update([item for item in list(_refused) if item[0] == uid])
    _forget_records(uid)
    return {}


# ---------------------------------------------------------------------------------------------------------------------
# What the platform holds
# ---------------------------------------------------------------------------------------------------------------------

def _environment(found):
    return {"id": found["id"], "hrid": str(found.get("hrid") or ""), "name": str(found.get("name") or ""),
            "organizationId": str(found.get("organizationId") or "")}


def _gateway(found):
    """A gateway as the page gets it. A connection the Cloud does not say is as it says an unknown one."""
    domains = [item.get("domain") if isinstance(item, dict) else item for item in found.get("domains") or []]
    return {"id": found.get("id"), "name": found.get("name"), "environmentId": found.get("environmentId"),
            "type": found.get("type"), "scope": found.get("scope") or "", "status": found.get("status"),
            "connection": found.get("connectionStatus") or "UNKNOWN_HYBRID", "version": found.get("version") or "",
            "domains": [domain for domain in domains if isinstance(domain, str)]}


def _listed(found):
    return isinstance(found.get("id"), str) and ID.fullmatch(found["id"])


def _remembered(key, read):
    """What `read` answers, kept for KEEP seconds: the plugins and the license of a Cloud change when it is upgraded.
    None when the Cloud does not say (a read that fails is asked again the next time, not kept)."""
    with _lock:
        hit = _kept.get(key)
    if hit and time.monotonic() - hit[0] < KEEP:
        return hit[1]
    try:
        value = read()
    except (cloud.CloudError, ValueError, OSError):
        return hit[1] if hit else None   # the last it said, rather than nothing, while it does not answer
    with _lock:
        _kept[key] = (time.monotonic(), value)
    return value


def _scopes(organization):
    """The products an organization has a console or a gateway for, as the scopes of its access points say."""
    return sorted({point.get("scope") for point in organization.get("accessPoints") or []
                   if isinstance(point, dict) and isinstance(point.get("scope"), str)})


def _entitled(record, client):
    """What the account's plan and its organizations' licenses say it can do: {plan: {gamma}, organizations: {id: {scopes,
    tier, expires}}}. The license is the signed text, which license.decode reads here, never kept nor sent."""
    uid, account = record["id"], record["account"]["id"]
    plan = _remembered((uid, "plan"), lambda: client.subscription(account))
    organizations = _remembered((uid, "organizations"), lambda: client.organizations(account)) or []
    found = {}
    for organization in organizations:
        if not isinstance(organization, dict) or not _listed(organization):
            continue
        def read(organization=organization):
            features, _ = license.decode(client.license(account, organization["id"]))
            expires = features.get("expiryDate")
            return {"tier": features.get("tier"), "expires": expires.isoformat() if hasattr(expires, "isoformat") else None}
        found[organization["id"]] = {"scopes": _scopes(organization),
                                     "license": _remembered((uid, "license", organization["id"]), read)}
    return {"plan": {"gamma": bool(plan.get("gammaEnabled"))} if isinstance(plan, dict) else None,
            "organizations": found}


def _installed(record, environment, env):
    """What the Management API of the Cloud has for an environment, read with its Cloud Token when the Lab keeps a valid
    one: {plugins: {policy|endpoint|entrypoint|resource: {id: deployed}}, license: {tier, packs, features}}, else None. The
    answer is kept while the token is the same."""
    token = env.get(_key(record["id"], "ENV", environment["id"]), "")
    if not token:
        return None
    try:
        claims = cloud.claims(token)
    except (cloud.CloudError, ValueError):
        return None
    if claims["expiry"] is not None and claims["expiry"] <= time.time() or (record["id"], environment["id"]) in _refused:
        return None
    organization = environment.get("organizationId") or claims["organization"]
    seal = hashlib.sha256(token.encode()).hexdigest()[:16]

    def read():
        management = cloud.Gate(gate(record, claims), token)
        return {"plugins": management.plugins(organization), "license": management.license(organization)}
    return _remembered((record["id"], "installed", environment["id"], seal), read)


def fetch(record):
    """A live read of a platform: {state, message, environments, gateways, entitled}. `state` is ok, invalid when the
    platform refuses the token or the rights, unreachable when it gives no usable answer; then `message` says it. An
    environment or a gateway with an id that is no segment of letters, digits and dashes is left out. `entitled` is
    what the account's plan and licenses say (_entitled); an environment with a valid Cloud Token also has `installed`,
    what its Management API has (_installed)."""
    try:
        client = _client(record)
        environments = client.environments(record["account"]["id"])
        gateways = client.gateways(record["account"]["id"])
    except cloud.CloudError as error:
        return {"state": error.kind, "message": str(error), "environments": [], "gateways": []}
    listed = [_environment(found) for found in environments if _listed(found)]
    env = envfiles.read(paths.ENV_FILE)
    return {"state": "ok", "message": "",
            "environments": [{**environment, "installed": _installed(record, environment, env)} for environment in listed],
            "gateways": [_gateway(found) for found in gateways if _listed(found)],
            "entitled": _entitled(record, client)}


def gateway_addon(project, kind):
    """The add-on that runs the gateway of this kind of platform on this machine, None when there is none: the one the project
    chose (project.build, resolve.gateway_addon: the first in the order of precedence that says it runs one and can start,
    and whose catalog may declare it), which `platform.gateways` holds. A project that does not say (one not built by
    project.build) is asked the same way over its add-ons by id. Whatever it names has to declare a gateway of this kind."""
    gateways = (project.get("platform") or {}).get("gateways")
    if isinstance(gateways, dict):
        found = gateways.get(kind)
    else:
        found = resolve.gateway_addon(project["addons"], kind, sorted(project["addons"]))
    unit = project["addons"].get(found) if isinstance(found, str) else None
    return unit if unit and (unit.get("gateway") or {}).get("platform") == kind else None


def gateway_claims(project, kind):
    """What the token of the gateway that runs here says of itself (claims), None when the add-on holds none."""
    addon = gateway_addon(project, kind) if project else None
    token = envfiles.read(addon["env_file"]).get(addon["gateway"]["token"], "") if addon else ""
    found = cloud.claims(token) if token else None
    return found if found and found["kind"] == "gateway" else None


def _token(uid, environment, env):
    """The state of the Cloud Token kept for an environment: unset, expired (its own claim says so), refused (Cloud
    Gate said no when it was last asked) or valid; and the day it expires when it says."""
    token = env.get(_key(uid, "ENV", environment), "")
    if not token:
        return {"state": "unset"}
    expiry = cloud.claims(token)["expiry"]
    state = "expired" if expiry is not None and expiry <= time.time() else \
        "refused" if (uid, environment) in _refused else "valid"
    return {"state": state, **({"expires": expiry} if expiry is not None else {})}


def _orphan(found, held):
    """The gateway the add-on holds the token of when it is of an environment of this platform and the platform no
    longer lists it (deleted in the platform): {gateway, environmentId}, else None. Only ids, never the token."""
    if not held or not held["environment"] or held["environment"] not in {item["id"] for item in found["environments"]}:
        return None
    if held["gateway"] in {item["id"] for item in found["gateways"]}:
        return None
    return {"gateway": held["gateway"], "environmentId": held["environment"]}


def annotate(record, found, project=None):
    """A read of a platform (fetch) with what this machine adds to it: the state of the Cloud Token of each environment,
    for each gateway whether it is the one that runs here (its id is in the claims of the token the add-on holds) and
    whether its token is kept, and `orphan`: the gateway that runs here and that the platform no longer lists. Without
    the project nothing is known to run here."""
    if found["state"] != "ok":
        return found
    env, held = envfiles.read(paths.ENV_FILE), gateway_claims(project, record["kind"])
    here = held["gateway"] if held else None
    return {**found,
            "environments": [{**environment, "token": _token(record["id"], environment["id"], env)}
                             for environment in found["environments"]],
            "gateways": [{**gateway, "here": gateway["id"] == here,
                          "kept": gateway["id"] == here or bool(env.get(_key(record["id"], "GATEWAY", gateway["id"])))}
                         for gateway in found["gateways"]],
            "orphan": _orphan(found, held)}


def check(uid, project=None):
    """A live read of a platform with what this machine knows of it (annotate)."""
    record = find(uid)
    return annotate(record, fetch(record), project)


# ---------------------------------------------------------------------------------------------------------------------
# What an environment runs
# ---------------------------------------------------------------------------------------------------------------------

def product_group(project, scope):
    """The group of the core whose product is the one of a gateway's scope (its plugins folder), None when there is none."""
    pid = SCOPES.get(scope)
    return next((group for group in project.get("groups") or [] if pid and group.get("product") == pid), None)


def product_name(project, scope):
    """The name the project gives the product of a gateway's scope: the group of the core whose product it is, else the
    product as its plugins folder names it, else the scope's own words."""
    group = product_group(project, scope)
    return (group or (project.get("products") or {}).get(SCOPES.get(scope)) or {}).get("name") or scope


def local_gateway(project):
    """The service of the core that is the gateway of API Management on this machine: the one whose image is the
    gateway's. None when the core has none."""
    return next((name for name, service in sorted(project["services"].items())
                 if service["owner"]["kind"] == "platform" and re.search(r"/apim-gateway(?::|$)", service.get("raw_image") or "")),
                None)


def retarget_names(project, kind):
    """How a file written for the gateway of this machine is written for the gateway that a kind of platform runs here:
    {the service of the core: the service of the platform}, empty without either. What an item sets on the one it sets on
    the other, and the host that names the one names the other."""
    addon, local = gateway_addon(project, kind), local_gateway(project)
    return {local: addon["gateway"]["service"]} if addon and local else {}


def plugins(project, kind):
    """Whether the gateway that runs here for a kind of platform mounts a plugins folder: the service of the add-on that
    runs it is one that loads a product's plugins (project.products), as its file mounts them."""
    addon = gateway_addon(project, kind) if project else None
    service = addon["gateway"]["service"] if addon else None
    return bool(service) and any(service in product["services"] for product in (project.get("products") or {}).values())


def _release(version):
    """The numbers of a version, to compare two: 4.12.21 is (4, 12, 21); None for text that starts with none."""
    found = re.match(r"\d+(?:\.\d+)*", version or "")
    return tuple(int(part) for part in found.group().split(".")) if found else None


def _extra_products(project, found, organization):
    """The products the Cloud offers that no gateway of the environment says: Access Management when an access point or a
    gateway has its scope, Gamma when the plan of the account enables it."""
    entitled = found.get("entitled") or {}
    offered = set(((entitled.get("organizations") or {}).get(organization) or {}).get("scopes") or [])
    names = {}
    if "AM" in offered:
        names[product_name(project, "AM")] = ""
    if (entitled.get("plan") or {}).get("gamma"):
        names[GAMMA] = ""
    return names


def capabilities(project, found, environment, kind="cloud"):
    """What an environment of a platform runs, from a read of the platform (fetch or annotate), which is all the Lab
    knows of it: {products: {name: version}, plugins, installed, license, detail}. A product is the scope of the
    environment's gateways, named as the project names it, at the oldest version they run, or one the account is
    entitled to without a gateway (Gamma, Access Management); `detail` says when they run several. `plugins`: whether the
    gateway that runs here mounts a plugins folder. `installed`: the plugins the environment's Management API has, by kind,
    each with whether the license of the organization deploys it, None while no Cloud Token of the environment tells;
    `license`: that license, {tier, packs, features}. None when nothing can be said: the read failed (its `state` says
    why), or the environment has no gateway, which is not set up."""
    if found.get("state") != "ok":
        return None
    mine = [gateway for gateway in found.get("gateways") or [] if gateway.get("environmentId") == environment]
    if not mine:
        return None
    versions = {}
    for gateway in mine:
        if gateway.get("scope"):
            versions.setdefault(product_name(project, gateway["scope"]), set()).add(gateway.get("version") or "")
    products, detail = {}, []
    for name, said in versions.items():
        known = sorted((version for version in said if _release(version)), key=_release)
        products[name] = known[0] if known else ""
        if len(known) > 1:
            detail.append(f"{name}: the gateways run {', '.join(known)}, the oldest counts")
    here = next((item for item in found.get("environments") or [] if item["id"] == environment), {})
    for name, version in _extra_products(project, found, here.get("organizationId")).items():
        products.setdefault(name, version)
    live = here.get("installed") or {}
    return {"products": products, "plugins": plugins(project, kind), "detail": "; ".join(detail),
            "installed": live.get("plugins"), "license": live.get("license")}


# ---------------------------------------------------------------------------------------------------------------------
# Cloud Tokens
# ---------------------------------------------------------------------------------------------------------------------

def gate(record, found):
    """Where Cloud Gate is for a Cloud Token that says this of itself (claims). Gravitee Cloud has one by region, which
    the token names; any other address (a proxy, another kind of platform) is its own Gate."""
    if (urlsplit(record["url"]).hostname or "").endswith(CLOUD_HOST):
        if not found["cpg"]:
            raise cloud.CloudError("This is not a Cloud Token", "invalid")
        return GATE_URL.format(found["cpg"])
    return record["url"]


def cloud_token(uid, environment):
    """The Cloud Token kept for an environment and what it says of itself: (token, claims). It is refused when it is
    none for this environment or has expired."""
    token = envfiles.read(paths.ENV_FILE).get(_key(uid, "ENV", environment), "")
    if not token:
        raise ValueError("No Cloud Token is kept for this environment")
    found = cloud.claims(token)
    if found["kind"] != "cloud" or found["environment"] != environment or not ID.fullmatch(found["organization"] or ""):
        raise ValueError("The Cloud Token kept for this environment is not one for it")
    if found["expiry"] is not None and found["expiry"] <= time.time():
        raise cloud.CloudError("This Cloud Token has expired", "invalid")
    return token, found


def prove(uid, environment):
    """Asks Cloud Gate one question with the Cloud Token kept for an environment: answers its claims. A token that Cloud
    refuses stays refused until it is replaced or forgotten."""
    record = find(uid)
    token, found = cloud_token(uid, environment)
    try:
        cloud.Gate(gate(record, found), token).read(found["organization"], found["environment"])
    except cloud.CloudError as error:
        if error.status in (401, 403):
            _refused.add((uid, environment))
        raise
    _refused.discard((uid, environment))
    return found


def _names(record):
    """The environments of the account of a record, by id: their names."""
    try:
        return {found["id"]: str(found.get("name") or "") for found in
                _client(record).environments(record["account"]["id"]) if _listed(found)}
    except cloud.CloudError as error:
        if error.status in (401, 403):
            raise cloud.CloudError(f"Gravitee Cloud refuses the Account Token of {record['name']}", "invalid",
                                   error.status) from None
        raise


def set_cloud_token(uid, environment, token):
    """Keeps the Cloud Token of an environment once it proves to be one: for this environment, not expired, and
    accepted by Cloud Gate, which is asked once. A token that says so is refused in plain words, as is one for another
    environment."""
    record = find(uid)
    token = token.strip() if isinstance(token, str) else ""
    found = cloud.claims(token)
    if found["kind"] == "gateway":
        raise cloud.CloudError("This is a gateway token, not a Cloud Token", "invalid")
    if found["kind"] != "cloud" or not (found["organization"] and found["environment"]):
        raise cloud.CloudError("This is not a Cloud Token", "invalid")
    if found["expiry"] is not None and found["expiry"] <= time.time():
        raise cloud.CloudError("This Cloud Token has expired", "invalid")
    names = _names(record)
    if not isinstance(environment, str) or environment not in names:
        raise ValueError("Choose an environment")
    if found["environment"] != environment:
        theirs = names.get(found["environment"])
        raise cloud.CloudError(f"This Cloud Token is for {theirs}, not {names[environment]}" if theirs
                               else f"This Cloud Token is not for an environment of {record['name']}", "invalid")
    cloud.Gate(gate(record, found), token).read(found["organization"], found["environment"])
    with _lock:
        envfiles.write(paths.ENV_FILE, {_key(uid, "ENV", environment): envfiles.quote(token)})
    _refused.discard((uid, environment))
    return {}


def delete_cloud_token(uid, environment):
    """Forgets the Cloud Token of an environment on this machine: the token stays valid in the platform."""
    find(uid)
    key = _key(uid, "ENV", environment)
    with _lock:
        if key in envfiles.read(paths.ENV_FILE):
            envfiles.write(paths.ENV_FILE, {key: None})
    _refused.discard((uid, environment))
    return {}


# ---------------------------------------------------------------------------------------------------------------------
# Hybrid gateways
# ---------------------------------------------------------------------------------------------------------------------

def create_gateway(project, uid, environment, name):
    """Creates a hybrid gateway of API Management in an environment of a platform and keeps its token, which the Cloud
    shows once: {gateway, tokenSaved}. The token is kept as the setting of the gateway, and also as the add-on's when
    the add-on holds none, so that the first gateway is the one that runs here; a gateway made while another runs does
    not take its place (swap_gateway does). The domains and the setting come from the add-on that runs the gateway. A
    gateway whose token could not be kept is removed again."""
    record = find(uid)
    addon = gateway_addon(project, record["kind"])
    if not addon:
        raise paths.NotFound(f"No add-on runs a gateway of {record['name']}")
    if not isinstance(environment, str) or not ID.fullmatch(environment.strip()):
        raise ValueError("Choose an environment")
    if not isinstance(name, str) or not cloud.NAME.fullmatch(name.strip()):
        raise ValueError("A gateway name is 2 to 128 letters, digits, spaces, dashes, dots or underscores")
    name, setting = name.strip(), addon["gateway"]["token"]
    client, account = _client(record), record["account"]["id"]
    gateway, token = client.create_gateway(account, environment.strip(), name, addon["gateway"]["domains"])
    key, here = None, False
    try:
        key = _key(uid, "GATEWAY", gateway.get("id"))
        with _lock:
            here = not envfiles.read(addon["env_file"]).get(setting)
            envfiles.write(paths.ENV_FILE, {key: envfiles.quote(token)})
            if here:
                envfiles.write(addon["env_file"], {setting: envfiles.quote(token)})
    except Exception as error:  # noqa: BLE001 - the token is shown once: whatever fails, the gateway cannot stay
        try:
            client.delete_gateway(account, gateway["id"])
            left = "the gateway was removed from the platform"
        except ValueError:   # a CloudError too
            left = "delete the gateway in the platform"
        try:
            if key:
                envfiles.write(paths.ENV_FILE, {key: None})
        except Exception:  # noqa: BLE001 - a setting nobody reads is all that is left
            pass
        raise RuntimeError(f"The gateway's token could not be saved ({_why(error)}): {left}") from None
    return {"gateway": {**_gateway(gateway), "here": here, "kept": True}, "tokenSaved": True}


def swap_gateway(project, uid, gateway):
    """Makes the token kept for a gateway the one the add-on holds, which the add-on takes when it is created again:
    the gateway that runs here is this one from then on."""
    record, key = find(uid), _key(uid, "GATEWAY", gateway)
    addon = gateway_addon(project, record["kind"])
    if not addon:
        raise paths.NotFound(f"No add-on runs a gateway of {record['name']}")
    token = envfiles.read(paths.ENV_FILE).get(key, "")
    if not token:
        raise ValueError("Its token is not kept here")
    with _lock:
        envfiles.write(addon["env_file"], {addon["gateway"]["token"]: envfiles.quote(token)})
    return {}


def delete_gateway(project, uid, gateway):
    """Deletes a gateway in a platform, then the token kept for it. When the add-on held that token its setting is
    emptied: no gateway runs here any more."""
    record, key = find(uid), _key(uid, "GATEWAY", gateway)
    _client(record).delete_gateway(record["account"]["id"], gateway)
    held, addon = gateway_claims(project, record["kind"]), gateway_addon(project, record["kind"])
    with _lock:
        if key in envfiles.read(paths.ENV_FILE):
            envfiles.write(paths.ENV_FILE, {key: None})
        if held and held["gateway"] == gateway:
            envfiles.write(addon["env_file"], {addon["gateway"]["token"]: None})
    return {}
