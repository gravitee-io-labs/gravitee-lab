"""The authors' and CI's command line: link a folder as a
catalog, check it, index it, add and update catalogs without a page, start over, write the file an employee needs, make a
catalog from the template and keep it in step with the Lab's standard. `python3 -I lab-ui/catalog.py <command>`.

    check [PATH or ID] [--ci] [--base COMMIT] [--pr NUMBER] [--online] [--history] [--strict] [--soft] [--error CODE]
                                      every rule that needs no Docker and no network; a folder is read where it is
    explain CODE                      the long form of a rule code
    new DIR --id ID --name NAME --maintainers @A[,@B]   a catalog from the template          (standard.main_new)
    upgrade [DIR] [--write] [--commit]  --fleet OWNER --lab-ref COMMIT       the managed files, at the Lab's level
                                                                             (standard.main_upgrade, fleet.main)
    release VERSION [--tag] [--pr]    prepare a release, then tag it                         (release.main)
    repo-settings [OWNER/REPO or DIR] audit and apply the repository standard on GitHub      (settings.main)
    ci STAGE ...                      the stages the managed workflows call                  (ci.main)
    link PATH [--allow-machine]       a folder as a catalog (a working copy takes the place of an installed one)
    unlink ID                         the way back; the folder is never touched
    add ADDRESS [--branch B] [--yes]  the preview, and with --yes the Add
    provision                         the first start, from a terminal: download what has no copy yet, then migrate
    list                              the catalogs and their state
    update ID [--yes [--confirm-history]]  undo ID [--yes]   the plan, and with --yes the change
    remove ID [--delete-settings]
    suggest ADDRESS --name NAME [--description TEXT] [--allow-machine]   one entry of the suggestions file
    reset [--empty]                   start over: the next start is a first run, or has no catalog at all
    index [PATH or ID] [--out FILE]   the generated catalog.json, of a folder as it is or of an added catalog
    images ID [--dry-run]             build the images a catalog builds
    matrix ID --changed BASE HEAD     the end-to-end matrix of a change

The five commands marked with a module are run by it: everything after their name is handed over as it is. Of those,
`new`, `upgrade` and `release` answer `--help` here (HELP): their modules have none, and `repo-settings` and `ci` have their own.

Exit 0 when it worked, 1 when it found a problem or an upgrade is pending or it was refused, 2 for a usage or environment error
(not a catalog, a tool missing) or a fault of the tool (a crash is never
taken for a verdict on a catalog). It reads and writes what the Lab UI does, through the same modules; `check` and `index` write
nothing and link nothing; whatever a catalog wrote is shown as text, never as a sequence for the terminal."""

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

from .. import paths
from ..docker import compose
from ..library import check, project
from . import audit, defaults, gitx, install, manifest, registry, report
from . import store as bare
from . import tree as trees
from .registry import CatalogError
from .report import Problem

# The refusals that are the person's usage or this machine's state, not a problem found in a catalog: exit 2
ENVIRONMENT = ("bad_address", "bad_address_secret", "folder_missing", "folder_not_catalog", "folder_not_shared",
               "branch_missing", "not_listed", "symlink_unsupported", "git_missing", "git_old", "docker_missing", "unread",
               "busy")
FORMATS = ("text", "github", "json")
SUGGEST_LIMIT = 50   # as many entries as the Lab reads of the suggestions file
POLL = 0.2   # seconds between two looks at where a download is
LAB = paths.ROOT   # the Lab checkout this command runs from: the folder that holds lab-ui
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
RULE_CODE = re.compile(r"[A-Z][A-Z0-9]*[0-9]{2}")
SUMMARY_COMMAND = "make check"   # the one command that reproduces what the summary of a run lists
ZEROS = re.compile(r"0{40}(?:0{24})?")   # the `before` of GitHub on the first push of a branch: no commit came before


class Usage(Exception):
    """The command cannot be run as asked, or here (exit 2): the line says why and what to do."""


def say(text=""):
    """A line on standard output. What a catalog wrote (a name, a reason, a problem of its catalog.yml) is shown as text: no
    control character or escape sequence of it reaches the terminal."""
    print(check.printable(text), flush=True)


def complain(text):
    print(check.printable(text), file=sys.stderr, flush=True)


def _lab(name):
    """A module of lab_ui.catalogs, imported when a command needs it: a command that does not run starts nothing it does not use."""
    return importlib.import_module(f"lab_ui.catalogs.{name}")


def _compose():
    """Compose 5 or later is what reads a catalog: without it every unit would be refused for a reason that is this machine's,
    so the command stops (exit 2) and says what is needed. `check` does not stop: it says what it did not check."""
    if check.compose_missing():
        raise Usage("the Docker CLI with Compose 5 or later is needed to read a catalog (a running daemon is not)")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lines(error):
    """What a refusal says: every problem of a catalog.yml when it has several (read_catalog), else its line."""
    problems = (getattr(error, "detail", None) or {}).get("problems")
    return [f"catalog.yml: {problem}." for problem in problems] if problems else [error.message]


# ---------------------------------------------------------------------------------------------------------------------
# link and unlink
# ---------------------------------------------------------------------------------------------------------------------

def _folder_catalog(text):
    """(the folder as an absolute path, the catalog.yml it holds as read) for a folder that is a catalog: it exists, it is
    under the home folder, its catalog.yml is the block and nothing else and passes the table of 4.1, and its namespace is
    one this machine may give (not reserved unless the engine lists it). A CatalogError says which of these fails."""
    address = gitx.parse_address(os.path.abspath(os.path.expanduser(text)))
    listed = [one["id"] for one in defaults.read()]
    wanted = Path(address.path) / manifest.FILE
    try:
        with open(wanted, "rb") as handle:
            found = registry.scan(handle.read(registry.SUMMARY_LIMIT)).get("id")
    except OSError:
        found = None
    borrowed = [one["name"] for one in defaults.read() if one["id"] != found]
    meta = manifest.read_catalog(str(wanted), address, borrowed)
    registry.check_namespace(meta["id"], listed=listed)
    return address.path, meta


