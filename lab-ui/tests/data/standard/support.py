"""What the tests of the standard share: the fixed parameters of a catalog, a catalog rendered and committed in a temporary
folder with an origin, a way to run git there with the person's name set, and a copy of the template that a test can change.
Nothing here is a real name or address: the Lab is `acme/gravitee-lab`, the owner is `@acme/maintainers`, the commits are
`0123...`. The shell tests need bash, jq and make; a test that does not find one prints a visible skip line."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from lab_ui.catalogs import gitx, standard
from tests import Case
from tests import gitfixture

HERE = Path(__file__).resolve().parent
LAB_COMMIT = "0123456789abcdef0123456789abcdef01234567"
NEWER_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"
PARAMS = {"id": "acme-hub", "name": "Acme Hub", "maintainers": "@acme/maintainers", "lab_repository": "acme/gravitee-lab",
          "lab_ref": LAB_COMMIT, "license": "Apache-2.0"}
SAID: List[str] = []


def params(**more) -> dict:
    return {**PARAMS, **more}


def render(**more) -> Dict[str, bytes]:
    """The new catalog of the fixed parameters at the Lab's level."""
    return standard.render_new(params(**more))


def write(root, files: Dict[str, bytes]) -> None:
    """Files written under a folder, with the folders they are in."""
    for path, data in files.items():
        target = Path(root).joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def skip(reason: str) -> None:
    """A visible skip line, once for each reason in a run, and the test skipped (a skip is not a pass)."""
    if reason not in SAID:
        SAID.append(reason)
        print(f"SKIP: {reason}", file=sys.stderr)
    raise unittest.SkipTest(reason)


def need(program: str) -> str:
    found = shutil.which(program)
    if found is None:
        skip(f"{program} is not installed, the tests that run it did not run")
    return found


def identity(case: Case) -> Path:
    """gitx ready for the test: a temporary home that holds the git configuration of a person called Ada (a name and an email,
    main as the first branch), `file://` allowed. Answers the home folder."""
    gitfixture.require()
    docker = os.environ.get("DOCKER_CONFIG") or os.path.join(os.path.expanduser("~"), ".docker")   # Compose is a plugin found there
    home = gitfixture.isolate(case)
    os.environ["DOCKER_CONFIG"] = docker   # put back with the environment when the test ends
    (home / ".gitconfig").write_text("[user]\n\tname = Ada Dev\n\temail = ada@example.test\n[init]\n\tdefaultBranch = main\n"
                                     "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n", encoding="utf-8")
    return home


class Repo:
    """A work tree with an origin (a bare repository beside it), both in a temporary folder. `git(...)` runs the Lab's own git."""

    def __init__(self, case: Case, files: Optional[Dict[str, bytes]] = None, name: str = "work"):
        self.top = Path(tempfile.mkdtemp(prefix="gravitee-lab-standard-")).resolve()
        case.addCleanup(shutil.rmtree, self.top, True)
        self.root = self.top / name
        self.origin = self.top / "origin.git"
        self.git_in(self.top, "init", "--quiet", "--bare", str(self.origin))
        self.root.mkdir()
        self.git("init", "--quiet", "-b", "main")
        self.git("remote", "add", "origin", str(self.origin))
        if files is not None:
            write(self.root, files)
            self.git("add", "-A")
            self.git("commit", "--quiet", "-m", "Create the catalog")
            self.git("push", "--quiet", "-u", "origin", "main")

    @staticmethod
    def git_in(folder, *args, ok: bool = True) -> str:
        result = gitx.run(list(args), cwd=str(folder), timeout=120)
        if ok and result.code != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.err.decode('utf-8', 'replace')}")
        return result.out.decode("utf-8", "replace")

    def git(self, *args, ok: bool = True) -> str:
        return self.git_in(self.root, *args, ok=ok)

    def commit(self, files: Dict[str, bytes], message: str = "Change it") -> str:
        write(self.root, files)
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def branches(self) -> List[str]:
        return sorted(line.strip(" *") for line in self.git("branch", "--format=%(refname:short)").splitlines())

    def remote_refs(self) -> str:
        return self.git_in(self.top, "--git-dir", str(self.origin), "for-each-ref", "--format=%(refname) %(objectname)")


def catalog_repo(case: Case, **more) -> Repo:
    """A repository holding the new catalog of the fixed parameters, committed on main and pushed."""
    identity(case)
    return Repo(case, render(**more))


def release_manifest(level: Optional[int] = None) -> bytes:
    """The manifest.json of a Lab release as far as a pin reads it: the level of the standard it ships, this Lab's by default."""
    return json.dumps({"level": standard.LEVEL if level is None else level}).encode()


def lab_checkout(case: Case, on_origin: bool = True) -> Repo:
    """A Lab checkout: one commit with a `lab-ui` folder, an origin that is `acme/gravitee-lab` on github.com by name, and, when
    `on_origin`, that commit as origin/main (the Lab's own main) and its release v1.0.0 (what a pin names, SD11)."""
    repo = Repo(case, {"lab-ui/catalog.py": b"print(1)\n", standard.MANIFEST_IN_LAB: release_manifest()}, name="lab")
    tip = repo.git("rev-parse", "HEAD").strip()
    repo.git("remote", "set-url", "origin", "https://github.com/acme/gravitee-lab.git")
    repo.git("update-ref", "refs/remotes/origin/main", tip)
    repo.git("tag", "v1.0.0", tip)
    if not on_origin:
        repo.git("update-ref", "-d", "refs/remotes/origin/main")
    return repo


def copy_template(case: Case) -> Path:
    """A copy of the template that a test may change, standing in for the Lab's own until the test ends."""
    folder = Path(tempfile.mkdtemp(prefix="gravitee-lab-template-")).resolve()
    case.addCleanup(shutil.rmtree, folder, True)
    target = folder / "catalog"
    shutil.copytree(standard.TEMPLATE, target)
    case.patch(standard, "TEMPLATE", target)
    return target


def set_level(template: Path, level: int) -> None:
    path = template / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["level"] = level
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def executable(path) -> bool:
    return bool(os.stat(path).st_mode & stat.S_IXUSR)


def run_shell(script: str, env: Dict[str, str], cwd, timeout: int = 60) -> subprocess.CompletedProcess:
    """A block of bash from a workflow run with bash, in a folder, with the environment given (and a PATH)."""
    bash = need("bash")
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "LC_ALL": "C"}
    return subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=str(cwd), env={**base, **env},
                          capture_output=True, text=True, timeout=timeout)
