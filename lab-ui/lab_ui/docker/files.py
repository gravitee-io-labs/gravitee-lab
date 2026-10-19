"""The editor: the project's compose files, the on file and a playground's guide (the ones shown with a file, read,
check, save), the secrets and settings of a .env as a form, the core's or an add-on's; and what Compose would make
each service from once a change is saved, for what applying it recreates.

What a catalog installed from git holds is shown and never written: its compose files and guides are read, and refused
by check and save as "Part of" the catalog (a 409); its .env, which is this machine's, is a form like any other. The
files of a folder catalog and of this machine are the person's own and are edited. A file of a catalog is checked, and
what it would change previewed, from copies in the Lab UI's scratch folder, each read as if it were in its own folder
(`project_directory`), so that nothing is ever written beside a catalog file."""

import hashlib
import json
import posixpath
import re
import threading

from . import compose, envfiles
from ..library import layers
from .. import paths
from ..catalogs import registry
from ..catalogs.resolve import shown
from ..paths import CORE_FILE, ENV_FILE, ON_FILE, ROOT, write_atomic
from ..library.project import brought

SECTIONS = (("Secrets", "secret"), ("Settings", "setting"))
POSITION = re.compile(r"L(\d+)\.C(\d+)")
UNKNOWN_KEY = re.compile(r"additional propert(?:y|ies) '([^']+)'")
TOP = (CORE_FILE, ENV_FILE, ON_FILE)   # the files of the project as a whole: they show everything that is on


# ---------------------------------------------------------------------------------------------------------------------
# Compose files
# ---------------------------------------------------------------------------------------------------------------------

def _is_on(kind, uid, unit, on):
    """Whether a file is part of what runs with the platform: an add-on or the playground that is on, a setup whose
    add-ons are all on."""
    if kind == "setup":
        members = unit.get("members") or []
        return bool(members) and set(members) <= set(on["effective"])
    return layers.is_on(kind, uid, on["playground"], on["effective"])


def _runs(project, kind, unit, is_on, running):
    """Whether something a file describes runs now (`running`: the services whose container runs), so that applying
    it recreates something: an add-on's or a playground's services, or what it brings while it is on. Only while it
    is on: what runs without being on is not what an apply makes again. A setup never runs: what it includes is on
    through the on file, its add-ons' files do."""
    if kind == "setup" or not unit or not is_on:
        return False
    return bool(set(layers.unit_services(project, unit, is_on)) & running)


# What sits beside a unit's compose file, and the kinds that have it: an add-on and a playground a .env, a playground
# a guide
BESIDE = {".env": ("addon", "playground"), "guide.md": ("playground",)}


def _owner(path):
    """(kind, id) of the add-on, setup or playground found on disk that a compose file, a .env or a guide belongs to, or
    None. By the shape of the path (paths.unit_of, which asks no listing), then one look for the unit's compose file."""
    if not isinstance(path, str):
        return None
    folder, _, name = path.rpartition("/")
    file = f"{folder}/compose.yml" if name in BESIDE else path
    unit = paths.unit_of(file)
    if not unit or (name in BESIDE and unit[0] not in BESIDE[name]):
        return None
    return unit if (ROOT / file).is_file() else None


def _unit_of(path):
    """(kind, id) of the add-on, setup or playground found on disk that a compose file, a .env or a guide belongs
    to."""
    unit = _owner(path)
    if unit is None:
        raise ValueError("Not a file of this project")
    return unit


def _is_guide(path):
    """Whether a path is the guide of a playground found on disk."""
    return isinstance(path, str) and path.endswith("/guide.md") and _owner(path) is not None


class ReadOnly(RuntimeError):
    """A file that the Lab UI shows and does not write: it is part of a catalog installed from git, which an update
    replaces. A RuntimeError, so that the API answers 409, as for a file that changed on disk."""


