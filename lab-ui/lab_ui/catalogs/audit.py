"""The orchestrator of the rules: `run(root, options)` reads a catalog and answers what is wrong with it, in the order SD, ST, SE and CR, WF, DF, GD, CO, then the items, so that a contributor's `make check`, the `static` job of CI and
Share's pre-flight list the same findings with the same words.

What `run` decides, and the rule modules do not:

- **The range.** Without a base, the base is `git merge-base origin/main HEAD` when `origin/main` exists, so the secrets and
  commits of a branch are read as CI reads them, before the push; `Result.header` says what was read. With `history`, every
  commit of the history is read (the go-public gate). Commits that cannot be read are `skipped`, never an empty answer.
- **`--base`.** The base is a name git resolves to a commit (`origin/main`, `HEAD~1`, a branch, an id), resolved once to its id;
  one that is not a commit is said (`skipped`) and the commits are not read. An error of a family of content hygiene (ST, GD, DF,
  IM) that the base commit already had, with the same code, path and message (the line is not part of it), is a warning "already
  there before this change"; T, N, SE, WF, SD, CO, CR, PB and BD are never downgraded, and a key is matched once, so a second one
  is a new error. It is not done for a change that holds nothing (the base is the head): there is nothing to subtract from. The
  base is read from the repository's git directory as git names it, so a linked work tree (`git worktree add`), whose `.git`
  is a file, is read as a clone is; when the files of the base cannot be read that is said, and the errors stay errors.
- **Severity.** `.github/standard.json "strict"` and `error` make a warning an error; `soft` prints every error as a warning.
  `status` is the exit code: 1 for an error (and a warning with `strict`), and 1 for a check that was skipped, 2 under `ci`.
- **What it does not do.** It never writes (git is asked to leave the index alone), never links, never runs a file of the tree,
  and makes no network call: `online` only asks for the checks that compare with GitHub (IM04), from what git already holds.

The modules of other packages (`standard` for SD, `workflows` for WF, `codeowners` for CO) are used when they exist, and said
(`skipped`) when they do not. A folder catalog, which has no `.github/`, is judged on the families that need no GitHub, and
`Result.header` says so in a second line."""

import importlib
import os
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, List, NamedTuple, Optional, Tuple

from .. import paths
from . import gitx, tree as trees
from .report import ERROR, WARNING, Problem, printable
from .rules import HYGIENE, Context, context, outline
from .rules.images import repository

ORDER = ("SD", "ST", "SE", "CR", "WF", "DF", "GD", "CO", "IM")
# The modules of each family: (module, the function that judges a tree). SD is the standard's own (`standard.drift`)
MODULES = {"ST": "structure", "SE": "secrets", "CR": "commits", "WF": "workflows", "DF": "dockerfiles", "GD": "guides",
           "CO": "codeowners", "IM": "images"}
PREFIX = "Already there before this change: "


