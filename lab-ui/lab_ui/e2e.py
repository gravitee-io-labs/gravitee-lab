"""`make e2e`: starts an add-on, a setup or a playground the way a person does from the Lab UI, on a machine that holds no
lab, and says whether it works: the start ends well (a playground's seed included), what it runs is up and not in an issue,
and the consoles it shows answer. It runs the Lab UI's own server on a free port and asks it, as its page does, so that what
is tried is what a person gets. Run it in CI, or on a machine where `make reset` is no loss.

An item is named `kind:id` with any id of the Lab: bare for the first catalog, `@catalog/name` for the others. A catalog is
tried by linking it into the engine (`catalog.py link`), which is all CI does before it asks.

Versions: a playground says the sets of versions it was tested with (`x-gravitee.tested`); `--tested N` runs on the N-th (from
0), by the variables of the process, which Compose reads before `.env`, so that no file of this machine is written. Without
it, the lab runs on the defaults of the core.

Exit code: 0 it works, 1 it does not, 2 it cannot be tried here (no lab-free machine, no usable license, a setting only a
person can give): said, never taken for a pass.

The second half of the module is the plan CI makes before it tries anything: which items of a catalog a change touches, the
jobs of each (one for each set of versions it says it was tested with), and the canary a catalog names in `catalog.yml` for a
change that touches no item. `.github/scripts/e2e_matrix.py` prints it for the workflow, and `catalog.py matrix` for a person.
No Docker and no network: the plan reads Compose files and asks git what changed."""

import contextlib
import http.client
import json
import os
import re
import secrets
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from .docker import compose
from .library import layers, project
from . import paths
from .catalogs import gitx, manifest, registry
from .paths import ON_FILE, ROOT

OK, FAILED, CANNOT = 0, 1, 2
POLL = 3            # seconds between two readings of the state while waiting
SETTLE = 300        # seconds a started item has to be up: its containers healthy, nothing in an issue
CONSOLES = 90       # seconds a console has to answer, once everything is up
LOG_LINES = 80      # lines of the log of a container that is not up, which the report prints
LOG_CHARS = 20000   # characters of such a log that the report keeps (the end of it): a summary of a job is 1 MiB at most
NAME_LIMIT = 200    # characters of an item named on a command line
# What a start that cannot happen here says: a license it lacks, a setting only a person gives
UNABLE = re.compile(r"Needs settings|Needs a license|Not in the license|License expired|The license")
# What a container may be once started and settled: up, or a one-shot that ended well
UP = {"running", "healthy", "done"}
# What a set of versions may name: a setting in capitals that says the version, the tag, the registry, the image, the repository or
# the prefix of an image, and none of the variables of the process or of the tools it runs. The catalog that writes the sets also
# writes the images, so a variable an image is written with is no proof that it is one of these: the form is, and PROCESS is a
# second check for the names that have that form and are read by the process (GITHUB_REPOSITORY, DOCKER_API_VERSION)
SETTING = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:VERSION|TAG|REGISTRY|IMAGE|REPOSITORY|PREFIX)")
PROCESS = re.compile(r"PATH|HOME|SHELL|USER|TMPDIR|IFS|ENV|BASH_ENV|LANG|GRAVITEE_LICENSE|NODE_(?:OPTIONS|PATH|EXTRA_CA_CERTS)"
                     r"|PYTHON(?!_)[A-Z0-9_]*|(?:LD|DYLD|LC|DOCKER|COMPOSE|GIT|SSH|BUILDKIT|BUILDX|GITHUB|ACTIONS|RUNNER)_[A-Z0-9_]*")


class Unable(Exception):
    """The item cannot be tried on this machine: the reason, in a sentence."""


class Failed(Exception):
    """The item does not work: what failed, in a sentence."""


class Step:
    """One thing that was tried: its name, whether it held, what to know of it, and how long it took."""

    def __init__(self, name, ok, detail="", seconds=0.0):
        self.name, self.ok, self.detail, self.seconds = name, ok, detail, seconds


def example(built, ns=None):
    """An item of the project written as it is asked for, to show how: the first playground, else the first add-on or setup
    that is shared (of catalog `ns`, when one is given). None when the project has none."""
    for kind in ("playground", "addon", "setup"):
        for uid, unit in sorted(((built or {}).get(f"{kind}s") or {}).items()):
            if not paths.personal(uid) and (ns is None or unit.get("catalog") == ns):
                return f"{kind}:{uid}"
    return None


