"""SE: secrets and private identifiers, in the files of a tree and in every line a change added.

Seven precise patterns (a private key, the tokens of GitHub, AWS, Slack and Google, a JWT, an `sk-` key) and the shapes that
need a place to be read (a literal value under a secret's name in a Compose file, a password in a URL, an allow marker that
silences a scanner). SE03 is the pinned gitleaks container's, run by CI, and is only a row of the table here.

Every file of the tree is read, whatever its name or its bytes, as `text_of` gives it (NULs taken out): GitHub shows no diff
for a file with a NUL in it, and nothing else a secret could hide behind is a reason not to look. SE04 is asked of the Compose
files by name, of `.env.example`, and of any other YAML file of a unit (not a seed) that has a top-level `services:`, in block or
flow style, with the value after the colon or on the next line.

How it stays fast on the file of a stranger. A pattern is only tried when a literal that every match holds is in the text
(`in` is a memory scan); every pattern is linear, with no run that two starting places can scan again (a bounded repeat, or a
word that is cut at its end by a lookahead and not by a backtrack); a JWT, whose three parts each run on, is read as a token
split at its dots; a URL is found at its `://` and read forward only. A line of a megabyte takes a fraction of a second.

Messages never carry what matched: a file, a line and a kind. A secret removed by a later commit is still public in the pull
request, so the lines a change added are read too, and the message says it was "removed later": a line that is not, whole, a line
of the file as it is now (a character added to a key that was public makes it another line). Pure: it reads the tree and the
commits it is given, writes nothing, runs nothing."""

import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from ..report import ERROR, WARNING, Problem
from ..tree import REGULAR
from . import PER_FILE, Context, basename, context, is_compose, line_of, newlines, outline, problem, read_limit, say, scalar, \
    table, text_of, unit_of, walk

SUFFIX = " in commit {sha}, removed later in this pull request"

RULES = table([
    ("SE01", ERROR, "A private key header (-----BEGIN ... PRIVATE KEY-----)",
     "{path}:{line} holds a private key. Fix: revoke it first, it is public in this pull request. Then remove it from the "
     "commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your branch."),
    ("SE02", ERROR, "A token pattern: GitHub, AWS access key, Slack, Google API key, a JWT triple, an sk- key",
     "{path}:{line} looks like {kind}. Fix: make it a setting (${PREFIX_NAME}) and keep the value in .env. If it is a real "
     "token, revoke it first, then remove it from the commits of this branch (git reset --soft $(git merge-base origin/main "
     "HEAD), commit again, force-push)."),
    ("SE03", ERROR, "A finding of the pinned gitleaks container over BASE..HEAD, run with the Lab's configuration",
     "{path}:{line} matches the rule {rule} (the secret is redacted). Fix: revoke it first, then remove it from the commits "
     "of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your branch."),
    ("SE04", ERROR, "A literal secret value in a Compose file or .env.example",
     "{path}:{line}: {name} has a literal value. Fix: write ${NAME:-} and set the value in .env."),
    ("SE05", ERROR, "A URL with a password (scheme://user:secret@host): an error in Compose files and seed/**, a warning "
                    "elsewhere",
     "{path}:{line} holds a URL with a password. Fix: take the password from a setting."),
    ("SE06", ERROR, "A private identifier: a registry host, an image prefix, a name or an address of any catalog this Lab has "
                    "added (Share, target Public)",
     "{path}:{line} names {identifier}, which belongs to a private catalog. Fix: remove it, or share to a Private catalog."),
    ("SE07", ERROR, "The text gitleaks:allow, or another scanner's allow marker",
     "{path}:{line} carries a scanner allow marker. Fix: remove it; a maintainer decides what the scan may skip."),
])

# The names of what is secret in an environment, a label or an account (an API key, not a public one), then the endings of a
# name that says where a secret is, what kind it is, how it is reached or whether it is on, which is none itself. The same as
# `check.SECRET_NAME` and `check.NOT_SECRET`, which a test keeps equal: the engine's own check and this rule read one thing
SECRET_NAME = re.compile(r"PASSWORD|PASSWD|PASSPHRASE|SECRET|API[_.-]?KEY|(?:^|[_.-])TOKEN$|(?<!PUBLIC)[_.-]KEY$", re.I)
NOT_SECRET = re.compile(r"(?:URL|URI|PATH|FILE|TYPE|ENDPOINT|ENABLED|HOST|PORT|METHOD|POLICY|REGEX|NAMESPACE|PROVIDER"
                        r"|TIMEOUT|TIMEOUTMS)$", re.I)

