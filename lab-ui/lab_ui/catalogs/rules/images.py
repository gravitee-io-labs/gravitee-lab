"""IM: images and names. A catalog publishes the images it builds under one prefix, `images` in `catalog.yml`, and each
name must be built by one service from one place; the engine's `lab_images.py` lets the last item with a name silently win,
and this closes that hole at pull request time.

    IM01  a service that builds an image has no `pull_policy` (so a person who sets `<ID>_PULL_POLICY` cannot choose)
    IM02  the image of a built service is outside the `images` prefix, or two units build the same name
    IM03  one image name, two build contexts or two Dockerfiles (within a unit; across units it is IM02)
    IM04  `images` is not `ghcr.io/<owner>/<repo>` of this repository (in CI, or with `--online`)

The Lab's seeder is the one named anchor: a built service whose image is `x-gravitee.seeder.image` of the Lab's core file
and whose build context is the Lab's seeder is the Lab's own image. IM02 and IM03 do not count it, it is not checked against the
prefix and it is never published, but it still needs its `pull_policy` (IM01). Without that exemption every playground with a
seed would fail its own standard. The caller says where the Lab's seeder is: `Context.seeder_images` and `seeder_paths`
(where it is from the top of a catalog), or `Context.is_seeder(folder, context)` when it knows the real folders. Only the image,
the context and the Dockerfile are compared: the other keys of a build (`args`, `target`, `additional_contexts`) are the trust
policy's (T18 and N01), which a unit with a seed service meets at Add.

The rules judge a list of `Service` rows. They come from the Compose files of the tree, read as text (`services_of_tree`: the
`services:` of every unit's `compose.yml` and of the files of the unit's folder that it includes, with the merge keys and the
aliases of the file, and, for a service that `extends` another, the build, image and pull policy it takes from it), or from the
units that the caller has already loaded with Compose (`services_of_units`: the resolved or raw JSON of each unit, which carries
no line). A file that is included or extended and cannot be read as text is said in `Context.notes`, so that the answer is never
"clean" by silence. Pure: it reads the tree and writes nothing, runs nothing, and `docker compose` is not needed."""

import posixpath
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from ..report import ERROR, Problem
from ..tree import REGULAR
from . import COMPOSE_NAMES, TEXT_LIMIT, Context, Node, Outline, basename, context, merged, outline, problem, read_limit, scalar, \
    table, text_of, unit_of
from .secrets import flow_items, flow_pairs

RULES = table([
    ("IM01", ERROR, "A service that builds an image has no pull_policy (the seeder's service too)",
     "{path}: {service} builds an image but has no pull_policy. Fix: pull_policy: ${ID_PULL_POLICY:-build}."),
    ("IM02", ERROR, "The image of a built service is outside the images prefix of catalog.yml, or two units build the same "
                    "name. The seeder is exempt",
     "{image} is built by {a} and {b}. Fix: name them {unit}-{app}."),
    ("IM03", ERROR, "One image name, two build contexts or two Dockerfiles. The seeder is exempt",
     "{image} is built from {a} and from {b}. Fix: one name, one build."),
    ("IM04", ERROR, "images is not ghcr.io/<owner>/<repo> of this repository (in CI, or with --online)",
     "catalog.yml images is {x}; this repository publishes to {y}. Fix: set images: {y}."),
])
# The second message of IM02: the image is outside the prefix of the catalog
MORE = {"IM02": ("{service}: the image {image} is outside {prefix}. Fix: name it {prefix}/{unit}-{app}.",)}

_DEFAULT = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^${}]*)\}")
_VARIABLE = re.compile(r"\$\{|\$[A-Za-z_]")
_REMOTE = re.compile(r"(?:https?://|git@|git://|ssh://|github\.com/)", re.I)
SHOWN = 80


class Service(NamedTuple):
    """A service that a unit defines, as the image rules need it. `line` is 0 when the source has none; `context` is "" and
    `built` False for a service that only runs an image."""
    path: str
    line: int
    unit: str
    folder: str
    name: str
    image: str
    built: bool
    context: str
    dockerfile: str
    pull_policy: bool


def defaults(text: str) -> str:
    """A text with each `${NAME:-default}` replaced by its default (inside out), as Compose reads it with no setting made."""
    for _ in range(8):
        replaced = _DEFAULT.sub(lambda found: found.group(1), text)
        if replaced == text:
            break
        text = replaced
    return text


def known(text: str) -> Optional[str]:
    """The text with its defaults put in, or None when a variable without a default is left in it."""
    text = defaults(text)
    return None if _VARIABLE.search(text) else text


