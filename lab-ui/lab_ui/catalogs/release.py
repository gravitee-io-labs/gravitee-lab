"""`catalog.py release VERSION [--tag] [--pr]`: a release is made in two steps, because a ruleset forbids a commit on
`main`.

    release/VERSION    a branch with `version:` of catalog.yml bumped, in one commit "Release VERSION", and the line that opens the
                       pull request (with `--pr`, the branch is pushed and `gh pr create` is run when `gh` is here: opening
                       the pull request is what was asked, and GitHub needs the branch for it). It is reviewed and squash merged.
    --tag              after the merge: `origin/main` has to say that version; the annotated tag `vVERSION` is made on it, and the
                       line that pushes it is printed. An administrator pushes it, and `publish.yml` takes it from there.

Both refuse what would make a release that people cannot trust: a tree that is not clean, a branch that is behind `origin/main`, a
version that is not greater than the latest tag. Only the `version:` line of catalog.yml is changed, by a targeted edit of that
line, and the lines that older Gravitee Labs read (`schema`, `lab`) are kept true or added (`with_legacy`), so its comments
and its other lines stay as they are. Nothing else is ever pushed, `main` and the tag least of all: the commands are printed.

`origin/main` and the tags are read after a fetch of both from `origin`, so a release is judged against what the server holds. The
git here is the Lab's (`gitx`): no hook runs, and the person's own name and email sign the commit and the tag. Standard library
only."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import gitx
from .report import FORMATS, printable

CATALOG = "catalog.yml"
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
TAG = re.compile(r"v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
BLOCK = re.compile(r"x-gravitee-catalog:[ \t]*(?:#.*)?\r?")
LINE = re.compile(r"(?P<head>[ \t]+version:[ \t]*)(?P<quote>[\"']?)(?P<number>[0-9]+\.[0-9]+\.[0-9]+)(?P=quote)(?P<tail>[ \t]*(?:#.*)?\r?)")
SHOWN = 200
NO_IDENTITY = ("Please tell me who you are", "Author identity unknown", "unable to auto-detect email address", "auto-detection is disabled",
               "empty ident name", "no email was given")


class ReleaseError(Exception):
    """The release cannot be made, or was stopped: one line for the person, with the fix."""


def numbers(text: str) -> Tuple[int, int, int]:
    found = VERSION.fullmatch(text)
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else (-1, -1, -1)


def edit_version(text: str, version: str) -> Tuple[str, str]:
    """(the catalog.yml text with `version:` of its `x-gravitee-catalog` block set, the version it had). Only that line changes:
    the comments, the other lines and the line ends stay as they are. A file with no such line, or with two, is an error."""
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if BLOCK.fullmatch(line)), None)
    if start is None:
        raise ReleaseError("catalog.yml has no x-gravitee-catalog block. Fix: it is not a catalog file.")
    indent = None
    found: List[int] = []
    for i in range(start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip(" \t\r")
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            break   # the next top-level key: the block is over
        here = len(line) - len(line.lstrip(" \t"))
        indent = here if indent is None else min(indent, here)
        if here == indent and LINE.fullmatch(line):
            found.append(i)
    if len(found) != 1:
        raise ReleaseError("catalog.yml has no single version line to change. Fix: write version: X.Y.Z in the x-gravitee-catalog "
                           "block, once.")
    match = LINE.fullmatch(lines[found[0]])
    lines[found[0]] = match.group("head") + match.group("quote") + version + match.group("quote") + match.group("tail")
    return "\n".join(lines), match.group("number")


def with_legacy(root: Path, text: str) -> str:
    """The text of catalog.yml with the fields of older Gravitee Labs made true (`manifest.with_legacy`): the `lab` the
    items of the catalog need when this Lab can say it (`check.folder_lab`), else the one the file has. A release is a version
    that older Labs read too, so it carries them."""
    from . import manifest   # here and not above: the reader of catalog.yml, which the rest of this command does without
    from ..library import check
    try:
        lab = check.folder_lab(root)
    except (check.Unavailable, OSError, ValueError, gitx.CatalogError):
        lab = None
    return manifest.with_legacy(text, lab)


def _run(root, *args, timeout: float = 120):
    try:
        return gitx.run(list(args), cwd=str(root), timeout=timeout)
    except gitx.CatalogError as error:
        raise ReleaseError(error.message) from None


def _out(result) -> str:
    return result.out.decode("utf-8", "replace").strip()


def _fail(result, what: str) -> ReleaseError:
    text = printable(result.err).strip()[:SHOWN]
    return ReleaseError(f"{what}" + (f": {text}" if text else "."))


def _fetch(root) -> None:
    """The state of `origin`: its main and its tags, read now. A tag that differs from a local one is not overwritten."""
    if _run(root, "remote", "get-url", "origin").code != 0:
        raise ReleaseError("This repository has no origin. Fix: git remote add origin ADDRESS.")
    result = _run(root, "fetch", "--quiet", "--tags", "origin", "+refs/heads/main:refs/remotes/origin/main", timeout=300)
    if result.code != 0:
        raise _fail(result, "origin could not be read")


def latest_tag(root) -> Optional[str]:
    """The highest tag of the form vX.Y.Z, or None."""
    result = _run(root, "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*")
    tags = [line for line in _out(result).splitlines() if TAG.fullmatch(line)]
    return max(tags, key=lambda each: numbers(each[1:])) if tags else None


def _check_version(root, version: str) -> None:
    if not VERSION.fullmatch(version or ""):
        raise ReleaseError("VERSION is three numbers, for example 1.5.0.")
    last = latest_tag(root)
    if last is not None and numbers(version) <= numbers(last[1:]):
        raise ReleaseError(f"VERSION {version} is not greater than the latest tag {last}. Fix: choose a higher version.")
    if _run(root, "rev-parse", "--verify", "--quiet", f"refs/tags/v{version}").code == 0:
        raise ReleaseError(f"The tag v{version} exists already. Fix: choose a higher version.")


def _repository(root) -> Path:
    root = Path(root)
    if not os.path.lexists(root / ".git"):
        raise ReleaseError("This folder is not a git repository. Fix: run it in the catalog.")
    if not (root / CATALOG).is_file():
        raise ReleaseError("This folder has no catalog.yml. Fix: run it in the catalog.")
    return root


def default_gh(argv: List[str], cwd) -> Tuple[int, str, str]:
    """Runs `gh` with the maintainer's own sign-in. (127, "", "") when there is no `gh`."""
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "", ""
    except (OSError, subprocess.SubprocessError) as error:
        return 1, "", type(error).__name__
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def release(root, version: str, tag: bool = False, pr: bool = False, *, gh: Optional[Callable] = None) -> List[str]:
    """Prepares the release of `version` (a branch `release/VERSION` with the bump, one commit), or with `tag` makes the tag
    `vVERSION` on `origin/main`. Answers the lines to print. Raises ReleaseError for what it refuses; pushes nothing.
    `gh(argv, cwd) -> (code, out, err)` is the seam for `--pr`."""
    root = _repository(root)
    if not VERSION.fullmatch(version or ""):
        raise ReleaseError("VERSION is three numbers, for example 1.5.0.")
    _fetch(root)
    return _tag(root, version) if tag else _prepare(root, version, pr, gh or default_gh)


