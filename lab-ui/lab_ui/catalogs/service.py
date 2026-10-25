"""What the API calls to work with catalogs: the rows of the state, the Add of an address and of a listed
catalog, Check again and the background pass, Update, Go back, Remove, Repair, the allowance of items that use this machine, the
gate at Start, and the folder catalog the Lab makes from its template. The route handlers are thin; the logic is here.

Three rules run through it. `describe` reads local files and memory only, never git, the network or Docker, because every state
poll calls it. A change of catalogs takes the catalogs lock for a moment (`_changing`): a second change at the same time answers
`busy`, it does not wait for the first; the network steps of a check hold no lock at all. A ticket is the memory of what a
check showed (a staged tree, a plan): it lives in this process for ten minutes and is spent by the change it is for, so a restart
or a quarter of an hour makes it `ticket_expired`. The module starts no thread: the one background thread of the Lab is
`firstrun.start_worker`, which calls `check_all` on its schedule.

What the Add dialog and the Update dialog show is judged by the same policy as the units that are installed: the staged candidate
is read as the project reads a catalog (screen, closure, Compose with the core, quarantine) and put through `project`'s own judge
as if it were installed, so the preview and the state after the Add agree."""

import calendar
import contextlib
import copy
import os
import re
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path

from ..docker import compose, jobs
from ..library import layers, model, moves, project
from .. import paths
from . import defaults, gitx, install, manifest, registry, resolve, trust
from . import store as bare
from .registry import CatalogError

TICKETS = {}   # ticket -> what a check showed, for the change it is for: {kind, made, ...}
TTL = 600.0   # seconds a ticket lives: ten minutes
GIT_AGE = 30.0   # seconds the version of git is kept: it is one more process for every poll
CHECK_CAP = 60.0   # seconds a check of an installed catalog may take
LONG_DOWNLOAD = 10.0   # seconds after which a download lights the dot of the menu
PATIENCE = 1.5   # seconds a change waits for the lock before it is `busy`: a write of the lock lasts milliseconds, a change seconds
WAIT_STAGE = 30.0   # seconds a second Add waits for the first to finish staging and judging its tree
MAX_TICKETS = 8   # checks that are kept at once: a tree of up to 100 MB is staged for each, so an eager script fills no disk
NAME_CUT = 80   # characters of an author's name in a line
DIFF_FILES = 200   # files whose diff "Show changes" computes; the others are listed and not shown
DIFF_BYTES = 1 << 20   # characters of diff it answers in all

EXPIRED = "This took too long. Start again."
NO_PREVIOUS = "No previous version."
UP_TO_DATE = "Already up to date."

_lock = threading.Lock()   # TICKETS and the memory below
_staging = threading.Lock()   # one tree is staged and judged at a time: the folder of a stage is named by the catalog
_checking = {}   # ticket -> how far the fetch of an Add check is (0..1 or None): state.catalogs[] says it
_started = {}   # catalog -> when a download was first seen under way (monotonic): the menu dot lights after a while
_known = {"at": None, "version": None}   # the version of git and when it was asked
_creating = threading.Lock()   # one new catalog at a time: its id is chosen from the ones that are there, and a folder that fails is taken away
_asking = threading.Lock()   # one question to git at a time, and not under the lock of the tickets: git may be slow


def _now():
    return time.monotonic()


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short(commit):
    return commit[:7] if isinstance(commit, str) and commit else None


class Refused(CatalogError, RuntimeError):
    """A refusal at the gate of a start: a CatalogError with its code and a RuntimeError for the callers that expect the
    gate of `layers.check` to raise one."""


def _text(value, field):
    if not isinstance(value, str):
        raise ValueError(f"The {field} must be text")
    return value


# ---------------------------------------------------------------------------------------------------------------------
# The version of git
# ---------------------------------------------------------------------------------------------------------------------

def git_version(fresh=False):
    """The version of git as gitx.version() finds it, kept for GIT_AGE seconds: the state asks at every poll and it is one
    process. A click that may have changed it (Try again, Add, Check again) asks `fresh`."""
    with _asking:
        if fresh or _known["at"] is None or _now() - _known["at"] > GIT_AGE:
            _known.update(at=_now(), version=gitx.version())
        return _known["version"]


# ---------------------------------------------------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------------------------------------------------

def _remove_stage(ns):
    """The staged tree of a catalog (a folder, or the link a folder catalog is staged as), removed. Nothing here raises: a
    leftover is the sweeper's (rule 12 of)."""
    with contextlib.suppress(OSError, ValueError):
        where = registry.stage_dir(ns)
        if where.is_symlink():
            where.unlink()
        else:
            shutil.rmtree(where, ignore_errors=True)


def _discard(data, stage=True):
    """What a ticket held on disk goes with it: the staged tree of an Add or of an Update (not when `stage` is false: the
    ticket that replaces it stands in the same place), and the store an Add fetched into."""
    if data.get("ns") and stage:
        _remove_stage(data["ns"])
    if data.get("store"):
        shutil.rmtree(data["store"], ignore_errors=True)


def _expired():
    """The tickets older than TTL, taken out of TICKETS: what they held is `_discard`ed by the caller once the lock is let go,
    since removing a tree is no short thing and the state asks for the lock at every poll. Called with `_lock` held."""
    return [TICKETS.pop(ticket) for ticket in [ticket for ticket, data in TICKETS.items() if _now() - data["made"] > TTL]]


def _keep(ticket, kind, **data):
    """Remembers what a check showed under its ticket. Another ticket of the same kind for the same catalog is dropped (its
    store goes, its staged tree is the one this ticket stands on), and so are the oldest past MAX_TICKETS and those that
    expired, with what they held."""
    gone, replaced = [], []
    with _lock:
        gone += _expired()
        for other, old in list(TICKETS.items()):
            if old["kind"] == kind and old.get("ns") == data.get("ns"):
                replaced.append(TICKETS.pop(other))
        TICKETS[ticket] = {"kind": kind, "made": _now(), **data}
        while len(TICKETS) > MAX_TICKETS:
            gone.append(TICKETS.pop(min(TICKETS, key=lambda name: TICKETS[name]["made"])))
    for old in gone:
        _discard(old)
    for old in replaced:
        _discard(old, stage=False)


def _held(ticket, *kinds):
    """The data of a ticket of one of these kinds, still valid; `ticket_expired` for one that is unknown, too old or for
    another change. The ticket is kept: it is spent (`_spend`) once the change is made, so that a refusal (held, a history
    to confirm) can be tried again with the same plan."""
    if not isinstance(ticket, str):
        raise CatalogError("ticket_expired", EXPIRED, 409)
    with _lock:
        gone = _expired()
        data = TICKETS.get(ticket)
    for old in gone:
        _discard(old)
    if data is None or data["kind"] not in kinds:
        raise CatalogError("ticket_expired", EXPIRED, 409)
    return data


def note(ticket, **fields):
    """Adds what a caller knows of a check to its ticket (an import notes what it found), for when the change is made."""
    with _lock:
        if isinstance(ticket, str) and ticket in TICKETS:
            TICKETS[ticket].update(fields)


def _spend(ticket):
    with _lock:
        TICKETS.pop(ticket, None)


def imports():
    """{the real path of its folder: [tickets]} of the imports whose preview is still valid: their folder waits for the change."""
    found = {}
    with _lock:
        for ticket, data in TICKETS.items():
            if data.get("imported") and _now() - data["made"] <= TTL:
                found.setdefault(os.path.realpath(getattr(data.get("address"), "path", "") or ""), []).append(ticket)
    return found


def forget(ticket):
    """Lets a check go before it is used, with what it staged: the judgement an export makes of its archive."""
    with _lock:
        data = TICKETS.pop(ticket, None) if isinstance(ticket, str) else None
    if data is not None:
        _discard(data)


# ---------------------------------------------------------------------------------------------------------------------
# What a person reads of a state: the server writes the words, the page draws them
# ---------------------------------------------------------------------------------------------------------------------

