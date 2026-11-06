"""Shared by every seeder: settings from the environment, logging, waiting, HTTP calls and seed files."""

import fcntl
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path

import requests
import yaml
from urllib3.exceptions import NewConnectionError

SEED_DIR = Path(os.getenv("SEED_DIR", "/seed"))
STATE = Path("/state")   # the playground's .state folder: This machine's record, and a folder for the record of each place
STATE_FILE = Path(os.getenv("STATE_DIR", STATE)) / "created.json"
READY_TIMEOUT = int(os.getenv("READY_TIMEOUT", "300"))


class SeedError(Exception):
    """A step that cannot complete: the seed stops and the container exits with an error."""


class HttpError(SeedError):
    """A request the platform refused: its status and body are kept for whoever decides what the refusal means."""

    def __init__(self, message, status, body):
        super().__init__(message)
        self.status, self.body = status, body


class Gone(SeedError):
    """What the platform will not do however long the seed waits: wait_until does not retry it."""


class Stopped(BaseException):
    """Another step failed, so this one stops where it is: never reported, the first failure is. Not an Exception, so that no
    handler of a step that may fail ("optional", a retry) takes it for one of its own."""


def log(section, message):
    print(f"[{section}] {message}", flush=True)


# --- Running in parallel ------------------------------------------------------------------
# What the seed does at the same time is bounded in one place: at most WORKERS requests are in flight at any moment, whatever
# the number of threads that wait for their answers, so the load on the platform is the same for a seed that is sequential
# in one place and parallel in another. SEED_WORKERS=1 runs everything in order, as a seed used to. A step that fails stops
# the others at their next request or pause, and the seed reports that first failure alone.

WORKERS = 1
STOP = threading.Event()   # set by the first failure
_gate = threading.BoundedSemaphore(1)
_state = threading.RLock()   # held by whoever reads what the seed created, changes it and writes it back


def configure(workers=None):
    """Sets how many requests may be in flight (SEED_WORKERS, 4 by default) and forgets any earlier failure."""
    global WORKERS, _gate
    try:
        WORKERS = max(1, int(workers if workers is not None else os.getenv("SEED_WORKERS", "4")))
    except ValueError:
        raise SeedError("SEED_WORKERS is a number of requests") from None
    _gate = threading.BoundedSemaphore(WORKERS)
    STOP.clear()
    _later.clear()
    _deferring[0] = False


def check():
    if STOP.is_set():
        raise Stopped("stopped: another step failed")


def pause(seconds):
    """Waits, unless the seed is stopping."""
    check()
    time.sleep(seconds)
    check()


def guarded(function, *args):
    """function(*args); an error ends the seed for everyone."""
    try:
        return function(*args)
    except BaseException:
        STOP.set()
        raise


def parallel(function, items):
    """[function(item) for item in items], the items at the same time when SEED_WORKERS allows, for steps that do not depend on
    one another. The results are in the order of the items. An item that fails ends the seed: the others stop at their next
    request, and the failure of the first item that failed is raised once they have."""
    items = list(items)
    if WORKERS <= 1 or len(items) < 2:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(len(items), 16)) as pool:
        futures = [pool.submit(guarded, function, item) for item in items]
    for future in futures:   # the failure of the first item that failed, whichever was first in time: the same seed, the same words
        error = future.exception()
        if error is not None and not isinstance(error, Stopped):
            raise error
    return [future.result() for future in futures]


def waits_for_gateway():
    """Whether the seed waits for the gateway to take what it deployed (SEED_WAIT_GATEWAY=1). By default it does not: what the
    management API holds is what the seed made, and the gateway takes it within a few seconds (a first deployment of an AI
    model downloads it, which takes longer). What the waits check, and the sleep that gives the gateway time before a refill,
    are left out."""
    return os.getenv("SEED_WAIT_GATEWAY", "0").strip().lower() in ("1", "true", "yes", "on")


def together(jobs):
    """Calls each of `jobs`, no argument, at the same time as SEED_WORKERS allows: steps of different kinds that do not depend on
    one another."""
    return parallel(lambda job: job(), jobs)


_later = []
_deferring = [False]