def repository(image: str) -> Optional[str]:
    """The repository of an image reference, lower case, with no tag and no digest, or None when it cannot be known without
    the settings of the machine (a variable in the name before its tag)."""
    text = defaults(image.strip())
    name = text.split("@", 1)[0]
    head, slash, tail = name.rpartition("/")
    base = head + slash + tail.split(":", 1)[0]
    return None if _VARIABLE.search(base) else base.lower() or None


def _clip(text: str) -> str:
    return text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"


def _pull_policy(node) -> bool:
    return node is not None


MAX_FILES = 16   # files one unit's Compose file reaches through `include` and `extends`
MAX_DEPTH = 8    # and how many files deep (a chain of `extends`, a file that includes a file)


class _File(NamedTuple):
    path: str
    parsed: Outline
    services: Optional[Node]


def _value(parsed: Outline, node: Optional[Node]) -> str:
    """The text of a scalar node, with an alias put in when the file defines the anchor."""
    text = node.value if node is not None else ""
    if text.startswith("*") and text[1:] in parsed.anchors:
        text = parsed.anchors[text[1:]].value
    return scalar(text)


def _paths_of(node: Optional[Node]) -> List[str]:
    """The files an `include:` names: `- a.yml`, `- path: a.yml`, `- path: [a.yml, b.yml]`, `[a.yml, {path: b.yml}]`."""
    out: List[str] = []
    if node is None:
        return out
    if node.value[:1] == "[":
        for item in flow_items(node.value[1:-1] if node.value.endswith("]") else node.value[1:]):
            item = item.strip()
            if item[:1] == "{":
                out += [scalar(value) for name, value in flow_pairs(item) if name == "path" and value[:1] != "["]
            elif item:
                out.append(scalar(item))
        return [each for each in out if each]
    for child in node.children:
        if child.key is None and child.value:
            out.append(scalar(child.value))
        elif child.key == "path":
            if child.value[:1] == "[":
                out += [scalar(each.strip()) for each in flow_items(child.value[1:-1] if child.value.endswith("]") else child.value[1:])]
            elif child.value:
                out.append(scalar(child.value))
            else:
                out += [scalar(each.value) for each in child.children if each.key is None and each.value]
    return [each for each in out if each]


def _extends_of(node: Optional[Node]) -> Tuple[str, str]:
    """(file, service) of an `extends:`: `extends: web` is a service of the same file (file ""), the mapping names a file."""
    if node is None:
        return "", ""
    if node.value[:1] == "{":
        pairs = {name: scalar(value) for name, value in flow_pairs(node.value)}
        return pairs.get("file", ""), pairs.get("service", "")
    if node.value and not node.children:
        return "", scalar(node.value)
    return (scalar(node.get("file").value) if node.get("file") is not None else "",
            scalar(node.get("service").value) if node.get("service") is not None else "")


def _read(tree, path: str, files: Dict[str, _File], notes: Optional[list], why: str) -> Optional[_File]:
    """The Compose file at `path` read as text, once; None (and a note) when it cannot be."""
    if path in files:
        return files[path]
    entry = tree.entry(path)
    data = tree.read(path, read_limit(path)) if entry is not None and entry.mode in REGULAR and entry.size <= read_limit(path) else None
    if data is None:
        if notes is not None:
            notes.append(f"the services of {path} ({why}): the file could not be read as text")
        return None
    parsed = outline(text_of(data))
    files[path] = _File(path, parsed, next((node for node in parsed.root.children if node.key == "services"), None))
    return files[path]


def _inside(folder: str, target: str, base: str) -> Optional[str]:
    """The path of `target` (written in a file of `base`) when it is below `folder`, else None: the unit's own files only."""
    where = known(target)
    if where is None or "://" in where or posixpath.isabs(where):
        return None
    joined = posixpath.normpath(posixpath.join(base, where))
    return joined if joined.startswith(folder + "/") else None