def _point(link, folder):
    """The link at `link` made to lead to `folder`, in one step: a link of another name is made first and renamed over the
    place, so that no reader sees it missing or half made. A real folder in the place is not replaced."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".link-{link.name}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        os.symlink(folder, temporary, target_is_directory=True)
        os.replace(temporary, link)
    except OSError as error:
        if os.path.lexists(temporary):
            os.unlink(temporary)
        if isinstance(error, (IsADirectoryError, FileExistsError, NotADirectoryError)):
            raise
        raise CatalogError("symlink_unsupported", "The Lab cannot link folders on this system. Use WSL 2.", 400) from None


def _linked(ns, folder, host):
    """A folder linked as catalog `ns` that no git install stands in the place of: the link, then its entry in
    catalogs.json. Idempotent: a folder that is linked already is linked again at no cost, and its allowance is set. The
    folder of another link is the new one once nothing of the catalog is on."""
    entry = registry.entry(ns)
    link = Path(paths.ROOT) / paths.CATALOGS_DIR / ns
    if os.path.lexists(link) and not os.path.islink(link):   # a folder of its own: another catalog, or a leftover
        registry.check_namespace(ns, taken=[ns], listed=[one["id"] for one in defaults.read()])   # name_taken
    same = os.path.islink(link) and os.readlink(link) == folder
    if not same:
        busy = sorted(install.on_units(ns)) if entry else []
        if busy:
            raise CatalogError("held", f"Turn off {busy[0].partition(':')[2]} first.", 409, units=busy)
        _point(link, folder)
    row = {"id": ns, "kind": "folder", "path": folder, "added": (entry or {}).get("added") or _utc()}
    if host and not defaults.listed({"id": ns, "kind": "folder"}):
        row["host"] = True
    elif (entry or {}).get("host") and not defaults.listed({"id": ns, "kind": "folder"}):
        row["host"] = True   # allowed before, and not taken back by linking again
    if entry:
        row.update({key: entry[key] for key in entry if key not in row and key not in ("kind", "path", "url", "branch", "auto")})
    if entry != row:
        config = registry.read_config() or {"schema": 1, "catalogs": []}
        config["catalogs"] = [row if one["id"] == ns else one for one in config["catalogs"]] \
            if entry else [*config["catalogs"], row]
        registry.write_config(config)
    return row


def link(args):
    """Validate the folder, then, by what the machine holds under that id: nothing, or a folder catalog (a
    link and an entry), or an installed git catalog (the working copy takes its place: install.shadow). Never reconciles
    the default file: in the CI of a catalog, a listed id with nothing installed is a plain link."""
    _compose()
    folder, meta = _folder_catalog(args.path)
    ns = meta["id"]
    if registry.config_problem():
        raise Usage(f"{registry.config_problem()}")
    with registry.flock():
        entry, host = registry.entry(ns), args.allow_machine
        if entry is not None and entry["kind"] == "git":
            held = registry.lock_of(ns)
            if held.get("pin") or held.get("pending"):
                install.shadow(ns, folder)
                say(f"Linked {ns}. Edit it in place; stop with catalog.py unlink {ns}")
                return 0
            # Declared and never downloaded (the first download failed, the repository is private and cannot be reached, or
            # nothing started it): there is no install to stand in for, and the author's loop starts from exactly here
            install.remove(ns)
            host = host or entry.get("host") is True
        _linked(ns, folder, host)
    project.forget()
    say(ns)
    return 0


def _fetch_again(ns, entry):
    """Fetches into the store of `ns` the address that the working copy stands in for, so that the version that was installed
    is there to restore (the store is a cache, and may have been cleared)."""
    git = entry.get("shadows") or {}
    address = gitx.parse_address(git.get("url") or "")
    bare.fetch(address, gitx.asked_branch(git.get("branch")), registry.store_dir(ns))


def _give_back(ns):
    """One try at ending the link of `ns`, under the catalogs lock. The entry of a working copy that stands in the place of an
    install when the store lacks the version to restore (`fsck`), so that the caller fetches it with no lock held; None once
    the link is gone."""
    with registry.flock():
        entry = registry.entry(ns)
        if entry is None or entry["kind"] != "folder":
            raise Usage(f"{ns} is not linked to a folder")
        if not entry.get("shadows"):
            install.remove(ns)
            return None
        try:
            install.unshadow(ns)
        except CatalogError as error:
            if error.code != "fsck":
                raise
            return entry
    return None


def unlink(args):
    """The way back of link: the link goes and the folder is never touched. A working copy that stood in the place of an
    installed catalog gives it back (install.unshadow); a store that was cleared meanwhile is fetched again, with no lock held
    while the network is waited for, and the step is tried once more."""
    ns = args.id
    entry = _give_back(ns)
    if entry is not None:
        _fetch_again(ns, entry)
        entry = _give_back(ns)
    if entry is not None:
        pin = (registry.lock_of(ns).get("pin") or "")[:7]
        where = registry.display_address(entry["shadows"].get("url") or "")
        raise CatalogError("fsck", f"{where} no longer has the version of {ns} that was installed ({pin}), so the "
                           f"link cannot be undone. Restore that commit there, then run unlink again.", 409)
    project.forget()
    say(f"Unlinked {ns}.")
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# What the page does, without the page: add, update, undo, remove, provision, list
# ---------------------------------------------------------------------------------------------------------------------

def _count(counts):
    names = (("playground", "playgrounds"), ("setup", "setups"), ("addon", "add-ons"))
    return ", ".join(f"{counts[kind]} {plural}" for kind, plural in names if counts.get(kind))


def _preview_lines(preview):
    """The Preview of the Add as lines: what the catalog is, what it brings, what it asks of this machine."""
    meta, rows = preview["catalog"], preview.get("rows") or {}
    lines = [f"{meta['name']} {meta.get('version') or ''} ({preview.get('address')}, {preview.get('audience')})".replace("  ", " ")]
    if preview.get("counts"):
        lines.append(f"  {_count(preview['counts'])}")
    builds = rows.get("builds") or {}
    if builds.get("local") or builds.get("remote"):
        lines.append("  Builds: " + ", ".join([f"{builds['local']} here"] * bool(builds.get("local"))
                                               + list(builds.get("remote") or [])))
    for key, label in (("ports", "Ports"), ("core", "Core"), ("reads", "Reads"), ("plugins", "Plugins")):
        if rows.get(key):
            lines.append(f"  {label}: " + ", ".join(str(one) for one in rows[key]))
    for use in rows.get("uses_machine") or []:
        lines.append(f"  Uses this machine: {use.get('name')} ({', '.join(use.get('what') or [])})")
    for need in rows.get("needs") or []:
        lines.append(f"  Needs: {need.get('name')} {need.get('min') or ''}".rstrip())
    for found in rows.get("found") or []:
        lines.append(f"  Settings found: {found.get('name')}")
    for one in preview.get("blocked") or []:
        lines.append(f"  Cannot run: {one.get('name')} ({'; '.join(one.get('reasons') or [])})")
    return lines


def _plan_lines(plan):
    """The Plan of an Update or a Go back as lines."""
    if plan.get("upToDate"):
        return ["Already up to date."]
    version = plan.get("version")
    if version and plan.get("version_from") and plan["version_from"] != version:
        version = f"{plan['version_from']} → {version}"
    if version and "release" in plan and not plan["release"]:
        version += " (unreleased)"
    lines = [f"From {plan.get('from')} to {plan.get('to')}" + (f", version {version}" if version else "")]
    if plan.get("rewritten"):
        lines.append("  This catalog was rewritten or moved.")
    for key, label in (("updates", "Updates"), ("adds", "Adds"), ("removes", "Removes")):
        if plan.get(key):
            lines.append(f"  {label}: " + ", ".join(one.get("name") or one.get("id") for one in plan[key]))
    for one in plan.get("breaks") or []:
        lines.append(f"  Breaks: {one.get('name')} (needs {one.get('needs')})")
    for one in plan.get("changes") or []:
        lines.append(f"  Changes: {one.get('name')} ({one.get('fact')} {one.get('value')})")
    for one in plan.get("blocked") or []:
        lines.append(f"  Cannot run: {one.get('name')} ({'; '.join(one.get('reasons') or [])})")
    lines += [f"  New: {subject}" for subject in plan.get("whatsnew") or []]
    if plan.get("held"):
        lines.append(f"  Held: {plan['held']}")
    return lines


def add(args):
    """The Add of the page without its dialog: the preview is printed, and it is applied only with --yes."""
    service = _lab("service")
    preview = service.check_address(args.address, args.branch)
    for line in _preview_lines(preview):
        say(line)
    if not args.yes:
        say("Run it again with --yes to add it.")
        return 0
    answer = service.add(preview["ticket"])
    say(f"Added {(answer.get('catalog') or {}).get('name') or preview['catalog']['name']}.")
    return 0


def _change(args, action, verb):
    """Update and Go back: the plan is printed; with --yes it is applied."""
    service = _lab("service")
    if action == "update":
        service.check_now(args.id)   # the plan reads the store: ask the address for news first, as the page's Check again does
    plan = getattr(service, action)(args.id, check=True)
    for line in _plan_lines(plan):
        say(line)
    if plan.get("upToDate") or plan.get("held"):
        return 0 if plan.get("upToDate") else 1
    if not args.yes:
        say("Run it again with --yes to apply it.")
        return 0
    extra = {}
    if action == "update" and plan.get("rewritten"):
        # The protection of a history that changed is not lifted by the word that applies any plan: a script that passes --yes
        # has not read this warning
        if not getattr(args, "confirm_history", False):
            complain(f"{args.id} was rewritten or moved: run it again with --yes --confirm-history if you expected that.")
            return 1
        extra = {"confirm_history": True}
    getattr(service, action)(args.id, ticket=plan["ticket"], **extra)
    say(f"{verb} {args.id}.")
    return 0


def update(args):
    return _change(args, "update", "Updated")


def undo(args):
    return _change(args, "undo", "Went back")


def remove(args):
    """Remove: what would go is printed, and it goes unless an item of the catalog is on (the held rules apply)."""
    service = _lab("service")
    removal = service.remove(args.id, check=True)
    if removal.get("held"):
        complain(removal["held"])
        return 1
    for one in removal.get("breaks") or []:
        say(f"Breaks: {one.get('name')}")
    answer = service.remove(args.id, keep_settings=not args.delete_settings)
    say(f"Removed {args.id}.")
    if answer.get("warning"):
        complain(answer["warning"])
    return 0


def list_catalogs(args):
    """The catalogs and their state, one per line: the id the other commands take, the state, the version, the address."""
    service = _lab("service")
    built, inputs = project.loaded()
    for row in service.describe(built, inputs):
        state = (row.get("state") or {}).get("label") or (row.get("state") or {}).get("key") or ""
        say("  ".join([str(row.get("id") or "-"), state, str(row.get("version") or "-"), str(row.get("address") or "")]).rstrip())
    return 0


def _progress(firstrun, ns, stop):
    """Says where a download is while `firstrun.provision` runs: a line when its phase changes."""
    last = None
    while not stop.wait(POLL):
        found = (getattr(firstrun, "PROGRESS", None) or {}).get(ns)
        phase = found.get("phase") if isinstance(found, dict) else None
        if phase and phase != last:
            say(f"  {phase}")
            last = phase


def provision(args):
    """The first start from a terminal, in the foreground. The intent is written when catalogs.json does not exist,
    every git entry that has no copy is downloaded, and the migration of an old layout runs when it waits. Exit 1, with the
    line of the error, when something fails."""
    firstrun = _lab("firstrun")
    layout = importlib.import_module("lab_ui.library.layout")
    firstrun.reconcile()
    failed = 0
    for entry in registry.entries():
        ns = entry["id"]
        if entry["kind"] != "git" or registry.lock_of(ns).get("pin"):
            continue
        say(f"Downloading {registry.display_address(entry['url'])}")
        stop = threading.Event()
        watcher = threading.Thread(target=_progress, args=(firstrun, ns, stop), daemon=True)
        watcher.start()
        try:
            row = firstrun.provision(ns)
            problem = (row or {}).get("error") if isinstance(row, dict) else None
            if problem:
                raise CatalogError(str(problem), str((row.get("state") or {}).get("reason") or problem), 400)
            say(f"Installed {(row or {}).get('name') or ns}")
        except CatalogError as error:
            complain(f"{ns}: {error.message}")
            failed += 1
        finally:
            stop.set()
            watcher.join()
    if layout.waiting():
        report = layout.migrate()
        moved = len(report.get("moved") or [])
        say(f"Moved {moved} item{'' if moved == 1 else 's'} of the old layout")
        for line in report.get("problems") or []:
            complain(str(line))
            failed += 1
    return 1 if failed else 0


# ---------------------------------------------------------------------------------------------------------------------
# suggest and reset: the opt-in and the opt-out
# ---------------------------------------------------------------------------------------------------------------------

def _suggestions(file):
    """The suggestions file as it is: {"schema": 1, "catalogs": [...]}, or an empty one when there is none. A file that is
    there and cannot be read is not replaced: the person's own is not overwritten by a tool."""
    if not os.path.lexists(file):
        return {"schema": 1, "catalogs": []}
    try:
        with open(file, "rb") as handle:
            data = json.loads(handle.read(registry.SUGGESTIONS_LIMIT + 1).decode("utf-8-sig"))
    except (OSError, ValueError):
        data = None
    if not (isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("catalogs"), list)):
        raise Usage(f"{registry.display_address(file)} cannot be read as a list of suggestions: fix it or remove it first")
    return data


