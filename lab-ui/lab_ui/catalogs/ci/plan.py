"""`catalog.py ci plan PATH --event E --base B --head H [--github-output]`: which units a change touches, and what the
expensive jobs of `ci.yml` have to do about it. It is the first thing a pull request's workflow asks of the Lab and it runs on
a tree nobody has judged yet, so it is **pure**: it reads the names of the files, the text of the `include:` lines of the
compose files and `.github/standard.json`, and it never runs Compose or Docker. A repository whose file names hold a newline
and `::error::`, a unit that includes a remote file, a compose file that is not text: none of them costs a call to anything
or changes what the step writes.

It writes four outputs, each one line: a flag it computed or `json.dumps(..., ensure_ascii=True)` of values that match
`[a-z0-9-]`, never a name from the tree.

    units    ["playground:bank", "addon:mock-bank"]: the units the change touches, and every unit that includes one
    seed     true: a playground of `units` has a seed/ folder, or the catalog has a tests/ of its own and the change touches
             it or any unit
    compile  true: a unit of `units` has a Dockerfile in its tree and the change is more than a README (guide.md counts: the
             tests of an app read it)
    matrix   {"include": [{"arch": "amd64", "platform": "linux/amd64", "runner": "ubuntu-24.04"}, ...]} from `platforms` of
             .github/standard.json; anything but linux/amd64 and linux/arm64 is SD04

The scope is **everything** on push, schedule and workflow_dispatch, on a first push (the base is all zeros or no commit), when
git cannot say what changed, and when the change touches catalog.yml, `.github/`, `.catalog/` or the Makefile (they change
what every unit is judged by). On a pull request that changes more than 20 apps the plan fails with BD06 and writes nothing:
a step that wrote nothing makes the result job red.

A unit whose `include:` lines the text screen cannot read (an include of another catalog, a spelling that YAML may read
another way) cannot be followed: it is taken to include every unit, so a change to any unit touches it. That costs a build,
never a miss.

Python 3.9 and later, standard library only."""

import json
import os
import posixpath
import re
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from .. import gitx, report, tree as trees, trust
from ..report import ERROR, Problem
from . import PLATFORMS, Unavailable, Usage, entry, json_line, parsed, parser, problem, write_outputs, write_summary

NAME = re.compile(r"[a-z0-9-]+")
EVENT = re.compile(r"[a-z_]{1,40}")
ZEROS = re.compile(r"0{40}")
LIMIT = 20   # apps a pull request may change (BD06)
# What the whole catalog is judged by: a change to one of them is a change to every unit
CONTROL_FILES = ("catalog.yml", "Makefile")
CONTROL_FOLDERS = (".github/", ".catalog/")
# The events whose scope is everything: nothing says what changed, or the run is the maintainers' own
EVERYTHING = ("push", "schedule", "workflow_dispatch")
MAX_FILE = 1024 * 1024
STANDARD = ".github/standard.json"
STANDARD_LIMIT = 4096


class Unit(NamedTuple):
    kind: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def file(self) -> str:
        return f"setups/{self.name}.yml" if self.kind == "setup" else f"{self.kind}s/{self.name}/compose.yml"


class Plan(NamedTuple):
    """What the plan decided. `units` are sorted keys; `scope` says why it is everything, or "change"."""
    units: List[str]
    seed: bool
    compile: bool
    matrix: dict
    scope: str
    apps: int
    problems: List[Problem]
    changed: int = 0

    def outputs(self) -> Dict[str, str]:
        """The four lines of `$GITHUB_OUTPUT`."""
        return {"units": json_line(self.units), "seed": "true" if self.seed else "false",
                "compile": "true" if self.compile else "false", "matrix": json_line(self.matrix)}


