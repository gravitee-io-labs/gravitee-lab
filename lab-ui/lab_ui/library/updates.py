"""The Lab's own versions: which one this Lab is, which ones are newer, what each changed, and the update to the newest.

A version is a tag of the Lab's repository, `v<major>.<minor>.<patch>`, on its main branch. This Lab is the version of the newest tag
its commit holds (`git describe`), with the number of changes made since when it is past it (a clone of main between two versions);
without a tag, a development version, said by its commit. What a version changed is the subjects of the commits between it and the
version before it, on the main branch: the titles of the pull requests merged, each with its number, which links to it when the Lab
comes from GitHub.

The tags are fetched in the background (`start_checking`): once when the Lab UI starts, then every few hours, and when the person
asks (`check`). Nothing waits for the network: the state says what the last fetch found (`describe`), and a Lab that cannot reach its
repository says when it last could.

The update moves the Lab to the newest version, as a job (`update`): a fast-forward of main to the tag (or a checkout of the tag for a
Lab that is on one), then the Lab UI starts again in the same process, on the new code, and does what it does at every start: it
downloads the catalogs the new version lists and moves what it moved. What keeps it from being made here is said (`blocked`): a Lab that is no
Git clone, on another branch, with files of its own changed, or with commits of its own. The person's files (.env, .lab/) are not the
repository's and are never touched.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time

from .. import paths

TAG = re.compile(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
CHECK_EVERY = 6 * 3600   # seconds between two fetches of the tags in the background
FETCH_WAIT = 60          # seconds a fetch may take
LOCAL_WAIT = 15          # seconds a local git command may take
FRESH = 30               # seconds what was read of the repository is kept: the state asks every 3 s
NOTES = 50               # the most changes said of one version
RESTARTED = "GRAVITEE_LAB_RESTARTED"   # set for the Lab UI that an update started again: it opens no browser
GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
PULL = re.compile(r"\s*\(#(\d+)\)\s*$")

RUN = os.urandom(4).hex()   # this run of the Lab UI: the page tells the one an update started again by it
_lock = threading.Lock()
_checked = {"at": None, "error": None, "checking": False}   # the last fetch of the tags: when it ended well, what it said
_cache = {"at": 0.0, "head": None, "value": None}
_thread = None


# ---------------------------------------------------------------------------------------------------------------------
# Git, read only
# ---------------------------------------------------------------------------------------------------------------------

def _git(*args, timeout=LOCAL_WAIT):
    """(exit code, output) of a git command in the Lab's folder: no prompt, no input, no pager; (None, "") when git cannot run."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"}
    try:
        done = subprocess.run(["git", "-C", str(paths.ROOT), *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, (done.stdout if done.returncode == 0 else done.stderr).rstrip()   # a porcelain line starts with a space


def version_of(tag):
    """(major, minor, patch) of a tag of a version, None for any other name."""
    found = TAG.fullmatch(tag or "")
    return tuple(int(part) for part in found.groups()) if found else None


def said(numbers):
    return ".".join(str(part) for part in numbers) if numbers else None


def _cloned():
    return (paths.ROOT / ".git").exists()


def _versions():
    """[(numbers, tag, commit, date)] of the tags of versions, newest first. `date`: when the tag was made (ISO), else its commit's."""
    code, out = _git("for-each-ref", "refs/tags/v*", "--format=%(refname:short)%00%(*objectname)%00%(objectname)%00%(creatordate:iso-strict)")
    found = []
    for line in out.splitlines() if code == 0 else ():
        tag, peeled, obj, date = (line.split("\0") + ["", "", ""])[:4]
        numbers = version_of(tag)
        if numbers:
            found.append((numbers, tag, peeled or obj, date))
    return sorted(found, reverse=True)


def _repository():
    """owner/name of the Lab's repository on GitHub, from its origin; None for another host or no origin."""
    code, out = _git("remote", "get-url", "origin")
    found = GITHUB.match(out.strip()) if code == 0 else None
    return found.group(1) if found else None


def _local():
    """What this Lab is, from its repository: {cloned, commit, branch, base (numbers of the newest tag it holds), tag, ahead
    (changes since), dirty (files of the repository changed here)}."""
    if not _cloned():
        return {"cloned": False}
    code, commit = _git("rev-parse", "HEAD")
    if code != 0:
        return {"cloned": False}
    code, branch = _git("symbolic-ref", "--short", "-q", "HEAD")
    code_tag, tag = _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD")
    base = version_of(tag) if code_tag == 0 else None
    ahead = 0
    if base:
        code, count = _git("rev-list", "--count", f"{tag}..HEAD")
        ahead = int(count) if code == 0 and count.isdigit() else 0
    code, status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = [line[3:].strip() for line in status.splitlines() if len(line) > 3] if code == 0 else []
    return {"cloned": True, "commit": commit, "branch": branch or None, "base": base, "tag": tag if base else None,
            "ahead": ahead, "dirty": dirty}


def _head():
    """What the cache of the state is kept by: the commit, the tags and the last fetch."""
    try:
        head = (paths.ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        head = None
    return head, _checked["at"]


def _read():
    """{local, versions}, read again when it is older than FRESH seconds or the repository moved."""
    now, head = time.monotonic(), _head()
    with _lock:
        if _cache["value"] is not None and _cache["head"] == head and now - _cache["at"] < FRESH:
            return _cache["value"]
    value = {"local": _local(), "versions": _versions() if _cloned() else []}
    with _lock:
        _cache.update(at=now, head=head, value=value)
    return value


def forget():
    with _lock:
        _cache.update(value=None)


# ---------------------------------------------------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------------------------------------------------

def _newest(found):
    return found["versions"][0] if found["versions"] else None


def blocked(found=None, target=None):
    """Why this Lab cannot update itself to `target` (a tag) here, in the words the page shows; None when it can."""
    found = found or _read()
    here = found["local"]
    if not here.get("cloned"):
        return "This Lab is not a Git clone. Download the new version from GitHub"
    if _git("remote", "get-url", "origin")[0] != 0:
        return "This Lab has no origin to update from"
    if here["branch"] not in ("main", None):
        return f"This Lab is on the branch {here['branch']}. Switch to main to update"
    if here["dirty"]:
        shown = ", ".join(here["dirty"][:3]) + (f" and {len(here['dirty']) - 3} more" if len(here["dirty"]) > 3 else "")
        return f"Files of the Lab were changed on this machine: {shown}. Undo or commit them to update"
    if target:
        code, _ = _git("merge-base", "--is-ancestor", "HEAD", target)
        if code == 1:
            return "This Lab has changes of its own that the new version does not have. Update it with Git"
    from ..docker.jobs import JOBS   # here and not above: the jobs read the project, which reads this module's package
    if JOBS.current and not JOBS.current.done:
        return f"Wait for {JOBS.current.title} to end"
    return None


def describe():
    """The Lab's version as the state carries it, read from this machine and the last fetch: {version, ahead, commit, run, latest,
    update, checked, checking, error, blocked}. `update`: a newer version exists; `blocked`: why it cannot be made here; `run`: this
    run of the Lab UI, which an update ends (the files on disk say the new version before the Lab UI runs it)."""
    found = _read()
    here, newest = found["local"], _newest(found)
    base = here.get("base")
    newer = newest is not None and (base is None or newest[0] > base)
    return {"version": said(base), "ahead": here.get("ahead", 0), "commit": (here.get("commit") or "")[:7] or None, "run": RUN,
            "latest": said(newest[0]) if newest else None, "update": bool(here.get("cloned")) and newer,
            "checked": _checked["at"], "checking": _checked["checking"], "error": _checked["error"],
            "blocked": blocked(found, newest[1]) if here.get("cloned") and newer else None}


def _changes(start, end, repository):
    """[{text, number, url}] of the commits between two commits on the first-parent line, newest first: the subjects of the
    merged pull requests, without their number, which is kept apart."""
    span = f"{start}..{end}" if start else end
    code, out = _git("log", "--first-parent", "--no-merges", f"--max-count={NOTES}", "--format=%s", span)
    found = []
    for subject in out.splitlines() if code == 0 else ():
        number = PULL.search(subject)
        text = PULL.sub("", subject).strip()
        if text:
            found.append({"text": text, "number": int(number.group(1)) if number else None,
                          "url": f"https://github.com/{repository}/pull/{number.group(1)}" if number and repository else None})
    return found


def releases():
    """The versions with what each changed, newest first, for the dialog of the Lab: {current: describe(), versions: [{version,
    date, url, newer (than this Lab), current (this Lab's), changes}], repository, url (where all of them are listed)}. The oldest
    version says what it changed since the first commit only when that is little."""
    found = _read()
    repository, versions, base = _repository(), found["versions"], found["local"].get("base")
    shown = []
    for index, (numbers, tag, commit, date) in enumerate(versions):
        before = versions[index + 1][1] if index + 1 < len(versions) else None
        shown.append({"version": said(numbers), "date": date or None, "newer": base is None or numbers > base,
                      "current": numbers == base,
                      "url": f"https://github.com/{repository}/releases/tag/{tag}" if repository else None,
                      "changes": _changes(before, tag, repository) if before else []})
    return {"current": describe(), "versions": shown, "repository": repository,
            "url": f"https://github.com/{repository}/releases" if repository else None}


# ---------------------------------------------------------------------------------------------------------------------
# The fetch of the tags
# ---------------------------------------------------------------------------------------------------------------------

def check():
    """Fetches the tags of the Lab's repository now, and answers describe(). A Lab that is no clone, or has no origin, fetches nothing."""
    with _lock:
        if _checked["checking"]:
            busy = True
        else:
            busy, _checked["checking"] = False, True
    if busy:
        return describe()
    try:
        if _cloned() and _git("remote", "get-url", "origin")[0] == 0:
            code, out = _git("fetch", "--quiet", "--tags", "origin", timeout=FETCH_WAIT)
            with _lock:
                if code == 0:
                    _checked.update(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), error=None)
                else:
                    _checked.update(error=_reason(out))
    finally:
        with _lock:
            _checked["checking"] = False
        forget()
    return describe()


def _reason(out):
    """A fetch that failed, in a line: the network, the sign-in, or git's own last line."""
    text = out.strip().splitlines()[-1] if out and out.strip() else ""
    if re.search(r"Could not resolve host|unable to access|Connection (refused|timed out)|Network is unreachable", out or "", re.I):
        return "The Lab's repository cannot be reached"
    if re.search(r"Authentication failed|could not read Username|Permission denied", out or "", re.I):
        return "The Lab's repository refused this machine's sign-in"
    return text[:200] or "The new versions cannot be read"


def start_checking():
    """The background fetch: now, then every CHECK_EVERY seconds. A daemon thread, once; none with LAB_NO_FIRST_RUN set (the tests)."""
    global _thread
    if os.environ.get("LAB_NO_FIRST_RUN"):
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="lab-updates", daemon=True)
    _thread.start()


def _loop():
    while True:
        try:
            check()
        except Exception:  # noqa: BLE001 - a fetch that fails is said in the state, and tried again later
            pass
        time.sleep(CHECK_EVERY)


# ---------------------------------------------------------------------------------------------------------------------
# The update
# ---------------------------------------------------------------------------------------------------------------------

def _not_now(reason):
    """An update that cannot be made here: its reason, in the page's words."""
    from ..catalogs.registry import CatalogError   # here and not above: the catalogs read this package
    return CatalogError("update_blocked", reason, 409)


def update(version):
    """Starts the job that moves this Lab to `version` ("1.4.0"), the newest; then the Lab UI starts again on its new code.
    `update_blocked` (409) with the reason when it cannot be made here."""
    found = _read()
    wanted = version_of(f"v{version}") if isinstance(version, str) else None
    match = next((one for one in found["versions"] if one[0] == wanted), None)
    if match is None:
        raise _not_now(f"There is no version {version}")
    base = found["local"].get("base")
    if base is not None and match[0] <= base:
        raise _not_now(f"This Lab is {said(base)} already")
    reason = blocked(found, match[1])
    if reason:
        raise _not_now(reason)
    tag, detached = match[1], found["local"]["branch"] is None
    from ..docker.jobs import JOBS, JobFailed

    def move(job):
        job.step(f"Move to {version}")
        job.exec(["git", "checkout", "--quiet", tag] if detached else ["git", "merge", "--ff-only", "--quiet", tag])
        forget()
        # What `make ui` makes before it starts the Lab UI, which a new version may add to; the Lab UI that starts says what is missing
        # when it fails, and it starts on the new code either way
        job.step("Start the Lab UI again")
        if shutil.which("make"):
            try:
                job.exec(["make", "--no-print-directory", "init"])
            except JobFailed as error:
                job.lines.append(f"make init failed: {error}")
        # The Lab UI that starts downloads what the new version lists and moves what it moved, as at every start
        job.lines.append(f"Gravitee Lab {version}: the page reloads once the Lab UI answers")
        threading.Timer(1.5, restart).start()
    return JOBS.start(f"Update Gravitee Lab to {version}", [move], key="lab", verb="update",
                      phases=[f"Move to {version}", "Start the Lab UI again"])


def restart():
    """The Lab UI starts again in this process, on the code on disk now, as it was started; it opens no browser."""
    os.environ[RESTARTED] = "1"
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])
