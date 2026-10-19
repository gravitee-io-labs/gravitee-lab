"""`catalog.py ci build PATH --units JSON --platform P [--ci]`: the apps of the touched units are built, on the architecture
this runner has, from what a stranger proposed. Nothing is pushed and no remote cache is read or written.

What it builds is read from the resolved Compose JSON of each unit the trust policy judges OK (`ci.load`, with the scrubbed
environment of 5.5, so a build argument is what the catalog's files say and never a value of the runner), for each service the
unit defines that has both `build:` and `image:`:

    docker buildx build --load --platform linux/<arch> --tag <image> --file <dockerfile> [--build-arg K=V] [--target T]
                        [--label K=V] <context>

as an argument list, never through a shell, with the build environment of 5.5 (the real HOME and DOCKER_CONFIG: a developer's
build needs the credentials of their own private base images; the untrusted lane of CI has none to leak).

- Only the build keys of the T18 allow-list are accepted: `context`, `dockerfile`, `args`, `target`, `labels`. Any other key is
  BD02, the service is not built, and the others still are (one run lists everything to fix). The allow-list is default-deny,
  which a deny list is not: `dockerfile_inline`, `extra_hosts`, `cache_from`, `ulimits` and `pull` pass a list of `ssh`, `secrets`
  and `network`. `additional_contexts` is for the Lab's seeder, which is not built here.
- A service built from the Lab's seeder is the Lab's own code, tested in the Lab's CI: it is left out.
- A service with a `build:` and no `image:` is built too, under a name of this stage's own (`lab-ci/<kind>-<unit>-<service>:ci`,
  `local_tag`), and tested under it: the Lab builds such a service when it runs it, and a Dockerfile that is never built in CI
  is one that breaks after the merge. (`specs_of` gives it only to the stages that ask: `publish` has no image name to push.)
- The context and the Dockerfile are read at their real place and must be inside the catalog (the trust policy says the same of
  the unit, T18; this is the second look, at the file that is about to be given to Docker); a remote context must be a git
  repository pinned to a commit.
- 10 minutes for an image (the process group is killed: BD04). A failure that is Docker Hub's or the network's (`toomanyrequests`,
  a TLS or DNS timeout, a 5xx from a registry), recognized in the lines BuildKit itself wrote and never in what the program of a
  `RUN` printed, is tried again twice, after 20 s and after 60 s, and then is BD05: it is not the author's change. Any other
  failure is BD01 with the step BuildKit named, and its last 15 lines.
- The summary lists every image with its size and how long it took.

What a stranger's program printed is shown between `::stop-commands::<token>` and `::<token>::`, so it cannot start a workflow
command.

Python 3.9 and later, standard library only."""

import os
import posixpath
import re
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ...docker import compose
from .. import explain, report, trust
from ..report import ERROR, Problem
from ..rules.images import repository
from . import (BD01_PLAIN, DOCKER_DOWN, IMAGE_NAME, Loaded, Unavailable, cell, conclude, docker_ready, entry, fence, load, megabytes,
               parse_units, parsed, parser, platform_of, problem, refusals, run_docker, seeder_anchor, seconds, tail)

# The five keys a build may carry (T18, default-deny). trust names its own list when it has one; this one is the most it may be
KEYS = ("context", "dockerfile", "args", "target", "labels")
KEY_NAMES = ("BUILD_KEYS", "BUILD_ALLOWED", "T18_KEYS", "ALLOWED_BUILD")
TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
ARG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")
LABEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}")
REMOTE = re.compile(r"(?:https://|git@|git://|ssh://|github\.com/)")
RETRIES = (20, 60)   # seconds before the first and the second new try of a build that Docker Hub or the network refused
IMAGE_TIMEOUT = 600   # seconds for one image (BD04)
TAIL = 15   # lines of a failed build that the summary shows

# What BuildKit says in its own words, as opposed to what the program of a step printed (that is `#8 1.245 text`, with the seconds)
STEP_ERROR = re.compile(r"^#(\d+) ERROR: (.*)$")
FINAL_ERROR = re.compile(r"^ERROR: (.*)$")
STEP_HEADER = re.compile(r"^#(\d+) \[(?:[^\]]*?\s)?(\d+)/(\d+)\] (.*)$")
PROCESS = re.compile(r"did not complete successfully: exit code: (\d+)")
INFRA = re.compile(
    r"toomanyrequests|too many requests|rate limit|TLS handshake timeout|tls: |i/o timeout|dial tcp|no such host|"
    r"server misbehaving|temporary failure in name resolution|connection reset by peer|unexpected EOF|context deadline exceeded|"
    r"net/http: request canceled|(?:unexpected|received unexpected HTTP) status[^\n]*\b5\d\d\b|\b5\d\d (?:Internal Server Error|"
    r"Bad Gateway|Service Unavailable|Gateway Time-?out)", re.I)
