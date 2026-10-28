"""CO: the CODEOWNERS file. Two fixed lines and nothing more is asked of it: `*` and `/.github/`, each with owners, and
every owner `@user` or `@organization/team`, in ASCII. The maintainers are, by definition, the owners of the `*` line; no other
file says who they are. What GitHub itself thinks of the file (errors in it, an owner without write access, whether the plan
honours it) is read by the settings audit (`files.codeowners`), not by a rule.

The same two checks guard `catalog.py new --maintainers`: an owner string with a newline or a space would write a line of the
file of its own (`"@x\\n/.github/ @evil"`), so `maintainers()` refuses anything that is not a name before anything is rendered.

One more thing is read, because it is the point of the second line: a line that comes later in the file and matches a file under
`.github/` with owners other than those of the `/.github/` line wins over it (the last matching line decides, and one approval
from any owner on it is enough), so `/.github/` has no owner in fact. It is said with the same words: add the line again, at
the end. "A file under `.github/`" is each file the template ships there, managed or seeded (`PROBES`), and anything a pattern
names below `.github/` by its path, whether the file is there yet or not.

Pure: it reads the tree, writes nothing, runs nothing, and a file of hostile lines costs a fraction of a second (a line is split
at blanks, never matched with a pattern that can backtrack; a name or a path is looked up in a set; a pattern with a wildcard is
matched in a table the size of the pattern and the path, after the cheap reasons it cannot match were tried, and only `WORK` of
them are matched in a file: the rest are taken to match, as a pattern too long to read is)."""

import re
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from ..report import ERROR, Problem, printable
from . import OTHER_LIMIT, Context, say, table

FILE = ".github/CODEOWNERS"
# The two lines the standard asks for: the pattern of each
REQUIRED = ("*", "/.github/")
UNKNOWN = "@OWNER/maintainers"   # what the fix names when the file does not say who the maintainers are

RULES = table([
    ("CO01", ERROR, "The file lacks the line * or the line /.github/, a line names no owner, or an owner is not @user or "
                    "@organization/team (ASCII only)",
     ".github/CODEOWNERS: {path} has no owner. Fix: add the line: {path} {maintainers}"),
])
MORE = {"CO01": (".github/CODEOWNERS: {value} is not a GitHub name. Fix: write @name or @organization/team.",)}

