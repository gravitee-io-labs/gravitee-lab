"""repo-settings: the repository standard of a catalog on GitHub, audited and applied.

    audit(repo, runner, options) -> Findings          one Finding(id, status, title, now, fix) for each id of the table
    apply(repo, runner, findings, yes) -> [Applied]   the requests that bring the repository to the standard; none is made
                                                      without `yes`, and none that is not in ALLOWED
    main(argv) -> int                                 the command line `catalog.py repo-settings`

GitHub is reached only through `runner(method, path, body=None) -> Reply(code, body)`. The real one is `Gh`, which runs the
`gh` command of the maintainer: their own sign-in, with no token read, asked for or stored here. A test hands in a table of
recorded replies. Reads are GET requests. The writes are PATCH, PUT and POST of the paths in ALLOWED, so a request that removes
something, or touches a secret, a variable, the visibility or a collaborator, is not one this module can make.

The rulesets come from the Lab's template (RULESETS: the managed files of `.github/rulesets/`), never from the repository,
because a pull request can edit the repository's copy. The `_managed` marker is taken off before a ruleset is sent, and a
ruleset is matched by name and replaced, never added twice.

A finding is `ok`, `fail`, `warn`, `n.a.` (the plan cannot give it: GitHub says so in words, or says so for the
plan) or `manual` (a person has to look). A reply is trusted for nothing: each value is type checked, and the text of a reply
that is shown is cut and stripped of control characters. Python 3.9, standard library only."""

import argparse
import base64
import binascii
import copy
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import quote

ACTIONS_APP_ID = 15368            # the integration id of GitHub Actions: only a workflow can pass the required check
REQUIRED_CHECK = "Catalog CI result"
TOPIC = "gravitee-lab-catalog"
LAB_SECRET = "LAB_DEPLOY_KEY"
NEEDS_SECRET = "NEEDS_DEPLOY_KEY"   # the read key of the catalogs this one requires, when it requires any
API_VERSION = "2022-11-28"
HOST = "github.com"
ADMIN_ROLE = 5                    # the repository role id of an administrator in the bypass list of a ruleset
TIMEOUT = 30
REPLY_LIMIT = 16 * 1024 * 1024
STANDARD_LIMIT = 4096             # the size SD04 allows `.github/standard.json`
DOCKER_PATTERNS = ("docker/*",)

# The Lab's own copy of the rulesets: the managed files of the template, applied as they are
RULESETS = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "managed" / ".github" / "rulesets"
PUSH_RULE_TYPES = ("file_path_restriction", "file_extension_restriction", "max_file_size", "max_file_path_length")
RULESET_FILES = {"main": "default-branch.json", "tags": "tags.json", "push": "push.json", "paths": "protected-paths.json"}
RULESET_LIMIT = 64 * 1024

OK, FAIL, WARN, NA, MANUAL = "ok", "fail", "warn", "n.a.", "manual"
STATUSES = (FAIL, WARN, OK, NA, MANUAL)
READS = ("GET",)
WRITES = ("PATCH", "POST", "PUT")

REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
OWNER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
BRANCH_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,199}")
TOPIC_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,49}")
PATTERN_TEXT = re.compile(r"[A-Za-z0-9._/@*+-]{1,200}")       # a pattern of the list of actions: one, with no list in it
ALLOWED_ACTIONS = ("all", "local_only", "selected")
# The path of a request: one of the roots this module reads or writes, no full address, no `..`
PATH_TEXT = re.compile(r"(?:repos|orgs|users|user)(?:/[A-Za-z0-9/._~:%?&=,+-]*|\?[A-Za-z0-9/._~:%&=,+-]*)?")

# The words with which GitHub says that the plan cannot give a feature. No other refusal is `n.a.`
PLAN_WORDS = ("Upgrade to GitHub", "make this repository public", "not allowed for private repositories")
NO_RULES = "needs GitHub Pro, an organization or a public repository: rules cannot be enforced here"

# The settings of `repo.merge`
MERGE = {"allow_squash_merge": True, "allow_merge_commit": False, "allow_rebase_merge": False,
         "delete_branch_on_merge": True, "allow_auto_merge": False, "allow_update_branch": True, "has_wiki": False,
         "squash_merge_commit_title": "PR_TITLE", "squash_merge_commit_message": "BLANK"}

TITLES = {
    "repo.default_branch": "The default branch is main",
    "repo.merge": "Squash merges only, with the pull request title and no body; the branch goes with the merge; "
                  "no auto-merge; no wiki",
    "repo.topic": "The repository has the topic " + TOPIC,
    "repo.forking": "Nobody forks a private repository",
    "repo.immutable_releases": "A published release cannot change",
    "actions.pinning": "Every action is pinned to a full commit, or the run does not start",
    "actions.listed": "Only listed actions run",
    "actions.token": "The token reads by default and cannot approve a pull request",
    "actions.fork": "A fork's pull request gets no write token and no secret",
    "actions.access": "Other repositories cannot call this repository's workflows",
    "actions.retention": "Logs and artifacts are kept 30 days at most",
    "actions.secrets": "No secret but " + LAB_SECRET,
    "actions.org_secrets": "Organization secrets and variables scoped to this repository",
    "actions.runners": "No self-hosted runner can run this repository's workflows",
    "actions.oidc": "Cloud trust policies pin the workflow or an environment",
    "actions.nightly": "The nightly workflow is active",
    "lab.key": "The Lab's deploy key exists and is read only",
    "environments.policy": "Publishing runs from main and from release tags only",
    "packages.actions_access": "Only the publish environment can write the package",
    "rules.deletion": "The default branch cannot be deleted",
    "rules.non_fast_forward": "No force push to the default branch",
    "rules.required_linear_history": "The default branch keeps a linear history",
    "rules.required_signatures": "Commits on the default branch are signed",
    "rules.pull_request": "A pull request, an approval of a code owner after the last push, squash only",
    "rules.checks": "The check " + REQUIRED_CHECK + ", run by GitHub Actions, passes first, on an up to date branch",
    "rules.bypass": "Nobody bypasses the rules",
    "rules.tags": "Only an administrator creates, moves or deletes a tag",
    "rules.push": "Machine files never enter",
    "rules.protected_paths": "Only an administrator changes the files that run with a token",
    "security.scanning": "Secret scanning and push protection",
    "security.dependabot": "Dependabot alerts and security updates",
    "security.reporting": "Private vulnerability reporting",
    "files.codeowners": "CODEOWNERS is valid",
    "people.write": "Who can push a branch, and so publish an image",
}
IDS = tuple(TITLES)
# What `--effective-only` reads: what a read-only token can see, the effective rules of the default branch and the files
MAIN_RULES = ("rules.deletion", "rules.non_fast_forward", "rules.required_linear_history", "rules.required_signatures")
EFFECTIVE_RULES = MAIN_RULES + ("rules.pull_request", "rules.checks")
ALL_RULES = tuple(i for i in IDS if i.startswith("rules."))

# How a live value is compared with the template's, which holds the strict value of each key. Not in the table: it must be
# equal (a flag is the strict flag, whichever way)
COMPARE = {("pull_request", "required_approving_review_count"): "min",
           ("pull_request", "allowed_merge_methods"): "subset",
           ("file_path_restriction", "restricted_file_paths"): "superset",
           ("file_extension_restriction", "restricted_file_extensions"): "superset",
           ("max_file_size", "max_file_size"): "max"}

# The environments the standard knows, and the branches and tags each may deploy from, as (name, type)
ENVIRONMENTS = {"publish": (("main", "branch"), ("v*", "tag")),
                "e2e": (("main", "branch"),),
                "e2e-nightly": (("main", "branch"),)}
PUBLISH_POLICY = {"protected_branches": False, "custom_branch_policies": True}

