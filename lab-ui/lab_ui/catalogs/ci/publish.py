"""`catalog.py ci publish-plan` and `catalog.py ci publish`: the trusted lane of a catalog. `publish.yml` is the only workflow
that writes packages and releases, it runs on the main branch and on a release tag only, and what a pull request writes never
reaches it. Level 3 of the standard, rendered when `catalog.yml` sets `images`.

    publish-plan PATH --ref R --sha S --repository OWNER/REPO --private BOOL [--github-output]
        The gates, and what to build. It writes three outputs, each one line: `images` (a JSON list of image names, no tag), `count`
        and `attest`. It runs with `GH_TOKEN` alone (`checks: read`, `actions: read`) and builds nothing.
    publish PATH --images JSON --ref R --sha S --records FILE [--github-output] [--dry-run]
        Builds and pushes the images the plan named, and writes the records. It re-reads the tree: what the plan gives it is a list
        of names, never a path, a command or a tag.

**A branch** (`refs/heads/main`, a push or a manual run). The plan waits for the check run `Catalog CI result` of the commit, made
by GitHub Actions (every 20 seconds, 30 minutes at most), and plans only if it ended `success` (PB03): that is the proof, where no
rule forces the review, that the commit on `main` is the one that was checked. PB08 reads the message and the identities of the
head commit for a tool credit (the list of CR01): `main` is never rewritten, so it is a detective control, and the only place that
sees a trailer GitHub appended at the merge. The images to build are those whose build files changed since the **last successful
run** of this workflow on `main` (`github.event.before` is not used: GitHub drops a queued run when a newer one arrives, and a
dropped run loses nothing because the next one covers its commits); no such run, one whose commit is not an ancestor of this one (a
force push), a manual run or a change to `catalog.yml` or `.github/standard.json` (the labels and the platforms) means every
image. A run whose commit a later successful run already contains (a re-run of an old one) plans nothing: `latest` is the tip of
`main`, and publishing an old commit again would move it back. Tags: `:sha-<7>` and `:latest`.

**A tag** (`vX.Y.Z`). The gates: PB01 the version in `catalog.yml` is the tag, PB02 the commit is on `main`, PB03 its check run
succeeded, PB05 `images` is `ghcr.io/<repository, lower case>`. A tag that fails a gate publishes nothing, not even the release.
Every image is planned. Tags: `:X.Y.Z` (immutable) and `:sha-<7>`; `latest` does not move. If `:X.Y.Z` exists already the image is not
built again: when it was published from this commit its digest is the record (a re-run), from another it is PB04.

**Never** a tag from a pull request or from any ref but those two. An image outside the `images` prefix is not published (IM02 says
so in `check`); the Lab's seeder is the Lab's image and is not published here.

**Naming.** `<images>/<last part of the Compose image>`, lower case. The labels are `org.opencontainers.image.source`, `.revision`
and `.version`, and `.licenses` only for a build from the catalog's own files, never one built from another project's source
(PB06). No `created`: the records and `images.json` are the same bytes for the same inputs. The platforms are the `platforms` of
`.github/standard.json` (amd64 only while a repository is private: what is published was proven where the pull requests run), with
BuildKit's provenance and SBOM. Build arguments are recorded in the provenance, and come from the resolved configuration read with the
scrubbed environment of 5.5, so no secret of the runner is ever one.

**Secrets.** No credential is ever on a command line or in a file written here: the registry login is `docker/login-action`'s, made
before this runs, and the stage only runs `docker` in the build environment. `GH_TOKEN` is read by `gh` alone.

    PB01 PB02 PB03 PB05 PB08   the gates of the plan        PB04 PB06   in the build

Exit 0 it worked, 1 a gate or the build said no, 2 the stage cannot run (no `gh`, a token that cannot read the checks, no Docker).

Python 3.9 and later, standard library only."""

import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from ... import paths
from .. import gitx, report, settings, tree as trees
from ..report import ERROR, Problem
from ..rules import catalog_meta, commits, images as image_rules, say
from . import (Unavailable, Usage, build, conclude, docker_ready, entry, json_line, load, parsed, parser, plan as planning,
               refusals, run_docker, tail, write_outputs, write_summary)
from .smoke import needs_settings

