"""A playground for Docker Compose alone: a folder, in a zip archive, that runs it with `docker compose up` and no Lab.

The folder holds one `docker-compose.yml`, its `.env.example`, a `README.md` that is the guide, and what the services build or mount:
the playground's own folder, the add-ons it brings, the seeder, the plugins it asks for, and demo certificates made for the archive.

The Compose file is the one Compose runs for the core and the playground (`compose.config_json`), cut down to what the playground
needs: its own services and those of the add-ons it brings, the products of the core it uses (what its seed fills and the images it
requires, `_products`), and what those depend on. The rest of the core goes; a product that goes is no longer named by those that
stay (the link to the Alert Engine, `_without`). What only the Lab UI uses goes too: its labels, its script in the consoles, the names
of the containers (which would take those of a Lab on the same machine). Paths are relative to the folder, and the file is written in
the short forms a person writes (`_tidy`).

Its variables are read in an environment of the Lab's choosing (`_resolve`), scrubbed as for a file someone else wrote: the
versions are those the playground was tested with, and every other setting takes its default, except the settings a person may
change (`_kept`: the license, the core's passwords, the playground's settings and those of what it brings, and what its catalogs
share). Those stay variables, `${NAME:-default}`, listed in `.env.example` with what each is; nothing of this machine's `.env` is
in the archive, the license least of all.

The guide becomes Markdown any viewer shows (`_guide`): an action link is the name of the button of the app it runs, an account link
the user and its password, a block to run a plain block. What to have before the start (Docker, the license and its features, what
the playground asks of the machine) and how to start it come before its first step, and how to stop it after the last.

What comes from a private catalog leaves with the file, and is said before it is made, as for an export (`exports._origins`); an
image the folder pulls from a registry that asks for a login is said too (`exports._registries`).

    plan(pid)        what the archive holds and asks: the services, what is left out, the settings, what it needs
    archive(pid)     plan(pid) and the file: {"file", "data" (base64)}
"""

import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..docker import compose
from ..library import project
from . import exports, resolve

MARK = "@@{}@@"                     # a setting a person keeps, while Compose reads the files: written back as ${NAME:-default}
MARKED = re.compile(r"@@([A-Za-z_][A-Za-z0-9_]*)@@")
FROZEN = re.compile(r"_(VERSION|REGISTRY|PULL_POLICY)$")   # set by the archive: the tested versions, how the images come
CERTS = "certs/mtls"                # the demo certificates of the gateway, made for each archive
KEYSTORE = "MTLS_KEYSTORE_PASSWORD"  # their password: the default, since they are made with it
LAB_UI_SCRIPT = "gravitee-lab.js"   # what the core's consoles load for the Lab UI (the Reload of a frame)
ALERTS = "gravitee_alerts_alertengine"   # the settings of the link to the Alert Engine
REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@)")   # a build context that is an address, not a folder of this machine
LAB_ONLY = ("Save & apply", "Re-seed", "Lab UI")   # words of a setting's comment that only mean something in the Lab
TROUBLESHOOTING = "https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/troubleshooting.md"
NEVER = ["__pycache__", "*.pyc", "node_modules", ".state", ".env", ".venv", ".pytest_cache", ".DS_Store"]
ORDER = ["image", "build", "pull_policy", "restart", "command", "entrypoint", "depends_on", "environment", "volumes", "ports",
         "extra_hosts", "networks", "healthcheck", "labels"]


def _fail(code, message, status=400):
    return exports._fail(code, message, status)


# ---------------------------------------------------------------------------------------------------------------------
# What the playground is made of
# ---------------------------------------------------------------------------------------------------------------------

