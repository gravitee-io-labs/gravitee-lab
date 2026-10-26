"""The first start and the one background thread of the Lab.

`reconcile` writes the intent, `.lab/catalogs.json`, from the tracked default file when the file does not exist, and marks
every git catalog that has no copy as downloading, in memory, before any network is touched: the first `/api/state`
already says so. `provision` is the Add pipeline of 9.1 with no ticket and no preview, for a catalog the engine lists: the
tracked address (and, when that is refused, its ssh form), the audience, a fetch that reports how far it is, the checks
that the repository is the catalog expected, the newest version this Lab reads, the stage and `install.commit_add`.
`start_worker` runs the thread: it downloads what has no copy, retries a failure at 15 s, 1 min, 5 min and then every 30
min (a click on Try again starts the schedule again), runs the second part of the migration of an old layout right after the
default catalog is committed and after a Remove (the person may have decided to have no default catalog), and then makes the
background check of 9.2 (`service.check_all`) on its schedule.

Three rules run through it. A download never holds the catalogs lock for its network steps: git runs with no lock, and
only `install.commit_add` takes them, for the milliseconds of the commit. Nothing it runs can ask a question: git is run
by `gitx`, which gives it no terminal, no askpass and no credential prompt, so a credential manager never opens a
window at start. And what the page shows does not flicker: the state `failed` stays from one automatic retry to the next
(`PROGRESS` holds only an attempt a person can see, the first one and the ones a click starts), and the only change a
person sees is the rows.

`content` is the field `state.content`, computed from the disk and from memory, never from the network. A failed
attempt writes its code in the lock (`error`) and drops the flag of the commit it began (`pending`), which is what the
Catalogs row and the content read, then forgets its progress, so that the state goes from downloading to failed with
nothing between, and a failure never leaves a row at Downloading with no way out."""

import os
import random
import re
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from .. import paths
from . import defaults, gitx, install, manifest, registry
from . import store as bare
from .registry import CatalogError

# id -> {"phase": str, "fraction": float}: the attempts a person can see, in memory. An entry is put by `reconcile` (the
# first start, a click on Try again, a catalog added from the list of those not added) and taken away by the attempt that
# ends. The Catalogs row, the content status and `catalog.py provision` read it
PROGRESS = {}
_progress = threading.Lock()   # the only shared state the worker writes

OPT_OUT = "LAB_NO_FIRST_RUN"   # set to 1: no intent written, no thread (for tests and tools)

PROBE = 20.0   # seconds ls-remote may take
STALL, CAP = 30.0, 600.0   # a fetch ends after this many seconds without a byte, and after this many in all
SCHEDULE = (15.0, 60.0, 300.0)   # seconds before the first, second and third automatic retry of a failure
LATER = 1800.0   # then every 30 minutes
FIRST_CHECK = 5.0   # seconds after the start before the first check of the installed catalogs
CHECK_EVERY, JITTER = 6 * 3600.0, 600.0   # then every six hours, with up to ten minutes added
NAP = 60.0   # the longest the worker sleeps: it then looks again at what has no copy, in case a person edited the file
# Where each phase of a download starts and ends in the number the bar draws: the probe, the fetch (git's own
# percentage fills it), the stage (the judge of 9.1 is the project's, when it reads what was installed) and the commit
PHASES = {"probe": (0.02, 0.05), "fetch": (0.05, 0.85), "stage": (0.85, 0.97), "commit": (0.97, 1.0)}
# The refusals of one form of an address that the other form may not have: a private repository over https is a 404 or a
# demand for a name, and over ssh a missing key or an unknown host
REFUSED = ("no_access", "not_found", "ssh_denied", "host_key")