# @user, and @organization/team: GitHub's own limits (39 characters for an account, 100 for a team), ASCII only
USER = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
TEAM = re.compile(r"@[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SEPARATE = re.compile(r"[ \t]+")
SHOWN = 60            # characters of a stranger's word that a message shows
MAX_OWNERS = 20       # owners of one line that `new` accepts
MAX_PATTERN = 512     # a pattern longer than this is not matched (it cannot name a file of the standard): it counts as a shadow
MAX_SEGMENTS = 64
WORK = 2000           # patterns with a wildcard that are matched in one file; the next ones count as a shadow, like a pattern too long
# The files under .github/ that the template ships, managed or seeded: the ones that run with a token, say who may change them, or
# are what a reviewer reads. A later line that owns any of them differently shadows `/.github/`
PROBES = (".github/CODEOWNERS", ".github/standard.json", ".github/dependabot.yml", ".github/PULL_REQUEST_TEMPLATE.md",
          ".github/ISSUE_TEMPLATE/config.yml", ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/new_item.yml",
          ".github/workflows/ci.yml", ".github/workflows/nightly.yml", ".github/workflows/publish.yml", ".github/workflows/e2e.yml",
          ".github/workflows/versions.yml",
          ".github/actions/lab/action.yml", ".github/rulesets/default-branch.json", ".github/rulesets/tags.json",
          ".github/rulesets/push.json", ".github/rulesets/protected-paths.json")
_NAMES = tuple(tuple(path.split("/")) for path in PROBES)
_FILES = frozenset(PROBES)
_FOLDERS = frozenset("/".join(names[:cut]) for names in _NAMES for cut in range(1, len(names)))
_ANY = frozenset(name for names in _NAMES for name in names)          # every name a probe is made of
_DIRS = frozenset(name for names in _NAMES for name in names[:-1])    # and those that are folders
STARS = re.compile(r"\*+")


class OwnerError(ValueError):
    """The maintainers given to `new` are not names: the message is one line for the person."""


class Line(NamedTuple):
    number: int
    pattern: str
    owners: Tuple[str, ...]


def owner_ok(token: str) -> bool:
    """Whether a word is an owner the standard accepts: `@user` or `@organization/team`, ASCII letters, digits and the few
    characters GitHub allows, nothing else (a newline, a blank, an address, a name that looks like one in another script)."""
    return len(token) <= 141 and (USER.fullmatch(token) is not None or TEAM.fullmatch(token) is not None)


def maintainers(text) -> List[str]:
    """The owners given to `new --maintainers` (`@A,@B` or `@ORG/TEAM`), in order and once each, or an OwnerError. Blanks around
    a comma are dropped; a blank or a line break inside a name is not: it would write a line of CODEOWNERS."""
    if not isinstance(text, str) or not text.strip(" ") or len(text) > 2000:
        raise OwnerError("--maintainers is required: name the people or the team that own this catalog, for example "
                         "--maintainers @acme/maintainers.")
    found: List[str] = []
    for part in text.split(","):
        word = part.strip(" ")
        if not owner_ok(word):
            raise OwnerError("--maintainers: " + say(MORE["CO01"][0].partition(": ")[2], value=shown(word)))
        if word.lower() not in [each.lower() for each in found]:
            found.append(word)
    if len(found) > MAX_OWNERS:
        raise OwnerError(f"--maintainers names {len(found)} owners, the most is {MAX_OWNERS}. Fix: name a team.")
    return found


def parse(text: str) -> List[Line]:
    """The rule lines of a CODEOWNERS text: (line number, pattern, owner words). A comment line, a blank one and a section
    header (`[Docs]`) are not rule lines. Words are split at blanks and tabs only, as GitHub splits them: any other kind of
    blank stays inside a word and makes it a name that is none."""
    lines: List[Line] = []
    if text.startswith("\U0000feff"):
        text = text[1:]
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r").strip(" \t")
        if not line or line.startswith("#") or line.startswith(("[", "^[")):
            continue
        words = SEPARATE.split(line)
        lines.append(Line(number, words[0], tuple(words[1:])))
    return lines


def _segment(pattern: str, name: str) -> bool:
    """Whether a name matches a pattern of `*` and `?` (no character classes: CODEOWNERS has none). Linear in the product of
    the two lengths at worst, with no backtracking pattern."""
    p = n = 0
    star = mark = -1
    while n < len(name):
        if p < len(pattern) and pattern[p] in (name[n], "?") and pattern[p] != "*":
            p += 1
            n += 1
        elif p < len(pattern) and pattern[p] == "*":
            star, mark = p, n
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            n = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


class Shape(NamedTuple):
    """How a pattern is read: from the top of the repository or at any depth, a folder or a name, its body, its segments (a run of
    `**` is one, a run of `*` inside a segment is one: the same patterns, a smaller table) and whether it has a wildcard."""
    anchored: bool
    folder: bool
    body: str
    parts: Tuple[str, ...]
    wild: bool


def shape(pattern: str) -> Optional[Shape]:
    """The Shape of a pattern, or None when it is not read: too long, the root (it owns every file), or too many segments. Such a
    pattern is taken to match: the file is judged by the stricter reading."""
    if len(pattern) > MAX_PATTERN:
        return None
    body = pattern.strip("/")
    raw = body.split("/") if body else []
    if not raw or len(raw) > MAX_SEGMENTS:
        return None
    parts: List[str] = []
    for part in raw:
        if part != "**":
            part = STARS.sub("*", part)
        elif parts and parts[-1] == "**":
            continue
        parts.append(part)
    return Shape("/" in pattern.rstrip("/"), pattern.endswith("/"), body, tuple(parts), "*" in body or "?" in body)


def _reach(parts: Sequence[str], names: Sequence[str], last: int) -> bool:
    """Whether the segments of a pattern, where `**` stands for any number of folders (none included), match the first `cut` names
    of a path for some cut from 1 to `last` (a folder owns every file below it). One row of a table for each segment: the cuts
    that can be reached after it, so that it ends when a row is empty. No recursion, no blow-up."""
    row = [True] + [False] * last
    for part in parts:
        if part == "**":
            first = row.index(True)
            row = [False] * first + [True] * (last + 1 - first)
            continue
        after = [False] * (last + 1)
        for cut in range(last):
            if row[cut] and _segment(part, names[cut]):
                after[cut + 1] = True
        if True not in after:
            return False
        row = after
    return True in row[1:]


def _owns(form: Shape, path: str, names: Sequence[str]) -> bool:
    """Whether a pattern of this Shape owns the file `path` (whose names are `names`)."""
    if not form.wild:   # a name or a path: the common line, with no table to fill
        if form.anchored:
            return path.startswith(form.body + "/") or (path == form.body and not form.folder)
        return any(name == form.body for name in names[:-1 if form.folder else len(names)]) if len(form.parts) == 1 else False
    parts = form.parts if form.anchored or form.parts[0] == "**" else ("**",) + form.parts
    # a folder owns every file below it: try every folder prefix of the path, and the file itself unless the pattern is a folder
    last = len(names) - 1 if form.folder else len(names)
    if sum(1 for part in parts if part != "**") > last:   # each segment but `**` takes exactly one name of the path
        return False
    return _reach(parts, names, last)


def matches(pattern: str, path: str) -> bool:
    """Whether a CODEOWNERS pattern owns a file, as git reads a pattern: a pattern with a slash in its start or middle is from the
    top, one without matches at any depth; a trailing slash is a folder, whose files are all owned; `*` does not cross a slash and
    `**` does. A pattern that is too long to read is taken to match: the file is judged by the stricter reading."""
    form = shape(pattern)
    return True if form is None else _owns(form, path, path.split("/"))


def _cannot(form: Shape) -> bool:
    """Whether a pattern with a wildcard cannot own any file of `PROBES`, for a reason that costs nothing to see: one of its segments
    has no wildcard and is none of the names the files are made of, or it starts from the top and its first segment cannot be
    `.github`."""
    for part in form.parts:
        if part != "**" and "*" not in part and "?" not in part and part not in _ANY:
            return True
    return form.anchored and form.parts[0] != "**" and not _segment(form.parts[0], ".github")


def _reaches(pattern: str, seen: Dict[str, bool], work: List[int]) -> bool:
    """Whether a pattern owns a file under `.github/` that the template ships (one of `PROBES`), or names by its path anything
    below `.github/`: the same answer as `matches` gives for each probe, without trying them all when it is known sooner. `seen`
    keeps the answers for the patterns with a wildcard, and `work` counts down how many of them may still be matched in this
    file: when it is spent a pattern is taken to match."""
    form = shape(pattern)
    if form is None:
        return True
    bare = pattern.lstrip("/")
    if bare == ".github" or bare.startswith(".github/"):
        return True   # a path below .github/ is a file of it, one of the template's or the catalog's own, there yet or not
    if not form.wild:
        if form.anchored:
            return form.body in _FOLDERS or (not form.folder and form.body in _FILES)
        return len(form.parts) == 1 and form.body in (_DIRS if form.folder else _ANY)
    if _cannot(form):
        return False
    if pattern not in seen:
        work[0] -= 1
        seen[pattern] = work[0] < 0 or any(_owns(form, path, names) for path, names in zip(PROBES, _NAMES))
    return seen[pattern]


def _fix_owners(lines: List[Line]) -> str:
    """The owners the fix names: those of the last `*` line when they are all names, else a placeholder."""
    for line in reversed(lines):
        if line.pattern == "*":
            if line.owners and all(owner_ok(word) for word in line.owners):
                return " ".join(line.owners)
            break
    return UNKNOWN


def shown(word: str) -> str:
    """A word of the file as a message shows it: made safe for a terminal and cut."""
    return printable(word)[:SHOWN]


def _no_owner(line: int, pattern: str, owners: str) -> Problem:
    """CO01, the first message: `pattern` has no owner (the line is missing, has none, or is taken over by a later one)."""
    return Problem("CO01", ERROR, FILE, line, say(RULES["CO01"].message, path=shown(pattern), maintainers=owners))


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """CO01 over `.github/CODEOWNERS`: the two lines, an owner on every line, every owner a name, and no later line that takes
    `/.github/` away from its owners. A file that is missing, a link or too large to be one is a file with neither line."""
    data = tree.read(FILE, OTHER_LIMIT + 1)
    if data is None or len(data) > OTHER_LIMIT:
        return [_no_owner(0, pattern, UNKNOWN) for pattern in REQUIRED]
    lines = parse(data.decode("utf-8", "replace"))
    fix = _fix_owners(lines)
    found: List[Problem] = []
    for wanted in REQUIRED:
        if not any(line.pattern == wanted for line in lines):
            found.append(_no_owner(0, wanted, fix))
    for line in lines:
        if not line.owners:
            found.append(_no_owner(line.number, line.pattern, fix))
        for word in line.owners:
            if not owner_ok(word):
                found.append(Problem("CO01", ERROR, FILE, line.number, say(MORE["CO01"][0], value=shown(word))))
    found += _shadowed(lines)
    return found[:200]


def _shadowed(lines: List[Line]) -> List[Problem]:
    """CO01 for `/.github/` when a line after it owns a file under `.github/` with other owners: the last matching line decides,
    so the standard's second line decides nothing. The finding is on the line that takes it away."""
    own = [index for index, line in enumerate(lines) if line.pattern == "/.github/"]
    if not own:
        return []
    ruling = lines[own[-1]]
    if not ruling.owners or not all(owner_ok(word) for word in ruling.owners):
        return []
    kept = {word.lower() for word in ruling.owners}
    seen: Dict[str, bool] = {}
    work = [WORK]
    for later in lines[own[-1] + 1:]:
        if {word.lower() for word in later.owners} == kept:
            continue
        if _reaches(later.pattern, seen, work):
            return [_no_owner(later.number, "/.github/", " ".join(ruling.owners))]
    return []


__all__ = ["FILE", "MORE", "OwnerError", "REQUIRED", "RULES", "check", "maintainers", "matches", "owner_ok", "parse"]
