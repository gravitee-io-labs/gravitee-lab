"""A playground in a file: its export, and its import on another Lab.

An export is a folder catalog in a zip archive, `<id>/` at its root: the catalog's own files (the template of a folder catalog),
the playground, and every setup and add-on it includes that the other Lab may not have, which is everything but the items of the
default catalog, which every Lab has. An include of a carried item is written again for its new place (`contribute.collect`); an
include of the default catalog stays as it is, and `catalog.yml` says the catalog needs it (`requires.catalogs`). What the Lab never
shares is never carried: settings (`.env`), keys, what a seed recorded. A license is not in it either: the person who imports the
playground adds their own.

Before the file is made, the export is judged as the import will be: the archive's folder is checked as an Add of a folder catalog
(`service.check_address`), so what the other Lab will block, or ask to use this machine, is said here, by the same rules.

An item that comes from a catalog of Git is carried too, whatever that catalog is: the person who receives the file may have no
access to it. What comes from a private one is said before the file is made (`private`), since it leaves with the file. The archive
says where each of those items comes from (`export.json`, at its root, which a catalog never installs): a Lab that has that catalog
uses its own item instead of a second copy of the same one (`_reuse`), and the import says so. An image the other Lab will pull
from a registry that asks for a login is said too (`registries`): the person who receives the file needs that access.

An import is that Add: the archive is read with the limits of a catalog (`store`), written under `.lab/imports/<id>`, which the Lab
owns, and checked; the page shows the preview of an Add, and Import is its Add. Or the person keeps it as their own: Personal
(`to_personal`), where nothing is blocked any more. The preview has judged it first, as for the Add: what it blocks cannot go to
Personal, and what it asks of this machine was shown before the person chose. An item of Personal with the same name is replaced,
unless it is the same (then it is used), and the preview says so (`personal`). A folder of `.lab/imports` that no catalog uses is
what an import left before it was confirmed: the next import of the same playground replaces it.

    plan(pid)                  what the export of a playground carries, needs, and what another Lab will make of it
    archive(pid)               plan(pid) and the file: {"file", "data" (base64)}
    receive(data)              the bytes of an archive, written and checked: the preview of the Add that imports it
"""

import base64
import binascii
import io
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from .. import paths
from ..docker import images
from ..library import moves, personal as personals, project
from . import contribute, defaults, manifest, registry, resolve, service, standard
from . import store as bare
from .registry import CatalogError

IMPORTS_DIR = f"{paths.LAB_DIR}/imports"   # where an imported playground's catalog is kept: the Lab's own folder
VERSION = "1.0.0"
NS = re.compile(r"[a-z][a-z0-9-]{0,22}[a-z0-9]")   # what a catalog id is (registry.check_namespace has the rest)
MAX_ARCHIVE = 64 << 20   # the largest file an import reads: a catalog keeps at most 100 MB, compressed it is far less
MAX_FILES = 10000
MAX_BYTES = 100 << 20
TEMPLATE_EXAMPLE = "addons/hello"   # the example the template of a new catalog brings: an export carries its own items only

NOT_A_PLAYGROUND = "This file is not a playground exported by Gravitee Lab."
ORIGINS = "export.json"   # where each item of a catalog of Git that the archive carries comes from


def _fail(code, message, status=400):
    return CatalogError(code, message, status)


# ---------------------------------------------------------------------------------------------------------------------
# What an export carries
# ---------------------------------------------------------------------------------------------------------------------

def _slug(uid):
    return uid.rsplit("/", 1)[-1]


def _folder(kind, ns, slug):
    """Where an item of catalog `ns` is, relative to the project: its folder, or the file of a setup."""
    return f"{paths.CATALOGS_DIR}/{ns}/setups/{slug}.yml" if kind == "setup" else f"{paths.CATALOGS_DIR}/{ns}/{kind}s/{slug}"


def _as_id(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:24].strip("-")