def _rows(memo):
    """What the registry says of each catalog ({namespace: row}), asked once per call (`memo`); none when it cannot
    say."""
    if "rows" not in memo:
        try:
            rows = registry.inputs()
        except Exception:  # noqa: BLE001 - a registry that cannot answer names nothing: the namespace stands in
            rows = None
        memo["rows"] = {ns: row for ns, row in rows.items() if isinstance(row, dict)} if isinstance(rows, dict) else {}
    return memo["rows"]


def _part_of(uid, memo):
    """The reason the files of a unit may not be changed, "Part of Acme Hub", when the unit is part of a catalog
    installed from git; None for the items of this machine, of a folder catalog (a link to the author's folder, whose
    files are theirs) and of a project that has no catalogs. A git install is a real folder that holds the manifest the
    Lab writes, or one the registry says is: either is enough, so that a doubt refuses."""
    try:
        ns = paths.origin(uid)
    except paths.NotFound:
        return None
    if ns in (None, paths.LOCAL):
        return None
    folder, row = ROOT / paths.CATALOGS_DIR / ns, _rows(memo).get(ns, {})
    if not (row.get("kind") == "git" or (not folder.is_symlink() and (folder / paths.MANIFEST).is_file())):
        return None
    name = shown(row["name"], 80) if isinstance(row.get("name"), str) else ""
    return f"Part of {name or ns}"


def _refuse(path):
    """Raises ReadOnly for a compose file or a guide that is part of a git catalog: it is read, never written."""
    unit = _owner(path)
    reason = _part_of(unit[1], {}) if unit else None
    if reason:
        raise ReadOnly(reason)


def _in_catalog(path):
    """Whether a path is a unit file of a catalog's folder, git or folder, not of this machine's own items nor of the
    project (the core, the on file, the layout before catalogs): the files that are checked from a copy in the scratch
    folder, never from one beside them."""
    unit = paths.unit_of(path) if isinstance(path, str) else None
    try:
        return unit is not None and paths.origin(unit[1]) not in (None, paths.LOCAL)
    except paths.NotFound:
        return False


def _unit(project, kind, uid):
    """An add-on, setup or playground of the project; {} when its file does not load."""
    return project[f"{kind}s"].get(uid) or {}


def _section(title, on, runs, file, env_file=None, guide=None, reason=None):
    """A section of the pane. `reason`: why its compose file and guide are only read ("Part of Acme Hub"); then it is
    marked `readonly`, and its .env, which is a form, is not."""
    files = [(file, "compose")] + ([(guide, "guide")] if guide else []) + ([(env_file, "env")] if env_file else [])
    return {"title": title, "on": on, "runs": runs, **({"readonly": True, "reason": reason} if reason else {}),
            "files": [{"path": path, "name": path.split("/")[-1], "kind": kind} for path, kind in files]}


def _unit_section(project, on, running, kind, uid, memo):
    """A unit's compose file, a playground's guide, and its .env when it has settings of its own. One that does not
    load is still listed, under its id, so that it can be repaired."""
    unit, guide = _unit(project, kind, uid), paths.unit_guide(kind, uid)
    is_on = _is_on(kind, uid, unit, on)
    return _section(unit.get("name") or uid, is_on, _runs(project, kind, unit, is_on, running),
                    paths.unit_file(kind, uid), unit.get("env_file") if unit.get("settings") else None,
                    guide if guide and (ROOT / guide).is_file() else None, _part_of(uid, memo))


def _running():
    """The services whose container runs now: none while Docker does not answer."""
    containers, _ = compose.containers()
    return {name for name, container in containers.items() if container.get("state") == "running"}