# The lines of the first start that are not the ones of the catalogue of errors: nobody asked for this catalog, so "then add it again" would be
# wrong. Every other line is the catalogue of errors', from `gitx.fail`, `install` and `manifest`: each string exists once
LINES = {
    "git_missing": "Git is not installed. Install it, then try again.",
    "git_old": "Git{have} is too old. Install Git 2.25 or later, then try again.",
    "no_readable_version": "The catalog needs a newer Lab. Run make update, then restart the Lab.",
    "lab_old": "The catalog is not ready for this version of the Lab. Try again later.",
    "failed": "The catalog could not be downloaded. Try again.",
}
REWORDED = ("git_missing", "git_old", "no_readable_version", "no_access")   # what a failure of the first start words again
# The codes that read as another one on a first start. GitHub answers 404 to a private repository; the address is the
# engine's own, so "check the address" would send a person the wrong way
ALIASES = {"not_found": "no_access", "lab_new": "no_readable_version", "schema_new": "no_readable_version"}

_lines = {}   # id -> (code, line) of the last failure of this process: the precise line, with the version of Git and the like


def opted_out() -> bool:
    """Whether LAB_NO_FIRST_RUN says to do nothing at start."""
    return os.environ.get(OPT_OUT, "").strip().lower() in ("1", "true", "yes", "on")


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def delay(failures: int) -> float:
    """The seconds to wait after the `failures`th failure in a row before the next automatic attempt: 15 s, 1 min, 5 min,
    then 30 min each time."""
    if failures < 1:
        return 0.0
    return SCHEDULE[failures - 1] if failures <= len(SCHEDULE) else LATER


# ---------------------------------------------------------------- what has no copy

def _waiting() -> List[dict]:
    """The git catalogs of this machine that have no copy yet, in the order they were added: what a download is for."""
    held = registry.read_lock()["catalogs"]
    return [entry for entry in registry.entries() if entry["kind"] == "git" and not (held.get(entry["id"]) or {}).get("pin")]


def _intent(row: dict) -> dict:
    """The entry of catalogs.json for a catalog the file lists: its id and address, no pin yet, checked
    automatically. No allowance is written for it: a listed catalog has it by being listed."""
    entry = {"id": row["id"], "kind": "git", "url": row["url"]}
    if row.get("branch"):
        entry["branch"] = row["branch"]
    entry.update(auto=True, added=_utc())
    return entry


def _mark() -> None:
    """Puts every git catalog with no copy in `PROGRESS` as being probed, unless it is there already (an attempt that is
    running keeps its place on the bar), and forgets the failure an earlier run left in the lock, so that the row says
    Downloading as the content does."""
    for entry in _waiting():
        ns = entry["id"]
        with _progress:
            new = ns not in PROGRESS
            if new:
                PROGRESS[ns] = {"phase": "probe", "fraction": PHASES["probe"][0]}
        if new and registry.lock_of(ns).get("error"):
            try:
                registry.set_lock(ns, error=None)
            except (CatalogError, OSError, ValueError):
                pass   # another process holds the lock: the attempt that follows says what it finds


def reconcile() -> None:
    """Writes `.lab/catalogs.json` from the tracked default file when it does not exist, and marks the catalogs
    with no copy as downloading. Synchronous, and nothing but local files: no git, no network. A file that exists is the
    person's decision, readable or not (`read_config` is None only for a file that is not there): nothing is added to it
    and nothing is removed, and a damaged one is never taken for a first run. A tracked file that cannot be read lists
    nothing, and writing an empty list then would be the opt-out without anyone asking: the file is left alone,
    and the next start with a good one is a first run. Does nothing with LAB_NO_FIRST_RUN set."""
    if opted_out():
        return
    listed = defaults.read()
    if listed and registry.read_config() is None:
        with registry.flock():
            if registry.read_config() is None:   # no one wrote it while this waited for the lock
                registry.write_config({"schema": registry.SCHEMA, "catalogs": [_intent(row) for row in listed]})
    _mark()


# ---------------------------------------------------------------- what the page says