def _gather(pid):
    """{found, unit, folder, nodes, units}: the playground, its folder and every item it brings. Raises NotFound."""
    found = project.load()
    unit = found["playgrounds"].get(pid)
    if unit is None:
        raise paths.NotFound(f"No playground {pid}")
    units = {"addon": found["addons"], "setup": found["setups"], "playground": found["playgrounds"]}
    nodes = [node for node in resolve.closure(units, "playground", pid) if node[1] in (units.get(node[0]) or {})]
    return {"found": found, "unit": unit, "folder": paths.ROOT / Path(unit["file"]).parent, "nodes": nodes, "units": units}


def _kept(gathered):
    """The settings a person may change, in the order .env.example lists them: [{key, default, comment, secret}]. The core's
    (but the keystore's password: the certificates are made with its default), those of the playground and of what it brings,
    and those their catalogs share; never a version, a registry or a pull policy, which the archive sets."""
    found, units = gathered["found"], gathered["units"]
    rows = [row for row in found["core_variables"] if row["key"] != KEYSTORE]
    for kind, uid in gathered["nodes"]:
        rows += (units[kind][uid].get("settings") or [])
    shared = {name for _, uid in gathered["nodes"] for name in (project._shared_settings(paths.origin(uid)) or [])}
    rows += [{"key": name, "default": "", "comment": "", "secret": False} for name in sorted(shared)]
    seen, kept = set(), []
    for row in rows:
        if row["key"] not in seen and not FROZEN.search(row["key"]):
            seen.add(row["key"])
            kept.append(row)
    return kept


def _resolve(gathered, kept):
    """The core and the playground as Compose runs them, in a scrubbed environment where the versions are those of the latest
    tested set and each kept setting is a mark. A mark Compose refuses where it stands (a port, a policy) gives way to the
    default there, and that setting is no longer kept. Answers (config, the keys still kept)."""
    unit = gathered["unit"]
    tested = next((one for one in reversed(unit.get("tested") or []) if one), {})
    keys = [row["key"] for row in kept]
    files = compose.all_files([unit["file"]])
    while True:
        environ = {**compose.scrubbed_environment(), **{k: str(v) for k, v in tested.items()}, **{k: MARK.format(k) for k in keys}}
        try:
            return compose.config_json(files, timeout=60, environ=environ), keys
        except compose.ComposeError as error:
            refused = [key for key in keys if MARK.format(key) in str(error)]
            if not refused:
                raise _fail("standalone_compose", f"Compose cannot read it: {error}", 409) from None
            keys = [key for key in keys if key not in refused]


def _products(gathered):
    """The groups of the core the playground uses: those its seed fills (seed/apim, seed/am...) and those whose images it
    requires. A product of the core it does not use is not in the archive."""
    found, unit, folder = gathered["found"], gathered["unit"], gathered["folder"]
    seed = folder / "seed"
    names = {entry.name for entry in seed.iterdir()} if seed.is_dir() else set()
    wanted = {project.SEEDED[name] for name in names if name in project.SEEDED}
    for variable in ((unit.get("requires") or {}).get("images") or {}):
        for service in found["services"].values():
            if service["owner"]["kind"] == "platform" and "${" + variable in (service.get("raw_image") or ""):
                wanted.add(service["owner"]["name"])
    return {group["id"] for group in found["groups"] if group["name"] in wanted}


def _keep(config, gathered, products):
    """The services of the archive: those of the playground and of what it brings, those of the products it uses, and what
    they depend on."""
    found = gathered["found"]
    platform = {name for name, service in found["services"].items() if service["owner"]["kind"] == "platform"}
    keep = {name for name in config["services"] if name not in platform}
    keep |= {name for name in config["services"] if name in platform and found["services"][name]["owner"]["id"] in products}
    todo = list(keep)
    while todo:
        for dep in config["services"][todo.pop()].get("depends_on") or {}:
            if dep in config["services"] and dep not in keep:
                keep.add(dep)
                todo.append(dep)
    return keep


# ---------------------------------------------------------------------------------------------------------------------
# The folder
# ---------------------------------------------------------------------------------------------------------------------

