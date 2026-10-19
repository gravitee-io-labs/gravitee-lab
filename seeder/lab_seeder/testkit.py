"""What the seeder's tests share, in the engine and in a catalog: a platform that answers from a table of routes, a seed
to run against it, and the seed services of playgrounds. A module is tested in its own file, `tests/test_<module>.py`:

    from .testkit import Seed

    class Providers(Seed):
        def test_a_missing_provider_is_created(self):
            providers = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim/providers"
            self.answer({("GET", providers): (200, {"data": []}), ("POST", providers): (201, {"id": "p1"})})
            aim_catalog.seed()
            self.assertEqual(self.platform.changes(), [("POST", providers)])

The settings are those of SETTINGS, the state is a temporary file, the seed folder is empty (`self.seed_files` fills it),
nothing sleeps and the output is kept (`output`).

A test that needs the files of a playground reads them under PLAYGROUNDS: the folder `LAB_PLAYGROUNDS` names, so that a
catalog runs the engine's checks on its own playgrounds, or else the frozen sample of the engine, SAMPLES. Only the checks
that take every playground they find (test_seed_files and test_common) run on any folder; the others name the files of the samples.
A catalog's own tests read PLAYGROUNDS as well: its test target sets `LAB_PLAYGROUNDS`, else they would read the samples.
This module lives in `seeder/`, beside the modules it tests, so a catalog puts that folder on `sys.path` and imports it as
`testkit`.

The checks of the seed files and the reading of a playground's seed service (`unset_variables`, `literal_addresses`,
`escaped_addresses`, `seed_service`, `seeded_playgrounds` and the rest of the last section) are those of seedlint.py, which a
catalog's CI runs on the command line: this module names them and holds no copy, so that the tests and the CI never disagree,
and a compose file is read with the guards of the lint (a link, a large file or a bomb of YAML is refused, not loaded). A test
of a catalog that looks for the addresses its seed files write out calls both `literal_addresses(text)` and
`escaped_addresses(path, text)`: the second finds what a YAML or JSON file writes in escapes.
"""

import fnmatch
import io
import json
import os
import tempfile
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

try:
    import requests
    from requests.structures import CaseInsensitiveDict

    from . import seedlint   # reads the seed with pyyaml and the seeder's own `common`, and says so itself when they are missing
except ImportError as e:
    raise ImportError("The seeder's tests need requests and pyyaml: pip install -r seeder/requirements.txt "
                      "(Python 3.10 or later), or run make setup") from e

from . import common
from . import controls
from . import refills
from . import seed

SETTINGS = {"APIM_MANAGEMENT_URL": "http://apim", "APIM_USERNAME": "admin", "APIM_PASSWORD": "admin",
            "AM_MANAGEMENT_URL": "http://am", "AM_USERNAME": "admin", "AM_PASSWORD": "adminadmin",
            "OPENFGA_API_URL": "http://openfga", "SEED_WORKERS": "1", "SEED_WAIT_GATEWAY": "1"}

# The two playgrounds the engine's tests read when no catalog is given: invented samples (a bakery and a hostel chain), so
# that the engine holds no content of a catalog
SAMPLES = seedlint.SAMPLES


def playgrounds_root(environ=None):
    """The folder of playgrounds the tests read: `LAB_PLAYGROUNDS` when it is set and not empty (a relative path is the
    one of the folder the tests run from), else SAMPLES."""
    return seedlint.playgrounds_root(os.environ if environ is None else environ)


def personal_root(environ=None):
    """The folder of the personal playgrounds of this machine, for the tests of one that is not shared yet (they are
    skipped where it is not here): `local/` of the folder `LAB_PLAYGROUNDS` names, else the one of this clone."""
    if (os.environ if environ is None else environ).get("LAB_PLAYGROUNDS"):
        return playgrounds_root(environ) / "local"
    return Path(__file__).resolve().parents[2] / "playgrounds" / "local"


PLAYGROUNDS = playgrounds_root()
PERSONAL = personal_root()


# --- The platform -----------------------------------------------------------------------

class Response:
    """What requests returns: a status, a body (a dict or a list is JSON, a string is text) and headers."""

    def __init__(self, status, body=None, headers=None):
        self.status_code, self.body, self.headers = status, body, CaseInsensitiveDict(headers or {})
        self.ok = status < 400
        if body is None:
            self.text = ""
        elif isinstance(body, str):
            self.text = body
            self.headers.setdefault("Content-Type", "text/plain")
        else:
            self.text = json.dumps(body)
            self.headers.setdefault("Content-Type", "application/json")
        self.content = self.text.encode()

    def json(self):
        if self.body is None or isinstance(self.body, str):
            raise requests.exceptions.JSONDecodeError("not JSON", self.text, 0)
        return self.body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


