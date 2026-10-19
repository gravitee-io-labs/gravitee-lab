"""`catalog.py ci e2e-plan` and `catalog.py ci e2e-gate` (E2E01 to E2E05): the plan of the optional module `e2e.yml`, the one
workflow that holds the CI license, and the last gate in the job that holds it. Level 3 of the standard.

The module is dispatched from `main` by a maintainer who has read a pull request, with the number of the pull request and the commit
they read. The pull request is **data**: its files are checked out into a folder of their own (`--change DIR`) and read by the plan, and
tried from there; nothing of it is ever a workflow, a step or a permission. There is no push trigger and no branch to push a pull
request's commit to, because a push runs every workflow of the pushed commit with the repository's secrets.

    e2e-plan PATH (--all | --changed | --items -- WORDS) [--change DIR --sha SHA [--pr N] [--ref REF]] [--github-output]
        The matrix of the module: one job for each item and each set of versions it says it was tested with (`x-gravitee.tested`), or one
        on the core's defaults. It writes two outputs, `matrix` (`{"include": [{item, tested, name}]}`, the form `e2e.jobs` makes and the
        workflow reads) and `count`, each one line, and nothing at all when a code below says no.
    e2e-gate PATH --change DIR --sha SHA [--pr N]
        E2E03 again, in the job that holds the license: the head can move between the plan and that job.

    E2E01  An item whose own services (`project._own`: neither the core's nor an included unit's) reference the license variable in
           `environment`, `env_file`, `command` or a build argument. Otherwise a unit could map the license into a container of its own
           and send it out over open egress, and after a human review that would look like the legitimate use of the variable by the
           platform's services. Only an overlay on a core service may read it. How it is found: the unit is read again by Compose, in
           the scrubbed environment of 5.5, with the variable set to a marker made at random for this run, and a service of the unit's own
           whose resolved definition holds the marker reads the license, whichever way the file wrote it (an alias, a block scalar, a
           `$${...}` that is not a reference, an `env_file`). The summary says `Surface reads: license` for a unit whose files reference
           the variable at all (REVIEWING.md asks about it).
    E2E02  A pull request tried from a ref other than `main`.
    E2E03  A head that is not the commit given.
    E2E04  A bad input: a number over 7 digits, a commit that is not 40 hexadecimal characters, an `--items` word that does not name an
           item or starts with a dash (the words come after `--`, so an option cannot be injected).
    E2E05  A change since the merge base with `main` to `.github/`, `.catalog/`, `Makefile` or `catalog.yml`: a maintainer reads those as
           code, merges them first, and then tries the items.

An item that is refused by the trust policy, or whose files do not load, ends the plan (exit 1), and is never left out as if the change had
deleted it. A unit that needs a setting Compose cannot resolve without one cannot be read by the plan: it is planned on the defaults of
the core (the Lab's `make e2e` says "not tried" for it, exit 2), but only if no file of its folder references the license variable at
all, and otherwise the plan stops, because it could not tell whether a service of its own reads it. Exit 0 it planned, 1 a code said
no, 2 the plan cannot be made (a usage error, a catalog that cannot be read here).

Python 3.9 and later, standard library only."""

import json
import os
import re
import secrets
from typing import List, NamedTuple, Optional, Sequence, Tuple

from .. import gitx, report, tree as trees
from ..report import ERROR, Problem
from ..rules import catalog_meta, say
from . import (Parser, Unavailable, Usage, conclude, entry, json_line, load, parsed, parser, plan as planning, refusals, write_outputs,
               write_summary)
from .smoke import needs_settings