def parse(text, built=None):
    """The kind and id of an item from `kind:id`, any id of the Lab: `name` for the first catalog, `@catalog/name` for the
    others, `local/name` for this machine. ValueError, saying how to write it, for what is none (the text is never repeated:
    it may come from a form anyone fills). Only the form is judged, never whether the item exists. `built` (the project)
    gives the example of the message."""
    kind, _, uid = text.partition(":") if isinstance(text, str) and len(text) <= NAME_LIMIT else ("", "", "")
    uid = paths.canonical(uid)   # @<first catalog>/name is how a person may write name
    if kind not in paths.KINDS or not paths.is_id(uid):
        shown = example(built)
        raise ValueError(f"Name the item as kind:id, kind being {', '.join(paths.KINDS)}"
                         + (f", for example {shown}" if shown else ""))
    return kind, uid


def target(text, built=None):
    """The kind and id of an item from `kind:id` (`parse`): the item has to be one of the project, of any catalog. What
    belongs to this machine (`local/`) is never run here: this is the check of what is shared."""
    kind, uid = parse(text, built)
    if paths.personal(uid):
        raise ValueError(f"{kind}:{uid} belongs to this machine: only what is shared with the project is run")
    found = built if built is not None else project.load()
    if uid not in found[f"{kind}s"]:
        raise ValueError(f"No {paths.NOUNS[kind]} named {uid}")
    return kind, uid


def image_variables(built):
    """The variables the images of the project are written with (`${APIM_VERSION:-4.13}`): the only ones a set of versions
    may set. None for a project that lists no services, which is not one the Lab UI builds."""
    if "services" not in built:
        return None
    return {key for service in built["services"].values() for key, _, _ in compose.references(service.get("raw_image") or "")}


def versions(unit, index, known=None):
    """The variables of the set of versions `index` (from 0) of `x-gravitee.tested` of a playground, as strings: what the
    process gives Compose. {} without an index: the core's defaults. A set is text a catalog wrote and the run has the license,
    so it sets the version or the registry of an image (SETTING, and one of `known`, the variables of the images, when they are
    given) and never a variable of the process or of the tools it runs (PROCESS)."""
    if index is None:
        return {}
    sets = [tested for tested in unit.get("tested") or [] if tested]
    if not sets:
        raise ValueError(f"{unit['name']} says no versions it was tested with: it has no set {index}")
    if not 0 <= index < len(sets):
        raise ValueError(f"{unit['name']} was tested with {len(sets)} set{'s' if len(sets) != 1 else ''} of versions: "
                         f"choose from 0 to {len(sets) - 1}")
    chosen = {str(key): str(value) for key, value in sets[index].items()}
    for key in chosen:
        if not SETTING.fullmatch(key) or PROCESS.fullmatch(key) or (known is not None and key not in known):
            raise ValueError(f"{unit['name']} sets {shortened(key)} in its versions, which is not the version or the "
                             "registry of an image: a run takes nothing else")
    return chosen


def keep_on_file():
    """What the on file says now, and what puts it back: a start turns the item on there, and a run leaves the file as it found it,
    so that a person who ran it on their own machine is not left with an item turned on that does not run."""
    file = ROOT / ON_FILE
    before = file.read_text(encoding="utf-8") if file.is_file() else None

    def restore():
        if before is None:
            file.unlink(missing_ok=True)
        else:
            paths.write_atomic(file, before)
    return restore


def clean_machine(shell=compose.run, name=None):
    """What a machine that is free for a run holds of the lab: nothing. Containers and volumes of the project, whatever their
    state, are what a person left, and the run removes everything it made at its end: it will not remove theirs. Returns
    the reason it is not free, or None."""
    name = name or compose.project_name()
    for what, command in (("containers", ["docker", "ps", "-aq"]), ("volumes", ["docker", "volume", "ls", "-q"])):
        rc, out, err = shell([*command, "--filter", f"label=com.docker.compose.project={name}"], timeout=30)
        if rc != 0:
            return f"Docker does not answer: {(err or out).strip().splitlines()[-1] if (err or out).strip() else 'no reason given'}"
        if out.strip():
            return (f"This machine already has {what} of the lab ({name}): a run starts from nothing and removes what it made, "
                    "so that it does not touch yours. Run it in CI, or remove the lab first (make reset)")
    return None