def _set(ns: str, phase: str, step: float = 0.0) -> None:
    """Moves the bar of a visible attempt to `step` of the way through `phase`; it only grows, and a value below where
    it is changes nothing. An attempt nobody can see (an automatic retry) writes nothing."""
    start, end = PHASES[phase]
    value = round(start + (end - start) * min(max(step, 0.0), 1.0), 4)
    with _progress:
        held = PROGRESS.get(ns)
        if held is not None and value >= (held.get("fraction") or 0.0):
            PROGRESS[ns] = {"phase": phase, "fraction": value}


def _have(message: str) -> str:
    """The version of Git that a git_old line names, as ' 2.20.1', or nothing."""
    found = re.match(r"Git (\S+) is too old", message or "")
    return f" {found.group(1)}" if found else ""


def line_of(code: str, address: Optional[gitx.Address] = None, have: str = "", branch: str = "") -> str:
    """The line a person reads for a failure of the first start, from its code alone: the catalogue of errors', except the
    ones `LINES` words for a catalog nobody asked for. `address` names the repository and the host in it, `have` the
    version of Git and `branch` the branch asked, the two facts a line can need. A code whose line needs a fact that was
    not given, or one that only the attempt knew (what was wrong in a catalog.yml), reads as the general line."""
    code = ALIASES.get(code, code)
    if code == "git_old":
        return LINES["git_old"].format(have=have)
    if code in LINES:
        return LINES[code]
    if code == "wrong_catalog":
        return install.not_expected(address.repo or address.display if address else None).message
    if code == "branch_missing":
        return gitx.fail(code, address, branch=branch).message if branch else LINES["failed"]
    if code in gitx.MESSAGES:
        return gitx.fail(code, address).message
    return LINES["failed"]


def _worded(error: CatalogError, address: Optional[gitx.Address]) -> CatalogError:
    """`error` in the words of the first start when the first start words it differently from the catalogue of errors (no Git, an old Git, a
    catalog that needs a newer Lab, no access); otherwise `error` itself. The same code, status and facts."""
    if ALIASES.get(error.code, error.code) not in REWORDED:
        return error
    return CatalogError(error.code, line_of(error.code, address, _have(error.message)), error.status, **error.detail)


def _address(entry: dict) -> Optional[gitx.Address]:
    """The address a catalog entry is read at, or None when it is not one."""
    url = defaults.effective(entry)[0]
    try:
        address = gitx.parse_address(url) if isinstance(url, str) else None
    except CatalogError:
        return None
    return address if address is not None and address.kind == "git" else None


def _failure(ns: str, held: dict) -> Optional[str]:
    """The code of the failure a catalog with no copy is in, None when it has not failed: the lock's `error`, else, while
    a commit is under way (`pending`; `install.commit_add` clears `error` when it sets it), the failure this process
    remembers, which is what a silent retry that reached the commit is: still the failure it was, for the milliseconds
    of the commit. A click clears `error` with no commit under way: that is a download, as the page was told."""
    one = held.get(ns) or {}
    said = _lines.get(ns)
    return one.get("error") or (said[0] if said and one.get("pending") else None)