def _free_id(name, pid):
    """The id of the archive's catalog, one that no catalog of this Lab has (the judgement of the export is made here, as an Add):
    the playground's own name first, since its settings start with it (STREAMS_ for streams) and a catalog's own settings start
    with its id; else its title."""
    taken = {one["id"] for one in registry.entries()} | {paths.LOCAL, paths.default_ns() or ""} | set(paths.RESERVED_NS)
    tries = [one for one in (_as_id(_slug(pid)), _as_id(name)) if one and NS.fullmatch(one)] or ["playground"]
    for one in tries:
        if one not in taken:
            return one
    base, number = tries[0], 2
    while True:
        suffix = f"-{number}"
        found, number = base[:24 - len(suffix)].strip("-") + suffix, number + 1
        if found not in taken and NS.fullmatch(found):
            return found


def _gather(pid):
    """What the export of playground `pid` carries: {project, unit, ns, name, carried [(kind, id)], needs [ns], files, problems}.
    Raises what keeps it from being exported at all."""
    found = project.load()
    unit = found["playgrounds"].get(pid)
    if unit is None:
        raise paths.NotFound(f"No playground {pid}")
    default = paths.default_ns()
    if default is not None and paths.origin(pid) == default:
        raise _fail("export_default", f"{unit['name']} comes with every Lab: share its name, there is nothing to export.", 409)
    units = {"addon": found["addons"], "setup": found["setups"], "playground": found["playgrounds"]}
    nodes = [node for node in resolve.closure(units, "playground", pid) if node[1] in (units.get(node[0]) or {})]
    carried = [node for node in nodes if paths.origin(node[1]) != default]
    needs = sorted({paths.origin(uid) for _, uid in nodes if paths.origin(uid) == default})
    slugs = {}
    for kind, uid in carried:
        other = slugs.setdefault((kind, _slug(uid)), uid)
        if other != uid:
            raise _fail("export_clash", f"It includes two items named {_slug(uid)}, {other} and {uid}. Fix: rename one of them.",
                        409)
    ns = _free_id(unit["name"], pid)
    moved = [(str(Path(paths.unit_file(kind, uid)).parent) if kind != "setup" else paths.unit_file(kind, uid),
              _folder(kind, ns, _slug(uid))) for kind, uid in carried]
    files, problems = {}, []
    for kind, uid in carried:
        copy = contribute.collect(kind, _slug(uid), ns, paths.origin(uid), moved)
        leak = contribute._leaked(copy, kind, _slug(uid))
        if leak:
            raise leak
        name = units[kind][uid]["name"]
        problems += [f"{name}: {line}" for line in copy.problems]
        files.update(copy.files)
    origins, private = _origins(carried, units)
    registries = _registries(found, carried, units)
    shared = _shared(carried, units, ns)
    if len(shared) > manifest.SETTINGS_LIMIT:
        problems.append(f"Its items read {len(shared)} settings; a catalog declares at most {manifest.SETTINGS_LIMIT}.")
    return {"project": found, "unit": unit, "ns": ns, "name": unit["name"], "carried": carried, "needs": needs, "files": files,
            "problems": problems, "units": units, "shared": shared[:manifest.SETTINGS_LIMIT], "origins": origins, "private": private,
            "registries": registries}


def _registries(found, carried, units):
    """[{registry, images}] of the images the other Lab will pull from a registry that asks for a login: those of the carried
    items' services that are not built there, and those of the platform its items ask a version of, from the registry their
    latest tested set names (what a start there switches to), else the platform's default one."""
    owners = {(kind, uid) for kind, uid in carried}
    services = found.get("services") or {}
    pulled = [one["image"] for one in services.values() if not one.get("built")
              and ((one.get("owner") or {}).get("kind"), (one.get("owner") or {}).get("id")) in owners]
    components = None
    for kind, uid in carried:
        unit = units[kind][uid]
        tested = next((one for one in reversed(unit.get("tested") or []) if one), {})
        wanted = {**((unit.get("requires") or {}).get("images") or {}), **tested}
        if not wanted:
            continue
        components = components if components is not None else images.components(found)
        for component in components:
            version, key = wanted.get(component["version_key"]), component["registry_key"]
            registry = (tested.get(key) or component["registry_default"]) if key else None
            if version is None or not key or not registry or "$" in str(registry):
                continue
            pulled.append(f"{registry}/{component['repository']}:{version}")
    return [{"registry": registry, "images": sorted(set(found_images))}
            for registry, found_images in sorted(images.private_images(pulled).items())]


