"""`catalog.py ci versions-plan`, `versions-record` and `versions-release`: the steps of the module `versions.yml` (level 7 of the
standard), which keeps what each playground says it was tested with (`x-gravitee.tested`) up with Gravitee's releases. The rules
of what is tried and what is kept are the Lab's (`lab_ui/compat.py`). Each step reads the catalog through the Lab it is linked into
(`catalog.py link PATH`), as the end-to-end run does.

    versions-plan PATH [--github-output]
        The matrix of the night: one job for each playground of the catalog and each set of versions it is to be tried on, which
        writes two outputs, `matrix` (`{"include": [{item, versions, name}]}`) and `count`. A set that failed is not tried again
        while the playground's files and the Lab are the ones it failed on (the matrix, below).
    versions-record PATH --plan FILE --results DIR
        Writes in the catalog at PATH the sets that passed: each file of DIR is `{item, versions, code}`, which a run wrote, and only
        a job of the plan (FILE, the matrix the plan wrote) whose code is 0 counts. Prints a line for each playground it wrote,
        with the sets it kept. A file it does not write the way a person writes it is left out and said (with `$GITHUB_OUTPUT`,
        `left_out`, how many): the others are recorded. Each file is read again by the Lab once written: a `tested` that is not
        the sets it meant, or any other change, puts every file back and fails (exit 1), so that nothing is pushed.
        Every job that ran, passed or failed, is written in the matrix of the catalog, docs/compatibility.json, and in its page,
        docs/compatibility.md. With `$GITHUB_OUTPUT`: `tested` (true when a `tested` was written: the catalog has news for the
        Labs), `failing` and `fixed`, the issues to open and to close, one for each playground and minor version of Gravitee.
    versions-release PATH
        Raises the patch number of `version` in catalog.yml past the newest tag, and prints the new version. Prints nothing, and
        changes nothing, for a catalog that has no release (it is followed commit by commit) or whose catalog.yml already says a
        version newer than its newest tag (a release its maintainers prepared: what was recorded ships with it).

The job that records runs nothing of the catalog: the results are data, checked against the plan, and what it writes is the one
block of each file that the record makes. Exit 0 it worked, 1 a file could not be written as meant, 2 it cannot be done (a usage
error, a catalog that cannot be read here).

Python 3.9 and later, standard library only."""

import datetime
import hashlib
import json
import os
import subprocess
from pathlib import Path

from ... import compat, e2e, paths
from .. import release as releases
from ..rules import catalog_meta
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


# The matrix: every set of versions a playground was tried on, and how it went, which the record writes and the plan reads
MATRIX = Path("docs") / "compatibility.json"
PAGE = Path("docs") / "compatibility.md"
LEFT = (".state", "secrets", ".env")   # what a run leaves in a playground's folder: no file of the catalog


def _local(uid):
    return uid.split("/", 1)[1] if uid.startswith("@") else uid