MESSAGES = {
    "PB01": (ERROR, "The version in catalog.yml ({a}) is not the tag ({b}). Fix: bump the version, then tag again."),
    "PB02": (ERROR, "The tag {tag} is not on main."),
    "PB03": (ERROR, "Catalog CI result has not passed on {sha7}. Fix: wait for it, or fix what failed."),
    "PB04": (ERROR, "{image}:{version} was published from another commit. Fix: bump the version."),
    "PB05": (ERROR, "catalog.yml images is {x}; this repository publishes to {y}."),
    "PB06": (ERROR, "{image} carries a license label but is built from another project's source."),
    "PB08": (ERROR, "The commit {sha7} carries a tool credit in its message or its author. main is never rewritten. Fix: find how it "
                    "got there (the merge settings, V18) and write the next title without it."),
}
CHECK = settings.REQUIRED_CHECK      # Catalog CI result
CHECK_APP = settings.ACTIONS_APP_ID  # the integration id of GitHub Actions: a check run of another app does not count
WORKFLOW = "publish.yml"
MAIN = "refs/heads/main"
POLL = 20            # seconds between two readings of the check run
LIMIT = 1800         # seconds the plan waits for it
RUNS = 30            # successful runs read, newest first
SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
VERSION = re.compile(r"(\d{1,6})\.(\d{1,6})\.(\d{1,6})")
TAG = re.compile(r"refs/tags/(v[^\s]{1,100})")
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")
HOST = "ghcr.io"
SOURCE = "org.opencontainers.image.source"
REVISION = "org.opencontainers.image.revision"
VERSION_LABEL = "org.opencontainers.image.version"
LICENSE_LABEL = "org.opencontainers.image.licenses"
BUILD_TIMEOUT = build.IMAGE_TIMEOUT
MAX_IMAGES = 500
CONTROL = ("catalog.yml", ".github/standard.json")   # what changes the labels and the platforms of every image


def finding(which: str, path: str = "", **fields) -> Problem:
    """The Problem of a code of this stage (`which`: PB01...), its message the words of 6.11 with the fields put in."""
    severity, message = MESSAGES[which]
    return Problem(which, severity, path, 0, say(message, **fields))


# ---------------------------------------------------------------------------------------------------------------------
# The ref, the commit, git
# ---------------------------------------------------------------------------------------------------------------------

class Ref(NamedTuple):
    """What the run is for: `branch` (main) or `tag`. `version` is the text after the `v` of a tag, as it was written."""
    kind: str
    ref: str
    version: str = ""


def parse_ref(text: str) -> Ref:
    """`refs/heads/main` or `refs/tags/v...`. Anything else, a pull request's ref and any other branch included, is refused: this
    lane publishes from the main branch and from a release tag only."""
    if text == MAIN:
        return Ref("branch", text)
    found = TAG.fullmatch(text) if isinstance(text, str) else None
    if found:
        return Ref("tag", text, found.group(1)[1:])
    raise Usage("publish runs from refs/heads/main and from a release tag (refs/tags/vX.Y.Z) only, never from a pull request or "
                f"another branch: {report.printable(str(text))[:60]} is neither")


def check_sha(text: str) -> str:
    if not isinstance(text, str) or not SHA.fullmatch(text):
        raise Usage("--sha is the commit: 40 hexadecimal characters")
    return text


def check_repository(text: str) -> str:
    if not isinstance(text, str) or not REPOSITORY.fullmatch(text) or ".." in text:
        raise Usage("--repository is OWNER/REPO")
    return text


def git(root: str, *args: str, timeout: float = 120) -> gitx.Result:
    """git in the work tree, through `gitx` (no hook, no file system monitor, no variable of the shell)."""
    return gitx.run(list(args), cwd=os.path.realpath(root), env=trees.QUIET, timeout=timeout)


def is_ancestor(root: str, ancestor: str, descendant: str) -> Optional[bool]:
    """Whether `ancestor` is `descendant` or leads to it; None when git cannot say (a commit it does not have)."""
    try:
        done = git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    except (gitx.CatalogError, gitx.OutputLimit):
        return None
    return True if done.code == 0 else False if done.code == 1 else None