def _origins(carried, units):
    """([{kind, item, name, catalog, catalog_name, version}] of the carried items that come from a catalog of Git, which another
    Lab may have too), and [{id, name, items}] of the private ones among those catalogs: what leaves with the file."""
    rows = project.loaded()[1].get("catalogs") or {}
    origins, private = [], {}
    for kind, uid in carried:
        ns = paths.origin(uid)
        entry = registry.entry(ns) if ns != paths.LOCAL else None
        if not entry or entry.get("kind") != "git":
            continue
        held = registry.lock_of(ns)
        title = (rows.get(ns) or {}).get("name") or held.get("name") or ns
        name = units[kind][uid]["name"]
        origins.append({"kind": kind, "item": _slug(uid), "name": name, "catalog": ns, "catalog_name": title,
                        "version": held.get("version")})
        if held.get("audience") != "public":
            private.setdefault(ns, {"id": ns, "name": title, "items": []})["items"].append(name)
    return origins, list(private.values())


def _shared(carried, units, ns):
    """The settings the carried items read that are neither the platform's nor their catalog's by name: what the archive's
    catalog.yml declares in `shared_settings`, the settings its own units share. Without it another Lab would block them (T20,
    a setting is a catalog's own when it starts with its id), and with it the person who imports them sees what they read."""
    inputs = project.loaded()[1]
    core = {one["key"] for one in (inputs.get("variables") or {}).get(paths.CORE_FILE, [])}
    mine = f"{ns.upper().replace('-', '_')}_"
    names = set()
    for kind, uid in carried:
        file = units[kind][uid]["file"]
        names |= {one["key"] for one in units[kind][uid].get("settings") or () if isinstance(one.get("key"), str)}
        names |= set((inputs.get("references") or {}).get(file) or ())
        names |= set((inputs.get("seed_references") or {}).get(file) or ())
    return sorted(name for name in names if name not in core and not name.startswith(mine)
                  and not name.endswith(("_VERSION", "_REGISTRY", "_PULL_POLICY")))


def _catalog_yml(text, gathered):
    """The template's catalog.yml with the playground's description, the version of an export, and the catalogs it needs."""
    description = (gathered["unit"].get("description") or f"{gathered['name']}, exported from Gravitee Lab").replace('"', "'")
    text = re.sub(r'(?m)^(  description: ).*$', lambda m: f'{m.group(1)}"{description}"', text)
    text = re.sub(r"(?m)^(  version: ).*$", lambda m: m.group(1) + VERSION, text)
    rows = project.loaded()[1].get("catalogs") or {}
    needed = []
    for ns in gathered["needs"]:
        row, url = rows.get(ns) or {}, _address(ns)
        if url is None:   # a catalog with no Git address cannot be named as needed: another Lab will say what it lacks
            continue
        version = row.get("version") if re.fullmatch(r"\d+\.\d+\.\d+", str(row.get("version") or "")) else "0.1.0"
        needed += [f"      {ns}:", f'        name: "{row.get("name") or ns}"', f"        url: {url}", f"        min: {version}"]
    if needed:
        text = text.rstrip("\n") + "\n  requires:\n    catalogs:\n" + "\n".join(needed) + "\n"
    if gathered["shared"]:
        text = text.rstrip("\n") + "\n  shared_settings:\n" + "".join(f"    - {name}\n" for name in gathered["shared"])
    return text


