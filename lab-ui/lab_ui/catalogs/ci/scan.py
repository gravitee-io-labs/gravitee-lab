"""`catalog.py ci scan-published PATH` (SC01): the images a catalog published have no flaw that is critical and has a fix. It is a step
of the nightly run at level 3, in the job that runs the Lab at the commit the catalog pinned (`verify-pinned`, which holds
`packages: read` and the token for this), and it is not a step of a pull request: a new flaw found in a base image must not turn an
unrelated pull request red, so the scan lives where only a night can fail.

    SC01  {image}: {id} in {package} {version}, fixed in {fixed}. Fix: update the package or the base image.     an error

What is scanned: the images the tree at `PATH` publishes (`publish.collect`: the services it builds whose image is under the `images`
prefix of `catalog.yml`; the Lab's seeder is the Lab's), each at `:latest`, the tip of `main`, which is what a person who sets
`<ID>_PULL_POLICY=always` runs. An image that is not there yet (nothing was published) is said, not a fault.

How, with no credential near a program that is not ours: the scanner is a container **pinned by digest** in `tools/Dockerfile` (the
`FROM` line named `trivy`; never a marketplace action, whose tag a hijack can move), and it runs in two steps. First it fetches its
databases into a folder of ours (this step has the network and mounts nothing else, no image and no secret). Then, for each image, the
stage signs in to ghcr.io itself, reading the token from the environment (`GH_TOKEN`, else `GITHUB_TOKEN`) and passing it on standard
input to `docker login` in a configuration folder of its own (never on a command line and never in a file of the catalog), pulls the
image and saves it to a file; and the scanner reads that file with **no network**, a read-only root, no capability and the user of the
runner, and writes its report to a folder of ours. The token never reaches the container, and the container never reaches the image's
registry. Only critical flaws that have a fixed version are findings (`--severity CRITICAL --ignore-unfixed`, and the report is read
for `FixedVersion` again): a flaw nobody can fix yet is no reason to fail a night.

Exit 0 nothing to fix, 1 a finding (or a unit that cannot be read), 2 the scan could not run (no Docker, a scanner that is not pinned
yet, a registry that refuses, no sign-in): a scan that did not run is never a green one. The flags of the scanner are those of Trivy
0.5x and have not been run against a real registry (rehearsal item V14, the publish rehearsal, is the first time).

Python 3.9 and later, standard library only."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ...docker import compose
from .. import report, tree as trees
from ..report import ERROR, Problem
from ..rules import catalog_meta, say
from . import Unavailable, Usage, conclude, docker_ready, entry, parsed, parser, run_limited, tail
from .gitleaks import BAD_PATH, image as pinned_image
from .publish import HOST, REPOSITORY, collect

MESSAGES = {"SC01": (ERROR, "{image}: {id} in {package} {version}, fixed in {fixed}. Fix: update the package or the base image.")}
TOOL = "trivy"
TAG = "latest"
FINDINGS = 500
REPORT_LIMIT = 64 * 1024 * 1024
PULL_TIMEOUT = 900
SAVE_TIMEOUT = 900
SCAN_TIMEOUT = 900
DB_TIMEOUT = 600
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
NOT_THERE = re.compile(r"not found|manifest unknown|name unknown|no such manifest|404", re.I)
HARDENED = ["--rm", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges", "--pids-limit", "256", "--memory", "6g", "-e", "HOME=/tmp"]


def finding(**fields) -> Problem:
    severity, message = MESSAGES["SC01"]
    return Problem("SC01", severity, "", 0, say(message, **fields))


class Folders(NamedTuple):
    cache: str
    images: str
    out: str
    config: str


def docker_env(config: str) -> Dict[str, str]:
    """The build environment of 5.5 with the Docker configuration of this scan: the credentials of the login are in a folder of
    ours and nowhere else."""
    return {**compose.build_environment(), "DOCKER_CONFIG": config}


def ask(argv: Sequence[str], timeout: float, config: str, runner: Optional[Callable]) -> Tuple[int, str, str]:
    """A docker command with the configuration of this scan."""
    if runner is not None:
        return runner(list(argv), timeout, None)
    return run_limited(argv, timeout=timeout, environ=docker_env(config))


def login(registry: str, user: str, token: str, config: str) -> Tuple[int, str]:
    """`docker login` with the token on standard input, in the configuration folder of this scan. The token is in no argument and no
    file of ours, and what it prints is not shown (it can name the user)."""
    try:
        done = subprocess.run(["docker", "login", registry, "--username", user, "--password-stdin"], input=token, text=True,
                              capture_output=True, timeout=120, env=docker_env(config))
    except (OSError, subprocess.SubprocessError) as error:
        return 127, str(error)
    return done.returncode, (done.stderr or "").strip()


def download(scanner: str, folders: Folders, kind: str, user: Optional[str], runner: Optional[Callable]) -> None:
    """One database into the cache of the scan. This container has the network and nothing else: it mounts the cache, and it is
    not given an image, a token or the catalog."""
    cmd = ["docker", "run", *HARDENED, *(["--user", user] if user else []), "-v", f"{folders.cache}:/cache", scanner, "image",
           f"--download-{kind}-only", "--cache-dir", "/cache", "--no-progress", "--quiet"]
    code, out, said = ask(cmd, DB_TIMEOUT, folders.config, runner)
    if code != 0:
        raise Unavailable(f"the scanner could not fetch its {kind} database: "
                          + (" ".join(report.printable(line) for line in tail(said or out, 2)) or f"exit {code}"))


def scan_command(scanner: str, folders: Folders, archive: str, user: Optional[str]) -> List[str]:
    """The scan of one saved image: no network, the archive and the cache, and a folder for the report."""
    return ["docker", "run", *HARDENED, "--network", "none", *(["--user", user] if user else []),
            "-v", f"{folders.images}/{archive}:/image.tar:ro", "-v", f"{folders.cache}:/cache", "-v", f"{folders.out}:/out", scanner,
            "image", "--input", "/image.tar", "--scanners", "vuln", "--severity", "CRITICAL", "--ignore-unfixed",
            "--skip-db-update", "--skip-java-db-update", "--cache-dir", "/cache", "--no-progress", "--quiet", "--exit-code", "0",
            "--format", "json", "--output", "/out/report.json"]


def findings_of(image: str, document) -> List[Problem]:
    """SC01 for each critical flaw with a fix in a Trivy report. Every value is checked: a package or a version that is not text, or an
    identifier that is not one, is left out rather than shown."""
    found: Dict[Tuple[str, str, str, str], Problem] = {}
    results = document.get("Results") if isinstance(document, dict) else None
    for result in results if isinstance(results, list) else ():
        for each in (result.get("Vulnerabilities") if isinstance(result, dict) else None) or ():
            if not isinstance(each, dict) or each.get("Severity") != "CRITICAL":
                continue
            identity, package, version, fixed = (each.get(key) for key in ("VulnerabilityID", "PkgName", "InstalledVersion",
                                                                         "FixedVersion"))
            if not (isinstance(identity, str) and ID.fullmatch(identity) and isinstance(package, str) and isinstance(version, str)
                    and isinstance(fixed, str) and fixed.strip() and package and version):
                continue
            key = (identity, package, version, fixed)
            if key not in found:
                found[key] = finding(image=report.printable(image), id=identity, package=report.printable(package)[:100],
                                     version=report.printable(version)[:60], fixed=report.printable(fixed)[:100])
        if len(found) >= FINDINGS:
            break
    return [found[key] for key in sorted(found)][:FINDINGS]


def read_report(folder: str) -> object:
    file = os.path.join(folder, "report.json")
    try:
        if os.path.getsize(file) > REPORT_LIMIT:
            raise Unavailable("the report of the scan is over 64 MB: something is wrong with the scan")
        with open(file, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise Unavailable("the scanner wrote no report") from None
    except OSError as error:
        raise Unavailable(f"the report of the scan cannot be read: {report.printable(str(error))[:100]}") from None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Unavailable("the report of the scan is not JSON") from None


def token_of(environ: Optional[Dict[str, str]] = None) -> str:
    environ = os.environ if environ is None else environ
    return environ.get("GH_TOKEN") or environ.get("GITHUB_TOKEN") or ""


def scan_images(images: Sequence[str], scanner: str, token: str, user: str, seams: dict, out) -> Tuple[List[Problem], List[str]]:
    """(the SC01 findings, the images that were not there to scan). Raises Unavailable when a step could not run."""
    runner = seams.get("runner")
    sign_in = seams.get("login") or login
    work = tempfile.mkdtemp(prefix="gravitee-lab-scan-")
    try:
        folders = Folders(*(os.path.join(work, name) for name in ("cache", "images", "out", "config")))
        for each in folders:
            os.mkdir(each, 0o700)
        if any(char in where for where in folders for char in BAD_PATH):
            raise Unavailable("a path of this scan holds a character a bind mount cannot carry")
        uid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
        code, _, _ = ask(["docker", "image", "inspect", scanner], 60, folders.config, runner)
        if code != 0:
            code, said, more = ask(["docker", "pull", "-q", scanner], PULL_TIMEOUT, folders.config, runner)
            if code != 0:
                raise Unavailable("the scanner image could not be pulled: " + " ".join(
                    report.printable(line) for line in tail(more or said, 2)))
        for kind in ("db", "java-db"):
            download(scanner, folders, kind, uid, runner)
        if token:
            code, why = sign_in(HOST, user, token, folders.config)
            if code != 0:
                raise Unavailable(f"the sign-in to {HOST} failed" + (": " + report.printable(why.splitlines()[-1])[:120] if why else ""))
        found: List[Problem] = []
        missing: List[str] = []
        for number, reference in enumerate(images):
            code, said, more = ask(["docker", "pull", "--quiet", reference], PULL_TIMEOUT, folders.config, runner)
            if code != 0:
                if NOT_THERE.search((more or "") + (said or "")):
                    missing.append(reference)
                    continue
                raise Unavailable(f"{report.printable(reference)} could not be pulled: "
                                  + " ".join(report.printable(line) for line in tail(more or said, 2)))
            archive = f"{number}.tar"
            code, said, more = ask(["docker", "save", "--output", os.path.join(folders.images, archive), reference], SAVE_TIMEOUT,
                                   folders.config, runner)
            if code != 0:
                raise Unavailable(f"{report.printable(reference)} could not be saved to scan it: "
                                  + " ".join(report.printable(line) for line in tail(more or said, 2)))
            report_file = os.path.join(folders.out, "report.json")
            if os.path.exists(report_file):
                os.unlink(report_file)
            code, said, more = ask(scan_command(scanner, folders, archive, uid), SCAN_TIMEOUT, folders.config, runner)
            if code != 0:
                raise Unavailable(f"the scan of {report.printable(reference)} did not run (exit {code}): "
                                  + " ".join(report.printable(line) for line in tail(more or said, 3)))
            found += findings_of(reference, read_report(folders.out))
            try:
                os.unlink(os.path.join(folders.images, archive))
            except OSError:
                pass
            print(f"Scanned {reference}", file=out, flush=True)
        return found, missing
    finally:
        shutil.rmtree(work, ignore_errors=True)


@entry
def main(argv, out, err, **seams) -> int:
    """`ci scan-published`."""
    found = parser("scan-published", "The published images have no critical flaw that has a fix.")
    found.add_argument("--tag", default=TAG, help="the tag of each image to scan (default: latest, the tip of main)")
    args = parsed(found, argv)
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}", args.tag):
        raise Usage("--tag is a tag of an image: letters, digits and . _ -")
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    meta = catalog_meta(trees.FolderTree(root).text("catalog.yml") or "")
    prefix = meta.get("images", "")
    if not prefix:
        if args.format != "json":
            out.write("Published images: catalog.yml names no registry in images, so nothing is published and nothing is scanned\n")
        return conclude("scan-published", [], args, out, "catalog.py ci scan-published", [],
                        title="The published images have no flaw to fix")
    if not prefix.startswith(HOST + "/") or not REPOSITORY.fullmatch(prefix[len(HOST) + 1:]):
        raise Usage(f"catalog.yml images is {report.printable(prefix)[:80]}, which is not {HOST}/<owner>/<repository>")
    scanner = pinned_image(seams.get("dockerfile"), TOOL)
    collected = collect(root, prefix, seams.get("lab"))
    problems = list(collected.problems)
    references = [f"{each.name}:{args.tag}" for each in collected.images]
    skipped: List[str] = list(collected.notes)
    if references:
        docker_ready(seams.get("runner"))
        user = os.environ.get("GITHUB_ACTOR") or prefix[len(HOST) + 1:].split("/", 1)[0]
        flaws, missing = scan_images(references, scanner, token_of(seams.get("environ")), user, seams, err if args.format == "json" else out)
        problems += flaws
        skipped += [f"{report.printable(each)} is not in the registry yet, so it was not scanned" for each in missing]
        if args.format != "json":
            out.write(f"Published images: {len(references) - len(missing)} scanned, {len(flaws)} flaws to fix\n")
    elif args.format != "json":
        out.write("Published images: this catalog builds no image under its prefix, so nothing is scanned\n")
    return conclude("scan-published", problems, args, out, "catalog.py ci scan-published", skipped,
                    title="The published images have no flaw to fix")


__all__ = ["MESSAGES", "findings_of", "main", "scan_command", "scan_images"]