class Lab:
    """The Lab UI's server, on a free port of this machine, and the calls its page makes."""

    def __init__(self):
        from .server.api import Handler   # the server is the Lab UI's; loaded here, which api.main does not need until a run
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, method, path, body=None):
        """The answer of the Lab UI as JSON; a refusal raises its message."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        try:
            headers = {"X-Gravitee-Lab-UI": "1", "Content-Type": "application/json"}
            connection.request(method, path, json.dumps(body or {}) if method == "POST" else None, headers)
            response = connection.getresponse()
            answer = json.loads(response.read() or b"{}")
        finally:
            connection.close()
        if response.status >= 400:
            raise RuntimeError(answer.get("error") or f"{method} {path}: {response.status}")
        return answer

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Run:
    """One item tried: its steps, in order. `lab` answers the calls, `clock` and `sleep` are the time, and `shell` runs a
    command: given, so that the tests need no Docker."""

    def __init__(self, lab, kind, uid, clock=time.monotonic, sleep=time.sleep, shell=compose.run, get=None):
        self.lab, self.kind, self.uid = lab, kind, uid
        self.clock, self.sleep, self.shell = clock, sleep, shell
        self.get = get or self._get
        self.steps = []
        self.item = None

    # --- Reading -----------------------------------------------------------------------------

    def state(self):
        return self.lab.call("GET", "/api/state")

    def unit(self, state=None):
        return next(unit for unit in (state or self.state())[f"{self.kind}s"] if unit["id"] == self.uid)

    def _get(self, url):
        """The status a console answers, 0 when it does not answer: a redirect is an answer, a server error is not."""
        parts = urlsplit(url)
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=5)
        try:
            connection.request("GET", parts.path or "/")
            return connection.getresponse().status
        except (OSError, http.client.HTTPException):
            return 0
        finally:
            connection.close()

    # --- Steps -------------------------------------------------------------------------------

    @contextlib.contextmanager
    def step(self, name):
        """Times what is done in it. A `Failed` ends the run with the step failed, anything else is the run's own error."""
        started = self.clock()
        try:
            yield
        except Failed as error:
            self.steps.append(Step(name, False, str(error), self.clock() - started))
            raise
        self.steps.append(Step(name, True, "", self.clock() - started))

    def wait(self, until, timeout, what):
        """Reads the state every POLL seconds until `until(state)` gives something, which it returns; Failed when that takes
        longer than `timeout`."""
        deadline = self.clock() + timeout
        while True:
            found = until(self.state())
            if found:
                return found
            if self.clock() >= deadline:
                raise Failed(f"{what} did not happen within {int(timeout)} s")
            self.sleep(POLL)

    def start(self, timeout):
        """Asks for the start as the item's button does, then waits for its job. A start the Lab UI itself says cannot happen
        here (no license, a setting to give) is Unable: nothing was tried."""
        item = self.unit()
        action = item.get("action") or {}
        if not action.get("enabled", True):
            reason = action.get("reason") or "The Lab UI does not allow it"
            if UNABLE.search(reason):
                raise Unable(reason)
            raise Failed(f"The Lab UI does not allow the start: {reason}")
        dialog = action.get("dialog") or {}
        accept = [f"{row['kind']}:{row['id']}" for row in [*dialog.get("stops", []), *dialog.get("turns_off", [])]]
        route = f"/api/{self.kind}s/{self.uid}/start"
        with self.step(f"Start {item['name']}"):
            try:
                job = self.lab.call("POST", route, {"accept": accept})
            except RuntimeError as error:
                raise Failed(str(error)) from None
            done = self.wait(lambda state: state["job"] if state.get("job") and state["job"]["id"] == job["id"]
                             and state["job"]["done"] else None, timeout, "the start")
            if not done["ok"]:
                raise Failed(done.get("error") or "the start failed")

    def settle(self):
        """Every container of the item is up, none is in an issue, and nor is the platform: what a person sees as running."""
        names = self.unit()["services"]

        def settled(state):
            platform = (state.get("platform") or {}).get("state") or {}
            if platform.get("key") == "issue":   # what the item needs of the core
                raise Failed(f"the platform is in an issue: {platform.get('label') or 'no reason'}")
            found = {name: state["services"][name]["state"] for name in names if name in state["services"]}
            bad = [name for name, one in found.items() if one["key"] == "issue"]
            if bad:
                raise Failed(f"{', '.join(bad)} in an issue: {', '.join(found[name]['label'] or 'no reason' for name in bad)}")
            return all(one["key"] in UP for one in found.values()) and len(found) == len(names)

        with self.step("Containers are up"):
            try:
                self.wait(settled, SETTLE, "every container being up")
            except Failed as error:
                late = self.not_up()
                raise Failed(f"{error}" + (f" (not up: {', '.join(late)})" if late else "")) from None

    def not_up(self):
        state = self.state()
        return [name for name in self.unit(state)["services"]
                if (state["services"].get(name, {}).get("state") or {}).get("key") not in UP]

    def consoles(self):
        """The consoles the item shows answer, once each: a page that loads, or sends the browser further."""
        state = self.state()
        mine = set(self.unit(state)["services"])
        links = [link for link in state["links"] if link["service"] in mine]
        if not links:
            return
        with self.step("1 console answers" if len(links) == 1 else f"{len(links)} consoles answer"):
            for link in links:
                deadline = self.clock() + CONSOLES
                status = self.get(link["url"])
                while status == 0 or status >= 500:
                    if self.clock() >= deadline:
                        raise Failed(f"{link['label']} ({link['url']}) does not answer: "
                                     f"{'nothing' if status == 0 else f'status {status}'}")
                    self.sleep(POLL)
                    status = self.get(link["url"])

    def logs(self):
        """The end of the log of each container of the item that is not up, to say why."""
        found = {}
        for name in self.not_up():
            rc, out, err = self.shell(["docker", "logs", "--tail", str(LOG_LINES), self.container(name)], timeout=30)
            found[name] = (out + err).strip()
        return found

    def container(self, name):
        return (self.state()["services"].get(name) or {}).get("container") or name

    def stop(self):
        """Removes what the run made: containers, networks and volumes of the project, which a free machine held none of."""
        rc, _, err = self.shell([*compose.by_name(), "down", "--volumes", "--remove-orphans", "--timeout", "30"], timeout=600)
        return None if rc == 0 else (err.strip().splitlines() or ["docker compose down failed"])[-1]