NO_BUILDX = re.compile(r"^(?:docker: unknown command: docker buildx|docker: 'buildx' is not a docker command)", re.M)

SEEDER = "the Lab's own seeder, which the Lab's CI builds"


class Spec(NamedTuple):
    """One image to build: whose it is, the command, and what to say of it."""
    unit: str
    service: str
    image: str
    command: List[str]
    path: str   # the compose file of the unit, relative to the catalog (where a finding is placed)
    context: str


class Row(NamedTuple):
    """What became of an image: `status` is built, failed, refused or skipped."""
    unit: str
    service: str
    image: str
    status: str
    seconds: float = 0.0
    size: int = 0
    note: str = ""


def allowed() -> frozenset:
    """The build keys that are accepted: the T18 list of the trust policy when it names one, and never more than the five of the
    standard. A list that cannot be read is the five."""
    named = next((getattr(trust, name) for name in KEY_NAMES if getattr(trust, name, None)), None)
    try:
        listed = frozenset(str(each) for each in named) if named else frozenset(KEYS)
    except TypeError:
        listed = frozenset(KEYS)
    return listed & frozenset(KEYS)


def t18(path: str, service: str, variant: int = 0) -> Problem:
    """The trust policy's own T18 finding for a build that leaves the catalog or the pin: its reason and its fix, in the Lab's words."""
    reason = trust.RULES["T18"]["lines"][variant]
    fix = (getattr(trust, "FIXES", None) or explain.T_FIXES).get("T18", "")
    return Problem("T18", ERROR, path, 0, f"{service}: {reason}. Fix: {fix}".rstrip())


def pinned(context: str) -> bool:
    """Whether a remote build context is a git repository at a commit: a git address, then # and 40 hexadecimal characters (T18)."""
    address, _, ref = context.partition("#")
    git = address.endswith(".git") or address.startswith(("git@", "git://", "ssh://", "github.com/"))
    return bool(git and trust.PINNED.fullmatch(ref))


def inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def text_of(value) -> Optional[str]:
    """A build argument or a label as the text Docker is given, or None for what is not one (a null is an argument that is not
    set; a number is its digits)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return value if isinstance(value, str) and "\x00" not in value else None   # (a NUL cannot be an argument of a program)


def command_of(image: str, platform: str, dockerfile: str, context: str, args: Dict[str, str], target: str,
               labels: Dict[str, str]) -> List[str]:
    """The `docker buildx build` of 5.6, exactly: the arguments and the labels in the order of their names."""
    cmd = ["docker", "buildx", "build", "--load", "--platform", platform, "--tag", image, "--file", dockerfile]
    for key in sorted(args):
        cmd += ["--build-arg", f"{key}={args[key]}"]
    if target:
        cmd += ["--target", target]
    for key in sorted(labels):
        cmd += ["--label", f"{key}={labels[key]}"]
    return cmd + [context]


LOCAL_TAG = re.compile(r"[^a-z0-9._-]+")


def local_tag(unit: str, service: str) -> str:
    """The name a service that builds and has no image is built under: `lab-ci/` and the unit and the service, lowercase, with
    anything Docker does not allow in a name written as a dash (`playground:Bank` and `Web_1` give
    `lab-ci/playground-bakery-web_1:ci`)."""
    return "lab-ci/" + LOCAL_TAG.sub("-", f"{unit}-{service}".lower()).strip("-.")[:100] + ":ci"


def one(unit: Loaded, name: str, service: dict, platform: str, root: str, seeders: Tuple[Tuple[str, ...], str],
        keys: frozenset, known: Dict[str, str], local: bool = False) -> Tuple[Optional[Spec], List[Problem], Optional[Row]]:
    """(the spec, the findings, the row of what was left out) of a service of a unit that has a `build`. `local`: a service with no
    image is built under `local_tag`."""
    where = unit.file
    build = service.get("build")
    image = service.get("image")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        return None, [problem("BD02", where, 0, service=name, key="build")], None
    context, remote = build.get("context") or "", False
    seeder_images, seeder_dir = seeders
    if not isinstance(context, str) or "\x00" in context or "\x00" in str(build.get("dockerfile") or ""):
        return None, [t18(where, name, 0)], Row(unit.key, name, str(image or ""), "refused", note="its context or Dockerfile is not a path")
    nameless = image is None or image == ""
    if os.path.isabs(context) and os.path.realpath(context) == seeder_dir \
            and ((isinstance(image, str) and repository(image) in seeder_images) or (local and nameless)):
        return None, [], Row(unit.key, name, image if isinstance(image, str) else "", "skipped", note=SEEDER)   # the Lab's own code
    found: List[Problem] = [problem("BD02", where, 0, service=name, key=key) for key in sorted(build) if key not in keys]
    if found:
        return None, found, Row(unit.key, name, str(image or ""), "refused", note="a build key that is not accepted")
    if local and nameless:
        image = local_tag(unit.key, name)
    elif not isinstance(image, str) or not IMAGE_NAME.fullmatch(image):
        return None, [], Row(unit.key, name, "", "skipped", note="no image name to give the build")
    if REMOTE.match(context):
        remote = True
        if not pinned(context):   # a git repository at a commit, or not at all (T18)
            return None, [t18(where, name, 1)], Row(unit.key, name, image, "refused", note="its context is not pinned")
    elif os.path.isabs(context):
        real = os.path.realpath(context)
        if not inside(real, root) or not os.path.isdir(real):
            return None, [t18(where, name, 0)], Row(unit.key, name, image, "refused", note="its context is outside the catalog")
        context = real
    else:
        return None, [t18(where, name, 0)], Row(unit.key, name, image, "refused", note="its context is outside the catalog")
    dockerfile = str(build.get("dockerfile") or "Dockerfile")
    if remote:
        if os.path.isabs(dockerfile) or ".." in dockerfile.split("/"):
            return None, [t18(where, name, 0)], Row(unit.key, name, image, "refused", note="its Dockerfile leaves the context")
    else:
        dockerfile = os.path.realpath(os.path.join(context, dockerfile))
        if not inside(dockerfile, root) or not os.path.isfile(dockerfile):
            return None, [t18(where, name, 0)], Row(unit.key, name, image, "refused", note="its Dockerfile is outside the catalog")
    args, labels = {}, {}
    for kind, given, into, pattern in (("args", build.get("args"), args, ARG_NAME), ("labels", build.get("labels"), labels, LABEL_NAME)):
        if given is None:
            continue
        if not isinstance(given, dict):
            return None, [problem("BD02", where, 0, service=name, key=kind)], Row(unit.key, name, image, "refused")
        for key, value in given.items():
            shown = text_of(value)
            if not isinstance(key, str) or not pattern.fullmatch(key) or (shown is None and value is not None):
                return None, [problem("BD02", where, 0, service=name, key=kind)], Row(unit.key, name, image, "refused")
            if shown is not None:
                into[key] = shown
    target = build.get("target") or ""
    if target and (not isinstance(target, str) or not TARGET.fullmatch(target)):
        return None, [problem("BD02", where, 0, service=name, key="target")], Row(unit.key, name, image, "refused")
    if image in known:
        return None, [], Row(unit.key, name, image, "skipped", note="it is built once, for another service")
    known[image] = context
    return Spec(unit.key, name, image, command_of(image, platform, dockerfile, context, args, target, labels), where, context), [], None


def specs_of(units: Sequence[Loaded], platform: str, root: str, lab=None, only: Optional[str] = None,
             local: bool = False) -> Tuple[List[Spec], List[Problem], List[Row]]:
    """(the images to build, the findings of those that cannot be, the rows of those left out) for these units, on one platform.
    Pure: it reads the resolved JSON and the disk to find the context, and runs nothing. `only` is the name of an app (the
    folder of its context): the others are not built. `local`: a service that builds and has no image is built under a name
    of the stage's own (`ci build` and `ci test` say so; `publish` has no image to push for it)."""
    keys, seeders, real = allowed(), seeder_anchor(lab), os.path.realpath(root)
    found: List[Spec] = []
    problems: List[Problem] = []
    rows: List[Row] = []
    known: Dict[str, str] = {}
    for unit in units:
        services = unit.config.get("services") if isinstance(unit.config, dict) else None
        for name in unit.own:
            service = (services or {}).get(name)
            if not isinstance(service, dict) or not service.get("build"):
                continue
            spec, more, row = one(unit, name, service, platform, real, seeders, keys, known, local)
            problems += more
            if row is not None:
                rows.append(row)
            if spec is not None and (only is None or app_of(spec.context) == only):
                found.append(spec)
            elif spec is not None:
                rows.append(Row(spec.unit, spec.service, spec.image, "skipped", note=f"not the app {only}"))
    return found, problems, rows