KINDS = {"github": "a GitHub token", "aws": "an AWS access key", "slack": "a Slack token", "google": "a Google API key",
         "jwt": "a JWT", "sk": "an API key that starts with sk-"}

_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----")
# A token starts where no letter or digit comes before it: an underscore, a dash or `=` before it hides nothing (`\b` would
# not see a token after `KEY_`, because an underscore is a word character)
_GITHUB = re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})", re.ASCII)
_AWS = re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])", re.ASCII)
_SLACK = re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}", re.ASCII)
_GOOGLE = re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])", re.ASCII)
_SK = re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_-]{32,}", re.ASCII)
_RUN = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{38,}")
# `scheme://user:password@`: the user may be empty (`redis://:secret@cache`), and the password is any text of three characters
# or more that is not a reference to a setting (`${NAME}`, `$NAME`); a `$$` or a brace in it is a letter of the password
_URL = re.compile(r"://([^/\s:@]*):((?!\$[A-Za-z_{])(?:(?!\$\{)[^/\s@]){3,})@")
_ALLOW = re.compile(r"(?i)gitleaks:allow|pragma:\s{0,3}allowlist\s{1,3}(?:nextline\s{1,3})?secret|trufflehog:ignore|"
                    r"secretlint-disable")
_REFERENCE = re.compile(r"\$(?:\{|[A-Za-z_])")
_ENV_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*[=:]\s*(.*)\Z")
# Each pattern with the literals one of which every match holds
_TOKENS = (
    ("github", _GITHUB, ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")),
    ("aws", _AWS, ("AKIA", "ASIA")),
    ("slack", _SLACK, ("xox",)),
    ("google", _GOOGLE, ("AIza",)),
    ("sk", _SK, ("sk-",)),
)
_ALLOW_LITERALS = ("gitleaks", "allowlist", "trufflehog", "secretlint")
_SECRET_FILES = (".env.example",)


class Hit(NamedTuple):
    """A pattern that matched: its code, where in the text, and what kind (SE02) or which name (SE04)."""
    code: str
    offset: int
    kind: str = ""


def _has(text: str, literals: Iterable[str]) -> bool:
    return any(literal in text for literal in literals)


def jwts(text: str) -> List[int]:
    """The offsets of the JWTs of a text: three runs of base64url characters joined by dots, the first two starting with
    `eyJ`. A run is cut at the characters that are in none, and read once."""
    found = []
    for run in _RUN.finditer(text):
        token = run.group()
        if "eyJ" not in token:
            continue
        parts = token.split(".")
        at = 0
        for index in range(len(parts) - 2):
            head, body, tail = parts[index], parts[index + 1], parts[index + 2]
            if head.startswith("eyJ") and len(head) >= 13 and body.startswith("eyJ") and len(body) >= 13 and len(tail) >= 10:
                found.append(run.start() + at)
                break
            at += len(head) + 1
    return found


def token_hits(text: str) -> List[Hit]:
    """SE01 and SE02 in a text."""
    hits: List[Hit] = []
    if "PRIVATE KEY" in text:
        hits += [Hit("SE01", found.start()) for found in _KEY.finditer(text)]
    for kind, pattern, literals in _TOKENS:
        if _has(text, literals):
            for found in pattern.finditer(text):
                if kind == "sk" and not any(c.isdigit() or c.isupper() for c in found.group()[3:]):
                    continue   # a kebab-case name, not a random key
                hits.append(Hit("SE02", found.start(), kind))
    if "eyJ" in text:
        hits += [Hit("SE02", offset, "jwt") for offset in jwts(text)]
    return hits


def password_urls(text: str) -> List[int]:
    """The offsets of the URLs of a text that carry a password: `://user:secret@`. A password that is a setting (`${NAME}`) is
    none, and the scheme before the `://` has to be a word."""
    found = []
    if "://" in text:
        for match in _URL.finditer(text):
            if _scheme_before(text, match.start()):
                found.append(match.start())
    return found


def _scheme_before(text: str, at: int) -> bool:
    """Whether the characters before `at` end a URL scheme: a run of at most 32 letters, digits, `+`, `.` and `-` that holds a
    letter (a scheme starts with one)."""
    start = at
    while start > 0 and at - start < 33 and text[start - 1].isascii() and (text[start - 1].isalnum() or text[start - 1] in "+.-"):
        start -= 1
    return at - start <= 32 and any(char.isascii() and char.isalpha() for char in text[start:at])


def allow_markers(text: str) -> List[int]:
    """The offsets of the allow markers of secret scanners in a text."""
    low = text.lower()
    if not any(literal in low for literal in _ALLOW_LITERALS):
        return []
    return [found.start() for found in _ALLOW.finditer(text)]


def names_a_secret(key: str) -> bool:
    """Whether the name of a setting says that its value is a secret (the engine's rule, `check.SECRET_NAME`)."""
    return bool(SECRET_NAME.search(key) and not NOT_SECRET.search(key))


def is_literal(value: str) -> bool:
    """Whether the text of a setting is a value written in the file: not a reference to a setting (`${NAME}`, `$NAME`), not an
    alias or a list (`secrets: [db_password]` names secrets and holds none) and not nothing."""
    value = scalar(value)
    return bool(value) and value not in ("~", "null", "Null", "NULL") and not _REFERENCE.search(value) \
        and not value.startswith(("*", "[", "{"))


def env_literal(line: str) -> Optional[str]:
    """The name of a setting that a line of a Compose file or an `.env.example` gives a literal value under a secret's name,
    or None. For a line alone, with no tree around it (a line a commit added): `KEY: value`, `- KEY=value` and `KEY=value`,
    where KEY is written in capitals as the name of a setting is."""
    text = line.strip()
    if text.startswith("- "):
        text = text[2:].strip().strip("\"'")
    found = _ENV_LINE.match(text) if len(text) < 4096 else None
    if not found:
        return None
    name, value = found.group(1), found.group(2)
    if value.startswith(("\"", "'")):
        value = value[:1] + value[1:].split(value[0], 1)[0] + value[0]
    elif " #" in value:
        value = value.split(" #", 1)[0]
    if not name.isupper() or not names_a_secret(name) or not is_literal(value):
        return None
    return name


def _account(path: Tuple[str, ...]) -> bool:
    return len(path) >= 2 and path[0] == "x-gravitee" and path[1] == "accounts"


FLOW_BUDGET = 4000     # items of the flow collections of one value that are read
FLOW_CHARS = 262144   # and the characters those items hold, counted again for each collection that holds them


def flow_items(body: str) -> List[str]:
    """The items of the body of a flow collection (the text between its brackets): the text between its commas that are outside
    quotes and brackets."""
    items, depth, quote, start, at = [], 0, None, 0, 0
    while at < len(body):
        char = body[at]
        if quote:
            if char == "\\" and quote == '"':
                at += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth <= 0:
            items.append(body[start:at])
            start = at + 1
        at += 1
    items.append(body[start:])
    return items


def flow_key(item: str) -> Tuple[str, str]:
    """(key, value) of the `key: value` item of a flow mapping, or ("", "") for an item with no colon."""
    if item[:1] in "\"'":
        end = item.find(item[0], 1)
        rest = item[end + 1:].lstrip() if end != -1 else ""
        return (item[1:end], rest[1:].strip()) if rest.startswith(":") else ("", "")
    depth, at = 0, 0
    while at < len(item):
        char = item[at]
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ":" and depth <= 0 and (at + 1 == len(item) or item[at + 1] in " \t"):
            return item[:at].strip(), item[at + 1:].strip()
        at += 1
    return "", ""


def flow_pairs(text: str) -> List[Tuple[str, str]]:
    """The (name, value) of what a flow collection writes as a setting: each `NAME=value` item of a `[...]` and each `name: value`
    of a `{...}`, in the collections nested in it as well. Read with no recursion and within FLOW_BUDGET items."""
    pairs: List[Tuple[str, str]] = []
    work, budget, chars = [text.strip()], FLOW_BUDGET, FLOW_CHARS
    while work and budget > 0 and chars > 0:
        collection = work.pop()
        if collection[:1] not in ("[", "{"):
            continue
        mapping = collection[0] == "{"
        body = collection[1:-1] if collection.endswith("}" if mapping else "]") else collection[1:]
        chars -= len(body)
        for item in flow_items(body):
            budget -= 1
            item = item.strip()
            if not item:
                continue
            if mapping:
                name, value = flow_key(item)
                if name:
                    pairs.append((name, value))
                    work.append(value)
            elif item[:1] in ("[", "{"):
                work.append(item)
            else:
                name, equals, value = scalar(item).partition("=")
                if equals and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
                    pairs.append((name, value))
    return pairs


def compose_literals(text: str) -> List[Tuple[int, str]]:
    """[(line, name)] of the entries of a Compose file that write a secret themselves: a key whose name says it is a secret and
    whose value is a text that names no setting (written after the colon, on the next line, or inside a flow collection such as
    `environment: [A_PASSWORD=x]`), a `- KEY=value` item of a list, and an alias of an anchor that is itself a literal. Of
    `x-gravitee` only the accounts hold secrets (the rest is read by the Lab, and a license feature may be named after a secret
    manager). A block scalar's text is not read."""
    parsed = outline(text)
    literal_anchors = {name for name, node in parsed.anchors.items() if node.value and is_literal(node.value)}
    found = []

    def judge(line: int, name: str, value: str, account: bool) -> None:
        if not (names_a_secret(name) or (account and name.lower() == "key")):
            return
        if value.startswith("*"):
            if value[1:] in literal_anchors:
                found.append((line, name))
        elif is_literal(value):
            found.append((line, name))

    for node, path in walk(parsed.root):
        if node.key == "<<":
            continue
        value, children = node.value, node.children
        if node.key is not None and not value and len(children) == 1 and children[0].key is None and not children[0].item \
                and not children[0].children:
            value, children = children[0].value, []   # `NAME:` and the value on the next line
        flow = value[:1] in ("[", "{")
        # The accounts of x-gravitee hold secrets; `accounts: [{...}]` holds them in the key that names the list
        account = _account(path[:-1]) if node.key is not None else _account(path)
        account = account or (flow and node.key is not None and _account(path))
        if path[:1] == ("x-gravitee",) and not account:
            continue
        if children or not value:
            continue
        if flow:
            for name, item in flow_pairs(value):
                judge(node.line, name, item, account)
        elif node.key is not None:
            judge(node.line, node.key, value, account)
        else:
            name, equals, item = scalar(value).partition("=")
            if equals and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) and names_a_secret(name) and is_literal(item):
                found.append((node.line, name))
    return found