def sections(project, path):
    """The files the editor shows with one of them, a compose file, a guide or a .env, a section per unit: the core, the
    unit the file belongs to, the setups a playground includes, then the add-ons brought, by name. With a file of
    the project as a whole: the on file when there is one, then everything that is on. Each says whether it is on,
    and whether something of it runs (`runs`): only then does applying it recreate anything. The core runs while a
    service of the platform does. A section of a unit that is part of a git catalog is `readonly`, with its `reason`."""
    belongs = None if path in TOP else _unit_of(path)   # before anything is asked of Docker: a path that is none is refused
    on, running, memo = layers.current(project), _running(), {}
    if belongs is None:
        units, ids = [("playground", on["playground"])] if on["playground"] else [], on["effective"]
    else:
        kind, uid = belongs
        unit = _unit(project, kind, uid)
        units, ids = [(kind, uid)], brought(unit) if unit else []
    units += [("setup", sid) for kind, uid in units for sid in _unit(project, kind, uid).get("setups", [])]
    addons = sorted((_unit_section(project, on, running, "addon", aid, memo) for aid in ids),
                    key=lambda section: section["title"].lower())
    core = any(service["owner"]["kind"] == "platform" for name, service in project["services"].items()
               if name in running)
    on_section = [_section("What is on", True, core, ON_FILE)] \
        if path in TOP and (ROOT / ON_FILE).is_file() else []
    return [_section("Core", True, core, CORE_FILE, ENV_FILE), *on_section,
            *(_unit_section(project, on, running, *unit, memo) for unit in units), *addons]


def _file(path):
    """Only the core file, the on file, the add-on, setup and playground files and the guides are read or written."""
    if not isinstance(path, str) or (path not in (CORE_FILE, ON_FILE) and not paths.unit_of(path)
                                     and not _is_guide(path)):
        raise ValueError("Not a compose file of this project")
    if not (ROOT / path).is_file():
        raise paths.NotFound(f"{path} does not exist")
    return ROOT / path


def _version(path):
    """A hash of a file's content, to notice that it changed on disk since it was opened."""
    file = ROOT / path
    return hashlib.sha256(file.read_bytes() if file.is_file() else b"").hexdigest()[:16]


def read(path):
    """A file's text, with the version that tells whether it changed on disk since."""
    return {"content": _file(path).read_text(encoding="utf-8"), "version": _version(path)}


def _first_use(content, key):
    """(line, column) of the first line that sets a key, or (None, None)."""
    for number, text in enumerate(content.split("\n"), 1):
        if re.match(rf"\s*{re.escape(key)}\s*:", text):
            return number, text.index(key) + 1
    return None, None


UNSET = re.compile(r'The \\*"(\w+)\\*" variable is not set')


def _unset(message):
    """The name of the variable that a line of Compose says is not set, else None."""
    found = UNSET.search(message)
    return found.group(1) if found else None


def _problem(message, content, severity):
    """A line Compose printed, with the place it points at in the edited text when it names one."""
    # Compose points at a place as "L3.C2" or "L1.C1-L3.C2" (the last position is the place), or as "line 12"
    positions = POSITION.findall(message) or [(line, "1") for line in re.findall(r"line (\d+)", message)]
    line, column = (int(positions[-1][0]), int(positions[-1][1])) if positions else (None, None)
    unknown = UNKNOWN_KEY.search(message)
    if line is None and unknown:   # an unknown key comes without a position
        line, column = _first_use(content, unknown.group(1))
    return {"message": message, "line": line, "column": column, "severity": severity}


_checking = threading.Lock()


def _including(project, path):
    """The add-on and playground files that include this one, themselves or through what they include: in each of
    them, Compose reads it from disk."""
    kind, uid = paths.unit_of(path) or (None, None)
    if kind == "addon":
        units = [*project["addons"].values(), *project.get("playgrounds", {}).values()]
        return {unit["file"] for unit in units if uid in brought(unit)}
    if kind == "setup":
        return {playground["file"] for playground in project["playgrounds"].values() if uid in playground["setups"]}
    return set()


def _text(content):
    """The text of an edit, else the ValueError (a 400) that a content which is not text is."""
    if not isinstance(content, str):
        raise ValueError("The content must be text")
    return content


