"""`catalog.py ci test PATH --units JSON [--app NAME] [--ci]` (TS01, TS02): the tests of each app run inside its own image,
offline and hardened. The images are those `ci build` just built on this machine (`--pull never`: nothing is pulled in place
of the image that was built), found as `ci build` found them: the services a unit defines that have a `build:` and an `image:`,
left out when the Lab's own seeder builds them.

An app is the folder of its build context, and its name is that folder's: `bakery-web` for `playgrounds/bank/apps/bakery-web`, the
`APP` of `make ci APP=bakery-web`. What runs, and the flags, are `apptests.py`'s:

    docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g
        --cpus 2 --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m -e PYTHONDONTWRITEBYTECODE=1 [-v GUIDE:/guide.md:ro
        -e GUIDE=/guide.md] --entrypoint python IMAGE -m unittest discover -s tests -t .

and the JavaScript tests of `tests/js` in the Node image the Makefile pins, with the app mounted read-only. Python apps run
`unittest`, JavaScript tests run in the Node image; an app in another language is not run in v1.

    TS01  {app}: {n} tests failed. Fix: run make ci APP={app} to see them.                 an error
    TS02  {app} has no tests. Fix: COPY tests ./tests in the Dockerfile and add some.       a warning (no tests, or none in the image)

What the tests printed is a stranger's program's output: it is shown between `::stop-commands::<token>` and `::<token>::`, and
the last 15 lines of a failed app (each cut to 500 characters) are in the summary. No variable of the runner reaches a test: the
environment is the build environment of 5.5, and `docker run` is given none of it. Each run has a name of its own (`apptests`),
and one that passes its time limit is removed by it.

The Node image is had before any test runs. A pull that Docker Hub or the network refuses is tried again after 20 s and 60 s; when
it still cannot be had the stage ends with exit 2 and says so, with the rate-limit text Docker gave and "this is not your
change": the author's tests did not fail, they were not run.

Python 3.9 and later, standard library only."""

import os
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

from .. import report
from ..report import Problem
from . import (apptests, Loaded, Unavailable, cell, conclude, docker_ready, entry, fence, host_platform, load, parse_units, parsed, parser,
               problem, refusals, run_docker, seconds, tail)
from .build import app_of, specs_of

TAIL = 15


class Result(NamedTuple):
    """How one app's tests went: `outcome` is apptests.Outcome, `seconds` how long it took."""
    unit: str
    image: str
    app: str
    path: str
    outcome: object
    seconds: float


def tool():
    """`apptests`, which builds an app and runs its tests in its image."""
    return apptests


def image_here(image: str, runner: Optional[Callable]) -> bool:
    return run_docker(["docker", "image", "inspect", image], timeout=60, runner=runner)[0] == 0


def node_unavailable(reason: str, apptests) -> Unavailable:
    """Why the Node image of the JavaScript tests could not be had, as the stage says it (exit 2): what Docker printed, and that it
    is not the author's change when the fault is Docker Hub's or the network's."""
    text = f"the Node image of the JavaScript tests could not be had: {report.printable(reason)[:300]}"
    infra = getattr(apptests, "INFRA", None)
    if infra is not None and infra.search(reason):
        text += ". This is not your change: run the job again in a few minutes"
    return Unavailable(text)


def run_all(units: Sequence[Loaded], specs, runner: Optional[Callable] = None, apptests=None,
            sleep: Optional[Callable] = None) -> List[Result]:
    """Runs the tests of each spec (an image `ci build` makes) and answers the results in order. Unavailable when an image is
    not here, because it was not built, or when the Node image the JavaScript tests need cannot be had (asked once, before the
    first test: after the new tries of a pull that Docker Hub or the network refused)."""
    apptests = apptests or tool()
    folders: Dict[str, str] = {unit.key: unit.folder for unit in units}

    def run(argv, timeout, cwd):
        return run_docker(argv, timeout=timeout, cwd=cwd, runner=runner)
    results: List[Result] = []
    node: Optional[str] = None
    tests = [(spec, apptests.has_tests(spec.context) if os.path.isabs(spec.context) else apptests.Tests(False, False)) for spec in specs]
    for spec, found in tests:   # what the tests need is had before the first one runs
        if found.any and not image_here(spec.image, runner):
            raise Unavailable(f"the image {report.printable(spec.image)} is not here: build it first (catalog.py ci build)")
    if any(found.js for _, found in tests):
        try:
            node = apptests.node_image()
        except apptests.Missing as error:
            raise Unavailable(report.printable(str(error))) from None
        reason = apptests.ensure_node(node, run, sleep or time.sleep)
        if reason:
            raise node_unavailable(reason, apptests)
    for spec, found in tests:
        name = app_of(spec.context)
        started = time.monotonic()
        if not found.any:
            results.append(Result(spec.unit, spec.image, name, spec.path, apptests.Outcome(name, "none"), 0.0))
            continue
        guide = apptests.guide_of(spec.context, folders.get(spec.unit) or None)
        outcome = apptests.run_app(spec.context, spec.image, run, node, guide, name, found)
        results.append(Result(spec.unit, spec.image, name, spec.path, outcome, time.monotonic() - started))
    return results