SOLO_BYPASS = {"actor_id": ADMIN_ROLE, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
SOLO_TUPLE = ("RepositoryRole", ADMIN_ROLE, "pull_request")
ADMINS = (("RepositoryRole", ADMIN_ROLE),)       # (actor type, actor id) of who may bypass a tag or a protected path
HIDDEN_BYPASS = "bypass_actors are returned to administrators only"
BYPASS_OTHERS = "bypass for someone but an administrator"
BYPASS_ANYONE = "bypass for someone"

# The only writes this module makes: (method, path after `repos/OWNER/REPO`). Nothing is removed, and nothing that holds a
# secret, a variable, the visibility or a person is touched. {id} stands for digits, {file} for a workflow file, and
# {environment} for an environment of the standard
ALLOWED = (
    ("PATCH", ""), ("PUT", "/topics"),
    ("PUT", "/actions/permissions"), ("PUT", "/actions/permissions/selected-actions"),
    ("PUT", "/actions/permissions/workflow"), ("PUT", "/actions/permissions/artifact-and-log-retention"),
    ("PUT", "/actions/permissions/access"), ("PUT", "/actions/permissions/fork-pr-workflows-private-repos"),
    ("PUT", "/actions/permissions/fork-pr-contributor-approval"), ("PUT", "/actions/workflows/{file}/enable"),
    ("PUT", "/vulnerability-alerts"), ("PUT", "/automated-security-fixes"),
    ("PUT", "/private-vulnerability-reporting"), ("PUT", "/immutable-releases"),
    ("PUT", "/environments/{environment}"), ("POST", "/environments/{environment}/deployment-branch-policies"),
    ("POST", "/rulesets"), ("PUT", "/rulesets/{id}"),
)
# The keys a PATCH of the repository may carry
REPO_KEYS = frozenset(MERGE) | {"allow_forking", "security_and_analysis"}
ANALYSIS_KEYS = ("secret_scanning", "secret_scanning_push_protection")


class Reply(NamedTuple):
    """What GitHub answered: its HTTP status (0 when it did not answer) and the JSON body (None when there is none)."""
    code: int
    body: Any


class Finding(NamedTuple):
    """One line of the audit. `fix` is None, words for a person (a str), or the requests that fix it (a list of dicts
    `{"method", "path", "body"}`)."""
    id: str
    status: str
    title: str
    now: str
    fix: Any


class Findings(list):
    """The findings of one audit, in the order of the table of 7.5. `repo` is the repository, `about` how it is described
    (`private, user-owned`), `level` the standard it is audited against (None when the Lab has no template here),
    `inapplicable` the ids whose `n.a.` says the rule does not apply to this repository, which is not a plan that falls short
    of the standard, and `optional` the ids whose `warn` is about something the plan sells and the standard does not ask
    for there (secret scanning on a private repository): `--strict` leaves both alone."""

    def __init__(self, *args):
        super().__init__(*args)
        self.repo, self.about, self.level, self.inapplicable, self.optional = "", "", None, set(), set()


class Applied(NamedTuple):
    """One request of `apply` and what became of it: `planned` (a dry run), `done`, `refused` (GitHub said no) or
    `skipped` (not sent: `detail` says why)."""
    method: str
    path: str
    body: Any
    outcome: str
    detail: str


class Standard(NamedTuple):
    """What `.github/standard.json` says that the audit needs. `error` is empty when the file was read."""
    level: Optional[int]
    form: Optional[str]
    lab: Optional[str]
    error: str
    needs: Tuple[str, ...] = ()   # the catalogs it requires (`needs` of standard.json): each is read with NEEDS_SECRET


class Unusable(Exception):
    """Something that is not a rule: no `gh`, no sign-in, not GitHub, a repository that cannot be read. One line."""


class Refused(Unusable):
    """One repository that cannot be read: not found, or no right to see it. The others can still be audited."""


class Usage(Unusable):
    """A command line that makes no sense."""


# ---------------------------------------------------------------- the transport

class Gh:
    """GitHub through `gh api`: an argument list, no shell, 30 seconds for a request. The sign-in is the maintainer's own,
    the one `gh` holds, and the request goes to github.com whatever `GH_HOST` says. Only the methods of READS and WRITES
    are sent. `run` is subprocess.run; a test replaces it."""

    def __init__(self, run: Callable = subprocess.run, timeout: float = TIMEOUT):
        self.run, self.timeout = run, timeout

    def __call__(self, method: str, path: str, body: Any = None) -> Reply:
        if method not in READS + WRITES:
            raise ValueError(f"{method} is not a request repo-settings makes")
        if not PATH_TEXT.fullmatch(path) or ".." in path or "//" in path:
            raise ValueError("the path of a request is not a GitHub path")
        argv = ["gh", "api", "--hostname", HOST, "--method", method, "-H", "Accept: application/vnd.github+json",
                "-H", f"X-GitHub-Api-Version: {API_VERSION}"]
        data = None
        if body is not None:
            argv += ["-H", "Content-Type: application/json", "--input", "-"]
            data = json.dumps(body)
        argv.append(path)
        feed = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
        try:
            done = self.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=self.timeout, **feed)
        except FileNotFoundError:
            raise Unusable("The GitHub command-line tool gh is not installed. Install it from https://cli.github.com "
                           "and run gh auth login.")
        except subprocess.TimeoutExpired:
            return Reply(0, {"message": f"GitHub did not answer within {int(self.timeout)} s"})
        except OSError as error:
            raise Unusable("The GitHub command-line tool gh could not run: "
                           f"{clean(error.strerror or type(error).__name__)}.")
        out, err = (done.stdout or "").strip(), (done.stderr or "").strip()
        if len(out) > REPLY_LIMIT:
            return Reply(0, {"message": "GitHub answered with more than 16 MB"})
        try:
            parsed = json.loads(out) if out else None
        except (ValueError, RecursionError):          # not JSON, or nested deeper than Python reads
            parsed = {"message": out[:300]}
        if done.returncode == 0:
            return Reply(200, parsed)
        found = re.search(r"HTTP (\d{3})", err)
        code = int(found.group(1)) if found else 0
        if not code and isinstance(parsed, dict) and str(parsed.get("status", "")).isdigit():
            code = int(parsed["status"])
        if "gh auth login" in err or code == 401:
            raise Unusable("Not signed in to GitHub. Run gh auth login, then run this again.")
        return Reply(code, parsed if parsed is not None else {"message": err.splitlines()[0][:300] if err else ""})


def as_reply(value) -> Reply:
    if isinstance(value, Reply):
        return value
    if isinstance(value, (tuple, list)) and len(value) == 2 and isinstance(value[0], int):
        return Reply(value[0], value[1])
    raise TypeError("a runner answers a Reply(code, body)")


class Reads:
    """The GET requests of one audit, each asked once."""

    def __init__(self, runner: Callable):
        self.runner, self.cache = runner, {}

    def get(self, path: str) -> Reply:
        if path not in self.cache:
            self.cache[path] = as_reply(self.runner("GET", path))
        return self.cache[path]

    def pages(self, path: str, key: Optional[str] = None, limit: int = 5) -> Tuple[Reply, list]:
        """(the reply that failed, or Reply(200, None); the items): a list read 100 at a time, `limit` pages at most.
        `key` names the list inside the body when the body is an object."""
        items = []
        joiner = "&" if "?" in path else "?"
        for number in range(1, limit + 1):
            reply = self.get(f"{path}{joiner}per_page=100" + ("" if number == 1 else f"&page={number}"))
            if reply.code != 200:
                return reply, items
            chunk = reply.body if key is None else as_dict(reply.body).get(key)
            chunk = chunk if isinstance(chunk, list) else []
            items += chunk
            if len(chunk) < 100:
                break
        return Reply(200, None), items


# ---------------------------------------------------------------- small helpers

def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    return value if isinstance(value, list) else []


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def names_of(items) -> List[str]:
    return [item["name"] for item in map(as_dict, items) if isinstance(item.get("name"), str)]


def clean(value, limit: int = 200) -> str:
    """Text from GitHub that is going to be shown: control characters and line breaks become a space, and a long text is
    cut. The name of a person, a secret or a ruleset is whatever a writer chose it to be."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    cut = len(text) > limit
    text = "".join(ch if ch.isprintable() else " " for ch in text[:limit])
    return text[:limit - 3] + "..." if cut else text


def shows(live, keys) -> str:
    live = as_dict(live)
    return ", ".join(f"{key}={clean(json.dumps(live.get(key)))}" for key in keys)


def message(reply: Reply) -> str:
    body = reply.body
    text = body.get("message") if isinstance(body, dict) else body if isinstance(body, str) else ""
    return clean(text if isinstance(text, str) and text else "no message", 160)


def refusal(reply: Reply) -> str:
    return f"HTTP {reply.code}: {message(reply)}" if reply.code else f"no answer: {message(reply)}"


def plan_limit(reply: Reply) -> bool:
    """Whether a refusal is the plan's, in the words GitHub uses for it, and not a missing right or a missing page."""
    text = as_dict(reply.body).get("message")
    return reply.code in (403, 422) and isinstance(text, str) and any(words in text for words in PLAN_WORDS)


def request(method: str, path: str, body: Any = None, **more) -> dict:
    return {"method": method, "path": path, "body": body, **more}


# ---------------------------------------------------------------- the template

def _in_githubs_form(body: dict) -> None:
    """GitHub refuses a ruleset whose restricted file extensions do not start with `*.` (a 422, and the push ruleset never exists),
    and lists them that way. The template says `.pem` as a person does: it is asked for, and compared, as `*.pem`."""
    for rule in body["rules"]:
        params = rule.get("parameters") if isinstance(rule, dict) and rule.get("type") == "file_extension_restriction" else None
        if isinstance(params, dict) and isinstance(params.get("restricted_file_extensions"), list):
            params["restricted_file_extensions"] = ["*" + each if isinstance(each, str) and each.startswith(".") else each
                                                    for each in params["restricted_file_extensions"]]


def load_rulesets(folder: Optional[Path] = None) -> Tuple[Dict[str, dict], str]:
    """({key: ruleset}, "") from the Lab's template with `_managed` taken off, or ({}, why not). Only the four files of the
    template are read, and a link that leads out of the folder is not followed."""
    folder = Path(RULESETS if folder is None else folder)
    found = {}
    try:
        root = os.path.realpath(folder)
        for key, name in RULESET_FILES.items():
            real = os.path.realpath(folder / name)
            if os.path.dirname(real) != root:
                return {}, f"{name} is not in the template's folder"
            with open(real, "rb") as handle:
                raw = handle.read(RULESET_LIMIT + 1)
            if len(raw) > RULESET_LIMIT:
                return {}, f"{name} is over {RULESET_LIMIT // 1024} KB"
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict) or not isinstance(body.get("name"), str) or \
                    body.get("target") not in ("branch", "tag", "push") or not isinstance(body.get("rules"), list):
                return {}, f"{name} is not a ruleset"
            body.pop("_managed", None)
            _in_githubs_form(body)
            found[key] = body
    except (OSError, ValueError) as error:
        return {}, f"the rulesets of the template could not be read ({type(error).__name__})"
    return found, ""


def template_level(folder: Optional[Path] = None) -> Optional[int]:
    """The standard that the rulesets of the template say they are managed by (the number in their `_managed` line)."""
    try:
        with open(Path(RULESETS if folder is None else folder) / RULESET_FILES["main"], "rb") as handle:
            body = json.loads(handle.read(RULESET_LIMIT).decode("utf-8"))
    except (OSError, ValueError):
        return None
    found = re.search(r"standard (\d+)", str(as_dict(body).get("_managed")))
    return int(found.group(1)) if found else None


def parse_standard(data) -> Standard:
    """The facts of a `standard.json` that the audit reads, each checked. The rest of the file is not looked at."""
    if not isinstance(data, dict):
        return Standard(None, None, None, "it is not a JSON object")
    level, form, lab = data.get("standard"), data.get("form"), as_dict(data.get("lab")).get("repository")
    return Standard(level if is_int(level) and level >= 1 else None, form if form in ("inline", "reusable") else None,
                    lab if isinstance(lab, str) and REPO_NAME.fullmatch(lab) and ".." not in lab else None, "",
                    tuple(sorted(str(name) for name in as_dict(data.get("needs")))))