def _in_place(path, copy):
    """The item of an `include` list that reads `copy` as the file `path` is read: from the folder of that file, so that
    its relative paths, its includes and its .env are the ones the file has in place."""
    return f"  - path: {json.dumps(str(copy))}\n    project_directory: {json.dumps(str(ROOT / posixpath.dirname(path)))}\n"


def _alone(path, copy):
    """The text that includes `copy` as the file `path` is included, and nothing else."""
    return "include:\n" + _in_place(path, copy)


def validate(project, path, content):
    """Checks an edited text with docker compose config, in place of its file: {valid, problems}. The core and
    every add-on and playground that loads are checked with it, as they use each other's services, but those that
    include it: they would bring it as it is on disk, which Compose merges with the edited text. The on file is
    checked as Compose reads it, on the core. A guide is markdown: Compose has nothing to say of it. ReadOnly for a file
    of a git catalog, which is not edited."""
    target = _file(path)
    _refuse(path)
    _text(content)
    if _is_guide(path):
        return {"valid": True, "problems": []}
    including = _including(project, path)
    on_top = [unit["file"] for kind in ("addons", "playgrounds") for unit in project[kind].values()
              if unit["file"] not in including]
    if path not in (CORE_FILE, ON_FILE) and path not in on_top:   # a setup, or a file being repaired
        on_top.append(path)
    # The edited text is read from a hidden copy, so that Compose reads it in place of its file. Always the same name:
    # the include file generated for it is the same too. One check at a time, so that none reads another's copy.
    # Next to the file, so that its relative paths and its .env still apply; but for a file of a catalog, in the scratch
    # folder, with the folder of the file as its project directory: nothing is written in a catalog.
    catalog = _in_catalog(path)
    tmp = compose.scratch() / f"check-{hashlib.sha256(path.encode()).hexdigest()[:12]}.yml" if catalog \
        else target.with_name(f".{target.name}.check.yml")
    made = [tmp, tmp.with_suffix(".include.yml")] if catalog else [tmp]
    elsewhere = ""
    with _checking:
        try:
            write_atomic(tmp, content, mode=0o600)
            if catalog:
                write_atomic(made[1], _alone(path, tmp), mode=0o600)
                files = [*compose.all_files([file for file in on_top if file != path]), "-f", str(made[1])]
            else:
                copy = tmp.relative_to(ROOT).as_posix()
                files = ["-f", CORE_FILE, "-f", copy] if path == ON_FILE \
                    else compose.all_files(on_top, replace={path: copy})
            rc, _, err = compose.run([*compose.COMPOSE, *files, "config", "-q"], timeout=60)
            if rc != 0 and path not in (CORE_FILE, ON_FILE) and not _concerns(err, (str(tmp), tmp.name, path)):
                # What stops Compose is another file (an include of a catalog that is gone, say): it is not this text's to answer for.
                # This one is loaded alone, with the core, and the other file is said as a warning.
                alone = [*compose.all_files([]), "-f", str(made[1])] if catalog else compose.all_files([path], replace={path: copy})
                rc_alone, _, err_alone = compose.run([*compose.COMPOSE, *alone, "config", "-q"], timeout=60)
                if rc_alone == 0:
                    elsewhere = _first_error(err)
                rc, err = rc_alone, err_alone
        finally:
            for file in made:
                file.unlink(missing_ok=True)
    # The copy's name means nothing to the reader: show the real one
    messages = [line.strip().replace(str(tmp), path).replace(tmp.name, target.name)
                for line in err.splitlines() if line.strip()]
    # Every add-on is loaded with the file, and Compose says of each variable one of them needs and nobody set that it is not set: that is
    # the other file's to say, in its own editor. Said here only when this text uses the variable.
    messages = [m for m in messages if _unset(m) is None or re.search(rf"\$\{{?{_unset(m)}\b", content)]
    severity = "error" if rc != 0 else "warning"
    # Every add-on is loaded with the file: a warning about one of them is not an error of this one
    problems = [_problem(m, content, "warning" if "level=warning" in m else severity) for m in messages]
    if elsewhere:
        problems.append({"message": f"Another file does not load: {elsewhere}", "line": None, "column": None, "severity": "warning"})
    return {"valid": rc == 0, "problems": problems}