def suggest(args):
    """One entry in the suggestions file, created or extended, so that nobody writes the JSON by hand. The same
    repository again replaces its entry. Writes that file and nothing else."""
    address = gitx.parse_address(args.address)
    if address.kind != "git":
        raise gitx.fail("bad_address")
    name = registry.shown(args.name, 40)
    if not name:
        raise Usage("--name must say what the catalog is called: it is empty once cleaned of control characters and spaces")
    file = registry.suggestions_file()
    data = _suggestions(file)
    entry = {"name": name}
    if args.description:
        entry["description"] = registry.shown(args.description, 90)
    entry["url"] = address.url
    if args.allow_machine:
        entry["host_access"] = True
    mine = registry.display_address(address.url)
    kept = [one for one in data["catalogs"] if not (isinstance(one, dict) and isinstance(one.get("url"), str)
                                                    and registry.display_address(one["url"]) == mine)]
    if len(kept) >= SUGGEST_LIMIT:
        raise Usage(f"the Lab reads at most {SUGGEST_LIMIT} suggestions")
    data["catalogs"] = [*kept, entry]
    Path(file).parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(Path(file), json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=0o600)
    say(f"Suggested {entry['name']} in {registry.display_address(file)}")
    return 0


def reset(args):
    """Starts over. Every catalog goes with its settings kept (the held rules apply: nothing is changed while
    an item of one is on) and `.lab/catalogs.json` is deleted, so that the next start is a first run; with --empty it is an
    empty list instead, so that the Lab adds nothing. The personal area stays. A catalog that a working copy stands in for is
    unlinked first; so is it said before anything is changed."""
    if registry.config_problem():
        raise Usage(registry.config_problem())
    with registry.flock():
        entries = registry.entries()
        for one in entries:
            busy = sorted(install.on_units(one["id"]))
            if busy:
                raise CatalogError("held", f"Turn off {busy[0].partition(':')[2]} first.", 409, units=busy)
            if one["kind"] == "folder" and one.get("shadows"):
                raise CatalogError("linked", f"{one['id']} is linked to a folder on this machine. Unlink it first, with "
                                   f"catalog.py unlink {one['id']}.", 409)
        for one in entries:
            install.remove(one["id"], keep_settings=True)
        if args.empty:
            registry.write_config({"schema": 1, "catalogs": []})
        else:
            file = Path(paths.ROOT) / registry.CONFIG_FILE
            if os.path.lexists(file):
                os.unlink(file)
    project.forget()
    say("The catalogs are empty." if args.empty else "The next start is a first run.")
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# check: every rule that needs no Docker and no network, over a folder where it is, an added catalog or a pull request
# ---------------------------------------------------------------------------------------------------------------------