# ---------------------------------------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------------------------------------

def seconds(value):
    """A time as a person reads it: 45 s, 4 min 12 s."""
    value = int(round(value))
    return f"{value} s" if value < 60 else f"{value // 60} min {value % 60:02d} s"


def lines(run, outcome, versions_used, message=""):
    """What the run says, one line a step, then the verdict."""
    out = [f"{'✓' if step.ok else '✗'} {step.name} ({seconds(step.seconds)})" + (f": {step.detail}" if step.detail else "")
           for step in run.steps]
    if versions_used:
        out.insert(0, "Versions: " + ", ".join(f"{key}={value}" for key, value in versions_used.items()))
    out.append({OK: "It works", FAILED: "It does not work", CANNOT: "Not tried"}[outcome] + (f": {message}" if message else ""))
    return out


def tail(log):
    """The end of a log, the part a person reads, within LOG_CHARS."""
    log = log or ""
    return log if len(log) <= LOG_CHARS else "…" + log[-LOG_CHARS:]


def fence(text):
    """The Markdown fence that holds `text` whole: longer than any run of backticks in it, so that a log cannot end its own
    block and write the page of the job."""
    return "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)


# What a renderer of GitHub turns into a link by itself: a scheme and `//` (http, https, ftp, and any other a unit writes), `www.`,
# `mailto:`, `xmpp:` and an address of mail. A word that holds one of these is shown as code (`plain`): a code span is never linked
# by any renderer, and the word is still there to read and to copy as it was written. The scheme is not matched: `://` is enough,
# and a pattern for the letters before it would read a long word again from each of its letters (a unit writes what it likes)
LINK = re.compile(r"(?i)://|www\.|mailto:|xmpp:|[^\s@]@[^\s@]")
MARKUP = re.compile(r"([\\`*_\[\]<>#~])")


def plain(text):
    """Words of a unit as one line of Markdown that says what they say and no more. A break would end a row of the table and
    a pipe its cell; a backslash, a backtick, `*`, `_`, brackets, angle brackets, `#` and `~` would write a heading, a link, an
    image, a tag or a style of the unit's own on the page of the job. A bare address would be a link the unit chose, on a page a
    maintainer trusts: the word that holds it is a span of code instead (`LINK`), which no renderer makes a link."""
    words = " ".join(str(text).split()).replace("|", "/").split(" ")
    return " ".join(code(word) if LINK.search(word) else MARKUP.sub(r"\\\1", word) for word in words)


def code(text):
    """A value of a unit as an inline code span on one line: no break, and no backtick that would end the span."""
    return "`" + " ".join(str(text).split()).replace("`", "'") + "`"