MESSAGES = {
    "E2E01": (ERROR, "{item}: {service} reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it."),
    "E2E02": (ERROR, "A pull request is tried from the workflow of main. Fix: run this workflow on main."),
    "E2E03": (ERROR, "The head of pull request {n} is {sha7}, not the commit you read ({sha7}). Fix: read the new commits, then run "
                     "again with the new one."),
    "E2E04": (ERROR, "{input} is not what it says. Fix: pr is digits only; sha is 40 hexadecimal characters; items are words that name "
                     "an item and do not start with a dash."),
    "E2E05": (ERROR, "The change touches {paths}, which this workflow does not try. Fix: merge it first, a maintainer reads it as code, "
                     "then try the items."),
}
LICENSE_VARIABLE = "GRAVITEE_LICENSE"
MAIN = "refs/heads/main"
SHA = re.compile(r"[0-9a-f]{40}")
NUMBER = re.compile(r"[0-9]{1,7}")
WORD = re.compile(r"(addon|playground|setup):(?:@([a-z][a-z0-9-]{0,22}[a-z0-9])/)?([a-z0-9-]{1,100})")
# What a pull request may not touch if its items are to be tried (E2E05): they are read as code, and decide what every item is judged by
GUARDED = (".github", ".catalog", "Makefile", "catalog.yml")
SHOWN_PATHS = 5
MAX_WORDS = 256
TEXT_LIMIT = 1024 * 1024
FILES_LIMIT = 500


def finding(which: str, path: str = "", **fields) -> Problem:
    """The Problem of a code of this stage (`which`: E2E01...), its message the words of 6.11 with the fields put in."""
    severity, message = MESSAGES[which]
    return Problem(which, severity, path, 0, say(message, **fields))


def head_mismatch(number: str, head: str, given: str) -> Problem:
    """E2E03: the one message that names two commits with the same word (`{sha7}`), the head first and the commit read second."""
    severity, message = MESSAGES["E2E03"]
    text = message.replace("{n}", number).replace("{sha7}", head[:7], 1).replace("{sha7}", given[:7], 1)
    return Problem("E2E03", severity, "", 0, text)


# ---------------------------------------------------------------------------------------------------------------------
# The inputs
# ---------------------------------------------------------------------------------------------------------------------