def _address(ns):
    """The Git address another Lab adds catalog `ns` from: the one the engine lists (catalogs.default.json), else the one this Lab
    has it from; None when neither is one."""
    listed = next((one.get("url") for one in defaults.read() if one.get("id") == ns), None)
    for url in (listed, (registry.entry(ns) or {}).get("url")):
        if manifest._address_ok(url):
            return url
    return None


def _readme(gathered):
    name = gathered["name"]
    return (f"# {name}\n\n"
            f"{gathered['unit'].get('description') or ''}\n\n"
            "A playground of Gravitee Lab, in a file.\n\n"
            "## Import it\n\n"
            "1. Get Gravitee Lab: `git clone https://github.com/gravitee-io-labs/gravitee-lab.git && cd gravitee-lab && make ui`\n"
            "2. Add your Gravitee license in the Lab UI.\n"
            f"3. On the **Playgrounds** page, choose **Import**, then this file. {name} is then a playground of your Lab.\n").replace(
        "\n\n\n", "\n\n")


def _short(name, most=40):
    """A name a catalog may have (at most 40 characters), cut at a word."""
    name = str(name).strip()
    return name if len(name) <= most else (name[:most].rsplit(" ", 1)[0] or name[:most]).rstrip(" ,:-")


def _write(root: Path, gathered):
    """The archive's folder: the template of a folder catalog without its example, then the carried items."""
    standard.create(root, {"id": gathered["ns"], "name": _short(gathered["name"]), "local": True})
    shutil.rmtree(root / TEMPLATE_EXAMPLE, ignore_errors=True)
    manifest = root / "catalog.yml"
    manifest.write_text(_catalog_yml(manifest.read_text(encoding="utf-8"), gathered), encoding="utf-8")
    (root / "README.md").write_text(_readme(gathered), encoding="utf-8")
    if gathered["origins"]:
        (root / ORIGINS).write_text(json.dumps({"items": gathered["origins"]}, indent=2) + "\n", encoding="utf-8")
    for path, (mode, data) in gathered["files"].items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o755 if mode == "100755" else 0o644)


def _zip(root: Path, top: str) -> bytes:
    """The folder `root` as a zip archive, its files under `top/`, in a stable order, with their modes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{top}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if os.stat(path).st_mode & 0o111 else 0o644)) << 16
            archive.writestr(info, path.read_bytes())
    return out.getvalue()


def _row(kind, uid, units):
    return {"kind": kind, "id": uid, "name": units[kind][uid]["name"]}


def _judge(root: Path):
    """What another Lab will make of the archive: the preview of the Add of its folder, made here and let go at once."""
    preview = service.check_address(str(root))
    service.forget(preview.get("ticket"))
    return preview


def _plan(gathered, preview):
    rows = project.loaded()[1].get("catalogs") or {}
    units = gathered["units"]
    # The items it carries are on this machine too, where they were exported from: a name they share with themselves is no clash
    # on another Lab
    # on another Lab, nor is what follows from it: an item blocked only for that is no reason to block what includes it
    carried = set(gathered["carried"])
    others = {unit["name"] for kind, found in units.items() for uid, unit in found.items() if (kind, uid) not in carried}
    dropped = {f"Same name as {units[kind][uid]['name']}" for kind, uid in carried if units[kind][uid]["name"] not in others}
    blocked = [dict(one) for one in preview.get("blocked") or ()]
    while True:
        for one in blocked:
            one["reasons"] = [reason for reason in one.get("reasons") or () if reason not in dropped]
        freed = {f"Needs {one['name']}, which is blocked" for one in blocked if not one["reasons"]} - dropped
        if not freed:
            break
        dropped |= freed
    return {"name": gathered["name"], "file": f"{gathered['ns']}.zip",
            "carries": [_row(kind, uid, units) for kind, uid in gathered["carried"]],
            "needs": [{"id": ns, "name": (rows.get(ns) or {}).get("name") or ns} for ns in gathered["needs"]],
            "problems": gathered["problems"],
            "private": gathered["private"],
            "registries": gathered["registries"],
            "blocked": [one for one in blocked if one["reasons"]],
            # What another Lab asks the person to allow: what each item's own services take, as this Lab judged them where they are
            "uses_machine": [{"name": units[kind][uid]["name"], "what": what} for kind, uid in gathered["carried"]
                             for what in [_uses(units[kind][uid])] if what]}


def _uses(unit):
    """What of this machine the services of a unit itself take ("runs privileged"), sorted; not those of what it includes."""
    own = set(unit.get("services") or ())
    return sorted({what for use in unit.get("uses_machine") or () if use.get("service") in own for what in use.get("what") or ()})


def _made(pid):
    """(the plan, the bytes of the archive) of playground `pid`, made in a folder of its own that is removed after."""
    gathered = _gather(pid)
    # Under the Lab's folder, not the system's: Docker shares the home folder, and the judgement asks Compose about the files
    (paths.ROOT / paths.LAB_DIR).mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".export-", dir=paths.ROOT / paths.LAB_DIR))
    try:
        root = scratch / gathered["ns"]
        _write(root, gathered)
        try:
            preview = _judge(root)
        except CatalogError as error:   # the archive's own catalog is refused as a whole: said as the import would say it
            preview = {"blocked": [{"name": gathered["name"], "reasons": [error.message]}]}
        return _plan(gathered, preview), _zip(root, gathered["ns"])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def plan(pid):
    """What the export of playground `pid` carries (`carries`), what it needs of the other Lab (`needs`, the default catalog),
    what it leaves out (`problems`), what the other Lab will block (`blocked`) or ask to use that machine (`uses_machine`), and
    the file's name. The archive is made, judged and dropped."""
    return _made(pid)[0]