def summary(run, outcome, versions_used, message="", logs=None):
    """The same as the page of the job on GitHub shows it, in Markdown: a title, a table of what was tried, the logs of what is
    not up. What the unit says (its name, a detail, a version) goes through `plain` or is cut to one line of code: the page is
    the one a maintainer trusts; only a log, in its fence, is shown as it is."""
    verdict = {OK: "works", FAILED: "does not work", CANNOT: "not tried"}[outcome]
    text = [f"### {run.kind}:{run.uid} {verdict}", ""]
    text.append("Versions: " + (", ".join(code(f"{key}={value}") for key, value in versions_used.items()) if versions_used
                                else "the defaults of the core"))
    text += ["", "| Step | Result | Time |", "|---|---|---|"]
    text += [f"| {plain(step.name)} | {'✅' if step.ok else '❌ ' + plain(step.detail)} | {seconds(step.seconds)} |"
             for step in run.steps]
    if message:
        text += ["", plain(message)]
    for name, log in (logs or {}).items():
        kept = tail(log) or "(empty)"
        mark = fence(kept)
        text += ["", f"<details><summary>Log of {name}</summary>", "", mark, kept, mark, "", "</details>"]
    return "\n".join(text) + "\n"


def escaped(text):
    """Text for the message of a workflow command: GitHub reads `%`, a carriage return and a line feed in it as the end of the
    command or as itself."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


@contextlib.contextmanager
def data(out, github):
    """What is printed in this block is data. What a hostile unit makes its containers say, or its seed fail with, can look
    like a workflow command (`::error::`, `::add-mask::`): on GitHub the block is closed between `::stop-commands::` and the
    token that ends it, which only this call knows."""
    if github:
        token = secrets.token_hex(16)
        out(f"::stop-commands::{token}")
    try:
        yield
    finally:
        if github:
            out(f"::{token}::")


def report(run, outcome, versions_used, message="", logs=None, out=print, summary_file=None):
    """Prints the run, its logs apart (grouped on GitHub), and appends its summary to the page of the job when it has one.
    The words of a unit (a step's detail, a log) are data: they never run a workflow command."""
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    with data(out, github):
        for line in lines(run, outcome, versions_used, message):
            out(line)
    for name, log in (logs or {}).items():
        out(f"::group::Log of {name}" if github else f"\n--- Log of {name}")
        with data(out, github):
            out(tail(log) or "(empty)")
        if github:
            out("::endgroup::")
    path = summary_file if summary_file is not None else os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as file:
                file.write(summary(run, outcome, versions_used, message, logs))
        except OSError as error:   # the page of the job is a courtesy: it never changes what the run found
            out(f"The summary could not be written: {error.strerror or error}")
    if outcome == CANNOT and github:
        out(f"::warning title={run.kind}:{run.uid} not tried::{escaped(message)}")


# ---------------------------------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------------------------------

def run(text, tested=None, timeout=1800, keep=False, out=print):
    """Tries the item named `text` (`kind:id`) on set `tested` of its versions, and answers the exit code."""
    layers.prepare()
    built = project.load()
    try:
        kind, uid = target(text, built)
        used = versions(built[f"{kind}s"][uid], tested, image_variables(built))
    except ValueError as error:   # a unit's own name may be in it: said as data
        with data(out, os.environ.get("GITHUB_ACTIONS") == "true"):
            out(" ".join(str(error).split()))
        return CANNOT
    busy = clean_machine()
    if busy:
        out(f"Not tried: {busy}")
        return CANNOT
    os.environ.update(used)   # Compose reads the process before .env: no file of this machine changes
    compose.CACHE.drop()
    project.forget()
    restore = keep_on_file()
    lab = Lab()
    attempt = Run(lab, kind, uid)
    outcome, message, logs = OK, "", None
    try:
        attempt.start(timeout)
        attempt.settle()
        attempt.consoles()
    except Unable as error:
        outcome, message = CANNOT, str(error)
    except Failed as error:
        outcome, message, logs = FAILED, str(error), attempt.logs()
    except RuntimeError as error:   # what the Lab UI refused, or Docker did not answer
        outcome, message = FAILED, str(error)
    finally:
        removal = None if keep else attempt.stop()
        lab.close()
        if not keep:
            restore()
    report(attempt, outcome, used, message, logs, out)
    if removal:
        out(f"The lab could not be removed: {removal}")
    return outcome


# ---------------------------------------------------------------------------------------------------------------------
# The plan: which items CI tries
# ---------------------------------------------------------------------------------------------------------------------

JOBS_LIMIT = 256   # the most jobs one matrix of GitHub holds
# The folder of an add-on and of a playground in a catalog, and the file of a setup
UNIT_PATH = re.compile(r"^(?:(addons|playgrounds)/([a-z0-9-]+)/.+|(setups)/([a-z0-9-]+)\.yml)$")
FOLDER_KIND = {"addons": "addon", "playgrounds": "playground", "setups": "setup"}
# What every item shares in the engine: a change of it may break any item of any catalog. A change that touches nothing else
# runs the canary of a catalog, and the engine's catalog job runs when one of these changes. A folder ends in /
ENGINE_PATHS = ("docker-compose.yml", ".env.example", "Makefile", "contract.lock", "catalogs.default.json", "seeder/",
                "lab-ui/lab_ui/", "lab-ui/server.py", "lab-ui/catalog.py", "certs/", "lab-ui/frames/", ".github/actions/",
                ".github/workflows/e2e.yml", ".github/workflows/e2e-run.yml", ".github/scripts/e2e_matrix.py",
                ".github/scripts/image_manifests.py")
# What every item shares in a catalog: its description, its commands, its workflows and the pin of the engine they use
CATALOG_PATHS = ("catalog.yml", "Makefile", ".github/workflows/", ".github/actions/", ".github/scripts/",
                 ".github/standard.json", ".catalog/")
# A commit or a branch, as git is asked for it: no option can start with one of these characters
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./@^~-]{0,199}")