def defer_waits():
    """From now on `later` puts a wait off to the end of the seed (seed.py asks for it; a module run alone waits where it is)."""
    _deferring[0] = True


def later(what, function):
    """A wait that nothing else depends on, put off to the end of the seed, where all of them are made at the same time, so that
    the steps that do not need it go on meanwhile. Made at once when the seed does not defer them."""
    if _deferring[0]:
        _later.append((what, function))
    else:
        function()


def run_later():
    pending = list(_later)
    _later.clear()
    parallel(lambda each: each[1](), pending)


def excerpt(text, limit=500):
    """A response body on one line: Gamma pretty-prints its errors, and the Lab UI shows the last line of the
    seed's output as the reason a seed failed."""
    return " ".join(str(text).split())[:limit]


def setting(name, default=None):
    """A required setting, unless a default is given. An empty value is no value: `NAME: ${NAME:-}` in Compose sets it."""
    value = os.getenv(name) or default
    if value in (None, ""):
        raise SeedError(f"{name} is not set")
    return value


# --- Seed files -------------------------------------------------------------------------

# ${VAR} or ${VAR:-default}, as in docker compose; $${ stays a literal ${
VARIABLE = re.compile(r"\$(\$)?\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# What render replaces: a variable, or any `$${` that is no variable's, which is how a page writes an expression of its own
# (`$${api.name}`) and has it left as `${api.name}`
TOKEN = re.compile(r"\$\$\{(?![A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\})|" + VARIABLE.pattern)

# What the engine itself gives the files of an environment (seed/environments/<id>/): the folder's id and the name its
# environment.yaml says. The seed service sets neither, so a file may use them with no value of its own.
PROVIDED = ("ENV_ID", "ENV_NAME")


def replace_variables(text, missing, json_escape=False, provided=None):
    """`text` with its variables replaced; the names that have no value are added to `missing`."""

    def substitute(m):
        if m.group(2) is None:
            return "${"
        if m.group(1):
            return m.group(0)[1:]
        value = provided[m.group(2)] if provided and m.group(2) in provided else os.getenv(m.group(2))
        if value in (None, ""):
            if m.group(3) is None:
                missing.add(m.group(2))
                return ""
            value = m.group(3)
        return json.dumps(value)[1:-1] if json_escape else value

    return TOKEN.sub(substitute, text)


def not_set(path, missing):
    if missing:
        raise SeedError(f"{path}: {', '.join(sorted(missing))} not set")


def render(text, path, json_escape=False, provided=None):
    """Replaces ${VAR} with the environment of the seed: everything that depends on the target. `provided` maps the names
    the engine gives (PROVIDED) to their values; they win over the environment of the seed."""
    missing = set()
    rendered = replace_variables(text, missing, json_escape, provided)
    not_set(path, missing)
    return rendered


def is_json_text(value):
    """Whether a string is itself a JSON object or array, as an endpoint's `sharedConfiguration` is: its variables are
    placeholders inside the strings of that text."""
    if not value.lstrip().startswith(("{", "[")):
        return False
    try:
        return isinstance(json.loads(value), (dict, list))
    except ValueError:
        return False


def render_json(text, path, provided=None):
    """A JSON file read as a document, with the variables replaced in each of its strings. A string that is itself JSON text
    gets the values escaped for that text, so that a password with a quote or a backslash arrives as it was set. A file that
    is no document until its variables are replaced (a bare ${PORT}) is replaced as text."""
    try:
        document = json.loads(text)
    except ValueError:
        return json.loads(render(text, path, json_escape=True, provided=provided))
    missing = set()

    def walk(node):
        if isinstance(node, dict):
            return {walk(key): walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str):
            return replace_variables(node, missing, is_json_text(node), provided)
        return node

    rendered = walk(document)
    not_set(path, missing)
    return rendered


# The loader of YAML copies the pairs of a mapping into each mapping that merges it with a `<<` key, and each of those may have
# merged others before: a file of twenty such keys, each merging the one before it twice, makes it copy a million pairs
# (about 0.3 s), and thirty fill the memory of the machine. A catalog's seed is a stranger's file, and the seed container
# would hang on it. The real files need a few hundred pairs; the limit is thousands of times that.
COPIED = 1_000_000
MERGE = "tag:yaml.org,2002:merge"
MERGES = ("merges mappings with << keys so that the loader of YAML would copy millions of pairs, which takes minutes or all "
          "the memory. Fix: write the values out, or merge less")


class MergeError(SeedError):
    """A YAML text whose merge keys would make the loader copy more than COPIED pairs: it is not loaded."""


def _copied(root):
    """How many pairs the loader copies for the merge keys of a composed document (`root`, a node or None), counted on the
    nodes, which hold no value: 0 when there is none, and more than COPIED stops the count at COPIED + 1. A key merges the
    pairs of the mappings it names into its own mapping, and each of those may have merged others before: the loader works
    on the pairs each time it reaches a mapping (the merge, the scan of the mapping it merges, the construction), so the
    count adds those. An anchor is walked once, whatever refers to it, and a cycle ends."""
    mappings, seen, pending = [], set(), [root]
    while pending:   # the nodes of the document, once each: an alias is the node it names
        node = pending.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            mappings.append(node)
            for key, value in node.value:
                pending.extend((key, value))
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(node.value)
    own, merged = {}, {}
    for node in mappings:
        pairs, names = 0, []
        for key, value in node.value:
            if key.tag != MERGE:
                pairs += 1
            elif isinstance(value, yaml.MappingNode):
                names.append(value)
            elif isinstance(value, yaml.SequenceNode):
                names.extend(each for each in value.value if isinstance(each, yaml.MappingNode))
        own[id(node)], merged[id(node)] = pairs, names
    size, entered = {}, set()   # the pairs a mapping holds once it has merged the others (a cycle counts as none)
    for start in mappings:
        pending = [(start, False)]
        while pending:
            node, ready = pending.pop()
            if ready:
                size[id(node)] = min(COPIED + 1, own[id(node)] + sum(size.get(id(each), 0) for each in merged[id(node)]))
            elif id(node) not in entered:
                entered.add(id(node))
                pending.append((node, True))
                pending.extend((each, False) for each in merged[id(node)])
    work = 0
    for node in mappings:
        if merged[id(node)]:
            work += size[id(node)] + 2 * sum(size[id(each)] for each in merged[id(node)])
            if work > COPIED:
                return COPIED + 1
    return work


def merged_pairs(text):
    """The pairs the loader of YAML copies for the merge keys of a text (see `_copied`), 0 for a text that has none or is no
    YAML: loading it says why."""
    if "<<" not in text and "!" not in text:   # a merge key is the plain scalar `<<`, or a scalar of the tag `!!merge`
        return 0
    try:
        return _copied(yaml.compose(text, Loader=yaml.SafeLoader))
    except Exception:  # noqa: BLE001 - a text that does not compose is no work for the loader either
        return 0


class _Loader(yaml.SafeLoader):
    """The safe loader, which marks each scalar whose type YAML reads from its text: no tag in the file, or the bare `!`."""

    def compose_scalar_node(self, anchor):
        implicit = self.peek_event().tag in (None, "!")
        node = super().compose_scalar_node(anchor)
        node.implicit = implicit
        return node


def parse_yaml(text, path="the text", fill=None):
    """`yaml.safe_load(text)`, but a text whose merge keys would make the loader copy more than COPIED pairs is a MergeError,
    and the loader never runs on it. The document is composed once and the count is of that: what a normal file loads is what
    safe_load gives, and it is not read twice. `fill(root, loader)` may change the values of the composed document before it
    is built (`render_yaml`)."""
    loader = _Loader(text)
    try:
        root = loader.get_single_node()
        if root is None:
            return None
        if ("<<" in text or "!" in text) and _copied(root) > COPIED:
            raise MergeError(f"{path}: {MERGES}")
        if fill:
            fill(root, loader)
        return loader.construct_document(root)
    finally:
        loader.dispose()


STR = "tag:yaml.org,2002:str"


def _filler(missing, provided):
    """What replaces the variables in each string of a composed YAML document, its keys too, once each however many aliases
    name it. A string that is itself JSON text gets the values escaped for that text, as in a JSON file. A plain scalar (no
    quotes, no tag) is then read as YAML reads its text, so that `port: ${PORT}` is the number it is in a file written out, and
    `!!str ${PORT}` the string; its value never becomes a mapping, a list or a merge key, whatever it holds."""
    def fill(root, loader):
        seen, pending = set(), [root]
        while pending:
            node = pending.pop()
            if node is None or id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, yaml.MappingNode):
                for key, value in node.value:
                    pending.extend((key, value))
            elif isinstance(node, yaml.SequenceNode):
                pending.extend(node.value)
            elif "$" in node.value:
                value = replace_variables(node.value, missing, is_json_text(node.value), provided)
                if node.style is None and node.implicit and node.tag == STR and value != node.value:
                    tag = loader.resolve(yaml.ScalarNode, value, (True, False))
                    node.tag = STR if tag == MERGE else tag
                node.value = value
    return fill