def findings(results: Sequence[Result]) -> List[Problem]:
    """TS01 for an app whose tests failed, TS02 for one that has none (or has none in its image)."""
    found: List[Problem] = []
    for each in results:
        if each.outcome.status == "failed":
            found.append(problem("TS01", each.path, 0, app=each.app, n=each.outcome.failed))
        elif each.outcome.status == "none":
            found.append(problem("TS02", each.path, 0, app=each.app))
    return found


def table(results: Sequence[Result]) -> str:
    """The Markdown table of the summary: each app, what ran, how it ended and how long it took."""
    lines = ["| App | Unit | Python | JavaScript | Result | Time |", "|---|---|---|---|---|---|"]
    for each in results:
        o = each.outcome
        shown = {"passed": "passed", "failed": f"{o.failed} failed", "none": "no tests"}[o.status]
        lines.append(f"| `{cell(each.app)}` | {each.unit} | {o.python if o.python != 'none' else ''} | {o.js if o.js != 'none' else ''} "
                     f"| {shown} | {seconds(each.seconds) if o.status != 'none' else ''} |")
    return "\n".join(lines) + "\n"


def lines_of(results: Sequence[Result]) -> List[str]:
    out = [f"Tests: {sum(1 for each in results if each.outcome.status == 'passed')} apps passed, "
           f"{sum(1 for each in results if each.outcome.status == 'failed')} failed, "
           f"{sum(1 for each in results if each.outcome.status == 'none')} with no tests"]
    for each in results:
        o = each.outcome
        mark = {"passed": "✓", "failed": "✗", "none": "-"}[o.status]
        said = {"passed": "passed", "failed": f"{o.failed} failed", "none": "no tests"}[o.status]
        out.append(f"{mark} {report.printable(each.app)}  {said}")
    return out


@entry
def main(argv, out, err, **seams) -> int:
    """`ci test`. Exit 1 when the tests of an app failed, 2 when this machine cannot run them (no Docker, an image that was not
    built)."""
    found = parser("test", "Run the tests of each app in its own image, offline.", units=True)
    found.add_argument("--app", default=None, help="test only this app (the folder of its build context)")
    args = parsed(found, argv)
    names = parse_units(args.units)
    if not os.path.isdir(args.path):
        raise Unavailable(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    loading = load(root, names)
    problems = refusals(loading.refused, "tested")
    specs, more, _ = specs_of(loading.units, host_platform(), root, seams.get("lab"), args.app, local=True)
    problems += more
    results: List[Result] = []
    if specs:
        runner = seams.get("runner")
        docker_ready(runner)
        results = run_all(loading.units, specs, runner, seams.get("apptests"), seams.get("sleep"))
        problems += findings(results)
    if args.format != "json":
        out.write("\n".join(lines_of(results)) + "\n")
        for each in results:
            if each.outcome.status == "failed":
                lines = tail(each.outcome.output, TAIL)
                out.write(f"The last {len(lines)} lines of the tests of {report.printable(each.app)}:\n")
                out.write(report.untrusted_text("\n".join(lines) + "\n"))
    extra = ""
    if args.ci:
        extra = "### Tests of the apps\n\n" + (table(results) if results else "No app to test.\n")
        for each in results:
            if each.outcome.status == "failed":
                extra += f"\nThe last {TAIL} lines of the tests of `{cell(each.app)}`:\n\n" + fence(tail(each.outcome.output, TAIL))
    return conclude("test", problems, args, out, "make ci", title="Test the apps", extra=extra)