class Target(NamedTuple):
    """What `check` was asked about: `kind` is `folder` (read where it is) or `catalog` (an added one: its install or the folder it
    is linked to); `path` the real folder; `ns` the catalog's id for an added one."""
    kind: str
    path: str
    ns: str


class Items(NamedTuple):
    """The second half of a check: the problems of the items, the files to list, the units for the rules of the images (None when
    they were not read), and what was not checked."""
    problems: list
    files: list
    units: Optional[list]
    skipped: list


def _target(text):
    """The folder or the added catalog a check is about: a text that names a folder is that folder, unless it is the id of an
    added catalog and not written as a path; nothing is the current folder. A folder is not linked and not written."""
    wanted = text or "."
    named = os.path.expanduser(wanted)
    pathlike = wanted in (".", "..") or os.sep in wanted or wanted.startswith(("~", "."))
    if os.path.isdir(named) and (pathlike or registry.entry(wanted) is None):
        return Target("folder", os.path.realpath(named), "")
    if pathlike or not re.fullmatch(paths.NS, wanted):
        raise Usage(f"{check.printable(wanted)[:200]} is not a folder")
    if registry.entry(wanted) is None:
        raise Usage(f"{wanted} is neither a folder nor a catalog installed or linked here")
    return Target("catalog", os.path.realpath(Path(paths.ROOT) / paths.CATALOGS_DIR / wanted), wanted)


CATALOG_SIGNS = (manifest.FILE, ".github/standard.json", "addons", "setups", "playgrounds")


def _is_catalog(folder):
    """Whether a folder has anything of a catalog: its catalog.yml, the file of the standard, or one of the three folders of
    units. A folder with none is not one, and a check of it would only list what it is not."""
    return any(os.path.lexists(os.path.join(folder, sign)) for sign in CATALOG_SIGNS)


def _codes(values):
    """The codes of `--error CODE[,CODE]` (repeatable), upper case, each shaped like a rule code."""
    codes = []
    for value in values or ():
        for one in value.split(","):
            one = one.strip().upper()
            if not RULE_CODE.fullmatch(one):
                raise Usage(f"--error takes rule codes such as ST05 or DF22, separated by commas: {check.printable(one)[:40]}")
            codes.append(one)
    return tuple(dict.fromkeys(codes))


def _descriptor(folder, summary, notes):
    """(the namespace the folder's catalog.yml gives, []) or (None, the problems of that file): the namespace is the id it
    declares, valid and one this machine may give. Read quickly as text; when that does not give one, with Compose, which is
    what says each problem of the file (the table of 4.1), as `catalog.py link` does."""
    listed = [one["id"] for one in defaults.read()]
    found = summary.get("id")
    if isinstance(found, str) and re.fullmatch(paths.NS, found):
        try:
            registry.check_namespace(found, listed=listed)
            return found, []
        except CatalogError as error:
            notes.append("the items: the id in catalog.yml cannot be used, so the units were not read")
            return None, [Problem("", report.ERROR, manifest.FILE, 0, error.message)]
    if not os.path.lexists(Path(folder) / manifest.FILE):
        notes.append("the items: there is no catalog.yml, so the units were not read")
        return None, []
    borrowed = [one["name"] for one in defaults.read() if one["id"] != found]
    try:
        with check.scrubbed():
            manifest.read_catalog(str(Path(folder) / manifest.FILE), None, borrowed)
    except CatalogError as error:
        notes.append("the items: catalog.yml is not valid, so the units were not read")
        return None, [Problem("", report.ERROR, manifest.FILE, 0, line[len("catalog.yml: "):] if line.startswith("catalog.yml: ")
                              else line) for line in _lines(error)]
    notes.append("the items: catalog.yml gives no id this machine can use, so the units were not read")
    return None, []


def _measure(address):
    """"public" when an anonymous ls-remote answers, "private" when it is refused, None when the remote could not be reached at all
    (then nothing is known): the Lab's own measure at Add."""
    try:
        gitx.ls_remote(address, None, anonymous=True)
    except CatalogError as error:
        return None if error.code in ("unreachable", "timeout", "certificate", "git_missing", "git_old") else "private"
    except OSError:
        return None
    return "public"


def _audiences(folder, ns, summary, notes):
    """What `--online` measures for the policy (T24): {namespace: public or private} for this catalog, from its `origin`, and for
    each catalog it requires, from the address it declares. One that could not be measured is said, and not guessed."""
    found = {}
    wanted = [(dep, want.get("url")) for dep, want in sorted((summary.get("requires_catalogs") or {}).items())]
    if not wanted:   # a catalog that includes nothing of another is judged on no audience
        return found
    try:
        done = gitx.run(["remote", "get-url", "origin"], cwd=folder, timeout=10)
        origin = done.out.decode("utf-8", "replace").strip() if done.code == 0 else ""
    except (CatalogError, OSError):
        origin = ""
    for name, url in [(ns, origin), *wanted]:
        try:
            audience = _measure(gitx.parse_address(url)) if url else None
        except CatalogError:
            audience = None
        if audience:
            found[name] = audience
        else:
            notes.append(f"T24: whether {name} is public could not be measured, so what it includes was not compared with that")
    return found


def _items(target, folder, summary, args, notes):
    """The items half of a check, with Compose: `catalog_problems` and the units for the rules of the images. An added catalog
    is read in the Lab's project. A folder is read through a temporary link into a copy of it (check.temporary_link), so that the
    Lab's own folder and `catalogs.json` stay as they are. Answers Items."""
    if target.kind == "catalog":
        try:
            check.available(target.ns)
        except check.Unavailable as error:
            raise Usage(str(error)) from None
        with check.scrubbed():
            built, inputs = check.loaded()
            try:
                found = check.catalog_problems(target.ns, built, inputs, audited=True)
            except check.ComposeGone:
                return Items([], [], None, [check.NO_COMPOSE])
            return Items(check.as_problems(found, built, target.ns, (paths.ROOT, target.path)), check.catalog_files(target.ns),
                         check.audit_units(target.ns, built, inputs), [])
    ns, problems = _descriptor(folder, summary, notes)
    if ns is None:
        return Items(problems, [], None, [])
    needs = sorted(summary.get("requires_catalogs") or {})
    try:
        check.needs_here(ns, needs, registry.inputs())
    except check.Unavailable as error:
        raise Usage(str(error)) from None
    audiences = _audiences(target.path, ns, summary, notes) if args.online else None   # its origin is in the folder it was cloned to
    with check.temporary_link(folder, ns, needs, audiences) as linked, check.scrubbed():
        built, inputs = check.loaded()
        try:
            found = check.catalog_problems(ns, built, inputs, audited=True)
        except check.ComposeGone:
            return Items([], [], None, [check.NO_COMPOSE])
        hide = (linked.root / paths.CATALOGS_DIR / ns, linked.root, linked.lab, linked.folder)
        return Items(check.as_problems(found, built, ns, hide), check.catalog_files(ns), check.audit_units(ns, built, inputs), [])