def archive(pid):
    """The plan and the archive, base64: what the page saves as a file."""
    shown, data = _made(pid)
    return {**shown, "data": base64.b64encode(data).decode("ascii")}


# ---------------------------------------------------------------------------------------------------------------------
# An import
# ---------------------------------------------------------------------------------------------------------------------

def _members(archive):
    """[(path under the top folder, mode, ZipInfo)] of the archive's files, and its top folder: what a catalog may hold, or the
    refusal. One folder at the root, no link, no path that leaves it, the limits of a catalog."""
    top, found, total = None, [], 0
    for info in archive.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        kind = (info.external_attr >> 16) & 0o170000
        parts = name.split("/")
        if "\\" in name or name.startswith("/") or any(part in ("", ".", "..") for part in parts) or len(parts) < 2 \
                or (kind and kind != stat.S_IFREG):
            raise _fail("import_invalid", NOT_A_PLAYGROUND)
        if top is None:
            top = parts[0]
        elif parts[0] != top:
            raise _fail("import_invalid", NOT_A_PLAYGROUND)
        total += info.file_size
        found.append(("/".join(parts[1:]), 0o755 if (info.external_attr >> 16) & 0o111 else 0o644, info))
        if len(found) > MAX_FILES or total > MAX_BYTES:
            raise _fail("import_too_large", "This playground holds more than a catalog may: 10,000 files, 100 MB.", 413)
    if top is None or not NS.fullmatch(top) or not any(rel == "catalog.yml" for rel, _, _ in found):
        raise _fail("import_invalid", NOT_A_PLAYGROUND)
    entries = [bare.Entry("100755" if mode == 0o755 else "100644", "0" * 40, info.file_size, rel) for rel, mode, info in found]
    refused = list(bare.rejections(entries))
    if refused:
        entry, code = refused[0]
        raise _fail("import_invalid", f"{NOT_A_PLAYGROUND} It holds {entry.path}, which a catalog cannot ({code}).")
    return top, found


def _name_of(archive, top):
    """The name catalog.yml gives, for the words of a refusal; the id when it says none."""
    try:
        text = archive.read(f"{top}/catalog.yml")[:65536].decode("utf-8", "replace")
    except KeyError:
        return top
    said = re.search(r'(?m)^  name:\s*"?([^"\n]+?)"?\s*$', text)
    return said.group(1) if said else top


