"""The loops over the topic: the audit of every catalog of an owner and the upgrade of every one.

    audit_fleet(owner, runner, options) -> Fleet     `repo-settings --fleet OWNER`: one Row(repo, findings, error) for each
                                                     repository that carries the topic gravitee-lab-catalog
    upgrade_fleet(owner, runner, lab_ref) -> [Upgraded]
                                                     `upgrade --fleet OWNER --lab-ref COMMIT`: for each of them, one pull
                                                     request that brings the managed files to the Lab's level

The repositories are listed through `runner` (the transport of settings.py, which wraps the maintainer's own `gh`). The upgrade
works in a temporary clone of each repository, made by a git that runs no hook, makes no link and checks every object
(`gitx`), and it asks the Lab's `standard` module to rewrite the managed files there (`upgrade_plan` and `upgrade_apply`). The commit it asks for must be a release of the Lab, a tag vX.Y.Z on its `origin/main` (SD11), asked of the Lab checkout that runs the
command before anything is listed or cloned, and a file the upgrade changed outside the managed folders (`.github/` and
`.catalog/`) stops that repository. Nothing is stored: every clone is removed when its repository is done. Nothing is merged and
nothing is forced. A branch named `standard/<level>-<date>` is pushed once, a repository whose default branch moved while the
upgrade ran is left alone, and a repository with a pull request of an earlier upgrade still open is not given a second. The
only writes to GitHub are that push and one POST of a pull request. Python 3.9, standard library only."""

import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

from . import gitx, settings, standard as standards
from .standard import pin_problem, release_state
from .tree import FolderTree
from .settings import FAIL, WARN, Refused, Unusable, Usage, as_dict, as_list, clean, is_int, plan_limit, refusal

COMMIT = re.compile(r"[0-9a-f]{40}")
KIND_FOLDERS = ("addons/", "setups/", "playgrounds/")   # the content of a catalog: an upgrade never touches it
MANAGED_ROOTS = standards.managed_roots()   # where the managed files and `standard.json` live, from the template: nothing else may change
LAB = Path(__file__).resolve().parents[3]               # the Lab checkout that runs this: the folder that holds lab-ui
LAB_LOG = 50                                            # the lines of the Lab's own log that the message of a pin carries
BRANCH_PREFIX = "standard/"
PULL_LIMIT = 3
STANDARD_FILE = ".github/standard.json"
NO_IDENTITY = ("Please tell me who you are", "Author identity unknown", "unable to auto-detect email address",
               "auto-detection is disabled")


class Row(NamedTuple):
    """One repository of the audit: its findings, or why it could not be audited."""
    repo: str
    findings: list
    error: str


class Fleet(list):
    """The rows of one audit, sorted by repository. `owner` is whose they are."""

    def __init__(self, *args, owner: str = ""):
        super().__init__(*args)
        self.owner = owner


class Upgraded(NamedTuple):
    """What became of one repository. `status`: `opened` (`detail` is the pull request), `current` (nothing to change), `open`
    (a pull request of an earlier upgrade is still open), `moved` (its default branch moved while the upgrade ran), `skipped`
    (archived) or `failed`. `owned` lists the files that the catalog owns, which the upgrade left as they are."""
    repo: str
    status: str
    detail: str
    owned: List[str]


# ---------------------------------------------------------------- the repositories