def app_of(context: str) -> str:
    """The name of an app: the folder of its build context."""
    return posixpath.basename(context.rstrip("/").split("#")[0]) if context else ""


# ---------------------------------------------------------------------------------------------------------------------
# What a failed build said
# ---------------------------------------------------------------------------------------------------------------------

class Failure(NamedTuple):
    """How a build ended badly: `kind` is process (a step of the Dockerfile failed), infra (Docker Hub or the network),
    timeout, missing (no docker or no buildx), unreachable (no daemon) or other; with the step it failed at when BuildKit named
    one: (number, of, instruction), and the exit code."""
    kind: str
    code: int
    step: Optional[Tuple[int, int, str]] = None
    lines: Tuple[str, ...] = ()


def classify(code: int, out: str, err: str) -> Failure:
    """What a build that did not end well is. Only the lines BuildKit wrote itself (`ERROR:`) are read for the cause: what the
    program of a step printed is a stranger's text, and `toomanyrequests` printed by a `RUN` must not make a failing build
    look like Docker Hub's."""
    text = (err or "") + "\n" + (out or "")
    last = tail(text, TAIL)
    if code == 124:
        return Failure("timeout", code, None, tuple(last))
    if code in (127, 130):
        return Failure("missing" if code == 127 else "stopped", code, None, tuple(last))
    if NO_BUILDX.search(text):
        return Failure("missing", code, None, tuple(last))
    if compose.unreachable(text):
        return Failure("unreachable", code, None, tuple(last))
    own: List[str] = []
    step_number = None
    exit_code = None
    for line in text.splitlines():
        found = STEP_ERROR.match(line)
        if found:
            own.append(found.group(2))
            step_number = step_number or int(found.group(1))
            continue
        found = FINAL_ERROR.match(line)
        if found:
            own.append(found.group(1))
    for each in own:
        ran = PROCESS.search(each)
        if ran:
            exit_code = int(ran.group(1))
            break
    step = None
    if step_number is not None:
        for line in text.splitlines():
            found = STEP_HEADER.match(line)
            if found and int(found.group(1)) == step_number:
                step = (int(found.group(2)), int(found.group(3)), " ".join(found.group(4).split())[:100])
                break
    if exit_code is not None:
        return Failure("process", exit_code, step, tuple(last))
    if any(INFRA.search(each) for each in own):
        return Failure("infra", code, None, tuple(last))
    return Failure("other", code, step, tuple(last))


def finding(spec: Spec, failure: Failure) -> Problem:
    """BD01, BD04 or BD05 for a build that failed for good."""
    if failure.kind == "timeout":
        return problem("BD04", spec.path, 0, image=spec.image)
    if failure.kind == "infra":
        return problem("BD05", spec.path, 0)
    if failure.step is not None:
        number, of, instruction = failure.step
        return problem("BD01", spec.path, 0, image=spec.image, n=number, m=of, instruction=instruction, code=failure.code)
    return Problem("BD01", ERROR, spec.path, 0, BD01_PLAIN.replace("{image}", spec.image).replace("{code}", str(failure.code)))


def size_of(image: str, runner: Optional[Callable]) -> int:
    """The size of a built image in bytes, or 0 when Docker does not say."""
    code, out, _ = run_docker(["docker", "image", "inspect", "--format", "{{.Size}}", image], timeout=60, runner=runner)
    try:
        return int(out.strip()) if code == 0 else 0
    except ValueError:
        return 0