class Pulled(NamedTuple):
    """A pull request, fetched: the folder that holds the files to hand to Compose, and what the audit reads in place of the folder."""
    folder: str
    options: dict
    has_seeder: bool


def _pull_number(spec, origin):
    """The number a `--pr` names: digits, or the address of a pull request of the catalog's origin."""
    if re.fullmatch(r"[1-9][0-9]{0,8}", spec):
        return int(spec)
    found = re.fullmatch(r"(?:https://)?(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/([1-9][0-9]{0,8})/?", spec)
    if not found:
        raise Usage("--pr takes the number of a pull request, or its address")
    mine = registry.display_address(origin).lower() if origin else ""
    if mine != f"github.com/{found.group(1)}/{found.group(2)}".lower():
        raise Usage("that pull request is not one of this catalog's origin")
    return int(found.group(3))


def _write_unit_file(root, tree, entry):
    """One file of a pull request, written below `root` as a plain file, never through a link: a name that is not a clean path
    is not written."""
    parts = entry.path.split("/")
    if any(part in ("", ".", "..") or "\\" in part or "\0" in part for part in parts):
        return
    data = tree.read(entry.path, entry.size + 1)
    if data is None:
        return
    target = Path(root).joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o755 if entry.mode == "100755" else 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


class _TooBig(Exception):
    """The download of a pull request went past what the Lab accepts of a repository."""


def _watch_download():
    """What `fetch` is given to read the progress of the download as it arrives (the same line as the Lab's own fetch reads,
    store.parse_progress): it raises _TooBig as soon as git says it has received more than the store may hold, which stops git
    there. A pull request of a stranger's fork is a hostile repository: its size is not left to be looked at after the whole of it
    was written."""
    pending = [""]

    def feed(text):
        *finished, pending[0] = re.split(r"[\r\n]", pending[0] + text)
        pending[0] = pending[0][-2048:]
        for line in finished:
            parsed = bare.parse_progress(line)
            if parsed is not None and parsed[1] is not None and parsed[1] > bare.STORE_LIMIT:
                raise _TooBig()
    return feed


def _pull(spec, target, stack, notes):
    """Fetches a pull request of the origin into a bare store of this run (hooks and file system monitor off, every object
    checked) and reads it from git objects (`GitTree`): no checkout, so no hook, no filter driver and no Makefile of the pull
    request can run. Its history is the commits since the merge base with the origin's main. The files the Lab would install
    (store.selected: no link, no special file) are written in a temporary folder, for Compose to read."""
    if target.kind != "folder":
        raise Usage("--pr reads a pull request of the origin of a folder, not an added catalog")
    done = gitx.run(["remote", "get-url", "origin"], cwd=target.path, timeout=10)
    origin = done.out.decode("utf-8", "replace").strip() if done.code == 0 else ""
    if not origin:
        raise Usage(f"{check.printable(target.path)} has no origin remote: --pr fetches the pull request from it")
    number = _pull_number(spec, origin)
    address = gitx.checked(gitx.parse_address(origin))
    holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-pr-")).resolve()
    stack.callback(shutil.rmtree, holder, True)
    store = holder / "store.git"
    bare.init(store)
    try:
        fetched = gitx.stream(["--git-dir", str(store), "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true",
                               "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "fetch", "--progress", "--no-tags",
                               "--no-recurse-submodules", "--", address.url, f"+refs/pull/{number}/head:refs/lab/pr",
                               "+refs/heads/main:refs/lab/base"], env=gitx.address_env(address), on_stderr=_watch_download())
    except _TooBig:
        raise gitx.fail("too_large", address) from None
    if fetched.code != 0:
        problem = gitx.classify(fetched.err, address)
        if problem.code == "branch_missing":
            if f"refs/pull/{number}/head" in ((problem.detail or {}).get("output") or ""):
                raise Usage(f"pull request {number} was not found in {address.display}")
            raise Usage(f"{address.display} has no branch main to compare pull request {number} with")
        raise problem
    if bare.size(store) > bare.STORE_LIMIT:
        raise gitx.fail("too_large", address)
    head, main = bare.resolve(store, "refs/lab/pr"), bare.resolve(store, "refs/lab/base")
    if head is None or main is None:
        raise gitx.fail("git_failed", address)
    joined = gitx.run(["--git-dir", str(store), "merge-base", main, head], timeout=60)
    base = joined.out.decode("utf-8", "replace").strip() if joined.code == 0 else ""
    base = base if FULL_COMMIT.fullmatch(base) else main
    listed = trees.GitTree(store, head)
    options = {"tree": listed, "commits": trees.History(git_dir=str(store), base=base, head=head),
               "base_tree": trees.GitTree(store, base), "base": base}
    folder = holder / "units"
    folder.mkdir()
    try:
        kept, _ = bare.selected(listed.entries())
    except CatalogError as error:
        notes.append(f"the items: {error.message}")
        kept = []
    except trees.TreeError as error:
        raise Usage(str(error)) from None
    for entry in kept:
        _write_unit_file(folder, listed, entry)
    seeder = any(each.path == "seeder" or each.path.startswith("seeder/") for each in listed.entries())
    return Pulled(str(folder), options, seeder)


def _tidy(text, problems):
    """The text of a check as `make check` prints it, for a problem that has no code (what the rules of the items say: the standard
    has none for them): its line starts where the others' code does, and the verdict does not send to `make explain` with no code."""
    text = re.sub(r"(?m)^     (?! )", "    ", text)
    lines = text.split("\n")
    last = max((number for number, line in enumerate(lines) if line.strip()), default=None)
    tail = ". Run make explain CODE= for more."
    if last is not None and lines[last].endswith(tail):
        coded = [one for one in problems if one.severity in (report.ERROR, report.WARNING) and one.code]
        lines[last] = lines[last][:-len(tail)]
        if coded:
            first = sorted(coded, key=lambda one: (one.severity != report.ERROR, one.path, int(one.line or 0), one.code))[0]
            lines[last] += f". Run make explain CODE={check.printable(first.code)} for more."
    return "\n".join(lines)


UNTITLED = (re.compile(r"^(::(?:error|warning|notice) [^:\n]*),title=::", re.M), re.compile(r"^(::(?:error|warning|notice)) title=::", re.M))


def _untitled(text):
    """The annotations without the empty `title=` of a finding that has no code. Only the properties of a command are touched (they
    hold no colon: a colon in a value is escaped), never the message after them."""
    for pattern in UNTITLED:
        text = pattern.sub(r"\1::", text)
    return text


def _render(found, files, skipped, fmt, verbose, header, ci):
    """The report of a check in the format asked. What was not checked is never left out: `text` says it with the verdict, `github`
    as an annotation (an error under --ci) and `json`, which is a list of problems, on standard error."""
    if fmt == "text":
        return _tidy(report.render(found, "text", checked=files, skipped=skipped, verbose=verbose, header=header), found), ""
    if fmt == "github":
        level = "error" if ci else "warning"
        extra = "".join(f"::{level} title=check::{report.escape_data(check.printable('Not checked: ' + why))}\n" for why in skipped)
        return _untitled(report.render(found, "github", verbose=verbose)) + extra, ""
    return report.render(found, "json", verbose=verbose), "".join(f"Not checked: {check.printable(why)}\n" for why in skipped)