# The names of every tag. GitHub matches a pattern with fnmatch and its pathname flag, where `*` and a bare `**` stop at a
# slash, so `refs/tags/*` leaves out a tag such as `v1/x`: only `~ALL` and the recursive form reach them all
EVERY_TAG = ("~ALL", "refs/tags/**/*")


def reaches_every_tag(conditions) -> bool:
    """Whether the conditions of a tag ruleset name every tag and leave none out."""
    names = as_dict(as_dict(conditions).get("ref_name"))
    return not as_list(names.get("exclude")) and any(name in EVERY_TAG for name in as_list(names.get("include")))


def outsiders(details, allowed=()) -> Optional[list]:
    """The actors of the bypass list of a ruleset's details who are not one of `allowed` (pairs of actor type and id): an
    empty list when there are none, None when the reply does not tell (a reader who is not an administrator is not sent the
    list, and a list that is not a list is not one to trust)."""
    listed = as_dict(details).get("bypass_actors")
    if not isinstance(listed, list):
        return None
    return [actor for actor in listed if (as_dict(actor).get("actor_type"), as_dict(actor).get("actor_id")) not in allowed]


def within(pattern, allowed) -> bool:
    """Whether a pattern of the list of actions allows nothing that `allowed` (patterns such as `docker/*`) does not: the
    pattern itself, or a narrower one under the same owner (`docker/login-action@*`, a repository at a commit)."""
    if not isinstance(pattern, str) or not PATTERN_TEXT.fullmatch(pattern) or ".." in pattern:
        return False
    return pattern in allowed or any(entry.endswith("/*") and pattern.startswith(entry[:-1]) and len(pattern) > len(entry) - 1
                                     for entry in allowed)


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True)


def plain(value):
    """The value when it can be put in a set, else its JSON: a reply may put a list where a name goes."""
    return value if value is None or isinstance(value, (str, int, float, bool)) else _canon(value)


def _unmet(kind: str, want: dict, instances: List[dict]) -> List[str]:
    """What of `want` the live parameters of a rule do not meet, as `key=value`. A rule can be in several rulesets and all
    of them apply, so the strictest value of each key counts."""
    out = []
    for key, strict in sorted(want.items()):
        values = [item[key] for item in instances if key in item]
        how = COMPARE.get((kind, key), "same")
        numbers = [value for value in values if is_int(value)]
        if not values:
            out.append(f"{key}=null")
        elif how == "min":
            if not numbers or max(numbers) < strict:
                out.append(f"{key}={clean(json.dumps(max(numbers) if numbers else values[0]))}")
        elif how == "max":
            if not numbers or min(numbers) > strict:
                out.append(f"{key}={clean(json.dumps(min(numbers) if numbers else values[0]))}")
        elif how in ("subset", "superset"):
            sets = [{_canon(item) for item in value} for value in values if isinstance(value, list)]
            wanted = {_canon(item) for item in as_list(strict)}
            if how == "subset":
                combined = set.intersection(*sets) if sets else set()
                met = bool(sets) and combined <= wanted
            else:
                combined = set.union(*sets) if sets else set()
                met = bool(sets) and wanted <= combined
            if not met:
                out.append(f"{key}=" + clean("[" + ", ".join(sorted(combined)) + "]" if sets else "null"))
        elif not any(value == strict and type(value) is type(strict) for value in values):
            out.append(f"{key}={clean(json.dumps(values[0]))}")
    return out


# ---------------------------------------------------------------- the audit

