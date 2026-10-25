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
owns, and checked; the page shows the preview of an Add, and Import is its Add. Its folder is named as its catalog, whose settings
start with that id. Or the person keeps it as their own: Personal (`to_personal`), where nothing is blocked any more. The preview
has judged it first, as for the Add, and Personal takes only what it judged, as it judged it (the digest of the files): what it
blocks cannot go there, nor what waits for a catalog this Lab lacks, which nothing could judge (`_waiting`), and what takes more
of this machine goes there once the person allowed it. An item of Personal with the same name is replaced, unless it is the same
(then it is used), and the preview says so (`personal`). A folder of `.lab/imports` that no catalog uses and no preview waits for
is what an import left before it was confirmed: the next import of the same playground replaces it. A removed import whose person
kept the settings leaves them in a folder the Lab owns (`KEPT_DIR`), with what the file held (`RECORD`): the same file imported
again takes them, any other file of that name leaves them where they are, and the preview says which (`kept`).

    plan(pid)                  what the export of a playground carries, needs, and what another Lab will make of it
    archive(pid, private)      plan(pid) and the file: {"file", "data" (base64)}, made as the person saw it
    receive(data)              the bytes of an archive, written and checked: the preview of the Add that imports it
    to_personal(ticket, allow) the import of a preview kept in Personal
"""

import base64
import binascii
import hashlib
import io
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path

from .. import paths
from ..docker import compose, images
from ..library import moves, project
from . import contribute, defaults, kept, manifest, moving, registry, resolve, service, standard
from . import store as bare
from .registry import CatalogError

IMPORTS_DIR = paths.IMPORTS_DIR   # where an imported playground's catalog is kept: the Lab's own folder
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
        # What is private content says so in the file (private_from), which the other Lab keeps: it can only add a notice
        files.update(contribute._unmarked(copy, kind, _slug(uid), contribute.private_of(copy.text, uid), name).files)
    origins, private = _origins(carried, units, copies_of(carried))
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


def copies_of(nodes):
    """{(kind, id): (catalog id, its name)} of the items that are a copy of an item of a private catalog (x-gravitee.copy_of),
    wherever they are now: in Personal, or in a catalog a share put them in. What they hold is that catalog's content."""
    found = {}
    for kind, uid in nodes:
        data = contribute._read(paths.ROOT / paths.unit_file(kind, uid), bare.TEXT_FILE + 1)
        source = contribute.private_source(data.decode("utf-8", "replace")) if data else None
        # A label (no original) always says private content; an older copy's chain does, but for an item of that very catalog
        if source and (source[2] is None or source[0] != paths.origin(uid)):
            found[(kind, uid)] = source[:2]
    return found


def _origins(carried, units, copies=None):
    """([{kind, item, name, catalog, catalog_name, version}] of the carried items that come from a catalog of Git, which another
    Lab may have too), and [{name, items, item_ids}] of the private ones among those catalogs: what leaves with the file. The person's
    copy of an item of a private catalog (`copies`: {(kind, id): (that catalog's id, its name)}) is that catalog's content too."""
    rows = project.loaded()[1].get("catalogs") or {}
    origins, private = [], {}
    for kind, uid in carried:
        if (kind, uid) in (copies or {}):
            ns, title = copies[(kind, uid)]
            one = private.setdefault(title, {"name": title, "items": [], "item_ids": []})
            one["items"].append(units[kind][uid]["name"])
            one["item_ids"].append(uid)
            continue
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
            one = private.setdefault(title, {"name": title, "items": [], "item_ids": []})
            one["items"].append(name)
            one["item_ids"].append(uid)
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


def archive(pid, private=()):
    """The plan and the archive, base64: what the page saves as a file. The plan is made again with the file, and holds it to what
    the person saw: what another Lab would block is no file, and what comes from a private catalog leaves only when the page
    showed that catalog (`private`: the ids it showed)."""
    shown, data = _made(pid)
    if shown["blocked"]:
        one = shown["blocked"][0]
        reason = (one.get("reasons") or ["it cannot run"])[0]
        raise _fail("export_blocked", f"{one['name']} would be blocked on another Lab: {reason[:1].lower()}{reason[1:]}. Fix: export it again "
                                      "to see what blocks it.", 409)
    unseen_private(shown, private)
    return {**shown, "data": base64.b64encode(data).decode("ascii")}