def unreadable(path, error):
    """Why a YAML file cannot be read, with its line and never its text: what the parser quotes of it may hold a password."""
    mark = getattr(error, "problem_mark", None)
    return f"{path}: it is not valid YAML once its variables are replaced" + (f" (line {mark.line + 1})" if mark else "")


def render_yaml(text, path, provided=None):
    """A YAML file read as a document, with the variables replaced in each of its strings, as `render_json` does for JSON: a
    value with a quote, a backslash or a line break arrives as it was set and leaves the file around it as it is. A file that
    is no YAML until its variables are replaced is replaced as text. YAML that would make the loader copy millions of pairs for
    its merge keys is a MergeError, and a file that is no YAML says at which line, never what it holds."""
    missing = set()
    try:
        loaded = parse_yaml(text, path, _filler(missing, provided))
    except yaml.YAMLError:
        try:
            return parse_yaml(render(text, path, provided=provided), path)
        except (yaml.YAMLError, ValueError) as e:
            raise SeedError(unreadable(path, e)) from e
    except ValueError as e:   # a value that YAML reads as a date that is none
        raise SeedError(unreadable(path, e)) from e
    not_set(path, missing)
    return loaded


def load(path, provided=None):
    """A seed file (YAML or JSON) with its variables replaced, each into the strings of the document (`render_yaml`,
    `render_json`). YAML that would make the loader copy millions of pairs for its merge keys is a MergeError."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".json"):
        return render_json(text, path, provided)
    return render_yaml(text, path, provided) or {}


def text(path, provided=None):
    """A seed file that is read as it is, the text of a page for instance, with its variables replaced."""
    return render(Path(path).read_text(encoding="utf-8"), path, provided=provided)


def files(*parts, suffixes=(".yaml", ".yml", ".json")):
    """Seed files of a folder, in name order (prefix them with 01-, 02- to choose the order)."""
    folder = SEED_DIR.joinpath(*parts)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix in suffixes and not p.name.startswith("."))


def folders(*parts):
    """The names of the folders inside a folder of the seed, in name order."""
    folder = SEED_DIR.joinpath(*parts)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def seed_file(*parts):
    path = SEED_DIR.joinpath(*parts)
    return path if path.is_file() else None


def has_seed(*paths):
    """Whether any of these files or folders of the seed exists ("aim/providers"): what a module asks before it reads a
    setting or connects, so that a playground that does not seed it pays nothing for it."""
    return any(SEED_DIR.joinpath(path).exists() for path in paths)


# --- HTTP -------------------------------------------------------------------------------
# One policy for every request of the seed. A request the platform could not answer now (no connection, no answer in time, a
# 5xx, a 429) is sent again, RETRIES times RETRY_PAUSE seconds apart, when sending it again cannot make a second object: a GET,
# a PUT or a DELETE, a POST its caller says is idempotent (a deployment, a start), and any request the platform did not take
# (the connection could not be made, a 429 or a 503). A POST or a PATCH whose answer is lost is not sent again: the platform
# may have made what it asked for (see `making`). Then the seed stops with what happened, in words. Inside a wait
# (`wait_until`) a request is sent once: the wait asks again itself.

TIMEOUT = 30   # seconds a request waits for its answer, unless it says otherwise
RETRIES = 7   # times a request the platform could not answer now is sent again, before the seed stops
RETRY_PAUSE = 8   # seconds before each: a management API that restarts is back within about a minute
REPEATABLE = ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")   # what may be sent again whatever became of the first
NOT_TAKEN = (429, 503)   # what a platform answers for a request it did not take
BUSY = (500, 502, 504) + NOT_TAKEN   # what it answers when it cannot answer now
OK = range(200, 300)
_local = threading.local()   # `waiting`: whether this thread is in a wait_until, which asks again itself


def unsent(error):
    """Whether a request that failed never reached the platform: its connection could not be made."""
    if isinstance(error, requests.ConnectTimeout):
        return True
    reason = getattr(error.args[0], "reason", None) if error.args else None
    return isinstance(reason, NewConnectionError)


def trouble(error, timeout=None):
    """What happened to a request that got no answer, in words: the library's own text names addresses and internals."""
    if unsent(error):
        return "cannot connect"
    if isinstance(error, requests.Timeout):
        return f"no answer in {timeout} s" if timeout else "no answer in time"
    return "the connection was cut"