def _places(gathered):
    """[(a folder of this machine, where it goes in the archive)], the most precise first: each item's folder (the playground at
    the root of the archive, under its slug; an add-on under addons/), the seeder, the certificates and the plugins."""
    slug = exports._slug(gathered["unit"]["id"])
    found = []
    for kind, uid in gathered["nodes"]:
        file = paths.ROOT / paths.unit_file(kind, uid)
        if kind == "playground" and uid == gathered["unit"]["id"]:
            found.append((file.parent, Path(slug)))
        elif kind != "setup":
            found.append((file.parent, Path(f"{kind}s") / exports._slug(uid)))
    found += [(paths.ROOT / "seeder", Path("seeder")), (paths.ROOT / "certs", Path("certs")), (paths.ROOT / "plugins", Path("plugins"))]
    return sorted(found, key=lambda pair: -len(pair[0].parts))


def _place(source, places):
    """Where a path of this machine goes in the archive. A path no item and no part of the Lab holds cannot go."""
    source = Path(source)
    for root, to in places:
        try:
            return to / source.relative_to(root)
        except ValueError:
            continue
    raise _fail("standalone_path", f"It uses {source}, which is not part of it.", 409)


def _ignore(folder: Path):
    """What is never copied of a folder: caches, settings and what a seed recorded, and what its build leaves out
    (its .dockerignore)."""
    never = list(NEVER)
    own = folder / ".dockerignore"
    if own.is_file():
        never += [line.strip().removeprefix("**/") for line in own.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith(("#", "!"))]
    return shutil.ignore_patterns(*never)


def _copy(source: Path, target: Path):
    if source.is_dir():
        shutil.copytree(source, target, ignore=_ignore(source), dirs_exist_ok=True)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _plugins(unit, product, target: Path):
    """The plugins the playground asks for, of one product (`requires.plugins`), copied from those this Lab downloaded.
    Answers the ids it does not have."""
    target.mkdir(parents=True, exist_ok=True)
    (target / ".gitkeep").touch()
    missing = []
    for pid in ((unit.get("requires") or {}).get("plugins") or {}).get(product) or []:
        files = sorted((paths.ROOT / "plugins" / product).glob(f"{pid}-*.zip"))
        if files:
            shutil.copy2(files[-1], target / files[-1].name)
        else:
            missing.append(pid)
    return missing


def _without(services, dropped):
    """A product the archive leaves out is no longer named by those it keeps: the link to the Alert Engine is turned off."""
    if "alert-engine" not in dropped:
        return
    for service in services.values():
        env = service.get("environment") or {}
        if any(key.startswith(ALERTS) for key in env):
            for key in [key for key in env if key.startswith(ALERTS)]:
                del env[key]
            env[f"{ALERTS}_enabled"] = "false"


def _short_port(port):
    host = f"{port['host_ip']}:" if port.get("host_ip") else ""
    proto = "" if port.get("protocol", "tcp") == "tcp" else "/" + port["protocol"]
    return f"{host}{port['published']}:{port['target']}{proto}" if port.get("published") else f"{port['target']}{proto}"


def _short_volume(volume):
    if volume.get("type") in ("bind", "volume") and set(volume) <= {"type", "source", "target", "read_only", "bind", "volume"}:
        return f"{volume['source']}:{volume['target']}" + (":ro" if volume.get("read_only") else "")
    return volume


def _tidy(service):
    """A service in the forms a person writes: no empty key, short ports and volumes, a build that is a folder, a list of
    networks, dependencies without what is the default, and the keys in the order a reader expects."""
    for key in [key for key, value in service.items() if value is None]:
        del service[key]
    if isinstance(service.get("networks"), dict) and all(value is None for value in service["networks"].values()):
        service["networks"] = list(service["networks"])
    deps = service.get("depends_on")
    if deps:
        for how in deps.values():
            if how.get("required") is True:
                del how["required"]
            if how.get("restart") is False:
                del how["restart"]
        if all(how == {"condition": "service_started"} for how in deps.values()):
            service["depends_on"] = list(deps)
    if service.get("ports"):
        service["ports"] = [_short_port(port) for port in service["ports"]]
    if service.get("volumes"):
        service["volumes"] = [_short_volume(volume) for volume in service["volumes"]]
    build = service.get("build")
    if isinstance(build, dict):
        if build.get("dockerfile") == "Dockerfile":
            del build["dockerfile"]
        if set(build) == {"context"}:
            service["build"] = build["context"]
    return {key: service[key] for key in sorted(service, key=lambda key: ORDER.index(key) if key in ORDER else len(ORDER))}