WORDS = {
    "machine": ("", "muted"), "uptodate": ("Up to date", "muted"), "update": ("Update available", "warn"),
    "checking": ("Checking", "muted"), "updating": ("Updating", "working"), "downloading": ("Downloading", "working"),
    "unreachable": ("Unreachable", "warn"), "noaccess": ("No access", "error"), "needs_git": ("Needs Git", "error"),
    "wrong_catalog": ("Wrong catalog", "error"), "rewritten": ("Rewritten", "error"),
    "lab_new": ("Needs a newer Gravitee Lab", "warn"), "lab_old": ("Written for an older Gravitee Lab", "warn"),
    "damaged": ("Damaged", "error"), "linked": ("Linked", "muted"), "missing_folder": ("Folder not found", "error"),
    "suggested": ("Not added", "muted"), "failed": ("Failed", "error"),
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _instant(text):
    """The time of an ISO stamp of the lock, in seconds since the epoch, or None."""
    try:
        return calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")) if isinstance(text, str) else None
    except (ValueError, OverflowError):
        return None


def ago(text):
    """"3 hours ago" for the stamp of the lock: the words of the popover, written here because the server writes them."""
    then = _instant(text)
    if then is None:
        return ""
    seconds = max(0, int(time.time() - then))
    for size, one, many in ((86400, "day", "days"), (3600, "hour", "hours"), (60, "minute", "minutes")):
        if seconds >= size:
            number = seconds // size
            return f"{number} {one if number == 1 else many} ago"
    return "just now"


def _day(text):
    """"12 Oct" for a stamp: the date of the copy that is shown while the address does not answer."""
    then = _instant(text)
    if then is None:
        return ""
    when = time.gmtime(then)
    return f"{when.tm_mday} {MONTHS[when.tm_mon - 1]}"


def _address_of(url):
    """The address as the lines of the catalogue of errors name it, or None for one the grammar refuses."""
    try:
        return gitx.parse_address(url)
    except CatalogError:
        return None


def _failure(code, url, installed):
    """(reason, next) of a failure code for the popover of a row: the line of the catalogue of errors, cut where its sentence ends."""
    address = _address_of(url)
    host = (address.host or address.display) if address else "the host"
    if code in ("git_missing", "git_old"):
        return ("Git is not installed." if code == "git_missing" else "Git is too old.",
                "Install Git, then try again." if code == "git_missing" else "Install Git 2.25 or later, then try again.")
    if code == "unreachable":
        return (f"Cannot reach {host}." + (f" Showing the copy from {installed}." if installed else ""),
                "Check your connection, then check again." if installed else "Check your connection, then try again.")
    if code in gitx.MESSAGES:
        line = gitx.fail(code, address).message
        first, _, rest = line.partition(". ")
        return (first + ("." if rest else ""), rest)
    return (f"{code.replace('_', ' ').capitalize()}.", "Try again.")


def _state(key, **fields):
    label, tone = WORDS[key]
    return {"key": key, "label": label, "tone": tone, "reason": fields.get("reason", ""), "next": fields.get("next", ""),
            "whatsnew": []}


def _seen(held):
    """What the last check learned and the lock does not hold: {target, update, tip, version, searched}, defensively.
    `searched`: the versions the check read when its budget ran out with none this Lab reads, else 0."""
    found = held.get("seen")
    found = found if isinstance(found, dict) else {}
    return {"target": found.get("target") if isinstance(found.get("target"), str) else None,
            "update": found.get("update") is True,
            "tip": found.get("tip") if found.get("tip") in ("ok", "new", "old") else "ok",
            "version": found.get("version") if isinstance(found.get("version"), str) else None,
            "searched": found["searched"] if type(found.get("searched")) is int and found["searched"] > 0 else 0}


def _update_available(held):
    """Whether the last check found a version that changes something installable and is not the one installed."""
    seen = _seen(held)
    return bool(seen["update"] and seen["target"] and seen["target"] != held.get("pin"))


def _kind_state(ns, found, held, entry, contract):
    """The state key of a git catalog and the words under it: the disk's reading (registry.inputs) refined with what the
    last check learned (the lock's `seen`) and with how this Lab reads the installed version."""
    key = found["state"]
    listed = bool(found.get("listed"))
    url = defaults.effective(entry)[0] if entry and entry.get("kind") == "git" else None
    installed = _day(held.get("installed"))
    seen = _seen(held)
    level = manifest.compat(found.get("lab"), contract.get("contract", 1), contract.get("contract_min", 1)) \
        if isinstance(found.get("lab"), int) else "ok"
    if key == "uptodate":
        update = _update_available(held)
        if level == "old":
            key = "lab_old"
        elif update:
            key = "update"
        elif seen["tip"] == "new":
            key = "lab_new"
        elif not held.get("checked"):
            key = "checking"
    if key in ("unreachable", "noaccess", "needs_git", "failed") and held.get("error"):
        reason, after = _failure(held["error"], url, installed if held.get("pin") else "")
        return _state(key, reason=reason, next=after)
    if key == "uptodate":   # a copy that is no release (a catalog that makes none, commits made after the last one) says so
        said = [f"Version {found['version']}"] if found.get("version") else []
        said += [] if held.get("release") else ["unreleased"]
        said += [f"checked {ago(held.get('checked'))}"] if held.get("checked") else []
        text = ", ".join(said)
        return _state(key, reason=text[:1].upper() + text[1:])
    if key == "update":   # a version the catalog did not raise says nothing of the update
        raised = seen["version"] and seen["version"] != held.get("version")
        return _state(key, reason=f"Version {seen['version']}" if raised else "")
    if key == "downloading":
        address = _address_of(url) if url else None
        return _state(key, reason=f"From {address.display}" if address else "")
    if key == "wrong_catalog":
        return _state(key, reason="This address does not serve the catalog this Lab expects.",
                      next="Run make update, then try again.")
    if key == "rewritten":
        return _state(key, reason="This catalog was rewritten or moved.", next="Update only if you expected it.")
    if key == "lab_new" and seen["searched"]:   # what was read is all that is known: the copy may not be the newest it reads
        return _state(key, reason=f"This Lab found no version of this catalog it reads among the newest {seen['searched']}.",
                      next="Run make update, then restart the Lab.")
    if key == "lab_new":
        return _state(key, reason="A newer version needs a newer Gravitee Lab. This is the newest one this Lab reads.",
                      next="Run make update, then restart the Lab.")
    if key == "lab_old":
        if listed:
            return _state(key, reason="The catalog is not ready for this version of the Lab. Try again later.")
        return _state(key, reason="This version is too old for this Lab.",
                      next=f"Update it, or ask the owners of {found.get('name') or ns} to update it.")
    if key == "damaged":
        return _state(key, reason="Some installed files changed.", next="Repair it from the menu.")
    return _state(key)


def _folder_state(key):
    if key == "linked":
        return _state(key, reason="The folder is linked.")
    return _state(key, reason="The folder is gone.", next="Put it back or remove the catalog.")


def _attention(ns, found, held, entry, state_key):
    """What the dot of the menu is for: "update", "repair", "lab", "download" or None. The version the person just
    left with Go back does not light it again, a catalog that is not checked automatically never lights it for an update, and
    a download that lasts a few seconds lights nothing."""
    if state_key == "damaged":
        return "repair"
    if _update_available(held) and entry and entry.get("auto") is not False \
            and _seen(held)["target"] != held.get("previous") and state_key in ("update", "lab_old"):
        return "update"
    if state_key == "lab_new":
        return "lab"
    if found.get("kind") == "git" and not held.get("pin"):
        since = _started.get(ns)
        if held.get("error") or (state_key == "downloading" and since is not None and _now() - since > LONG_DOWNLOAD):
            return "download"
    return None


def _progress_of(ns, state_key):
    """How far a download is, 0..1, from what the worker says in memory; None when nothing is known or nothing runs."""
    if state_key != "downloading":
        return None
    from . import firstrun   # here and not above: the worker calls this module
    found = (getattr(firstrun, "PROGRESS", None) or {}).get(ns)
    fraction = found.get("fraction") if isinstance(found, dict) else None
    return float(fraction) if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) else None


# ---------------------------------------------------------------------------------------------------------------------
# What stops a change: a job, an item that is on or up
# ---------------------------------------------------------------------------------------------------------------------

def _no_job():
    """`job_running` while a job runs: a change of catalogs is a plain request and never starts a job, and a job reads the
    install while it runs."""
    last = jobs.JOBS.current
    if last and not last.done:
        raise CatalogError("job_running", f"Wait for {last.title}.", 409)


def _waiting():
    """"Wait for {job title}" while a job runs, else None: the same reason, as the line of a plan that is held."""
    last = jobs.JOBS.current
    return f"Wait for {last.title}" if last and not last.done else None


def _unit_id(ns, kind, name):
    return name if ns == paths.default_ns() else f"@{ns}/{name}"


def _unit_name(found, ns, key):
    """What a unit of a catalog is called, by its `kind:name` key, for a line about it."""
    kind, _, name = key.partition(":")
    unit = ((found or {}).get(f"{kind}s") or {}).get(_unit_id(ns, kind, name)) or {}
    return resolve.shown(unit.get("name") or name, NAME_CUT)


def _running(found, ns):
    """The `kind:name` of the units of catalog `ns` that run now (an add-on with a running container, a playground with a
    running app), asked of Docker. Docker not answering is nothing running here; it is the check's best knowledge."""
    try:
        up = model.up(found, compose.ps())
    except (compose.ComposeError, OSError, KeyError, TypeError):
        return set()
    return {f"{kind}:{unit['id'].rsplit('/', 1)[-1]}"
            for kind, group in (("addon", up["addons"]), ("playground", up["playgrounds"]))
            for uid in group for unit in [(found.get(f"{kind}s") or {}).get(uid) or {}]
            if unit.get("catalog") == ns}


def _used(ns, found=None):
    """({kind:name that the on file turns on}, {kind:name that run}) among the units of catalog `ns`."""
    found = found if found is not None else project.loaded()[0]
    return install.on_units(ns), _running(found, ns)


def _turn_off_line(found, ns, keys, running=()):
    """The line of a hold: "Stop X first" for a unit that runs, "Turn off X first" for one that is only on; the first of the
    sorted keys. None for no unit."""
    keys = sorted(keys)
    if not keys:
        return None
    name = _unit_name(found, ns, keys[0])
    return f"{'Stop' if keys[0] in running else 'Turn off'} {name} first"


def _hold(ns, keys=None, found=None):
    """Why the units of catalog `ns` (all of them, or `keys`) cannot be changed now: a job runs, or one is on or up. None
    when nothing holds."""
    found = found if found is not None else project.loaded()[0]
    why = _waiting()
    if why:
        return why
    on, running = _used(ns, found)
    busy = (on | running) if keys is None else (on | running) & set(keys)
    return _turn_off_line(found, ns, busy, running)


# ---------------------------------------------------------------------------------------------------------------------
# describe: the rows of state.catalogs
# ---------------------------------------------------------------------------------------------------------------------

def _counts(units):
    return {kind: len(units.get(kind) or []) for kind in ("playground", "setup", "addon")}


def _unit_text(entry):
    return {key: entry.get(key) for key in ("id", "name", "what", "why", "service") if key in entry}