class _Audit:
    """One audit: the replies read, the facts derived from them and the findings made, in the order of the sections."""

    def __init__(self, repo: str, runner: Callable, options: dict):
        self.repo, self.options = repo, options
        self.owner, self.name = repo.split("/", 1)
        self.reads = Reads(runner)
        self.found = Findings()
        self.solo = options["solo"]
        self.effective_only = options["effective_only"]
        self.no_rulesets = False           # GitHub said the plan has none
        self.protected_paths_ok = False
        self.secret_state = ""
        self.listing_reply = Reply(200, None)
        self._templates = None
        self._listing = None
        self._standard = False

    # --- the facts

    def add(self, id, status, title=None, now="", fix=None, applies=True, optional=False):
        self.found.append(Finding(id, status, TITLES[id] if title is None else title, clean(now, 400), fix))
        if not applies:
            self.found.inapplicable.add(id)
        if optional:
            self.found.optional.add(id)

    def unreadable(self, id, reply):
        self.add(id, MANUAL, now=refusal(reply))

    def refused_or(self, id, reply, why=None):
        """The finding for a reply that is not the answer: `n.a.` when it is the plan's words, else a thing to look at."""
        if plan_limit(reply):
            self.add(id, NA, now=why or message(reply))
        else:
            self.unreadable(id, reply)

    def start(self):
        info = self.reads.get(f"repos/{self.repo}")
        if info.code != 200 or not isinstance(info.body, dict):
            raise Refused(f"{self.repo} was not found, or you cannot read it ({refusal(info)}).")
        self.meta = meta = info.body
        seen = meta.get("visibility")
        self.visibility = seen if seen in ("public", "private", "internal") else \
            ("public" if meta.get("private") is False else "private")
        self.org = as_dict(meta.get("owner")).get("type") == "Organization"
        self.personal = self.visibility == "private" and not self.org
        asked = self.options["as"]
        if asked in ("private", "internal") and self.visibility == "public":
            raise Usage(f"--as {asked} cannot describe a public repository.")
        self.profile = asked or self.visibility
        self.public = self.profile == "public"
        branch = meta.get("default_branch")
        self.default = branch if isinstance(branch, str) and BRANCH_NAME.fullmatch(branch) else None
        self.found.repo = self.repo
        self.found.level = template_level()
        self.found.about = f"{self.visibility}, {'organization' if self.org else 'user'}-owned" + \
            (f", audited as {self.profile}" if asked and asked != self.visibility else "")

    def standard(self) -> Optional[Standard]:
        """The standard of the repository: the one given in the options, else the one read from `.github/standard.json` on
        the default branch. None when the file is not there, which is a repository that is not a catalog yet."""
        if self._standard is False:
            given = self.options["standard"]
            self._standard = parse_standard(given) if given is not None else self.read_standard()
        return self._standard

    def read_standard(self) -> Optional[Standard]:
        if self.default is None:
            return None
        reply = self.reads.get(f"repos/{self.repo}/contents/.github/standard.json?ref={quote(self.default, safe='')}")
        if reply.code == 404:
            return None
        body = as_dict(reply.body)
        size = body.get("size", 0)
        if reply.code != 200 or body.get("encoding") != "base64" or not isinstance(body.get("content"), str) or \
                not is_int(size) or size > STANDARD_LIMIT or len(body["content"]) > 2 * STANDARD_LIMIT:
            return Standard(None, None, None, refusal(reply) if reply.code != 200 else "it cannot be read")
        try:
            return parse_standard(json.loads(base64.b64decode(body["content"]).decode("utf-8")))
        except (ValueError, binascii.Error, RecursionError):      # a contributor's file: brackets nested deeper than Python reads
            return Standard(None, None, None, "it is not JSON")

    def templates(self) -> Tuple[Dict[str, dict], str]:
        if self._templates is None:
            self._templates = load_rulesets()
        return self._templates

    def ruleset(self, key: str, solo: Optional[bool] = None) -> Optional[dict]:
        """The template's ruleset `key` as it is applied here: `main` with the administrator bypass under --solo and with
        the checks the caller named."""
        body = copy.deepcopy(self.templates()[0].get(key))
        if body is None:
            return None
        if key == "main":
            if self.solo if solo is None else solo:
                body["bypass_actors"] = [dict(SOLO_BYPASS)]
            elif "bypass_actors" in body:
                body["bypass_actors"] = []
            if self.options["required_checks"]:
                for rule in body["rules"]:
                    if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                        rule.setdefault("parameters", {})["required_status_checks"] = [
                            {"context": name, "integration_id": ACTIONS_APP_ID} for name in self.options["required_checks"]]
        return body

    def ruleset_fix(self, key: str, solo: Optional[bool] = None):
        """The request that makes the template's ruleset `key` the one in force: the replacement of the ruleset of its name
        when there is one, else its creation (`apply` asks again before it sends). Words when the template cannot be read."""
        body = self.ruleset(key, solo)
        if body is None:
            return f"The Lab's rulesets are not here: {self.templates()[1] or 'update the Lab'}."
        for item in self.rulesets():
            if item["name"] == body["name"] and item["target"] == body["target"] and item["source"] == "Repository":
                return [request("PUT", f"repos/{self.repo}/rulesets/{item['id']}", body, ruleset=body["name"])]
        return [request("POST", f"repos/{self.repo}/rulesets", body, ruleset=body["name"])]

    def rulesets(self) -> list:
        """[{id, name, target, enforcement, source}] of the rulesets that apply to the repository, the organization's
        included. Empty when they cannot be listed (`listing_reply` says why)."""
        if self._listing is None:
            self.listing_reply, items = self.reads.pages(f"repos/{self.repo}/rulesets")
            self._listing = []
            for item in map(as_dict, items):
                if is_int(item.get("id")) and isinstance(item.get("name"), str) \
                        and item.get("target") in ("branch", "tag", "push"):
                    self._listing.append({"id": item["id"], "name": item["name"], "target": item["target"],
                                          "enforcement": item.get("enforcement"),
                                          "source": item.get("source_type") or "Repository"})
        return self._listing

    def detail(self, number: int) -> Reply:
        return self.reads.get(f"repos/{self.repo}/rulesets/{number}")

    # --- the order of the sections

    def run(self) -> Findings:
        self.start()
        if not self.effective_only:
            self.repo_settings()
            self.actions()
            self.lab_key()
            self.environments()
        self.rules()
        if not self.effective_only:
            self.security()
        self.codeowners()
        if not self.effective_only:
            self.people()
        order = {id: number for number, id in enumerate(IDS)}
        self.found.sort(key=lambda finding: order[finding.id])
        return self.found

    # --- repo.*

    def repo_settings(self):
        meta, repo = self.meta, self.repo
        branch = meta.get("default_branch")
        if branch == "main":
            self.add("repo.default_branch", OK)
        elif isinstance(branch, str):
            self.add("repo.default_branch", FAIL, now=shows(meta, ("default_branch",)),
                     fix=f"Rename it: gh api --method POST repos/{repo}/branches/{quote(branch, safe='')}/rename "
                         "-f new_name=main")
        else:
            self.add("repo.default_branch", MANUAL, now="GitHub did not say which branch it is")

        present = {key: meta[key] for key in MERGE if key in meta}
        differ = [key for key in MERGE if key in present and present[key] != MERGE[key]]
        if differ:
            self.add("repo.merge", FAIL, now=shows(meta, differ),
                     fix=[request("PATCH", f"repos/{repo}", {key: MERGE[key] for key in differ})])
        elif len(present) < len(MERGE):
            self.add("repo.merge", MANUAL, now="GitHub did not return " + ", ".join(k for k in MERGE if k not in present)
                     + ": write access is needed to read them")
        else:
            self.add("repo.merge", OK)

        topics = meta.get("topics")
        if not isinstance(topics, list):
            reply = self.reads.get(f"repos/{repo}/topics")
            topics = as_dict(reply.body).get("names") if reply.code == 200 else None
        if not isinstance(topics, list):
            self.add("repo.topic", MANUAL, now="the topics could not be read")
        elif TOPIC in topics:
            self.add("repo.topic", OK)
        else:
            wanted = sorted({name for name in topics if isinstance(name, str) and TOPIC_NAME.fullmatch(name)} | {TOPIC})
            self.add("repo.topic", FAIL, now="topics: " + (", ".join(clean(name, 40) for name in topics) or "none"),
                     fix=[request("PUT", f"repos/{repo}/topics", {"names": wanted})])

        allow = meta.get("allow_forking")
        if not isinstance(allow, bool):
            self.add("repo.forking", MANUAL, now="allow_forking is not returned: administration rights are needed to read it")
        elif self.public:
            title = "A public repository can be forked"
            if self.visibility == "public" or allow:
                self.add("repo.forking", OK, title)
            else:
                self.add("repo.forking", FAIL, title, now="allow_forking=false",
                         fix=f"Switch forking on at the go-public step: PATCH repos/{repo} allow_forking=true")
        elif allow and self.personal:   # GitHub answers 422 (measured): only an organization may turn it off on a private repository
            self.add("repo.forking", NA, now="the repository belongs to a user: GitHub lets only an organization turn forking off",
                     applies=False)
        elif allow:
            self.add("repo.forking", FAIL, now="allow_forking=true",
                     fix=[request("PATCH", f"repos/{repo}", {"allow_forking": False})])
        else:
            self.add("repo.forking", OK)

        reply = self.reads.get(f"repos/{repo}/immutable-releases")
        if reply.code != 200:
            self.refused_or("repo.immutable_releases", reply)
        elif as_dict(reply.body).get("enabled") is True:
            self.add("repo.immutable_releases", OK)
        else:
            self.add("repo.immutable_releases", WARN if self.personal else FAIL, now=shows(reply.body, ("enabled",)),
                     fix=[request("PUT", f"repos/{repo}/immutable-releases")])

    # --- actions.*

    def actions(self):
        self.pinning_and_list()
        self.token()
        self.fork()
        self.access()
        self.retention()
        self.secrets()
        self.org_secrets()
        self.runners()
        if self.org:
            self.add("actions.oidc", MANUAL, now="id-token: write can be requested by a workflow on any branch",
                     fix="Have the organization's cloud trust policies pin job_workflow_ref or an environment, "
                         "never repo:ORG/*")
        else:
            self.add("actions.oidc", NA, now="the repository belongs to a user", applies=False)
        self.nightly()

    def pinning_and_list(self):
        repo = self.repo
        perms = self.reads.get(f"repos/{repo}/actions/permissions")
        if perms.code != 200:
            self.refused_or("actions.pinning", perms)
            return self.refused_or("actions.listed", perms)
        body = as_dict(perms.body)
        have = body.get("allowed_actions")
        if body.get("sha_pinning_required") is True:
            self.add("actions.pinning", OK)
        else:
            # A PUT of these settings carries all of them, as they are now: it must not undo another
            self.add("actions.pinning", FAIL, now=shows(body, ("sha_pinning_required",)),
                     fix=[request("PUT", f"repos/{repo}/actions/permissions",
                                  {"enabled": True, "allowed_actions": have if have in ALLOWED_ACTIONS else "all",
                                   "sha_pinning_required": True})])
        self.list_of_actions(body)

    def list_of_actions(self, body):
        repo, standard = self.repo, self.standard()
        reusable = standard is not None and standard.form == "reusable"
        allowed = set(DOCKER_PATTERNS) | ({standard.lab + "/*"} if reusable and standard.lab else set())
        sel = None
        if body.get("allowed_actions") == "selected":
            sel = self.reads.get(f"repos/{repo}/actions/permissions/selected-actions")
            if sel.code != 200:
                return self.unreadable("actions.listed", sel)
        patterns_now = as_list(as_dict(sel.body).get("patterns_allowed")) if sel is not None else []
        good = sel is not None and body.get("enabled") is True and as_dict(sel.body).get("github_owned_allowed") is True \
            and as_dict(sel.body).get("verified_allowed") is not True \
            and all(within(pattern, allowed) for pattern in patterns_now)
        if good:
            return self.add("actions.listed", OK)
        now = shows(body, ("enabled", "allowed_actions")) + (
            ", " + shows(sel.body, ("github_owned_allowed", "verified_allowed", "patterns_allowed"))
            if sel is not None else "")
        if reusable and not standard.lab:
            return self.add("actions.listed", MANUAL, now="the standard says reusable but names no Lab repository, so the "
                                                         "list of actions cannot be made")
        patterns = sorted(allowed)
        if self.personal:
            # The pattern list may apply to public repositories only (V3): a list half in place is worse than none
            return self.add("actions.listed", WARN, now=now,
                            fix=f"Once the rehearsal shows that the plan enforces it: PUT repos/{repo}/actions/permissions "
                                "with "
                                f"allowed_actions=selected, then PUT repos/{repo}/actions/permissions/selected-actions with "
                                f"{', '.join(patterns)}")
        self.add("actions.listed", FAIL, now=now, fix=[
            request("PUT", f"repos/{repo}/actions/permissions",
                    {"enabled": True, "allowed_actions": "selected", "sha_pinning_required": True}, enables_list=True),
            request("PUT", f"repos/{repo}/actions/permissions/selected-actions",
                    {"github_owned_allowed": True, "verified_allowed": False, "patterns_allowed": patterns})])

    def token(self):
        reply = self.reads.get(f"repos/{self.repo}/actions/permissions/workflow")
        if reply.code != 200:
            return self.refused_or("actions.token", reply)
        want = {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}
        if all(as_dict(reply.body).get(key) == value for key, value in want.items()):
            return self.add("actions.token", OK)
        self.add("actions.token", FAIL, now=shows(reply.body, want),
                 fix=[request("PUT", f"repos/{self.repo}/actions/permissions/workflow", want)])

    def fork(self):
        repo = self.repo
        if self.public:
            title = "A first-time contributor's run waits for a maintainer"
            reply = self.reads.get(f"repos/{repo}/actions/permissions/fork-pr-contributor-approval")
            if plan_limit(reply):
                return self.add("actions.fork", NA, title, now="the approval policy of a public repository is read once the "
                                                               "repository is public")
            if reply.code != 200:
                return self.add("actions.fork", MANUAL, title, now=refusal(reply))
            if as_dict(reply.body).get("approval_policy") in ("first_time_contributors", "all_external_contributors"):
                return self.add("actions.fork", OK, title)
            return self.add("actions.fork", FAIL, title, now=shows(reply.body, ("approval_policy",)),
                            fix=[request("PUT", f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
                                         {"approval_policy": "first_time_contributors"})])
        reply = self.reads.get(f"repos/{repo}/actions/permissions/fork-pr-workflows-private-repos")
        if reply.code != 200:
            return self.refused_or("actions.fork", reply)
        body = as_dict(reply.body)
        run, write, secrets, approval = ("run_workflows_from_fork_pull_requests", "send_write_tokens_to_workflows",
                                         "send_secrets_and_variables", "require_approval_for_fork_pr_workflows")
        missing = [key for key in (run, write, secrets, approval) if not isinstance(body.get(key), bool)]
        if missing:
            return self.add("actions.fork", MANUAL, now="GitHub did not return " + ", ".join(missing))
        # Forks may run, with no write token and no secret and after approval; or they do not run at all
        if not body[write] and not body[secrets] and (not body[run] or body[approval]):
            return self.add("actions.fork", OK)
        self.add("actions.fork", FAIL, now=shows(body, (run, write, secrets, approval)),
                 fix=[request("PUT", f"repos/{repo}/actions/permissions/fork-pr-workflows-private-repos",
                              {run: body[run], write: False, secrets: False, approval: True})])

    def access(self):
        if self.public:
            return
        reply = self.reads.get(f"repos/{self.repo}/actions/permissions/access")
        if reply.code != 200:
            return self.refused_or("actions.access", reply)
        if as_dict(reply.body).get("access_level") == "none":
            return self.add("actions.access", OK)
        self.add("actions.access", WARN, now=shows(reply.body, ("access_level",)),
                 fix=[request("PUT", f"repos/{self.repo}/actions/permissions/access", {"access_level": "none"})])

    def retention(self):
        reply = self.reads.get(f"repos/{self.repo}/actions/permissions/artifact-and-log-retention")
        if reply.code != 200:
            return self.refused_or("actions.retention", reply)
        days = as_dict(reply.body).get("days")
        if is_int(days) and days <= 30:
            return self.add("actions.retention", OK)
        self.add("actions.retention", WARN, now=shows(reply.body, ("days",)),
                 fix=[request("PUT", f"repos/{self.repo}/actions/permissions/artifact-and-log-retention", {"days": 30})])

    def lab_is_public(self) -> Optional[bool]:
        """Whether the Lab is public, asked of GitHub as the maintainer: None when it cannot be seen."""
        standard = self.standard()
        if standard is None or not standard.lab:
            return None
        reply = self.reads.get(f"repos/{standard.lab}")
        private = as_dict(reply.body).get("private")
        return (not private) if reply.code == 200 and isinstance(private, bool) else None

    def secrets(self):
        repo = self.repo
        stores = {}
        for label, path in (("Actions", f"repos/{repo}/actions/secrets"), ("Dependabot", f"repos/{repo}/dependabot/secrets")):
            reply, items = self.reads.pages(path, "secrets")
            if reply.code != 200:
                return self.unreadable("actions.secrets", reply)
            stores[label] = set(names_of(items))
        none = self.public or self.lab_is_public() is True
        title = "No secret" if none else TITLES["actions.secrets"]
        have = [label for label, names in stores.items() if LAB_SECRET in names]
        where = f"the {' and '.join(have)} store" + ("s" if len(have) > 1 else "")
        problems = []
        if none and have:
            problems.append(f"{LAB_SECRET} is in {where}" + ("" if self.public else ", and the Lab is public"))
        elif len(have) == 1:
            other = "Dependabot" if have[0] == "Actions" else "Actions"
            problems.append(f"{LAB_SECRET} is in {where} and not in the {other} store")
        standard = self.standard()
        wanted = {LAB_SECRET} | ({NEEDS_SECRET} if standard is not None and standard.needs else set())
        for label, names in stores.items():
            if NEEDS_SECRET in wanted and NEEDS_SECRET not in names:
                problems.append(f"{NEEDS_SECRET} is not in the {label} store: the catalogs it requires cannot be read")
            extra = sorted(names - wanted)
            if extra:
                problems.append(f"{label} also holds {', '.join(clean(name, 60) for name in extra)}")
        self.secret_state = f"{LAB_SECRET} is in {where}" if have else f"{LAB_SECRET} is in no store"
        if not problems:
            return self.add("actions.secrets", OK, title)
        fix = f"Remove it from both stores: gh secret delete {LAB_SECRET} --repo {repo}, and the same with --app dependabot" \
            if none and have else None
        self.add("actions.secrets", FAIL if self.public and have else WARN, title, now="; ".join(problems), fix=fix)

    def org_secrets(self):
        names = []
        for what, key in (("organization-secrets", "secrets"), ("organization-variables", "variables")):
            reply, items = self.reads.pages(f"repos/{self.repo}/actions/{what}", key)
            if reply.code == 422 and not self.org:
                return self.add("actions.org_secrets", NA, now="the repository belongs to a user", applies=False)
            if reply.code != 200:
                return self.unreadable("actions.org_secrets", reply)
            names += names_of(items)
        extra = sorted(set(names) - set(self.options["allow_org_secrets"]))
        if not extra:
            return self.add("actions.org_secrets", OK)
        self.add("actions.org_secrets", FAIL, now="also: " + ", ".join(clean(name, 60) for name in extra),
                 fix="Scope them to other repositories, or name them as the maintainers' own with --allow-org-secret NAME: "
                     "they reach a workflow on any branch")

    def runners(self):
        repo, setting = self.repo, "Runner groups: Selected repositories, never a catalog; Allow public repositories off"
        reply = self.reads.get(f"repos/{repo}/actions/runners")
        if reply.code != 200:
            return self.unreadable("actions.runners", reply)
        total = as_dict(reply.body).get("total_count")
        if not is_int(total):
            return self.add("actions.runners", MANUAL, now="GitHub did not say how many runners the repository has")
        if total > 0:
            return self.add("actions.runners", FAIL, now=f"{total} self-hosted runner(s) on the repository",
                            fix="Remove them in the repository's settings: a pull request's code can run on them")
        if not self.org:
            return self.add("actions.runners", OK)
        groups, items = self.reads.pages(f"orgs/{self.owner}/actions/runner-groups?visible_to_repository={quote(self.name)}",
                                         "runner_groups")
        if groups.code != 200:
            return self.add("actions.runners", MANUAL, now="the runner groups of the organization cannot be read: "
                            + refusal(groups), fix=setting)
        busy = []
        for item in map(as_dict, items):
            if not is_int(item.get("id")):
                continue
            inside = self.reads.get(f"orgs/{self.owner}/actions/runner-groups/{item['id']}/runners")
            count = as_dict(inside.body).get("total_count")
            if inside.code != 200 or not is_int(count):
                return self.add("actions.runners", MANUAL, now=refusal(inside), fix=setting)
            if count > 0:
                busy.append(clean(item.get("name") or item["id"], 60))
        if busy:
            return self.add("actions.runners", FAIL, now="self-hosted runners in " + ", ".join(busy), fix=setting)
        self.add("actions.runners", OK)

    def nightly(self):
        standard = self.standard()
        if standard is None:
            return self.add("actions.nightly", NA, now="there is no .github/standard.json: this is not a catalog yet",
                            applies=False)
        if standard.error or standard.level is None:
            return self.add("actions.nightly", MANUAL, now="the level of the standard cannot be read: "
                            + (standard.error or "it has no level"))
        if standard.level < 2:
            return self.add("actions.nightly", NA, now=f"standard {standard.level} has no nightly.yml", applies=False)
        reply, items = self.reads.pages(f"repos/{self.repo}/actions/workflows", "workflows")
        if reply.code != 200:
            return self.unreadable("actions.nightly", reply)
        for item in map(as_dict, items):
            if item.get("path") == ".github/workflows/nightly.yml":
                if item.get("state") == "active":
                    return self.add("actions.nightly", OK)
                return self.add("actions.nightly", FAIL, now=shows(item, ("state",)),
                                fix=[request("PUT", f"repos/{self.repo}/actions/workflows/nightly.yml/enable")])
        self.add("actions.nightly", FAIL, now="nightly.yml is not there",
                 fix=f"Bring the managed files to the Lab's level, the repository is on standard {standard.level}: "
                     "catalog.py upgrade")

    # --- lab.*, environments.*, packages.*

    def lab_key(self):
        standard = self.standard()
        lab = standard.lab if standard is not None and standard.lab else "OWNER/LAB"
        # The key reads the Lab: whether one is needed is the Lab's visibility, not this repository's
        if self.public or self.lab_is_public() is True:
            if self.secret_state.endswith("in no store"):
                return self.add("lab.key", NA, now="the Lab is public: no key reads it", applies=False)
            return self.add("lab.key", MANUAL, now=self.secret_state,
                            fix="A public Lab needs no key: remove LAB_DEPLOY_KEY from both stores and the deploy key "
                                "from the Lab repository")
        self.add("lab.key", MANUAL, now=self.secret_state, fix="\n".join([
            "d=$(mktemp -d)", f"ssh-keygen -t ed25519 -N '' -C \"lab-read-{self.repo}\" -f \"$d/key\"",
            f"gh repo deploy-key add \"$d/key.pub\" --repo {lab} --title \"{self.repo}\"",
            f"gh secret set {LAB_SECRET} --repo {self.repo} < \"$d/key\"",
            f"gh secret set {LAB_SECRET} --repo {self.repo} --app dependabot < \"$d/key\"", "rm -rf \"$d\""]))

    def environments(self):
        repo = self.repo
        if self.personal:
            self.add("environments.policy", NA, now="a personal private repository has no environments with a branch policy")
            return self.add("packages.actions_access", NA, now="a personal private repository has no environments with a "
                                                              "branch policy")
        reply, items = self.reads.pages(f"repos/{repo}/environments", "environments")
        if reply.code != 200:
            self.refused_or("environments.policy", reply)
        else:
            self.environment_policy({item["name"]: item for item in map(as_dict, items) if isinstance(item.get("name"), str)})
        self.add("packages.actions_access", MANUAL,
                 fix="Only for the hardened publish option: in the settings of the package, Manage Actions access, "
                     "remove this repository")

    def environment_policy(self, found: Dict[str, dict]):
        repo, problems, requests = self.repo, [], []
        for name, wanted in ENVIRONMENTS.items():
            item = found.get(name)
            if item is None:
                if name == "publish":
                    problems.append("publish does not exist")
                    requests.append(request("PUT", f"repos/{repo}/environments/publish",
                                            {"deployment_branch_policy": dict(PUBLISH_POLICY)}))
                    requests += [request("POST", f"repos/{repo}/environments/publish/deployment-branch-policies",
                                         {"name": policy, "type": kind}) for policy, kind in wanted]
                continue
            if as_dict(item.get("deployment_branch_policy")).get("custom_branch_policies") is not True:
                # Changing an environment that exists would send its reviewers with it: a person does it
                problems.append(f"{name} deploys from any branch; in its settings, allow only " +
                                ", ".join(f"{policy} ({kind})" for policy, kind in wanted))
                continue
            reply, policies = self.reads.pages(f"repos/{repo}/environments/{quote(name, safe='')}/deployment-branch-policies",
                                               "branch_policies")
            if reply.code != 200:
                problems.append(f"{name}: {refusal(reply)}")
                continue
            have = {(as_dict(p)["name"], plain(as_dict(p).get("type")) or "branch") for p in policies
                    if isinstance(as_dict(p).get("name"), str)}
            extra = sorted(have - set(wanted), key=repr)
            if extra:
                problems.append(f"{name} also deploys from " + ", ".join(clean(policy, 40) for policy, _ in extra)
                                + "; remove them in its settings")
            for policy, kind in wanted:
                if (policy, kind) not in have:
                    problems.append(f"{name} does not allow {policy}")
                    requests.append(request("POST", f"repos/{repo}/environments/{name}/deployment-branch-policies",
                                            {"name": policy, "type": kind}))
        if not problems:
            return self.add("environments.policy", OK)
        self.add("environments.policy", FAIL, now="; ".join(problems), fix=requests or None)

    # --- rules.*

    def rules(self):
        wanted = EFFECTIVE_RULES if self.effective_only else ALL_RULES
        branch = quote(self.default or "main", safe="")
        rules = self.reads.get(f"repos/{self.repo}/rules/branches/{branch}")
        if plan_limit(rules):
            self.no_rulesets = True
            for id in wanted:
                self.add(id, NA, now=NO_RULES)
            return
        if rules.code != 200 or not isinstance(rules.body, list):
            for id in wanted:
                self.unreadable(id, rules)
            return
        if self.reads.get(f"repos/{self.repo}/branches/{branch}").code == 404:
            for id in wanted:
                self.add(id, MANUAL, now=f"there is no commit on {self.default or 'the default branch'} yet",
                         fix="Push it first: the signature rule would refuse an unsigned first commit")
            return
        effective = [rule for rule in rules.body if isinstance(rule, dict) and isinstance(rule.get("type"), str)]
        by_type = {}
        for rule in effective:
            by_type.setdefault(rule["type"], []).append(as_dict(rule.get("parameters")))
        fix = None if self.effective_only else self.ruleset_fix("main")
        for id in MAIN_RULES:
            if id[len("rules."):] in by_type:
                self.add(id, OK)
            else:
                self.add(id, FAIL, now="missing", fix=fix)
        template = as_dict(self.templates()[0].get("main"))
        want = {rule["type"]: as_dict(rule.get("parameters")) for rule in as_list(template.get("rules"))
                if isinstance(rule, dict) and isinstance(rule.get("type"), str)}
        self.pull_request(by_type, want, fix)
        self.checks(by_type, want, fix)
        if not self.effective_only:
            self.bypass(effective)
            self.tags()
            self.push_rules()

    def pull_request(self, by_type, want, fix):
        if "pull_request" not in want:
            return self.add("rules.pull_request", MANUAL, now=self.templates()[1] or "the template has no pull_request rule")
        if "pull_request" not in by_type:
            return self.add("rules.pull_request", FAIL, now="missing", fix=fix)
        unmet = _unmet("pull_request", want["pull_request"], by_type["pull_request"])
        if unmet:
            return self.add("rules.pull_request", FAIL, now=", ".join(unmet), fix=fix)
        self.add("rules.pull_request", OK)

    def checks(self, by_type, want, fix):
        params = as_dict(want.get("required_status_checks"))
        if not params:
            return self.add("rules.checks", MANUAL,
                            now=self.templates()[1] or "the template has no required_status_checks rule")
        in_template = [c["context"] for c in map(as_dict, as_list(params.get("required_status_checks")))
                       if isinstance(c.get("context"), str)]
        names = self.options["required_checks"] or in_template
        # The rule's title names the checks it asks for: those given (--required-check) rather than the template's
        title = None if names == in_template else (
            f"The check{'s' if len(names) > 1 else ''} {' and '.join(clean(name, 100) for name in names)}, run by GitHub Actions, "
            f"pass{'' if len(names) > 1 else 'es'} first, on an up to date branch")
        instances = by_type.get("required_status_checks", [])
        if not instances:
            return self.add("rules.checks", FAIL, title, now="missing", fix=fix)
        bound = {(plain(as_dict(c).get("context")), plain(as_dict(c).get("integration_id"))) for item in instances
                 for c in as_list(item.get("required_status_checks"))}
        problems = []
        for name in names:
            if (name, ACTIONS_APP_ID) not in bound:
                problems.append(f"{clean(name, 100)} is required but any app can post it" if any(c == name for c, _ in bound)
                                else f"{clean(name, 100)} is not required")
        flags = {key: value for key, value in params.items() if key != "required_status_checks"}
        problems += _unmet("required_status_checks", flags, instances)
        if problems:
            return self.add("rules.checks", FAIL, title, now="; ".join(problems), fix=fix)
        self.add("rules.checks", OK, title)

    def bypass(self, effective):
        # GitHub lists the rules of push rulesets among those of the branch; who may push past them is no way to merge a pull
        # request, and the standard gives an administrator exactly that for the files that run with a token
        ids = sorted({rule["ruleset_id"] for rule in effective
                      if is_int(rule.get("ruleset_id")) and rule["type"] not in PUSH_RULE_TYPES})
        actors, hidden = [], ""
        for number in ids:
            reply = self.detail(number)
            body = as_dict(reply.body)
            if reply.code != 200 or "bypass_actors" not in body:
                hidden = hidden or (HIDDEN_BYPASS if reply.code == 200 else refusal(reply))
                continue
            actors += [tuple(plain(as_dict(a).get(key)) for key in ("actor_type", "actor_id", "bypass_mode"))
                       for a in as_list(body["bypass_actors"])]
        if hidden or not ids:
            return self.add("rules.bypass", MANUAL, now=hidden or "GitHub did not say which ruleset the rules come from")
        actors = sorted(set(actors), key=repr)
        if not actors:
            if self.solo:
                return self.add("rules.bypass", FAIL, now="--solo asks for one administrator, in pull request mode, and there "
                                                           "is none", fix=self.ruleset_fix("main", True))
            return self.add("rules.bypass", OK)
        if actors == [SOLO_TUPLE]:
            return self.add("rules.bypass", WARN, now="one administrator can merge their own pull request",
                            fix=None if self.solo else self.ruleset_fix("main", False))
        self.add("rules.bypass", FAIL, now="bypass: " + clean(actors), fix=self.ruleset_fix("main"))

    def tags(self):
        listing = self.rulesets()
        if not listing and self.listing_reply.code != 200:
            return self.refused_or("rules.tags", self.listing_reply, NO_RULES)
        need, active, narrow = {"creation", "deletion", "non_fast_forward"}, False, False
        # The rule types of the rulesets that reach every tag, by who may bypass them: an administrator at most (they bind
        # everyone else), anyone else too, or a list that the reply does not tell. A ruleset that anyone writes to is no
        # protection against a tag that starts the publishing workflow
        bound, loose, hidden = set(), set(), set()
        for item in listing:
            if item["target"] == "tag" and item["enforcement"] == "active":
                reply = self.detail(item["id"])
                if reply.code != 200:
                    return self.unreadable("rules.tags", reply)
                body = as_dict(reply.body)
                if not reaches_every_tag(body.get("conditions")):
                    narrow = True
                    continue
                active = True
                types = {r["type"] for r in map(as_dict, as_list(body.get("rules"))) if isinstance(r.get("type"), str)}
                odd = outsiders(body, ADMINS)
                (hidden if odd is None else loose if odd else bound).update(types)
        if need <= bound:
            return self.add("rules.tags", OK)
        if not need <= bound | loose | hidden:
            now = ("missing: " + ", ".join(sorted(need - bound - loose - hidden))) if active else \
                "the tag ruleset does not reach every tag" if narrow else "no active tag ruleset"
            return self.add("rules.tags", FAIL, now=now, fix=self.ruleset_fix("tags"))
        if need <= bound | hidden:
            return self.add("rules.tags", MANUAL, now=HIDDEN_BYPASS)
        self.add("rules.tags", FAIL, now=BYPASS_OTHERS, fix=self.ruleset_fix("tags"))

    def push_rules(self):
        for key, id in (("push", "rules.push"), ("paths", "rules.protected_paths")):
            if self.public:
                self.add(id, NA, now="push rules are not offered on a public repository", applies=False)
            elif not self.org:
                self.add(id, NA, now="push rules need an organization plan, Team or Enterprise")
            else:
                self.push_rule(key, id)

    def push_rule(self, key, id):
        template = self.templates()[0].get(key)
        if template is None:
            return self.add(id, MANUAL, now=self.templates()[1])
        listing = self.rulesets()
        if not listing and self.listing_reply.code != 200:
            return self.refused_or(id, self.listing_reply, NO_RULES)
        fix = self.ruleset_fix(key)
        item = next((i for i in listing if i["name"] == template["name"] and i["target"] == "push"), None)
        if item is None:
            return self.add(id, FAIL, now=f"no push ruleset named {clean(template['name'], 60)}", fix=fix)
        if item["enforcement"] != "active":
            return self.add(id, FAIL, now=f"enforcement={clean(item['enforcement'], 40)}", fix=fix)
        reply = self.detail(item["id"])
        if reply.code != 200:
            return self.unreadable(id, reply)
        live = [rule for rule in map(as_dict, as_list(as_dict(reply.body).get("rules")))]
        unmet = []
        for rule in map(as_dict, template["rules"]):
            instances = [as_dict(r.get("parameters")) for r in live if r.get("type") == rule.get("type")]
            if instances:
                unmet += _unmet(rule["type"], as_dict(rule.get("parameters")), instances)
            else:
                unmet.append(f"{rule.get('type')} missing")
        # Who may push past the rule: nobody for the machine files, an administrator for the files that run with a token
        odd = outsiders(reply.body, ADMINS if key == "paths" else ())
        if odd:
            unmet.append(BYPASS_OTHERS if key == "paths" else BYPASS_ANYONE)
        if unmet:
            return self.add(id, FAIL, now=", ".join(unmet), fix=fix)
        if odd is None:
            return self.add(id, MANUAL, now=HIDDEN_BYPASS)
        if key == "paths":
            self.protected_paths_ok = True
        self.add(id, OK)

    # --- security.*, files.*, people.*

    def security(self):
        repo, meta = self.repo, self.meta
        analysis = meta.get("security_and_analysis")
        hidden = "permissions" in meta and as_dict(meta.get("permissions")).get("admin") is not True
        if not isinstance(analysis, dict):
            if hidden:
                self.add("security.scanning", MANUAL, now="security_and_analysis is returned to administrators only")
            else:
                self.add("security.scanning", NA, now="needs a public repository or GitHub Secret Protection")
        elif all(as_dict(analysis.get(key)).get("status") == "enabled" for key in ANALYSIS_KEYS):
            self.add("security.scanning", OK)
        else:
            # On a private repository it is a paid add-on, also when the repository is audited as a public one: something
            # to do when it is bought and never sent without --warnings, not a failure. The standard asks for it on a
            # private or internal repository nowhere, so --strict leaves it alone unless the audit is the go-public
            # gate, which lists what only a public repository can have
            wanted = {"security_and_analysis": {key: {"status": "enabled"} for key in ANALYSIS_KEYS}}
            paid = self.visibility != "public"
            self.add("security.scanning", WARN if paid else FAIL,
                     now=", ".join(f"{key}={clean(as_dict(analysis.get(key)).get('status'), 20)}" for key in ANALYSIS_KEYS),
                     fix=[request("PATCH", f"repos/{repo}", wanted)], optional=paid and not self.public)
        alerts = self.reads.get(f"repos/{repo}/vulnerability-alerts")
        updates = self.reads.get(f"repos/{repo}/automated-security-fixes")
        problems, requests = [], []
        if alerts.code == 404:
            problems.append("alerts disabled")
            requests.append(request("PUT", f"repos/{repo}/vulnerability-alerts"))
        elif alerts.code != 200:
            return self.unreadable("security.dependabot", alerts)
        if updates.code == 404 or (updates.code == 200 and as_dict(updates.body).get("enabled") is not True):
            problems.append("security updates disabled")
            requests.append(request("PUT", f"repos/{repo}/automated-security-fixes"))
        elif updates.code != 200:
            return self.unreadable("security.dependabot", updates)
        if problems:
            self.add("security.dependabot", FAIL, now=", ".join(problems), fix=requests)
        else:
            self.add("security.dependabot", OK)
        if self.visibility != "public":
            return self.add("security.reporting", NA, now="public repositories only; SECURITY.md names the channel",
                            applies=self.public)
        reply = self.reads.get(f"repos/{repo}/private-vulnerability-reporting")
        if reply.code != 200:
            return self.unreadable("security.reporting", reply)
        if as_dict(reply.body).get("enabled") is True:
            return self.add("security.reporting", OK)
        self.add("security.reporting", FAIL, now="disabled",
                 fix=[request("PUT", f"repos/{repo}/private-vulnerability-reporting")])

    def codeowners(self):
        reply = self.reads.get(f"repos/{self.repo}/codeowners/errors")
        fix = "Add the two lines of the standard with catalog.py upgrade --adopt, and name the maintainers"
        if reply.code == 404:
            return self.add("files.codeowners", FAIL, now="no CODEOWNERS file", fix=fix)
        if reply.code != 200:
            return self.unreadable("files.codeowners", reply)
        errors = as_list(as_dict(reply.body).get("errors"))
        if errors:
            first = as_dict(errors[0])
            return self.add("files.codeowners", FAIL, fix=fix,
                            now=f"{len(errors)} error(s), the first at line {clean(first.get('line'), 10)}: "
                                f"{clean(first.get('message') or first.get('kind') or 'invalid', 120)}")
        if self.personal:
            return self.add("files.codeowners", NA, now="a private repository of a personal account may not ask for a review "
                                                        "from the file until the rehearsal shows that it does")
        self.add("files.codeowners", OK)

    def people(self):
        reply, items = self.reads.pages(f"repos/{self.repo}/collaborators?permission=push")
        if reply.code != 200:
            return self.unreadable("people.write", reply)
        logins = sorted({item["login"] for item in map(as_dict, items) if isinstance(item.get("login"), str)
                         and as_dict(item.get("permissions")).get("push", True) is not False})
        shown = ", ".join(clean(login, 40) for login in logins[:20]) + \
            (f" and {len(logins) - 20} more" if len(logins) > 20 else "")
        # A second writer is a second publisher unless something stops a branch from publishing: rulesets, and for the
        # internal catalog the rule of the protected paths in particular
        closed = self.protected_paths_ok if self.profile == "internal" else not self.no_rulesets
        if len(logins) > 1 and not closed:
            return self.add("people.write", FAIL, now=shown,
                            fix="Write access is publish access here: keep one writer until the repository has rulesets"
                                + (" and the protected paths rule" if self.profile == "internal" else ""))
        self.add("people.write", MANUAL, now=shown)


