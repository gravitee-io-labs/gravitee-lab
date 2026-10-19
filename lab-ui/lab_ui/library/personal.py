"""What is created from the Lab UI: an add-on, a setup or a playground written from a few fields, kept on this machine (the
personal catalog, `.lab/catalogs/local/`, out of git) or in a catalog that is a folder of the person's. What is of this machine
can then be shared to a catalog (catalogs/contribute.py), or deleted. Also finds the skills an AI agent follows to create
the same."""

import json
import os
import posixpath
import re
import shutil
import stat

from . import check, layers, moves, starred, tested
from ..docker import compose, files, images
from .. import paths
from ..catalogs import registry, resolve
from ..catalogs.registry import CatalogError
from ..paths import NOUNS
from .project import brought

OWN = re.compile(r"local/[a-z0-9-]+")   # the id of what belongs to this machine: nothing else is moved or removed
IMAGE = re.compile(rf"((?:[a-z0-9.-]+(?::\d+)?/)?[a-z0-9]+(?:[._/-]+[a-z0-9]+)*)(?::({images.TAG.pattern}))?")
PORT = re.compile(r"(\d+)(?::(\d+))?")
# What tells an AI agent how to create each kind, a catalog to keep them in, and how to share one
SKILLS = {"addon": "new-addon", "setup": "new-setup", "playground": "new-playground", "catalog": "new-catalog",
          "share": "share-item"}


def skills():
    """The skill files found on disk: {what it is for: its path}."""
    found = {what: paths.SKILL_FILE.format(name) for what, name in SKILLS.items()}
    return {what: path for what, path in found.items() if (paths.ROOT / path).is_file()}


# ---------------------------------------------------------------------------------------------------------------------
# What the fields must be
# ---------------------------------------------------------------------------------------------------------------------

def _line(text):
    return " ".join(str(text or "").split())


def _quoted(text):
    """A text as YAML reads it back: quoted as JSON, its characters kept as typed (YAML refuses the escapes JSON
    gives an emoji)."""
    return json.dumps(text, ensure_ascii=False)


def _where(catalog):
    """Where a new item is kept: None for Personal (no catalog, or `local`), else the namespace of `catalog`, a catalog that is
    a folder of the person's, linked and writable. Any other (a catalog that is a repository, one the Lab does not have) is
    `not_editable`: a repository changes by a pull request, which is Share."""
    if catalog in (None, "", paths.LOCAL):
        return None
    entry = registry.entry(catalog) if isinstance(catalog, str) else None
    row = registry.inputs().get(catalog) if entry else None
    if entry is not None and entry["kind"] == "folder" and row and row["state"] == "linked" \
            and os.access(os.path.realpath(registry.install_dir(catalog)), os.W_OK | os.X_OK):
        return catalog
    shown = row["name"] if row else resolve.shown(catalog, 40)
    raise CatalogError("not_editable", f"{shown} cannot take new items from the Lab. Choose Personal or a folder catalog.",
                       400)


def _named(kind, name, ns=None):
    """(name, id): the id is the name as a slug, `local/<slug>` for Personal, `@<ns>/<slug>` in the catalog `ns` (the bare
    slug in the first one, which owns the bare ids)."""
    name = _line(name)
    slug = paths.slug(name)
    if not slug or len(name) > 60:
        raise ValueError(f"Give the {NOUNS[kind]} a name of 60 characters at most")
    if "$" in name:   # Compose would read a variable there
        raise ValueError("Write the name without $")
    if slug == "local":   # the folder of what belongs to this machine
        raise ValueError("Choose another name than local")
    return name, paths.canonical(f"@{ns}/{slug}") if ns else f"local/{slug}"


def _free(kind, uid, replaced=None):
    """Refuses a name another item of the kind has, in any catalog or of this machine; `replaced` is the one being saved."""
    names = {other.split("/")[-1] for other in paths.ids(kind) if other != replaced}
    if uid.split("/")[-1] in names:
        raise ValueError(f"Another {NOUNS[kind]} has this name")


