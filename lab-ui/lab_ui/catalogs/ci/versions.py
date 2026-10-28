"""`catalog.py ci versions-plan`, `versions-record` and `versions-release`: the steps of the module `versions.yml` (level 7 of the
standard), which keeps what each playground says it was tested with (`x-gravitee.tested`) up with Gravitee's releases. The rules
of what is tried and what is kept are the Lab's (`lab_ui/compat.py`). Each step reads the catalog through the Lab it is linked into
(`catalog.py link PATH`), as the end-to-end run does.

    versions-plan PATH [--github-output]
        The matrix of the night: one job for each playground of the catalog and each set of versions it is to be tried on, which
        writes two outputs, `matrix` (`{"include": [{item, versions, name}]}`) and `count`.
    versions-record PATH --plan FILE --results DIR
        Writes in the catalog at PATH the sets that passed: each file of DIR is `{item, versions, code}`, which a run wrote, and only
        a job of the plan (FILE, the matrix the plan wrote) whose code is 0 counts. Prints a line for each playground it wrote,
        with the sets it kept. A file it does not write the way a person writes it is left out and said (with `$GITHUB_OUTPUT`,
        `left_out`, how many): the others are recorded. Each file is read again by the Lab once written: a `tested` that is not
        the sets it meant, or any other change, puts every file back and fails (exit 1), so that nothing is pushed.
    versions-release PATH
        Raises the patch number of `version` in catalog.yml past the newest tag, and prints the new version. Prints nothing, and
        changes nothing, for a catalog that has no release (it is followed commit by commit) or whose catalog.yml already says a
        version newer than its newest tag (a release its maintainers prepared: what was recorded ships with it).

The job that records runs nothing of the catalog: the results are data, checked against the plan, and what it writes is the one
block of each file that the record makes. Exit 0 it worked, 1 a file could not be written as meant, 2 it cannot be done (a usage
error, a catalog that cannot be read here).

Python 3.9 and later, standard library only."""

import datetime
import json
import os
import re
from pathlib import Path

from ... import compat, e2e
from .. import release as releases
from . import Parser, Unavailable, Usage, entry, json_line, parsed, write_outputs


def _catalog(path):
    """(the folder, the id) of the catalog at `path`, which has to be linked into this Lab."""
    from ..cli import _folder_catalog
    folder, meta = _folder_catalog(path)
    return Path(folder).resolve(), meta["id"]


def _project():
    from ...library import project
    try:
        return project.load()
    except (e2e.compose.ComposeError, OSError) as error:
        raise Unavailable(f"The Lab cannot read its project: {' '.join(str(error).split())[:200]}") from None


@entry
def plan_main(argv, out, err, **seams) -> int:
    """`ci versions-plan`."""
    found = Parser(prog="catalog.py ci versions-plan", description="The versions the playgrounds are tried on tonight.")
    found.add_argument("path", nargs="?", default=".", help="the catalog folder, linked into this Lab (default: the current folder)")
    found.add_argument("--github-output", action="store_true", help="write matrix and count to $GITHUB_OUTPUT")
    args = parsed(found, argv)
    _, ns = _catalog(args.path)
    night = seams.get("night", datetime.date.today().toordinal())
    jobs = compat.plan(seams.get("project") or _project(), ns, seams.get("tags"), warn=lambda said: err.write(f"{said}\n"), night=night)
    out.write("".join(f"{job['name']}\n" for job in jobs) or "Every playground is tested on the newest versions\n")
    if args.github_output:
        write_outputs({"matrix": json_line({"include": jobs}), "count": str(len(jobs))})
    return 0


def passed(planned, results, err):
    """{item: [set]} of the results that are a job of the plan and ended well; any other file is said on `err` and left out."""
    asked = {(job["item"], job["versions"]) for job in planned}
    found = {}
    for path in results:
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8"))
            key = (str(result["item"]), json.dumps(json.loads(result["versions"]), sort_keys=True))
            code = int(result["code"])
        except (OSError, ValueError, KeyError, TypeError):
            err.write(f"Not read: {Path(path).name}\n")
            continue
        if key not in asked:
            err.write(f"Not a job of the plan: {key[0][:120]}\n")
        elif code == 0:
            found.setdefault(key[0], []).append(json.loads(key[1]))
    return found


def _follow(built):
    """The version variables of Gravitee's images (compat.plan's `follow`); None for a project that lists no services."""
    if "services" not in built:
        return None
    from ...docker import images
    return {component["id"] for component in images.components(built)
            if component.get("registry_key") and "graviteeio" in str(component.get("registry_default") or "")}


def _plain(sets):
    return [{str(key): str(value) for key, value in found.items()} for found in sets or [] if found]