def _concerns(err, names):
    """Whether a line of Compose's errors names one of `names`: the file that was checked, or the copy that stands for it."""
    return any(name in line for line in err.splitlines() if line.strip() and "level=warning" not in line for name in names)


def _first_error(err):
    """The first line of Compose's errors that is not a warning, cut short."""
    first = next((line.strip() for line in err.splitlines() if line.strip() and "level=warning" not in line), "")
    return first.replace(f"{ROOT}/", "")[:300]   # the project's own folder says nothing to the reader


def accepted(project, path, content):
    """validate(), for a text about to stay on disk: what Compose refuses is raised, with its errors."""
    check = validate(project, path, content)
    if not check["valid"]:
        errors = [problem["message"] for problem in check["problems"] if problem["severity"] == "error"]
        raise ValueError("Invalid compose file: " + "; ".join(errors)[:500])
    return check


def save(project, path, content, version):
    """Writes an edited file when it still is the version that was opened and Compose accepts it: {content, version,
    problems}, the warnings Compose still has. ReadOnly for a file of a git catalog."""
    target = _file(path)
    _refuse(path)
    _text(content)
    if _version(path) != version:
        raise RuntimeError(f"{path} changed on disk since you opened it: reload it first")
    check = accepted(project, path, content)
    write_atomic(target, content)
    return {**read(path), "problems": check["problems"]}


# ---------------------------------------------------------------------------------------------------------------------
# What Compose would make each service from once a change is saved: what applying it recreates
# ---------------------------------------------------------------------------------------------------------------------

def _copy(path):
    """Where Compose reads a file in its place: a hidden copy next to it, so that its relative paths and its .env
    still apply."""
    folder, name = posixpath.split(path)
    return posixpath.join(folder, f".{name}.effects.yml")


def _chain(path, entries):
    """The files that read `path` from the on file's entries: it, and every file among them and what they include
    that includes one of these. Empty when nothing on includes it."""
    found, todo = {}, list(entries)
    while todo:
        file = todo.pop()
        if file not in found:
            found[file] = compose.includes(file)
            todo += found[file]
    chain, grew = {path} & set(found), True
    while grew:
        reading = {file for file, included in found.items() if file not in chain and set(included) & chain}
        chain |= reading
        grew = bool(reading)
    return chain


def _scratch_name(path):
    """The name, in the scratch folder, of the copy of a file of the project: the same for the same file."""
    return compose.scratch() / f"draft-{hashlib.sha256(path.encode()).hexdigest()[:12]}.yml"


def _scratch_reads(path, content, entries, chain):
    """(writes, files) that have Compose read `content` in place of `path` when a file among those that read it from the
    on file is one of a catalog, and so nothing may be written beside any of them: every copy is made in the scratch
    folder and is read from the folder of its file (_in_place), and the on file's list is written again with each copy
    in the place of the file it stands for, because the place of a file in that list decides what Compose makes of a
    service that several of them change. A file that includes one of the chain is copied with the item that names it
    written again to a file that includes that copy in place (compose.retargeted gives the path from the folder of the
    includer, which is the folder its copy is read from)."""
    copies = {file: _scratch_name(file) for file in chain}
    inner = {file: [one for one in compose.includes(file) if one in chain] for file in chain - {path}}
    wrapped = {one for found in inner.values() for one in found}   # the files that another file of the chain includes
    wrappers = {one: copies[one].with_suffix(".include.yml") for one in wrapped}
    writes = {copies[path]: content, **{wrappers[one]: _alone(one, copies[one]) for one in wrappers}}
    for file, found in inner.items():
        text = (ROOT / file).read_text(encoding="utf-8")
        writes[copies[file]] = compose.retargeted(file, text, {one: str(wrappers[one]) for one in found})
    on_list = copies[path].with_suffix(".on.yml")
    writes[on_list] = "include:\n" + "".join(_in_place(entry, copies[entry]) if entry in chain
                                             else f"  - {json.dumps(str(ROOT / entry))}\n" for entry in entries)
    return writes, ["-f", CORE_FILE, "-f", str(on_list)]