def _described(kind, description, required):
    """What an item is, in one line. An add-on and a playground must say it: `make check` asks for it."""
    description = _line(description)
    if required and not description:
        raise ValueError(f"Describe the {NOUNS[kind]} in one line")
    if "$" in description:   # Compose would read a variable there
        raise ValueError("Write the description without $")
    return description


def _category(project, category, required):
    """The category of an item, one the core file lists. An add-on has one, and so does anything kept in a catalog: the
    catalogs group by it."""
    category = _line(category)
    if (category or required) and category not in project["categories"]:
        raise ValueError("Choose a category")
    return category


def _chosen(project, kind, ids, ns):
    """The add-ons or setups to include, each once. What a catalog keeps includes nothing of this machine, and only what it
    holds or the catalogs it declares hold."""
    if not isinstance(ids or [], (list, tuple)) or not all(isinstance(one, str) for one in ids or []):
        raise ValueError(f"The {NOUNS[kind]}s must be a list of ids")
    ids, units = sorted(set(ids or [])), project[kind + "s"]
    declared = set((registry.inputs().get(ns) or {}).get("requires_catalogs") or ()) if ns else set()
    for uid in ids:
        if uid not in units:
            raise ValueError(f"No such {NOUNS[kind]}: {uid}")
        if ns and units[uid]["personal"]:
            raise ValueError(f"{units[uid]['name']} is of this machine: share it first")
        if ns and paths.origin(uid) not in (ns, *declared):
            raise ValueError(f"{units[uid]['name']} is in a catalog that {registry.inputs()[ns]['name']} does not declare")
    return ids


def _together(project, addons):
    """Refuses add-ons that cannot run together, with the ones they need."""
    clashes = layers.conflicts(project, layers.closure(project, addons))
    if clashes:
        a, b, reason = (clashes[0][key] for key in ("a", "b", "reason"))
        names = f"{project['addons'][a]['name']} and {project['addons'][b]['name']}"
        raise ValueError(f"{names} cannot run together" + (f": {reason}" if reason else ""))


def _publisher(project, port):
    """The name of what publishes a host port, None when it is free: the add-on or the playground `make check` finds,
    which may publish it on a service of the core, else the service itself."""
    kind, uid = paths.unit_of(check.publishers().get(port, "")) or (None, None)
    unit = project[kind + "s"].get(uid) if kind else None
    if unit:
        return unit["name"]
    return next((s["label"] for s in project["services"].values() if port in s["ports"]), None)


def _port(project, port):
    """(host port, container port) from `host` or `host:container`; None without a port."""
    text = _line(port)
    if not text:
        return None
    found = PORT.fullmatch(text)
    host, target = (int(found.group(1)), int(found.group(2) or found.group(1))) if found else (0, 0)
    if not (0 < host < 65536 and 0 < target < 65536):
        raise ValueError("Give the port as 8098, or as 8098:80")
    publisher = _publisher(project, str(host))
    if publisher:
        raise ValueError(f"Port {host} is published by {publisher}")
    return host, target


# ---------------------------------------------------------------------------------------------------------------------
# What is written
# ---------------------------------------------------------------------------------------------------------------------

def _metadata(kind, name, category, description):
    """The x-gravitee block: what the Lab UI shows of the item."""
    lines = [f"  kind: {kind}", f"  name: {_quoted(name)}"]
    lines += [f"  category: {category}"] if category else []
    lines += [f"  description: {_quoted(description)}"] if description else []
    return "x-gravitee:\n" + "".join(f"{line}\n" for line in lines)


def _include(file, included):
    """The `include` block of a file: these add-ons and setups ((kind, id) pairs), each written from its folder."""
    folder = posixpath.dirname(file)
    lines = [f"  - {posixpath.relpath(paths.unit_file(kind, uid), folder)}\n" for kind, uid in included]
    return "include:\n" + "".join(lines)