def content(project: dict, inputs: dict) -> dict:
    """What the state says of the content, from the disk and from memory, never from the network:
    {key, line, code, progress, address, offer}. `key` is `ready` when a catalog has a copy or a folder catalog is there;
    else `downloading` while a git catalog has an attempt a person can see, or has not failed yet (one that is under way,
    or that waits for its turn); else `failed` for the first catalog that did, with its `line`, `code` and `address`;
    else `none`. An automatic retry leaves the state `failed`, because it leaves no mark in `PROGRESS` and a failure
    stays one through its commit (`_failure`); a failure whose flags were left in the lock never reads as a download.
    `progress` is how far the first download is (0 to 1, or None). `offer` is the first listed catalog the person does not
    have, {id, name}, for the page to offer; None when there is none. Local items count for nothing here."""
    entries = registry.entries()
    held = registry.read_lock()["catalogs"]
    with _progress:
        seen = {ns: dict(one) for ns, one in PROGRESS.items() if isinstance(one, dict)}
    answer = {"key": "none", "line": "", "code": None, "progress": None, "address": "", "offer": None}
    folders = [entry for entry in entries if entry["kind"] == "folder"]
    rows = inputs.get("catalogs") if isinstance(inputs, dict) and isinstance(inputs.get("catalogs"), dict) else None
    if folders and rows is None:
        rows = registry.inputs()
    here = any((rows or {}).get(entry["id"], {}).get("state") == "linked" for entry in folders)
    git = [entry for entry in entries if entry["kind"] == "git"]
    copies = any((held.get(entry["id"]) or {}).get("pin") for entry in git)
    waiting = [entry for entry in git if not (held.get(entry["id"]) or {}).get("pin")]
    running = [entry for entry in waiting if entry["id"] in seen or not _failure(entry["id"], held)]
    failed = [entry for entry in waiting if entry not in running]
    if copies or here:
        answer["key"] = "ready"
    elif running:
        found = running[0]
        answer.update(key="downloading", address=_shown(found), progress=_fraction(seen, running))
    elif failed:
        found = failed[0]
        error = _failure(found["id"], held)
        said = _lines.get(found["id"])
        answer.update(key="failed", code=error, address=_shown(found),
                      line=said[1] if said and said[0] == error
                      else line_of(error, _address(found), branch=defaults.effective(found)[1] or ""))
    offer = defaults.not_added()
    if offer:
        answer["offer"] = {"id": offer[0]["id"], "name": offer[0]["name"]}
    return answer


def _shown(entry: dict) -> str:
    """The address of a catalog as a person reads it: github.com/owner/repo."""
    return registry.display_address(defaults.effective(entry)[0] or "")


def _fraction(seen: dict, running: List[dict]) -> Optional[float]:
    """How far the first download that has a number is, else None."""
    for entry in running:
        value = (seen.get(entry["id"]) or {}).get("fraction")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


# ---------------------------------------------------------------- one download

def _contract() -> dict:
    """The level this Lab reads, from the core file as the project read it; 1 and 1 when it says nothing."""
    from ..library import project   # here and not above: the project reads this package
    found = project.loaded()[1].get("contract")
    return found if isinstance(found, dict) else {"contract": 1, "contract_min": 1}


def _anonymous(address: gitx.Address) -> str:
    """"public" when a stranger's ls-remote answers, else "private": an ssh address and any doubt are private."""
    try:
        gitx.ls_remote(address, None, anonymous=True, timeout=PROBE)
    except (CatalogError, OSError):
        return "private"
    return "public"


def _forms(entry: dict, address: gitx.Address, held: dict) -> List[gitx.Address]:
    """The addresses to ask, in the order: the tracked one, then for a listed catalog its ssh form, or the other
    way round when the ssh form answered last time (`via`)."""
    again = gitx.ssh_form(address) if defaults.listed(entry) else None
    if again is None:
        return [address]
    return [again, address] if held.get("via") == "ssh" else [address, again]


def _reach(forms: List[gitx.Address], branch: Optional[str]):
    """(the address that answered, what ls-remote said, 20 s each). A form that was refused (REFUSED) is followed by the
    next; any other failure ends the asking. When none answers the failure is that of the tracked https form, which is
    the person's: a missing ssh key is not what to tell someone who never chose ssh."""
    failures = []
    for form in forms:
        try:
            return form, gitx.ls_remote(form, branch, timeout=PROBE)
        except CatalogError as error:
            failures.append((form, error))
            if error.code not in REFUSED:
                break
    raise next((error for form, error in failures if not gitx.is_ssh(form)), failures[0][1])


def _forget_stage(ns: str) -> None:
    shutil.rmtree(registry.stage_dir(ns), ignore_errors=True)