def _services(config, keep, gathered, root: Path):
    """The services of the archive, their files copied into `root`, in the order of the core then of the playground. Answers
    (services, the plugins the playground asks for that this Lab does not have)."""
    unit = gathered["unit"]
    places, lab_ui, missing = _places(gathered), paths.ROOT / "lab-ui", []
    services = {name: config["services"][name] for name in config["services"] if name in keep}
    for service in services.values():
        service.pop("container_name", None)
        labels = {key: value for key, value in (service.get("labels") or {}).items() if not key.startswith("io.gravitee")}
        service["labels"] = labels or None
        if any(LAB_UI_SCRIPT in str(part) for part in (service.get("command") or [])):
            service["command"] = service["entrypoint"] = None   # the console as its image starts it
        volumes = []
        for volume in service.get("volumes") or []:
            if volume.get("type") == "bind":
                source = Path(volume["source"])
                if source.is_relative_to(lab_ui):
                    continue   # the Lab UI's script in the consoles
                to = _place(source, places)
                if source.is_relative_to(paths.ROOT / "plugins"):
                    missing += _plugins(unit, source.name, root / to)
                elif source.name != ".state" and not source.is_relative_to(paths.ROOT / CERTS):
                    _copy(source, root / to)
                volume = {**{key: value for key, value in volume.items() if key != "bind"}, "source": f"./{to.as_posix()}"}
            volumes.append(volume)
        service["volumes"] = volumes or None
        build = service.get("build")
        if build and not REMOTE.match(str(build.get("context") or "")):   # a Git address stays: Docker builds from it
            to = _place(build["context"], places)
            _copy(Path(build["context"]), root / to)
            service["build"] = {**{key: value for key, value in build.items() if key != "context"}, "context": f"./{to.as_posix()}"}
        for dep in [dep for dep in (service.get("depends_on") or {}) if dep not in keep]:
            del service["depends_on"][dep]
    _without(services, set(config["services"]) - keep)
    ranked = _written_order(gathered)
    order = sorted(services, key=lambda name: ranked.index(name) if name in ranked else len(ranked))
    return {name: _tidy(services[name]) for name in order}, sorted(set(missing))


SERVICE_LINE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*(?:#.*)?$")


def _written_order(gathered):
    """The services in the order their files write them: the core's (its databases, then each product, its API first), then
    the playground's and those of what it brings."""
    files = [paths.ROOT / compose.CORE_FILE, *[paths.ROOT / paths.unit_file(kind, uid) for kind, uid in gathered["nodes"]]]
    order = []
    for file in files:
        inside = False
        for line in file.read_text(encoding="utf-8").splitlines() if file.is_file() else []:
            if line.startswith("services:"):
                inside = True
            elif line and not line[0].isspace() and not line.startswith("#"):
                inside = False
            elif inside and SERVICE_LINE.match(line) and SERVICE_LINE.match(line).group(1) not in order:
                order.append(SERVICE_LINE.match(line).group(1))
    return order


def _certificates(root: Path):
    """Demo certificates of the gateway, made for this archive with the Lab's script, which it carries too."""
    folder = root / CERTS
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.ROOT / CERTS / "generate.sh", folder / "generate.sh")
    done = subprocess.run(["sh", str(folder / "generate.sh")], env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                                                                      KEYSTORE: "gravitee"},
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise _fail("standalone_certs", "The demo certificates could not be made: openssl is needed.", 409)