def _options(options) -> dict:
    given = dict(options or {})
    asked = given.get("as")
    if asked not in (None, "public", "private", "internal"):
        raise Usage("--as takes public, private or internal.")
    out = {}
    for key, what in (("required_checks", "--required-check takes the name of a check."),
                      ("allow_org_secrets", "--allow-org-secret takes a name.")):
        names = list(as_list(given.get(key)))
        if not all(isinstance(name, str) and 1 <= len(name) <= 100 and name.isprintable() for name in names):
            raise Usage(what)
        out[key] = names
    return {"as": asked, "solo": bool(given.get("solo")), "effective_only": bool(given.get("effective_only")),
            "standard": given.get("standard"), **out}


def audit(repo: str, runner: Callable, options: Optional[dict] = None) -> Findings:
    """The findings for `repo` ("OWNER/REPO"), read through `runner`. `options`: `as` (public, private or internal: the
    visibility the repository is audited against, else its own), `solo`, `effective_only`, `required_checks` (names that
    replace "Catalog CI result"), `allow_org_secrets` (the names of the organization secrets and variables that are the
    maintainers' own), `standard` (the parsed `.github/standard.json`, else it is read from the repository). Writes nothing.
    Raises Unusable for a repository that cannot be read and for a sign-in that is missing."""
    if not isinstance(repo, str) or not REPO_NAME.fullmatch(repo) or repo.endswith(".git") \
            or repo.split("/")[1] in (".", ".."):
        raise Usage("The repository is OWNER/REPO on github.com.")
    return _Audit(repo, runner, _options(options)).run()