def unit_of(path: str) -> Optional[Unit]:
    """The unit a path of the catalog belongs to: a file under addons/<name>/ or playgrounds/<name>/, or setups/<name>.yml. By
    the segments of the path, so that no name with a line break or a colon in it is ever one."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] in ("addons", "playgrounds") and NAME.fullmatch(parts[1]):
        return Unit(parts[0][:-1], parts[1])
    if len(parts) == 2 and parts[0] == "setups" and parts[1].endswith(".yml") and NAME.fullmatch(parts[1][:-4]):
        return Unit("setup", parts[1][:-4])
    return None


def is_dockerfile(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def is_readme(path: str) -> bool:
    return path.rsplit("/", 1)[-1].lower() in ("readme", "readme.md")


class Listing(NamedTuple):
    """What the files of the catalog say, by name alone."""
    units: Dict[Unit, str]                # the units that exist, each with its compose file
    dockerfiles: Dict[Unit, Set[str]]     # the folders of a unit's tree that hold a Dockerfile (the apps)
    seeds: Set[Unit]                      # the playgrounds that have a seed/ folder
    tests: bool                           # the catalog has a tests/ of its own


def listing(entries) -> Listing:
    """The units of a list of tree entries (`Entry(mode, path, size)`): a unit exists when its compose file is a regular file."""
    units: Dict[Unit, str] = {}
    folders: Dict[Unit, Set[str]] = {}
    seeds: Set[Unit] = set()
    tests = False
    for each in entries:
        regular = each.mode in trees.REGULAR
        found = unit_of(each.path)
        if each.path.startswith("tests/") and regular:
            tests = True
        if found is None:
            continue
        if regular and each.path == found.file:
            units[found] = each.path
        if regular and is_dockerfile(each.path):
            folders.setdefault(found, set()).add(posixpath.dirname(each.path))
        if regular and found.kind == "playground" and each.path.startswith(f"playgrounds/{found.name}/seed/"):
            seeds.add(found)
    return Listing(units, {unit: folders.get(unit, set()) for unit in units}, {unit for unit in seeds if unit in units}, tests)


def included(tree, unit: Unit, file: str) -> Optional[Set[Unit]]:
    """The units of this catalog that the compose file of `unit` includes, by the `include:` lines of its text alone; None
    when the text cannot be read as the screen reads it (the unit is then taken to include every unit)."""
    data = tree.read(file, MAX_FILE + 1)
    if data is None or len(data) > MAX_FILE:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    targets = trust.include_targets(text, file)
    if targets is None:
        return None
    found: Set[Unit] = set()
    for target in targets:
        mine = unit_of(target)
        if mine is not None and mine != unit:
            found.add(mine)
    return found


def closure(touched: Set[Unit], needs: Dict[Unit, Optional[Set[Unit]]]) -> Set[Unit]:
    """`touched` and every unit that includes one of them, however far up. A unit whose includes are unknown includes all."""
    out = set(touched)
    grew = bool(out)
    while grew:
        grew = False
        for unit, inner in needs.items():
            if unit not in out and (inner is None or inner & out):
                out.add(unit)
                grew = True
    return out


def matrix_of(tree, platforms: Optional[Sequence[str]] = None) -> Tuple[Optional[dict], List[Problem]]:
    """The matrix of `.github/standard.json platforms` ({"include": [...]}, the canonical order), or the SD04 that says what is
    wrong with it. `platforms` takes the place of the file (a run on this machine, which has no file to read)."""
    def bad(why: str):
        return None, [problem("SD04", STANDARD, 0, problem=why)]
    wanted = platforms
    if wanted is None:
        data = tree.read(STANDARD, STANDARD_LIMIT + 1)
        if data is None:
            return bad("the file is missing, so the architectures to build for are not known")
        if len(data) > STANDARD_LIMIT:
            return bad("the file is over 4 KB")
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return bad("the file is not JSON")
        wanted = document.get("platforms") if isinstance(document, dict) else None
    if not isinstance(wanted, (list, tuple)) or not wanted or not all(isinstance(each, str) for each in wanted) \
            or any(each not in PLATFORMS for each in wanted) or len(set(wanted)) != len(wanted):
        return bad("platforms must be a non-empty list made of linux/amd64 and linux/arm64")
    return {"include": [dict(PLATFORMS[name]) for name in PLATFORMS if name in wanted]}, []


def changed_files(root, base: str, head: Optional[str] = None) -> Optional[List[str]]:
    """The paths that differ between `base` and `head` (the work tree of `root` when there is no head, new files included),
    from `git diff --name-only -z` with the renames split in two, or None when git cannot say: no repository, a name that is no
    commit, a failure. The names are exactly as git has them, any character included."""
    try:
        first = trees.resolve(root, base)
        last = trees.resolve(root, head) if head else None
        if first is None or (head and last is None):
            return None
        options = ["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv"]
        done = gitx.run([*options, first, *([last] if last else []), "--"], cwd=os.path.realpath(str(root)), env=trees.QUIET,
                        timeout=120, limit=gitx.OUTPUT_LIMIT)
        if done.code != 0:
            return None
        names = [os.fsdecode(name) for name in done.out.split(b"\0") if name]
        if not head:
            more = gitx.run(["ls-files", "-z", "--others", "--exclude-standard"], cwd=os.path.realpath(str(root)),
                            env=trees.QUIET, timeout=120, limit=gitx.OUTPUT_LIMIT)
            if more.code == 0:
                names += [os.fsdecode(name) for name in more.out.split(b"\0") if name]
        return list(dict.fromkeys(names))
    except (gitx.CatalogError, gitx.OutputLimit, OSError, ValueError):
        return None


def widest(event: str, base: str, changed: Optional[List[str]], reason: str = "") -> str:
    """Why the scope is everything, or "": the event, a first push, a base git cannot use, a change to what judges every unit."""
    if event in EVERYTHING:
        return event
    if not base or ZEROS.fullmatch(base):
        return reason or "first push"
    if changed is None:
        return reason or "git could not say what changed"
    for path in changed:
        if path in CONTROL_FILES or path.startswith(CONTROL_FOLDERS):
            return "a change to a file every unit is judged by"
    return ""


def compute(tree, event: str, changed: Optional[List[str]], base: str = "", platforms: Optional[Sequence[str]] = None,
            reason: str = "") -> Plan:
    """The plan of a change. `tree` lists the catalog as it will be (the head), `changed` the paths the change touches (None:
    unknown), `base` the commit it is measured from."""
    found = listing(tree.entries())
    scope = widest(event, base, changed, reason)
    grid, problems = matrix_of(tree, platforms)
    existing = set(found.units)
    if scope:
        touched = set(existing)
    else:
        moved = {unit_of(path) for path in (changed or ())} - {None}
        needs = {unit: included(tree, unit, file) for unit, file in found.units.items()}
        touched = closure(moved, needs) & existing
    ordered = sorted(touched, key=lambda each: each.key)
    apps = sum(len(found.dockerfiles.get(unit, ())) for unit in ordered)
    if event == "pull_request" and not scope and apps > LIMIT:
        problems.append(problem("BD06", "", 0, n=apps))
    paths_changed = changed or []
    whole = bool(scope)
    # (a change to a file of a unit that is no README: what is changed outside the units builds nothing)
    more_than_readme = whole or any(not is_readme(path) for path in paths_changed if unit_of(path) is not None)
    seeds = any(unit in found.seeds for unit in ordered)
    touches_tests = whole or any(path.startswith("tests/") for path in paths_changed)
    own_tests = found.tests and (touches_tests or bool(ordered))
    return Plan([unit.key for unit in ordered], bool(seeds or own_tests),
                bool(more_than_readme and any(found.dockerfiles.get(unit) for unit in ordered)), grid or {"include": []},
                scope or "change", apps, problems, len(paths_changed))


def lines_of(plan: Plan) -> List[str]:
    """What a person reads of the plan, every word of it a constant or a name that matched `[a-z0-9-]`."""
    scope = "everything" + f" ({plan.scope})" if plan.scope != "change" else "the change and what includes it"
    shown = ", ".join(plan.units) if plan.units else "none"
    arches = ", ".join(each["arch"] for each in plan.matrix.get("include", ())) or "none"
    return [f"Scope: {scope}", f"Units: {shown}", f"Seed: {'yes' if plan.seed else 'no'}   Build: "
            f"{'yes' if plan.compile else 'no'}   Architectures: {arches}"]


@entry
def main(argv, out, err, **seams) -> int:
    """`ci plan`. Exit 1 for BD06 or SD04 (and nothing is written to the outputs), 2 for a usage error."""
    found = parser("plan", "What a change touches: the units, whether to seed, whether to build, on which architectures.",
                   ci=True)
    found.add_argument("--event", default="pull_request", help="the event: push, pull_request, workflow_dispatch, schedule")
    found.add_argument("--base", default="", help="the commit the change is measured from (default: origin/main)")
    found.add_argument("--head", default="", help="the commit the change ends at (default: the files as they are)")
    found.add_argument("--github-output", action="store_true", help="write the four outputs to $GITHUB_OUTPUT")
    args = parsed(found, argv)
    if not EVENT.fullmatch(args.event):
        raise Usage("--event is the name of a GitHub event, for example pull_request")
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        raise Usage(f"{args.path} is not a folder")
    if args.github_output and not os.environ.get("GITHUB_OUTPUT"):
        raise Unavailable("--github-output needs $GITHUB_OUTPUT, which only a workflow step has")
    tree = trees.FolderTree(root)
    base, reason = args.base, ""
    if args.event not in EVERYTHING:
        if not base and not args.head and trees.is_repository(root):
            base = trees.default_base(root) or ""
        changed = None if (not base or ZEROS.fullmatch(base)) else changed_files(root, base, args.head or None)
        if changed is None and base and not ZEROS.fullmatch(base):
            reason = "git could not say what changed"
    else:
        changed = None
    plan = compute(tree, args.event, changed, base, reason=reason)
    failed = [each for each in plan.problems if each.severity == ERROR]
    if args.format == "json":
        document = {"units": plan.units, "seed": plan.seed, "compile": plan.compile, "matrix": plan.matrix,
                    "scope": plan.scope, "problems": [each._asdict() for each in plan.problems]}
        out.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    else:
        out.write("\n".join(lines_of(plan)) + "\n")
        if plan.problems:
            out.write(report.render(plan.problems, args.format))
    if args.ci:
        write_summary("### What the change touches\n\n" + "\n".join(f"- {line}" for line in lines_of(plan)) + "\n"
                      + ("\n" + report.summary(plan.problems, "make ci") if plan.problems else ""))
    if failed:
        return 1
    if args.github_output:
        write_outputs(plan.outputs())
    return 0