def _facts(files: Dict[str, _File], tree, notes, folder: str, path: str, service: Node, depth: int, seen: frozenset) -> dict:
    """What the image rules need of a service: its image, whether it builds (and from which folder, context and Dockerfile)
    and whether it has a pull policy. A service that `extends` another takes what it has not got from it, as Compose does,
    and a build it takes brings the folder of the file it was written in."""
    parsed = files[path].parsed
    keys = merged(service, parsed.anchors)
    build = keys.get("build")
    context_ = dockerfile = ""
    if build is not None:
        nested = merged(build, parsed.anchors) if build.children else {}
        context_ = _value(parsed, nested.get("context")) if nested else _value(parsed, build)
        dockerfile = _value(parsed, nested.get("dockerfile")) if nested else ""
    facts = {"image": _value(parsed, keys.get("image")), "built": build is not None, "context": context_,
             "dockerfile": dockerfile, "pull": keys.get("pull_policy") is not None, "folder": posixpath.dirname(path)}
    target, name = _extends_of(keys.get("extends"))
    if name and depth < MAX_DEPTH and not (facts["built"] and facts["image"] and facts["pull"]):
        where = path if not target else _inside(folder, target, posixpath.dirname(path))
        if target and where is None and notes is not None:
            notes.append(f"the build of {service.key} in {path}: it extends {target}, which is not a file of the unit that can be read")
        base_file = files.get(where) or (_read(tree, where, files, notes, f"extended by {path}") if where else None)
        base = next((child for child in base_file.services.children if child.key == name), None) \
            if base_file is not None and base_file.services is not None else None
        if base is not None and (where, name) not in seen:
            inherited = _facts(files, tree, notes, folder, where, base, depth + 1, seen | {(where, name)})
            if not facts["built"] and inherited["built"]:
                facts.update(built=True, context=inherited["context"], dockerfile=inherited["dockerfile"],
                             folder=inherited["folder"])
            facts["image"] = facts["image"] or inherited["image"]
            facts["pull"] = facts["pull"] or inherited["pull"]
    return facts


def services_of_tree(tree, notes: Optional[list] = None) -> List[Service]:
    """The services of every unit's Compose file in a tree, read as text, and of the files of the unit's folder that it
    includes. A service takes the keys of the mappings its merge keys name (`<<: *name`, where the file defines the anchor) and
    what its `extends` brings; an alias as the value of `image`, `build` or `context` is put in when the file defines the
    anchor. What cannot be read is a sentence in `notes`."""
    rows: List[Service] = []
    for entry in tree.entries():
        unit = unit_of(entry.path)
        if unit is None or unit[0] == "setup" or entry.mode not in REGULAR or entry.size > read_limit(entry.path) \
                or basename(entry.path) not in COMPOSE_NAMES or posixpath.dirname(entry.path).count("/") != 1:
            continue
        folder = posixpath.dirname(entry.path)
        files: Dict[str, _File] = {}
        if _read(tree, entry.path, files, notes, "the unit") is None:
            continue
        active, queue, level = [entry.path], [entry.path], 0
        while queue and level < MAX_DEPTH and len(files) < MAX_FILES:
            level, following = level + 1, []
            for path in queue:
                for target in _paths_of(files[path].parsed.root.get("include")):
                    if known(target) is None and notes is not None:
                        notes.append(f"the services of {target}, which {path} includes: its name depends on a setting")
                    where = _inside(folder, target, posixpath.dirname(path))
                    if where is None or where in active:
                        continue
                    if len(files) >= MAX_FILES:
                        break
                    if _read(tree, where, files, notes, f"included by {path}") is not None:
                        active.append(where)
                        following.append(where)
            queue = following
        for path in active:
            services = files[path].services
            for service in (services.children if services is not None else ()):
                if service.key is None:
                    continue
                facts = _facts(files, tree, notes, folder, path, service, 0, frozenset({(path, service.key)}))
                rows.append(Service(path, service.line, f"{unit[0]}:{unit[1]}", facts["folder"], service.key, facts["image"],
                                    facts["built"], facts["context"], facts["dockerfile"], facts["pull"]))
    return rows


def services_of_units(units: Iterable[dict]) -> List[Service]:
    """The services of units that the caller has loaded: a list of `{"kind", "id", "file", "config"}`, where `config` is the
    Compose model of the unit as JSON, resolved or raw, with its `services`. The rows carry no line."""
    rows: List[Service] = []
    for unit in units:
        config = unit.get("config") if isinstance(unit, dict) else None
        services = config.get("services") if isinstance(config, dict) else None
        if not isinstance(services, dict):
            continue
        path = str(unit.get("file") or "")
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            build = service.get("build")
            built = build is not None
            context_ = dockerfile = ""
            if isinstance(build, str):
                context_ = build
            elif isinstance(build, dict):
                context_ = str(build.get("context") or "")
                dockerfile = str(build.get("dockerfile") or "")
            rows.append(Service(path, 0, f"{unit.get('kind')}:{unit.get('id')}", posixpath.dirname(path), str(name),
                                str(service.get("image") or ""), built, context_, dockerfile, service.get("pull_policy") is not None))
    return rows