class PlanError(Exception):
    """The plan cannot be made: what is wrong, in a sentence, and what to do."""


def item_of(path):
    """The `kind:name` of the item a file of a catalog belongs to; None for a file of no item, and for what belongs to a
    machine (`local`). An item changes when a file of its folder does (a setup is one file)."""
    found = UNIT_PATH.match(path)
    if not found:
        return None
    folder, name, setup, setup_name = found.groups()
    name = name or setup_name
    return None if name == paths.LOCAL else f"{FOLDER_KIND[folder or setup]}:{name}"


def lab_item(ns, item):
    """`kind:id` for the `kind:name` item of catalog `ns`: the name for the first catalog, whose ids are bare, and
    `@catalog/name` for the others. `ns` None is a repository that held its items itself: the names are the ids."""
    kind, _, name = item.partition(":")
    if ns is None or ns == paths.default_ns():
        return f"{kind}:{name}"
    return f"{kind}:@{ns}/{name}"


def touched(files, shared, canary, named=True, alive=None):
    """The `kind:name` items that `files` touch, sorted. When none does and a file is one of `shared`, the items `canary()`
    gives: no item changed of its own, so what they share is tried on the ones that say the most. `named` False reads no
    item from the files: the change is in a repository that holds none (the engine's). `alive(item)` says whether a touched
    item is still there, and leaves out the ones that are not: a change that deletes the one item it touches and changes
    what they share is a change of what they share, and has no item of its own left."""
    found = sorted({item for item in map(item_of, files) if item}) if named else []
    if alive is not None:
        found = [item for item in found if alive(item)]
    if found or not any(path.startswith(shared) for path in files):
        return found
    return list(canary())


def triggers(files, shared=ENGINE_PATHS):
    """The files of a change that are of what every item shares: what makes a catalog worth trying again."""
    return [path for path in files if path.startswith(shared)]


def exists(item, built, ns=None):
    """Whether the item is still there, and in catalog `ns` when one is given: a pull request that deletes one has nothing of
    it to run."""
    kind, _, uid = item.partition(":")
    unit = (built.get(f"{kind}s") or {}).get(uid)
    return unit is not None and (ns is None or unit.get("catalog") == ns)


def shared_items(built, ns=None):
    """Every item shared with the project, of catalog `ns` when one is given: not what belongs to a machine."""
    return [f"{kind}:{uid}" for kind in ("addon", "setup", "playground") for uid, unit in sorted(built[f"{kind}s"].items())
            if not paths.personal(uid) and (ns is None or unit.get("catalog") == ns)]


def named(words, built, ns=None):
    """The items a person named (`kind:id`), each once. One that is not a shared item (of catalog `ns`, when one is given) is
    an error, said before anything runs; it is never repeated in full, it may be anything."""
    found = []
    for word in words:
        try:
            kind, uid = parse(word)
        except ValueError:
            kind = uid = None
        if kind is None or paths.personal(uid) or not exists(f"{kind}:{uid}", built, ns):
            hint = example(built, ns)
            raise PlanError(f"{shortened(word)} is not a shared item{f' of {ns}' if ns else ''}: name it kind:id"
                            + (f", for example {hint}" if hint else ""))
        found.append(f"{kind}:{uid}")
    return list(dict.fromkeys(found))