def fingerprint(folder):
    """What a result was had on, of the playground: the files of its folder, its `tested` left out, which the record writes."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        name = path.relative_to(folder).as_posix()
        if name.split("/", 1)[0] in LEFT:
            continue
        data = path.read_bytes()
        if name == "compose.yml":
            data = compat.TESTED.sub("", data.decode("utf-8")).encode("utf-8")
        digest.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return digest.hexdigest()[:16]


def lab_commit():
    """What a result was had on, of the Lab: its commit, since a seeder that changed may make a set work."""
    try:
        return subprocess.run(["git", "-C", str(paths.ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def read_matrix(folder):
    try:
        found = json.loads((Path(folder) / MATRIX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": 1, "playgrounds": {}}
    if not isinstance(found, dict) or not isinstance(found.get("playgrounds"), dict):
        return {"schema": 1, "playgrounds": {}}
    return found


def page(matrix):
    """The matrix as people read it: a table for each playground, the newest versions first."""
    lines = ["# Compatibility", "",
             "What each playground was tried on, as the workflow *Gravitee versions* wrote it: a start from nothing, on a machine with",
             "no lab, with the license, as `make e2e` does. A set that fails opens an issue labeled `versions`.", ""]
    for local, held in sorted(matrix["playgrounds"].items(), key=lambda item: item[1].get("name", item[0]).lower()):
        lines += [f"## {held.get('name', local)}", "", "| Versions | Result | Date |", "|---|---|---|"]
        rows = sorted(held.get("results", {}).items(), reverse=True,
                      key=lambda item: [compat.order(found) for found in map(compat.parse, item[0].replace(",", " ").split()) if found])
        for label, result in rows:
            run = f" ([run]({result['run']}))" if result.get("run") else ""
            lines.append(f"| {label} | {'Works' if result.get('passed') else 'Fails' + run} | {result.get('date', '')} |")
        lines.append("")
    return "\n".join(lines)


def _line(label):
    """The minor version of Gravitee a set is of, from the first version it says: "4.13"."""
    for word in label.replace(",", " ").split():
        found = compat.parse(word)
        if found:
            return ".".join(map(str, found[0][:2]))
    return label


@entry
def plan_main(argv, out, err, **seams) -> int:
    """`ci versions-plan`."""
    found = Parser(prog="catalog.py ci versions-plan", description="The versions the playgrounds are tried on tonight.")
    found.add_argument("path", nargs="?", default=".", help="the catalog folder, linked into this Lab (default: the current folder)")
    found.add_argument("--github-output", action="store_true", help="write matrix and count to $GITHUB_OUTPUT")
    args = parsed(found, argv)
    folder, ns = _catalog(args.path)
    night = seams.get("night", datetime.date.today().toordinal())
    matrix, lab = read_matrix(folder), seams.get("lab", lab_commit())

    def failed(uid, label):
        result = matrix["playgrounds"].get(_local(uid), {}).get("results", {}).get(label)
        return bool(result) and not result.get("passed") and result.get("lab") == lab \
            and result.get("files") == fingerprint(folder / "playgrounds" / _local(uid))
    jobs = compat.plan(seams.get("project") or _project(), ns, seams.get("tags"), warn=lambda said: err.write(f"{said}\n"), night=night,
                       failed=failed)
    out.write("".join(f"{job['name']}\n" for job in jobs) or "Every playground is tested on the newest versions\n")
    if args.github_output:
        write_outputs({"matrix": json_line({"include": jobs}), "count": str(len(jobs))})
    return 0


def passed(planned, results, err):
    """{item: [set]} of the results that are a job of the plan and ended well; any other file is said on `err` and left out."""
    found = {}
    for job, code in outcomes(planned, results, err):
        if code == 0:
            found.setdefault(job["item"], []).append(json.loads(job["versions"]))
    return found


def outcomes(planned, results, err):
    """[(job of the plan, exit code)] of the results that are a job of the plan; any other file is said on `err` and left out."""
    asked = {(job["item"], json.dumps(json.loads(job["versions"]), sort_keys=True)): job for job in planned}
    found = []
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
        else:
            found.append((asked[key], code))
    return found


def record_matrix(folder, built, planned, results, err, lab):
    """Writes each job that ran in the matrix and its page; (lines said, the issues to open, the issues to close). An exit code of
    2 is a run that was not tried (a login or a setting only a person can give): it says nothing of the versions."""
    matrix = read_matrix(folder)
    server, repository, run_id = (os.environ.get(name, "") for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    run = f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else ""
    today = datetime.date.today().isoformat()
    said, failing, fixed = [], {}, {}
    for job, code in outcomes(planned, results, err):
        kind, uid = e2e.parse(job["item"])
        unit = built["playgrounds"].get(uid)
        if kind != "playground" or unit is None or code == 2:
            continue
        local = _local(uid)
        label = job["name"][len(unit["name"]) + len(" on "):]
        entry = matrix["playgrounds"].setdefault(local, {"name": unit["name"], "results": {}})
        entry["name"] = unit["name"]
        entry["results"][label] = {"passed": code == 0, "date": today, "files": fingerprint(folder / "playgrounds" / local),
                                   "lab": lab, **({"run": run} if run and code else {})}
        title = f"{unit['name']} does not work on Gravitee {_line(label)}"
        if code:
            said.append(f"{unit['name']}: does not work on {label}")
            failing[title] = {"title": title, "body": f"{unit['name']} does not work on {label}" + (f": {run}" if run else "")}
        else:
            fixed.setdefault(title, {"title": title, "body": f"{unit['name']} works on {label}" + (f": {run}" if run else "")})
    fixed = {title: issue for title, issue in fixed.items() if title not in failing}
    if said or fixed:
        (folder / MATRIX).parent.mkdir(exist_ok=True)
        (folder / MATRIX).write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (folder / PAGE).write_text(page(matrix), encoding="utf-8")
    return said, list(failing.values()), list(fixed.values())


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
    follow = compat.followed(built)
    results = sorted(Path(args.results).rglob("*.json")) if Path(args.results).is_dir() else []
    written, said, meant, left_out = {}, [], {}, 0
    try:
        for item, sets in sorted(passed(planned, results, err).items()):
            kind, uid = e2e.parse(item)
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
    lines, failing, fixed = record_matrix(folder, built, planned, results, err, seams.get("lab", lab_commit()))
    out.write("".join(f"{line}\n" for line in [*said, *lines]))
    if os.environ.get("GITHUB_OUTPUT"):
        write_outputs({**({"left_out": str(left_out)} if left_out else {}), **({"tested": "true"} if said else {}),
                       **({"failing": json_line(failing)} if failing else {}), **({"fixed": json_line(fixed)} if fixed else {})})
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
    current = catalog_meta(text).get("version")
    if latest is None or (current and releases.numbers(current) > releases.numbers(latest[1:])):
        return 0   # no release to make: commit by commit, or a release its maintainers prepared
    numbers = releases.numbers(latest[1:])
    version = f"{numbers[0]}.{numbers[1]}.{numbers[2] + 1}"
    text, _ = releases.edit_version(text, version)
    file.write_text(releases.with_legacy(folder, text), encoding="utf-8")
    out.write(f"{version}\n")
    return 0