def _seeds(row: Service, ctx: Context) -> bool:
    """Whether a built service is the Lab's seeder: its image is the seeder's and its build context is the Lab's seeder."""
    repo = repository(row.image) if row.image else None
    if not repo or repo not in {each.lower().split(":", 1)[0] for each in ctx.seeder_images}:
        return False
    if ctx.is_seeder is not None:
        return bool(ctx.is_seeder(row.folder, row.context))
    if not row.context or _REMOTE.match(row.context) or row.dockerfile not in ("", "Dockerfile"):
        return False
    where = posixpath.normpath(posixpath.join(row.folder, known(row.context) or row.context))
    return where in ctx.seeder_paths


def _place(row: Service) -> Tuple[str, str]:
    """(where it is built from, the Dockerfile) as comparable text: a local context from the top of the catalog."""
    context_ = known(row.context) or row.context
    if _REMOTE.match(context_) or posixpath.isabs(context_):
        return context_, row.dockerfile or "Dockerfile"
    return posixpath.normpath(posixpath.join(row.folder, context_)), row.dockerfile or "Dockerfile"


def _app(row: Service) -> Tuple[str, str]:
    """(unit, app) as the name `{unit}-{app}` is made: the unit's id, and the service without the unit's name before it."""
    unit = row.unit.partition(":")[2]
    prefix = unit + "-"
    return unit, row.name[len(prefix):] if row.name.startswith(prefix) else row.name


def evaluate(rows: List[Service], ctx: Context) -> List[Problem]:
    """IM01 to IM03 over a list of services."""
    found: List[Problem] = []
    prefix = ctx.meta("images").strip().rstrip("/")
    built: Dict[str, List[Service]] = {}
    for row in rows:
        if not row.built:
            continue
        if not row.pull_policy:
            found.append(problem(RULES, "IM01", row.path, row.line, service=row.name))
        if not row.image or _seeds(row, ctx):
            continue
        repo = repository(row.image)
        if prefix and (repo is None or not repo.startswith(prefix.lower() + "/")):
            unit, app = _app(row)
            found.append(problem(RULES, "IM02", row.path, row.line, template=MORE["IM02"][0], service=row.name,
                                 image=_clip(row.image), prefix=prefix, unit=unit, app=app))
        if repo is not None:
            built.setdefault(repo, []).append(row)
    for repo, group in built.items():
        first = group[0]
        reported = set()
        for row in group[1:]:
            if row.unit != first.unit:
                if (first.unit, row.unit) not in reported:
                    reported.add((first.unit, row.unit))
                    found.append(problem(RULES, "IM02", row.path, row.line, image=_clip(repo), a=first.unit, b=row.unit))
            elif _place(row) != _place(first):
                found.append(problem(RULES, "IM03", row.path, row.line, image=_clip(repo), a=_clip(" ".join(
                    part for part in _place(first) if part != "Dockerfile")), b=_clip(" ".join(
                        part for part in _place(row) if part != "Dockerfile"))))
    return found


def _images_line(tree) -> int:
    data = tree.read("catalog.yml", TEXT_LIMIT)
    if data is None:
        return 0
    for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
        if line.strip().startswith("images:"):
            return number
    return 0


def publishes_to(repository_name: str) -> str:
    """`ghcr.io/<owner>/<repo>` for `OWNER/REPO`, in lower case: where the catalog's CI publishes its images."""
    return "ghcr.io/" + repository_name.strip().lower()


def check_publish(tree, ctx: Context) -> List[Problem]:
    """IM04: `images` is the registry path of this repository. Asked in CI or with `--online`, and only when the repository is
    known (`ctx.repository`)."""
    images = ctx.meta("images").strip()
    if not (ctx.ci or ctx.online) or not images or not ctx.repository:
        return []
    wanted = publishes_to(ctx.repository)
    if images.rstrip("/").lower() == wanted:
        return []
    return [problem(RULES, "IM04", "catalog.yml", _images_line(tree), x=_clip(images), y=wanted)]


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """IM01 to IM04: the services of the units the caller has loaded (`ctx.units`), else the Compose files of the tree."""
    ctx = ctx if ctx is not None else context(tree)
    rows = services_of_units(ctx.units) if ctx.units is not None else services_of_tree(tree, ctx.notes)
    return evaluate(rows, ctx) + check_publish(tree, ctx)


__all__ = ["MORE", "RULES", "Service", "check", "check_publish", "evaluate", "known", "publishes_to", "repository",
           "services_of_tree", "services_of_units"]