# What YAML reads as something else than the text: a word it knows, a number, an empty text
PLAIN = re.compile(r"[A-Za-z0-9_./@][A-Za-z0-9_./@:+=,() -]*")
TAKEN = re.compile(r"(?i)(~|null|true|false|yes|no|on|off|y|n|[-+]?(\d[\d_]*)?(\.\d*)?([eE][-+]?\d+)?|0x[0-9a-f]+|0o[0-7]+)")


def _scalar(value):
    """A value as YAML writes it: plain when it reads back as the same text, else in double quotes (JSON's, which YAML reads)."""
    if value is None:
        return "null"
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if PLAIN.fullmatch(text) and not TAKEN.fullmatch(text) and ": " not in text and " #" not in text and text == text.strip():
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml(value, depth=0):
    """The lines of a value of a Compose file: mappings and lists in block style, as a person writes them. Standard library
    only: the Lab UI reads YAML through Compose, and writes this one."""
    pad = "  " * depth
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{_scalar(key)}:")
                lines += _yaml(item, depth + 1)
            else:
                lines.append(f"{pad}{_scalar(key)}: {'{}' if item == {} else '[]' if item == [] else _scalar(item)}")
    else:
        for item in value:
            if isinstance(item, dict) and item:
                inner = _yaml(item, depth + 1)
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines += inner[1:]
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                lines += _yaml(item, depth + 1)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return lines


def _settled(value, defaults):
    """`value` with each mark of a kept setting written as the variable a person sets: ${NAME:-default}, or ${NAME} without one."""
    if isinstance(value, dict):
        return {key: _settled(item, defaults) for key, item in value.items()}
    if isinstance(value, list):
        return [_settled(item, defaults) for item in value]
    if isinstance(value, str):
        return MARKED.sub(lambda m: "${%s:-%s}" % (m.group(1), defaults[m.group(1)]) if defaults.get(m.group(1))
                          else "${%s}" % m.group(1), value)
    return value


def _compose_text(name, unit, services, config, keys, kept):
    named = {volume.split(":")[0] for service in services.values() for volume in service.get("volumes") or []
             if isinstance(volume, str) and not volume.startswith((".", "/"))}
    networks = {net for service in services.values() for net in (service.get("networks") or [])}
    doc = {"name": name, "services": services}
    for key, used in (("networks", networks), ("volumes", named)):
        kept_ = {each: {k: v for k, v in (spec or {}).items() if k != "name" and v not in ({}, None, [])} or None
                 for each, spec in (config.get(key) or {}).items() if each in used}
        if kept_:
            doc[key] = {each: spec or {} for each, spec in kept_.items()}
    doc = _settled(doc, {row["key"]: row.get("default") or "" for row in kept})
    text = "\n".join(_yaml(doc)) + "\n"
    head = (f"# {unit['name']}: {unit.get('description') or ''}\n"
            "# cp .env.example .env, put your license in it, then: docker compose up -d. README.md says the rest.\n")
    return head + text


def _env_text(kept, keys, gone=()):
    lines = ["# The settings of this playground: copy this file to .env, which docker compose reads, and change what you need.", ""]
    for row in [row for row in kept if row["key"] in keys]:
        comment = _comment(row, gone)
        if row["key"] == "GRAVITEE_LICENSE":
            comment = "Your Gravitee Enterprise license, base64 (base64 -i license.key): required"
        if comment:
            lines.append(f"# {comment}")
        lines += [f"{row['key']}={row.get('default') or ''}", ""]
    return "\n".join(lines)


def _comment(row, gone=()):
    """What a setting is, without what only the Lab UI has (its buttons) nor what the archive leaves out (`gone`, products of
    the core)."""
    parts = re.split(r"(?<=[.;,])\s+", (row.get("comment") or "").strip())
    kept = []
    for part in parts:
        if any(word in part for word in LAB_ONLY):
            continue
        part = " and ".join(piece for piece in part.split(" and ") if not any(name in piece for name in gone))
        kept.append(part)
    return " ".join(kept).rstrip(",; ")


