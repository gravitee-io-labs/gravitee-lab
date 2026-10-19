"""`catalog.py ci gitleaks PATH --base B [--head H]` (SE03): the broad net for secrets. The Python scanner of `check` (SE01,
SE02, SE04, SE05, SE07) already read every line the change added; this runs `gitleaks` v8 as a container over `B..HEAD`
(the whole history when B is empty or zeros), pinned by digest in `tools/Dockerfile`:

    docker run --rm --network none --read-only ... -v <catalog>:/repo:ro -v <the Lab's gitleaks.toml>:/config/gitleaks.toml:ro
        -v <an empty folder>:/ignore:ro -v <a folder of ours>:/out  <image>@sha256:...
        git --config /config/gitleaks.toml --ignore-gitleaks-allow --gitleaks-ignore-path /ignore --redact
            --log-opts "--text --no-textconv --no-ext-diff B..HEAD" /repo

**It runs with the Lab's configuration, never the catalog's.** Left alone, gitleaks reads `.gitleaks.toml` and `.gitleaksignore`
from the repository it scans and honours `gitleaks:allow` comments, so a pull request that added any of the three would silence
the scan that judges it. So `--config` is the Lab's own file from the pinned Lab checkout (`tools/gitleaks.toml`), mounted
read-only; `--gitleaks-ignore-path` is an empty folder; `--ignore-gitleaks-allow` is always passed. ST21 refuses such files and
SE07 the marker, so the contributor learns why; the container never reads the catalog's choice. It is the CLI image, not the
`gitleaks-action` (which needs a license for organization accounts) and not a marketplace action.

**It reads every file as text.** gitleaks scans what `git log -p` prints, and git prints "Binary files differ" and no added line
for a file it takes for binary: one with a NUL byte in its first 8 KB, or one a `.gitattributes` marks `-diff` or `binary` (a
pull request can add that file itself, with the token in the same commit). So the range always carries `--text`, which makes git
print every file as text, with `--no-textconv --no-ext-diff` so that no driver changes what is printed; passing `--log-opts`
replaces gitleaks' own default of `--full-history --all`, so the scan of the whole history says those two itself. The Python
scanner of `check` does the same (`--text` in `tree.py`).

The container has no network, a read-only root, no capability and the user that owns the checkout; the catalog is mounted
read-only. The report is written to a folder of ours and read from there; findings are redacted (`--redact`) and the secret is
not in what this prints: only the rule, the file and the line (SE03).

Exit 0 nothing found, 1 a finding, 2 the scan could not run (no Docker, an image that is not pinned yet, a base git does not
know, a container that failed): a scan that did not run is never a green one.

Python 3.9 and later, standard library only."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

from .. import report, tree as trees
from ..report import Problem
from ..rules import secrets
from . import NOT_PINNED_YET, Unavailable, Usage, conclude, docker_ready, entry, parsed, parser, run_docker, tail

TOOLS = Path(__file__).resolve().parent / "tools"
DOCKERFILE = TOOLS / "Dockerfile"
CONFIG = TOOLS / "gitleaks.toml"
TOOL = "gitleaks"
# The options of the `git log -p` that gitleaks reads (see above), and what it reads of the history when there is no base
LOG_TEXT = "--text --no-textconv --no-ext-diff"
LOG_ALL = "--full-history --all"
PIN = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*(?::[\w][\w.-]{0,127})?@sha256:[0-9a-f]{64}")
UNPINNED = "sha256:" + "0" * 64
FROM = re.compile(r"^FROM\s+(\S+)\s+AS\s+(\S+)\s*$", re.I | re.M)
RULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
ZEROS = re.compile(r"0{40}")
SHA = re.compile(r"[0-9a-f]{40}")
FINDINGS = 1000   # the most findings read: past it the report is a flood
REPORT_LIMIT = 32 * 1024 * 1024
SCAN_TIMEOUT = 900
PULL_TIMEOUT = 600
# What may not be in a path handed to a bind mount: `-v` splits on a colon, and a newline ends the word for some tools
BAD_PATH = ":\n\r\0,"


class Mounts(NamedTuple):
    repo: str
    config: str
    ignore: str
    out: str


def image(dockerfile: Optional[Path] = None, name: str = TOOL) -> str:
    """The reference of a tool in `tools/Dockerfile`, `name@sha256:...`, from the FROM line named for the tool. Unavailable when
    the file cannot be read, has no such line, does not pin a digest, or has the digest that stands for one not resolved yet."""
    try:
        text = (dockerfile or DOCKERFILE).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Unavailable(f"the Lab has no {name} image to run: lab_ui/catalogs/ci/tools/Dockerfile cannot be read") from None
    for found in FROM.finditer(text):
        if found.group(2).lower() == name:
            reference = found.group(1)
            if not PIN.fullmatch(reference):
                raise Unavailable(f"the {name} image of the Lab is not pinned by digest: {report.printable(reference)[:80]}")
            if reference.endswith(UNPINNED):
                raise Unavailable(f"the {name} image of the Lab {NOT_PINNED_YET} (its digest is the placeholder of zeros): write "
                                  f"the digest that `docker buildx imagetools inspect` prints in {DOCKERFILE.name}")
            return reference
    raise Unavailable(f"the Lab has no {name} image to run: tools/Dockerfile has no FROM line named {name}")


def revisions(root: str, base: str, head: str) -> List[str]:
    """The `--log-opts` of the scan: `BASE..HEAD` with both resolved to commits of this repository (git is asked, the text is
    never given to the container), or the whole history when there is no base (or one git does not know). Always with LOG_TEXT:
    git prints no line for a file it takes for binary, and a scan that cannot see a file is not a scan of it."""
    if not base or ZEROS.fullmatch(base):
        return ["--log-opts", f"{LOG_TEXT} {LOG_ALL}"]
    first = trees.resolve(root, base)
    last = trees.resolve(root, head or "HEAD")
    if first is None:
        return ["--log-opts", f"{LOG_TEXT} {LOG_ALL}"]   # a base git does not know: everything is read rather than a part of it
    if last is None:
        raise Unavailable(f"{report.printable(head)[:60]} is not a commit of this repository, so its secrets cannot be scanned")
    return ["--log-opts", f"{LOG_TEXT} {first}..{last}"]


def command(reference: str, mounts: Mounts, log_opts: List[str], mode: str = "git", user: Optional[str] = None) -> List[str]:
    """The `docker run` of the scan. Every path that follows `-v` is the Lab's or ours, except the catalog, which is mounted
    read-only; `--config` and `--gitleaks-ignore-path` name places inside the container."""
    for where in mounts:
        if any(char in where for char in BAD_PATH):
            raise Unavailable("a path of this scan holds a character a bind mount cannot carry (a colon, a comma or a line break)")
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "2g"]
    if user:
        cmd += ["--user", user]
    cmd += ["-e", "HOME=/tmp", "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory", "-e", "GIT_CONFIG_VALUE_0=/repo",
            "-v", f"{mounts.repo}:/repo:ro", "-v", f"{mounts.config}:/config/gitleaks.toml:ro", "-v", f"{mounts.ignore}:/ignore:ro",
            "-v", f"{mounts.out}:/out", reference, mode, "--config", "/config/gitleaks.toml", "--ignore-gitleaks-allow",
            "--gitleaks-ignore-path", "/ignore", "--redact", "--exit-code", "0", "--no-banner", "--report-format", "json",
            "--report-path", "/out/report.json"]
    if mode == "git":
        cmd += log_opts
    return cmd + ["/repo"]


def findings(document) -> List[Problem]:
    """SE03 for each finding of gitleaks' JSON report: the file, the line and the rule, and nothing of the secret. A rule name
    that is not a name is `unknown`; a file or a line that is not what it should be is the catalog and line 0."""
    found: List[Problem] = []
    for each in document[:FINDINGS] if isinstance(document, list) else []:
        if not isinstance(each, dict):
            continue
        rule = each.get("RuleID")
        path = each.get("File")
        line = each.get("StartLine")
        found.append(secrets.se03(path if isinstance(path, str) else "", line if isinstance(line, int) and line > 0 else 0,
                                  rule if isinstance(rule, str) and RULE.fullmatch(rule) else "unknown"))
    return found


def read_report(folder: str) -> List[Problem]:
    """The findings of the report the container wrote, if it wrote one (a gitleaks that found nothing may write none)."""
    file = os.path.join(folder, "report.json")
    try:
        if os.path.getsize(file) > REPORT_LIMIT:
            raise Unavailable("the report of the scan is over 32 MB: something is wrong with the scan")
        with open(file, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise Unavailable(f"the report of the scan cannot be read: {report.printable(str(error))[:100]}") from None
    if not data.strip():
        return []
    try:
        return findings(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Unavailable("the report of the scan is not JSON") from None


def scan(root: str, base: str, head: str = "", runner: Optional[Callable] = None, dockerfile: Optional[Path] = None,
         config: Optional[Path] = None) -> List[Problem]:
    """The SE03 findings of the history of the repository at `root`. Raises Unavailable when the scan could not run."""
    root = os.path.realpath(root)
    reference = image(dockerfile)
    git = trees.is_repository(root)
    log_opts = revisions(root, base, head) if git else []
    docker_ready(runner)
    code, _, _ = run_docker(["docker", "image", "inspect", reference], timeout=60, runner=runner)
    if code != 0:
        code, out, said = run_docker(["docker", "pull", "-q", reference], timeout=PULL_TIMEOUT, runner=runner)
        if code != 0:
            raise Unavailable("the gitleaks image could not be pulled: " + " ".join(report.printable(line) for line in tail(said or out, 2)))
    work = tempfile.mkdtemp(prefix="gravitee-lab-gitleaks-")
    try:
        ignore, out_dir = os.path.join(work, "ignore"), os.path.join(work, "out")
        os.mkdir(ignore)
        os.mkdir(out_dir)
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
        cmd = command(reference, Mounts(root, os.path.realpath(str(config or CONFIG)), ignore, out_dir), log_opts,
                      "git" if git else "dir", user)
        code, out, said = run_docker(cmd, timeout=SCAN_TIMEOUT, runner=runner)
        if code != 0:
            raise Unavailable("gitleaks did not run (exit " + str(code) + "): " +
                              " ".join(report.printable(line) for line in tail(said or out, 3)))
        return read_report(out_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@entry
def main(argv, out, err, **seams) -> int:
    """`ci gitleaks`. Exit 1 for a finding, 2 when the scan could not run."""
    found = parser("gitleaks", "The secrets of every commit of BASE..HEAD, with the Lab's own configuration.")
    found.add_argument("--base", default="", help="the commit the change is measured from (empty or zeros: the whole history)")
    found.add_argument("--head", default="", help="the last commit to read (default: HEAD)")
    args = parsed(found, argv)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    problems = scan(args.path, args.base, args.head, seams.get("runner"))
    out.write(("Secrets: no commit of the range holds one\n" if not problems else
               f"Secrets: {len(problems)} found in the commits of the range\n") if args.format == "text" else "")
    return conclude("gitleaks", problems, args, out, "make ci", title="Secrets in every commit")