def draft_hashes(project, path, content):
    """What Compose would make each service of what is on from, were this text saved in place of its file: (hashes,
    exact). The text goes in a hidden copy next to its file, and next to each file that includes it on the way from
    the on file, a copy that includes the copy: Compose reads them in place of the files. A file that nothing on
    includes, and a guide, change nothing it reads: (None, True), what is saved stands. (None, False) when it cannot
    be known: an on file with the user's own changes, a text that does not load, a file on the way from the on file that
    cannot be read. Nothing is written next to a file of a catalog: when one is among the files that read the text from
    the on file, each copy is made in the scratch folder and read from the folder of its file (_scratch_reads), as
    validate() reads a file, and the hashes are those of the files in place. ReadOnly for a file of a git catalog."""
    _file(path)
    _text(content)
    _refuse(path)
    if _is_guide(path):
        return None, True
    has_on_file = (ROOT / ON_FILE).is_file()
    if path == CORE_FILE:
        writes, files = {ROOT / _copy(path): content}, ["-f", _copy(path)] + (["-f", ON_FILE] if has_on_file else [])
    elif path == ON_FILE:
        writes, files = {ROOT / _copy(path): content}, ["-f", CORE_FILE, "-f", _copy(path)]
    else:
        if layers.current(project)["locked"]:
            return None, False
        try:
            entries = compose.includes(ON_FILE)
            chain = _chain(path, entries)
            if not chain:
                return None, True
            if any(_in_catalog(file) for file in chain):
                writes, files = _scratch_reads(path, content, entries, chain)
            else:
                texts = {file: compose.retargeted(file, (ROOT / file).read_text(encoding="utf-8"),
                                                  {inner: _copy(inner) for inner in compose.includes(file)
                                                   if inner in chain})
                         for file in chain - {path}}
                texts[path] = content
                writes = {ROOT / _copy(file): text for file, text in texts.items()}
                files = compose.all_files(entries, replace={entry: _copy(entry) for entry in entries if entry in chain})
        except (OSError, ValueError):   # a file that includes it cannot be read (not UTF-8, gone): nothing is known
            return None, False
    with _checking:   # one at a time: the copies of a check or of another draft have the same names
        try:
            for file, text in writes.items():
                write_atomic(file, text, mode=0o600)
            hashes = compose.config_hashes(files)
        finally:
            for file in writes:
                file.unlink(missing_ok=True)
    return (hashes, True) if hashes is not None else (None, False)


def _theirs(project, others):
    """Whether values of add-ons' .env files given to Compose (`others`: {.env: {variable: value}}), which it reads
    for every file, reach only the file of their add-on: the core's .env sets none of them, and no other compose file
    refers to one."""
    taken = set(envfiles.read(ENV_FILE))
    for env_file, values in others.items():
        own = next((unit["file"] for unit in [*project["addons"].values(), *project.get("playgrounds", {}).values()]
                    if unit["env_file"] == env_file), None)
        files = [path for path in [CORE_FILE, *paths.files()] if path != own and (ROOT / path).is_file()]
        if set(values) & (taken | {variable["key"] for path in files for variable in compose.variables(path)}):
            return False
    return True


