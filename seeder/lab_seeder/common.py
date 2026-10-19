"""Shared by every seeder: settings from the environment, logging, waiting, HTTP calls and seed files."""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml

SEED_DIR = Path(os.getenv("SEED_DIR", "/seed"))
STATE_FILE = Path(os.getenv("STATE_DIR", "/state")) / "created.json"
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
_failures = []
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
    _failures.clear()
    _later.clear()
    _milestones.clear()
    _deferring[0] = False


def stop(error):
    """Records the first failure and tells every other step to stop."""
    with _state:
        if not _failures and not isinstance(error, Stopped):
            _failures.append(error)
        STOP.set()


def failure():
    """The first failure of the seed, or None."""
    return _failures[0] if _failures else None


def check():
    if STOP.is_set():
        raise Stopped("stopped: another step failed")


def slot():
    """What a request of one's own (another client than Api) holds while it is in flight: `with slot(): ...`"""
    check()
    return _gate


def pause(seconds):
    """Waits, unless the seed is stopping."""
    check()
    time.sleep(seconds)
    check()


def guarded(function, *args):
    """function(*args); an error ends the seed for everyone, and is the one reported when it is the first."""
    try:
        return function(*args)
    except BaseException as error:
        stop(error)
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
    if STOP.is_set():
        raise failure() or SeedError("the seed was stopped")
    return [future.result() for future in futures]


_milestones = {}


def milestone(name):
    """Says that `name` is done: the steps that wait for it (`reached`) go on."""
    with _state:
        _milestones.setdefault(name, threading.Event()).set()


def reached(name, also=None):
    """Waits until `name` (or `also`, which says that everything of it is done) is, and not past the end of the seed. Returns at
    once when the seed runs in order: what it waits for is done."""
    if WORKERS <= 1:
        return
    with _state:
        events = [_milestones.setdefault(each, threading.Event()) for each in (name, also) if each]
    while not any(event.is_set() for event in events):
        check()
        events[0].wait(0.05)


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


def parse_yaml(text, path="the text"):
    """`yaml.safe_load(text)`, but a text whose merge keys would make the loader copy more than COPIED pairs is a MergeError,
    and the loader never runs on it. The document is composed once and the count is of that: what a normal file loads is what
    safe_load gives, and it is not read twice."""
    loader = yaml.SafeLoader(text)
    try:
        root = loader.get_single_node()
        if root is None:
            return None
        if ("<<" in text or "!" in text) and _copied(root) > COPIED:
            raise MergeError(f"{path}: {MERGES}")
        return loader.construct_document(root)
    finally:
        loader.dispose()


def parse_yaml_all(text, path="the text"):
    """`list(yaml.safe_load_all(text))`, with the guard of `parse_yaml` for a text of several documents: the pairs the loader
    would copy for all of them are counted together, and a text that asks for more than COPIED is a MergeError before any
    document is built. A document is composed once, and the count is of that."""
    loader = yaml.SafeLoader(text)
    try:
        roots, total = [], 0
        while loader.check_node():
            root = loader.get_node()
            if "<<" in text or "!" in text:
                total += _copied(root)
                if total > COPIED:
                    raise MergeError(f"{path}: {MERGES}")
            roots.append(root)
        return [loader.construct_document(root) for root in roots]
    finally:
        loader.dispose()


def load(path, provided=None):
    """A seed file (YAML or JSON) with its variables replaced. YAML that would make the loader copy millions of pairs for its
    merge keys is a MergeError."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".json"):
        return render_json(text, path, provided)
    return parse_yaml(render(text, path, provided=provided), path) or {}


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

class Api:
    """A JSON HTTP client that reports the response body on errors."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    def send(self, method, path, **kwargs):
        """One request, whatever the answer. At most SEED_WORKERS are in flight at once, and none starts once the seed stops."""
        check()
        with _gate:
            return self.session.request(method, self.base_url + path, **kwargs)

    def call(self, method, path, expect=range(200, 300), timeout=30, **kwargs):
        """A status out of `expect` (any 2xx by default) is an error. `timeout` is in seconds, for what takes long."""
        r = self.send(method, path, timeout=timeout, **kwargs)
        if r.status_code not in expect:
            raise HttpError(f"{method} {path}: HTTP {r.status_code} {excerpt(r.text)}", r.status_code, r.text)
        return r

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


def wait_until(section, what, probe, timeout=READY_TIMEOUT):
    """Retries probe() until it returns without an error: management APIs answer before they are fully started. A
    probe that raises Gone ends the wait at once."""
    deadline = time.time() + timeout
    last, looks = None, 0
    log(section, f"Waiting for {what}")
    while time.time() < deadline:
        try:
            probe()
            log(section, f"✓ {what} ready")
            return
        except Gone:
            raise
        except (requests.RequestException, SeedError) as e:
            last = e
        pause(POLLS[min(looks, len(POLLS) - 1)])
        looks += 1
    raise SeedError(f"{what} not ready after {timeout} s" + (f": {last}" if last else ""))


def items(data):
    """Lists from either a bare array or a paged {"data": [...]} response."""
    if isinstance(data, dict):
        return data.get("data", data.get("content", []))
    return data or []


# --- What the seed created ---------------------------------------------------------------
# Only what it created, never what already existed: removing a playground deletes exactly this list.

def created():
    with _state:   # not while another step replaces the file: a mounted folder can fail a read for a moment, and nothing is the answer of a failed read
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []


def recorded(product, kind, id):
    """Whether the seed created this item."""
    return any((x["product"], x["kind"], x["id"]) == (product, kind, id) for x in created())


def record(product, kind, label, name, id, **extra):
    """Adds a created item (or replaces the one with the same product, kind and name: an API replaced).
    `label` is what people call things of this kind, in the plural: the lab shows it when it asks whether
    to delete them ("3 MCP servers")."""
    with _state:
        records = [x for x in created() if (x["product"], x["kind"], x["name"]) != (product, kind, name)]
        records.append({"product": product, "kind": kind, "label": label, "name": name, "id": id, **extra})
        save_created(records)


def save_created(records):
    with _state:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            partial = STATE_FILE.with_name(STATE_FILE.name + ".part")   # whole or not at all: another step may read it
            partial.write_text(json.dumps(records, indent=2), encoding="utf-8")
            os.replace(partial, STATE_FILE)
            return
        except OSError as e:
            log("seed", f"! Cannot record what was created ({e}): removing the playground will not delete it")


def owned(records, product, order):
    """What a module removes: its records, whose kinds `order` lists, dependents first. A record of another product, or
    of a kind another module of the same product owns, is none of its business."""
    return sorted((x for x in records if x["product"] == product and x["kind"] in order),
                  key=lambda x: order.index(x["kind"]))
