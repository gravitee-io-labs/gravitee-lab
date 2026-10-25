"""Kept settings: the person's settings, secrets and state (`.env`, `secrets/`, `.state/` of an item) that no item holds any
more, in a folder of the Lab's own that no file reaches: those of an imported playground removed with Keep settings, and those
of an item of Personal that a new version replaced and that the new one had too. Each set has its own folder, `<key>-<token>`,
and its line in the record (`RECORD`), written before anything moves: the key (the id of the import, or `local`) and the digest
of the file it came with, when there is one. What is kept is never deleted unused: only the same file imported again takes it
back, and a folder with no line in the record is still found and shown, used by nothing. Every change here is made under the
lock of the catalogs.

    keep(key, digest, pairs)   moves [(source, rel)] into a new folder of kept settings: (its place, [what could not move])
    found(key)                 [(folder, digest)] of what is kept for `key`, newest first
    used_by(key, digest)       the folder kept for that very file, or None
    forget(key, folder)        the folder and its line go: what it held went back
    remove_but(folder, spared) a folder deleted but for the person's files that could be neither moved nor kept
    listed()                   what is kept, for the Catalogs page, which shows it with Imported
    remove(name)               a folder of kept settings deleted at the person's word
"""

import contextlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from .. import paths

MACHINE_FILES = (".env", ".state", "secrets")   # the person's in the folder of an item, never a file's
KEPT_DIR = f"{paths.LAB_DIR}/kept-settings"
RECORD = f"{paths.LAB_DIR}/imports.json"   # {imported: {id: digest of its file}, kept: {key: [{folder, digest}]}}
LOCAL = "local"   # the key of what a new version of an item of Personal replaced


def record():
    try:
        found = json.loads((paths.ROOT / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    found = found if isinstance(found, dict) else {}
    return {key: dict(found.get(key)) if isinstance(found.get(key), dict) else {} for key in ("imported", "kept")}


def write_record(found):
    (paths.ROOT / RECORD).parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(paths.ROOT / RECORD, json.dumps(found, indent=2, sort_keys=True) + "\n")


def _lines(found, key):
    one = found["kept"].get(key)
    return [line for line in one if isinstance(line, dict)] if isinstance(one, list) else []


def found(key):
    """[(folder, digest)] of the settings kept for `key`, newest first; a folder with no line (the Lab stopped before it wrote
    one) is there too, with no digest: nothing is invisible."""
    base, listed, seen = paths.ROOT / KEPT_DIR, [], set()
    for line in reversed(_lines(record(), key)):
        name = line.get("folder")
        if isinstance(name, str) and "/" not in name and (base / name).is_dir():
            listed.append((base / name, line.get("digest")))
            seen.add(name)
    pattern = re.compile(rf"{re.escape(key)}-[0-9a-f]{{8}}")
    listed += [(one, None) for one in sorted(base.glob(f"{key}-*") if base.is_dir() else ())
               if one.is_dir() and one.name not in seen and pattern.fullmatch(one.name)]
    return listed


def used_by(key, digest):
    """The folder of the settings kept for that very file (`digest`), or None."""
    return next((folder for folder, said in found(key) if said and said == digest), None)


def forget(key, folder: Path):
    found_ = record()
    found_["kept"][key] = [line for line in _lines(found_, key) if line.get("folder") != folder.name]
    if not found_["kept"][key]:
        del found_["kept"][key]
    write_record(found_)
    shutil.rmtree(folder, ignore_errors=True)


def settings_of(folder: Path):
    """[(item folder relative to `folder`, name)] of the person's settings, secrets and state there."""
    return [(f"{area}/{item.name}", name) for area in ("addons", "playgrounds") for item in sorted((folder / area).glob("*"))
            if item.is_dir() and not item.is_symlink() for name in MACHINE_FILES if os.path.lexists(item / name)]


def carry(source: Path, target: Path):
    """A file or folder of the person's moved to `target`, else copied (what Docker made as root may only be read), never half,
    never over what is there: answers whether it is there now. It never raises: what cannot be placed stays where it is."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)   # a file where a folder must be: it cannot go there, and is said
    except OSError:
        return False
    if os.path.lexists(target):
        return False
    with contextlib.suppress(OSError):
        os.rename(source, target)
        return True
    try:
        (shutil.copytree if source.is_dir() and not source.is_symlink() else shutil.copy2)(source, target)
    except OSError:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.lexists(target):
            with contextlib.suppress(OSError):
                target.unlink()
        return False
    if source.is_dir() and not source.is_symlink():
        shutil.rmtree(source, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            source.unlink()
    return True


def keep(key, digest, pairs):
    """`pairs` ([(source, its place in the kept folder)]) moved into a new folder of kept settings for `key`, its line written
    first. Answers (the folder, relative to the project, or None when nothing moved; [the sources that could not move])."""
    if not pairs:
        return None, []
    folder = paths.ROOT / KEPT_DIR / f"{key}-{secrets.token_hex(4)}"
    found_ = record()
    found_["kept"][key] = [*_lines(found_, key), {"folder": folder.name, "digest": digest}]
    write_record(found_)
    failed = [source for source, rel in pairs if not carry(source, folder / rel)]
    if len(failed) == len(pairs):
        forget(key, folder)
        return None, failed
    return f"{KEPT_DIR}/{folder.name}", failed


def remove_but(folder: Path, spared):
    """`folder` deleted, but for the paths in `spared`, which are the person's and could not be moved: they stay where they are."""
    if not spared:
        shutil.rmtree(folder, ignore_errors=True)
        return
    spared = {Path(one) for one in spared}
    ancestors = {parent for path in spared for parent in path.parents}

    def clear(here):
        for child in sorted(here.iterdir()):
            if child in spared:
                continue
            if child in ancestors and child.is_dir() and not child.is_symlink():
                clear(child)
            elif child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    child.unlink()
    if folder.is_dir():
        clear(folder)


def listed():
    """What is kept, for the page: [{folder, from (the import's id, or Personal), items, at}], newest first."""
    base, rows = paths.ROOT / KEPT_DIR, []
    for folder in sorted(base.iterdir() if base.is_dir() else (), key=lambda one: one.stat().st_mtime, reverse=True):
        found = re.fullmatch(r"(.+)-[0-9a-f]{8}", folder.name)
        if not folder.is_dir() or folder.is_symlink() or not found:
            continue
        items = sorted({rel.rsplit("/", 1)[-1] for rel, _ in settings_of(folder)})
        rows.append({"folder": folder.name, "from": "Personal" if found.group(1) == LOCAL else found.group(1), "items": items,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(folder.stat().st_mtime))})
    return rows


def remove(name):
    """The kept settings in the folder `name` deleted, at the person's word, with their line in the record."""
    found = re.fullmatch(r"(.+)-[0-9a-f]{8}", name) if isinstance(name, str) and "/" not in name else None
    folder = paths.ROOT / KEPT_DIR / name if found else None
    if folder is None or not folder.is_dir() or folder.is_symlink():
        raise paths.NotFound("No such kept settings")
    forget(found.group(1), folder)
    return {"warning": "Some files were created by Docker and could not be deleted. Remove the folder "
                       f"{KEPT_DIR}/{name} with administrator rights."} if os.path.lexists(folder) else {}