def _stand_in(ns: str, entry: Optional[dict], row: Optional[dict]) -> dict:
    """The entry a catalog is read at: its own in catalogs.json, or for a listed catalog the person does not have the one
    its commit will write (`not_listed` for any other)."""
    if entry is not None:
        return entry
    if row is None:
        raise CatalogError("not_listed", f"{ns} is not a catalog this Lab lists.", 400)
    return {"id": ns, "kind": "git", "url": row["url"], **({"branch": row["branch"]} if row.get("branch") else {})}


def _remember(ns: str, address: gitx.Address, answered: gitx.Address) -> None:
    """Which form of a listed address answered (`via`), in the lock, so that the next run starts with it. Only
    for a catalog that has an entry (a lock entry with no catalog is garbage), and only when there were two forms."""
    via = "ssh" if gitx.is_ssh(answered) else "https"
    if gitx.ssh_form(address) is not None and registry.entry(ns) is not None and registry.lock_of(ns).get("via") != via:
        registry.set_lock(ns, via=via)


def _download(ns: str, entry: Optional[dict], row: Optional[dict]) -> None:
    """The download and install of a catalog with no copy. `entry` is its entry in catalogs.json (None for a listed catalog
    the person does not have, whose entry `install.commit_add` writes) and `row` the file's row for it (None when the
    catalog is not listed)."""
    stand_in = _stand_in(ns, entry, row)
    address = _address(stand_in)
    if address is None:
        raise gitx.fail("bad_address")
    listed = row is not None
    branch = defaults.effective(stand_in)[1]
    _set(ns, "probe", 0.0)
    gitx.require()   # no git, or one too old, is known before anything is asked of the network
    forms = _forms(stand_in, address, registry.lock_of(ns))
    answered, remote = _reach(forms, branch)
    if listed:
        _remember(ns, address, answered)
    _set(ns, "probe", 0.5)
    audience = _anonymous(answered)
    _set(ns, "probe", 1.0)
    own = entry is not None
    store_path = registry.store_dir(ns) if own else registry.new_store_dir(secrets.token_hex(8))
    try:
        tip = bare.fetch(answered, remote["branch"], store_path, stall=STALL, cap=CAP,
                         progress=lambda fraction: _set(ns, "fetch", fraction))
        roots = bare.roots(store_path, tip)
        if row is not None and row.get("roots") and sorted(row["roots"]) != sorted(roots):
            shutil.rmtree(store_path, ignore_errors=True)   # another repository: nothing of it is worth keeping
            raise install.not_expected(address.repo or address.display)
        contract = _contract()
        offered = manifest.offer(store_path, tip, contract["contract"], contract["contract_min"], answered,
                                 readable=install.readable(store_path))
        commit, tip_state = offered.commit, offered.tip
        if commit is None:
            raise manifest.no_commit(offered.levels, contract["contract"], contract["contract_min"], listed)
        kept, ignored = bare.selected(bare.listing(store_path, commit))
        _set(ns, "stage", 0.0)
        staged = install.stage(answered, ns, store_path, commit, kept, ignored)
        try:
            meta = _read(staged, answered, contract, ns, listed)
            _set(ns, "stage", 1.0)
            _set(ns, "commit", 0.0)
            install.commit_add(staged, address, remote["branch"], audience, roots, meta=meta, host=False, auto=True)
        except Exception:
            _forget_stage(ns)
            raise
    except Exception:
        if not own:
            shutil.rmtree(store_path, ignore_errors=True)   # a store no catalog owns is garbage
        raise
    try:   # the catalog is installed: a note that cannot be written is the first check's to make, not a failure
        if listed:
            _remember(ns, address, answered)
        if commit != tip or tip_state != "ok":   # the tip needs a newer Lab: the row says so at once (9.2 case B)
            registry.set_lock(ns, seen={"target": commit, "update": False, "tip": tip_state, "version": meta["version"]})
    except (CatalogError, OSError, ValueError):
        pass
    _set(ns, "commit", 1.0)


