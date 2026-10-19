"""What the rule tests of the catalog standard share: a small catalog that passes every rule, a way to change it, and the
secrets the tests plant. A planted secret is made of pieces when a test runs and is never written in a file of the repository, so
a scan of the repository for secrets finds nothing here."""

import hashlib
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from lab_ui.catalogs import gitx
from lab_ui.catalogs.report import Problem
from lab_ui.catalogs.rules import Context, context
from lab_ui.catalogs.tree import ListTree
from tests import Case
from tests import gitfixture

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software.\n"
APACHE = "Apache License\nVersion 2.0, January 2004\n"
AGPL = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n"
LAB_COMMIT = "0123456789abcdef0123456789abcdef01234567"

CATALOG = ("x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  description: Demos for workshops\n"
           "  version: 1.0.0\n  lab: 1\n  license: MIT\n")

# A catalog with the standard's files and one add-on: every rule is quiet on it
GOOD: Dict[str, object] = {
    "catalog.yml": CATALOG,
    "README.md": "# Acme Hub\n\nDemos. See [the add-ons](addons/) and [the guide](docs/guide.md).\n",
    "LICENSE": MIT,
    "SECURITY.md": "# Security\n",
    "CONTRIBUTING.md": "# Contributing\n",
    "REVIEWING.md": "# Reviewing\n",
    "AGENTS.md": "# Agents\n",
    "CLAUDE.md": "@AGENTS.md\n",
    "Makefile": "include .catalog/standard.mk\n",
    ".gitignore": ".env\n.state/\nsecrets/\n",
    ".catalog/standard.mk": "# Managed\n",
    ".github/standard.json": '{\n  "form": "inline",\n  "lab": {\n    "ref": "%s",\n    "repository": "OWNER/lab"\n  },\n'
                             '  "platforms": ["linux/amd64"],\n  "standard": 1\n}\n' % LAB_COMMIT,
    ".github/CODEOWNERS": "* @acme/maintainers\n/.github/ @acme/maintainers\n",
    ".github/PULL_REQUEST_TEMPLATE.md": "## What and why\n",
    "docs/guide.md": "# Guide\n\nStart with [the readme](../README.md).\n",
    "addons/README.md": "# Add-ons\n\n| [Hello](hello/) | Says hello |\n",
    "addons/AGENTS.md": "# Add-ons\n",
    "addons/CLAUDE.md": "@AGENTS.md\n",
    "addons/hello/compose.yml": "x-gravitee:\n  kind: addon\n  name: Hello\nservices:\n  hello:\n    image: traefik/whoami:v1.11.0\n",
    "addons/hello/README.md": "# Hello\n",
}

# The same, as a folder catalog (3.1): no .github/, so the files that need GitHub are not asked for
FOLDER: Dict[str, object] = {key: value for key, value in GOOD.items() if not key.startswith((".github/", ".catalog/"))
                             and key not in ("SECURITY.md", "CONTRIBUTING.md", "REVIEWING.md")}


def files(*changes, drop: Iterable[str] = (), base: Optional[dict] = None) -> Dict[str, object]:
    """The good catalog (or `base`) with `changes` laid over it (dicts of {path: content}) and the paths of `drop` removed."""
    out = dict(GOOD if base is None else base)
    for change in changes:
        out.update(change)
    for path in drop:
        out.pop(path, None)
    return out


def tree(*changes, drop: Iterable[str] = (), base: Optional[dict] = None) -> ListTree:
    """A tree in memory of the good catalog with the changes."""
    return ListTree.from_files(files(*changes, drop=drop, base=base))


def codes(problems: Iterable[Problem]) -> Set[str]:
    return {each.code for each in problems}


def ctx_for(listing: ListTree, **given) -> Context:
    return context(listing, **given)


def run(module, *changes, drop: Iterable[str] = (), base: Optional[dict] = None, **given) -> List[Problem]:
    """The problems a rule module finds in the good catalog with the changes."""
    listing = tree(*changes, drop=drop, base=base)
    return module.check(listing, context(listing, **given))


def timed(call: Callable, *args, **kwargs):
    """(answer, seconds) of a call: the seconds of processor it took, not the seconds that went by, because the machine of a
    test run is shared and a loaded one makes a quick call slow."""
    start = time.process_time()
    answer = call(*args, **kwargs)
    return answer, time.process_time() - start


# What a test plants. Each is built from pieces, so no file holds a secret
def github_token() -> str:
    return "gh" + "p_" + "A1b2C3d4" * 5


def aws_key() -> str:
    return "AK" + "IA" + "ABCDEFGHIJKLMNOP"


def slack_token() -> str:
    return "xo" + "xb-" + "1234567890-abcdefghij"


def google_key() -> str:
    return "AI" + "za" + "Sy" + "A" * 33


def jwt() -> str:
    return ".".join(["ey" + "J" + "hbGciOiJIUzI1NiJ9", "ey" + "J" + "zdWIiOiIxMjM0NTY3ODkwIn0", "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"])


def sk_key() -> str:
    return "s" + "k-" + "proj" + "Ab12Cd34" * 5


def private_key() -> str:
    return "-----" + "BEGIN RSA PRIVATE" + " KEY-----"


def tool() -> str:
    """The name of a tool, built from pieces: the commits tests need one and the repository holds no credit."""
    return "Cl" + "aude"


def tool_address() -> str:
    return "noreply@" + "anthropic" + ".com"


GIT = gitx.executable()
PRINCIPAL = {"GIT_AUTHOR_NAME": "Ada Dev", "GIT_AUTHOR_EMAIL": "ada@example.test", "GIT_COMMITTER_NAME": "Ada Dev",
             "GIT_COMMITTER_EMAIL": "ada@example.test"}


def sha_of(path: Path) -> str:
    """A fingerprint of every file below a folder, `.git` included: names, modes, sizes and contents."""
    digest = hashlib.sha256()
    for folder, names, names_of_files in sorted(os.walk(path)):
        names.sort()
        for name in sorted(names_of_files + names):
            item = Path(folder) / name
            info = item.lstat()
            digest.update(f"{item.relative_to(path)} {info.st_mode} {info.st_size}\n".encode())
            if stat.S_ISREG(info.st_mode):
                digest.update(item.read_bytes())
    return digest.hexdigest()


class WithGit(Case):
    """A test case with a git that runs (else a skip line), a home with no git configuration, and helpers that make work
    trees with commits."""

    def setUp(self):
        gitfixture.require()
        gitfixture.isolate(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.holder = Path(holder.name).resolve()

    def git(self, repo, *args, extra=None, check=True):
        env = {**gitfixture.env(), **PRINCIPAL, **(extra or {})}
        # No standard input: a filter or a program a test configures on purpose may read it, and would wait for the runner's
        done = subprocess.run([GIT, *args], cwd=str(repo), env=env, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        if check and done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {done.stderr.decode(errors='replace')}")
        return done.stdout.decode("utf-8", "replace")

    def work(self, name="work", files=None, commit=True):
        """A work tree with `files` ({path: text or bytes}) committed on main."""
        repo = self.holder / name
        repo.mkdir()
        self.git(repo, "init", "-q", "--template=")
        self.git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(repo, "config", "gc.auto", "0")   # no background clean-up writing under .git while a test measures it
        for path, content in (files or {}).items():
            self.put(repo, path, content)
        if commit and files:
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-q", "-m", "First")
        return repo

    @staticmethod
    def put(repo, path, content):
        file = Path(repo) / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return file

    def commit(self, repo, message, extra=None):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message, extra=extra)
        return self.git(repo, "rev-parse", "HEAD").strip()