def _service(slug, name, description, repository, variable, tag, port):
    """The one service of a new add-on: its version is a variable the Images page sets, its port a console to open."""
    lines = [f"  {slug}:", f"    image: {repository}:${{{variable}:-{tag}}}", f"    container_name: gio-{slug}",
             "    restart: unless-stopped"]
    if port:
        lines += ["    ports:", f'      - "127.0.0.1:{port[0]}:{port[1]}"     # Web UI']
    lines += ["    labels:", f"      io.gravitee.name: {_quoted(name)}",
              f"      io.gravitee.description: {_quoted(description)}"]
    if port:
        lines += [f"      io.gravitee.console.{slug}.name: {_quoted(name)}",
                  f"      io.gravitee.console.{slug}.url: http://localhost:{port[0]}"]
    lines += ["    networks:", "      - internal"]
    return "services:\n\n" + "".join(f"{line}\n" for line in lines)


def _write(project, kind, uid, texts):
    """Writes the folder of a new add-on or playground ({file name: text}) and has Compose check its compose file with the
    project, as the editor does before it saves. Refused, nothing of it stays. A catalog's folder is written through its link
    only while the place stays inside it. Answers {file, id}."""
    path = paths.unit_file(kind, uid)
    folder = (paths.ROOT / path).parent
    ns = paths.origin(uid)
    if ns not in (None, paths.LOCAL) and not paths.contained(path, f"{paths.CATALOGS_DIR}/{ns}"):
        raise ValueError("The folder of the catalog does not hold it")
    if folder.exists():
        raise ValueError(f"A folder named {folder.name} is already there")
    folder.mkdir(parents=True)
    try:
        for name, text in texts.items():
            paths.write_atomic(folder / name, text)
        files.accepted(project, path, texts["compose.yml"])
    except Exception:
        shutil.rmtree(folder)
        raise
    return {"file": path, "id": uid}


# ---------------------------------------------------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------------------------------------------------

def create_addon(project, name, description, category, image, port=None, catalog=None):
    """Writes an add-on that runs one image, to be completed in the editor: in the folder `addons/<name>/` of the personal
    catalog, or of the folder catalog `catalog`, with its README. `image` is repository[:tag], `port` host[:container] or
    nothing. Answers {file, id}. Refused (RuntimeError, `layers.WAITING`) while the old layout is being moved."""
    layers.ready()
    ns = _where(catalog)
    name, uid = _named("addon", name, ns)
    slug = uid.split("/")[-1]
    _free("addon", uid)
    # Its service and its version variable are named after it
    variable = slug.upper().replace("-", "_") + "_VERSION"
    if slug in project["services"]:
        raise ValueError(f"The project has a service named {slug}")
    if slug[0].isdigit():
        raise ValueError("Start the name with a letter")
    if variable in {component["id"] for component in images.components(project)}:
        raise ValueError(f"The project has an image version named {variable}: choose another name")
    description = _described("addon", description, required=True)
    category = _category(project, category, required=True)
    found = IMAGE.fullmatch(_line(image))
    if not found:
        raise ValueError("Give the image as repository:tag")
    service = _service(slug, name, description, found.group(1), variable, found.group(2) or "latest",
                       _port(project, port))
    text = _metadata("addon", name, category, description) + "\n" + service
    return _write(project, "addon", uid, {"compose.yml": text, "README.md": f"# {name}\n\n{description}\n"})