# What the platform was asked: `url` whole, `path` without its query, `params` from the URL and the call, `headers` as
# sent (the session's and the call's, without those the call removes with None), `auth` from the call or its session.
Call = namedtuple("Call", "method url path params json data files headers auth timeout")


def specificity(route, call):
    """None when the route does not answer the call, else how closely it does: more for one that names query parameters."""
    method, path = route
    if method != call.method:
        return None
    if hasattr(path, "fullmatch"):
        return 0 if path.fullmatch(call.path) else None
    where, _, query = path.partition("?")
    if where != (call.url.split("?")[0] if where.startswith("http") else call.path):
        return None
    wanted = parse_qsl(query)
    if any(str(call.params.get(name)) != value for name, value in wanted):
        return None
    return 1 + len(wanted)


def respond(answer, call, hit):
    """The Response of an answer. `hit` counts the calls the route has answered, for a list."""
    if isinstance(answer, list):
        return respond(answer[min(hit, len(answer) - 1)], call, hit)
    if callable(answer):
        return respond(answer(call), call, hit)
    if isinstance(answer, Exception):
        raise answer
    if isinstance(answer, Response):
        return answer
    return Response(*answer) if isinstance(answer, tuple) else Response(answer)


class Platform:
    """The platform as the seed's HTTP sessions see it, from a table of routes, {(method, path): answer}.

    A path is a string: "/stores"; with query parameters that must be sent, "/apis?page=2" (the route naming the
    most wins); starting with http, matched against the whole URL; or a compiled regular expression that must match
    the whole path. An answer is a status, (status, body) or (status, body, headers), a Response, a function of the
    Call that gives one of those, an exception to raise, or a list of them: one per call, the last one for the rest.
    A call no route answers fails the test. `calls` lists every call; `changes()` those that are not a GET."""

    def __init__(self, routes=None):
        self.routes, self.calls, self.hits = dict(routes or {}), [], {}

    def session(self):
        return Session(self)

    def add(self, routes):
        """Adds routes, or replaces those with the same method and path: a list answers from its first item again."""
        self.routes.update(routes)
        for route in routes:
            self.hits.pop(route, None)

    def handle(self, call):
        self.calls.append(call)
        matches = [(specificity(route, call), route) for route in self.routes]
        matches = [(rank, route) for rank, route in matches if rank is not None]
        if not matches:
            raise AssertionError(f"Unexpected {call.method} {call.path}" + (f" {call.params}" if call.params else ""))
        route = max(matches, key=lambda match: match[0])[1]
        self.hits[route] = hit = self.hits.get(route, -1) + 1
        return respond(self.routes[route], call, hit)

    def sent(self, method, path):
        """The calls a route (a method and a path, as in the table) would answer."""
        return [call for call in self.calls if specificity((method, path), call) is not None]

    def changes(self):
        return [(call.method, call.path) for call in self.calls if call.method != "GET"]


class Session:
    """A requests.Session of the platform: its own headers and credentials, the platform's routes."""

    def __init__(self, platform):
        self.platform, self.headers, self.auth = platform, CaseInsensitiveDict(), None

    def request(self, method, url, params=None, data=None, headers=None, files=None, auth=None, timeout=None,
                json=None, **ignored):
        sent = CaseInsensitiveDict({**self.headers, **(headers or {})})
        for name in [name for name, value in sent.items() if value is None]:
            del sent[name]
        parts = urlsplit(url)
        call = Call(method.upper(), url, parts.path, {**dict(parse_qsl(parts.query)), **dict(params or {})}, json, data,
                    files, sent, auth or self.auth, timeout)
        return self.platform.handle(call)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()


