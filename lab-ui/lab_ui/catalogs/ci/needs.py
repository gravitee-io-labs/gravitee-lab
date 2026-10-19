"""`ci needs`: the catalogs a catalog requires, fetched at the commit its standard.json names and linked, so that `check` and
`index` read the catalog together with what it includes.

A catalog that includes another one cannot be read alone (`check` says so and stops). While that other catalog is private,
CI reads it with a read-only deploy key of its own repository, the secret NEEDS_DEPLOY_KEY, which this stage writes to a file
of its own for the length of the fetch and nothing else. `.github/standard.json` names what is needed:

    "needs": {"gravitee": {"repository": "owner/catalog", "ref": "<a full commit>"}}

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
from typing import Callable, Dict, List, Optional, Tuple

from ... import paths
from .. import report
from . import Usage, conclude, entry, parsed, parser, problem

NAME = re.compile(r"[a-z][a-z0-9-]{0,38}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
COMMIT = re.compile(r"[0-9a-f]{40}")
KEY = "NEEDS_DEPLOY_KEY"
LIMIT = 4096   # a standard.json is under 4 KB

Runner = Callable[[List[str], Dict[str, str]], Tuple[int, str]]


def declared(folder: str) -> Dict[str, Dict[str, str]]:
    """{id: {repository, ref}} of `.github/standard.json` in `folder`: empty when it asks for nothing, a Usage when it asks
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
        found[name] = {"repository": what["repository"], "ref": what["ref"]}
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


def fetch(name: str, what: Dict[str, str], key: str, runner: Runner, into: Path) -> Optional[str]:
    """The reason the catalog `name` could not be fetched, or None: `what["ref"]` of `what["repository"]` in `into`."""
    ssh = f"ssh -i {shlex.quote(key)} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    steps = [["git", "init", "-q", str(into)],
             ["git", "-C", str(into), "fetch", "-q", "--depth", "1", f"git@github.com:{what['repository']}.git", what["ref"]],
             ["git", "-C", str(into), "checkout", "-q", "--detach", "FETCH_HEAD"]]
    for step in steps:
        code, output = runner(step, {"GIT_SSH_COMMAND": ssh})
        if code:
            last = [line for line in output.strip().splitlines() if line.strip()][-1:] or [f"exit {code}"]
            return report.printable(last[0])[:200]
    return None


@entry
def main(argv, out, err, **seams) -> int:
    """`ci needs`. Exit 1 when a needed catalog cannot be fetched or linked, 2 when the stage cannot run."""
    found = parser("needs", "Fetch and link the catalogs this one requires, at the commits its standard.json names.")
    args = parsed(found, argv)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    needs = declared(args.path)
    if not needs:
        out.write("Needs: none\n" if args.format == "text" else "")
        return conclude("needs", [], args, out, "make ci", title="Catalogs it needs")
    environment = seams.get("environ", os.environ)
    secret = environment.get(KEY, "")
    problems = []
    if not secret.strip():
        problems.append(problem("ND01", ".github/standard.json", ids=", ".join(sorted(needs))))
        return conclude("needs", problems, args, out, "make ci", title="Catalogs it needs")
    runner: Runner = seams.get("runner") or run
    linker = seams.get("linker") or link
    where = Path(seams.get("into") or paths.ROOT / ".needs")
    folder = Path(tempfile.mkdtemp(prefix="needs-"))
    try:
        keyfile = folder / "key"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        with os.fdopen(os.open(keyfile, flags, 0o600), "w", encoding="utf-8") as handle:
            handle.write(secret.rstrip("\n") + "\n")
        for name in sorted(needs):
            into = where / name
            shutil.rmtree(into, ignore_errors=True)
            into.mkdir(parents=True)
            why = fetch(name, needs[name], str(keyfile), runner, into)
            if why is None:
                why = linker(into)
            if why is not None:
                problems.append(problem("ND02", ".github/standard.json", name=name, repository=needs[name]["repository"], why=why))
            else:
                out.write(f"Needs: {name} at {needs[name]['ref'][:7]}\n" if args.format == "text" else "")
    finally:
        shutil.rmtree(folder, ignore_errors=True)   # the key goes with it
    return conclude("needs", problems, args, out, "make ci", title="Catalogs it needs")


def link(folder: Path) -> Optional[str]:
    """The reason `catalog.py link` refused the folder, or None."""
    code, output = run([sys.executable, "-I", str(paths.ROOT / "lab-ui" / "catalog.py"), "link", str(folder)], {})
    if code:
        last = [line for line in output.strip().splitlines() if line.strip()][-1:] or [f"exit {code}"]
        return report.printable(last[0])[:200]
    return None