def head_commit(root: str, sha: str) -> Optional[trees.Commit]:
    """The author, committer and message of a commit."""
    try:
        done = git(root, "log", "-1", "-z", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B", sha)
    except (gitx.CatalogError, gitx.OutputLimit):
        return None
    parts = done.out.split(b"\0") if done.code == 0 else []
    if len(parts) < 6:
        return None
    sha_, *rest = [part.decode("utf-8", "replace") for part in parts[:6]]
    return trees.Commit(sha_, *rest) if SHA.fullmatch(sha_) else None


def carries_tool_credit(commit: trees.Commit) -> bool:
    """Whether the commit names a tool as its author or committer, or credits one in its message or a `Co-authored-by` trailer: the
    list of CR01 and CR02."""
    return (commits.is_tool(commit.author, commit.author_email) or commits.is_tool(commit.committer, commit.committer_email)
            or any(commits.is_tool(name, email) for name, email in commits.trailers(commit.message))
            or commits.credited(commit.message) is not None)


# ---------------------------------------------------------------------------------------------------------------------
# GitHub: the check run and the last successful run
# ---------------------------------------------------------------------------------------------------------------------

def gh_of(seams: dict) -> Callable:
    return seams.get("gh") or settings.Gh()


def read(call: Callable, path: str) -> settings.Reply:
    """A GET through `gh api`: the Reply, or Unavailable when `gh` is not there or not signed in."""
    try:
        return settings.as_reply(call("GET", path))
    except settings.Unusable as error:
        raise Unavailable(report.printable(str(error))) from None


def newest_check(body: dict) -> Optional[dict]:
    """The latest check run named `Catalog CI result` that GitHub Actions made, from a reply of the checks of a commit."""
    runs = body.get("check_runs") if isinstance(body, dict) else None
    mine = []
    for each in runs if isinstance(runs, list) else ():
        app = each.get("app") if isinstance(each, dict) else None
        if isinstance(each, dict) and each.get("name") == CHECK and isinstance(app, dict) and app.get("id") == CHECK_APP \
                and isinstance(each.get("id"), int):
            mine.append(each)
    return max(mine, key=lambda each: each["id"]) if mine else None


def wait_for_check(repository: str, sha: str, call: Callable, sleep: Callable, clock: Callable, out, interval: float = POLL,
                   limit: float = LIMIT) -> bool:
    """Whether the check run of the commit ended in success. Waits while it has not started or is running, up to `limit` seconds.
    A check run that ended in anything else says no at once. A token that cannot read the checks is Unavailable, and so is a GitHub
    that does not answer for the whole time."""
    path = f"repos/{repository}/commits/{sha}/check-runs?check_name=" + CHECK.replace(" ", "%20") + "&per_page=100"
    started = clock()
    while True:
        reply = read(call, path)
        if reply.code in (401, 403, 404):
            raise Unavailable(f"GitHub did not let this job read the checks of {sha[:7]} (HTTP {reply.code}): the job needs "
                              "checks: read on the repository")
        found = newest_check(reply.body) if reply.code == 200 else None
        if reply.code == 200 and found is not None and found.get("status") == "completed":
            return found.get("conclusion") == "success"
        if clock() - started >= limit:
            if reply.code != 200:
                raise Unavailable("GitHub did not answer when it was asked for the checks of " + sha[:7])
            return False   # still not done, or never started, after the whole time: not passed
        print(f"Waiting for {CHECK} on {sha[:7]}: "
              + ("it has not started" if found is None and reply.code == 200 else
                 "GitHub did not answer" if reply.code != 200 else str(found.get("status") or "queued")), file=out, flush=True)
        sleep(interval)


def last_successful_run(repository: str, call: Callable) -> Tuple[Optional[str], str]:
    """(the commit of the newest successful run of this workflow on `main`, or None; a sentence for a person when it is None). A
    workflow with no run yet is not an error: it is the first, and every image is built."""
    reply = read(call, f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?branch=main&event=push&status=success&per_page={RUNS}")
    if reply.code in (401, 403):
        raise Unavailable(f"GitHub did not let this job read the runs of the workflow (HTTP {reply.code}): the job needs "
                          "actions: read on the repository")
    if reply.code == 404:
        return None, "this workflow has no run yet"
    if reply.code != 200:
        return None, "GitHub did not say which run was the last that succeeded"
    runs = reply.body.get("workflow_runs") if isinstance(reply.body, dict) else None
    for each in runs if isinstance(runs, list) else ():
        if isinstance(each, dict) and each.get("conclusion") == "success" and each.get("event") == "push" \
                and each.get("head_branch") == "main" and isinstance(each.get("head_sha"), str) and SHA.fullmatch(each["head_sha"]):
            return each["head_sha"], ""
    return None, "no run of this workflow has succeeded yet"


# ---------------------------------------------------------------------------------------------------------------------
# The images of a catalog
# ---------------------------------------------------------------------------------------------------------------------

class Image(NamedTuple):
    """An image a catalog publishes, as its tree says it is built. `watches` are the places whose change rebuilds it: its context,
    its Dockerfile and the compose file of its unit, from the top of the catalog."""
    name: str
    unit: str
    service: str
    path: str
    context: str
    dockerfile: str
    args: Tuple[str, ...]
    target: str
    labels: Tuple[str, ...]
    remote: bool
    watches: Tuple[str, ...]


class Collected(NamedTuple):
    images: List[Image]
    problems: List[Problem]
    notes: List[str]


def parts_of(command: Sequence[str]) -> dict:
    """The pieces of the `docker buildx build` that `ci build` makes for a service (`build.command_of`): its tag, Dockerfile, build
    arguments, target, labels and context. Read from the command because that is the one place the build keys were judged
    (T18's allow-list, the contexts inside the catalog), so what is published is built exactly from what `ci build` built."""
    found = {"tag": "", "file": "", "args": [], "target": "", "labels": [], "context": command[-1] if command else ""}
    words = list(command[3:-1])
    at = 0
    while at < len(words):
        word = words[at]
        at += 1
        if word == "--load":
            continue
        value = words[at] if at < len(words) else ""
        at += 1
        if word == "--tag":
            found["tag"] = value
        elif word == "--file":
            found["file"] = value
        elif word == "--build-arg":
            found["args"].append(value)
        elif word == "--label":
            found["labels"].append(value)
        elif word == "--target":
            found["target"] = value
    return found


def last_part(image: str) -> str:
    """The last path part of an image reference, without its tag or digest: `ghcr.io/o/r/bakery-web:latest` is `bakery-web`."""
    return image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]


def watches(spec: build.Spec, pieces: dict, root: str) -> Tuple[str, ...]:
    """The places of the catalog whose change rebuilds an image."""
    found = [spec.path]
    if not pieces["context"].startswith(("http", "git", "ssh", "github.com")) and os.path.isabs(pieces["context"]):
        found.append(os.path.relpath(pieces["context"], root).replace(os.sep, "/"))
        if os.path.isabs(pieces["file"]):
            found.append(os.path.relpath(pieces["file"], root).replace(os.sep, "/"))
    return tuple(dict.fromkeys(found))


def collect(root: str, prefix: str, lab=None) -> Collected:
    """The images of the whole catalog that carry its prefix, by name. Every unit is read by the Lab's own screen and policy
    (`ci.load`, Compose in the scrubbed environment) and every service that builds is judged as `ci build` judges it. A unit left out
    for want of a setting is said and its images are not planned; any other unit that is refused, and a build that `ci build` would
    refuse, are errors: a lane that publishes a part of a catalog without saying so is worse than one that stops."""
    tree = trees.FolderTree(root)
    names = [(unit.kind, unit.name) for unit in sorted(planning.listing(tree.entries()).units, key=lambda each: each.key)]
    real = os.path.realpath(root)
    problems: List[Problem] = []
    notes: List[str] = []
    if not names:
        return Collected([], problems, notes)
    loading = load(root, names)
    lacking = [each for each in loading.refused if needs_settings(each)]
    problems += refusals([each for each in loading.refused if not needs_settings(each)], "read")
    notes += [f"{each.kind}:{each.name} needs settings, so its images are not planned" for each in lacking]
    specs, more, _ = build.specs_of(loading.units, "linux/amd64", real, lab)
    problems += more
    found: Dict[str, Image] = {}
    for spec in specs:
        pieces = parts_of(spec.command)
        repository = image_rules.repository(spec.image)
        name = f"{prefix}/{last_part(spec.image)}".lower()
        if not repository or repository != name:
            unit = spec.unit.partition(":")[2]
            problems.append(Problem("IM02", ERROR, spec.path, 0, say(
                image_rules.MORE["IM02"][0], service=spec.service, image=report.printable(spec.image), prefix=prefix, unit=unit,
                app=build.app_of(spec.context) or last_part(spec.image))))
            continue
        if not NAME.fullmatch(name[len(prefix) + 1:]):
            problems.append(Problem("IM02", ERROR, spec.path, 0, say(
                image_rules.MORE["IM02"][0], service=spec.service, image=report.printable(spec.image), prefix=prefix,
                unit=spec.unit.partition(":")[2], app="app")))
            continue
        remote = not os.path.isabs(pieces["context"])
        found.setdefault(name, Image(name, spec.unit, spec.service, spec.path, pieces["context"], pieces["file"],
                                     tuple(pieces["args"]), pieces["target"], tuple(pieces["labels"]), remote,
                                     watches(spec, pieces, real)))
    return Collected([found[name] for name in sorted(found)], problems, notes)


def touched_places(changed: Sequence[str]) -> Set[str]:
    """Every path in a list of changed paths, and every folder above each: a place of the catalog is touched when it is one of these,
    which is the same as a changed path being the place or below it, found in one pass."""
    found: Set[str] = set()
    for path in changed:
        while path and path not in found:
            found.add(path)
            path = path.rpartition("/")[0]
    return found


def affected(image: Image, changed: Sequence[str], touched: Optional[Set[str]] = None) -> bool:
    """Whether a changed path is one the image is built from: its context (or any file below it), its Dockerfile, the compose file of
    its unit. `touched` is `touched_places(changed)`, when the caller asks about many images."""
    touched = touched_places(changed) if touched is None else touched
    return any(place in ("", ".") and bool(touched) or place in touched for place in image.watches)


# ---------------------------------------------------------------------------------------------------------------------
# publish-plan
# ---------------------------------------------------------------------------------------------------------------------

class PlanResult(NamedTuple):
    images: List[str]
    problems: List[Problem]
    notes: List[str]
    attest: bool


def platforms_of(tree) -> Tuple[List[str], List[Problem]]:
    """The platforms of `.github/standard.json`, in the canonical order, or the SD04 that says why they cannot be read."""
    grid, problems = planning.matrix_of(tree)
    return [each["platform"] for each in (grid or {}).get("include", ())], problems


def tag_gates(root: str, meta: dict, ref: Ref, sha: str, repository: str) -> List[Problem]:
    """PB01, PB02 and PB05 for a tag: what needs no network."""
    problems: List[Problem] = []
    have = meta.get("version", "")
    if have != ref.version or not VERSION.fullmatch(ref.version):
        problems.append(finding("PB01", "catalog.yml", a=report.printable(have or "none"), b=report.printable(ref.version)))
    on_main = is_ancestor(root, sha, "origin/main")
    if on_main is not True:
        problems.append(finding("PB02", tag=report.printable(ref.ref.rsplit("/", 1)[-1])))
    return problems


def prefix_gate(meta: dict, repository: str) -> List[Problem]:
    """PB05: `images` is `ghcr.io/<repository>` in lower case."""
    wanted = f"{HOST}/{repository.lower()}"
    have = meta.get("images", "")
    return [] if have == wanted else [finding("PB05", "catalog.yml", x=report.printable(have or "none"), y=wanted)]


def make_plan(root: str, ref: Ref, sha: str, repository: str, private: bool, event: str, seams: dict, out) -> PlanResult:
    """The gates and the images of one run."""
    tree = trees.FolderTree(root)
    meta = catalog_meta(tree.text("catalog.yml") or "")
    prefix = meta.get("images", "")
    notes: List[str] = []
    problems: List[Problem] = []
    sleep, clock = seams.get("sleep") or time.sleep, seams.get("clock") or time.monotonic
    call = None

    def gh() -> Callable:
        nonlocal call
        call = call or gh_of(seams)
        return call
    previous: Optional[str] = None
    why = ""
    if ref.kind == "tag":
        problems += tag_gates(root, meta, ref, sha, repository)
        if prefix:
            problems += prefix_gate(meta, repository)
        if problems:
            return PlanResult([], problems, notes, not private)
    else:
        if not prefix:
            return PlanResult([], [], ["catalog.yml names no registry in images, so nothing is published"], not private)
        problems += prefix_gate(meta, repository)
        commit = head_commit(root, sha)
        if commit is not None and carries_tool_credit(commit):
            problems.append(finding("PB08", sha7=sha[:7]))
        if problems:
            return PlanResult([], problems, notes, not private)
        previous, why = last_successful_run(repository, gh())
        if previous is not None and previous != sha and is_ancestor(root, sha, previous) is True:
            # A re-run of an old run, or a run that waited behind newer ones: what a run that came after it published includes this
            # commit, and publishing it again would move `latest` back to an old build
            return PlanResult([], [], [f"a run that succeeded after {sha[:7]} ({previous[:7]}) has published it: nothing is published "
                                       "again"], not private)
    if not wait_for_check(repository, sha, gh(), sleep, clock, out, seams.get("interval", POLL), seams.get("limit", LIMIT)):
        return PlanResult([], [finding("PB03", sha7=sha[:7])], notes, not private)
    if not prefix:   # (a tag of a catalog that publishes no image still has its release)
        return PlanResult([], [], ["catalog.yml names no registry in images, so no image is published"], not private)
    collected = collect(root, prefix, seams.get("lab"))
    problems += collected.problems
    notes += collected.notes
    names = [each.name for each in collected.images]
    if problems:
        return PlanResult([], problems, notes, not private)
    if ref.kind == "tag":
        return PlanResult(names, [], notes + ["a tag publishes every image"], not private)
    if event == "workflow_dispatch":
        return PlanResult(names, [], notes + ["a manual run publishes every image"], not private)
    base = None
    if previous is not None:
        if is_ancestor(root, previous, sha) is True:
            base = previous
        else:
            why = f"the last successful run ({previous[:7]}) is not an ancestor of {sha[:7]}"
    if base is None:
        return PlanResult(names, [], notes + [f"{why}: every image is built"], not private)
    changed = planning.changed_files(root, base, sha)
    if changed is None:
        return PlanResult(names, [], notes + [f"git could not say what changed since {base[:7]}: every image is built"], not private)
    if any(path in CONTROL for path in changed):
        return PlanResult(names, [], notes + [f"{', '.join(path for path in changed if path in CONTROL)} changed since "
                                              f"{base[:7]}: every image is built"], not private)
    places = touched_places(changed)
    chosen = [each.name for each in collected.images if affected(each, changed, places)]
    return PlanResult(chosen, [], notes + [f"{len(chosen)} of {len(names)} images have build files that changed since the last "
                                           f"successful run ({base[:7]})"], not private)


def booleans(text: str) -> bool:
    if text not in ("true", "false"):
        raise Usage("--private is true or false")
    return text == "true"


@entry
def plan_main(argv, out, err, **seams) -> int:
    """`ci publish-plan`. Exit 1 when a gate says no, 2 when the plan cannot be made."""
    found = parser("publish-plan", "Whether this ref may publish, and what to build.")
    found.add_argument("--ref", required=True, help="refs/heads/main or refs/tags/vX.Y.Z")
    found.add_argument("--sha", required=True, help="the commit: 40 hexadecimal characters")
    found.add_argument("--repository", required=True, help="OWNER/REPO")
    found.add_argument("--private", required=True, help="true or false: whether the repository is private")
    found.add_argument("--event", default=None, help="the event of the run (default: $GITHUB_EVENT_NAME, else push)")
    found.add_argument("--github-output", action="store_true", help="write images, count and attest to $GITHUB_OUTPUT")
    args = parsed(found, argv)
    ref, sha, repository, private = parse_ref(args.ref), check_sha(args.sha), check_repository(args.repository), booleans(args.private)
    event = args.event or os.environ.get("GITHUB_EVENT_NAME") or "push"
    if not re.fullmatch(r"[a-z_]{1,40}", event):
        raise Usage("--event is the name of a GitHub event")
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    if args.github_output and not os.environ.get("GITHUB_OUTPUT"):
        raise Unavailable("--github-output needs $GITHUB_OUTPUT, which only a workflow step has")
    root = os.path.abspath(args.path)
    try:
        result = make_plan(root, ref, sha, repository, private, event, seams, err if args.format == "json" else out)
    except (gitx.CatalogError, gitx.OutputLimit) as error:
        raise Unavailable("git could not be run: " + report.printable(getattr(error, "message", None) or str(error))) from None
    failed = [each for each in result.problems if each.severity == ERROR]
    if args.format == "json":
        out.write(json.dumps({"images": result.images, "count": len(result.images), "attest": result.attest,
                              "problems": [each._asdict() for each in result.problems], "notes": result.notes},
                             indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    else:
        out.write(f"Publish plan for {report.printable(ref.ref)} at {sha[:7]}\n")
        out.write("".join(f"{report.printable(note)}\n" for note in result.notes))
        out.write(f"Images: {len(result.images)}\n" + "".join(f"  {report.printable(name)}\n" for name in result.images))
        if result.problems:
            out.write(report.render(result.problems, args.format))
    if args.ci:
        write_summary("### What to publish\n\n" + "\n".join(f"- {note}" for note in result.notes) + "\n\n"
                      + ("".join(f"- `{name.replace('`', '')}`\n" for name in result.images) or "No image to publish.\n")
                      + ("\n" + report.summary(result.problems, "catalog.py check") if result.problems else ""))
    if failed:
        return 1
    if args.github_output:
        write_outputs({"images": json_line(result.images), "count": str(len(result.images)),
                       "attest": "true" if result.attest else "false"})
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------------------------------------------------

class Info(NamedTuple):
    """What a registry says of a tag that exists: the digest of its manifest and the commit its label names (None: no label)."""
    digest: str
    revision: Optional[str]


def dig(data, *keys):
    """A value by keys, whatever the case of the keys (buildx has written both `manifest` and `Manifest`), or None."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = next((value for name, value in data.items() if isinstance(name, str) and name.lower() == key), None)
    return data


def revision_in(data) -> Optional[str]:
    """The `org.opencontainers.image.revision` label anywhere in the image configuration of a reply (one for each platform of an
    index). Searched without recursion, with a limit on what is looked at."""
    pending, seen = [data], 0
    while pending and seen < 20000:
        item = pending.pop()
        seen += 1
        if isinstance(item, dict):
            for name, value in item.items():
                if isinstance(name, str) and name.lower() == "labels" and isinstance(value, dict) \
                        and isinstance(value.get(REVISION), str):
                    return value[REVISION]
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    return None


def inspect(reference: str, runner: Optional[Callable]) -> Optional[Info]:
    """What the registry has under a tag: None when it has nothing there, Info when it has. Anything else (a registry that does not
    answer, a failure of the network) is Unavailable: a tag that cannot be looked at is not taken for one that is free. A refusal of
    access (`denied`, `unauthorized`) is taken for "nothing there": ghcr answers so for a package that does not exist yet, and a
    job that can write a package can read it, so a tag it cannot read is one it cannot overwrite either (the push would be refused
    the same way, and says so)."""
    code, out, said = run_docker(["docker", "buildx", "imagetools", "inspect", "--format", "{{json .}}", reference], timeout=120,
                                 runner=runner)
    if code != 0:
        if re.search(r"not found|manifest unknown|name unknown|no such manifest|404|denied|unauthorized", said or "", re.I):
            return None
        raise Unavailable(f"the registry could not be asked about {report.printable(reference)}: "
                          + (" ".join(report.printable(line) for line in tail(said or out, 2)) or f"exit {code}"))
    try:
        data = json.loads(out)
    except (ValueError, RecursionError):
        raise Unavailable(f"the registry's answer about {report.printable(reference)} is not JSON") from None
    digest = dig(data, "manifest", "digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise Unavailable(f"the registry's answer about {report.printable(reference)} holds no digest")
    return Info(digest, revision_in(data))


def from_this_commit(name: str, sha: str, existing: Info, runner: Optional[Callable]) -> bool:
    """Whether an image whose registry answer shows no revision label is this commit's anyway: its `sha-<7>` tag, pushed by the same
    build, is the very same manifest. The label is the proof; this is the fallback for an answer that does not carry it."""
    by_commit = inspect(f"{name}:sha-{sha[:7]}", runner)
    return by_commit is not None and by_commit.digest == existing.digest


def labels_for(image: Image, ref: Ref, sha: str, repository_url: str, license_: str) -> List[str]:
    """The labels of an image: those of its own build, then the standard's, which win. `licenses` only for a build from the
    catalog's own files."""
    own = [each for each in image.labels if each.partition("=")[0] not in (SOURCE, REVISION, VERSION_LABEL)]
    version = ref.version if ref.kind == "tag" else f"sha-{sha[:7]}"
    standard = [f"{SOURCE}={repository_url}", f"{REVISION}={sha}", f"{VERSION_LABEL}={version}"]
    if license_ and not image.remote and not any(each.partition("=")[0] == LICENSE_LABEL for each in own):
        standard.append(f"{LICENSE_LABEL}={license_}")
    return own + standard


def tags_for(ref: Ref, sha: str) -> List[str]:
    """`X.Y.Z` and `sha-<7>` for a tag; `sha-<7>` and `latest` for the main branch."""
    return [ref.version, f"sha-{sha[:7]}"] if ref.kind == "tag" else [f"sha-{sha[:7]}", "latest"]


def publish_command(image: Image, tags: Sequence[str], labels: Sequence[str], platforms: Sequence[str]) -> List[str]:
    """The `docker buildx build` of one image, as an argument list: all platforms in one push, with provenance and SBOM."""
    cmd = ["docker", "buildx", "build", "--platform", ",".join(platforms), "--push", "--provenance=mode=max", "--sbom=true"]
    for tag in tags:
        cmd += ["--tag", f"{image.name}:{tag}"]
    for label in labels:
        cmd += ["--label", label]
    cmd += ["--file", image.dockerfile]
    for argument in image.args:
        cmd += ["--build-arg", argument]
    if image.target:
        cmd += ["--target", image.target]
    return cmd + [image.context]


class Record(NamedTuple):
    name: str
    digest: str
    tags: Tuple[str, ...]
    platforms: Tuple[str, ...]

    def as_dict(self) -> dict:
        return {"digest": self.digest, "name": self.name, "platforms": list(self.platforms), "tags": list(self.tags)}


def records_text(records: Sequence[Record]) -> str:
    """records.json: a JSON list sorted by name, keys sorted, ASCII, one final line feed: the same bytes for the same inputs."""
    return json.dumps([each.as_dict() for each in sorted(records, key=lambda one: one.name)], indent=2, sort_keys=True,
                      ensure_ascii=True) + "\n"


def write_text(file: str, text: str) -> None:
    """A file written whole through a copy beside it, so that nothing reads half of it. A link at the name is replaced, not followed."""
    paths.write_atomic(Path(os.path.abspath(file)), text)


@entry
def main(argv, out, err, **seams) -> int:
    """`ci publish`. Exit 1 for PB04, PB06 or a build that failed, 2 when the stage cannot run."""
    found = parser("publish", "Build and push the images the plan named.")
    found.add_argument("--images", required=True, help="the images to publish, a JSON list of names")
    found.add_argument("--ref", required=True, help="refs/heads/main or refs/tags/vX.Y.Z")
    found.add_argument("--sha", required=True, help="the commit: 40 hexadecimal characters")
    found.add_argument("--records", required=True, help="the file the records are written to")
    found.add_argument("--repository", default=None, help="OWNER/REPO (default: the prefix of images in catalog.yml)")
    found.add_argument("--github-output", action="store_true", help="write records to $GITHUB_OUTPUT")
    found.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    args = parsed(found, argv)
    ref, sha = parse_ref(args.ref), check_sha(args.sha)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    if args.github_output and not os.environ.get("GITHUB_OUTPUT") and not args.dry_run:
        raise Unavailable("--github-output needs $GITHUB_OUTPUT, which only a workflow step has")
    root = os.path.abspath(args.path)
    wanted = named(args.images)
    tree = trees.FolderTree(root)
    meta = catalog_meta(tree.text("catalog.yml") or "")
    prefix = meta.get("images", "")
    if not prefix and wanted:
        raise Usage("catalog.yml names no registry in images, so there is nothing to publish to")
    owner = args.repository or (prefix[len(HOST) + 1:] if prefix.startswith(HOST + "/") else "")
    problems: List[Problem] = []
    if prefix and (not prefix.startswith(HOST + "/") or not REPOSITORY.fullmatch(prefix[len(HOST) + 1:])):
        problems.append(finding("PB05", "catalog.yml", x=report.printable(prefix), y=f"{HOST}/<owner>/<repository>"))
    if ref.kind == "tag":
        if not VERSION.fullmatch(ref.version):
            raise Usage(f"{report.printable(ref.ref)} is not a version tag: vX.Y.Z")
    platforms, standard = platforms_of(tree)
    problems += standard
    if problems:
        return finish(problems, [], args, out, err, "")
    collected = collect(root, prefix, seams.get("lab")) if wanted else Collected([], [], [])
    problems += collected.problems
    by_name = {each.name: each for each in collected.images}
    missing = [name for name in wanted if name not in by_name]
    if missing:
        raise Usage(f"the plan named {report.printable(missing[0])}, which this tree does not build as an image")
    chosen = [by_name[name] for name in wanted]
    for image in chosen:
        if image.remote and any(each.partition("=")[0] == LICENSE_LABEL for each in image.labels):
            problems.append(finding("PB06", image.path, image=image.name))
    if problems:
        return finish(problems, [], args, out, err, "")
    url = f"https://github.com/{owner}"
    license_ = meta.get("license", "")
    license_ = license_ if LICENSE.fullmatch(license_) else ""
    runner = seams.get("runner")
    records: List[Record] = []
    outcome = Done([], [])
    if args.dry_run:
        for image in chosen:
            tags = tags_for(ref, sha)
            out.write(f"== {report.printable(image.name)}\n")
            if ref.kind == "tag":
                out.write("# first asked whether it exists: " + shlex.join(
                    ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .}}", f"{image.name}:{ref.version}"]) + "\n")
            out.write(shlex.join(publish_command(image, tags, labels_for(image, ref, sha, url, license_), platforms)) + "\n")
        return 0
    if chosen:
        docker_ready(runner)
        outcome = publish_all(chosen, ref, sha, url, license_, platforms, runner, seams.get("sleep") or time.sleep,
                              err if args.format == "json" else out)
        records = outcome.records
    write_text(args.records, records_text(records))
    if args.github_output:
        write_outputs({"records": json_line([{"digest": each.digest, "name": each.name} for each in
                                             sorted(records, key=lambda one: one.name)])})
    return finish(outcome.problems, records, args, out, err, root, logs=outcome.logs)


class Done(NamedTuple):
    problems: List[Problem]
    logs: List[str]
    records: List[Record] = []


def named(text: str) -> List[str]:
    """The image names of `--images`: a JSON list of names without a tag, each once. Anything else is a usage error."""
    try:
        items = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise Usage("--images is not JSON: give a list of image names") from None
    if not isinstance(items, list) or len(items) > MAX_IMAGES or not all(isinstance(each, str) for each in items):
        raise Usage("--images must be a JSON list of image names")
    for each in items:
        if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9][a-z0-9._-]*)+", each) or len(each) > 255:
            raise Usage(f"--images names {report.printable(each)[:60]}, which is not an image name without a tag")
    return list(dict.fromkeys(items))


def publish_all(images: Sequence[Image], ref: Ref, sha: str, url: str, license_: str, platforms: Sequence[str],
                runner: Optional[Callable], sleep: Callable, out) -> Done:
    """Pushes each image. A tag that exists already is not built again: from this commit its digest is the record, from another it
    is PB04 for that image and the others go on. A build that Docker Hub or the network refused is tried again twice, as `ci build`
    does; one that failed is BD01 with its last lines, and the others go on."""
    records: List[Record] = []
    problems: List[Problem] = []
    logs: List[str] = []
    for image in images:
        tags = tags_for(ref, sha)
        if ref.kind == "tag":
            existing = inspect(f"{image.name}:{ref.version}", runner)
            if existing is not None:
                if existing.revision == sha or (existing.revision is None and from_this_commit(image.name, sha, existing, runner)):
                    records.append(Record(image.name, existing.digest, tuple(tags), tuple(platforms)))
                    print(f"{image.name}:{ref.version} is there already, from this commit: not built again", file=out)
                else:
                    problems.append(finding("PB04", image.path, image=image.name, version=ref.version))
                continue
        cmd = publish_command(image, tags, labels_for(image, ref, sha, url, license_), platforms)
        spec = build.Spec(image.unit, image.service, image.name, cmd, image.path, image.context)
        failure = None
        for attempt in range(len(build.RETRIES) + 1):
            code, output, said = run_docker(cmd, timeout=BUILD_TIMEOUT, runner=runner)
            if code == 0:
                failure = None
                break
            failure = build.classify(code, output, said)
            if failure.kind in ("missing", "unreachable", "stopped"):
                raise Unavailable({"missing": "Docker with buildx is not installed or not on the PATH",
                                   "unreachable": "Docker is not running", "stopped": "the build was stopped"}[failure.kind])
            if failure.kind != "infra" or attempt == len(build.RETRIES):
                break
            sleep(build.RETRIES[attempt])
        if failure is not None:
            problems.append(build.finding(spec, failure))
            logs += [f"The last {len(failure.lines)} lines of the build of {report.printable(image.name)}:",
                     report.untrusted_text("\n".join(failure.lines) + "\n").rstrip("\n")]
            continue
        pushed = inspect(f"{image.name}:{tags[0]}", runner)
        if pushed is None:
            raise Unavailable(f"{report.printable(image.name)}:{tags[0]} was pushed and the registry does not show it")
        records.append(Record(image.name, pushed.digest, tuple(tags), tuple(platforms)))
        print(f"{image.name} {pushed.digest}", file=out)
    return Done(problems, logs, records)


def finish(problems: List[Problem], records: Sequence[Record], args, out, err, root: str, logs: Sequence[str] = ()) -> int:
    for line in logs:
        out.write(line + "\n")
    extra = ""
    if getattr(args, "ci", False) and records:
        extra = "### Published\n\n" + "\n".join(f"- `{each.name.replace('`', '')}` {each.digest} ({', '.join(each.tags)})"
                                                  for each in records) + "\n"
    return conclude("publish", problems, args, out, "make ci", title="Publish the images", extra=extra)


__all__ = ["Image", "Info", "MESSAGES", "Record", "Ref", "affected", "carries_tool_credit", "collect", "dig", "finding", "inspect",
           "labels_for", "main", "make_plan", "named", "newest_check", "parse_ref", "parts_of", "plan_main", "publish_command",
           "records_text", "revision_in", "tags_for", "wait_for_check"]