def _default(text):
    """The default of a `${NAME:-default}` text, else the text."""
    found = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}", str(text or ""))
    return found.group(1) if found else str(text or "")


def _guide(text, accounts):
    """The guide as Markdown any viewer shows: an action link is the name of the button of the app, an account link its user
    and password (or key), a block to run a plain block."""
    def account(match):
        one = accounts.get(match.group(2))
        if not one:
            return f"**{match.group(1)}**"
        secret = f"password `{_default(one['password'])}`" if one.get("password") else f"key `{_default(one.get('key'))}`"
        return f"**{match.group(1)}** (user `{one['username']}`, {secret})"
    text = re.sub(r"\[([^\]]+)\]\(account:([^)\s]+)\)", account, text)
    text = re.sub(r"\[([^\]]+)\]\(action:[^)]+\)", r"**\1**", text)
    return re.sub(r"^(```+|~~~+)(\w+)\s+run\s*$", r"\1\2", text, flags=re.M)


def _readme(gathered, raw, services, consoles, seed, missing):
    unit, folder = gathered["unit"], gathered["folder"]
    meta, requires = raw.get("x-gravitee") or {}, unit.get("requires") or {}
    accounts = meta.get("accounts") or []
    guide_file = folder / "guide.md"
    guide = _guide(guide_file.read_text(encoding="utf-8"), {one.get("username"): one for one in accounts if one.get("username")}) \
        if guide_file.is_file() else f"# {unit['name']}\n\n{unit.get('description') or ''}\n"
    first = re.search(r"^## ", guide, flags=re.M)
    intro, steps = (guide[:first.start()], guide[first.start():]) if first else (guide, "")
    before = ["## Before you start", "",
              "- **Docker** with Compose 5.0 or later, and 8 GB of memory for Docker, or more. The first start downloads the images"
              + (f" and takes about {unit['first_start']}." if unit.get("first_start") else ".")]
    features = requires.get("license") or []
    before.append("- **A Gravitee Enterprise license**" + (" with " + ", ".join(f"`{f}`" for f in features) if features else "")
                  + ". It goes in `.env`.")
    for host in requires.get("host") or []:
        help_ = re.sub(r"\(Troubleshooting, ([^)]+)\)", lambda m: f"([troubleshooting: {m.group(1)}]({TROUBLESHOOTING}))",
                       host.get("help") or "")
        before.append(f"- **{host['name']}**" + (f": {help_.rstrip('.')}." if help_ else ""))
    for pid in missing:
        before.append(f"- **The plugin {pid}**, from download.gravitee.io, in `plugins/`: this Lab did not have it to carry.")
    start = ["## Start it", "", "```bash", "cp .env.example .env", "```", "",
             "Put your license in `.env` (`GRAVITEE_LICENSE=`), then:", "", "```bash", "docker compose up -d", "```", ""]
    if seed:
        start += [f"It is ready when its seed has ended: `docker compose logs -f {seed}` ends with *Playground ready*.", ""]
    if consoles:
        start += ["Open:", ""] + [f"- [{name}]({url})" for name, url in consoles] + [""]
    if accounts:
        start += ["| For | User | Password or key |", "|---|---|---|"]
        start += [f"| {one.get('for', '')} | {('`' + one['username'] + '`') if one.get('username') else ''} | "
                  f"`{_default(one.get('password') or one.get('key'))}` |" for one in accounts]
        start.append("")
    if unit.get("terminal") in services:
        start += [f"The guide's commands run in the shell of `{unit['terminal']}`: `docker compose exec {unit['terminal']} sh`.", ""]
    stop = ["## Stop it", "", "```bash", "docker compose down", "```", "",
            "`docker compose down -v` deletes its data too: the next start is a first one."]
    return intro.rstrip() + "\n\n" + "\n".join(before) + "\n\n" + "\n".join(start).rstrip() + "\n\n" + steps.rstrip() \
        + "\n\n" + "\n".join(stop) + "\n"