# ---------------------------------------------------------------- apply

def _shape(text: str):
    """The regular expression of a path of ALLOWED."""
    classes = {"id": r"\d{1,12}", "file": r"[A-Za-z0-9._-]{1,100}\.yml", "environment": "|".join(map(re.escape, ENVIRONMENTS))}
    out = []
    for number, part in enumerate(re.split(r"\{(\w+)\}", text)):
        out.append(re.escape(part) if number % 2 == 0 else f"(?:{classes[part]})")
    return re.compile("".join(out))


_ALLOWED = tuple((method, _shape(shape)) for method, shape in ALLOWED)


def allowed(repo: str, method: str, path: str, body: Any = None) -> bool:
    """Whether this request is one repo-settings may make on `repo`."""
    base = f"repos/{repo}"
    if not path.startswith(base) or ".." in path or "//" in path:
        return False
    if not any(m == method and pattern.fullmatch(path[len(base):]) for m, pattern in _ALLOWED):
        return False
    if path == base:
        analysis = as_dict(body).get("security_and_analysis")
        return isinstance(body, dict) and set(body) <= REPO_KEYS and (analysis is None or (
            isinstance(analysis, dict) and set(analysis) <= set(ANALYSIS_KEYS)))
    return body is None or isinstance(body, dict)