def _read(staged, address: gitx.Address, contract: dict, ns: str, listed: bool) -> dict:
    """catalog.yml of the staged tree, read as an Add reads it and checked to be the catalog expected: this
    Lab reads its legacy level and every key of it (a key it does not know needs a newer Gravitee Lab), and its `id` is the
    one asked for (`wrong_catalog`)."""
    here = [one["id"] for one in defaults.read() if registry.display_address(one["url"]) == address.display]
    borrowed = [one["name"] for one in defaults.read() if one["id"] not in here]
    meta = manifest.read_catalog(os.path.relpath(Path(staged.path) / manifest.FILE, paths.ROOT), address, borrowed)
    refusal = manifest.compat_error(meta["lab"], contract["contract"], contract["contract_min"], listed)
    if refusal is not None:
        raise refusal
    if meta["unknown"]:
        raise manifest.not_understood([("catalog.key", key) for key in meta["unknown"]])
    if meta["id"] != ns:
        raise install.not_expected(address.repo or address.display)
    return meta


def _ending(error: BaseException) -> bool:
    """Whether a refusal is git not being run because the Lab UI is ending: nothing about the catalog."""
    return gitx.ENDING in str((getattr(error, "detail", None) or {}).get("output") or "")


def _record(ns: str, error: CatalogError) -> None:
    """The failure of an attempt in the lock (`error`), where the Catalogs row and the content read it, and in the same
    write the flag of the commit it began (`pending`, set at step 14b): an attempt that failed after it is over, and a
    flag left standing would keep the row at Downloading for good, with the failure hidden behind it. Nothing for a
    catalog with no entry, for one that has a copy by now (another process finished what this one failed at: a stale
    failure would mark a good install), and nothing while the Lab UI ends (the flag then stays, for `recover()`). What
    a failed commit left in the install folder is the next attempt's to set aside (`install._target`) and the stage is
    forgotten already, so the flag has nothing left to say."""
    if registry.entry(ns) is None or registry.lock_of(ns).get("pin") or _ending(error):
        return
    code = error.code if isinstance(error.code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error.code) else "failed"
    try:
        registry.set_lock(ns, error=code, pending=None)
    except (CatalogError, OSError, ValueError):
        pass   # the lock is held by another process, or cannot be written: the attempt still raises what it found


def _row(ns: str) -> dict:
    """The row of a catalog as the state draws it, from the disk alone. The install is done when this is asked, so a
    row that cannot be drawn is no failure of the download: the little one the disk gives is returned instead."""
    try:
        from . import service   # here and not above: the worker calls this module
        return next(one for one in service.describe({}, {}) if one.get("id") == ns and one.get("kind") != "progress")
    except Exception:  # noqa: BLE001 - see above
        held = registry.lock_of(ns)
        found = registry.inputs().get(ns) or {}
        return {"id": ns, "kind": "git", "name": found.get("name") or ns, "version": found.get("version"),
                "commit": (held.get("pin") or "")[:7] or None, "error": held.get("error"),
                "state": {"key": found.get("state") or "uptodate"}}


def provision(ns: str) -> dict:
    """The Add pipeline with no ticket and no preview, for a catalog with no copy, and the row of it once it has
    one. The entry is the one the default file wrote, or a listed catalog the person does not have, whose entry the commit writes.
    Nothing happens for a catalog that has a copy. A failure is a CatalogError in the words of the first start, written in the lock
    too (`error`), where the row and the content read it. An automatic retry shows nothing meanwhile: `PROGRESS` holds
    only the attempts a person can see. The network steps hold no lock; `install.commit_add` takes them for the
    commit, and its write of the lock (step 16) is the commit point."""
    if not isinstance(ns, str) or not re.fullmatch(paths.NS, ns):
        raise ValueError(f"not a catalog id: {ns!r}")
    try:
        entry = registry.entry(ns)
        if entry is not None and (entry["kind"] != "git" or registry.lock_of(ns).get("pin")):
            return _row(ns)   # a folder has nothing to download, and a copy is a copy
        row = next((one for one in defaults.read() if one["id"] == ns), None)
        stand_in = _stand_in(ns, entry, row)
        try:
            _download(ns, entry, row)
        except CatalogError as error:
            if error.code == "already_added":   # another process finished it while this fetched
                return _row(ns)
            failure = _worded(error, _address(stand_in))
            _lines[ns] = (failure.code, failure.message)
            _record(ns, failure)
            raise failure from None
        except OSError as error:
            failure = CatalogError("failed", LINES["failed"], 500, why=type(error).__name__)
            _lines[ns] = (failure.code, failure.message)
            _record(ns, failure)
            raise failure from error
        except Exception:
            _lines[ns] = ("failed", LINES["failed"])
            _record(ns, CatalogError("failed", LINES["failed"], 500))
            raise
        _lines.pop(ns, None)
        _arrived()
        return _row(ns)
    finally:
        with _progress:
            PROGRESS.pop(ns, None)