def _sweep(keep=()):
    """The folders of .lab/imports that no catalog uses, and the half-written ones: what an import left before it was confirmed."""
    base = paths.ROOT / IMPORTS_DIR
    used = {os.path.realpath(one.get("path") or "") for one in registry.entries() if one.get("kind") == "folder"}
    for child in (base.iterdir() if base.is_dir() else ()):
        if child.name not in keep and os.path.realpath(child) not in used:
            shutil.rmtree(child, ignore_errors=True)


def _said_origins(folder: Path):
    """The items of a catalog of Git the archive says it carries, as export.json says them; [] when it says nothing readable."""
    try:
        said = json.loads((folder / ORIGINS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = said.get("items") if isinstance(said, dict) else None
    return [one for one in items if isinstance(one, dict)] if isinstance(items, list) else []


def _rel(kind, item):
    return f"setups/{item}.yml" if kind == "setup" else f"{kind}s/{item}"


def _requires(text, needed):
    """catalog.yml with the catalogs `needed` ([(ns, name, url, min)]) in its requires.catalogs, as an export writes them."""
    rows = "".join(f'      {ns}:\n        name: "{name}"\n        url: {url}\n        min: {least}\n' for ns, name, url, least in needed)
    if not rows:
        return text
    head = "\n  requires:\n    catalogs:\n"
    if head in text:
        return text.replace(head, head + rows, 1)
    return text.rstrip("\n") + head + rows


def _reuse(folder: Path, top):
    """The archive in `folder` (catalog `top`) uses this Lab's own copy of what it carries from a catalog of Git that this Lab has:
    that item leaves the archive, what includes it includes this Lab's, and catalog.yml requires that catalog. An item is the same
    item when its catalog has it under the same name. Answers [{name, catalog}] of the items reused."""
    table, gone, needed, reused = [], [], {}, []
    for one in _said_origins(folder):
        kind, item, ns = one.get("kind"), one.get("item"), one.get("catalog")
        if kind not in ("addon", "setup", "playground") or not paths.is_default(item) or not (isinstance(ns, str) and NS.fullmatch(ns)):
            continue
        entry, rel = registry.entry(ns), _rel(kind, item)
        url = _address(ns) if entry and entry.get("kind") == "git" else None
        if url is None or item not in registry.units_of(ns).get(kind, ()) or not (folder / rel).exists():
            continue
        held = registry.lock_of(ns)
        least = held.get("version") if re.fullmatch(r"\d+\.\d+\.\d+", str(held.get("version") or "")) else "0.1.0"
        needed[ns] = (ns, str(held.get("name") or ns).replace('"', "'"), url, least)
        table.append((f"{paths.CATALOGS_DIR}/{top}/{rel}", f"{paths.CATALOGS_DIR}/{ns}/{rel}"))
        gone.append(rel)
        reused.append({"name": str(one.get("name") or item), "catalog": str(held.get("name") or ns)})
    if not table:
        return []
    files = [*folder.glob("playgrounds/*/compose.yml"), *folder.glob("addons/*/compose.yml"), *folder.glob("setups/*.yml")]
    for file in files:
        rel = file.relative_to(folder).as_posix()
        if any(rel == one or rel.startswith(one + "/") for one in gone):
            continue
        place = posixpath.dirname(f"{paths.CATALOGS_DIR}/{top}/{rel}")
        text = file.read_text(encoding="utf-8")
        written = moves.repoint(text, place, place, table)
        if written != text:
            file.write_text(written, encoding="utf-8")
    for rel in gone:
        target = folder / rel
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    manifest_file = folder / "catalog.yml"
    manifest_file.write_text(_requires(manifest_file.read_text(encoding="utf-8"), list(needed.values())), encoding="utf-8")
    if not any(folder.glob("playgrounds/*/compose.yml")):
        root = next((one for one, rel in zip(reused, gone) if rel.startswith("playgrounds/")), reused[0])
        raise _fail("already_here", f"{root['name']} is already in this Lab, from {root['catalog']}.", 409)
    return reused


def _units(folder: Path):
    """[(kind, slug, rel)] of the items of an archive's folder: rel is its folder, or the file of a setup."""
    found = [("playground", one.parent.name, f"playgrounds/{one.parent.name}") for one in sorted(folder.glob("playgrounds/*/compose.yml"))]
    found += [("setup", one.stem, f"setups/{one.name}") for one in sorted(folder.glob("setups/*.yml"))]
    found += [("addon", one.parent.name, f"addons/{one.parent.name}") for one in sorted(folder.glob("addons/*/compose.yml"))]
    return [one for one in found if paths.is_default(one[1])]


def _compose_rel(kind, rel):
    return rel if kind == "setup" else f"{rel}/compose.yml"


def _named(text, fallback):
    """The name an item's x-gravitee block gives it, for the words of the page; its folder's when it says none."""
    said = re.search(r"(?m)^x-gravitee:\s*\{[^}]*?\bname:\s*([^,}\n]+)", text) \
        or re.search(r"(?m)^x-gravitee:\s*(?:#.*)?\n(?:[ \t]+.*\n)*?[ \t]+name:\s*(.+)", text)
    return said.group(1).strip().strip("'\"") if said else fallback


def _for_personal(folder: Path, top):
    """The archive's items as Personal holds them: [(kind, slug, rel, name, {path: (mode, bytes)})], each compose file written
    again for its place in .lab/catalogs/local, and the includes of one another with it."""
    units = _units(folder)
    table = [(f"{paths.CATALOGS_DIR}/{top}/{rel}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{rel}") for _, _, rel in units]
    found = []
    for kind, slug, rel in units:
        compose_rel = _compose_rel(kind, rel)
        text = (folder / compose_rel).read_text(encoding="utf-8")
        here = posixpath.dirname(compose_rel)
        written = moves.repoint(text, f"{paths.CATALOGS_DIR}/{top}/{here}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{here}", table)
        files = {compose_rel: (0o644, written.encode("utf-8"))}
        if kind != "setup":
            for path in sorted(p for p in (folder / rel).rglob("*") if p.is_file() and not p.is_symlink()):
                rel_path = path.relative_to(folder).as_posix()
                if rel_path != compose_rel:
                    files[rel_path] = (0o755 if os.stat(path).st_mode & 0o111 else 0o644, path.read_bytes())
        found.append((kind, slug, rel, _named(written, slug), files))
    return found


def _personal_now(kind, rel):
    """The compose text of the item of Personal at `rel`, None when there is none."""
    try:
        return (paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / _compose_rel(kind, rel)).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _personal_plan(folder: Path, top):
    """What going to Personal does to the items of Personal: {replaces: [names], same: [names]}."""
    replaces, same = [], []
    for kind, _, rel, name, files in _for_personal(folder, top):
        now = _personal_now(kind, rel)
        if now is not None:
            (same if now.encode("utf-8") == files[_compose_rel(kind, rel)][1] else replaces).append(name)
    return {"replaces": replaces, "same": same}


MACHINE_FILES = (".env", ".state", "secrets")   # what Personal keeps of an item it replaces: the person's, never the file's


def _clear(target: Path):
    """The folder of an item of Personal emptied of what its files were, keeping the person's settings, secrets and state."""
    for child in target.iterdir():
        if child.name in MACHINE_FILES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def to_personal(ticket):
    """The import the preview `ticket` showed, kept in Personal instead of as a catalog: refused when the preview found an item that
    cannot run (Personal would run it unjudged) or when an item it replaces is on. Answers {items: [{kind, id}], playground}."""
    data = service._held(ticket, "add")
    folder = Path(getattr(data.get("address"), "path", "") or "")
    base = (paths.ROOT / IMPORTS_DIR).resolve()
    if not folder.is_dir() or folder.resolve().parent != base or not data.get("imported"):
        raise _fail("not_an_import", "This is not a playground being imported. Choose its file again.", 409)
    blocked = data.get("blocked") or []
    if blocked:
        raise _fail("blocked_personal", f"{', '.join(blocked)} cannot run: import to Imported, where it stays blocked.", 409)
    top = folder.name
    items = _for_personal(folder, top)
    found = project.load()
    for kind, slug, rel, name, files in items:
        now = _personal_now(kind, rel)
        if now is not None and now.encode("utf-8") != files[_compose_rel(kind, rel)][1]:
            try:
                personals._not_held(found, kind, f"{paths.LOCAL}/{slug}")
            except RuntimeError as error:
                raise _fail("held", str(error), 409) from None
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    for kind, slug, rel, name, files in items:
        now = _personal_now(kind, rel)
        if now is not None and now.encode("utf-8") == files[_compose_rel(kind, rel)][1]:
            continue   # the same item: Personal's is used
        if kind != "setup" and (home / rel).is_dir():
            _clear(home / rel)
        for path, (mode, content) in files.items():
            target = home / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            os.chmod(target, mode)
    service.forget(ticket)
    shutil.rmtree(folder, ignore_errors=True)
    project.forget()
    playground = next((f"{paths.LOCAL}/{slug}" for kind, slug, _, _, _ in items if kind == "playground"), None)
    return {"items": [{"kind": kind, "id": f"{paths.LOCAL}/{slug}"} for kind, slug, _, _, _ in items], "playground": playground}


def receive(data):
    """The import of an archive (its bytes, or base64 text): written under .lab/imports/<id> and checked as the Add of a folder
    catalog. Answers that preview, whose ticket the Add takes; `already_imported` when this Lab has the catalog."""
    if isinstance(data, str):
        try:
            data = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise _fail("import_invalid", NOT_A_PLAYGROUND) from None
    if not isinstance(data, bytes) or not data:
        raise _fail("import_invalid", NOT_A_PLAYGROUND)
    if len(data) > MAX_ARCHIVE:
        raise _fail("import_too_large", "This file is larger than an exported playground can be: 64 MB.", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise _fail("import_invalid", NOT_A_PLAYGROUND) from None
    with archive:
        top, members = _members(archive)
        if registry.entry(top) is not None:
            raise _fail("already_imported", f"{_name_of(archive, top)} is already in this Lab. To import it again, remove it on "
                                            "the Catalogs page first.", 409)
        base = paths.ROOT / IMPORTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        _sweep()
        incoming = base / f".incoming-{secrets.token_hex(4)}"
        os.mkdir(incoming, 0o755)
        try:
            written = 0
            for rel, mode, info in members:
                target = incoming / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "xb") as out:
                    while True:   # what the archive says of a size is not trusted: what is read is counted
                        chunk = source.read(1 << 16)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise _fail("import_too_large", "This playground holds more than a catalog may: 10,000 files, 100 MB.",
                                        413)
                        out.write(chunk)
                os.chmod(target, mode)
            final = base / top
            os.replace(incoming, final)
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
    try:
        reused = _reuse(final, top)
        preview = service.check_address(str(final))
        blocked = [one.get("name") for one in preview.get("blocked") or ()]
        service.note(preview.get("ticket"), imported=True, blocked=blocked)
        return {**preview, "imported": True, "reused": reused, "personal": _personal_plan(final, top)}
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise


def forget_folder(entry):
    """The files of an imported playground's catalog go when the catalog is removed: they are the Lab's, in .lab/imports."""
    where = os.path.realpath((entry or {}).get("path") or "")
    base = os.path.realpath(paths.ROOT / IMPORTS_DIR)
    if where.startswith(base + os.sep) and posixpath.dirname(where) == base:
        shutil.rmtree(where, ignore_errors=True)