def unseen_private(shown, private):
    """Refuses a file that carries an item of private content the page did not show (`private`: the ids of the items it showed)."""
    unseen = [one["name"] for one in shown.get("private") or () if set(one.get("item_ids") or ()) - set(private or ())]
    if unseen:
        raise _fail("export_private", f"It now carries private content of {', '.join(unseen)}. Fix: export it again to see what "
                                      "leaves with it.", 409)


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


MACHINE_FILES, KEPT_DIR = kept.MACHINE_FILES, kept.KEPT_DIR


def _kept_view(top, content):
    """What the preview says of the settings kept from a removed import of the same name: {used, where}, or None. Only the same
    file imported again uses them: any file can be named as the one that was removed."""
    listed, used = kept.found(top), kept.used_by(top, content)
    if not listed:
        return None
    return {"used": used is not None, "where": f"{KEPT_DIR}/{(used or listed[0][0]).name}"}


def added(ns, data):
    """An import kept as a catalog (service.add): what its file held is recorded, for a Remove that keeps its settings, and the
    settings kept from a removed import of the same file go back to its items, where they have none."""
    if not data.get("imported") or not data.get("content"):
        return
    found = kept.record()
    found["imported"][ns] = data["content"]
    kept.write_record(found)
    folder, where = kept.used_by(ns, data["content"]), Path((registry.entry(ns) or {}).get("path") or "")
    if folder is None or not where.is_dir() or _digest(where) != data.get("digest"):
        return   # another file of that name, or files that changed after the preview: the kept settings stay where they are
    for rel, name in kept.settings_of(folder):
        if (where / rel).is_dir() and not os.path.lexists(where / rel / name):
            kept.carry(folder / rel / name, where / rel / name)
    if not kept.settings_of(folder):
        kept.forget(ns, folder)


def _sweep(replacing=None):
    """The folders of .lab/imports that no catalog uses and no preview waits for, and the half-written ones: what an import left
    before it was confirmed. The folder of `replacing`, the playground being imported again, goes with the previews of it: a
    preview is of the files it judged, which the new ones replace. A half-written folder younger than a preview may be another
    import at work. What of the person's a folder still holds (a Remove the Lab did not finish) goes to the kept settings first,
    and what cannot be moved stays."""
    base = paths.ROOT / IMPORTS_DIR
    used = {os.path.realpath(one.get("path") or "") for one in registry.entries() if one.get("kind") == "folder"}
    waiting = service.imports()
    for child in (base.iterdir() if base.is_dir() else ()):
        real = os.path.realpath(child)
        if real in used:
            continue
        if child.name == replacing:
            for ticket in waiting.get(real, ()):
                service.forget(ticket)
        elif real in waiting or child.name in _reading or \
                (child.name.startswith(".incoming-") and time.time() - child.stat().st_mtime < service.TTL):
            continue
        # A folder no catalog uses may still hold the person's settings (the Lab stopped in a Remove): they go to the kept ones
        failed = _keep_settings(child, child.name, _content_of(child)) if not child.name.startswith(".") else []
        kept.remove_but(child, [child / rel / name for rel, name in failed])