def shortened(text, limit=60):
    """Text from anywhere as one quoted line that can be printed: cut, and in quotes, so that it starts with no `::`."""
    text = " ".join(str(text).split())
    return repr(text if len(text) <= limit else text[:limit - 1] + "…")


def jobs(items, sets):
    """One job for each set of versions of each item, and one on the defaults of the core for an item that says none.
    `sets(item)` is how many sets it says it was tested with. A job is {item, tested, name}: `tested` is the number of the set,
    empty for the defaults, so that the Makefile's TESTED reads it as given or not."""
    found = []
    for item in items:
        count = sets(item)
        for tested in (range(count) if count else [None]):
            found.append({"item": item, "tested": "" if tested is None else str(tested),
                          "name": f"{item} (versions {tested})" if tested is not None else f"{item} (core defaults)"})
    return found


def matrix(items, built):
    """{"matrix": {"include": [job, ...]}, "count": n} of these items: what a workflow reads to start one job for each."""
    def sets(item):
        kind, _, uid = item.partition(":")
        return len((built[f"{kind}s"][uid]).get("tested") or [])
    found = jobs(items, sets)
    if len(found) > JOBS_LIMIT:
        raise PlanError(f"The plan has {len(found)} jobs and a matrix of GitHub runs {JOBS_LIMIT} at most: try the items "
                        "a part at a time")
    return {"matrix": {"include": found}, "count": len(found)}


def default_catalog():
    """The namespace of the catalog this engine lists first, which the bare ids belong to."""
    ns = paths.default_ns()
    if ns is None:
        raise PlanError("This engine lists no default catalog (catalogs.default.json): name the catalog")
    return ns


def available(ns):
    """Catalog `ns`, checked to be one this engine has: installed, or linked from a folder. PlanError otherwise."""
    if ns == paths.LOCAL:
        raise PlanError("local is the area of this machine: only what is shared with the project is tried")
    if not isinstance(ns, str) or not re.fullmatch(paths.NS, ns) or registry.entry(ns) is None:
        raise PlanError(f"The catalog {shortened(ns)} is neither installed nor linked in this engine: link its folder "
                        "first (catalog.py link PATH)")
    return ns


def unreadable_items(built, ns):
    """{`kind:name`: why} for the items of catalog `ns` whose file could not be read (invalid YAML, a Compose error): the project
    lists them as problems of the catalog and not as items, so that a plan which asked only whether an item is there would take
    one for an item the change deleted, try nothing and pass. The commonest mistake of a pull request, a file that no longer
    loads, must end the plan."""
    found = {}
    for problem in ((built or {}).get("catalog_problems") or {}).get(ns) or ():
        item = item_of(str(problem.get("display") or ""))
        if item and item not in found:
            found[item] = " ".join(str(problem.get("message") or "no reason given").split())[:300]
    return found


def unreadable_error(ns, item, why):
    """The refusal for item `kind:name` of catalog `ns` that cannot be read: it starts with the item (a name of the catalog's, in
    the form of an id), so that no line of the log can start with the words of a unit."""
    return PlanError(f"{item} of {ns} cannot be tried, its files do not load: {why}. Fix: catalog.py check {ns}")


def canary_names(ns, built, unreadable=None):
    """The `kind:name` items that `catalog.yml` of catalog `ns` names as its canary: what is tried when a change touches no
    item of the catalog. [] when it names none. An item that is not there is an error and not a thing to leave out, so that a
    canary that went stale cannot turn the check off, and so is one that cannot be read (`unreadable`, from
    `unreadable_items`, when the caller has it)."""
    try:
        names = manifest.read_catalog(f"{paths.CATALOGS_DIR}/{ns}/{manifest.FILE}")["canary"]
    except registry.CatalogError as error:
        raise PlanError(f"catalog.yml of {ns} cannot be read: {error.message}") from None
    unreadable = unreadable_items(built, ns) if unreadable is None else unreadable
    for name in names:
        if name in unreadable:
            raise unreadable_error(ns, name, unreadable[name])
        if not exists(lab_item(ns, name), built, ns):
            raise PlanError(f"The canary of {ns} names {name}, which is not an item of it: fix catalog.yml")
    return names


def _top(folder):
    """Whether `folder` is the top of a repository of its own, a work tree or a bare one (a linked work tree is one, and so is the
    checkout of a submodule): git run in a folder of a repository above would answer for that one."""
    bare = gitx.run(["rev-parse", "--is-bare-repository", "--git-dir"], cwd=str(folder), timeout=20)
    if bare.code != 0:
        return False
    answer = bare.out.decode("utf-8", "replace").split()
    if answer[:1] == ["true"]:
        return answer[1:2] == ["."]
    top = gitx.run(["rev-parse", "--show-toplevel"], cwd=str(folder), timeout=20)
    return top.code == 0 and os.path.realpath(top.out.decode("utf-8", "replace").strip()) == os.path.realpath(folder)