def _write_summary(found, skipped):
    """`--ci`: the Markdown of the run is appended to the file `$GITHUB_STEP_SUMMARY` names, whose first line says how many things are
    wrong and the one command that shows them on this machine. The only thing a check writes."""
    where = os.environ.get("GITHUB_STEP_SUMMARY")
    if not where or not os.path.isabs(where):
        return
    try:
        with open(where, "a", encoding="utf-8") as handle:
            handle.write(report.summary(found, SUMMARY_COMMAND, skipped, "Catalog check") + "\n")
    except OSError as error:
        complain(f"catalog.py: the summary could not be written: {error.strerror or error}")


SD11 = "The Lab commit {sha7} is not a release of the Lab. Fix: pin the latest release (catalog.py upgrade --lab-ref latest)."
WORKFLOW = re.compile(r"^[ \t-]*uses:[ \t]*([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/\.github/workflows/[A-Za-z0-9._-]+@([0-9a-f]{40})\b", re.M)


def _line_of(text, needle):
    """The line (from 1) of a text that holds `needle`, else 0."""
    return next((number for number, line in enumerate(text.split("\n"), 1) if needle in line), 0)


def _on_main(sha):
    """Whether the Lab checkout that runs this holds `sha` as the commit of a release on its `origin/main` (`standard.release_state`):
    "yes", "no", or why it cannot say."""
    from . import standard as standards   # here and not above: the module of the standard, which the other commands do not need
    return standards.release_state(LAB, sha)


def _pin_problems(tree, notes):
    """SD11 for `check --online`: the commit of the Lab that `.github/standard.json` pins, and the commit of each reusable caller
    (`uses: <the Lab>/.github/workflows/...@<commit>`), has to be a release of the Lab (a tag vX.Y.Z on its `origin/main`), as the
    Lab checkout that runs this knows it. A pin it cannot place is said, never taken for fine."""
    data = tree.read(".github/standard.json", 4097)
    if data is None or len(data) > 4096:
        return []
    try:
        standard = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return []
    lab = standard.get("lab") if isinstance(standard, dict) and isinstance(standard.get("lab"), dict) else {}
    pins = {}
    if isinstance(lab.get("ref"), str) and FULL_COMMIT.fullmatch(lab["ref"]):
        pins[lab["ref"]] = (".github/standard.json", _line_of(data.decode("utf-8", "replace"), lab["ref"]))
    for entry in tree.entries():
        if entry.mode in trees.REGULAR and re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", entry.path):
            text = tree.text(entry.path) or ""
            for found in WORKFLOW.finditer(text):
                if found.group(1) == lab.get("repository"):
                    pins.setdefault(found.group(2), (entry.path, text.count("\n", 0, found.start()) + 1))
    problems = []
    for sha, (where, line) in pins.items():
        state = _on_main(sha)
        if state == "no":
            problems.append(Problem("SD11", report.ERROR, where, line, SD11.format(sha7=sha[:7])))
        elif state != "yes":
            notes.append(f"SD11: {state}")
    return problems


def _base_asked(args):
    """(base, history) for the audit from `--base` and `--history`. A base that is empty asks for nothing: the default is taken. A
    base of zeros is what GitHub gives as `before` on the first push of a branch (the managed workflow passes it as it is): no
    commit came before, so every commit is new and the whole history is read, as `ci gitleaks` takes it. It is not a commit that
    could not be found."""
    if args.base and ZEROS.fullmatch(args.base):
        return None, True
    return args.base or None, args.history


def run_check(args):
    """`audit.run` (the families SD, ST, SE, CR, WF, DF, GD, CO and IM over the files and the commits since origin/main), then the
    items (`catalog_problems`, the trust policy, naming, ports, `lab`) when Compose 5 can read them. A folder is read where it is:
    nothing is linked into the Lab and nothing is written. The report is text (what `make check` prints), `github` annotations or a
    JSON list of {code, severity, path, line, message}. Exit 1 when it found an error (a warning too with --strict) or skipped
    something, 0 with --soft, 2 under --ci when it could not read what a check needs, and for a catalog that is not here."""
    if args.pr is not None and args.base is not None:
        raise Usage("--base cannot be combined with --pr: the base of a pull request is main")
    fmt = args.format or ("github" if args.ci else "text")
    error = _codes(args.error)
    target = _target(args.target)
    if target.kind == "folder" and args.pr is None and not _is_catalog(target.path):
        raise Usage(f"{check.printable(args.target or '.')[:200]} is not a catalog: it has no catalog.yml")
    notes = []
    why = check.compose_missing()
    install = target.kind == "catalog" and (registry.entry(target.ns) or {}).get("kind") == "git"
    with contextlib.ExitStack() as stack:
        pulled = _pull(args.pr, target, stack, notes) if args.pr is not None else None
        folder = pulled.folder if pulled else target.path
        items = Items([], [], None, [])
        if why is None and pulled is not None and not os.listdir(folder):
            notes.append("the items: this pull request holds nothing the Lab installs")
        elif why is None:
            items = _items(target, folder, check.folder_summary(folder), args, notes)
        has_seeder = pulled.has_seeder if pulled else os.path.lexists(Path(target.path) / "seeder")
        base, history = _base_asked(args)
        how = dict(base=base, soft=args.soft, error=error, strict=args.strict, online=args.online, history=history,
                   verbose=args.verbose, ci=args.ci, units=items.units, lab=Path(paths.ROOT),
                   seeder_paths=("../../../seeder",) if has_seeder else ("../../../seeder", "seeder"))
        how.update(pulled.options if pulled else {})
        options = audit.Options(**how)
        result = audit.run(Path(target.path), options)
        pins = _pin_problems(options.tree or trees.FolderTree(target.path), notes) if args.online else []
    # An installed copy holds what the Lab installs and not the repository it came from: the files a repository has are not asked of it
    kept = [one for one in result.problems if not (install and one.code == "ST17")]
    problems = [*kept, *items.problems, *pins]
    skipped = [*result.skipped, *notes, *items.skipped, *([why] if why else [])]
    if args.soft:
        problems = [one._replace(severity=report.WARNING) if one.severity == report.ERROR else one for one in problems]
    skipped = list(dict.fromkeys(skipped))
    result = result._replace(problems=problems, skipped=skipped)
    out, err = _render(problems, items.files, skipped, fmt, args.verbose, result.header, args.ci)
    sys.stdout.write(out)
    sys.stdout.flush()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    if args.ci:
        _write_summary(problems, skipped)
    return audit.status(result, options)