def _merge(wanted: List[Tuple[str, dict]]) -> List[Tuple[tuple, dict, List[str]]]:
    """[(key, request, ids of the findings it fixes)] in the order the requests were first asked for: one request for a
    path several findings fix, with their keys put together; one for a ruleset."""
    steps: Dict[tuple, list] = {}
    for id, req in wanted:
        key = ("ruleset", req["ruleset"], req["body"].get("target")) if "ruleset" in req else (req["method"], req["path"])
        if key not in steps:
            steps[key] = [copy.deepcopy(req), []]
        elif isinstance(req.get("body"), dict) and isinstance(steps[key][0].get("body"), dict) and "ruleset" not in req:
            steps[key][0]["body"] = {**steps[key][0]["body"], **req["body"]}
            steps[key][0].update({name: value for name, value in req.items() if name not in ("method", "path", "body")})
        if id not in steps[key][1]:
            steps[key][1].append(id)
    return [(key, req, ids) for key, (req, ids) in steps.items()]


def apply(repo: str, runner: Callable, findings: list, yes: bool, warnings: bool = False) -> List[Applied]:
    """The requests that bring `repo` to the standard, from the fixes of `findings` that failed (and that warn, with
    `warnings`). Without `yes` they are only listed: no request but a read is made. A request of one fix is not made when an
    earlier request of that fix was refused or skipped, so an allow-list never comes in without its patterns. Rulesets are
    looked for by name just before they are sent: replaced if there, else created. A request is `done` when GitHub answered
    2xx; no answer is `refused` like a refusal is."""
    wanted = [(finding.id, req) for finding in findings if finding.status == FAIL or (warnings and finding.status == WARN)
              for req in (finding.fix if isinstance(finding.fix, list) else [])]
    steps = _merge(wanted)
    chain: Dict[str, list] = {}
    for key, _, ids in steps:
        for id in ids:
            chain.setdefault(id, []).append(key)
    lists_patterns = any(req["path"].endswith("/actions/permissions/selected-actions")
                         and as_list(as_dict(req.get("body")).get("patterns_allowed")) for _, req, _ in steps)
    out, stopped = [], set()
    for key, req, ids in steps:
        method, path, body = req["method"], req["path"], req.get("body")
        reason = None
        if not allowed(repo, method, path, body):
            reason = "this is not a request repo-settings makes"
        elif req.get("enables_list") and not lists_patterns:
            reason = "an allow-list is not enabled without its patterns"
        elif any(earlier in stopped for id in ids for earlier in chain[id][:chain[id].index(key)]):
            reason = "an earlier request of the same fix was not made"
        elif "ruleset" in req:
            method, path, reason = _find_ruleset(repo, runner, body)
            if reason is None and not allowed(repo, method, path, body):
                reason = "this is not a request repo-settings makes"
        if reason is not None:
            out.append(Applied(method, path, body, "skipped", reason))
            stopped.add(key)
        elif not yes:
            out.append(Applied(method, path, body, "planned", ""))
        else:
            reply = as_reply(runner(method, path, body))
            if 200 <= reply.code < 300:
                out.append(Applied(method, path, body, "done", ""))
            else:
                # Code 0 is no answer at all (a timeout, a flood, a connection that failed): not a request that was made.
                # A push ruleset on a plan without push rules is refused in the plan's words: it is not for this repository
                out.append(Applied(method, path, body, "skipped" if plan_limit(reply) else "refused", refusal(reply)))
                stopped.add(key)
    return out


def _find_ruleset(repo: str, runner: Callable, body: dict) -> Tuple[str, str, Optional[str]]:
    """(method, path, why not) for a ruleset: its replacement when the repository has a ruleset of that name and target, else
    its creation."""
    reply, items = Reads(runner).pages(f"repos/{repo}/rulesets")
    if reply.code != 200:
        return "POST", f"repos/{repo}/rulesets", "the rulesets of the repository cannot be listed: " + refusal(reply)
    for item in map(as_dict, items):
        if item.get("name") == body.get("name") and item.get("target") == body.get("target") and \
                (item.get("source_type") or "Repository") == "Repository" and is_int(item.get("id")):
            return "PUT", f"repos/{repo}/rulesets/{item['id']}", None
    return "POST", f"repos/{repo}/rulesets", None


