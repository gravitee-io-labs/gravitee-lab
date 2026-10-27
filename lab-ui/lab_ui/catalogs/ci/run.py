"""`catalog.py ci run [PATH] [--all] [--base REF] [--app NAME]`: the local runner behind `make ci`. It runs on a person's
machine what the CI of a catalog runs without a license, in the order CI runs it, on the architecture of this machine:

    plan        the units changed since `origin/main` (`--base REF` for another commit, `--all` for every unit)
    manifests   every image they pull exists in its registry
    build       the apps of those units, for this machine's architecture
    test        the tests of each app in its image, offline (`--app NAME`: that app alone)
    smoke       what does not need the platform starts and answers: from standard level 2, when the stage is in this Lab
    scan        the images the build made have no critical flaw that has a fix: from standard level 6
    gitleaks    the secrets of every commit since the base, where Docker can run the pinned container

(`make ci` runs `check` first.) The plan is the CI's own function with the same inputs, so the units a person builds before the
push are the units the pull request will build; the one difference is the architecture, which here is the machine's.

A stage that is not in this Lab yet is **said and skipped, never silent**: `make ci` works from level 1, and a run that skipped
something does not say it is all good. A scan that cannot run here because Docker is not installed or not running, or because the
Lab's gitleaks or trivy image has no digest yet, is skipped the same way, with that reason; the scan that cannot run for any other reason
(a base git does not know, a container that failed, a report that is not JSON) is a stage that could not run, with the stage's
own words. A stage the change does not ask for (no unit changed, or nothing in it builds: a README alone, or no app) is not
asked, as CI does not ask it, so that such a change can end in All good. A unit the trust policy refuses ends the run before
anything is built (that is what `check` is for); a build that fails ends the tests of that run (they would test an image that
is not there).

The folder is read where it is, as `make check` reads it (a temporary project of this run in which it is linked: nothing is linked
into the Lab and nothing of it is written), so `make ci` needs no `make dev` first; the catalogs it includes from have to be the
Lab's own, installed or linked.

Exit 0 every stage that ran passed (the skipped ones are listed), 1 one of them found something to fix, 2 this machine cannot
run it.

Python 3.9 and later, standard library only."""

import importlib
import io
import json
import os
import sys
from typing import Callable, Dict, List, NamedTuple, Optional

from .. import report, tree as trees
from . import SKIPPABLE, STAGES, Usage, entry, host_platform, load, parsed, parser, refusals
from . import plan as planning
from .build import app_of, specs_of

ORDER = ("manifests", "build", "test", "smoke", "scan", "gitleaks")
SMOKE_LEVEL = 2   # the standard level that adds `ci smoke` to the workflow of `compile`
SCAN_LEVEL = 6    # the standard level that adds `ci scan-built` to it
STAGE_OF = {"scan": "scan-built"}   # the stage word of a step whose name here is shorter
SCANS = ("scan", "gitleaks")   # the steps that run a pinned container: one this machine cannot run is a skip, with its reason


class Step(NamedTuple):
    """How a stage ended in this run: `state` is passed, failed, error (this machine cannot run it), skipped (it is not here, or
    cannot run here) or absent (it is not a stage of this standard level)."""
    name: str
    state: str
    note: str = ""