def changed_files(folder, base, head):
    """The files `head` changes since it left `base`, in the repository at `folder`, as paths from its top. A rename counts as
    the deletion of the old path and the addition of the new, so that the item that lost a file is touched as well. The folder
    has to be the top of a repository of its own (_top)."""
    for ref in (base, head):
        if not isinstance(ref, str) or not REF.fullmatch(ref):
            raise PlanError(f"{shortened(ref)} is not a commit or a branch")
    try:
        # The `--` ends the revisions: a range that git cannot resolve is an error, and never the name of a file of the change
        # (a file called like the range would be read as a path, and the change as empty)
        done = gitx.run(["diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"], cwd=str(folder), timeout=60) \
            if _top(folder) else None
    except (registry.CatalogError, gitx.OutputLimit, OSError) as error:
        raise PlanError(f"git cannot tell what {head} changes since {base}: "
                        f"{getattr(error, 'message', None) or error}") from None
    if done is None:
        raise PlanError("The folder of the catalog is not a git repository of its own: a change is read in the working copy "
                        "linked with catalog.py link")
    if done.code != 0:
        raise PlanError(f"git cannot tell what {head} changes since {base}: "
                        f"{' '.join(str(done.err).split())[:300] or 'no reason given'}")
    return [name for name in done.out.decode("utf-8", "replace").split("\0") if name]


def plan(ns, built, changed=None, engine=False, everything=False, canary_only=False, words=None, git=changed_files, warn=None):
    """The matrix of what to try in catalog `ns` (the default catalog when None): {"matrix": {"include": [...]}, "count": n}.

    Exactly one of: `changed` (base, head), the commits a change is read between; `everything`, every shared item of the
    catalog; `canary_only`, the items it names as its canary; `words`, the items a person named. `changed` is read in the
    repository of the catalog, or in the engine's when `engine` is true. A change in the catalog tries the items it touches,
    and its canary when it touches none but changes what they share (CATALOG_PATHS). A change in the engine touches no item,
    and tries the canary when it changes what every item shares (ENGINE_PATHS). `git(folder, base, head)` gives the files.

    An item whose files do not load is never taken for one the change deleted: asked for, touched by the change, named by the
    canary, or among those of `everything`, it ends the plan (PlanError, `unreadable_items`). A canary that is asked for, or that
    an engine change needs, and that catalog.yml does not name, ends it too: a check with nothing to try would pass. A change of
    the catalog that would have tried it is told to `warn(sentence)`, when given, and tries nothing."""
    ns = available(default_catalog() if ns is None else ns)
    if sum(1 for one in (changed is not None, everything, canary_only, words is not None) if one) != 1:
        raise PlanError("Ask for one of: a change, everything, the canary, or named items")
    unreadable = unreadable_items(built, ns)

    def canary():
        names = canary_names(ns, built, unreadable)
        if not names:
            fix = "add `canary: [kind:name]` to the x-gravitee-catalog block of its catalog.yml"
            if engine or canary_only:
                raise PlanError(f"The catalog {ns} names no canary, so there is nothing to try: {fix}")
            if warn:
                warn(f"The catalog {ns} names no canary, so a change of what its items share tries nothing: {fix}")
        return names

    def alive(item):
        if item in unreadable:
            raise unreadable_error(ns, item, unreadable[item])
        return exists(lab_item(ns, item), built, ns)

    if words is not None:
        ids = {lab_item(ns, item): item for item in unreadable}
        for word in words:
            try:
                found = "{}:{}".format(*parse(word))
            except ValueError:
                continue   # `named` says so, in its words
            if found in ids:
                raise unreadable_error(ns, ids[found], unreadable[ids[found]])
        items = named(words, built, ns)
    elif everything:
        if unreadable:
            first = min(unreadable)
            raise unreadable_error(ns, first, unreadable[first])
        items = shared_items(built, ns)
    elif canary_only:
        items = [lab_item(ns, name) for name in canary()]
    else:
        folder = paths.ROOT if engine else paths.ROOT / paths.CATALOGS_DIR / ns
        files = git(folder, *changed)
        items = [lab_item(ns, name) for name in touched(files, ENGINE_PATHS if engine else CATALOG_PATHS, canary,
                                                        named=not engine, alive=alive)]
    return matrix(items, built)