def _folder_index(path):
    """The text of the index of a folder that is read where it is: through a temporary link into a copy of the Lab's project, as
    `check` reads it (check.temporary_link: nothing is linked into the Lab, nothing of the folder is written), in the scrubbed
    environment. A catalog.yml that cannot give the catalog's id is a problem of the catalog (exit 1, with its words), and a
    catalog it includes from that is not here is the machine's (exit 2)."""
    notes = []
    summary = check.folder_summary(path)
    ns, problems = _descriptor(path, summary, notes)
    if ns is None:
        for one in problems:
            complain(f"catalog.yml: {one.message}")
        if not problems:
            complain("catalog.py: no index was made: " + re.sub(r"^the items: |, so the units were not read$", "", notes[-1]
                                                                   if notes else "catalog.yml cannot be used"))
        return None
    needs = sorted(summary.get("requires_catalogs") or {})
    try:
        check.needs_here(ns, needs, registry.inputs())
    except check.Unavailable as error:
        raise Usage(str(error)) from None
    with check.temporary_link(path, ns, needs), check.scrubbed():
        return manifest.render_index(manifest.build_index(ns))


def index(args):
    """The generated catalog.json, deterministic, on standard output or in the file asked for. Of a folder (`index .`, what
    the managed workflows and `make index` run) it is read where it is, as `check` reads it; of an added catalog, by its id."""
    _compose()
    target = _target(args.target)
    if target.kind == "catalog":
        try:
            check.available(target.ns)
        except check.Unavailable as error:
            raise Usage(str(error)) from None
        with check.scrubbed():
            text = manifest.render_index(manifest.build_index(target.ns))
    else:
        if not _is_catalog(target.path):
            raise Usage(f"{check.printable(args.target or '.')[:200]} is not a catalog: it has no catalog.yml")
        text = _folder_index(target.path)
        if text is None:
            return 1
    if args.out:
        paths.write_atomic(Path(args.out), text)
        return 0
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