def _commit_message(message: str, sha: str) -> str:
    """The message of a finding in a commit: it says in which, and that the secret is still public."""
    head, sep, tail = message.partition(". Fix:")
    return head + say(SUFFIX, sha=sha[:7]) + sep + tail


def _is_seed(path: str) -> bool:
    return unit_of(path) is not None and "seed" in path.split("/")[:-1]


def _severity_url(path: str) -> str:
    return ERROR if is_compose(path) or _is_seed(path) else WARNING


def _wants_env(path: str) -> bool:
    return (is_compose(path) and path != "catalog.yml") or basename(path) in _SECRET_FILES


_SERVICES = re.compile(r"^services[ \t]{0,8}:", re.M)


def _compose_like(path: str, text: str) -> bool:
    """Whether a YAML file of a unit that is not named like a Compose file is one: it has a top-level `services:`. A unit can
    include or extend any file (`compose.override.yml`, `services.yml`). A seed brings data and a configuration is no Compose
    file, so neither is asked."""
    return path.lower().endswith((".yml", ".yaml")) and unit_of(path) is not None and not _is_seed(path) and "services" in text \
        and bool(_SERVICES.search(text))


def scan_text(path: str, text: str, ctx: Context) -> List[Problem]:
    """SE01 to SE07 in one file's text."""
    found: List[Problem] = []
    offsets: Optional[List[int]] = None
    count: Dict[str, int] = {}

    def add(code: str, offset: int, **fields):
        nonlocal offsets
        if offsets is None:
            offsets = newlines(text)
        if count.get(code, 0) >= PER_FILE:
            return
        count[code] = count.get(code, 0) + 1
        severity = _severity_url(path) if code == "SE05" else None
        found.append(problem(RULES, code, path, line_of(offsets, offset), severity, **fields))

    for hit in token_hits(text):
        add(hit.code, hit.offset, kind=KINDS.get(hit.kind, ""))
    for offset in password_urls(text):
        add("SE05", offset)
    for offset in allow_markers(text):
        add("SE07", offset)
    if _wants_env(path) or _compose_like(path, text):
        if basename(path) in _SECRET_FILES:
            for number, line in enumerate(text.split("\n"), 1):
                name = env_literal(line) if line.strip() and not line.lstrip().startswith("#") else None
                if name and count.get("SE04", 0) < PER_FILE:
                    count["SE04"] = count.get("SE04", 0) + 1
                    found.append(problem(RULES, "SE04", path, number, name=name))
        else:
            for number, name in compose_literals(text)[:PER_FILE]:
                found.append(problem(RULES, "SE04", path, number, name=name))
    if ctx.private:
        lowered = text.lower()
        for identifier in ctx.private:
            at = lowered.find(identifier.lower()) if len(identifier) >= 4 else -1
            if at != -1:
                add("SE06", at, identifier=identifier)
    return found