@entry
def record_main(argv, out, err, **seams) -> int:
    """`ci versions-record`."""
    found = Parser(prog="catalog.py ci versions-record", description="Writes the versions that passed in x-gravitee.tested.")
    found.add_argument("path", help="the catalog folder, linked into this Lab")
    found.add_argument("--plan", required=True, help="the matrix the plan wrote")
    found.add_argument("--results", required=True, help="the folder of the files the runs wrote")
    args = parsed(found, argv)
    folder, ns = _catalog(args.path)
    try:
        planned = json.loads(Path(args.plan).read_text(encoding="utf-8"))["include"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Usage("--plan is the matrix the plan wrote: {\"include\": [...]}") from None
    built = seams.get("project") or _project()
    follow = _follow(built)
    results = sorted(Path(args.results).rglob("*.json")) if Path(args.results).is_dir() else []
    written, said, meant, left_out = {}, [], {}, 0
    try:
        for item, sets in sorted(passed(planned, results, err).items()):
            kind, uid = e2e.parse(item, built)
            unit = built["playgrounds"].get(uid)
            if kind != "playground" or unit is None or unit.get("catalog") != ns:
                continue
            local = uid.split("/", 1)[1] if uid.startswith("@") else uid
            path = (folder / "playgrounds" / local / "compose.yml").resolve()
            if folder not in path.parents:
                continue
            tested = compat.merged(unit.get("tested") or [], sets, compat.variables(unit, follow) or None)
            if _plain(tested) == _plain(unit.get("tested")):
                continue
            text = path.read_bytes().decode("utf-8")   # as it is: read_text would make CRLF line ends LF unseen
            try:
                changed = compat.write(text, tested)
            except ValueError as error:
                err.write(f"{unit['name']}: {path.relative_to(folder)} is left out: {error}. Write it as the other playgrounds do\n")
                left_out += 1
                continue
            written[path] = text
            path.write_bytes(changed.encode("utf-8"))
            meant[uid] = (unit, tested)
            kept = [one for one in sets if one in tested]
            keys = compat.variables(unit, follow) or sorted(key for one in kept for key in one if compat.VERSION_KEY.fullmatch(key))
            said.append(f"{unit['name']}: tested on " + "; ".join(", ".join(f"{key} {one[key]}" for key in dict.fromkeys(keys) if key in one)
                                                                         for one in kept))
        if written:
            _check(meant, seams.get("reload") or _reload)
    except RuntimeError as error:
        for path, text in written.items():
            path.write_bytes(text.encode("utf-8"))
        err.write(f"{error}\n")
        return 1
    out.write("".join(f"{line}\n" for line in said))
    if left_out and os.environ.get("GITHUB_OUTPUT"):
        write_outputs({"left_out": str(left_out)})
    return 0


def _reload():
    from ...docker import compose
    from ...library import project
    compose.CACHE.drop()
    project.forget()
    return _project()


def _check(meant, reload):
    """Each file written says, read again by the Lab, the sets it was meant to, and nothing else of its playground changed.
    RuntimeError otherwise."""
    built = reload()
    for uid, (before, tested) in meant.items():
        after = built["playgrounds"].get(uid)
        if after is None or _plain(after.get("tested")) != _plain(tested):
            raise RuntimeError(f"{before['name']}: its file does not say the sets that were meant once written; nothing is written")
        if {key: value for key, value in after.items() if key != "tested"} != {key: value for key, value in before.items()
                                                                                 if key != "tested"}:
            raise RuntimeError(f"{before['name']}: writing its versions changed more than them; nothing is written")


@entry
def release_main(argv, out, err, **seams) -> int:
    """`ci versions-release`."""
    found = Parser(prog="catalog.py ci versions-release", description="Raises the patch number of the catalog's version.")
    found.add_argument("path", nargs="?", default=".", help="the catalog folder, a git checkout with its tags")
    args = parsed(found, argv)
    folder = Path(args.path)
    file = folder / "catalog.yml"
    if not file.is_file():
        raise Usage(f"{args.path} has no catalog.yml")
    latest = releases.latest_tag(folder)
    text = file.read_text(encoding="utf-8")
    current = _version_of(text)
    if latest is None or (current and releases.numbers(current) > releases.numbers(latest[1:])):
        return 0   # no release to make: commit by commit, or a release its maintainers prepared
    numbers = releases.numbers(latest[1:])
    version = f"{numbers[0]}.{numbers[1]}.{numbers[2] + 1}"
    text, _ = releases.edit_version(text, version)
    file.write_text(releases.with_legacy(folder, text), encoding="utf-8")
    out.write(f"{version}\n")
    return 0


def _version_of(text):
    """The `version` catalog.yml says, three numbers, or None."""
    found = re.search(r"^  version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*(?:#.*)?$", text, re.M)
    return found.group(1) if found else None