def _arrived() -> None:
    """What follows a catalog that arrived: the two Compose flags in `.env` and a project read again, since
    the files it reads changed."""
    from ..library import layers, project   # here and not above: they read this package
    try:
        layers.prepare_catalogs()
    except (OSError, ValueError):   # a .env that is not UTF-8 is a ValueError
        pass   # the .env is for a docker compose typed by hand: the catalog is added all the same
    project.forget()


# ---------------------------------------------------------------- the thread

class Worker:
    """The one background thread of the Lab: downloads what has no copy, finishes the migration of an old layout,
    and makes the background check. Sequential, so an attempt is never started while another runs. `tick` does what is due
    now and answers the seconds until something is due again; `run` is the loop. The clock is a parameter so that a test
    drives the schedule without waiting."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.stop = threading.Event()
        self.nudge = threading.Event()
        self.thread = None
        self.failures = 0   # attempts that failed in a row, for the schedule
        self.retry_at = None   # when the next automatic attempt is due, None while none is waiting
        self.check_at = clock() + FIRST_CHECK
        self.first = True   # the first pass: the layout is looked at once even when nothing arrives
        self.arrived = False   # the default catalog arrived since the layout was looked at
        self._clicked = False
        self._removed = False   # a catalog was removed since the layout was looked at
        self._mutex = threading.Lock()

    def wake(self) -> None:
        """A click on Try again: the schedule starts again, and what has no copy is attempted now."""
        with self._mutex:
            self._clicked = True
        self.nudge.set()

    def removed(self) -> None:
        """A catalog was removed: the layout is looked at again at once. The one that waits for the default catalog is settled by
        the person's decision to have none, and nothing else would make the move before the next start."""
        with self._mutex:
            self._removed = True
        self.nudge.set()

    def _click(self) -> bool:
        with self._mutex:
            clicked, self._clicked = self._clicked, False
        return clicked

    def _download(self, clicked: bool) -> None:
        """One round: every catalog with no copy, in order, when none is waiting for its turn."""
        waiting = [entry["id"] for entry in _waiting()]
        if not waiting:
            self.failures, self.retry_at = 0, None
            return
        if self.retry_at is not None and self.clock() < self.retry_at and not clicked:
            return
        if clicked:
            _mark()   # a click is seen, whatever called it
        failed = False
        for ns in waiting:
            if self.stop.is_set():
                return
            if registry.entry(ns) is None:
                continue   # removed while an earlier one downloaded: provision would take it for an Add and bring it back
            try:
                provision(ns)
                if ns == paths.default_ns():   # the move waits for this catalog alone: not for the downloads that follow it
                    self.arrived = True
                    self._move()
            except CatalogError as error:
                if _ending(error):
                    self.stop.set()
                    return
                failed = True
            except Exception as error:  # noqa: BLE001 - a fault of this attempt is its failure, said once, and retried
                print(f"Gravitee Lab UI: could not download {ns} ({type(error).__name__}: {error})", file=sys.stderr)
                failed = True
        if failed:
            self.failures += 1
            self.retry_at = self.clock() + delay(self.failures)
        else:
            self.failures, self.retry_at = 0, None

    def _move(self) -> None:
        """The second part of the migration of an old layout once the default catalog is there, or once the person
        removed a catalog (it may be that one: the move then runs with none). The first part ran at start, offline. It
        raises nothing for a disk or a registry that failed: the report says so, and so does the status of the layout, whose
        Try again is the person's (a route of its own); the lines the report holds are said once, as `layout.ensure` says them
        at start."""
        with self._mutex:
            removed, self._removed = self._removed, False
        if not (self.first or self.arrived or removed):
            return
        self.first = self.arrived = False
        try:
            from ..library import layout   # here and not above: it reads this package
            if layout.waiting():
                report = layout.migrate()
                for line in (report.get("problems") if isinstance(report, dict) else None) or ():
                    if line != layout.FAILED_LINE:   # `migrate` has said that one, with its reason
                        print(f"Gravitee Lab UI: {line}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - the layout says it failed in its status; the Lab goes on
            print(f"Gravitee Lab UI: could not move the settings ({type(error).__name__}: {error})", file=sys.stderr)

    def _check(self) -> None:
        """The background check: first 5 seconds after the start, then every six hours, with up to ten minutes
        more so that Labs do not all ask at once."""
        if self.clock() < self.check_at:
            return
        try:
            from . import service   # here and not above: it reads this package
            service.check_all()
        except Exception as error:  # noqa: BLE001 - one pass failing is not the thread's end
            print(f"Gravitee Lab UI: could not check the catalogs ({type(error).__name__}: {error})", file=sys.stderr)
        self.check_at = self.clock() + CHECK_EVERY + random.uniform(0, JITTER)

    def tick(self) -> float:
        """What is due now, and the seconds until something is due again."""
        clicked = self._click()
        if clicked:
            self.failures, self.retry_at = 0, None
        self._download(clicked)
        if not self.stop.is_set():
            self._move()
            self._check()
        now = self.clock()
        due = [NAP, self.check_at - now] + ([self.retry_at - now] if self.retry_at is not None else [])
        return max(min(due), 0.0)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                wait = self.tick()
            except Exception as error:  # noqa: BLE001 - the thread outlives a fault in one pass
                print(f"Gravitee Lab UI: the download of catalogs failed ({type(error).__name__}: {error})", file=sys.stderr)
                wait = NAP
            self.nudge.wait(max(wait, 0.01))
            self.nudge.clear()

    def end(self, timeout: float = 5.0) -> None:
        """Stops the loop and waits for the thread (for a test, and for whoever needs the Lab to stop at once). An attempt
        that runs ends when its git does."""
        self.stop.set()
        self.nudge.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)


_worker = None
_started = threading.Lock()


def start_worker() -> None:
    """Starts the one background thread of the Lab, a daemon: it provisions what has no copy, runs the
    second part of the migration, and then makes the background check. A second call does nothing while the thread
    runs. Nothing with LAB_NO_FIRST_RUN set."""
    global _worker
    if opted_out():
        return
    with _started:
        if _worker is not None and _worker.thread is not None and _worker.thread.is_alive():
            return
        worker = Worker()
        worker.thread = threading.Thread(target=worker.run, name="lab-first-run", daemon=True)
        _worker = worker
        worker.thread.start()


def stop_worker(timeout: float = 5.0) -> None:
    """Ends the thread `start_worker` started, if it runs."""
    with _started:
        worker = _worker
    if worker is not None:
        worker.end(timeout)


def wake() -> None:
    """A click on Try again resets the retry schedule: the worker looks at what has no copy at once. Nothing without a
    worker."""
    worker = _worker
    if worker is not None:
        worker.wake()


def removed() -> None:
    """A catalog was removed: while the old layout waits for the default catalog, the person may just have decided to have none,
    and the worker makes the move at once. Nothing without a worker (the next start looks at the layout)."""
    worker = _worker
    if worker is not None:
        worker.removed()