def _on_by_catalog():
    """{catalog: {kind:name}} of the units that the on file turns on, directly or through what they include: what
    `install.on_units(ns)` says, for every catalog in one walk of the include closure (a state asks it of every row, every few
    seconds, and each walk reads the files again). Nothing is on when the files cannot be read."""
    found, seen, todo = {}, set(), [paths.ON_FILE]
    while todo and len(seen) < 2000:
        file = todo.pop()
        if file in seen:
            continue
        seen.add(file)
        target = resolve.target_of(file)
        if target:
            found.setdefault(target[0], set()).add(f"{target[1]}:{target[2]}")
        try:
            todo += compose.includes(file)
        except (OSError, ValueError):
            continue
    return found


def _held_of(ns, project_, held, on):
    """The reasons the row's menu is held, {update, remove, undo}: a job; an item of the catalog that is on (`on`, the
    `kind:name` of its units the on file turns on). The exact units an update changes are the plan's to say."""
    why = _waiting()
    turn = _turn_off_line(project_, ns, on) if on else None
    previous = "" if held.get("previous") else NO_PREVIOUS.rstrip(".")
    return {"update": why or "", "remove": why or turn or "", "undo": previous or why or turn or ""}


def _with_access(needs, asked):
    """The needs of a catalog or of a preview with `host_access: true` on the ones whose address the suggestions file lists with
    `host_access`: what the Add dialog preselects as the allowance when the person arrives at it from that address. A
    suggestion can preselect, never imply. `asked` is the display form of those addresses."""
    return [dict(one, host_access=True) if one.get("url") and registry.display_address(one["url"]) in asked else dict(one)
            for one in needs]


def _remembered(ns, found, held):
    """The code of the failure this process remembers for a catalog that is in the commit of a download, else None. The disk
    says Downloading for the milliseconds of a commit (`pending`, with the `error` the commit cleared), but when the attempt
    is a silent retry (one a person cannot see: it has no mark in `firstrun.PROGRESS`) of a download that failed, the page
    already says it failed, and must go on saying it until the copy is there. A click on Try again clears the failure and
    marks the catalog before the attempt starts, so that one is a download, as the page was told. The rule is
    `firstrun._failure`'s, the single one: this module asks it, and reaches the module here and not above (the worker calls
    this one)."""
    if found.get("state") != "downloading" or held.get("pin") or held.get("error") or not held.get("pending"):
        return None
    from . import firstrun
    if ns in firstrun.PROGRESS:
        return None
    return (getattr(firstrun, "failure", None) or firstrun._failure)(ns, {ns: held})


def _git_row(ns, found, summary, project_, entry, contract, asked=frozenset(), on=frozenset()):
    held = registry.lock_of(ns)
    code = _remembered(ns, found, held)
    if code:   # the failure the page says, through the milliseconds of a silent retry's commit
        found, held = {**found, "state": registry.FAILURES.get(code, "failed")}, {**held, "error": code, "pending": None}
    url = defaults.effective(entry)[0] if entry and entry["kind"] == "git" else None
    address = registry.display_address(url or (entry or {}).get("path") or "")
    state = _folder_state(found["state"]) if found["kind"] == "folder" else _kind_state(ns, found, held, entry, contract)
    progress = _progress_of(ns, state["key"])
    if state["key"] == "downloading" and ns not in _started:
        _started[ns] = _now()
    elif state["key"] != "downloading":
        _started.pop(ns, None)
    pin = held.get("pin")
    return {
        "id": ns, "kind": found["kind"], "listed": bool(found.get("listed")), "name": found["name"], "imported": registry.imported(ns),
        "description": found.get("description") or "", "address": address,
        "url": url or "", "web_url": registry.web_url(entry) if entry else None,
        "version": found.get("version"), "commit": _short(pin), "updated": held.get("installed"),
        "checked": held.get("checked"), "audience": found.get("audience") or "private",
        "counts": _counts(found.get("units") or {}), "state": state, "progress": progress,
        "attention": _attention(ns, found, held, entry, state["key"]), "auto": (entry or {}).get("auto") is not False,
        "has_previous": bool(held.get("previous")), "writable": found["kind"] == "folder",
        "host": found.get("host") or "no", "held": _held_of(ns, project_, held, on),
        "blocked": [dict(one) for one in summary.get("blocked") or []],
        "uses_machine": [_unit_text(one) for one in summary.get("uses_machine") or []],
        "ignored": found.get("ignored") or 0,
        "problems": [f"{one.get('display') or one.get('file')}: {one.get('message')}"
                     for one in summary.get("problems") or [] if isinstance(one, dict)],
        "needs": _with_access(summary.get("needs") or [], asked),
    }


def _machine_row(found, summary):
    return {"id": paths.LOCAL, "kind": "machine", "listed": False, "name": found.get("name") or "Personal",
            "description": "", "counts": _counts(found.get("units") or {}), "state": _state("machine"),
            "progress": None, "attention": None, "writable": True, "blocked": [], "uses_machine": [], "problems": [
                f"{one.get('display') or one.get('file')}: {one.get('message')}"
                for one in summary.get("problems") or [] if isinstance(one, dict)]}


def _suggested_row(one):
    address = registry.display_address(one["url"])
    row = {"id": one.get("id"), "kind": "suggested", "listed": bool(one.get("listed")), "name": one["name"],
           "description": one.get("description") or "", "address": address, "url": one["url"],
           "state": _state("suggested", reason=one.get("description") or ""), "progress": None, "attention": None}
    if one.get("branch"):
        row["branch"] = one["branch"]
    if one.get("host_access"):
        row["host_access"] = True
    return row