@contextlib.contextmanager
def _process_environment(new):
    """The environment of this process is `new` for the length of the block, and what it was after. For a script of the engine that
    starts Compose with the environment it finds (.github/scripts/lab_images.py) while it reads the files of a catalog."""
    before = dict(os.environ)
    os.environ.clear()
    os.environ.update(new)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def _lab_images():
    """The engine's builder of images (.github/scripts/lab_images.py), whose label and tag logic a catalog's builder shares."""
    script = Path(paths.ROOT) / ".github" / "scripts" / "lab_images.py"
    if not script.is_file():
        raise Usage("this engine has no .github/scripts/lab_images.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_images", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def images(args):
    """The images the units of a catalog build, with the engine's label and tag logic and the prefix of the catalog's
    `images`: printed with --dry-run, built and pushed otherwise. The seeder is the engine's, and is not built here. A build runs
    in the build environment (compose.build_environment): the person's own HOME and DOCKER_CONFIG, for their credentials, and
    nothing else of the shell."""
    _compose()
    try:
        check.available(args.id)
    except check.Unavailable as error:
        raise Usage(str(error)) from None
    with check.scrubbed():
        meta = manifest.read_catalog(f"{paths.CATALOGS_DIR}/{args.id}/{manifest.FILE}")
    if meta.get("images") and not os.environ.get("REGISTRY"):
        os.environ["REGISTRY"] = meta["images"]
    builder = _lab_images()
    folder = Path(paths.ROOT) / paths.CATALOGS_DIR / args.id
    found = {}
    with _process_environment(compose.scrubbed_environment()):   # the engine's script starts Compose with the environment it has
        for item in builder.items([folder / "addons", folder / "playgrounds"]):
            found.update(builder.builds(item))
    seeder = Path(builder.SEEDER).resolve()
    found = {name: build for name, build in found.items() if Path(build["context"]).resolve() != seeder}
    registry_name, commit, source = builder.settings(args.dry_run)
    for name, build in sorted(found.items()):
        say(f"== {registry_name}/{name}")
        command = builder.command(registry_name, name, build, commit, source)
        if args.dry_run:
            say(" ".join(command))
            continue
        try:
            subprocess.run(command, check=True, env=compose.build_environment())
        except subprocess.CalledProcessError as error:
            complain(f"{registry_name}/{name}: the build failed (exit {error.returncode})")
            return 1
    return 0


def matrix(args):
    """The end-to-end matrix of the items a change touches (lab_ui.e2e.plan), as JSON on standard output."""
    from .. import e2e   # only this command needs it
    _compose()
    try:
        found = e2e.plan(args.id, project.load(), changed=tuple(args.changed))
    except e2e.PlanError as error:
        raise Usage(str(error)) from None
    say(json.dumps(found, indent=2))
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# explain, and the commands that the modules of the standard run: new, upgrade, release, repo-settings, ci
# ---------------------------------------------------------------------------------------------------------------------

def explain_command(args):
    """The long form of a rule code: the rule, why it exists, the fix in full and what the Lab does with the same file at Add.
    A text that is no code is a usage error, with the families that exist."""
    explain = _lab("explain")
    code = args.code.strip().upper()
    table = explain.table()
    if code not in table:
        complain(explain.explain(args.code).rstrip("\n"))
        return 2
    entry = table[code]
    if args.format == "json":
        sys.stdout.write(json.dumps(entry._asdict(), indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    else:
        sys.stdout.write(check.printable(explain.explain(code)))
    sys.stdout.flush()
    return 0


# The commands whose arguments belong to the module that runs them: everything after the name is handed over as it is. Each is the
# module of lab_ui.catalogs and its function `main(argv) -> exit code`, imported when the command is run. `upgrade --fleet OWNER` is
# the loop over the catalogs of an owner: it is `fleet.main`
DELEGATED = {"new": ("standard", "main_new"), "upgrade": ("standard", "main_upgrade"), "release": ("release", "main"),
             "repo-settings": ("settings", "main"), "ci": ("ci", "main")}


# What `catalog.py COMMAND --help` says for the commands whose module answers no help of its own (their parsers stop at the first
# word they do not know, so the dispatcher says it): the words of the command and its usage line. `repo-settings` and `ci` are not
# here: their modules list their options and their stages.
HELP = {
    "new": ("a catalog from the template of the standard, rendered into an empty folder",
            "catalog.py new DIR --id ID --name NAME --maintainers @A[,@B or @ORG/TEAM] [--license SPDX] [--images PREFIX]\n"
            "                [--description TEXT] [--security-contact ADDRESS] [--lab-repo OWNER/REPO] [--lab-ref COMMIT]\n"
            "                [--platforms LIST] [--form inline|reusable] [--with e2e] [--local]"),
    "upgrade": ("bring the managed files of a catalog to the level of this Lab, or of every catalog of an owner",
                "catalog.py upgrade [DIR] [--write] [--commit] [--lab-ref COMMIT|latest] [--refresh-pins] [--adopt]\n"
                "                   [--replace PATH] [--form inline|reusable] [--with e2e] [--maintainers @A] [--lab-repo OWNER/REPO]\n"
                "                   [--format text|json]\n"
                "catalog.py upgrade --fleet OWNER --lab-ref COMMIT [--format text|json|github]"),
    "release": ("prepare a release of a catalog, then tag it",
                "catalog.py release VERSION [--tag] [--pr] [--dir DIR]"),
}


def _fleet(rest):
    """Whether the words after `upgrade` are the loop over the catalogs of an owner (`--fleet OWNER`)."""
    return any(word == "--fleet" or word.startswith("--fleet=") for word in rest)


def _help(command):
    """The help of a delegated command that has none of its own: what it does and its usage line, on standard output."""
    text, usage = HELP[command]
    say(f"{text}\n\nusage: {usage}")
    return 0


def delegate(args):
    module, function = DELEGATED[args.command]
    rest = list(args.rest)
    fleet = args.command == "upgrade" and _fleet(rest)
    if fleet:
        module, function = "fleet", "main"
    elif args.command in HELP and any(word in ("-h", "--help") for word in rest):
        return _help(args.command)
    return getattr(_lab(module), function)(rest)


# ---------------------------------------------------------------------------------------------------------------------

def parser():
    top = argparse.ArgumentParser(prog="catalog.py", description=__doc__.split("\n\n")[0])
    commands = top.add_subparsers(dest="command", required=True, metavar="command")

    def one(name, function, text):
        sub = commands.add_parser(name, help=text, description=text)
        sub.set_defaults(function=function)
        return sub

    def formatted(sub):
        sub.add_argument("--format", choices=FORMATS, help="text, github annotations or json (default text)")
        return sub
    sub = one("link", link, "link a folder as a catalog")
    sub.add_argument("path")
    sub.add_argument("--allow-machine", action="store_true", help="allow its items to use this machine")
    sub = one("unlink", unlink, "reverse a link")
    sub.add_argument("id")
    sub = one("add", add, "add a catalog by its address")
    sub.add_argument("address")
    sub.add_argument("--branch")
    sub.add_argument("--yes", action="store_true", help="add it, after the preview")
    one("provision", provision, "download what has no copy yet and migrate an old layout")
    one("list", list_catalogs, "the catalogs and their state")
    for name, function, text in (("update", update, "update a catalog"), ("undo", undo, "go back to the previous version")):
        sub = one(name, function, text)
        sub.add_argument("id")
        sub.add_argument("--yes", action="store_true", help="apply the plan")
        if name == "update":
            sub.add_argument("--confirm-history", action="store_true",
                             help="with --yes, update a catalog whose history was rewritten or moved")
    sub = one("remove", remove, "remove a catalog")
    sub.add_argument("id")
    sub.add_argument("--delete-settings", action="store_true", help="delete its settings too")
    sub = one("suggest", suggest, "add an entry to the suggestions file")
    sub.add_argument("address")
    sub.add_argument("--name", required=True)
    sub.add_argument("--description")
    sub.add_argument("--allow-machine", action="store_true", help="preselect the allowance in the Add dialog")
    sub = one("reset", reset, "start over: the next start is a first run")
    sub.add_argument("--empty", action="store_true", help="have no catalog at all")
    sub = formatted(one("check", run_check, "every rule that needs no Docker and no network; a folder is read where it is"))
    sub.add_argument("target", nargs="?", metavar="PATH_or_ID", help="a folder, or an added catalog (default: this folder; the id of a catalog wins over a folder of the same name, write ./ID)")
    sub.add_argument("--ci", action="store_true", help="annotations, the summary of the run, no skipped check")
    sub.add_argument("--base", metavar="COMMIT", help="the commit the change is measured from (default: git merge-base origin/main HEAD; "
                                                       "40 zeros, the `before` of a first push, reads the whole history)")
    sub.add_argument("--pr", metavar="NUMBER", help="read a pull request of the origin, from git objects, without running any of it")
    sub.add_argument("--online", action="store_true", help="also the checks that need GitHub")
    sub.add_argument("--history", action="store_true", help="the secrets and commits of every commit of the history")
    sub.add_argument("--strict", action="store_true", help="a warning is a failure")
    sub.add_argument("--soft", action="store_true", help="every error is a warning, and the exit code is 0")
    sub.add_argument("--error", action="append", metavar="CODE[,CODE]", help="make these rules errors for this run")
    sub.add_argument("--verbose", action="store_true", help="the information too")
    sub = one("index", index, "the generated catalog.json, of a folder (default: this one) or of an added catalog")
    sub.add_argument("target", nargs="?", metavar="PATH_or_ID", help="a folder, or an added catalog (default: this folder; the id of a catalog wins over a folder of the same name, write ./ID)")
    sub.add_argument("--out", metavar="FILE", help="write it to this file and not to standard output")
    sub = one("images", images, "build the images a catalog builds")
    sub.add_argument("id")
    sub.add_argument("--dry-run", action="store_true")
    sub = one("matrix", matrix, "the end-to-end matrix of a change")
    sub.add_argument("id")
    sub.add_argument("--changed", nargs=2, metavar=("BASE", "HEAD"), required=True)
    sub = formatted(one("explain", explain_command, "the long form of a rule code"))
    sub.add_argument("code", metavar="CODE")
    for name, text in (("new", "a catalog from the template of the standard (catalog.py new DIR --id ID --name NAME --maintainers @A)"),
                       ("upgrade", "bring the managed files to the level of this Lab; --fleet OWNER --lab-ref COMMIT does it for every "
                                   "catalog of an owner"),
                       ("release", "prepare a release, then tag it (catalog.py release VERSION [--tag] [--pr])"),
                       ("repo-settings", "audit and apply the repository standard on GitHub (catalog.py repo-settings --help)"),
                       ("ci", "the stages the managed workflows call: plan, gitleaks, manifests, build, test, smoke, run, ...")):
        sub = one(name, delegate, text)
        sub.set_defaults(command=name)
        sub.add_argument("rest", nargs=argparse.REMAINDER)
    return top


@contextlib.contextmanager
def _at_the_engine():
    """$PWD names the engine as the engine is: Compose, which every command that reads a catalog runs from the engine's folder,
    takes its working directory from $PWD when that names the folder it is in, so a caller whose $PWD is a link to the engine
    (a shell that went there by a symlink, a temporary folder reached through one) would have it spell every path another way
    than the Lab does, and the same catalog would pass from one folder and be refused from another. The caller's own is put
    back when the command ends. Relative arguments are made absolute from the real working directory, which this does not
    change."""
    before = os.environ.get("PWD")
    os.environ["PWD"] = str(paths.ROOT)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("PWD", None)
        else:
            os.environ["PWD"] = before


def main(argv=None):
    """Runs a command and answers its exit code (0 it worked, 1 a problem or a refusal, 2 usage or environment)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DELEGATED:   # their arguments are the module's, not argparse's
        args = argparse.Namespace(function=delegate, command=argv[0], rest=argv[1:])
    else:
        try:
            args = parser().parse_args(argv)
        except SystemExit as stop:   # argparse ends with 2 for a usage error and 0 after --help
            return stop.code if isinstance(stop.code, int) else 2
    try:
        with _at_the_engine():
            return args.function(args) or 0
    except Usage as error:
        complain(f"catalog.py: {error}")
        return 2
    except check.Unavailable as error:
        complain(f"catalog.py: {error}")
        return 2
    except CatalogError as error:
        for line in _lines(error):
            complain(line)
        return 2 if error.code in ENVIRONMENT else 1
    except (OSError, ValueError, paths.NotFound) as error:   # what the machine refuses (a folder that cannot be written), or a name that is none
        complain(f"catalog.py: {error}")
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:   # noqa: BLE001 - a fault of the tool is not "problems found" (exit 1): CI reads 1 as a verdict on the catalog
        complain("catalog.py: this is a fault of the tool, not a problem of the catalog. Please report it, with this:\n"
                 + traceback.format_exc())
        return 2