def drafted(project, core=None, others=None):
    """What Compose would make each service of what is on from, with these changes of the core's .env (`core`, as
    envfiles.write() takes them: a private copy is read in its place) and these values of add-ons' .env files
    (`others`: {.env: {variable: value}}, given to Compose, which reads them before what those files hold): (hashes,
    exact). Compose takes such a value as it is, for every file: one that refers to another variable, or that would
    reach another file than its add-on's (_theirs), cannot be read so, nor can files that do not load: (None,
    False)."""
    others = others or {}
    values = {key: value for found in others.values() for key, value in found.items()}
    if any("$" in value for value in values.values()) or not _theirs(project, others):
        return None, False
    hashes = compose.config_hashes(env=values or None,
                                   dotenv=envfiles.changed(ENV_FILE, core) if core is not None else None)
    return (hashes, True) if hashes is not None else (None, False)


def env_hashes(project, env_file, values):
    """What Compose would make each service of what is on from, were these values saved in this .env, as
    save_settings() writes them: (hashes, exact), as drafted() says it."""
    variables = _variables(project, env_file)
    defaults = {v["key"]: v["default"] or "" for v in variables}
    _checked(values, defaults)
    if env_file == ENV_FILE:
        return drafted(project, core=_changes(values, defaults))
    return drafted(project, others={env_file: values})


def affected(project, path):
    """The services a change of this file may have Compose make again, when what it would make them from cannot be
    known: everything that is on for a file of the project as a whole, else the services of the unit it belongs to
    and the core services that unit changes."""
    on = layers.current(project)
    if path in TOP:
        return layers.up_services(project, on["playground"], on["effective"])
    kind, uid = _unit_of(path)
    unit = _unit(project, kind, uid)
    return [*layers.unit_services(project, unit, True), *unit.get("restarts", [])] if unit else []


# ---------------------------------------------------------------------------------------------------------------------
# Secrets and settings
# ---------------------------------------------------------------------------------------------------------------------

def _variables(project, env_file):
    """The variables a .env sets: the core's, or the settings an add-on's or a playground's compose file asks for."""
    if env_file == ENV_FILE:
        return project["core_variables"]
    for unit in [*project["addons"].values(), *project.get("playgrounds", {}).values()]:
        if unit["env_file"] == env_file:
            return unit["settings"]
    raise ValueError("Not an env file of this project")


def settings(project, env_file):
    """A .env as a form: {sections, version}. A section, Secrets or Settings, holds groups of variables that share a
    description: [{description, vars: [{key, value, default, required}]}]."""
    variables = _variables(project, env_file)
    values, sections = envfiles.read(env_file), []
    for title, kind in SECTIONS:
        groups = []
        for v in variables:
            if v["secret"] != (kind == "secret"):
                continue
            var = {"key": v["key"], "value": values.get(v["key"], ""), "default": v["default"],
                   "required": v["required"]}
            if groups and groups[-1]["description"] == v["comment"]:
                groups[-1]["vars"].append(var)
            else:
                groups.append({"description": v["comment"], "vars": [var]})
        if groups:
            sections.append({"title": title, "kind": kind, "groups": groups})
    return {"sections": sections, "version": _version(env_file)}


def _checked(values, defaults):
    """Refuses form values that are not the form's, or not one line each."""
    if not isinstance(values, dict) or not set(values) <= set(defaults):
        raise ValueError("Only secrets and settings can be changed here")
    for key, value in values.items():
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError(f"{key}: one line only")


def _changes(values, defaults):
    """Form values as a .env holds them: quoted, and no line for the default."""
    return {key: None if value == defaults[key] else envfiles.quote(value) for key, value in values.items()}


def save_settings(project, env_file, values, version):
    """Writes form values into a .env, keeping its comments and order. A value that is the default needs no line."""
    variables = _variables(project, env_file)
    if version != _version(env_file):
        raise RuntimeError(f"{env_file} changed on disk since you opened it: reload it first")
    defaults = {v["key"]: v["default"] or "" for v in variables}
    _checked(values, defaults)
    envfiles.write(env_file, _changes(values, defaults))
    return settings(project, env_file)