def catalogs_of(owner: str, runner: Callable) -> List[dict]:
    """[{repo, branch, archived}] of the repositories of `owner` that carry the topic, sorted by name. The list is the one of
    the owner's account (their private repositories included when `owner` is the signed-in user) or of the organization, so
    that the topic is read from the repository itself and not from a search, which lags."""
    if not isinstance(owner, str) or not settings.OWNER_NAME.fullmatch(owner):
        raise Usage("OWNER is a user or an organization of github.com.")
    reads = settings.Reads(runner)
    who = reads.get(f"users/{owner}")
    if who.code != 200:
        raise Unusable(f"{owner} is not a user or an organization on github.com ({refusal(who)}).")
    if as_dict(who.body).get("type") == "Organization":
        path = f"orgs/{owner}/repos?type=all"
    else:
        me = as_dict(reads.get("user").body).get("login")
        path = "user/repos?affiliation=owner" if isinstance(me, str) and me.lower() == owner.lower() \
            else f"users/{owner}/repos?type=owner"
    reply, items = reads.pages(path, limit=10)
    if reply.code != 200:
        raise Unusable(f"The repositories of {owner} cannot be listed ({refusal(reply)}).")
    found = []
    for item in map(as_dict, items):
        name, topics = item.get("full_name"), item.get("topics")
        if isinstance(name, str) and settings.REPO_NAME.fullmatch(name) and name.split("/")[0].lower() == owner.lower() \
                and isinstance(topics, list) and settings.TOPIC in topics:
            found.append({"repo": name, "branch": item.get("default_branch"),
                          "archived": bool(item.get("archived") or item.get("disabled"))})
    return sorted(found, key=lambda entry: entry["repo"].lower())


# ---------------------------------------------------------------- the audit

def audit_fleet(owner: str, runner: Callable, options: Optional[dict] = None) -> Fleet:
    """One Row for each catalog of `owner`, audited as `settings.audit` does with `options`. A repository that cannot be read
    is a Row with an error; a missing `gh` or sign-in ends the whole audit."""
    rows = Fleet(owner=owner)
    for entry in catalogs_of(owner, runner):
        if entry["archived"]:
            rows.append(Row(entry["repo"], [], "archived, not audited"))
            continue
        try:
            rows.append(Row(entry["repo"], settings.audit(entry["repo"], runner, options), ""))
        except Refused as problem:
            rows.append(Row(entry["repo"], [], str(problem)))
    return rows


def _unread(row: Row) -> bool:
    return bool(row.error) and not row.error.startswith("archived")


def exit_code(rows, strict: bool = False) -> int:
    """1 when a repository failed a rule or could not be read (an archived one is left out), as settings.exit_code says for
    one; else 0."""
    return 1 if any(_unread(row) or settings.exit_code(row.findings, strict) for row in rows) else 0


def render_audit(rows, fmt: str = "text") -> str:
    """The audit as one table of identifiers by repository, or as `github` annotations, or as JSON."""
    owner = getattr(rows, "owner", "")
    if fmt == "json":
        return json.dumps({"owner": owner, "repositories": [
            {"repository": row.repo, "error": row.error, "summary": settings.summary(row.findings),
             "not_applicable": sorted(getattr(row.findings, "inapplicable", ())),
             "findings": [finding._asdict() for finding in row.findings]} for row in rows]}, indent=2, sort_keys=True)
    if fmt == "github":
        lines = []
        for row in rows:
            if _unread(row):
                lines.append(f"::error title={settings.TOPIC}::" + settings.escape(f"{row.repo}: {row.error}"))
            for finding in row.findings:
                if finding.status in (FAIL, WARN):
                    level = "error" if finding.status == FAIL else "warning"
                    lines.append(f"::{level} title={finding.id}::" + settings.escape(
                        f"{row.repo}: {finding.title}" + (f": {finding.now}" if finding.now else "")))
        return "\n".join(lines)
    if not rows:
        return f"No repository of {owner} has the topic {settings.TOPIC}"
    names = [row.repo.split("/", 1)[1] for row in rows]
    ids = [id for id in settings.IDS if any(finding.id == id for row in rows for finding in row.findings)]
    id_width, widths = max([len(id) for id in ids] + [2]), [max(len(name), 6) for name in names]
    lines = [f"Catalog standard for {len(rows)} {'repository' if len(rows) == 1 else 'repositories'} of {owner}",
             ("  " + "id".ljust(id_width) + "".join(f" {name:<{width}}" for name, width in zip(names, widths))).rstrip()]
    for id in ids:
        cells = [f" {next((f.status for f in row.findings if f.id == id), '-'):<{width}}" for row, width in zip(rows, widths)]
        lines.append(("  " + id.ljust(id_width) + "".join(cells)).rstrip())
    for row in rows:
        total = ", ".join(f"{number} {status}" for status, number in settings.summary(row.findings).items())
        lines.append(f"{row.repo}: " + (clean(row.error, 300) if row.error else total))
    return "\n".join(lines)