class Options(NamedTuple):
    """What a run is asked for. Every field has a default, so `Options()` is the check of a folder.

    base           the commit the change is measured from, as git names a commit (`origin/main`, `HEAD~1`, a branch, an id); None
                   takes `git merge-base origin/main HEAD` when there is one
    soft           every error is a warning and the exit code is 0 (the check of a newer Lab on a catalog written for an older)
    error          codes that are errors for this run whatever they are
    strict         the exit code is 1 for a warning too
    online         the checks that compare with GitHub are asked for
    history        read every commit of the history, not only those since the base
    verbose        the information too (a rendering option; `run` always returns it)
    ci             the run is CI's: IM04 is asked for, and a skipped check is exit 2
    tree           a Tree to read instead of the folder (`GitTree`, for `check --pr`)
    base_tree      the tree of the base commit, when `tree` is not a folder of a repository
    commits        a `tree.History` to read instead of the repository's own
    families       the families to run (None: all); Share asks for ST, SE, DF, GD and IM
    private        identifiers of private catalogs (SE06, Share into a Public catalog)
    units          the units that the caller loaded with Compose (IM reads their services instead of the files)
    seeder_images  the repositories of the Lab's seeder; None reads `x-gravitee.seeder.image` of the Lab's core file
    seeder_paths   where the Lab's seeder is from the top of a catalog
    is_seeder      `f(unit_folder, context) -> bool`, for a caller that knows the real folders
    lab            the Lab checkout (its core file names the seeder); default the one this code is in
    repository     OWNER/REPO of this repository (IM04); default $GITHUB_REPOSITORY, else the `origin` remote
    """
    base: Optional[str] = None
    soft: bool = False
    error: Tuple[str, ...] = ()
    strict: bool = False
    online: bool = False
    history: bool = False
    verbose: bool = False
    ci: bool = False
    tree: Optional[object] = None
    base_tree: Optional[object] = None
    commits: Optional[object] = None
    families: Optional[Tuple[str, ...]] = None
    private: Tuple[str, ...] = ()
    units: Optional[list] = None
    seeder_images: Optional[Tuple[str, ...]] = None
    seeder_paths: Tuple[str, ...] = ("../../../seeder",)
    is_seeder: Optional[Callable] = None
    lab: Optional[Path] = None
    repository: str = ""


class Result(NamedTuple):
    """What `run` found: the problems (every severity), what was not checked (each a sentence, "Docker Compose 5 not found"),
    the id of the base the commits were read from ("" for none), how many commits were read, and the head of a report: the line
    that says what was read, and, for a catalog with no `.github/standard.json`, a second that says which families were judged."""
    problems: List[Problem]
    skipped: List[str]
    base: str = ""
    commits: int = 0
    header: str = ""


def family(code: str) -> str:
    """The family of a code: the letters before its digits (`ST05` is `ST`, `E2E01` is `E2E`)."""
    return code.rstrip("0123456789")