def _snapshot(folder: Path, people=True):
    """{path: (mode, bytes)} of the files of an import's folder, read once: what is judged, digested and staged is the same.
    Without `people`, the person's settings, secrets and state of its items are not read."""
    found = {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        parts = rel.split("/")
        if not people and len(parts) > 2 and parts[0] in ("addons", "playgrounds") and parts[2] in MACHINE_FILES:
            continue
        found[rel] = (0o755 if os.stat(path).st_mode & 0o111 else 0o644, path.read_bytes())
    return found


def _digest(folder, people=True):
    """What the files of an import's folder (or a `_snapshot` of it) are, as one digest: its paths, whether each runs, and their
    bytes. What a preview judged is what goes to Personal. Without `people`, the person's settings, secrets and state of its
    items are left out."""
    found = hashlib.sha256()
    for rel, (mode, data) in sorted((folder if isinstance(folder, dict) else _snapshot(folder, people)).items()):
        parts = rel.split("/")
        if not people and len(parts) > 2 and parts[0] in ("addons", "playgrounds") and parts[2] in MACHINE_FILES:
            continue
        found.update(f"{rel}\0{mode & 0o111}\0".encode("utf-8"))
        found.update(hashlib.sha256(data).digest())
    return found.hexdigest()


def _content_of(folder: Path):
    """The digest of the file an import came with: the one its Add recorded, else (an import added before the record was kept) the
    digest of its folder as it is, without the person's settings, which the same file imported again gives too."""
    try:
        return kept.record()["imported"].get(folder.name) or _digest(folder, people=False)
    except OSError:
        return None   # a file it cannot read: what is kept is bound to no file, and shown


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


# The name in the x-gravitee block of a file someone else made. The block is the first top-level `x-gravitee:` (a key is said once),
# so the search starts there once: a flow block is read up to its closing brace, and at most HEAD_LIMIT characters of it; a
# block of lines is read a line at a time, each in one way (blank, or indentation then a word). No text makes the search take
# longer than reading it.
HEAD = re.compile(r"(?m)^x-gravitee:[ \t]*")
HEAD_LIMIT = 65536
FLOW_NAME = re.compile(r"\bname:[ \t]*([^,}\n]+)")
BLOCK_NAME = re.compile(r"(?:#[^\n]*)?\n(?:[ \t]*\n|[ \t]+(?!name:)[^ \t\n][^\n]*\n)*?[ \t]+name:[ \t]*([^\n]+)")


def _named(text, fallback):
    """The name an item's x-gravitee block gives it, for the words of the page; its folder's when it says none."""
    head = HEAD.search(text)
    if head is None:
        return fallback
    at = head.end()
    if text.startswith("{", at):
        end = text.find("}", at, at + HEAD_LIMIT)
        said = FLOW_NAME.search(text, at, end if end >= 0 else min(len(text), at + HEAD_LIMIT))
    else:
        said = BLOCK_NAME.match(text, at)
    return said.group(1).strip().strip("'\"") if said else fallback


def _for_personal(folder: Path, top, snapshot=None):
    """The archive's items as Personal holds them: [(kind, slug, rel, name, {path: (mode, bytes)})], each compose file written
    again for its place in .lab/catalogs/local, and the includes of one another with it; from `snapshot` when given."""
    files_of = snapshot if snapshot is not None else _snapshot(folder)
    units = _units(folder)
    table = [(f"{paths.CATALOGS_DIR}/{top}/{rel}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{rel}") for _, _, rel in units]
    found = []
    for kind, slug, rel in units:
        compose_rel = _compose_rel(kind, rel)
        if compose_rel not in files_of:
            continue
        text = files_of[compose_rel][1].decode("utf-8")
        here = posixpath.dirname(compose_rel)
        written = moves.repoint(text, f"{paths.CATALOGS_DIR}/{top}/{here}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{here}", table)
        files = {compose_rel: (0o644, written.encode("utf-8"))}
        if kind != "setup":
            files.update({path: entry for path, entry in files_of.items() if path.startswith(rel + "/") and path != compose_rel})
        found.append((kind, slug, rel, _named(written, slug), files))
    return found


def _personal_now(kind, rel):
    """The compose text of the item of Personal at `rel`, None when there is none."""
    try:
        return (paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / _compose_rel(kind, rel)).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _same_in_personal(kind, slug, rel, name, files):
    now = _personal_now(kind, rel)
    return now is not None and now.encode("utf-8") == files[_compose_rel(kind, rel)][1]


def _personal_plan(items):
    """What going to Personal does to the items of Personal (`_for_personal`): {replaces: [names], same: [names]}."""
    replaces, same = [], []
    for kind, _, rel, name, files in items:
        now = _personal_now(kind, rel)
        if now is not None:
            (same if now.encode("utf-8") == files[_compose_rel(kind, rel)][1] else replaces).append(name)
    return {"replaces": replaces, "same": same}


def to_personal(ticket, allow=False):
    """The import the preview `ticket` showed, kept in Personal instead of as a catalog: refused when the preview found an item that
    cannot run (Personal would run it unjudged), when an item uses this machine and the person did not allow it (`allow`), when the
    files are no longer those the preview judged, or when an item it replaces is on. The items are written whole beside Personal
    with no lock, then swapped in under the lock of the catalogs (moving.replace). Answers {items: [{kind, id}], playground, kept,
    warning}."""
    data = service._held(ticket, "add")
    folder = Path(getattr(data.get("address"), "path", "") or "")
    base = (paths.ROOT / IMPORTS_DIR).resolve()
    if not folder.is_dir() or folder.resolve().parent != base or not data.get("imported"):
        raise _fail("not_an_import", "This is not a playground being imported. Choose its file again.", 409)
    unread = data.get("unread") or []
    if unread:
        raise _fail("unread_personal", f"{', '.join(unread)} cannot be read: Compose does not load its compose file. Fix: import it "
                                       "to Imported, where the Lab says why.", 409)
    blocked = data.get("blocked") or []
    if blocked:
        raise _fail("blocked_personal", f"{', '.join(blocked)} cannot run: import to Imported, where it stays blocked.", 409)
    uses = data.get("uses") or []
    if uses and allow is not True:
        raise _fail("allow_machine", f"{', '.join(uses)} uses this machine: allow it to import it to Personal.")
    top, snapshot = folder.name, _snapshot(folder)   # what is staged is what is digested: the preview's, or a refusal
    items = _for_personal(folder, top, snapshot)
    going = [one for one in items if not _same_in_personal(*one)]   # the same item is used as Personal has it

    def write(root):
        for _, _, _, _, files in going:
            for path, (mode, content) in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(content)
                os.chmod(root / path, mode)
    swap = moving.stage(write)
    try:
        with service._changing():
            recovered = moving.recover()
            with moving.telling(recovered):
                if data.get("digest") != _digest(snapshot):
                    raise _fail("import_changed", "The files of this import changed after its preview. Fix: choose its file again.", 409)
                found = project.load()
                held = next(filter(None, (moving.replaced_on(found, kind, slug) for kind, slug, rel, _, _ in going
                                          if _personal_now(kind, rel) is not None)), "")
                if held:
                    raise _fail("held", held, 409)
            rels = [rel for _, _, rel, _, _ in going]
            said = moving.swap_in(swap, rels, moving.placed_without_claims(rels))
            service.forget(ticket)
            shutil.rmtree(folder, ignore_errors=True)
            stayed = _kept_to_personal(top, data.get("content"), items)
            project.forget()
    finally:
        moving.discard(swap)   # what was not swapped in: nothing when the change was made
    warning = " ".join(one for one in (recovered, said) if one)
    playground = next((f"{paths.LOCAL}/{slug}" for kind, slug, _, _, _ in items if kind == "playground"), None)
    return {"items": [{"kind": kind, "id": f"{paths.LOCAL}/{slug}"} for kind, slug, _, _, _ in items], "playground": playground,
            **({"kept": stayed} if stayed else {}), **({"warning": warning} if warning else {})}


def _kept_to_personal(top, content, items):
    """The settings kept from a removed import of the same file, given to its items in Personal where they have none. What
    Personal has already stays as it is, and so do the kept ones, which are never deleted unused: answers {items: [names], where}
    of those that stayed, or None."""
    folder = kept.used_by(top, content)
    if folder is None:
        return None
    home, names, stayed = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, {rel: name for _, _, rel, name, _ in items}, []
    for rel, name in kept.settings_of(folder):
        target = home / rel / name
        if rel not in names or not (home / rel).is_dir() or os.path.lexists(target) or not kept.carry(folder / rel / name, target):
            stayed.append(names.get(rel, rel.rsplit("/", 1)[-1]))
    if not stayed:
        kept.forget(top, folder)
        return None
    return {"items": list(dict.fromkeys(stayed)), "where": f"{KEPT_DIR}/{folder.name}"}


def _waiting(preview):
    """The preview of an import, where an item that waits for a catalog this Lab lacks is one that cannot run: nothing judged it,
    and Personal, which judges nothing either, cannot take it."""
    for one in preview.pop("waits", None) or ():
        why = moving.waiting(one)
        if why:
            preview["blocked"] = [*(preview.get("blocked") or ()), {"kind": one["kind"], "id": one["id"], "name": one["name"],
                                                                    "reasons": [why[0].replace("it needs", "Needs", 1)]}]
    return preview


_reading = set()   # the imports placed and being judged, outside the lock: no other import replaces or sweeps them meanwhile


def receive(data):
    """The import of an archive (its bytes, or base64 text): read, written in a folder of its own and checked as the Add of a folder
    catalog. Answers that preview, whose ticket the Add takes; `already_imported` when this Lab has the catalog. Only placing the
    folder at `.lab/imports/<id>` takes the lock of the catalogs: the file is read and judged without it, so that another change is
    never kept waiting by an import, and the files judged are checked to be those placed."""
    archive = _opened(data)
    with archive:
        top, members = _members(archive)
        _refused_id(archive, top)
        base = paths.ROOT / IMPORTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        incoming = base / f".incoming-{secrets.token_hex(4)}"
        os.mkdir(incoming, 0o755)
        try:
            _extract(archive, members, incoming)
            content = _digest(incoming)   # what the file holds, as it came: what settings kept from an import of it are bound to
            reused = _reuse(incoming, top)
            final = base / top
            with service._changing():
                recovered = moving.recover()
                with moving.telling(recovered):
                    _refused_id(archive, top)
                    if top in _reading:
                        raise registry.busy()
                    _sweep(top)
                    if os.path.lexists(final):
                        raise _leftover(archive, top)   # what a removal could not delete, the sweep could not either
                    os.replace(incoming, final)
                    _reading.add(top)
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
    try:
        with moving.telling(recovered):
            preview = _judged_import(final, top, content, reused)
        return {**preview, **({"warning": recovered} if recovered else {})}
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    finally:
        _reading.discard(top)


def _opened(data):
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
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise _fail("import_invalid", NOT_A_PLAYGROUND) from None


def _refused_id(archive, top):
    if registry.entry(top) is not None and not registry.imported(top):   # another catalog, which a Remove would lose
        other = (registry.inputs().get(top) or {}).get("name") or registry.lock_of(top).get("name") or top
        raise _fail("import_taken", f"{_name_of(archive, top)} has the id of a catalog of this Lab, {other}. Fix: ask for it "
                                    "exported again from a playground of another name.", 409)
    if registry.entry(top) is not None:
        raise _fail("already_imported", f"{_name_of(archive, top)} is already in this Lab. To import it again, remove it on "
                                        "the Catalogs page first.", 409)


def _extract(archive, members, folder):
    written = 0
    for rel, mode, info in members:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target, "xb") as out:
            while True:   # what the archive says of a size is not trusted: what is read is counted
                chunk = source.read(1 << 16)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise _fail("import_too_large", "This playground holds more than a catalog may: 10,000 files, 100 MB.", 413)
                out.write(chunk)
        os.chmod(target, mode)