# ---------------------------------------------------------------- the upgrade

def _default_git(args, cwd=None, timeout=300):
    try:
        return gitx.run(args, cwd=cwd, timeout=timeout)
    except gitx.CatalogError as error:
        raise Unusable(error.message)


def _plain_address(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def _today() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


class _Failed(Exception):
    """A step of one repository that did not work: the line that says why."""


class _Work:
    """The seams of one run."""

    def __init__(self, runner, lab_ref, standard, tree, git, remote, workdir, today, lab):
        self.runner, self.lab_ref, self.git, self.remote = runner, lab_ref, git, remote
        self.standard, self.tree, self.workdir, self.today = standard, tree, workdir, today
        self.lab, self.logs = lab, {}               # the Lab checkout that runs this, and its log between two pins, asked once

    def run(self, args, cwd=None, timeout=300) -> str:
        """The output of a git command, or the line of why it failed."""
        result = self.git(args, cwd=cwd, timeout=timeout)
        if result.code != 0:
            if any(words in (result.err or "") for words in NO_IDENTITY):
                raise _Failed("Git has no name and email to commit with. Set user.name and user.email, then run this again.")
            verb = next((a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] != "-c")),
                        "command")
            raise _Failed(f"git {verb} failed: " + clean(gitx.redact(result.err or ""), 300))
        return result.out.decode("utf-8", "replace")


def _open_pull(runner: Callable, repo: str) -> Optional[int]:
    """The number of a pull request of an earlier upgrade that is still open, if there is one: from a branch of this
    repository named standard/..., not from a fork."""
    reply, items = settings.Reads(runner).pages(f"repos/{repo}/pulls?state=open", limit=PULL_LIMIT)
    if reply.code != 200:
        raise _Failed("the open pull requests cannot be listed: " + refusal(reply))
    for item in map(as_dict, items):
        head = as_dict(item.get("head"))
        if isinstance(head.get("ref"), str) and head["ref"].startswith(BRANCH_PREFIX) and \
                as_dict(head.get("repo")).get("full_name") == repo and is_int(item.get("number")):
            return item["number"]
    return None


def _owned(plan) -> List[str]:
    """The paths the plan leaves to the catalog (their marker was removed): the `owned` entries of the plan of 7.4."""
    plan = as_dict(plan)
    entries = plan.get("owned")
    if not isinstance(entries, list):
        entries = [e for e in as_list(plan.get("files")) if isinstance(e, dict) and e.get("action") == "owned"]
    found = [entry if isinstance(entry, str) else as_dict(entry).get("path") for entry in entries]
    return sorted({clean(path, 200) for path in found if isinstance(path, str)})


