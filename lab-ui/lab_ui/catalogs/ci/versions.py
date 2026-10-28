"""`catalog.py ci versions-plan`, `versions-record` and `versions-release`: the steps of the module `versions.yml` (level 7 of the
standard), which keeps what each playground says it was tested with (`x-gravitee.tested`) up with Gravitee's releases. The rules
of what is tried and what is kept are the Lab's (`lab_ui/compat.py`). Each step reads the catalog through the Lab it is linked into
(`catalog.py link PATH`), as the end-to-end run does.

    versions-plan PATH [--github-output]
        The matrix of the night: one job for each playground of the catalog and each set of versions it is to be tried on, which
        writes two outputs, `matrix` (`{"include": [{item, versions, name}]}`) and `count`.
    versions-record PATH --plan FILE --results DIR
        Writes in the catalog at PATH the sets that passed: each file of DIR is `{item, versions, code}`, which a run wrote, and only
        a job of the plan (FILE, the matrix the plan wrote) whose code is 0 counts. Prints a line for each playground it wrote.
    versions-release PATH
        Raises the patch number of `version` in catalog.yml past the newest tag, and prints the new version.

The job that records holds the key that writes main. It runs nothing of the catalog: the results are data, checked against the
plan, and what it writes is the one block of each file that the record makes. Exit 0 it worked, 2 it cannot be done (a usage
error, a catalog that cannot be read here).

Python 3.9 and later, standard library only."""

import json
import os
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
    jobs = compat.plan(seams.get("project") or _project(), ns, seams.get("tags"))
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
    results = sorted(Path(args.results).rglob("*.json")) if Path(args.results).is_dir() else []
    for item, sets in sorted(passed(planned, results, err).items()):
        kind, uid = e2e.parse(item, built)
        unit = built["playgrounds"].get(uid)
        if kind != "playground" or unit is None or unit.get("catalog") != ns:
            continue
        local = uid.split("/", 1)[1] if uid.startswith("@") else uid
        path = (folder / "playgrounds" / local / "compose.yml").resolve()
        if folder not in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        written = compat.write(text, compat.merged(unit.get("tested") or [], sets))
        if written != text:
            path.write_text(written, encoding="utf-8")
            said = "; ".join(", ".join(f"{key} {value}" for key, value in one.items() if not key.endswith("_REGISTRY")) for one in sets)
            out.write(f"{unit['name']}: tested on {said}\n")
    return 0


@entry
def release_main(argv, out, err, **seams) -> int:
    """`ci versions-release`."""
    found = Parser(prog="catalog.py ci versions-release", description="Raises the patch number of the catalog's version.")
    found.add_argument("path", nargs="?", default=".", help="the catalog folder, a git checkout with its tags")
    args = parsed(found, argv)
    folder = Path(args.path)
    if not (folder / "catalog.yml").is_file():
        raise Usage(f"{args.path} has no catalog.yml")
    latest = releases.latest_tag(folder)
    numbers = releases.numbers(latest[1:]) if latest else (0, 0, 0)
    version = f"{numbers[0]}.{numbers[1]}.{numbers[2] + 1}"
    file = folder / "catalog.yml"
    text, _ = releases.edit_version(file.read_text(encoding="utf-8"), version)
    file.write_text(releases.with_legacy(folder, text), encoding="utf-8")
    out.write(f"{version}\n")
    if os.environ.get("GITHUB_OUTPUT"):
        write_outputs({"version": version})
    return 0