class FakeRedis:
    """The part of redis-py a refill uses, over the hashes of a dict, {key: {field: value}}: scan_iter with a glob, a
    pipeline of hget, delete and close. A key whose value is not a dict is not a hash: asking it for a field is an error,
    which a pipeline gives in place of the answer when it is told not to raise. `calls` lists what was asked."""

    class WrongType(Exception):
        pass

    def __init__(self, hashes=None):
        self.data, self.calls, self.closed = {key: value for key, value in (hashes or {}).items()}, [], False

    def scan_iter(self, match="*", count=None):
        self.calls.append(("scan", match))
        return iter([key for key in list(self.data) if fnmatch.fnmatchcase(key, match)])

    def hget(self, key, field):
        if key not in self.data:
            return None
        if not isinstance(self.data[key], dict):
            raise self.WrongType("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self.data[key].get(field)

    def pipeline(self, transaction=True):
        return FakeBatch(self)

    def delete(self, *keys):
        self.calls.append(("delete", sorted(keys)))
        return sum(self.data.pop(key, None) is not None for key in keys)

    def close(self):
        self.closed = True


class FakeBatch:
    def __init__(self, redis):
        self.redis, self.asked = redis, []

    def hget(self, key, field):
        self.asked.append((key, field))
        return self

    def execute(self, raise_on_error=True):
        self.redis.calls.append(("pipeline", len(self.asked)))
        answers = []
        for key, field in self.asked:
            try:
                answers.append(self.redis.hget(key, field))
            except FakeRedis.WrongType as e:
                if raise_on_error:
                    raise
                answers.append(e)
        return answers


class Clock:
    """Time that moves only when something sleeps: a seed that waits for a platform that never answers ends at once."""

    def __init__(self):
        self.sleeps, self.elapsed = [], 0.0
        self._time, self._monotonic = time.time, time.monotonic

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def time(self):
        return self._time() + self.elapsed

    def monotonic(self):
        return self._monotonic() + self.elapsed


class Seed(unittest.TestCase):
    """A seed against the fake platform, with the settings of SETTINGS, its state in a temporary file, an empty seed
    folder, no sleeping, and its output kept."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "created.json"
        self.platform, self.clock, self.stdout = Platform({}), Clock(), io.StringIO()
        patches = [mock.patch.object(common, "STATE_FILE", self.state),
                   mock.patch.object(common, "SEED_DIR", Path(folder.name) / "seed"),
                   mock.patch.object(requests, "Session", self.platform.session),
                   mock.patch.object(requests.sessions, "Session", self.platform.session),
                   mock.patch.dict(os.environ, SETTINGS, clear=True), mock.patch("sys.stdout", self.stdout)]
        patches += [mock.patch.object(time, name, getattr(self.clock, name)) for name in ("sleep", "time", "monotonic")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        controls.reset()
        refills.reset()
        common.configure(1)   # in order, with no earlier failure: a test that runs steps together asks for `workers` (below)
        self.addCleanup(common.configure, 1)

    def workers(self, count):
        """Lets `count` requests be in flight at once, as SEED_WORKERS does."""
        common.configure(count)
        os.environ["SEED_WORKERS"] = str(count)

    def answer(self, routes, created=()):
        """Adds routes to the platform, and sets what the seed had already created."""
        self.platform.add(routes)
        common.save_created([{"label": "", **x} for x in created])

    def seed_files(self, files):
        """Writes seed files: {"aim/providers/ollama.yaml": text, ...}."""
        for name, text in files.items():
            path = common.SEED_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def env(self, **values):
        """Sets settings for this test; None unsets one."""
        patch = mock.patch.dict(os.environ, {name: value for name, value in values.items() if value is not None})
        patch.start()
        self.addCleanup(patch.stop)
        for name in [name for name, value in values.items() if value is None]:
            os.environ.pop(name, None)

    def output(self):
        return self.stdout.getvalue()

    def main(self, *arguments):
        """seed.main() as the container runs it, with these arguments ("--remove"); its exit code."""
        with mock.patch("sys.argv", ["seed.py", *arguments]):
            return seed.main()


# --- The playgrounds' seed services and the checks of their seed files -----------------------
# seedlint.py holds them, and what they answer is what the command answers

TEXT, LITERAL_URL, PUBLIC = seedlint.TEXT, seedlint.LITERAL_URL, seedlint.PUBLIC
as_mapping, compose_value = seedlint.as_mapping, seedlint.compose_value
seed_service, seed_environment = seedlint.seed_service, seedlint.seed_environment
seed_paths, provided_to = seedlint.seed_paths, seedlint.provided_to
unset_variables, literal_addresses = seedlint.unset_variables, seedlint.literal_addresses
escaped_addresses = seedlint.escaped_addresses


def seeded_playgrounds(root=None):
    """(folder, environment of its seed service) of each shared playground of `root` (PLAYGROUNDS) that has a seed
    folder and a seed service."""
    return seedlint.seeded_playgrounds(PLAYGROUNDS if root is None else root)