def lab_seeder_images(lab: Optional[Path] = None) -> Tuple[str, ...]:
    """The repository of the Lab's seeder, `x-gravitee.seeder.image` of its core file, as a one-item tuple, or () when it cannot
    be read. Read as text (no Compose), the same anchor the trust policy uses."""
    core = Path(lab) if lab else paths.ROOT
    try:
        text = (core / "docker-compose.yml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    for node in outline(text).root.children:
        if node.key == "x-gravitee":
            seeder = node.get("seeder")
            image = seeder.get("image") if seeder is not None else None
            repo = repository(image.value.strip("'\"")) if image is not None and image.value else None
            return (repo,) if repo else ()
    return ()


def origin_repository(root) -> str:
    """OWNER/REPO of the `origin` remote when it is on github.com, else "". Read from the repository's configuration."""
    try:
        result = gitx.run(["config", "--get", "remote.origin.url"], cwd=str(root), env=trees.QUIET, timeout=30)
    except gitx.CatalogError:
        return ""
    url = result.out.decode("utf-8", "replace").strip() if result.code == 0 else ""
    found = re.fullmatch(r"(?:https://(?:[^/@]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/"
                         r"([A-Za-z0-9._-]+?)(?:\.git)?/?", url)
    return f"{found.group(1)}/{found.group(2)}" if found else ""


def _load(family_code: str):
    """The check function of a family: its module is imported when the family is run."""
    module = importlib.import_module(f"{__package__}.{'standard' if family_code == 'SD' else 'rules.' + MODULES[family_code]}")
    if family_code == "SD":
        return lambda tree, ctx: module.drift(tree, module.LEVEL)
    return module.check


def _fresh(root, options: Options, folder: bool, skipped: List[str]):
    """(tree, history, base, lost) for a run: the tree to read, the commits of the change, the base they were read from (the id
    of a commit, or "") and the base that was asked for and is no commit of the repository ("" if there is none). That one is a
    line in `skipped`, never an empty history."""
    tree = options.tree if options.tree is not None else trees.FolderTree(root)
    history = options.commits
    base, lost = options.base or "", ""
    if history is None and folder and trees.is_repository(root) and trees.head_of(root):
        if options.history:
            history, base = trees.History(root, None), ""
        else:
            if base:
                resolved = trees.resolve(root, base)
                if resolved is None:
                    lost = base
                    skipped.append(f"the commits since the base: {printable(base)[:60]} is not a commit of this repository, so "
                                   "they could not be read")
                base = resolved or ""
            else:
                base = trees.default_base(root) or ""
            history = trees.History(root, base) if base else None
    elif history is not None:
        base = getattr(history, "base", None) or base
    return tree, history, base, lost


def _context(tree, root, options: Options, history, base: str, notes: List[str]) -> Context:
    seeders = options.seeder_images if options.seeder_images is not None else lab_seeder_images(options.lab)
    repository = options.repository or os.environ.get("GITHUB_REPOSITORY", "")
    if not repository and (options.ci or options.online) and root is not None and trees.is_repository(root):
        repository = origin_repository(root)
    return context(tree, history=history, units=options.units, seeder_images=tuple(seeders), seeder_paths=options.seeder_paths,
                   is_seeder=options.is_seeder, private=tuple(options.private), online=options.online, ci=options.ci,
                   repository=repository, base=base, notes=notes)


def _run_families(tree, ctx: Context, wanted: Iterable[str], skipped: List[str]) -> List[Problem]:
    found: List[Problem] = []
    for code in ORDER:
        if code not in wanted:
            continue
        if code in ("SD", "WF", "CO") and not ctx.git:
            continue   # a folder catalog has no .github/: only the families that need no GitHub apply
        found += _load(code)(tree, ctx)
    return found


def _key(problem: Problem) -> Tuple[str, str, str]:
    """What matches a finding at the base with one now: its code, its path and its message without the line number."""
    return problem.code, problem.path, problem.message.replace(f"{problem.path}:{problem.line}", problem.path, 1)


def subtract(now: List[Problem], earlier: List[Problem]) -> List[Problem]:
    """The problems with each error of a family of content hygiene that `earlier` (the findings at the base) already had made a
    warning, once for each time it was there. Nothing else changes."""
    left = Counter(_key(each) for each in earlier if each.severity == ERROR and family(each.code) in HYGIENE)
    out = []
    for each in now:
        key = _key(each)
        if each.severity == ERROR and family(each.code) in HYGIENE and left[key] > 0:
            left[key] -= 1
            each = each._replace(severity=WARNING, message=PREFIX + each.message)
        out.append(each)
    return out


def promote(problems: List[Problem], standard: Optional[dict], error: Iterable[str]) -> List[Problem]:
    """A warning whose code the catalog (`.github/standard.json "strict"`) or the run (`error`) makes an error is one."""
    codes = set(error)
    listed = standard.get("strict") if isinstance(standard, dict) else None
    if isinstance(listed, list):
        codes |= {each for each in listed if isinstance(each, str)}
    return [each._replace(severity=ERROR) if each.severity == WARNING and each.code in codes else each for each in problems]


def status(result: Result, options: Optional[Options] = None) -> int:
    """The exit code of a check: 1 for an error (and for a warning with `strict`), 1 for a skipped check (2 under `ci`: the
    environment cannot do what the check needs), 0 otherwise. `soft` makes it 0."""
    options = options or Options()
    if options.soft:
        return 0
    if any(each.severity == ERROR for each in result.problems) or (options.strict and any(
            each.severity == WARNING for each in result.problems)):
        return 1
    return (2 if options.ci else 1) if result.skipped else 0


def _header(base: str, commits: int, options: Options, history, ctx: Optional[Context] = None, lost: str = "") -> str:
    """The first line says what was read; a second says that the families that need GitHub were not asked for (a catalog with no
    `.github/standard.json`, 3.1) unless the caller chose the families."""
    count = f"{commits} commit{'' if commits == 1 else 's'}"
    if history is None:
        first = (f"Reading the files only: {printable(lost)[:60]} is not a commit of this repository" if lost
                 else "Reading the files only: there is no origin/main to read the commits from")
    elif options.history or not base:
        first = f"Reading the files and the {count} of the whole history"
    else:
        since = base[:7] if options.base or options.commits is not None else "origin/main"
        first = f"Reading the files and the {count} since {since}"
    if ctx is not None and not ctx.git and options.families is None:
        left = [code for code in ORDER if code not in ("SD", "WF", "CO")]
        first += f"\nNo .github/standard.json: judged on the families that need no GitHub ({', '.join(left)})"
    return first


def run(root, options: Optional[Options] = None) -> Result:
    """Reads the catalog at `root` (or `options.tree`) and judges it. Never writes, never links, makes no network call."""
    options = options or Options()
    skipped: List[str] = []
    folder = options.tree is None
    tree, history, base, lost = _fresh(root, options, folder, skipped)
    try:
        tree.entries()
    except trees.TreeError as error:
        return Result([], [str(error)], base, 0, "")
    ctx = _context(tree, root, options, history, base, skipped)
    wanted = options.families or ORDER
    found = _run_families(tree, ctx, wanted, skipped)
    if (options.ci or options.online) and not ctx.repository and ctx.meta("images") and "IM" in wanted:
        skipped.append("IM04: the address of this repository is not known")
    commits = 0
    if history is not None:
        commits = len(history.commits())
        if getattr(history, "note", ""):
            skipped.append(history.note[:1].upper() + history.note[1:])
    if options.base and folder and history is None and not lost:
        skipped.append("the commits since the base: this folder has no git history")
    if options.history and folder and history is None:
        skipped.append("the history: this folder has no git history")
    found = promote(found, ctx.standard, options.error)
    found = _against_base(found, tree, root, options, base, ctx, skipped)
    if options.soft:
        found = [each._replace(severity=WARNING) if each.severity == ERROR else each for each in found]
    found.sort(key=lambda each: (ORDER.index(family(each.code)) if family(each.code) in ORDER else len(ORDER), each.path,
                                 each.line, each.code, each.message))
    return Result(found, list(dict.fromkeys(skipped)), base, commits, _header(base, commits, options, history, ctx, lost))


def _against_base(found: List[Problem], tree, root, options: Options, base: str, ctx: Context, skipped: List[str]) -> List[Problem]:
    """`subtract` with the findings of the base commit, run only when there is a change to measure and an error to soften. The base
    is read from the git directory git names (a clone's `.git` folder, a linked work tree's), never guessed from the folder; when it
    cannot be read, that is said and the errors stay errors."""
    if not base or not any(each.severity == ERROR and family(each.code) in HYGIENE for each in found):
        return found
    before = options.base_tree
    if before is None and options.tree is None and root is not None and trees.is_repository(root):
        sha = base if re.fullmatch(r"[0-9a-f]{40}", base) else trees.resolve(root, base)
        if sha and trees.head_of(root) == sha:
            return found
        git_dir = trees.git_dir_of(root) if sha else None
        if git_dir:
            before = trees.GitTree(git_dir, sha)
    try:
        if before is None:
            raise trees.TreeError("")
        before.entries()
    except trees.TreeError:
        skipped.append("which errors were already there at the base: the files of the base could not be read, so they stay errors")
        return found
    quiet = context(before, units=None, seeder_images=ctx.seeder_images, seeder_paths=ctx.seeder_paths,
                    is_seeder=ctx.is_seeder, private=(), repository=ctx.repository, ci=ctx.ci, online=ctx.online)
    earlier = _run_families(before, quiet, [code for code in ORDER if code in HYGIENE], [])
    return subtract(found, promote(earlier, ctx.standard, options.error))


__all__ = ["Options", "Result", "family", "lab_seeder_images", "origin_repository", "promote", "run", "status", "subtract"]