def describe(project, inputs):
    """The rows of `state.catalogs`, in the order the page shows them: the first listed catalog, Personal when it holds something,
    the others as they were added, then the ones that are not added, and last, while an Add is fetching, the entry
    {id: "check", kind: "progress", progress} that carries how far the fetch is (`check` is a reserved name, so no catalog is
    ever called so). `project` is what project.build made, `inputs` what project.gather read: the rows read the registry's
    reading of the disk (`inputs["catalogs"]`), the lock, the summary the project made of each catalog, and the memory of the
    worker. Nothing here reads git, the network or Docker."""
    project = project if isinstance(project, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    found = inputs["catalogs"] if isinstance(inputs.get("catalogs"), dict) else registry.inputs()
    summaries = project.get("catalogs") if isinstance(project.get("catalogs"), dict) else {}
    contract = inputs.get("contract") if isinstance(inputs.get("contract"), dict) else {}
    entries = {one["id"]: one for one in registry.entries()}
    suggestions = registry.suggestions()
    asked = {registry.display_address(one["url"]) for one in suggestions if one.get("host_access")}
    on = _on_by_catalog()
    rows = []
    for ns, one in found.items():
        if one.get("kind") == "machine":
            if sum(_counts(one.get("units") or {}).values()):
                rows.append(_machine_row(one, summaries.get(ns) or {}))
        else:
            rows.append(_git_row(ns, one, summaries.get(ns) or {}, project, entries.get(ns), contract, asked,
                                 on.get(ns, frozenset())))
    rows += [_suggested_row(one) for one in suggestions]
    with _lock:
        fetching = list(_checking.values())
    if fetching:
        rows.append({"id": "check", "kind": "progress", "progress": fetching[-1]})
    return rows


def problems():
    """The line the banner shows when the files that record the catalogs cannot be read (`registry.config_problem`), in the
    shape of the project's own problems: [{file, message, editable}], [] when they read. Without it every catalog would
    silently be gone from the page. Local files only; it is gone as soon as the file is fixed."""
    line = registry.config_problem()
    return [{"file": "Catalogs", "message": line, "editable": False}] if line else []


# ---------------------------------------------------------------------------------------------------------------------
# Judging a staged tree
# ---------------------------------------------------------------------------------------------------------------------

def _contract():
    """The level of this Lab as the core file says it: {contract, contract_min, seeder_image}."""
    found = project.loaded()[1].get("contract")
    return found if isinstance(found, dict) else {"contract": 1, "contract_min": 1, "seeder_image": ""}


def _row_of_candidate(meta, kind, audience, listed, host, ignored, units):
    """The row of `registry.inputs()` for a catalog that is staged, as the project reads it for the judgement."""
    return {"name": meta["name"], "description": meta.get("description") or "", "version": meta["version"],
            "lab": meta["lab"], "audience": audience, "kind": kind,
            "requires_catalogs": copy.deepcopy((meta.get("requires") or {}).get("catalogs") or {}), "state": "uptodate",
            "units": {k: sorted(n for knd, n, _ in units if knd == k) for k in paths.KINDS}, "ignored": ignored,
            "error": None, "pending": None, "listed": listed, "host": host}


def _review(ns, meta, kind, audience, listed, host, ignored, replacing=False, said=None):
    """The judgement of the staged catalog `ns` (the candidate of an Add, the new version of an Update): ({kind: {id: unit}},
    {(kind, id): own verdict}, [the keys of the catalog's own units]). The catalog is judged as installed under its name,
    allowed to use this machine as `host` says ("listed", "allowed" or "no"), against everything else installed. `said`: a dict
    that gets the x-gravitee of each of its units as the judgement read it ({id: block})."""
    row = _row_of_candidate(meta, kind, audience, listed, host, ignored, project.staged_units(ns))
    blocks = {} if said is not None else None
    judged, verdicts = project.judge_staged(ns, meta, row, replacing, blocks)
    for file, block in (blocks or {}).items():
        found = paths.unit_of(file)
        if found:
            said[found[1]] = block
    mine = [(k, uid) for k in paths.KINDS for uid, unit in judged[k].items() if unit.get("catalog") == ns]
    return judged, verdicts, mine


def _blocked_of(judged, mine):
    return [{"kind": k, "id": uid, "name": resolve.shown(judged[k][uid]["name"], NAME_CUT),
             "reasons": list(judged[k][uid]["blocked"]["reasons"])} for k, uid in mine if judged[k][uid].get("blocked")]


def _uses_of(judged, verdicts, mine):
    """The services of the catalog's own units that use this machine, one entry for each: the item, what the service
    takes and the reason its author gave. Units that cannot run anyway are left out: the allowance would change nothing."""
    return [{"id": uid, "name": use["label"], "service": use["service"], "what": list(use["what"]), "why": use["why"]}
            for k, uid in mine if not judged[k][uid].get("blocked")
            for use in (verdicts.get((k, uid)) or {}).get("surface", {}).get("uses_machine", [])]


def _surface(judged, verdicts, mine):
    """What the catalog's own units that can run do, summed: a unit that cannot start publishes nothing and builds nothing."""
    return trust.sum_surfaces([verdicts[key]["surface"] for key in mine
                               if key in verdicts and not judged[key[0]][key[1]].get("blocked")])


def _needs_of(judged, mine, meta):
    """The catalogs the catalog's units include from that are not added: [{id, name, url, min}], once each, from what its
    units lack (`needs_catalogs`, 5.3) and what the catalog declares of them. The units that cannot run are asked too: the
    catalog they need is a reason to add it."""
    declared = (meta.get("requires") or {}).get("catalogs") or {}
    found = {}
    for k, uid in mine:
        for need in judged[k][uid].get("needs_catalogs") or ():
            if need.get("reason") == "missing" and need["id"] not in found:
                want = declared.get(need["id"]) if isinstance(declared.get(need["id"]), dict) else {}
                found[need["id"]] = {"id": need["id"], "name": need.get("name") or want.get("name") or need["id"],
                                     "url": need.get("url") or want.get("url"), "min": need.get("min") or want.get("min")}
    return [found[name] for name in sorted(found)]


def _found_of(ns, meta, judged, mine):
    """The items whose held settings the catalog takes over: [{id, name}]. The first listed catalog
    qualifies for any unit, another only for the units it declares in `moved_from`."""
    default, declared = paths.default_ns(), set(meta.get("moved_from") or ())
    found = []
    for k, uid in mine:
        name = uid.rsplit("/", 1)[-1]
        if (ns == default or f"{k}:{name}" in declared) and moves.has_held(k, name):
            found.append({"id": uid, "name": resolve.shown(judged[k][uid]["name"], NAME_CUT)})
    return found


def _preview_rows(ns, meta, judged, verdicts, mine, listed):
    surface = _surface(judged, verdicts, mine)
    builds = surface["builds"]
    rows = {"builds": {"local": sum(1 for one in builds if one["source"] == "local"),
                       "remote": sorted({one["source"] for one in builds if one["source"] != "local"})},
            "ports": list(surface["ports"]), "core": sorted({one["label"] for one in surface["core"]}),
            "reads": list(surface["reads"]),
            "uses_machine": [] if listed else _uses_of(judged, verdicts, mine),
            "needs": _with_access(_needs_of(judged, mine, meta),
                                  {registry.display_address(one["url"]) for one in registry.suggestions() if one.get("host_access")})}
    found = _found_of(ns, meta, judged, mine)
    if found:
        rows["found"] = found
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# Changing catalogs: the lock, the row, the address
# ---------------------------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _changing():
    """The lock of a change of catalogs, for a moment only: a second change while one is under way is `busy`, it does
    not wait for the first, but the write of the lock that the checker makes in the background (milliseconds) is no change. It
    is the lock the operations of `install` take, which they take again at no cost; the order is the catalogs' lock, then the
    project's."""
    with registry.flock(wait=PATIENCE):
        yield


def _row_of(ns):
    """The row of one catalog as the state would give it now, for the answer of a change: the project is read again, since a
    change moved the files it is read from. Without a project (it cannot be read) the rows are those of the disk alone."""
    project.forget()
    try:
        found, inputs = project.loaded()
    except Exception:  # noqa: BLE001 - the answer of a change that was made does not fail for want of the project
        found, inputs = {}, {}
    return next((one for one in describe(found, inputs) if one.get("id") == ns and one.get("kind") != "progress"), None)


def _entry(ns):
    """The entry of catalog `ns`; NotFound (404) for one the person does not have."""
    entry = registry.entry(ns) if isinstance(ns, str) and re.fullmatch(paths.NS, ns) else None
    if entry is None:
        raise paths.NotFound(f"No catalog {ns}")
    return entry


def _git(ns):
    """(entry, lock entry) of a git catalog that can be updated, checked or repaired: `linked` for one a working copy stands in
    for, a ValueError for a plain folder, which has no versions."""
    entry = _entry(ns)
    held = registry.lock_of(ns)
    if held.get("shadowed") or (entry["kind"] == "folder" and entry.get("shadows")):
        raise CatalogError("linked", install.LINKED, 409)
    if entry["kind"] != "git":
        raise ValueError(f"{registry.lock_of(ns).get('name') or ns} is a folder on this machine: it has no versions")
    return entry, held


def _called(ns):
    return registry.lock_of(ns).get("name") or ns


def _address_text(address, branch):
    text = _text(address, "address").strip()
    if branch is not None and not isinstance(branch, str):
        raise ValueError("The branch must be text")
    return text, branch


def _listed_ids_at(display):
    """The ids the tracked file lists at this address: the only ones a reserved word is accepted for."""
    return [one["id"] for one in defaults.read() if registry.display_address(one["url"]) == display]


def _already(address):
    """`already_added` when a catalog the person has is at this address, in any spelling (asked first so that the
    fetch is not made for nothing)."""
    shown = address.display
    for entry in registry.entries():
        there = defaults.effective(entry)[0] if entry["kind"] == "git" else entry.get("path")
        if shown and registry.display_address(there or "") == shown:
            raise CatalogError("already_added", f"Already added as {_called(entry['id'])}.", 409, id=entry["id"])


def _shorthand(text):
    """Whether the text is the short form owner/repo, which means GitHub and may be tried over ssh."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}", text))


def _reached(text, address, branch):
    """(the address that answered, what the remote has): ls-remote with the 20 s limit. The short form owner/repo that
    https refuses (`no_access`, `not_found`) is tried again as git@github.com:owner/repo.git, and the address that answered
    is the one kept; if both are refused, the first refusal is the person's."""
    try:
        return address, gitx.ls_remote(address, branch)
    except CatalogError as first:
        again = gitx.ssh_form(address) if first.code in ("no_access", "not_found") and _shorthand(text) else None
        if again is None:
            raise
        try:
            return again, gitx.ls_remote(again, branch)
        except CatalogError:
            raise first from None


def _audience(address):
    """"public" when an anonymous ls-remote answers, else "private": an ssh address and any doubt are private."""
    try:
        gitx.ls_remote(address, None, anonymous=True)
    except (CatalogError, OSError):
        return "private"
    return "public"


@contextlib.contextmanager
def _one_stage():
    """One tree is staged and judged at a time (the folder of a stage is named by the catalog): a second waits a while, then it
    is `busy`."""
    if not _staging.acquire(timeout=WAIT_STAGE):
        raise registry.busy()
    try:
        yield
    finally:
        _staging.release()


def _clean_up(ticket, staged, store_path):
    """Removes what an Add check made when it ends in a refusal: the stage under either name, and the store."""
    for ns in {getattr(staged, "ns", None), f"new-{ticket}"} - {None}:
        _remove_stage(ns)
    if store_path is not None:
        shutil.rmtree(store_path, ignore_errors=True)


def check_address(address, branch=None):
    """What adding the address would do, as the Preview of an Add, and a ticket for the Add. Nothing is kept
    but the staged tree and the ticket, which lives ten minutes. The network steps (ls-remote, the fetch) hold no lock;
    the fetch reports how far it is in the state (`catalogs[]`, the entry `check`)."""
    text, branch = _address_text(address, branch)
    asked = gitx.parse_address(text)
    _already(asked)
    if asked.kind == "folder":
        return _check_folder(asked)
    gitx.require()
    git_version(fresh=True)
    found_at, remote = _reached(text, asked, branch)
    audience = _audience(found_at)
    ticket = secrets.token_hex(8)
    store_path, staged = registry.new_store_dir(ticket), None
    contract = _contract()
    try:
        with _lock:
            _checking[ticket] = None

        def moved(fraction):
            with _lock:
                _checking[ticket] = fraction
        tip = bare.fetch(found_at, remote["branch"], store_path, progress=moved)
        roots = bare.roots(store_path, tip)
        if len(roots) > install.ROOTS_LIMIT:   # the identity of a catalog is its roots, and the lock keeps this many
            raise gitx.fail("too_large", found_at)
        offered = manifest.offer(store_path, tip, contract["contract"], contract["contract_min"], found_at,
                                 readable=install.readable(store_path))
        listed_at = _listed_ids_at(found_at.display)
        commit = offered.commit
        if commit is None:
            raise manifest.no_commit(offered.levels, contract["contract"], contract["contract_min"], bool(listed_at),
                                     offered.searched)
        kept, ignored = bare.selected(bare.listing(store_path, commit))
        with _one_stage():
            staged = install.stage(found_at, f"new-{ticket}", store_path, commit, kept, ignored)
            meta = _read_meta(staged, found_at, contract, listed_at, published=True)
            _understood(meta)
            staged = install.rename_stage(staged, meta["id"])
            judged, verdicts, mine = _review(meta["id"], meta, "git", audience, bool(listed_at),
                                             "listed" if listed_at else "allowed", ignored)
            _understood(meta, judged, mine)
        preview = _preview(ticket, meta, "git", found_at, audience, bool(listed_at), ignored, judged, verdicts, mine,
                           newer=offered.tip != "ok")
        _keep(ticket, "add", ns=meta["id"], staged=staged, address=found_at, branch=remote["branch"], audience=audience,
              roots=roots, meta=meta, store=store_path, listed=bool(listed_at))
        return preview
    except BaseException:
        _clean_up(ticket, staged, store_path)
        raise
    finally:
        with _lock:
            _checking.pop(ticket, None)


def _check_folder(asked):
    """The Add check of a folder catalog: nothing is fetched, the stage is a link to the folder, and the same judgement is
    made of what is in it."""
    ticket, staged = secrets.token_hex(8), None
    contract = _contract()
    try:
        kept, ignored = bare.selected(install.walk(asked.path))
        with _one_stage():
            staged = install.stage(asked, f"new-{ticket}", None, "", [], 0)
            meta = _read_meta(staged, asked, contract, [])
            staged = install.rename_stage(staged, meta["id"])
            said = {}
            judged, verdicts, mine = _review(meta["id"], meta, "folder", "private", False, "allowed", ignored, said=said)
        preview = _preview(ticket, meta, "folder", asked, "private", False, ignored, judged, verdicts, mine)
        _keep(ticket, "add", ns=meta["id"], staged=staged, address=asked, branch=None, audience="private", roots=[],
              meta=meta, store=None, listed=False, judged=sorted(uid for _, uid in mine), said=said)
        return preview
    except BaseException:
        _clean_up(ticket, staged, None)
        raise


def _understood(meta, judged=None, mine=()):
    """Refuses a version of a published catalog that uses what this Lab does not read (`lab_new`: it needs a newer Gravitee
    Lab): a key of its catalog.yml, or what a unit uses, as the judgement found it (`unknown`). `pick` passes such a version
    over when it reads it first, so this is the gate for one it could not read: a Lab never runs a file it half understands."""
    found = manifest.unread(meta)
    for kind, uid in mine:
        found += [("", name) for name in (judged[kind][uid].get("unknown") or ())]
    if found:
        raise manifest.not_understood(found)


def _read_meta(staged, address, contract, listed_at, published=False):
    """catalog.yml of the staged tree, read and checked: its schema and shape, that this Lab reads its level, and
    that the name it declares is free (a listed one is accepted at its listed address only). `published`: a git catalog, whose
    values of a newer Lab are what it does not read (`_understood`) rather than mistakes."""
    taken = [one["id"] for one in registry.entries()]
    borrowed = [one["name"] for one in defaults.read() if one["id"] not in listed_at]
    meta = manifest.read_catalog(os.path.relpath(Path(staged.path) / manifest.FILE, paths.ROOT), address, borrowed,
                                 published=published)
    refusal = manifest.compat_error(meta["lab"], contract["contract"], contract["contract_min"], bool(listed_at))
    if refusal is not None:
        raise refusal
    registry.check_namespace(meta["id"], taken=taken, listed=listed_at)
    if listed_at and meta["id"] not in listed_at:   # the listed address serves another catalog than the one listed
        raise install.not_expected(address.repo or address.display)
    return meta


def _preview(ticket, meta, kind, address, audience, listed, ignored, judged, verdicts, mine, newer=False):
    units = project.staged_units(meta["id"])   # what the tree holds, a unit that does not read included: the manifest counts the same
    counts = {k: sum(1 for knd, _, _ in units if knd == k) for k in ("playground", "setup", "addon")}
    shown = {"ticket": ticket, "expires": int(TTL),
             "catalog": {"id": meta["id"], "name": meta["name"], "description": meta.get("description") or "",
                         "version": meta["version"], "lab": meta["lab"], "kind": kind},
             "address": address.display, "url": address.url if kind == "git" else address.display, "audience": audience,
             "counts": counts, "rows": _preview_rows(meta["id"], meta, judged, verdicts, mine, listed),
             "blocked": _blocked_of(judged, mine), "ignored": ignored}
    if newer:
        shown["newer"] = True
    # What Compose could not read for want of a catalog was not judged: an import says it cannot run until that catalog comes
    waits = [{"kind": k, "id": uid, "name": resolve.shown(judged[k][uid]["name"], NAME_CUT),
              "needs_catalogs": copy.deepcopy(judged[k][uid]["needs_catalogs"])}
             for k, uid in mine if judged[k][uid].get("needs_catalogs") and not judged[k][uid].get("blocked")]
    if waits:
        shown["waits"] = waits
    return shown


def add(ticket, host=False):
    """Puts the catalog the check showed in place. `host` is the allowance the person chose with the
    dialog (the page sends it as a request of its own after the Add, and may send it here). Answers {"catalog": Row}."""
    data = _held(ticket, "add")
    staged, ns = data["staged"], data["ns"]
    with _changing():
        install.commit_add(staged, data["address"], data["branch"], data["audience"], data["roots"], meta=data["meta"],
                           host=bool(host) and not data["listed"], auto=True)
        if data.get("imported"):   # an imported playground: what its file held is recorded, and settings kept for it go back
            from . import exports   # here and not above: exports reads this module
            exports.added(ns, data)
    _spend(ticket)
    with contextlib.suppress(OSError):   # the .env is for a docker compose typed by hand: the catalog is added all the same
        layers.prepare_catalogs()
    git_version(fresh=True)
    return {"catalog": _row_of(ns)}


def add_listed(ns):
    """Adds a catalog the tracked file lists, with no check and no dialog: its entry is written, and the one background
    thread of the Lab downloads it. Answers the row at once, downloading; the state follows."""
    tracked = next((one for one in defaults.read() if one["id"] == ns), None) if isinstance(ns, str) else None
    if tracked is None:
        raise CatalogError("not_listed", f"{ns if isinstance(ns, str) else 'That'} is not a catalog this Lab lists.", 400)
    with _changing():
        entry = registry.entry(ns)
        if entry is not None and registry.lock_of(ns).get("pin"):
            raise CatalogError("already_added", f"Already added as {_called(ns)}.", 409, id=ns)
        if entry is None:
            config = registry.read_config() or {"schema": 1, "catalogs": []}
            row = {"id": ns, "kind": "git", "url": tracked["url"], "auto": True, "added": _utc()}
            if tracked.get("branch"):
                row["branch"] = tracked["branch"]
            registry.write_config({**config, "catalogs": [*config["catalogs"], row]})
        registry.set_lock(ns, error=None)
    _wake()
    return {"catalog": _row_of(ns)}


def _wake():
    """The worker is asked to download what has no copy now: the entries are marked as downloading, its retry schedule starts
    again."""
    from . import firstrun   # here and not above: the worker calls this module
    firstrun.reconcile()
    firstrun.wake()


# ---------------------------------------------------------------------------------------------------------------------
# Check again, and the pass of the background checker
# ---------------------------------------------------------------------------------------------------------------------

def _vouched(entry):
    """The root commits the tracked file pins for a listed catalog, lower case, or []: a rewrite that lands on them is
    the engine's own and is not flagged."""
    tracked = next((one for one in defaults.read() if one["id"] == entry["id"]), None) if entry["kind"] == "git" else None
    return sorted(root.lower() for root in (tracked or {}).get("roots") or ())


def _fetch_tip(ns, entry, held):
    """The branch is fetched into the catalog's store (`refs/lab/tip`), 60 s at most. A
    listed catalog whose https address answers `no_access` or `not_found` is asked once over ssh, and the form that answered is
    remembered (`via`) for the next runs. Answers (the tip, the address that answered)."""
    gitx.require()
    url, branch = defaults.effective(entry)
    address = gitx.parse_address(url)
    tries = [address]
    if defaults.listed(entry):
        again = gitx.ssh_form(address)
        if again is not None:
            tries = [again, address] if held.get("via") == "ssh" else [address, again]
    for number, one in enumerate(tries):
        try:
            tip = bare.fetch(one, branch, registry.store_dir(ns), stall=30, cap=CHECK_CAP)
        except CatalogError as error:
            if number + 1 == len(tries) or error.code not in ("no_access", "not_found"):
                raise
            continue
        if len(tries) > 1 and held.get("via") != ("ssh" if gitx.is_ssh(one) else "https"):
            registry.set_lock(ns, via="ssh" if gitx.is_ssh(one) else "https")
        return tip, one
    raise AssertionError("unreachable")


def _mark_damaged(ns):
    """The lock of a catalog says `damaged` once a gate found its installed files changed, so that the row says it and offers
    Repair: the refusal of a start is gone with the request, and the state is what stays. Not while an operation is
    under way (the files are in flux), and for a catalog that is installed from git only. Nothing here raises: the refusal
    that called it is the answer."""
    with contextlib.suppress(CatalogError, OSError, ValueError):
        entry = registry.entry(ns)
        if entry is not None and entry["kind"] == "git" and not registry.lock_of(ns).get("pending"):
            registry.set_lock(ns, error="damaged")


def _failed_with(ns, code):
    """The `error` the lock gets after a check: `damaged` stays whatever the check learned, since it is the state of the files
    here and Repair works from the local store (a check clears the failures of the network, never this); else `code`."""
    return "damaged" if registry.lock_of(ns).get("error") == "damaged" else code


def _offered(store_path, held, tip, contract, address=None):
    """(the Offer of `manifest.offer`, moved) for an installed catalog. A version older than the copy (an ancestor of the pin:
    a copy of commits made after the last release, a release whose tag was deleted) is no offer: the pin is. `moved`: the tag
    of the release installed now names the commit offered, which is another one: the owners moved a release, and the same
    version is not the same files."""
    found = manifest.offer(store_path, tip, contract["contract"], contract["contract_min"], address,
                           readable=install.readable(store_path))
    pin, target = held.get("pin"), found.commit
    if target is not None and pin and target != pin and bare.is_ancestor(store_path, target, pin):
        return found._replace(commit=pin, release=held.get("release")), False
    mine = held.get("release")
    return found, bool(mine and target and target != pin and bare.release_commit(store_path, mine) == target)


def _ask(ns, entry):
    """Fetch, identity, audience, the compatible version, whether it changes anything, and the result in the
    lock. The network steps hold no lock; the lock is written at the end. A refusal is the caller's (`check_now` keeps it).
    A release the owners moved is a history that changed: it is said as one, and Update asks to confirm it."""
    held = registry.lock_of(ns)
    tip, answered = _fetch_tip(ns, entry, held)
    store_path, contract = registry.store_dir(ns), _contract()
    held = registry.lock_of(ns)   # it may have changed during the fetch
    pin = held.get("pin")
    roots = bare.roots(store_path, tip)
    same = roots == sorted(held.get("roots") or []) and bare.is_ancestor(store_path, pin, tip)
    if not same and not (_vouched(entry) and roots == _vouched(entry)):
        registry.set_lock(ns, tip=tip, error=_failed_with(ns, "history_changed"))   # nothing else proceeds
        return
    audience = _audience(answered)
    found, moved = _offered(store_path, held, tip, contract, answered)
    target, tip_state = found.commit, found.tip
    update, version = False, None
    if target is not None:
        kept, _ = bare.selected(bare.listing(store_path, target))
        digests = bare.unit_digests(kept)
        there = digests.pop("@catalog", None)
        installed = registry.manifest(ns)
        if installed is not None and target != pin:
            update = digests != installed["units"] or there != ((installed["files"].get(manifest.FILE) or {}).get("oid"))
        version = registry.scan(bare.read(store_path, target, manifest.FILE) or b"").get("version")
    if moved and update:
        registry.set_lock(ns, tip=tip, error=_failed_with(ns, "history_changed"))
        return
    mine = held.get("release") or manifest.release_of(store_path, pin, held.get("version"))   # a copy installed before releases
    seen = {"target": target, "update": update, "tip": tip_state, "version": version}
    if target is None and found.searched:   # the budget ran out: the line says what was read, and no more
        seen["searched"] = found.searched
    registry.set_lock(ns, tip=tip, checked=_utc(), error=_failed_with(ns, None), audience=audience, release=mine, seen=seen)


def _unstick(ns):
    """A change that raised (the disk was full, a file was in the way) leaves `pending` in the lock, and the row would say
    Updating with no action until the next start finished it. It is finished now as that start would, or the catalog is
    marked damaged, which Repair clears. Only an operation on an installed catalog: the download of a first copy is the
    worker's and may well be running. Another change that holds the lock is left to finish. Nothing here raises: the failure
    that came first is the answer."""
    with contextlib.suppress(Exception):
        found = registry.lock_of(ns).get("pending")
        if found and found["op"] != "add" and registry.lock_of(ns).get("pin"):
            recover()


def check_now(ns):
    """Check again: asks the catalog's address whether it has news and says so in the lock, then answers the row. A
    refusal of git or of the network is the row's state (unreachable, no access, needs Git), never an error of the request; on
    an entry with no copy it is the download, which the worker does (the answer is the row, at once). A folder has
    nothing to ask. A change that failed halfway is finished first (`_unstick`); one that runs is left alone."""
    entry = _entry(ns)
    held = registry.lock_of(ns)
    if entry["kind"] != "git" or held.get("shadowed"):
        return {"catalog": _row_of(ns)}
    if held.get("pending"):
        _unstick(ns)
        return {"catalog": _row_of(ns)}
    if not held.get("pin"):
        registry.set_lock(ns, error=None)
        _wake()
        return {"catalog": _row_of(ns)}
    git_version(fresh=True)
    try:
        _ask(ns, entry)
    except CatalogError as error:
        registry.set_lock(ns, error=_failed_with(ns, error.code))
    return {"catalog": _row_of(ns)}


def check_all():
    """One pass of the background checker: Check again for every git catalog that has a copy, is checked
    automatically and is not linked to a working copy, with the 60 s of a check for each; and, for a listed catalog this Lab
    can no longer read, the one automatic update of 9.3b. Nothing is asked without Git. The module starts no thread: the
    worker of `firstrun` calls this on its schedule. A failure of one catalog is its row's state and never stops the pass."""
    if git_version(fresh=True) is None:
        return
    for entry in registry.entries():
        ns, held = entry["id"], registry.lock_of(entry["id"])
        if entry["kind"] != "git" or entry.get("auto") is False or held.get("shadowed") or held.get("pending") \
                or not held.get("pin"):
            continue
        try:
            check_now(ns)
            _bring_forward(ns, entry)
        except Exception as error:  # noqa: BLE001 - one catalog's trouble is not the pass's
            print(f"Gravitee Lab UI: could not check {ns} ({type(error).__name__})", file=sys.stderr)


def _bring_forward(ns, entry):
    """A listed catalog whose installed version this Lab can no longer read, and that has a newer one it can, is brought
    to it with no dialog when nothing of it is on or up and nothing needs deciding. `previous` is kept, so Go back exists."""
    held = registry.lock_of(ns)
    found = registry.inputs().get(ns) or {}
    old = isinstance(found.get("lab"), int) and manifest.compat(
        found["lab"], _contract()["contract"], _contract()["contract_min"]) == "old"
    if not (defaults.listed(entry) and old and _update_available(held)) or _hold(ns) is not None:
        return
    try:
        plan = update(ns, check=True)
        if plan.get("upToDate") or plan.get("held") or plan.get("rewritten"):
            return
        update(ns, ticket=plan["ticket"])
    except CatalogError:   # a change under way, an item that came on meanwhile: what it says is the row's, and the next pass tries
        return


def set_auto(ns, enabled):
    """Stop checking automatically, or check again by itself (the entry's `auto`). Answers {"catalog": Row}."""
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    _entry(ns)
    with _changing():
        config = registry.read_config() or {"schema": 1, "catalogs": []}
        config["catalogs"] = [dict(one, auto=enabled) if one["id"] == ns else one for one in config["catalogs"]]
        registry.write_config(config)
    return {"catalog": _row_of(ns)}


def allow_host(ns, enabled):
    """The allowance of items that use this machine: `host: true` in the entry of a catalog that is not listed. Turning
    it off is `host_not_allowed` while an item of the catalog that uses this machine is on or up. A listed catalog has the
    allowance by being listed, and nothing is written. Answers {"catalog": Row}."""
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    entry = _entry(ns)
    if defaults.listed(entry):
        return {"catalog": _row_of(ns)}
    with _changing():
        if not enabled:
            found = project.loaded()[0]
            used = {one["id"] for one in (found.get("catalogs") or {}).get(ns, {}).get("uses_machine") or ()}
            keys = {f"{kind}:{uid.rsplit('/', 1)[-1]}" for kind in paths.KINDS
                    for uid, unit in (found.get(f"{kind}s") or {}).items() if uid in used and unit.get("catalog") == ns}
            on, running = _used(ns, found)
            busy = keys & (on | running)
            if busy:
                raise CatalogError("host_not_allowed", f"{_turn_off_line(found, ns, busy, running)}.", 409,
                                   units=sorted(busy))
        config = registry.read_config() or {"schema": 1, "catalogs": []}
        config["catalogs"] = [({**one, "host": True} if enabled else {k: v for k, v in one.items() if k != "host"})
                              if one["id"] == ns else one for one in config["catalogs"]]
        registry.write_config(config)
    project.forget()
    return {"catalog": _row_of(ns)}


# ---------------------------------------------------------------------------------------------------------------------
# Update and Go back
# ---------------------------------------------------------------------------------------------------------------------

def _refetch(ns, entry):
    """The store of a catalog lacks an object (the cache is disposable): the branch is fetched again, with no lock held, from
    the address the entry has (the tracked one for a listed catalog), and the step that needed it is made again."""
    url, branch = (entry["shadows"].get("url"), entry["shadows"].get("branch")) if entry["kind"] == "folder" \
        else defaults.effective(entry)
    gitx.require()
    bare.fetch(gitx.parse_address(url), branch, registry.store_dir(ns), stall=30, cap=CHECK_CAP)


def _targeted(ns, op, held, entry):
    """(the commit an Update or a Go back moves to, moved): the one `_offered` gives from the tip the store holds, or
    `lock.previous`. None when there is nothing to move to. `moved`: the release installed was moved to it."""
    if op == "undo":
        if not held.get("previous"):
            raise CatalogError("no_previous", NO_PREVIOUS, 409)
        return held["previous"], False
    store_path, contract = registry.store_dir(ns), _contract()
    if store_path.is_dir() and not bare.releases_read(store_path):
        # A store fetched before this Lab read releases does not say whether the catalog makes some: an Update reads them first,
        # rather than move to the newest commit of a catalog that offers releases
        _refetch(ns, entry)
    tip = bare.resolve(store_path, "tip") if store_path.is_dir() else None
    if tip is None:
        tip = held.get("tip")
        if tip is None or not store_path.is_dir() or bare.resolve(store_path, tip) is None:
            raise bare.MissingObject(tip or "tip")
    found, moved = _offered(store_path, held, tip, contract)
    return found.commit, moved


def _on_keys(ns):
    """The function the hold rule calls for the units of `ns` that are on or up (install's `context["on"]`)."""
    def keys():
        found = project.loaded()[0]
        on, running = _used(ns, found)
        return on | running
    return keys


def _names_of(found, ns, keys):
    return [{"kind": key.partition(":")[0], "id": _unit_id(ns, *key.split(":", 1)),
             "name": _unit_name(found, ns, key)} for key in sorted(keys)]


def _items_that_include(found, going, after=None):
    """The installed items of any origin that include one of the units `going` (a set of (kind, id)), directly or through
    what they include: [{kind, id, name, needs}], `needs` the name of the first unit they lose. `after`, a {kind: {id: unit}}
    world, is what the walk is made in when it is not the installed one (an update: the catalog's units as the new version
    has them); the names are always the installed ones."""
    now = {"addon": found.get("addons") or {}, "setup": found.get("setups") or {}, "playground": found.get("playgrounds") or {}}
    units = after if after is not None else now
    names = {key: resolve.shown(now[key[0]].get(key[1], {}).get("name") or key[1], NAME_CUT) for key in going}
    broken = []
    for kind in paths.KINDS:
        for uid, unit in units[kind].items():
            if (kind, uid) in going:
                continue
            lost = next((node for node in resolve.closure(units, kind, uid)[1:] if node in going), None)
            if lost:
                broken.append({"kind": kind, "id": uid, "name": resolve.shown(unit.get("name") or uid, NAME_CUT),
                               "needs": names[lost]})
    return broken


def _after(found, ns, plan, judged):
    """The units as they will be once the plan is applied: the installed ones, with the units of the catalog
    that the plan drops left out and those it rewrites replaced by the new version's. An item of the catalog that the same
    release rewrites, or removes, is not broken by what it no longer includes."""
    changed = {(key.partition(":")[0], _unit_id(ns, *key.split(":", 1))) for key in plan["updates"] + plan["removes"]}
    dropped = {(key.partition(":")[0], _unit_id(ns, *key.split(":", 1))) for key in plan["removes"]}
    world = {}
    for kind in paths.KINDS:
        world[kind] = {uid: unit for uid, unit in (found.get(f"{kind}s") or {}).items() if (kind, uid) not in changed}
        world[kind].update({uid: (judged.get(kind) or {})[uid] for k, uid in changed - dropped
                            if k == kind and uid in (judged.get(kind) or {})})
    return world


def _growth(old, new):
    """What `new` does that `old` did not, as [(fact, value)] in the vocabulary of the preview: a port, a build, a core service,
    something read, a product's plugins, a custom seed, and what it takes of this machine."""
    found = [("port", str(port)) for port in sorted(set(new["ports"]) - set(old["ports"]))]
    before = {(one["service"], one["source"]) for one in old["builds"]}
    found += [("build", one["source"]) for one in new["builds"] if (one["service"], one["source"]) not in before]
    seen = {one["label"]: set(one["keys"]) for one in old["core"]}
    found += [("core", one["label"]) for one in new["core"] if set(one["keys"]) - seen.get(one["label"], set())]
    found += [("reads", name) for name in new["reads"] if name not in old["reads"]]
    found += [("plugins", name) for name in new["plugins"] if name not in old["plugins"]]
    if new["seed"] == "custom" and old["seed"] != "custom":
        found.append(("seed", "custom"))
    taken = {(one["service"], what) for one in old["uses_machine"] for what in one["what"]}
    found += [("host", what) for one in new["uses_machine"] for what in one["what"] if (one["service"], what) not in taken]
    return list(dict.fromkeys(found))


def _changes_of(plan, ns, judged, verdicts, old_verdicts):
    """The growth of power the update makes: for each changed unit, what its new surface does that the installed
    one did not. Lines {id, name, fact, value}."""
    changes = []
    for key in plan["updates"]:
        kind, name = key.split(":", 1)
        uid = _unit_id(ns, kind, name)
        new, old = (verdicts.get((kind, uid)) or {}).get("surface"), (old_verdicts.get((kind, uid)) or {}).get("surface")
        if new is None or old is None:
            continue
        label = judged[kind][uid]["name"] if uid in judged[kind] else name
        changes += [{"id": uid, "name": label, "fact": fact, "value": value} for fact, value in _growth(old, new)]
    return changes


def _plan(ns, op):
    """The plan of moving a catalog to the commit `pick` chose, or back to the
    previous one. Under the catalogs lock it stages the candidate, judges it against everything installed and tells what
    changes, what that breaks, what grows in power, and what holds it back. Answers the Plan of 13.2 with a ticket, or
    {"upToDate": true}."""
    entry, held = _git(ns)
    if not held.get("pin"):
        raise ValueError(f"{_called(ns)} has nothing installed yet")
    try:
        target, moved = _targeted(ns, op, held, entry)
    except bare.MissingObject:
        _refetch(ns, entry)
        target, moved = _targeted(ns, op, held, entry)
    if target is None:
        return {"upToDate": True}
    context = {"on": _on_keys(ns), "vouched": _vouched(entry), "contract": _contract(), "moved": moved}
    with _changing():
        plan = install.plan_update(ns, target, context)
        if plan.get("upToDate"):
            return {"upToDate": True}
        live = project.loaded()
        found, listed = live[0], defaults.listed(entry)
        host = (registry.inputs().get(ns) or {}).get("host") or "no"
        judged, verdicts, mine = _review(ns, plan["meta"], "git", held.get("audience") or "private", listed,
                                         "listed" if listed else "allowed", plan["ignored"], replacing=True)
        try:
            _understood(plan["meta"], judged, mine)
        except CatalogError:
            _remove_stage(ns)   # what was refused is not left to be installed
            raise
        old_verdicts = project.judge_inputs(live[1])[1]
        gone = {(key.partition(":")[0], _unit_id(ns, *key.split(":", 1))) for key in plan["removes"]}
        breaks = _items_that_include(found, gone, _after(found, ns, plan, judged))
        running = _running(found, ns)
        stop = _waiting() or _turn_off_line(found, ns, plan["held"], running)
        uses = [] if host in ("listed", "allowed") else _uses_of(judged, verdicts, mine)
        ticket = secrets.token_hex(8)
        _keep(ticket, op, ns=ns, plan=plan)
    updates = [{**one, "what": plan["touched"].get(key, [])}
               for key, one in zip(sorted(plan["updates"]), _names_of_new(plan["updates"], ns, judged))]
    return {"ticket": ticket, "expires": int(TTL), "from": _short(plan["from"]), "to": _short(plan["to"]),
            "version": plan["version"], "version_from": held.get("version"), "release": plan["release"],
            "rewritten": bool(plan["rewritten"]),
            "updates": updates, "adds": _names_of_new(plan["adds"], ns, judged),
            "removes": _names_of(found, ns, plan["removes"]), "breaks": breaks,
            "changes": _changes_of(plan, ns, judged, verdicts, old_verdicts), "whatsnew": list(plan["whatsnew"]),
            "blocked": _blocked_of(judged, mine), "uses_machine": uses, "held": stop}


def _names_of_new(keys, ns, judged):
    """The same as `_names_of` for units of the new version, named as the new catalog.yml and compose files name them."""
    return [{"kind": key.partition(":")[0], "id": _unit_id(ns, *key.split(":", 1)),
             "name": resolve.shown((judged[key.partition(":")[0]].get(_unit_id(ns, *key.split(":", 1))) or {}).get("name")
                                   or key.partition(":")[2], NAME_CUT)} for key in sorted(keys)]


def _apply(ns, ticket, confirm_history, op):
    """Puts the plan the ticket holds in place. A job that runs, an item of the catalog that is on or up,
    and a history that changed and was not confirmed refuse it; `up_to_date` when the version is already the plan's. A Go
    back needs no confirmation of a history: the version it returns to is one the person ran, on the history they confirmed
    (the plan says `rewritten` and the dialog warns, but there is nothing to decide). A change that raised halfway is finished
    or marked damaged before the failure is raised (`_unstick`)."""
    data = _held(ticket, op)
    if data.get("ns") != ns:
        raise CatalogError("ticket_expired", EXPIRED, 409)
    plan = data["plan"]
    _no_job()
    before = registry.lock_of(ns)
    try:
        with _changing():
            if before.get("pin") == plan["to"]:
                raise CatalogError("up_to_date", UP_TO_DATE, 409)
            try:
                install.apply_update(ns, plan, bool(confirm_history) or op == "undo")
            except CatalogError as error:
                if error.code == "held" and error.detail.get("units"):
                    found = project.loaded()[0]
                    raise CatalogError("held", f"{_turn_off_line(found, ns, error.detail['units'], _running(found, ns))}.", 409,
                                       units=error.detail["units"]) from None
                raise
            _settled(ns, plan, before)
    except Exception:
        _unstick(ns)
        raise
    _spend(ticket)
    with contextlib.suppress(OSError):
        layers.prepare_catalogs()
    project.forget()
    return {"catalog": _row_of(ns)}


def _settled(ns, plan, before):
    """What the lock remembers of the last check after the pin moved. An Update moved to the newest version the store holds
    (the plan's target is what `pick` chose from the tip it has), so nothing newer is on offer, whatever the last check said
    (it may have named a commit of the history that was just replaced). A Go back leaves the version it left as the newer one,
    so the row says "Update available" again and the action toggles, and going forward again leaves nothing newer."""
    seen = {key: value for key, value in _seen(registry.lock_of(ns)).items() if key != "searched"}   # the next check reads again
    if plan["op"] == "update":
        seen.update(target=plan["to"], update=False, version=plan["version"])
        registry.set_lock(ns, seen=seen)
        return
    newest = seen["target"]
    if newest is None:   # no check told which is the newest: the one the pin moved towards, if it moved forward
        forward = bare.is_ancestor(registry.store_dir(ns), plan["from"], plan["to"])
        newest = plan["to"] if forward else plan["from"]
    if plan["to"] == newest:
        seen.update(target=newest, update=False)
    else:
        seen.update(target=newest, update=True, version=before.get("version") if newest == plan["from"] else seen["version"])
    registry.set_lock(ns, seen=seen)


def update(ns, check=False, ticket=None, confirm_history=False):
    """Update: `check` answers the plan, `ticket` applies it (`confirm_history` for a catalog that was rewritten)."""
    if check:
        return _plan(ns, "update")
    return _apply(ns, ticket, confirm_history, "update")


def undo(ns, check=False, ticket=None):
    """Go back: the same plan and apply with the previous version as the target."""
    if check:
        return _plan(ns, "undo")
    return _apply(ns, ticket, False, "undo")


def changes(ns, ticket):
    """The diff for "Show changes": {"files": [{path, status, diff}]}, the files the plan changes, a unified diff for
    each, or null for one that is binary or too large (the dialog says "Changed, not shown"). The diffs are cut where a
    request must stop: past DIFF_FILES files, or DIFF_BYTES of text in all, the rest are listed and not shown (each is a
    process of git and a catalog may change thousands of files)."""
    data = _held(ticket, "update", "undo")
    if data.get("ns") != ns:
        raise CatalogError("ticket_expired", EXPIRED, 409)
    plan, store_path = data["plan"], registry.store_dir(ns)
    listing = sorted((path, status) for status, names in (("added", plan["files"]["add"]), ("changed", plan["files"]["change"]),
                                                         ("removed", plan["files"]["remove"])) for path in names)
    files, size = [], 0
    for number, (path, status) in enumerate(listing):   # in the order the dialog lists them: the first ones are shown
        text = bare.diff(store_path, plan["from"], plan["to"], path) if number < DIFF_FILES and size < DIFF_BYTES else None
        size += len(text or "")
        files.append({"path": path, "status": status, "diff": text or None})
    return {"files": files}


# ---------------------------------------------------------------------------------------------------------------------
# Remove, Repair
# ---------------------------------------------------------------------------------------------------------------------

def _stays(found, ns):
    """What the playgrounds of the catalog created in Gravitee and the record still holds, {what: count}, or None."""
    from ..library import playgrounds   # here and not above: it reads the layers, which look for this module
    total = {}
    for uid, unit in (found.get("playgrounds") or {}).items():
        if unit.get("catalog") == ns:
            for one in playgrounds.created(uid):
                total[one["what"]] = total.get(one["what"], 0) + one["count"]
    return total or None


def _removal(ns, entry):
    """Remove with check: {held, items, folder, breaks, stays}."""
    held = registry.lock_of(ns)
    if held.get("shadowed") or (entry["kind"] == "folder" and entry.get("shadows")):
        raise CatalogError("linked", install.LINKED, 409)
    found = project.loaded()[0]
    units = registry.units_of(ns)
    going = {(kind, _unit_id(ns, kind, name)) for kind, names in units.items() for name in names}
    return {"held": _hold(ns, None, found), "items": sum(len(names) for names in units.values()),
            "folder": entry["kind"] == "folder", "breaks": [{k: one[k] for k in ("kind", "id", "name")}
                                                            for one in _items_that_include(found, going)],
            "stays": _stays(found, ns)}


def remove(ns, check=False, keep_settings=True):
    """Remove: with `check`, what it would do and what holds it; without, the removal. A job that runs and an item of the
    catalog that is on or up refuse it (`job_running`, `held`); a folder catalog loses its link and nothing else, but for an
    imported playground's, whose folder the Lab keeps: it goes, but for the person's settings with `keep_settings`. Answers
    {"warning": ...} (the line of `removed_partial` when Docker made files that could not be deleted)."""
    entry = _entry(ns)
    if check:
        return _removal(ns, entry)
    if not isinstance(keep_settings, bool):
        raise ValueError("keep_settings must be true or false")
    _no_job()
    with _changing():
        found = project.loaded()[0]
        on, running = _used(ns, found)
        if on | running:
            raise CatalogError("held", f"{_turn_off_line(found, ns, on | running, running)}.", 409,
                               units=sorted(on | running))
        done = install.remove(ns, keep_settings)
        warning = None
        if entry.get("kind") == "folder":   # an imported playground's catalog is the Lab's own folder: it goes with it, under the lock
            from . import exports   # here and not above: exports reads this module
            warning = exports.forget_folder(entry, keep_settings)
    project.forget()
    return {"warning": done.get("warning") or warning}


def repair(ns):
    """Repair: the install is made again at the pin from the local store. A store that lacks an object is fetched again
    with no lock held () and the repair is made again. Answers {"catalog": Row}."""
    entry, _ = _git(ns)
    _no_job()
    try:
        try:
            with _changing():
                install.repair(ns)
        except bare.MissingObject:
            _refetch(ns, entry)
            with _changing():
                install.repair(ns)
    except Exception:
        _unstick(ns)
        raise
    project.forget()
    return {"catalog": _row_of(ns)}


def unshadow(ns):
    """Stops a working copy standing in for a git catalog: the install is made again from the store, which is fetched
    again first when the cache was cleared meanwhile (), from the address the entry remembers. Answers what
    `install.unshadow` does."""
    entry = _entry(ns)
    if entry["kind"] != "folder" or not entry.get("shadows"):
        raise ValueError(f"{_called(ns)} is not a catalog that a working copy stands in for")
    try:
        with _changing():
            done = install.unshadow(ns)
    except bare.MissingObject:
        _refetch(ns, entry)
        with _changing():
            done = install.unshadow(ns)
    project.forget()
    return done


# ---------------------------------------------------------------------------------------------------------------------
# The gate at Start, a catalog from the template, and the start of the Lab
# ---------------------------------------------------------------------------------------------------------------------

def preflight(found, closure):
    """The gate at Start, asked by `layers.check` for a start that puts a unit of a catalog in the on file: (a) each
    unit of `closure` ([(kind, id)]) is judged again on the project as it is now, since `.env` and the shell change what
    Compose says without a file changing; (b) the installed files of the units of a git catalog are the ones that were
    installed (the sha256 of the manifest). Raises RuntimeError with the line to show; `damaged` (a CatalogError too) for a
    file that changed after the install, which the lock then records, so that the row of the catalog says Damaged and offers
    Repair (a refusal is gone with its request; the state stays until Repair)."""
    project.forget()
    fresh = project.load()
    blocked = layers.blocked_files(fresh)
    wanted = {}
    for kind, uid in closure:
        unit = (fresh.get(f"{kind}s") or {}).get(uid)
        if unit is None:   # it was there for the caller and is not now: a unit of a catalog whose manifest went is gone with it
            old = ((found or {}).get(f"{kind}s") or {}).get(uid) or {}
            if old.get("catalog") not in (None, paths.LOCAL):
                _mark_damaged(old["catalog"])
                raise Refused("damaged", f"{resolve.shown(old.get('name') or uid, NAME_CUT)} changed on disk after it was installed. Repair "
                              f"{_called(old['catalog'])}, then start it again.", 409, id=old["catalog"])
            continue
        if unit.get("file") in blocked:
            raise RuntimeError(f"{resolve.shown(unit['name'], NAME_CUT)} is blocked: {blocked[unit['file']]}")
        ns = unit.get("catalog")
        if ns and ns != paths.LOCAL:
            wanted.setdefault(ns, []).append((kind, uid, unit))
    for ns, units in wanted.items():
        entry = registry.entry(ns)
        if entry is None or entry["kind"] != "git" or registry.lock_of(ns).get("shadowed"):
            continue
        keys = [f"{kind}:{uid.rsplit('/', 1)[-1]}" for kind, uid, _ in units]
        try:
            changed = install.verify(ns, keys)
        except CatalogError as error:
            if error.code == "damaged":
                _mark_damaged(ns)
            raise Refused(error.code, error.message, error.status, **error.detail) from None
        if changed:
            _mark_damaged(ns)
            unit = install.unit_key(changed[0])
            name = next((resolve.shown(one["name"], NAME_CUT) for kind, uid, one in units
                         if f"{kind}:{uid.rsplit('/', 1)[-1]}" == unit), _called(ns))
            raise Refused("damaged", f"{name} changed on disk after it was installed. Repair {_called(ns)}, then start it "
                          "again.", 409, id=ns, files=changed)


def _folder_name(text):
    """The id and the name a New catalog gets from what the person typed: the slug of the name, the NS grammar, a numeric
    suffix on a clash with a catalog or a reserved word; `catalog_invalid` when the name has no usable slug."""
    name = " ".join(str(text or "").split())
    slug = paths.slug(name)[:22].strip("-")
    if not re.fullmatch(paths.NS, slug or "-") or not name:
        raise CatalogError("catalog_invalid", f"catalog.yml: {manifest.NAMESPACE_PROBLEM}.", 400)
    taken = {one["id"] for one in registry.entries()} | set(paths.RESERVED_NS)
    ns, number = slug, 1
    while ns in taken:
        number += 1
        ns = f"{slug[:22 - len(str(number))].rstrip('-')}-{number}"
    return ns, name[:60]


def create_folder(name):
    """New catalog: a folder catalog made from the template under the home folder and linked, as `catalog.py link` does
    (it is the Add of a folder). The id is the slug of the name; the folder is ~/gravitee-lab-catalogs/<id> and must not exist
    (`folder_exists`). The Lab does not run `git init`. Answers {"catalog": Row}."""
    with _creating:
        ns, label = _folder_name(_text(name, "name"))
        folder = Path(os.path.expanduser("~")) / "gravitee-lab-catalogs" / ns
        if os.path.lexists(folder):
            raise CatalogError("folder_exists", f"There is already a catalog folder named {ns}. Choose another name.", 409)
        from . import standard   # here and not above: the template is rendered only for this
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                standard.create(folder, {"id": ns, "name": label, "local": True})
            except standard.TemplateError as error:   # a name the template refuses (too long, one the Lab keeps): said as it is
                raise CatalogError("catalog_invalid", str(error), 400) from None
            shown = check_address(str(folder))
            return add(shown["ticket"])
        except BaseException:   # a folder this call made and could not link is not left to answer folder_exists for ever
            if registry.entry(ns) is None:
                shutil.rmtree(folder, ignore_errors=True)
            raise


def recover():
    """The sweeper and the completion of what a stop left, run after the port is bound: `install.recover`
    under the catalogs lock tried once. Another Lab that is writing holds it, and then there is nothing to do here."""
    try:
        with registry.flock(wait=0):
            install.recover()
    except CatalogError as error:
        if error.code != "busy":
            raise