class Api:
    """A JSON HTTP client that reports the response body on errors. `section` prefixes what it says when it asks again."""

    def __init__(self, base_url, section="seed"):
        self.base_url, self.section = base_url.rstrip("/"), section
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    def send(self, method, path, **kwargs):
        """One request, whatever the answer, within TIMEOUT seconds unless it gives its own. At most SEED_WORKERS are in
        flight at once, and none starts once the seed stops."""
        check()
        kwargs.setdefault("timeout", TIMEOUT)
        with _gate:
            return self.session.request(method, self.base_url + path, **kwargs)

    def busy(self, response):
        """Whether an answer says the platform cannot answer now, rather than anything about the request."""
        return response.status_code in BUSY

    def call(self, method, path, expect=OK, timeout=TIMEOUT, what=None, idempotent=None, **kwargs):
        """The answer to a request, sent again as the policy above says. A status out of `expect` (any 2xx by default) is an
        HttpError; no answer is a SeedError. `what` names the request in what is said (its method and path by default);
        `idempotent` says that a POST or a PATCH may be sent again (a GET, a PUT or a DELETE may)."""
        said = what or f"{method} {path}"
        repeatable = method.upper() in REPEATABLE if idempotent is None else idempotent
        tries = 1 if getattr(_local, "waiting", False) else RETRIES + 1
        for attempt in range(1, tries + 1):
            tried = f" ({attempt} tries)" if attempt > 1 else ""
            try:
                r = self.send(method, path, timeout=timeout, **kwargs)
            except requests.RequestException as e:
                problem = trouble(e, timeout)
                if attempt == tries or not (repeatable or unsent(e)):
                    raise SeedError(f"{said}: {problem}{tried}") from None
            else:
                if r.status_code in expect:
                    return r
                problem = f"HTTP {r.status_code}"
                if attempt == tries or not (self.busy(r) and (repeatable or r.status_code in NOT_TAKEN)):
                    raise HttpError(f"{said}: {problem} {excerpt(r.text)}".rstrip() + tried, r.status_code, r.text)
            log(self.section, f"! {said}: {problem}, trying again in {RETRY_PAUSE} s ({attempt}/{RETRIES})")
            pause(RETRY_PAUSE)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, **kw):
        return self.call("POST", path, **kw)

    def put(self, path, **kw):
        return self.call("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.call("PATCH", path, **kw)

    def delete(self, path, **kw):
        """What is already gone counts as deleted; any other refusal is an error."""
        return self.call("DELETE", path, expect=(200, 202, 204, 404), **kw)


POLLS = (0.5, 1, 2, 3)   # seconds before each look again at what is not ready: the first looks are close, then every 3 s
REFUSED = (400, 401, 403)   # what a management API answers to credentials or ids it will never take: `final` of its first wait


def wait_until(section, what, probe, timeout=READY_TIMEOUT, final=()):
    """Retries probe() until it returns without an error: management APIs answer before they are fully started. A probe
    that raises Gone, or an HttpError of a status in `final` (REFUSED for the first call to a platform: a wrong password,
    token or environment id is not fixed by waiting), ends the wait at once. A request of the probe is sent once: the wait
    is what asks again."""
    deadline = time.time() + timeout
    last, looks = None, 0
    log(section, f"Waiting for {what}")
    while time.time() < deadline:
        waiting, _local.waiting = getattr(_local, "waiting", False), True
        try:
            probe()
            log(section, f"✓ {what} ready")
            return
        except Gone:
            raise
        except HttpError as e:
            if e.status in final:
                raise SeedError(f"{what}: {e}") from None
            last = e
        except requests.RequestException as e:
            last = trouble(e)
        except SeedError as e:
            last = e
        finally:
            _local.waiting = waiting
        pause(POLLS[min(looks, len(POLLS) - 1)])
        looks += 1
    raise SeedError(f"{what} not ready after {timeout} s" + (f": {last}" if last else ""))


def items(data):
    """Lists from either a bare array or a paged {"data": [...]} response."""
    if isinstance(data, dict):
        return data.get("data", data.get("content", []))
    return data or []


# --- What the seed created ---------------------------------------------------------------
# Only what it created, never what already existed: removing a playground deletes exactly this list. It is read and written
# by one step at a time in this process (`_state`) and by one process at a time (a lock beside it: a removal may run while a
# seed still does), and a record that cannot be read or written stops the seed rather than lose what it holds.

KEYS = ("product", "kind", "name", "id")   # what every record has: what it is, and how the seed finds it again
_holding = [0]   # how deep the step that holds the record is in it


def _record(problem, fix=False):
    """Why the record stops the seed, with the file named: created.json in the playground's .state folder for This machine, in
    .state/<platform id>/<environment id> for a place (a deploy to Gravitee Cloud, whose STATE_DIR is that folder). `fix` adds
    what to do with a record that cannot be read: a Reset empties This machine's record, never a place's."""
    folder = STATE_FILE.parent
    place = folder.relative_to(STATE).as_posix() if STATE in folder.parents else None
    where = f".state/{place}" if place else ".state"
    message = f"The seed's record of what it created (created.json in the playground's {where} folder) {problem}"
    if fix:
        message += ": fix it, or " + ("delete it and what the seed made in that environment" if place else "Reset")
    return message


@contextmanager
def held():
    """The record for this step alone, in this process and across processes, for as long as the block lasts."""
    with _state:
        if _holding[0]:
            _holding[0] += 1
            try:
                yield
            finally:
                _holding[0] -= 1
            return
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock = open(STATE_FILE.with_name(STATE_FILE.name + ".lock"), "a")
        except OSError as e:
            raise SeedError(_record(f"cannot be written: {e.strerror or e}")) from None
        with lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _holding[0] = 1
            try:
                yield
            finally:
                _holding[0] = 0
                fcntl.flock(lock, fcntl.LOCK_UN)


def _read():
    """Every record as the file holds it, those of what the seed is making (`making`) too; [] when there is no record yet. One
    that cannot be read, or is not a list of records, stops the seed: read as nothing, the next write would forget all it
    holds, and a removal would delete nothing and say it did."""
    with held():
        for attempt in (1, 2):   # a mounted folder can fail a read for a moment
            try:
                text = STATE_FILE.read_text(encoding="utf-8")
                break
            except FileNotFoundError:
                return []
            except OSError as e:
                if attempt == 2:
                    raise SeedError(_record(f"cannot be read ({e.strerror or e})", fix=True)) from None
                pause(0.5)
        try:
            records = json.loads(text)
        except ValueError:
            records = None
        if not isinstance(records, list) or not all(isinstance(x, dict) and all(key in x for key in KEYS) for x in records):
            raise SeedError(_record("is damaged", fix=True))
        return records


def created():
    """The records of what the seed created (see `_read`): what it is still making, with no id yet, is no object of it."""
    return [x for x in _read() if x["id"] is not None]


def recorded(product, kind, id):
    """Whether the seed created this item."""
    return any((x["product"], x["kind"], x["id"]) == (product, kind, id) for x in created())


def record(product, kind, label, name, id, **extra):
    """Adds a created item (or replaces the one with the same product, kind and name: an API replaced).
    `label` is what people call things of this kind, in the plural: the lab shows it when it asks whether
    to delete them ("3 MCP servers")."""
    with held():
        records = [x for x in _read() if (x["product"], x["kind"], x["name"]) != (product, kind, name)]
        records.append({"product": product, "kind": kind, "label": label, "name": name, "id": id, **extra})
        save_created(records)


def forget(*gone):
    """Drops the records of what a removal has just deleted, as soon as it is: a removal that fails later keeps the records of
    what is still there, and those alone."""
    if not gone:
        return
    with held():
        drop = {tuple(x[key] for key in KEYS) for x in gone}
        save_created([x for x in _read() if tuple(x[key] for key in KEYS) not in drop])


def notes(product, kind):
    """The notes of `making` of this kind: what the seed was making, with no id yet."""
    return [x for x in _read() if (x["product"], x["kind"], x["id"]) == (product, kind, None)]


def pending(product, kind, name):
    """The note of `making` for this object, None when there is none."""
    return next((x for x in notes(product, kind) if x["name"] == name), None)


@contextmanager
def making(product, kind, label, name, missing=False, **extra):
    """The create of one object, noted before it is asked for: a record under its name, with no id yet and a count of 0, which
    the Lab counts as nothing. `record`, with the id the platform answers, takes its place. When the answer is lost (no answer
    in time, a seed stopped in the middle of the request), it stays, and the next run takes the object it finds under that
    name as its own (`adopted`), where it would stop on an object it did not create. A refusal of the request (a 4xx) takes
    it back: nothing was made. A record the seed has under that name is left as it is: the record of the answer replaces it.
    With `missing`, the caller looked the object up and found none, so that record may name one that is gone (deleted in a
    console): the note is written beside it, so that the next run adopts what a lost answer made, and a refusal, which
    takes back the note alone, leaves the record as it was."""
    with held():
        records = [x for x in _read() if (x["product"], x["kind"], x["name"], x["id"]) != (product, kind, name, None)]
        if missing or not any((x["product"], x["kind"], x["name"]) == (product, kind, name) for x in records):
            save_created([*records, {"product": product, "kind": kind, "label": label, "name": name, "id": None, "count": 0,
                                     **extra}])
    try:
        yield
    except HttpError as e:
        if e.status < 500:
            unmade(product, kind, name)
        raise


def unmade(product, kind, name):
    """Takes back what `making` said the seed was making, when the platform answered that it made nothing."""
    with held():
        entry = pending(product, kind, name)
        if entry:
            forget(entry)


def claimed(product, kind, name):
    """The note of `making` when an earlier seed was making an object of this kind and name and lost the platform's answer: the
    object found under that name is then the seed's, and the note goes. None when there is none. The caller records the object."""
    with held():
        entry = pending(product, kind, name)
        if entry:
            forget(entry)
            log(product, f"! {name}: made by an earlier seed that lost the platform's answer, now known as the seed's")
        return entry


def adopted(product, kind, name, id, **extra):
    """Whether the object the seed finds under this name, with no record of it, is the one an earlier seed was making when it
    lost the platform's answer (`claimed`): then it is recorded with its id, as the seed's, and True."""
    with held():
        entry = claimed(product, kind, name)
        if entry is None:
            return False
        kept = {key: value for key, value in entry.items() if key not in (*KEYS, "label", "count")}
        record(product, kind, entry["label"], name, id, **{**kept, **extra})
    return True


def save_created(records):
    """Writes the records whole: a file of its own, written and synced, then put in place of the record, so that another
    reader never sees half of it. A record that cannot be written stops the seed: what it made would be left with nothing to
    say it is the seed's."""
    with held():
        partial = STATE_FILE.with_name(f"{STATE_FILE.name}.{uuid.uuid4().hex}.part")
        try:
            with open(partial, "x", encoding="utf-8") as f:
                f.write(json.dumps(records, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(partial, STATE_FILE)
        except OSError as e:
            with suppress(OSError):
                partial.unlink()
            raise SeedError(_record(f"cannot be written: {e.strerror or e}")) from None


def owned(records, product, order):
    """What a module removes: its records, whose kinds `order` lists, dependents first. A record of another product, or
    of a kind another module of the same product owns, is none of its business."""
    return sorted((x for x in records if x["product"] == product and x["kind"] in order),
                  key=lambda x: order.index(x["kind"]))