TWIN_CLAIMED = "Says it is the copy of an item of this Lab (x-gravitee.copy_of), which only this Lab writes"


def claims_twin(text):
    """Why a compose text someone else wrote cannot go to Personal for what its x-gravitee says, as Compose reads it: that it is
    the copy of an item of this Lab (a twin only this Lab makes), or that Compose cannot read it alone; "" when neither."""
    try:
        said = compose.alone(text).get("x-gravitee")
    except compose.ComposeError as error:
        return f"Its x-gravitee cannot be read: {str(error)[:120]}"
    return TWIN_CLAIMED if isinstance(said, dict) and said.get("copy_of") else ""


def _judged_import(final, top, content, reused):
    """The preview of the import placed at `final`, judged as the Add of a folder catalog, with what Personal would take of it."""
    placed = _digest(final)
    preview = _waiting(service.check_address(str(final)))
    if preview["catalog"]["id"] != top:   # its settings start with the id: the folder and the catalog are one name
        service.forget(preview.get("ticket"))
        raise _fail("import_invalid", f"{NOT_A_PLAYGROUND} Its folder, {top}, is not the id of its catalog, {preview['catalog']['id']}.")
    items = _for_personal(final, top)
    # What the judgement did not produce, since Compose could not read it, is judged by nothing: it cannot go to Personal
    judged = set(service._held(preview["ticket"], "add").get("judged") or ())
    unread = [name for _, slug, _, name, _ in items if f"@{top}/{slug}" not in judged]
    preview["blocked"] = [*(preview.get("blocked") or ()), *({"kind": kind, "id": f"@{top}/{slug}", "name": name,
                                                              "reasons": ["Its compose file cannot be read"]}
                                                             for kind, slug, _, name, _ in items if name in unread)]
    # A file never says whose copy an item is: only this Lab writes copy_of, in Personal. Asked of Compose, the reader the Lab
    # takes it from later, so that no way of writing YAML hides it; an item that says it cannot go to Personal
    for kind, slug, rel, name, _ in items:
        if name not in unread:
            claim = claims_twin((final / _compose_rel(kind, rel)).read_text(encoding="utf-8", errors="replace"))
            if claim:
                preview["blocked"].append({"kind": kind, "id": f"@{top}/{slug}", "name": name, "reasons": [claim]})
    blocked = [one.get("name") for one in preview.get("blocked") or ()]
    # What Personal would run unjudged that takes more of this machine: the person allows it, as for a Move
    named = {f"@{top}/{slug}": name for _, slug, _, name, _ in items}
    uses = list(dict.fromkeys(named.get(one.get("id"), one.get("name")) for one in (preview.get("rows") or {}).get("uses_machine") or ()))
    if _digest(final) != placed:   # what was judged is what is there
        service.forget(preview.get("ticket"))
        raise _fail("import_changed", "The files of this import changed while it was read. Fix: choose its file again.", 409)
    shown = _kept_view(top, content)
    service.note(preview.get("ticket"), imported=True, blocked=blocked, unread=unread, uses=uses, digest=placed, content=content)
    return {**preview, "imported": True, "reused": reused, "personal": _personal_plan(items), **({"kept": shown} if shown else {})}


