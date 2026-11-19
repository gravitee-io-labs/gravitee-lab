"""`ci needs`: the catalogs a catalog requires, fetched at the commit its standard.json names and linked, so that `check` and
`index` read the catalog together with what it includes.

A catalog that includes another one cannot be read alone (`check` says so and stops). CI reads a dependency marked
`public: true` over anonymous HTTPS. Otherwise it uses a read-only deploy key of the dependency's repository, the secret
NEEDS_DEPLOY_KEY, which this stage writes to a file for the length of the stage. `.github/standard.json` names what is needed:

    "needs": {"gravitee": {"repository": "owner/catalog", "ref": "<a full commit>", "public": true}}

A catalog with no `needs` has nothing to fetch and this stage is a line that says so. The commit is a full one so that the
content that was checked is the content that is read; a stage that is told another ref refuses it.

Python 3.9 and later, standard library only."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, TypedDict

from ... import paths
from .. import gitx, report
from . import Usage, conclude, entry, parsed, parser, problem

NAME = re.compile(r"[a-z][a-z0-9-]{0,38}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
COMMIT = re.compile(r"[0-9a-f]{40}")
KEY = "NEEDS_DEPLOY_KEY"
LIMIT = 4096   # a standard.json is under 4 KB

Runner = Callable[[List[str], Dict[str, str]], Tuple[int, str]]


class Dependency(TypedDict):
    repository: str
    ref: str
    public: bool


def declared(folder: str) -> Dict[str, Dependency]:
    """{id: {repository, ref, public}} of `.github/standard.json` in `folder`: empty when it asks for nothing, a Usage when it asks
    for something that is not a catalog at a full commit."""
    path = Path(folder) / ".github" / "standard.json"
    try:
        if path.stat().st_size > LIMIT:
            raise Usage(".github/standard.json is over 4 KB")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        raise Usage(".github/standard.json cannot be read") from None
    needs = data.get("needs") if isinstance(data, dict) else None
    if needs is None:
        return {}
    if not isinstance(needs, dict) or len(needs) > 10:
        raise Usage("needs in .github/standard.json is a map of catalog ids to {repository, ref}")
    found = {}
    for name, what in needs.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(what, dict) \
                or not isinstance(what.get("repository"), str) or not REPOSITORY.fullmatch(what["repository"]) \
                or not isinstance(what.get("ref"), str) or not COMMIT.fullmatch(what["ref"]):
            raise Usage(f"needs.{report.printable(str(name)[:40])} is {{repository: OWNER/NAME, ref: a full commit}}")
        public = what.get("public", False)
        if not isinstance(public, bool):
            raise Usage(f"needs.{name}.public is true or false")
        found[name] = {"repository": what["repository"], "ref": what["ref"], "public": public}
    return found


def run(command: List[str], env: Dict[str, str]) -> Tuple[int, str]:
    """(exit code, the output) of a command with `env` added to a scrubbed one; it asks nothing and is ended after two minutes."""
    base = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if name in os.environ}
    try:
        done = subprocess.run(command, env={**base, **env, "GIT_TERMINAL_PROMPT": "0"}, stdin=subprocess.DEVNULL, timeout=120,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        return 127, type(error).__name__
    return done.returncode, done.stdout


def failure(code: int, output: str) -> str:
    """The last line of a failed command, with URL credentials and terminal controls removed."""
    last = [line for line in output.strip().splitlines() if line.strip()][-1:] or [f"exit {code}"]
    return report.printable(gitx.redact(last[0]))[:200]


def fetch(what: Dependency, scratch: Path, runner: Runner, into: Path) -> Optional[str]:
    """The reason a catalog could not be fetched, or None: `what["ref"]` of `what["repository"]` in `into`."""
    git = ["git", "-C", str(into)]
    init = ["init", "-q"]
    env = {}
    if what["public"]:
        address = f"https://github.com/{what['repository']}.git"
        # libcurl can read .netrc even without a Git credential helper, and a redirect can change the protocol.
        empty = str(scratch / "home")
        env = {"HOME": empty, "XDG_CONFIG_HOME": empty, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CEILING_DIRECTORIES": str(into.parent.resolve()), "GIT_ALLOW_PROTOCOL": "https", "GIT_ASKPASS": ""}
        git += [*gitx.HARDEN, *gitx.STRANGER, "-c", "http.followRedirects=false"]
        init.append("--template=")
        fetch_env = env
    else:
        address = f"git@github.com:{what['repository']}.git"
        key = shlex.quote(str(scratch / "key"))
        fetch_env = {"GIT_SSH_COMMAND": f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"}
    steps = [(init, env), (["fetch", "-q", "--depth", "1", "--", address, what["ref"]], fetch_env),
             (["checkout", "-q", "--detach", "FETCH_HEAD"], env)]
    for step, environment in steps:
        code, output = runner([*git, *step], environment)
        if code:
            return failure(code, output)
    return None


@entry
def main(argv, out, err, **seams) -> int:
    """`ci needs`. Exit 1 when a needed catalog cannot be fetched or linked, 2 when the stage cannot run."""
    found = parser("needs", "Fetch and link the catalogs this one requires, at the commits its standard.json names.")
    args = parsed(found, argv)
    needs = declared(args.path)
    if not needs:
        out.write("Needs: none\n" if not args.ci else "")
        return conclude([], args, out, "make ci", title="Catalogs it needs")
    environment = seams.get("environ", os.environ)
    secret = environment.get(KEY, "")
    problems = []
    private = sorted(name for name, what in needs.items() if not what["public"])
    if private and not secret.strip():
        problems.append(problem("ND01", ".github/standard.json", ids=", ".join(private)))
        return conclude(problems, args, out, "make ci", title="Catalogs it needs")
    runner: Runner = seams.get("runner") or run
    linker = seams.get("linker") or link
    where = Path(seams.get("into") or paths.ROOT / ".needs")
    with tempfile.TemporaryDirectory(prefix="needs-") as folder:
        scratch = Path(folder)
        if private:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            with os.fdopen(os.open(scratch / "key", flags, 0o600), "w", encoding="utf-8") as handle:
                handle.write(secret.rstrip("\n") + "\n")
        if len(private) < len(needs):
            (scratch / "home").mkdir(mode=0o700)
        for name in sorted(needs):
            into = where / name
            shutil.rmtree(into, ignore_errors=True)
            into.mkdir(parents=True)
            why = fetch(needs[name], scratch, runner, into)
            if why is None:
                why = linker(into)
            if why is not None:
                problems.append(problem("ND02", ".github/standard.json", name=name, repository=needs[name]["repository"], why=why))
            else:
                out.write(f"Needs: {name} at {needs[name]['ref'][:7]}\n" if not args.ci else "")
    return conclude(problems, args, out, "make ci", title="Catalogs it needs")


def link(folder: Path) -> Optional[str]:
    """The reason `catalog.py link` refused the folder, or None."""
    code, output = run([sys.executable, "-I", str(paths.ROOT / "lab-ui" / "catalog.py"), "link", str(folder)], {})
    return failure(code, output) if code else None