def _read_standard(folder: Path) -> dict:
    """`.github/standard.json` of the clone as a dict, {} when it is not there or cannot be read. A link is not followed
    out of the clone."""
    target = os.path.realpath(folder / STANDARD_FILE)
    if os.path.dirname(os.path.dirname(target)) != os.path.realpath(folder):
        return {}
    try:
        with open(target, "rb") as handle:
            raw = handle.read(settings.STANDARD_LIMIT + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= settings.STANDARD_LIMIT else {}
    except (OSError, ValueError, RecursionError):      # a file a contributor merged: not JSON, or nested deeper than Python reads
        return {}
    return as_dict(data)


def _changed(work: _Work, folder: Path) -> List[str]:
    # No rename detection: each change is its own entry, and the old name of a rename is not an entry with no status
    out = work.run(["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"], cwd=str(folder))
    return sorted(entry[3:] for entry in out.split("\0") if len(entry) > 3)


def _unmanaged(changed: List[str]) -> Optional[str]:
    """Why an upgrade that changed these paths must stop, or None. It rewrites managed files, and every one of them lives in
    `.github/` or `.catalog/`: the content of the catalog, its `catalog.yml`, its root files and anything else are not
    the upgrade's to change, and a pull request opened with them would have a maintainer approve more than the standard."""
    content = [path for path in changed if path.startswith(KIND_FOLDERS)]
    if content:
        return "the upgrade changed the content of the catalog: " + ", ".join(clean(path, 60) for path in content[:3])
    foreign = [path for path in changed if not path.startswith(MANAGED_ROOTS)]
    if foreign:
        return "the upgrade changed files that are not managed: " + ", ".join(clean(path, 60) for path in foreign[:3])
    return None


def _lab_log(work: _Work, old: str, new: str) -> str:
    """The Lab's own log of `lab-ui` and `.github` between two pins (at most LAB_LOG lines), as an indented block: the
    subjects are words of another repository that this pull request prints, so they are cut, and an indented block is code,
    where nothing mentions a person or links an issue. Empty when this Lab checkout does not hold both commits."""
    if (old, new) not in work.logs:
        text = ""
        try:
            result = work.git(["log", "--oneline", "--no-decorate", "-n", str(LAB_LOG), f"{old}..{new}", "--", "lab-ui", ".github"],
                              cwd=str(work.lab), timeout=30)
        except Unusable:
            result = None
        if result is not None and result.code == 0:
            lines = [clean(gitx.redact(line), 200).strip() for line in result.out.decode("utf-8", "replace").splitlines()]
            text = "\n".join("    " + line for line in [line for line in lines if line][:LAB_LOG])
        work.logs[(old, new)] = text
    return work.logs[(old, new)]


def _words(work: _Work, folder: Path, before: dict) -> tuple:
    """(level, the lines of the message, the log of the Lab) of the pull request, from the standard the upgrade wrote. A
    moved pin brings the link that compares the two commits of the Lab and the Lab's own log between them, which is what a
    reviewer of a pin reads: that code will judge every pull request of the catalog."""
    after = _read_standard(folder)
    level = after.get("standard") if is_int(after.get("standard")) else getattr(work.standard, "LEVEL", None)
    lines = [f"Brings the managed files to catalog standard {level}." if level
             else "Brings the managed files to the Lab's level."]
    old, new, log = as_dict(before.get("lab")), as_dict(after.get("lab")), ""
    lab = new.get("repository")
    if isinstance(lab, str) and settings.REPO_NAME.fullmatch(lab) and COMMIT.fullmatch(str(old.get("ref"))) and \
            COMMIT.fullmatch(str(new.get("ref"))) and old.get("ref") != new.get("ref"):
        lines += [f"Lab pin: {old['ref'][:7]} to {new['ref'][:7]}",
                  f"https://github.com/{lab}/compare/{old['ref']}...{new['ref']}"]
        found = _lab_log(work, old["ref"], new["ref"])
        if found:
            log = f"git log --oneline {old['ref']}..{new['ref']} -- lab-ui .github\n\n{found}"
    return level, lines, log


def _free_branch(work: _Work, url: str, name: str) -> str:
    """`name`, or `name-2`, `name-3`: the first that the remote does not have. A name is never reused."""
    for number in range(1, 10):
        candidate = name if number == 1 else f"{name}-{number}"
        if not work.run(["ls-remote", "--heads", "--", url, f"refs/heads/{candidate}"]).strip():
            return candidate
    raise _Failed(f"the branches {name} to {name}-9 all exist")


def _upgrade(work: _Work, repo: str, branch: str, folder: Path) -> Upgraded:
    url = work.remote(repo)
    # Every object is checked, and no link is made: a link in the clone could lead a write out of it. The setting stays in the
    # clone (--config), so that the commands after it see the links as the files they were made as and change no entry
    work.run(["-c", "transfer.fsckObjects=true", "clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags",
              "--config", "core.symlinks=false", "--branch", branch, "--", url, str(folder)])
    base = work.run(["rev-parse", "HEAD"], cwd=str(folder)).strip()
    before = _read_standard(folder)
    plan = work.standard.upgrade_plan(work.tree(folder), {"lab_ref": work.lab_ref})
    work.standard.upgrade_apply(folder, plan)
    owned = _owned(plan)
    changed = _changed(work, folder)
    if not changed:
        return Upgraded(repo, "current", "", owned)
    stop = _unmanaged(changed)
    if stop:
        raise _Failed(stop)
    level, lines, log = _words(work, folder, before)
    subject = f"Update to catalog standard {level}" if level else "Update to the Lab's catalog standard"
    name = _free_branch(work, url, f"{BRANCH_PREFIX}{level or 'lab'}-{work.today()}")
    work.run(["checkout", "--quiet", "-b", name], cwd=str(folder))
    work.run(["add", "-A", "--", "."], cwd=str(folder))
    # The commit is the maintainer's own, unsigned (a branch is not required to be signed), with no trailer
    notes = "\n".join(lines[1:]) + (("\n\n" if len(lines) > 1 else "") + log if log else "")
    work.run(["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", subject] + (["-m", notes] if notes else []), cwd=str(folder))
    now = work.run(["ls-remote", "--", url, f"refs/heads/{branch}"]).split()
    if not now or now[0] != base:
        return Upgraded(repo, "moved", f"{branch} moved while the upgrade ran; run it again", owned)
    work.run(["push", "--quiet", "--", url, f"HEAD:refs/heads/{name}"], cwd=str(folder))
    reply = settings.as_reply(work.runner("POST", f"repos/{repo}/pulls", {
        "title": subject, "head": name, "base": branch, "body": "\n\n".join(lines + ([log] if log else []))}))
    link = as_dict(reply.body).get("html_url")
    if not 200 <= reply.code < 300 or not isinstance(link, str):
        why = "the plan does not allow it" if plan_limit(reply) else refusal(reply)
        return Upgraded(repo, "failed", f"the branch {name} is pushed and the pull request was refused: {why}", owned)
    return Upgraded(repo, "opened", clean(link, 300), owned)


def _one(work: _Work, entry: dict) -> Upgraded:
    repo, branch = entry["repo"], entry["branch"]
    if entry["archived"]:
        return Upgraded(repo, "skipped", "archived", [])
    try:
        gitx.valid_branch(branch)
    except gitx.CatalogError:
        return Upgraded(repo, "failed", "the default branch has a name that is not safe to use", [])
    try:
        number = _open_pull(work.runner, repo)
    except _Failed as problem:
        return Upgraded(repo, "failed", clean(str(problem), 300), [])
    if number is not None:
        return Upgraded(repo, "open", f"pull request #{number} is still open", [])
    try:
        parent = tempfile.mkdtemp(prefix="gravitee-lab-fleet-", dir=work.workdir)
    except OSError as error:
        return Upgraded(repo, "failed", clean(error.strerror or type(error).__name__), [])
    try:
        return _upgrade(work, repo, branch, Path(parent) / "catalog")
    except _Failed as problem:
        return Upgraded(repo, "failed", clean(str(problem), 400), [])
    except Unusable:
        raise
    except Exception as error:  # noqa: BLE001 - the standard module is another package's: its failure is this repository's
        return Upgraded(repo, "failed", clean(f"{type(error).__name__}: {error}", 300), [])
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def _on_lab_main(work: _Work) -> None:
    """SD11 for the pin the fleet is about to write in every catalog: the commit has to be a release of the Lab, a tag vX.Y.Z on
    its `origin/main`, as the Lab checkout that runs this knows it (`standard.release_state`). A commit that exists only in a fork
    of the Lab, one that no release names, or a typo would otherwise become a pull request in each repository, and the code it
    names would judge every pull request after it."""
    def git(args):
        try:
            return work.git(args, cwd=str(work.lab), timeout=30)
        except OSError:                                # the folder of the Lab is gone: nothing says the commit is a release
            return None
    if release_state(work.lab, work.lab_ref, git=git) != "yes":
        raise Unusable(f"The Lab commit {work.lab_ref[:7]} is not a release of the Lab. Fix: pin the latest release "
                       "(catalog.py upgrade --lab-ref latest).")
    problem = pin_problem(work.lab, work.lab_ref, getattr(work.standard, "LEVEL", 0), git=git)
    if problem:   # the release must ship the standard the fleet writes, or the catalogs' CI calls what it does not have
        raise Unusable(problem)


def upgrade_fleet(owner: str, runner: Callable, lab_ref: str, *, standard=None, tree: Optional[Callable] = None,
                  git: Optional[Callable] = None, remote: Optional[Callable] = None, workdir: Optional[str] = None,
                  today: Optional[Callable] = None, lab: Optional[str] = None) -> List[Upgraded]:
    """One Upgraded for each catalog of `owner`. `lab_ref` is the full commit of the Lab that every catalog is moved to; it
    has to be a release of the Lab checkout that runs this (SD11), or nothing is listed, cloned or
    written and the answer is Unusable. The keywords are the seams of a test: `standard` (a module with `upgrade_plan(tree,
    options)` and `upgrade_apply(root, plan)`, else the Lab's), `tree` (root -> Tree, else FolderTree), `git` (args, cwd,
    timeout -> gitx.Result, else gitx.run), `remote` (OWNER/REPO -> the address git clones, else its https address),
    `workdir` (where the clones are made, else the system's temporary folder), `today` (the date in the name of the branch)
    and `lab` (the Lab checkout, else the one that holds this file)."""
    if not isinstance(lab_ref, str) or not COMMIT.fullmatch(lab_ref):
        raise Usage("--lab-ref takes the full commit of the Lab: 40 hexadecimal characters.")
    work = _Work(runner, lab_ref, standard or standards, tree or FolderTree, git or _default_git,
                 remote or _plain_address, workdir, today or _today, Path(lab) if lab else LAB)
    _on_lab_main(work)
    return [_one(work, entry) for entry in catalogs_of(owner, runner)]


def render_upgrade(results, fmt: str = "text") -> str:
    """One line for each repository: what became of it, and the files the catalog owns that were left as they are. As
    `github` annotations, one for each repository that is not current: an error when it could not be upgraded (the exit
    code says so too), else a notice."""
    if fmt == "json":
        return json.dumps([item._asdict() for item in results], indent=2, sort_keys=True)
    if fmt == "github":
        return "\n".join(
            f"::{'error' if item.status in ('failed', 'moved') else 'notice'} title=upgrade::" + settings.escape(
                f"{item.repo}: {item.status}" + (f" {item.detail}" if item.detail else "")
                + ("; owned files skipped: " + ", ".join(item.owned) if item.owned else ""))
            for item in results if item.status != "current")
    if not results:
        return "No catalog to upgrade"
    width = max(len(item.repo) for item in results)
    lines = []
    for item in results:
        text = f"{item.repo:<{width}}  {item.status}" + (f"  {item.detail}" if item.detail else "")
        if item.owned:
            text += "  owned files skipped: " + ", ".join(item.owned)
        lines.append(text)
    return "\n".join(lines)


def upgrade_exit_code(results) -> int:
    """1 when a repository could not be upgraded (it failed, or its default branch moved); else 0."""
    return 1 if any(item.status in ("failed", "moved") for item in results) else 0


def main(argv: Optional[List[str]] = None, runner: Optional[Callable] = None, stdout=None, stderr=None, **seams) -> int:
    """`catalog.py upgrade --fleet OWNER --lab-ref COMMIT [--format text or json or github]`. Exit 0, 1 when a repository could
    not be upgraded, 2 when it cannot be done: no `gh`, no sign-in, no `standard` module yet, a commit that is not a release
    of the Lab (SD11), a command line that makes no sense. `seams` are those of `upgrade_fleet`."""
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        parser = settings.Parser(prog="catalog.py upgrade", add_help=False)
        parser.add_argument("--fleet", required=True, metavar="OWNER")
        parser.add_argument("--lab-ref", required=True, metavar="COMMIT")
        parser.add_argument("--format", choices=("text", "json", "github"), default="text")
        args = parser.parse_args(argv)
        results = upgrade_fleet(args.fleet, runner or settings.Gh(), args.lab_ref, **seams)
        print(render_upgrade(results, args.format), file=out)
        return upgrade_exit_code(results)
    except SystemExit as leave:
        return leave.code if isinstance(leave.code, int) else 2
    except Unusable as problem:
        print(problem, file=err)
        return 2