def split_words(argv: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(the options, the words after the first `--`): what is typed for `--items` comes after `--`, so that no word can be an
    option of this command, whatever it looks like."""
    argv = list(argv)
    if "--" in argv:
        at = argv.index("--")
        return argv[:at], argv[at + 1:]
    return argv, []


def pull_request_number(given: Optional[str], change: Optional[str], environ: Optional[dict] = None) -> str:
    """The number to put in E2E03: the one given, else the one the dispatch carries in the event of the run, else the one the
    checkout of the pull request named its ref with; `?` when none says. Only digits are ever returned."""
    if given and NUMBER.fullmatch(given):
        return given
    environ = os.environ if environ is None else environ
    path = environ.get("GITHUB_EVENT_PATH")
    if path:
        try:
            with open(path, "rb") as handle:
                data = json.loads(handle.read(TEXT_LIMIT).decode("utf-8"))
            number = ((data.get("inputs") if isinstance(data, dict) else None) or {}).get("pr")
            if isinstance(number, str) and NUMBER.fullmatch(number):
                return number
        except (OSError, ValueError, RecursionError, AttributeError):
            pass
    if change:
        try:
            done = gitx.run(["for-each-ref", "--format=%(refname)", "refs/remotes/pull"], cwd=os.path.realpath(change),
                            env=trees.QUIET, timeout=30)
            for name in done.out.decode("utf-8", "replace").split("\n") if done.code == 0 else ():
                found = re.fullmatch(r"refs/remotes/pull/([0-9]{1,7})/head", name.strip())
                if found:
                    return found.group(1)
        except (gitx.CatalogError, gitx.OutputLimit):
            pass
    return "?"


def check_inputs(sha: Optional[str], number: Optional[str], words: Optional[Sequence[str]]) -> List[Problem]:
    """E2E04 for each input that is not what it says."""
    bad: List[Problem] = []
    if sha is not None and not SHA.fullmatch(sha):
        bad.append(finding("E2E04", input="sha"))
    if number is not None and not NUMBER.fullmatch(number):
        bad.append(finding("E2E04", input="pr"))
    if words is not None and (not words or len(words) > MAX_WORDS or any(not WORD.fullmatch(word) for word in words)):
        bad.append(finding("E2E04", input="items"))
    return bad


def head_of(folder: str) -> Optional[str]:
    return trees.head_of(folder)


def merge_base(change: str, sha: str, main_tip: Optional[str]) -> Optional[str]:
    """The commit the change left main at: `git merge-base origin/main SHA` in the pull request's checkout, else with the tip of the
    main checkout when the pull request's clone has that commit. None when neither says."""
    for other in ("origin/main", main_tip):
        if not other:
            continue
        try:
            done = gitx.run(["merge-base", other, sha], cwd=os.path.realpath(change), env=trees.QUIET, timeout=60)
        except (gitx.CatalogError, gitx.OutputLimit):
            continue
        found = done.out.decode("utf-8", "replace").strip()
        if done.code == 0 and SHA.fullmatch(found):
            return found
    return None


def guarded_paths(change: str, base: str, sha: str) -> Optional[List[str]]:
    """The paths below `.github/`, `.catalog/`, the `Makefile` and `catalog.yml` that the change touches since `base`, or None when git
    cannot say. `git diff --name-only <merge-base> <sha> -- .github .catalog Makefile catalog.yml`."""
    try:
        done = gitx.run(["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", base, sha, "--", *GUARDED],
                        cwd=os.path.realpath(change), env=trees.QUIET, timeout=120, limit=gitx.OUTPUT_LIMIT)
    except (gitx.CatalogError, gitx.OutputLimit):
        return None
    if done.code != 0:
        return None
    return sorted({os.fsdecode(name) for name in done.out.split(b"\0") if name})


def paths_text(found: Sequence[str]) -> str:
    shown = ", ".join(report.printable(path) for path in list(found)[:SHOWN_PATHS])
    return shown + (f" and {len(found) - SHOWN_PATHS} more" if len(found) > SHOWN_PATHS else "")


# ---------------------------------------------------------------------------------------------------------------------
# The license
# ---------------------------------------------------------------------------------------------------------------------

def references_license(text: str) -> bool:
    """Whether a text references the license variable the way Compose reads it: `$NAME` or `${NAME...` after an odd number of dollar
    signs (an even number is an escaped one, `$${NAME}`, which is text). Found with `find` and counted backwards from each place, so
    that a line of a million dollar signs costs one pass and not a pattern's backtracking."""
    size, start = len(LICENSE_VARIABLE), 0
    while True:
        at = text.find(LICENSE_VARIABLE, start)
        if at == -1:
            return False
        start = at + 1
        after = at + size
        if after < len(text) and (text[after].isalnum() or text[after] == "_"):
            continue   # (GRAVITEE_LICENSE_FILE is another name)
        here = at - 1 if at > 0 and text[at - 1] == "{" else at
        back = here
        while back > 0 and text[back - 1] == "$":
            back -= 1
        if (here - back) % 2 == 1:
            return True


def folder_references_license(folder: str) -> bool:
    """Whether a file of a unit's folder references the license variable: what a unit that cannot be read as Compose is judged by. Read
    through the tree (no link followed, a size limit), so a reference cannot be hidden, and cannot make this read anything else."""
    tree = trees.FolderTree(folder)
    seen = 0
    for each in tree.entries():
        if each.mode not in trees.REGULAR or each.size > TEXT_LIMIT:
            continue
        seen += 1
        if seen > FILES_LIMIT:
            return True   # (so many files that they were not all read: not a unit to run with a license)
        data = tree.read(each.path, TEXT_LIMIT)
        if data is not None and references_license(data.decode("utf-8", "replace")):
            return True
    return False


class Surface(NamedTuple):
    """What the license reaches in a unit: the own services that read it (E2E01), and whether its files reference it at all."""
    own: List[str]
    reads: bool


def license_surface(unit, root: str, tree=None) -> Surface:
    """E2E01 for a loaded unit: its own services whose resolved definition holds a marker that only this run knows, set as the license
    variable. The unit must be resolvable with it (`Loaded.resolve`); one that is not is Unavailable, because the run that follows holds
    the real license."""
    if not unit.own:   # (it defines no service of its own: there is none that could read the license)
        return Surface([], unit_references_license(unit, root, tree))
    if unit.resolve is None:
        raise Unavailable(f"{unit.key} cannot be read again with the license variable set, so the plan cannot tell whether a service of "
                          "its own reads it: it is not tried")
    marker = "LABMARK-" + secrets.token_hex(12)
    try:
        resolved = unit.resolve({LICENSE_VARIABLE: marker})
    except Exception as error:   # noqa: BLE001 - whatever Compose or the loader says, the unit was not read: the plan stops
        raise Unavailable(f"{unit.key} could not be read again to look for the license: "
                          + report.printable(str(error))[:120]) from None
    services = resolved.get("services") if isinstance(resolved, dict) else None
    exposed = []
    for name in unit.own:
        definition = (services or {}).get(name) if isinstance(services, dict) else None
        if definition is not None and marker in json.dumps(definition, default=str):
            exposed.append(str(name))
    return Surface(sorted(exposed), unit_references_license(unit, root, tree))


def unit_references_license(unit, root: str, tree=None) -> bool:
    """Whether the compose file of a unit, or any file of its folder, references the license variable at all."""
    text = (tree or trees.FolderTree(root)).text(unit.file, TEXT_LIMIT)
    if text is not None and references_license(text):
        return True
    where = os.path.dirname(os.path.join(root, unit.file)) if unit.kind != "setup" else None
    return bool(where and os.path.isdir(where) and folder_references_license(where))


# ---------------------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------------------

class Planned(NamedTuple):
    jobs: List[dict]
    problems: List[Problem]
    reads: List[str]
    notes: List[str]


def lab_item(ns: str, key: str) -> str:
    """`kind:id` the way the Lab names an item: bare for the first catalog, `kind:@catalog/name` for the others."""
    from ... import e2e as engine
    return engine.lab_item(ns, key)


def item_key(word: str, ns: str) -> Optional[str]:
    """`kind:name` of an `--items` word (`kind:name` or `kind:@ns/name` for this catalog), or None when it names another catalog."""
    found = WORD.fullmatch(word)
    if not found:
        return None
    kind, owner, name = found.groups()
    return f"{kind}:{name}" if owner in (None, ns) else None


def make_plan(root: str, change: Optional[str], sha: Optional[str], number: Optional[str], ref: str, mode: str,
              words: Sequence[str], seams: dict) -> Planned:
    """The jobs of the module, or the codes that say no."""
    problems: List[Problem] = []
    notes: List[str] = []
    problems += check_inputs(sha, number, words if mode == "items" else None)
    if change is not None and ref != MAIN:
        problems.append(finding("E2E02"))
    if problems:
        return Planned([], problems, [], notes)
    where = root
    changed: Optional[List[str]] = None
    if change is not None:
        head = head_of(change)
        if head != sha:
            return Planned([], [head_mismatch(pull_request_number(number, change, seams.get("environ")), head or "0" * 40, sha or "")],
                           [], notes)
        base = merge_base(change, sha, head_of(root))
        if base is None:
            raise Unavailable("the pull request cannot be compared with main (is main fetched in its checkout?), so the plan cannot tell "
                              "whether it touches .github/, .catalog/, the Makefile or catalog.yml")
        touched = guarded_paths(change, base, sha)
        if touched is None:
            raise Unavailable("git could not say what the pull request changes")
        if touched:
            return Planned([], [finding("E2E05", paths=paths_text(touched))], [], notes)
        where = change
        if mode == "changed":
            changed = planning.changed_files(change, base, sha)
            if changed is None:
                raise Unavailable("git could not say what the pull request changes")
    elif mode == "changed":
        raise Usage("--changed measures a pull request: give --change DIR --sha SHA, or name the items, or use --all")
    tree = trees.FolderTree(where)
    meta = catalog_meta(tree.text("catalog.yml") or "")
    ns = meta.get("id", "")
    if not ns:
        raise Unavailable("catalog.yml gives no id, so the items of this catalog cannot be named: make check says why")
    existing = {unit.key: unit for unit in planning.listing(tree.entries()).units}
    if mode == "items":
        keys = [item_key(word, ns) for word in words]
        if any(key is None or key not in existing for key in keys):
            return Planned([], [finding("E2E04", input="items")], [], notes)
        selected = sorted(dict.fromkeys(keys))
    elif mode == "changed":
        selected = sorted({unit.key for unit in {planning.unit_of(path) for path in changed or ()} - {None}} & set(existing))
    else:
        selected = sorted(existing)
    if not selected:
        notes.append("no item to try")
        return Planned([], [], [], notes)
    names = [(key.partition(":")[0], key.partition(":")[2]) for key in selected]
    loading = load(where, names)
    lacking = {f"{each.kind}:{each.name}": each for each in loading.refused if needs_settings(each)}
    problems += refusals([each for each in loading.refused if not needs_settings(each)], "tried")
    units = {unit.key: unit for unit in loading.units}
    reads: List[str] = []
    jobs: List[dict] = []
    from ... import e2e as engine

    def sets(item: str) -> int:
        key = item.replace(f"@{ns}/", "")
        unit = units.get(key)
        return len([one for one in (unit.tested if unit else ()) if one])
    items = []
    for key in selected:
        item = lab_item(ns, key)
        if key in lacking:
            if folder_references_license(os.path.join(where, os.path.dirname(lacking[key].file))) \
                    or references_license(tree.text(lacking[key].file, TEXT_LIMIT) or ""):
                raise Unavailable(f"{item} needs a setting Compose cannot resolve without one, and its files reference "
                                  f"{LICENSE_VARIABLE}, so the plan cannot tell whether a service of its own reads it: it is not tried")
            notes.append(f"{item} needs settings: it is planned on the defaults and the run will say it was not tried")
            items.append(item)
            continue
        unit = units.get(key)
        if unit is None:
            continue
        surface = license_surface(unit, where, tree)
        for service in surface.own:
            problems.append(finding("E2E01", unit.file, item=item, service=report.printable(service)))
        if surface.reads:
            reads.append(item)
        items.append(item)
    if problems:
        return Planned([], problems, reads, notes)
    count = {item: sets(item) for item in items}
    jobs = engine.jobs(items, lambda item: count[item])
    if len(jobs) > engine.JOBS_LIMIT:
        raise Usage(f"The plan has {len(jobs)} jobs and a matrix of GitHub runs {engine.JOBS_LIMIT} at most: try the items a part at "
                    "a time")
    return Planned(jobs, [], reads, notes)


def lines_of(planned: Planned) -> List[str]:
    out = [f"E2E plan: {len(planned.jobs)} jobs"]
    out += [f"  {report.printable(job['name'])}" for job in planned.jobs]
    out += [f"Surface reads: license ({report.printable(item)})" for item in planned.reads]
    out += [report.printable(note) for note in planned.notes]
    return out


@entry
def plan_main(argv, out, err, **seams) -> int:
    """`ci e2e-plan`. Exit 1 when a code says no, 2 when the plan cannot be made."""
    options, words = split_words(argv)
    found = Parser(prog="catalog.py ci e2e-plan", description="The matrix of the end-to-end module.")
    found.add_argument("path", nargs="?", default=".", help="the catalog folder, on main (default: the current folder)")
    what = found.add_mutually_exclusive_group(required=True)
    what.add_argument("--all", action="store_true", help="every item")
    what.add_argument("--changed", action="store_true", help="the items a pull request touches (needs --change)")
    what.add_argument("--items", action="store_true", help="the items named after --")
    found.add_argument("--change", default=None, help="the folder the pull request was checked out into: read as data, never run")
    found.add_argument("--sha", default=None, help="the commit of the pull request you read: 40 hexadecimal characters")
    found.add_argument("--pr", default=None, help="the number of the pull request (for the message of E2E03)")
    found.add_argument("--ref", default=None, help="the ref the workflow runs on (default: $GITHUB_REF)")
    found.add_argument("--github-output", action="store_true", help="write matrix and count to $GITHUB_OUTPUT")
    found.add_argument("--format", choices=report.FORMATS, default=None)
    found.add_argument("--ci", action="store_true")
    args = parsed(found, options)
    mode = "all" if args.all else "changed" if args.changed else "items"
    if (args.change is None) != (args.sha is None):
        raise Usage("--change and --sha go together: the folder of the pull request, and the commit you read")
    if mode != "items" and words:
        raise Usage("words after -- are the items of --items")
    if args.github_output and not os.environ.get("GITHUB_OUTPUT"):
        raise Unavailable("--github-output needs $GITHUB_OUTPUT, which only a workflow step has")
    for folder in (args.path, args.change):
        if folder is not None and not os.path.isdir(folder):
            raise Usage(f"{report.printable(folder)} is not a folder")
    ref = args.ref if args.ref is not None else os.environ.get("GITHUB_REF", "")
    planned = make_plan(os.path.abspath(args.path), os.path.abspath(args.change) if args.change else None, args.sha, args.pr, ref, mode,
                        words, seams)
    if args.format == "json":
        out.write(json.dumps({"count": len(planned.jobs), "matrix": {"include": planned.jobs}, "notes": planned.notes,
                              "problems": [each._asdict() for each in planned.problems], "reads_license": planned.reads},
                             indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    else:
        out.write("\n".join(lines_of(planned)) + "\n")
        if planned.problems:
            out.write(report.render(planned.problems, args.format or ("github" if args.ci else "text")))
    if args.ci:
        write_summary("### End to end plan\n\n" + "\n".join(f"- {line}" for line in lines_of(planned)) + "\n"
                      + ("\n" + report.summary(planned.problems, "catalog.py check") if planned.problems else ""))
    if planned.problems:
        return 1
    if args.github_output:
        write_outputs({"matrix": json_line({"include": planned.jobs}), "count": str(len(planned.jobs))})
    return 0


@entry
def gate_main(argv, out, err, **seams) -> int:
    """`ci e2e-gate`: the head of the pull request is still the commit that was read. Exit 1 when it moved."""
    found = parser("e2e-gate", "The last gate of the job that holds the license.", fmt=True, ci=True)
    found.add_argument("--change", required=True, help="the folder the pull request was checked out into")
    found.add_argument("--sha", required=True, help="the commit you read: 40 hexadecimal characters")
    found.add_argument("--pr", default=None, help="the number of the pull request (for the message)")
    args = parsed(found, argv)
    bad = check_inputs(args.sha, args.pr, None)
    problems = list(bad)
    if not os.path.isdir(args.change):
        raise Usage(f"{report.printable(args.change)} is not a folder")
    if not bad:
        head = head_of(os.path.abspath(args.change))
        if head != args.sha:
            problems.append(head_mismatch(pull_request_number(args.pr, os.path.abspath(args.change), seams.get("environ")),
                                          head or "0" * 40, args.sha))
    if args.format != "json":
        out.write("The head of the pull request is the commit that was read\n" if not problems else "")
    return conclude("e2e-gate", problems, args, out, "catalog.py ci e2e-gate", title="The change is the one that was read")


__all__ = ["MESSAGES", "Planned", "Surface", "check_inputs", "finding", "gate_main", "guarded_paths", "head_mismatch", "item_key",
           "license_surface", "make_plan", "merge_base", "plan_main", "pull_request_number", "references_license", "split_words"]