def build_all(specs: Sequence[Spec], runner: Optional[Callable] = None, sleep: Callable = time.sleep, cwd=None,
              clock: Callable = time.monotonic) -> Tuple[List[Row], List[Problem], Dict[str, Tuple[str, ...]]]:
    """Builds each image once or, for what Docker Hub or the network refuses, up to three times. (the rows, the findings, the
    last lines of each build that failed). A machine that cannot build at all (no docker, no buildx, no daemon) is an
    Unavailable: it says nothing of the catalog."""
    rows: List[Row] = []
    problems: List[Problem] = []
    logs: Dict[str, Tuple[str, ...]] = {}
    for spec in specs:
        started = clock()
        failure: Optional[Failure] = None
        for attempt in range(len(RETRIES) + 1):
            code, out, err = run_docker(spec.command, timeout=IMAGE_TIMEOUT, cwd=cwd, runner=runner)
            if code == 0:
                failure = None
                break
            failure = classify(code, out, err)
            if failure.kind in ("missing", "unreachable", "stopped"):
                raise Unavailable({"missing": "Docker with buildx is not installed or not on the PATH",
                                   "unreachable": f"{DOCKER_DOWN}: start Docker Desktop (or the docker service), then run it again",
                                   "stopped": "the build was stopped"}[failure.kind])
            if failure.kind != "infra" or attempt == len(RETRIES):
                break
            sleep(RETRIES[attempt])
        spent = clock() - started
        if failure is None:
            rows.append(Row(spec.unit, spec.service, spec.image, "built", spent, size_of(spec.image, runner)))
        else:
            rows.append(Row(spec.unit, spec.service, spec.image, "failed", spent, 0, failure.kind))
            problems.append(finding(spec, failure))
            logs[spec.image] = failure.lines
    return rows, problems, logs


def table(rows: Sequence[Row]) -> str:
    """The Markdown table of the summary: every image with its size and how long it took."""
    lines = ["| Image | Unit | Result | Size | Time |", "|---|---|---|---|---|"]
    for row in rows:
        result = {"built": "built", "failed": "failed", "refused": "not accepted", "skipped": "left out"}[row.status]
        if row.status in ("skipped", "refused") and row.note:
            result += f": {row.note}"
        size = megabytes(row.size) if row.status == "built" and row.size else ""
        spent = seconds(row.seconds) if row.status in ("built", "failed") else ""
        lines.append(f"| `{row.image}` | {row.unit} | {result} | {size} | {spent} |" if row.image else
                     f"| | {row.unit} {cell(row.service)} | {result} | | |")
    return "\n".join(lines) + "\n"


def lines_of(rows: Sequence[Row], platform: str) -> List[str]:
    """What a person reads while it runs: one line for each image."""
    out = [f"Build for {platform}: {sum(1 for each in rows if each.status == 'built')} built, "
           f"{sum(1 for each in rows if each.status == 'failed')} failed, "
           f"{sum(1 for each in rows if each.status in ('skipped', 'refused'))} left out"]
    for row in rows:
        shown = report.printable(row.image or f"{row.unit} {row.service}")
        if row.status == "built":
            out.append(f"✓ {shown}  {megabytes(row.size) if row.size else ''}  {seconds(row.seconds)}".rstrip())
        elif row.status == "failed":
            out.append(f"✗ {shown}  after {seconds(row.seconds)}")
        else:
            out.append(f"- {shown}  {report.printable(row.note)}")
    return out


@entry
def main(argv, out, err, **seams) -> int:
    """`ci build`. Exit 1 when an image did not build (or a key is not accepted), 2 when this machine cannot build or the units
    cannot be read here."""
    found = parser("build", "Build the apps of the touched units on one architecture.", units=True)
    found.add_argument("--platform", required=True, help="linux/amd64 or linux/arm64")
    found.add_argument("--app", default=None, help="build only this app (the folder of its build context)")
    args = parsed(found, argv)
    platform = platform_of(args.platform)
    names = parse_units(args.units)
    if not os.path.isdir(args.path):
        raise Unavailable(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    loading = load(root, names)
    problems = refusals(loading.refused, "built")
    specs, more, skipped_rows = specs_of(loading.units, platform, root, seams.get("lab"), args.app, local=True)
    problems += more
    rows: List[Row] = list(skipped_rows)
    logs: Dict[str, Tuple[str, ...]] = {}
    if specs:
        runner = seams.get("runner")
        docker_ready(runner)
        built, failed, logs = build_all(specs, runner, seams.get("sleep") or time.sleep, root)
        rows = built + rows
        problems += failed
    if args.format != "json":
        out.write("\n".join(lines_of(rows, platform)) + "\n")
        for image, lines in logs.items():
            out.write(f"The last {len(lines)} lines of the build of {report.printable(image)}:\n")
            out.write(report.untrusted_text("\n".join(lines) + "\n"))
    extra = ""
    if args.ci:
        extra = f"### Images built for {platform}\n\n" + (table(rows) if rows else "No image to build.\n")
        for image, lines in logs.items():
            extra += f"\nThe last {len(lines)} lines of the build of `{cell(image)}`:\n\n" + fence(lines)
    return conclude("build", problems, args, out, "make ci", title="Build the apps", extra=extra)