def save_setup(project, name, description, addons, sid=None, category=None, catalog=None, check_only=False):
    """Saves add-ons as a setup, a compose file that includes them: `setups/<name>.yml` of the personal catalog, or of the
    folder catalog `catalog`. With `sid`, that setup of this machine is replaced; renamed, its file moves, and what includes it
    follows. Answers {file, changes, id}: where it is, the other files a rename writes again, and its id. With `check_only`,
    nothing is written: what it would do is said first. It is shared from its menu, not here. Nothing starts or stops. Refused
    (RuntimeError, `layers.WAITING`) while the old layout is being moved, a check too: what it would say is read from files that
    are not where the on file says."""
    layers.ready()
    ns = _where(catalog)
    if sid is not None and ns:
        raise ValueError("Share it from its menu")
    name, new = _named("setup", name, ns)
    description = _described("setup", description, required=False)
    category = _category(project, category, required=bool(ns))
    addons = _chosen(project, "addon", addons, ns)
    if not addons:
        raise ValueError("Choose at least one add-on")
    _together(project, addons)
    if sid is not None and not (paths.personal(sid) and sid in project["setups"]):
        raise ValueError("Only your own setups can be changed here")
    if new != sid:
        _free("setup", new, replaced=sid)
    renamed = sid is not None and sid != new
    if renamed:   # the on file may name its file
        _not_held(project, "setup", sid)
    file = paths.unit_file("setup", new)
    changes = _including(paths.unit_file("setup", sid)) if renamed else []
    if check_only:
        return {"file": file, "changes": changes, "id": new}
    target = paths.ROOT / file
    if ns and not paths.contained(file, f"{paths.CATALOGS_DIR}/{ns}"):
        raise ValueError("The folder of the catalog does not hold it")
    target.parent.mkdir(parents=True, exist_ok=True)
    if renamed:
        _move(paths.unit_file("setup", sid), file)
        starred.moved("setup", sid, new)
    included = _include(file, [("addon", aid) for aid in addons])
    paths.write_atomic(target, _metadata("setup", name, category, description) + "\n" + included)
    return {"file": file, "changes": changes, "id": new}


def create_playground(project, name, description, category=None, setups=None, addons=None, catalog=None):
    """Writes a playground to be completed in the editor: the folder `playgrounds/<name>/` of the personal catalog, or of the
    folder catalog `catalog`, with its compose file, which includes these setups and add-ons and runs nothing yet, the first
    lines of its guide and its README. Answers {file, id}. Refused (RuntimeError, `layers.WAITING`) while the old layout is
    being moved."""
    layers.ready()
    ns = _where(catalog)
    name, uid = _named("playground", name, ns)
    _free("playground", uid)
    description = _described("playground", description, required=True)
    category = _category(project, category, required=bool(ns))
    setups, addons = _chosen(project, "setup", setups, ns), _chosen(project, "addon", addons, ns)
    _together(project, [*addons, *(aid for sid in setups for aid in project["setups"][sid]["members"])])
    included = [("setup", sid) for sid in setups] + [("addon", aid) for aid in addons]
    text = _metadata("playground", name, category, description) + "\n" \
        + (_include(paths.unit_file("playground", uid), included) + "\n" if included else "") + "services: {}\n"
    guide = f"# {name}\n\n{description}\n\n## 1. First step\n"
    return _write(project, "playground", uid,
                  {"compose.yml": text, "guide.md": guide, "README.md": f"# {name}\n\n{description}\n"})


# ---------------------------------------------------------------------------------------------------------------------
# Sharing and deleting what is of this machine
# ---------------------------------------------------------------------------------------------------------------------

def _own(kind, uid, done):
    """The file of an item of this machine, the only ones something is `done` to here. The id is checked before a
    path is made of it: nothing outside the personal catalog is ever moved or removed."""
    if not OWN.fullmatch(uid):
        raise ValueError(f"Only your own {NOUNS[kind]}s can be {done} here")
    path = paths.unit_file(kind, uid)
    if not (paths.ROOT / path).is_file():
        raise paths.NotFound(f"No such {NOUNS[kind]}: {uid}")
    return path


def _users(project, kind, uid):
    """The names of what includes an add-on or a setup, itself or through what it includes."""
    if kind == "addon":
        units = [unit for other in paths.KINDS for unit in project[other + "s"].values()]
        return sorted(unit["name"] for unit in units if uid in brought(unit))
    if kind == "setup":
        return sorted(p["name"] for p in project["playgrounds"].values() if uid in p["setups"])
    return []


def _on(on, kind, uid, running):
    """"Stop it first" while something of an item runs, "Turn it off first" while it is on and does not run, else "". An item
    is on when the on file turns it on; a setup, only when the on file names it, which a person may write: a Start or a Stop
    names its add-ons instead."""
    if _turned_on(on, kind, uid):
        return "Stop it first" if running else "Turn it off first"
    return ""