def _leftover(archive, top):
    """The refusal of an import whose folder holds what an earlier one left and the Lab cannot delete: the person's settings
    among it are named first, to copy out, so that what is said to remove never takes them."""
    held = [f"{rel}/{name}" for rel, name in kept.settings_of(paths.ROOT / IMPORTS_DIR / top)]
    if held:
        return _fail("import_leftover", f"What an earlier import of {_name_of(archive, top)} left cannot be deleted, and holds its "
                                        f"settings {', '.join(held)}. Fix: copy them out of {IMPORTS_DIR}/{top}, then remove that "
                                        "folder with administrator rights, and import it again.", 409)
    return _fail("import_leftover", f"What an earlier import of {_name_of(archive, top)} left cannot be deleted. Fix: remove the "
                                    f"folder {IMPORTS_DIR}/{top} with administrator rights, then import it again.", 409)


PARTIAL = "Some files were created by Docker and could not be deleted. Remove the folder {where} with administrator rights."


def forget_folder(entry, keep_settings=True):
    """The files of an imported playground's catalog go when the catalog is removed: they are the Lab's, in .lab/imports. With
    `keep_settings` the person's settings, secrets and state of its items go first to a folder of their own (`KEPT_DIR`), bound
    to the file the import came from: only that file imported again takes them. A setting that cannot be moved stays where it
    is, and is said with where it is; what else cannot be deleted stays apart from the kept settings, and is said. Answers that
    warning, or None. Called under the lock of the change, which no import gets in between."""
    where = os.path.realpath((entry or {}).get("path") or "")
    base = os.path.realpath(paths.ROOT / IMPORTS_DIR)
    if not (where.startswith(base + os.sep) and posixpath.dirname(where) == base):
        return None
    folder = Path(where)
    ns = folder.name
    failed = _keep_settings(folder, ns, _content_of(folder)) if keep_settings and folder.is_dir() else []
    found = kept.record()
    found["imported"].pop(ns, None)
    kept.write_record(found)
    kept.remove_but(folder, [folder / rel / name for rel, name in failed])
    if failed:
        return NOT_KEPT.format(paths=", ".join(f"{area}/{item}/{name}" for area, item, name in
                                               ((*rel.split("/", 1), name) for rel, name in failed)), where=f"{IMPORTS_DIR}/{ns}")
    return PARTIAL.format(where=f"{IMPORTS_DIR}/{folder.name}") if os.path.lexists(where) else None


NOT_KEPT = ("The settings of {paths} could not be kept and are still in {where}: copy them out, then remove that folder with "
            "administrator rights.")


def _keep_settings(folder: Path, ns, content):
    """The person's settings, secrets and state of an import's items moved to a folder of kept settings of their own. Answers
    [(rel, name)] of those that could not be moved, which stay whole where they are."""
    settings = kept.settings_of(folder)
    _, failed = kept.keep(ns, content, [(folder / rel / name, f"{rel}/{name}") for rel, name in settings])
    return [(rel, name) for rel, name in settings if folder / rel / name in failed]