def _branch(root) -> str:
    result = _run(root, "symbolic-ref", "--short", "-q", "HEAD")
    if result.code != 0:
        raise ReleaseError("HEAD is detached. Fix: git switch main, then run again.")
    return _out(result)


def _prepare(root: Path, version: str, pr: bool, gh: Callable) -> List[str]:
    status = _run(root, "status", "--porcelain", "-z")
    if status.code != 0:
        raise _fail(status, "git could not read the tree")
    if status.out:
        raise ReleaseError("The tree is not clean. Fix: commit or stash your changes, then run again.")
    here = _branch(root)
    tip = _out(_run(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"))
    if not tip:
        raise ReleaseError("origin has no main branch. Fix: push main first.")
    if here == "main":
        if _out(_run(root, "rev-parse", "--verify", "refs/heads/main^{commit}")) != tip:
            raise ReleaseError("main is not at origin/main. Fix: git pull --ff-only (or push what main has that origin lacks), "
                               "then run again.")
    elif _run(root, "merge-base", "--is-ancestor", tip, "HEAD").code != 0:
        raise ReleaseError(f"The branch {printable(here)[:80]} is behind origin/main. Fix: git merge origin/main, then run again.")
    _check_version(root, version)
    name = f"release/{version}"
    if _run(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").code == 0:
        raise ReleaseError(f"The branch {name} exists already. Fix: delete it (git branch -D {name}) or choose the next version.")
    file = root / CATALOG
    original = file.read_bytes()
    try:
        text, old = edit_version(original.decode("utf-8"), version)
    except UnicodeDecodeError:
        raise ReleaseError("catalog.yml is not UTF-8 text.") from None
    text = with_legacy(root, text)
    if old == version:
        raise ReleaseError(f"catalog.yml already says {version}. Fix: if it is on main, tag it: make release VERSION={version} TAG=1.")
    if numbers(version) < numbers(old):
        raise ReleaseError(f"catalog.yml says {old} already, which is greater than {version}. Fix: tag {old} "
                           f"(make release VERSION={old} TAG=1), or choose a version above it.")
    made = _run(root, "switch", "--quiet", "-c", name)
    if made.code != 0:
        raise _fail(made, "The branch could not be made")
    try:
        mode = file.stat().st_mode & 0o777
        handle, tmp = tempfile.mkstemp(dir=str(root), prefix=".catalog.yml.")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(text.encode("utf-8"))
            os.chmod(tmp, mode)
            os.replace(tmp, file)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        added = _run(root, "add", "--", CATALOG)
        if added.code != 0:
            raise _fail(added, "git add failed")
        done = _run(root, "commit", "--quiet", "-m", f"Release {version}")
        if done.code != 0:
            text_ = done.err + done.out.decode("utf-8", "replace")
            if any(each in text_ for each in NO_IDENTITY):
                raise ReleaseError("git has no name and email for you. Fix: git config user.name and user.email, then run again.")
            raise ReleaseError("git commit failed: " + printable(text_).strip()[:SHOWN])
    except BaseException:
        _run(root, "reset", "--quiet", "--hard", "HEAD")
        _run(root, "switch", "--quiet", here)
        _run(root, "branch", "-D", "--quiet", name)
        file.write_bytes(original)
        raise
    command = ["gh", "pr", "create", "--base", "main", "--head", name, "--title", f"Release {version}", "--body", ""]
    line = f'gh pr create --base main --head {name} --title "Release {version}" --body ""'
    lines = [f"Prepared {name}: catalog.yml says {version} (one commit).", "", "Next", f"  git push -u origin {name}", f"  {line}",
             f"  after it is merged: make release VERSION={version} TAG=1"]
    if pr:
        # The release branch alone: no tag follows it, whatever the person's git says (push.followTags)
        pushed = _run(root, "push", "--quiet", "--no-follow-tags", "--set-upstream", "origin", f"refs/heads/{name}:refs/heads/{name}")
        if pushed.code != 0:
            return lines + ["", f"git push did not work: {printable(pushed.err).strip()[:SHOWN] or 'it said nothing'}",
                            "The branch is ready here: push it and open the pull request with the lines above."]
        code, out, err = gh(command, root)
        if code == 127:
            lines += ["", "gh is not installed: run the lines above yourself."]
        elif code != 0:
            lines += ["", "gh pr create did not work: " + (printable(err).strip() or "it said nothing")[:SHOWN],
                      f"The branch {name} is on origin: open the pull request with the line above."]
        else:
            # Pushed and opened: what is left is the second step
            lines = [f"Prepared {name}: catalog.yml says {version} (one commit), pushed, and its pull request opened:",
                     printable(out).strip()[:SHOWN], "", "Next", f"  after it is merged: make release VERSION={version} TAG=1"]
    return lines


def _tag(root: Path, version: str) -> List[str]:
    tip = _out(_run(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"))
    if not tip:
        raise ReleaseError("origin has no main branch. Fix: push main first.")
    shown = _run(root, "cat-file", "blob", f"{tip}:{CATALOG}")
    if shown.code != 0:
        raise ReleaseError("origin/main has no catalog.yml. Fix: it is not a catalog.")
    try:
        _, on_main = edit_version(shown.out.decode("utf-8"), version)
    except UnicodeDecodeError:
        raise ReleaseError("catalog.yml on origin/main is not UTF-8 text.") from None
    if on_main != version:
        raise ReleaseError(f"origin/main says {on_main}, not {version}. Fix: merge the release pull request first, then run this "
                           "again.")
    _check_version(root, version)
    made = _run(root, "tag", "-a", f"v{version}", "-m", f"Release {version}", tip)
    if made.code != 0:
        text = made.err
        if any(each in text for each in NO_IDENTITY):
            raise ReleaseError("git has no name and email for you. Fix: git config user.name and user.email, then run again.")
        raise _fail(made, "The tag could not be made")
    return [f"Tagged v{version} on origin/main ({tip[:7]}). Nothing was pushed.", "", "Next",
            f"  git push origin v{version}   (an administrator pushes it; publish.yml takes it from there)"]


def main(argv: Optional[List[str]] = None, gh: Optional[Callable] = None) -> int:
    """`catalog.py release VERSION [--tag] [--pr] [--format text or github or json]`: exit 0 when it worked, 2 when it refuses or
    cannot be done (the line says why and what to do). `json` is one document, {version, step, lines}; the other two print the
    lines, for there is nothing in them to annotate."""
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ReleaseError(f"catalog.py release: {message}")
    parser = Parser(prog="catalog.py release", add_help=False)
    parser.add_argument("version")
    parser.add_argument("--tag", action="store_true")
    parser.add_argument("--pr", action="store_true")
    parser.add_argument("--dir", default=".")
    parser.add_argument("--format", choices=FORMATS, default="text")
    try:
        args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))
        if args.tag and args.pr:
            raise ReleaseError("--pr opens the pull request of the first step and --tag is the second: use one.")
        lines = release(args.dir, args.version, tag=args.tag, pr=args.pr, gh=gh)
    except ReleaseError as error:
        print(printable(str(error)), file=sys.stderr)
        return 2
    except (gitx.CatalogError, OSError) as error:
        print(printable(getattr(error, "message", None) or str(error)), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps({"lines": [printable(line) for line in lines], "step": "tag" if args.tag else "branch", "version": args.version},
                         indent=2, sort_keys=True))
        return 0
    print(printable("\n".join(lines), keep=("\n",)))
    return 0


__all__ = ["ReleaseError", "edit_version", "latest_tag", "main", "numbers", "release"]