def held(project, on, kind, uid, deleting=False, running=False):
    """Why an item of this machine cannot be deleted now; "" when it can. `on` is layers.current(). It goes only while it is
    off: the on file would name a file that is not there; and what another item includes is not deleted: that one would not
    load. Sharing it is never held: a share is a copy, the item stays, runs and keeps its settings. Pure: the state gives it
    to the item's menu, delete() refuses with it."""
    if not deleting:
        return ""
    users = [] if _turned_on(on, kind, uid) else _users(project, kind, uid)
    return _on(on, kind, uid, running) or (f"Used by {', '.join(users)}" if users else "")


def _turned_on(on, kind, uid):
    """Whether an item is on, as held() sees it: a setup only when the on file names it."""
    return (kind == "setup" and uid in on["setups"]) or layers.is_on(kind, uid, on["playground"], on["effective"])


def _running(project, kind, uid):
    """Whether something of an item runs now, as held() says it: nothing does while Docker does not answer."""
    containers, _ = compose.containers()
    unit = project[kind + "s"].get(uid)
    names = layers.unit_services(project, unit, True) if unit else []
    return any(containers.get(name, {}).get("state") == "running" for name in names)


def _not_held(project, kind, uid, deleting=False):
    """Refuses while the item is on (Docker is asked what runs only for an item that is on) and, to delete it, while another
    item includes it."""
    on = layers.current(project)
    running = _turned_on(on, kind, uid) and _running(project, kind, uid)
    reason = held(project, on, kind, uid, deleting, running=running) if deleting else _on(on, kind, uid, running)
    if reason:
        raise RuntimeError(reason)


def delete(project, kind, uid):
    """Deletes an item of this machine: the file of a setup, the folder of an add-on or a playground with what it holds. What
    a catalog keeps is changed in the catalog, like any file. Its star and its tested sets go with it. Refused (RuntimeError,
    `layers.WAITING`) while the old layout is being moved: an item the old on file turns on would not be seen as on."""
    layers.ready()
    path = _own(kind, uid, "deleted")
    _not_held(project, kind, uid, deleting=True)
    target = paths.ROOT / path
    if kind == "setup":
        target.unlink()
    else:
        shutil.rmtree(target.parent)
    starred.moved(kind, uid)
    tested.moved(kind, uid)
    return {}


def _personal_files():
    """The compose files of the items of this machine, relative to the project."""
    return [file for file in paths.files() if (paths.unit_of(file) or ("", ""))[1].startswith("local/")]


def _including(path):
    """The files of this machine that include a file themselves."""
    return [file for file in _personal_files() if path in compose.includes(file)]


def follow(old, new):
    """Writes again, in the files of this machine that name the file or folder `old` (relative to the project, as an include or
    a path), the place `new` it has now (`moves.repoint`: only the paths that lead to what moved change). Each file is
    replaced whole and the change is journaled. A file that is a link or is not text is left. Idempotent."""
    for file in _personal_files():
        path = paths.ROOT / file
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        folder = posixpath.dirname(file)
        written = moves.repoint(text, folder, folder, [(old, new)])
        if written != text:
            paths.write_atomic(path, written, mode=stat.S_IMODE(path.stat().st_mode))
            moves.journal("repoint", path=file, old=old, new=new)


def _move(path, target):
    """Moves the file of a setup of this machine, then writes again the includes of it in the files that name it."""
    (paths.ROOT / path).rename(paths.ROOT / target)
    follow(path, target)


def share(project, kind, uid, check_only=False, to=""):
    """Shares an item of this machine to the catalog `to` (catalogs/contribute.py: a copy, the item stays); the answer is its
    ShareCheck or ShareDone. Without `to` there is no share to make, since nothing is shared into the Lab: a check answers the
    item's file in its own catalog, the problems that keep it from being shared anywhere (none) and the other files that would
    change (none), for the page to ask for the destinations; asked to share, it is refused."""
    if to:
        from ..catalogs import contribute   # here: it reads this module
        return contribute.share(project, kind, uid, to, check_only)
    path = _own(kind, uid, "shared")
    if not check_only:
        raise ValueError("Choose where to share")
    return {"file": path.split("/", 3)[3] if path.startswith(f"{paths.CATALOGS_DIR}/{paths.LOCAL}/") else path,
            "problems": [], "changes": []}
