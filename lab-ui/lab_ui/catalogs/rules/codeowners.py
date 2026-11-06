"""CO: the CODEOWNERS file. Two fixed lines and nothing more is asked of it: `*` and `/.github/`, each with owners, and
every owner `@user` or `@organization/team`, in ASCII. The maintainers are, by definition, the owners of the `*` line; no other
file says who they are. What GitHub itself thinks of the file (errors in it, an owner without write access, whether the plan
honours it) is read by the settings audit (`files.codeowners`), not by a rule.

The same two checks guard `catalog.py new --maintainers`: an owner string with a newline or a space would write a line of the
file of its own (`"@x\\n/.github/ @evil"`), so `maintainers()` refuses anything that is not a name before anything is rendered.

One more thing is read, because it is the point of the second line: a line that comes later in the file and can own a file under
`.github/` with owners other than those of the `/.github/` line wins over it (the last matching line decides, and one approval
from any owner on it is enough), so `/.github/` has no owner in fact. It is said with the same words: add the line again, at
the end. "Can own" is read from the start of the pattern, so a file that is not there yet counts: a pattern with no slash before
its end matches at any depth, and one from the top by its first folder.

Pure: it reads the tree, writes nothing, runs nothing, and a file of hostile lines costs a fraction of a second (a line is split
at blanks, never matched with a pattern that can backtrack, and a first folder is matched in a table the size of it and `.github`)."""

import re
from typing import List, NamedTuple, Optional, Tuple

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
MAX_PATTERN = 512     # a first folder longer than this is not matched: it counts as a shadow


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
    blank stays inside a word and makes it a name that is none. A word that starts with `#` starts a comment."""
    lines: List[Line] = []
    if text.startswith("\U0000feff"):
        text = text[1:]
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r").strip(" \t")
        if not line or line.startswith("#") or line.startswith(("[", "^[")):
            continue
        words = SEPARATE.split(line)
        end = next((at for at, word in enumerate(words) if word.startswith("#")), len(words))
        lines.append(Line(number, words[0], tuple(words[1:end])))
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


def _reaches(pattern: str) -> bool:
    """Whether a pattern can own a file under `.github/`: one with no slash before its end matches at any depth (the root, `/`, is
    all of them), and one from the top when its first folder can be `.github`."""
    body = pattern.rstrip("/")
    if "/" not in body:
        return True
    first = body.lstrip("/").partition("/")[0]
    return len(first) > MAX_PATTERN or _segment(first, ".github")


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
    for later in lines[own[-1] + 1:]:
        if {word.lower() for word in later.owners} != kept and _reaches(later.pattern):
            return [_no_owner(later.number, "/.github/", " ".join(ruling.owners))]
    return []