def _consoles(config, keep, unit):
    """(name, address) of each console of the archive, those the playground lists first; a legacy one only when the playground
    lists its service."""
    asked = unit.get("consoles") or []
    found = []
    for name in sorted(config["services"], key=lambda one: (0, asked.index(one)) if one in asked else (1, one)):
        if name not in keep:
            continue
        labels = config["services"][name].get("labels") or {}
        for key, value in labels.items():
            match = re.fullmatch(r"io\.gravitee\.console\.([\w-]+)\.name", key)
            legacy = labels.get(f"io.gravitee.console.{match.group(1)}.legacy") == "true" if match else False
            if match and (not legacy or name in asked) and labels.get(f"io.gravitee.console.{match.group(1)}.url"):
                found.append((value, labels[f"io.gravitee.console.{match.group(1)}.url"]))
    return found


def _build(pid, root: Path):
    """The folder of playground `pid` in `root`, and what plan() says of it."""
    gathered = _gather(pid)
    unit = gathered["unit"]
    kept = _kept(gathered)
    config, keys = _resolve(gathered, kept)
    pristine = json.loads(json.dumps(config))   # the labels, before the archive drops them
    keep = _keep(config, gathered, _products(gathered))
    dropped = sorted(set(config["services"]) - keep)
    services, missing = _services(config, keep, gathered, root)
    _certificates(root)
    raw = compose.raw(str(paths.ROOT / unit["file"]))   # as written: an account's password is its setting and its default
    name = exports._slug(pid)
    (root / "docker-compose.yml").write_text(_compose_text(name, unit, services, config, keys, kept), encoding="utf-8")
    groups = {group["id"]: group["name"] for group in gathered["found"]["groups"]}
    owners = {name: (service.get("owner") or {}).get("id") for name, service in gathered["found"]["services"].items()}
    gone = sorted({groups.get(owners.get(name), name) for name in dropped})
    runs = [groups[id_] for id_ in groups if any(owners.get(name) == id_ for name in services)] + [unit["name"]]
    (root / ".env.example").write_text(_env_text(kept, keys, gone), encoding="utf-8")
    seed = unit.get("seed") if unit.get("seed") in services else None
    (root / "README.md").write_text(_readme(gathered, raw, services, _consoles(pristine, keep, unit), seed, missing),
                                    encoding="utf-8")
    origins, private = exports._origins([node for node in gathered["nodes"] if node[1] in gathered["units"][node[0]]],
                                        gathered["units"])
    return {"name": unit["name"], "file": f"{name}-docker-compose.zip", "services": list(services), "runs": runs, "leaves_out": gone,
            "settings": [key for key in keys], "license": (unit.get("requires") or {}).get("license") or [],
            "host": [host["name"] for host in (unit.get("requires") or {}).get("host") or []], "plugins_missing": missing,
            "private": private, "registries": exports._registries(gathered["found"], gathered["nodes"], gathered["units"])}


def plan(pid):
    """What the archive of playground `pid` holds and asks, the folder made and thrown away: {name, file, services, runs (the
    products of the core it keeps, then the playground), leaves_out (the products it does not), settings, license, host,
    plugins_missing, private, registries}."""
    with tempfile.TemporaryDirectory(prefix="lab-standalone-") as folder:
        return _build(pid, Path(folder) / exports._slug(pid))


def archive(pid):
    """plan(pid) and the zip of the folder, `<slug>/` at its root: {..., "data": base64}."""
    with tempfile.TemporaryDirectory(prefix="lab-standalone-") as folder:
        top = exports._slug(pid)
        made = _build(pid, Path(folder) / top)
        return {**made, "data": base64.b64encode(exports._zip(Path(folder) / top, top)).decode("ascii")}