def level() -> int:
    """The standard level of this Lab (`standard.LEVEL`), 1 while the module is not here: a stage of a higher level is then not
    asked for."""
    try:
        module = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.standard")
    except ModuleNotFoundError as error:
        if error.name != f"{__package__.rsplit('.', 1)[0]}.standard":
            raise
        return 1
    value = getattr(module, "LEVEL", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def stage_function(name: str, stages: Optional[Dict[str, Callable]] = None) -> Optional[Callable]:
    """The `main` of a stage (a step of this run, or the stage word it stands for), or None when its module is not in this Lab yet."""
    if stages and name in stages:
        return stages[name]
    module_name, function = STAGES[STAGE_OF.get(name, name)]
    target = f"{__package__}.{module_name}"
    try:
        module = importlib.import_module(target)
    except ModuleNotFoundError as error:
        if error.name != target:
            raise
        return None
    return getattr(module, function, None)


def base_of(root: str, asked: str) -> str:
    """The commit the change is measured from: the one asked for, else `git merge-base origin/main HEAD`; "" when there is none."""
    if asked:
        return trees.resolve(root, asked) or ""
    return trees.default_base(root) or "" if trees.is_repository(root) else ""


def units_with_app(units, root: str, name: str, lab=None) -> List[str]:
    """The keys of the loaded units that build an app of this name."""
    specs, _, _ = specs_of(units, host_platform(), root, lab, local=True)
    return sorted({spec.unit for spec in specs if app_of(spec.context) == name})


@entry
def main(argv, out, err, **seams) -> int:
    found = parser("run", "Plan, manifests, build and test on this machine, as CI does.", fmt=False, ci=False)
    found.add_argument("--all", action="store_true", help="every unit, not only those changed since the base")
    found.add_argument("--base", default="", help="the commit the change is measured from (default: origin/main)")
    found.add_argument("--app", default=None, help="build and test this app only (the folder of its build context)")
    args = parsed(found, argv)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    platform = host_platform()
    steps: List[Step] = []

    # plan: the CI's own function, on this machine's architecture
    tree = trees.FolderTree(root)
    base, reason = base_of(root, args.base), ""
    if args.base and not base:
        raise Usage(f"{report.printable(args.base)[:60]} is not a commit of this repository")
    changed = None
    if not args.all:
        if not base:
            reason = "there is no origin/main to measure the change from" if trees.is_repository(root) else "this is not a git repository"
        else:
            changed = planning.changed_files(root, base, None)
            if changed is None:
                reason = "git could not say what changed"
    event = "workflow_dispatch" if args.all else "pull_request"
    plan = planning.compute(tree, event, changed, base, [platform], reason)
    print("== plan", file=out)
    out.write("\n".join(planning.lines_of(plan)) + "\n")
    if plan.problems:
        out.write(report.render(plan.problems, "text"))
        steps.append(Step("plan", "failed"))
        return finish(steps, out)
    steps.append(Step("plan", "passed", f"{len(plan.units)} units"))
    loading = None
    wanted: List[str] = []
    if plan.units:
        # the units are read once, by the Lab's own screen and policy, and every stage is given what was read
        names = [(each.partition(":")[0], each.partition(":")[2]) for each in plan.units]
        loading = load(root, names)
        if loading.refused:
            out.write(report.render(refusals(loading.refused, "tried"), "text"))
            steps.append(Step("units", "failed", "the trust policy refuses a unit: make check says why"))
            return finish(steps, out)
        wanted = [unit.key for unit in loading.units]
        if args.app:
            wanted = units_with_app(loading.units, root, args.app, seams.get("lab"))
            if not wanted:
                raise Usage(f"no app named {report.printable(args.app)[:60]} in the units that changed (use --all for every unit)")
    else:
        print("Nothing changed in a unit: there is nothing to check, build or start; the commits are still scanned.", file=out)
    units_json = json.dumps(wanted)
    stages = seams.pop("stages", None)
    asked = {"manifests": [root, "--units", units_json],
             "build": [root, "--units", units_json, "--platform", platform, *(["--app", args.app] if args.app else [])],
             "test": [root, "--units", units_json, *(["--app", args.app] if args.app else [])],
             "smoke": [root, "--units", units_json],
             "scan": [root, "--units", units_json, *(["--app", args.app] if args.app else [])],
             "gitleaks": [root, "--base", base]}
    package = sys.modules[__package__]
    before = package.LOADER

    def given(folder, wanted_names):
        """The one reading, given to every stage for the units it asks for."""
        keep = set(wanted_names)
        return package.Loading([each for each in loading.units if (each.kind, each.name) in keep],
                               [each for each in loading.refused if (each.kind, each.name) in keep], list(loading.notes))
    if loading is not None:
        package.LOADER = given
    try:
        built = True
        for name in ORDER:
            if name != "gitleaks" and not plan.units:
                steps.append(Step(name, "absent", "no unit changed"))
                continue
            if (name == "smoke" and level() < SMOKE_LEVEL) or (name == "scan" and level() < SCAN_LEVEL):
                steps.append(Step(name, "absent", f"not a stage of standard level {level()}"))
                continue
            if name in ("build", "test", "scan") and not plan.compile:
                steps.append(Step(name, "absent", "the change builds nothing (a README alone, or no app)"))
                continue
            if name in ("test", "scan") and not built:
                steps.append(Step(name, "skipped", "the build did not end well, so there is no image to "
                                  + ("test" if name == "test" else "scan")))
                continue
            function = stage_function(name, stages)
            if function is None:
                steps.append(Step(name, "skipped", "this stage is not in this Lab yet"))
                continue
            print(f"== {name}", file=out)
            said = io.StringIO() if name in SCANS else err   # (what a scan says it could not do is read, then shown)
            code = function(asked[name], out, said, **seams)
            if name == "build" and code != 0:
                built = False
            why = not_here(said.getvalue()) if name in SCANS and code == 2 else None
            if said is not err and why is None:
                err.write(said.getvalue())
            if code == 0:
                steps.append(Step(name, "passed"))
            elif code == 1:
                steps.append(Step(name, "failed"))
            elif why:
                steps.append(Step(name, "skipped", why))
            else:
                steps.append(Step(name, "error", "this machine cannot run it"))
    finally:
        package.LOADER = before
    return finish(steps, out)


def not_here(said: str) -> Optional[str]:
    """Why a stage that is not asked of a person's machine cannot run here, from the one line it wrote on exit 2, when it is one of
    the reasons that make it a skip (SKIPPABLE: no Docker, Docker not running, an image with no digest yet); None for any other
    reason, which is shown as it was said."""
    for line in said.splitlines():
        if any(mark in line for mark in SKIPPABLE):
            return report.printable(line.partition("catalog.py ci: ")[2] or line)[:200]
    return None


def finish(steps: List[Step], out) -> int:
    """The last lines: what each stage did, and the exit code (2 for a stage this machine cannot run, 1 for one that found something)."""
    print("", file=out)
    width = max(len(each.name) for each in steps)
    for each in steps:
        mark = {"passed": "✓", "failed": "✗", "skipped": "-", "absent": "-", "error": "!"}[each.state]
        said = {"error": "could not run", "absent": "not asked"}.get(each.state, each.state)
        out.write(f"{mark} {each.name.ljust(width)}  {said}" + (f": {each.note}" if each.note else "") + "\n")
    failed = [each for each in steps if each.state == "failed"]
    skipped = [each for each in steps if each.state == "skipped"]
    errors = [each for each in steps if each.state == "error"]
    if failed:
        out.write(f"{len(failed)} stage{'s' if len(failed) != 1 else ''} to fix.\n")
    elif errors:
        out.write("Not everything could run on this machine.\n")
    elif skipped:
        out.write(f"{len(skipped)} stage{'s' if len(skipped) != 1 else ''} skipped.\n")
    else:
        out.write("All good\n")
    return 1 if failed else 2 if errors else 0


__all__ = ["Step", "finish", "level", "main", "not_here", "stage_function"]