# ---------------------------------------------------------------- output

def summary(findings) -> Dict[str, int]:
    count = {status: 0 for status in STATUSES}
    for finding in findings:
        count[finding.status] += 1
    return {status: number for status, number in count.items() if number}


def exit_code(findings, strict: bool = False) -> int:
    """1 when a rule failed; with `strict` also when one warns or cannot be enforced on this plan (an `n.a.` that says the
    rule does not apply here is not that, and neither is a `warn` about what the plan sells and the standard does not ask
    for there)."""
    skip, optional = getattr(findings, "inapplicable", ()), getattr(findings, "optional", ())
    bad = (FAIL, WARN, NA) if strict else (FAIL,)

    def counts(finding) -> bool:
        if finding.status not in bad:
            return False
        return not (finding.status == NA and finding.id in skip or finding.status == WARN and finding.id in optional)
    return 1 if any(counts(finding) for finding in findings) else 0


def fix_lines(fix) -> List[str]:
    if isinstance(fix, str):
        return fix.splitlines()
    return [f"{req['method']} {req['path']}" for req in fix] if isinstance(fix, list) else []


def escape(text: str) -> str:
    """Data of a workflow command of GitHub Actions: nothing in it can end the command or begin another."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render(findings, fmt: str = "text") -> str:
    """The audit as text (the table), as `github` annotations, or as one JSON document with sorted keys."""
    repo, about, level = getattr(findings, "repo", ""), getattr(findings, "about", ""), getattr(findings, "level", None)
    if fmt == "json":
        return json.dumps({"repository": repo, "about": about, "standard": level, "summary": summary(findings),
                           "not_applicable": sorted(getattr(findings, "inapplicable", ())),
                           "findings": [finding._asdict() for finding in findings]}, indent=2, sort_keys=True)
    if fmt == "github":
        return "\n".join(f"::{'error' if finding.status == FAIL else 'warning'} title={finding.id}::" + escape(
            finding.title + (f": {finding.now}" if finding.now else "")) for finding in findings
            if finding.status in (FAIL, WARN))
    width = max([len(finding.id) for finding in findings] or [0])
    lines = [f"Catalog standard{f' {level}' if level else ''} for {repo} ({about})"]
    for finding in findings:
        lines.append(f"  {finding.status:<6} {finding.id:<{width}} {finding.title}")
        if finding.now and finding.status != OK:
            lines.append(f"           {'why' if finding.status == NA else 'now'}: {finding.now}")
        if finding.status in (FAIL, WARN, MANUAL):
            for number, line in enumerate(fix_lines(finding.fix)):
                lines.append(("           fix: " if number == 0 else "                ") + line)
    lines.append("Summary: " + ", ".join(f"{number} {status}" for status, number in summary(findings).items()))
    return "\n".join(lines)


def render_applied(applied: List[Applied], fmt: str = "text") -> str:
    """The requests of `apply`: the `gh api` commands of a dry run, else what became of each."""
    if fmt == "json":
        return json.dumps([item._asdict() for item in applied], indent=2, sort_keys=True)
    if not applied:
        return "Nothing to change"
    lines = []
    for item in applied:
        if item.outcome == "planned":
            body = "" if item.body is None else " --input - <<< " + shlex.quote(json.dumps(item.body, sort_keys=True))
            lines.append(f"gh api --method {item.method} {item.path}{body}")
        else:
            lines.append(f"{item.outcome:<8} {item.method} {item.path}" + (f"  {item.detail}" if item.detail else ""))
    if all(item.outcome == "planned" for item in applied):
        lines.append("Nothing was changed. To make these requests: add --yes")
    return "\n".join(lines)


# ---------------------------------------------------------------- the command line

def _default_git(args, cwd=None):
    from . import gitx
    return gitx.run(args, cwd=cwd, timeout=20)


def repo_of_address(text: str) -> str:
    """OWNER/REPO of an address of github.com (https, ssh or the scp form), else Unusable with one line. The address is
    never shown: it could hold a credential."""
    from . import gitx
    try:
        address = gitx.parse_address(text)
    except gitx.CatalogError as error:
        if error.code == "bad_address_secret":
            raise Unusable("The origin remote holds a credential. Remove it from the address, then run this again.")
        raise Unusable("The origin remote is not an address repo-settings can read. Give OWNER/REPO.")
    if address.kind != "git" or address.host != HOST:
        raise Unusable(f"{clean(address.host or address.display, 80)} is not github.com. repo-settings works for GitHub only.")
    if not REPO_NAME.fullmatch(address.repo or ""):
        raise Unusable("The origin remote is not an address repo-settings can read. Give OWNER/REPO.")
    return address.repo


def resolve_target(text: Optional[str], git: Optional[Callable] = None, cwd: Optional[str] = None) -> str:
    """OWNER/REPO for what the person gave: OWNER/REPO itself, an address of github.com, or a folder, whose remote `origin`
    says. A folder that exists wins over a name that looks like OWNER/REPO."""
    given = (text or ".").strip()
    try:
        folder = Path(cwd or os.getcwd()) / given
    except OSError:                                    # the folder this runs in was removed
        folder = Path(given)
    # os.path.isdir, which answers False for a name too long or with a NUL, where Path.is_dir raises on some Pythons
    if os.path.isdir(folder):
        result = (git or _default_git)(["remote", "get-url", "origin"], cwd=str(folder))
        origin = result.out.decode("utf-8", "replace").strip() if result.code == 0 else ""
        if not origin:
            raise Unusable(f"{clean(given, 80)} has no origin remote. Give OWNER/REPO.")
        return repo_of_address(origin)
    if REPO_NAME.fullmatch(given):
        return given
    if "://" in given or "@" in given:
        return repo_of_address(given)
    raise Unusable(f"{clean(given, 80)} is neither OWNER/REPO nor a folder.")


class Parser(argparse.ArgumentParser):
    def error(self, text):
        raise Usage(text)


def _name(text: str) -> str:
    if not 1 <= len(text) <= 100 or not text.isprintable():
        raise argparse.ArgumentTypeError("a name of 1 to 100 printable characters")
    return text


USAGE = """catalog.py repo-settings [OWNER/REPO or FOLDER] [--apply [--yes] [--warnings]] [--solo] [--effective-only]
                         [--strict] [--as public or private or internal] [--required-check NAME]
                         [--allow-org-secret NAME] [--fleet OWNER] [--format text or json or github]

Audits the repository standard of a catalog on GitHub, through the gh command of the maintainer. --apply lists the
requests that bring the repository to the standard, --yes makes them. Exit 0 when nothing failed, 1 when a rule failed
(with --strict also a warning or a plan that cannot enforce a rule), 2 when it cannot be done."""


def parser() -> argparse.ArgumentParser:
    p = Parser(prog="catalog.py repo-settings", add_help=False)
    p.add_argument("target", nargs="?", help="OWNER/REPO or a folder (default: this folder)")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--warnings", action="store_true")
    p.add_argument("--solo", action="store_true")
    p.add_argument("--effective-only", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--as", dest="visibility", choices=("public", "private", "internal"))
    p.add_argument("--required-check", action="append", default=[], type=_name)
    p.add_argument("--allow-org-secret", action="append", default=[], type=_name)
    p.add_argument("--fleet", metavar="OWNER")
    p.add_argument("--format", choices=("text", "json", "github"), default="text")
    return p


def main(argv: Optional[List[str]] = None, runner: Optional[Callable] = None, git: Optional[Callable] = None,
         stdout=None, stderr=None, cwd: Optional[str] = None) -> int:
    """`catalog.py repo-settings`. Exit 0 when nothing failed, 1 when a rule failed (with --strict also a warning or an `n.a.`;
    with --apply --yes, when GitHub refused a request), 2 when it cannot be done: no `gh`, no sign-in, a repository that is
    not on GitHub, a command line that makes no sense. Every exit 2 is one line on standard error."""
    out, err = stdout or sys.stdout, stderr or sys.stderr
    if argv is not None and any(word in ("-h", "--help") for word in argv):
        print(USAGE, file=out)
        return 0
    try:
        args = parser().parse_args(argv)
        if (args.yes or args.warnings) and not args.apply:
            raise Usage("--yes and --warnings go with --apply.")
        if args.fleet is not None and (args.target or args.apply):
            raise Usage("--fleet audits every catalog of an owner: it takes no repository and no --apply.")
        options = {"as": args.visibility, "solo": args.solo, "effective_only": args.effective_only,
                   "required_checks": args.required_check, "allow_org_secrets": args.allow_org_secret}
        runner = runner or Gh()
        if args.fleet is not None:
            from . import fleet
            rows = fleet.audit_fleet(args.fleet, runner, options)
            print(fleet.render_audit(rows, args.format), file=out)
            return fleet.exit_code(rows, args.strict)
        repo = resolve_target(args.target, git, cwd)
        findings = audit(repo, runner, options)
        if not args.apply:
            print(render(findings, args.format), file=out)
            return exit_code(findings, args.strict)
        applied = apply(repo, runner, findings, args.yes, args.warnings)
        if args.format == "json":
            document = json.loads(render(findings, "json"))
            document["requests"] = [item._asdict() for item in applied]
            print(json.dumps(document, indent=2, sort_keys=True), file=out)
        else:
            print(render(findings, args.format), file=out)
            print(render_applied(applied), file=out)
        if args.yes and any(item.outcome == "refused" for item in applied):
            return 1
        if args.yes and any(item.outcome == "done" for item in applied):
            findings = audit(repo, runner, options)           # what is there now, asked again (it writes nothing)
        return exit_code(findings, args.strict)
    except SystemExit as leave:
        return leave.code if isinstance(leave.code, int) else 2
    except Unusable as problem:
        print(problem, file=err)
        return 2