def scan_added(added: Iterable, ctx: Context, tree) -> List[Problem]:
    """SE01, SE02, SE04, SE05 and SE07 over the lines a change added. A line that is still in the file today was found by the
    scan of the tree; one that is not has been taken away since, and is reported with the commit that added it: it is public
    in the pull request all the same."""
    found: List[Problem] = []
    count: Dict[Tuple[str, str], int] = {}
    reported: Set[Tuple[str, str, int, str]] = set()
    current: Dict[str, Set[str]] = {}
    composes: Dict[str, bool] = {}

    def lines_of(path: str) -> Set[str]:
        """The lines of the file as it is now, with no carriage return and no NUL, and whether it is a Compose file."""
        if path not in current:
            data = tree.read(path, read_limit(path))
            if len(current) >= 64:
                current.clear()
                composes.clear()
            text = text_of(data) if data is not None else ""
            current[path] = {each.rstrip("\r") for each in text.split("\n")}
            composes[path] = _compose_like(path, text)
        return current[path]

    def compose_now(path: str) -> bool:
        lines_of(path)
        return composes[path]

    def still_there(path: str, text: str) -> bool:
        """Whether the scan of the tree sees this very line: a whole line of the file as it is now. A line that only contains
        the old one (a character added to a key that was public) is another line, and the old one is still public."""
        return text.rstrip("\r") in lines_of(path)

    for line in added:
        text = line.text[:read_limit(line.path)].replace("\0", "")
        hits: List[Tuple[str, str, str]] = [(hit.code, KINDS.get(hit.kind, ""), "") for hit in token_hits(text)]
        if password_urls(text):
            hits.append(("SE05", "", ""))
        if allow_markers(text):
            hits.append(("SE07", "", ""))
        name = env_literal(text)
        if name and (_wants_env(line.path) or (line.path.lower().endswith((".yml", ".yaml")) and compose_now(line.path))):
            hits.append(("SE04", "", name))
        if not hits or still_there(line.path, text):
            continue
        for code, kind, name in hits:
            marker = (code, line.path, line.line, line.sha)
            if marker in reported or count.get((code, line.sha), 0) >= PER_FILE:
                continue
            reported.add(marker)
            count[(code, line.sha)] = count.get((code, line.sha), 0) + 1
            severity = _severity_url(line.path) if code == "SE05" else None
            made = problem(RULES, code, line.path, line.line, severity, kind=kind, name=name)
            found.append(made._replace(message=_commit_message(made.message, line.sha)))
    return found


def se03(path: str, line: int, rule: str) -> Problem:
    """A finding of the gitleaks container, as CI words it (the secret is redacted, only its rule is named)."""
    return problem(RULES, "SE03", path, line, rule=rule)


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """SE01, SE02, SE04 to SE07 over the text files of a tree, and over the lines of `ctx.history`, if it has any."""
    ctx = ctx if ctx is not None else context(tree)
    found: List[Problem] = []
    for entry in tree.entries():
        if entry.mode not in REGULAR or entry.size > read_limit(entry.path):
            continue
        data = tree.read(entry.path, read_limit(entry.path))
        if data is not None:
            found += scan_text(entry.path, text_of(data), ctx)
    if ctx.history is not None:
        found += scan_added(ctx.history.added(), ctx, tree)
    return found


__all__ = ["KINDS", "NOT_SECRET", "RULES", "SECRET_NAME", "check", "compose_literals", "env_literal", "flow_items", "flow_key", "flow_pairs",
           "jwts", "names_a_secret", "scan_added", "scan_text", "se03", "token_hits"]
