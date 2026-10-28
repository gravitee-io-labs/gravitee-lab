"""The workflow rules WF01 to WF21 of the catalog repository standard: what a workflow of a catalog may do, read as text
by indentation with no YAML library. A pull request writes the file that judges it, so the rules are only an early warning and
a convenience for contributors, never the gate (2.1 principle 3); they are the same rules in the engine's own test, in
`catalog.py check` and in the pre-flight of Share.

Two sets, for two kinds of file. GENERIC is what any workflow should satisfy, and the engine's own test imports it. LANES is
written for the lanes of a catalog (which file may read which secret, which job may write, the conditions of the publish
lane) and runs on catalogs only: the engine's `e2e.yml` and `lab-images.yml` trip it by design.

    check(tree, ctx) -> [Problem]            every workflow and local action of a catalog, both sets
    problems(path, text, rules, standard)    one file, any set of codes; `standard` is {"repository", "ref"} for WF15
    run_blocks(text), jobs(text), triggers(text)   the readers of the engine's test, now over the outline below

The reader is an outline by indentation (`_Reader`), not one regular expression per line: a rule that looks for `^      timeout`
at six spaces is blind to a file written with four. Whatever the outline cannot read with certainty is WF21, so a workflow that
hides what the rules look at is refused instead of passed: a flow mapping, an anchor, a tag, a quoted key, a duplicate key, a
block scalar with a header it does not know, a control character, a line break YAML takes and a line reading would not.

A file is a workflow or an action by its folder: everything in `.github/workflows/` is a workflow for GitHub, whatever it is
called, and so for the rules (a workflow called `action.yml` breaks WF01 like any other). The name decides only for a file
outside both folders.

What GitHub reads is what the rules read. A double-quoted scalar is decoded (`s\\x65crets` is `secrets`) before the rules that scan
text see it; a secret is read inside `${{ }}` and in an `if:`, wherever a value can be (a key, a list item, a flow list), and a
file called `secrets.yaml` or a sentence that holds the word is no read.

Hostile input is the case this module is written for. It never executes, imports or follows anything of the tree it reads. A
line of 1 MB, a file of 20,000 short lines and a pile of quotes, brackets or `!contains(` each take a fraction of a second, under
every name a file can have (the publish lane has rules that `ci.yml` has not). Every scan moves forward: `str.find`, a regular
expression with no nested repetition, a stack for the parentheses of an expression. What cannot be linear is bounded: nesting
stops at MAX_DEPTH, a flow collection longer than MAX_FLOW is not read, the parentheses of one expression are paired up to
MAX_PARENS (the rest of an expression that turns a test over counts as turned over), a double-quoted scalar with more than
MAX_ESCAPES backslashes is refused (WF21), and a rule reports at most MAX_PER_RULE findings per file. A key that is spelt with
other letter case than the rules expect counts for what a rule forbids and not for what it asks: `Permissions:` does not make a
token read-only, and `Continue-On-Error: true` is still found. The rules are a text check of the spellings that are met, an early
warning and not a proof: the code owner reads the workflows too."""

import bisect
import json
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

try:
    from ..report import Problem
except ImportError:   # report.py lands in parallel: the same five fields in the same order
    class Problem(NamedTuple):
        code: str
        severity: str
        path: str
        line: int
        message: str

__all__ = ["ALL", "FAMILY", "GENERIC", "LANES", "MAY_WRITE", "MESSAGES", "Problem", "RULES", "Rule", "check", "jobs", "problems",
           "run_blocks", "triggers", "unreadable"]

MAX_LINES = 20000       # a workflow longer than this is not read: WF21
MAX_DEPTH = 40          # levels of nesting
MAX_FLOW = 2048         # characters of a flow collection `[a, b]` that are read
MAX_KEY = 512           # characters of a key
MAX_PER_RULE = 50       # findings of one rule in one file
MAX_PIPES = 1000        # pipes in one pipeline that starts with a download
MAX_FILES = 100         # workflows and actions that `check` reads in one tree; the others are WF21
MAX_ESCAPES = 20000     # backslashes in one double-quoted scalar that are decoded; more is WF21
FILE_LIMIT = 1024 * 1024
STANDARD_LIMIT = 4096   # what `.github/standard.json` may weigh (SD04)

EVENTS = ("pull_request", "push", "schedule", "workflow_dispatch", "workflow_call")
RUNNERS = ("ubuntu-24.04", "ubuntu-24.04-arm")
# The keys whose value must be written in block style: a rule reads them by indentation (WF21)
SENSITIVE = ("on", "permissions", "secrets", "runs-on", "uses", "environment")
# The keys GitHub knows at the three levels the rules read. The same word with other letters is refused (WF21): the rules ask
# for an exact spelling, and a parser that folds case would read what they do not
KNOWN = {
    "workflow": ("name", "run-name", "on", "permissions", "env", "defaults", "concurrency", "jobs"),
    "job": ("name", "permissions", "needs", "if", "runs-on", "snapshot", "environment", "concurrency", "outputs", "env",
            "defaults", "steps", "timeout-minutes", "strategy", "continue-on-error", "container", "services", "uses", "with",
            "secrets"),
    "step": ("id", "if", "name", "uses", "run", "working-directory", "shell", "with", "env", "continue-on-error",
             "timeout-minutes"),
}


class Rule(NamedTuple):
    code: str
    severity: str
    rule: str        # the rule, as the table of 6.4 words it
    message: str     # the message, with {file}, {line} and the fields of the rule


def _table(rows: Iterable[Tuple[str, str, str]]) -> Dict[str, Rule]:
    return {code: Rule(code, "error", rule, message) for code, rule, message in rows}


RULES = _table([
    ("WF01", "Events are only `pull_request`, `push`, `schedule`, `workflow_dispatch`, `workflow_call`",
     "{file}: the trigger {event} is not allowed. Use pull_request: pull_request_target and workflow_run run with "
     "secrets on content from outsiders."),
    ("WF02", "Every `uses:` is `./...` or `owner/repo[/path]@<40 hex>`",
     "{file}:{line}: {action} is not pinned to a full commit. Pin it as owner/repo@<40 hex> # tag; Dependabot "
     "proposes the next one."),
    ("WF03", "The top level says `permissions:` then `contents: read` and nothing else",
     "{file}: give the token the least it needs: permissions: contents: read at the top."),
    ("WF04", "No `${{` inside a `run:`",
     "{file}:{line}: ${{ }} inside run:. Pass the value through env: and read the variable; a title or a branch "
     "name would become a command."),
    ("WF05", "Every job has `timeout-minutes` (a job that calls a reusable workflow is exempt)",
     "{file}: job {job} has no timeout-minutes. Add one, so that a stuck build ends."),
    ("WF06", "`runs-on` is `ubuntu-24.04`, `ubuntu-24.04-arm`, or exactly `${{ matrix.runner }}` where the job's "
             "`matrix` is `fromJSON(needs.<job>.outputs.matrix)` of a job of the same file, or lists only those "
             "two labels",
     "{file}: job {job} runs on {label}. Use ubuntu-24.04 or ubuntu-24.04-arm, or a matrix that the plan job of "
     "this file makes: a self-hosted runner keeps what a pull request leaves on it."),
    ("WF07", "Every `actions/checkout` says `persist-credentials: false`",
     "{file}:{line}: actions/checkout leaves its token in .git. Add persist-credentials: false."),
    ("WF08", "Each secret is read only where a rule allows: `LAB_DEPLOY_KEY` as the `key:` of the `lab` action or "
             "as the same-named entry of a caller's `secrets:`; `GITHUB_TOKEN` or `REGISTRY_TOKEN` as the "
             "`password:` of `docker/login-action` in `publish.yml` (and `catalog-publish.yml`, its reusable "
             "counterpart in the Lab); `GRAVITEE_LICENSE` in exactly one step of `e2e.yml` and of `versions.yml`; "
             "`VERSIONS_DEPLOY_KEY` in the step of `versions.yml` that runs `catalog.py ci versions-release`. Never "
             "`secrets: inherit`",
     "{file}:{line}: secrets.{name} is read where no rule allows it. Pass it by name to the one step that needs "
     "it."),
    ("WF09", "Write permissions only where the file may have them: none in `ci.yml` and `e2e.yml`; `issues` in the "
             "job `report` of `nightly.yml` and of `versions.yml`; `packages` in `images` and `attest`, `id-token` and "
             "`attestations` in `attest`, `contents` in `release`, of `publish.yml`; in the reusable form the single call "
             "job of `publish.yml` holds their union",
     "{file}: job {job} holds {permission}: write. Only publish.yml's images, attest and release jobs, and the "
     "report jobs of nightly.yml and versions.yml, may write."),
    ("WF10", "A job with steps and `packages: write` declares an `environment` (a call job of the reusable form is "
             "exempt: the jobs of its callee are checked)",
     "{file}: job {job} writes packages without an environment. Add environment: publish."),
    ("WF11", "A `concurrency:` group exists (not in a reusable file)",
     "{file}: add a concurrency group, so that a push does not pile runs up."),
    ("WF12", "No download piped into a shell",
     "{file}:{line}: a download is piped into a shell. Download it, check a digest, then run it."),
    ("WF13", "`id-token: write` appears in the job `attest` of `publish.yml` and `catalog-publish.yml` only, and "
             "no step exports the runtime token of Actions (`ACTIONS_RUNTIME_TOKEN`, `ACTIONS_CACHE_URL`, "
             "`ACTIONS_RESULTS_URL`, `ghaction-github-runtime`)",
     "{file}: the identity token and the runtime token of Actions go to the attest job of publish.yml only."),
    ("WF14", "No `continue-on-error: true`",
     "{file}:{line}: continue-on-error hides a failure. Make the step decide its own exit code."),
    ("WF15", "Reusable form: the Lab repository and commit named by a caller equal `lab.repository` and `lab.ref` "
             "of `standard.json`",
     "{file}: the Lab pinned here is not lab.ref of .github/standard.json. Fix: catalog.py upgrade --lab-ref "
     "{ref}."),
    ("WF16", "Reusable form: a caller passes `LAB_DEPLOY_KEY` and nothing else",
     "{file}: pass the Lab key by name and nothing else; secrets: inherit hands over every secret."),
    ("WF17", "No cache of any kind: no `actions/cache`, no `cache:` input of a `setup-*` action, no `cache-from` "
             "or `cache-to`, no `type=gha` or `type=registry`",
     "{file}:{line}: a cache lets a pull request write what main later reads. Remove it."),
    ("WF18", "In `publish.yml` and `catalog-publish.yml` a `needs.<job>.result` is compared with `== 'success'` or "
             '`contains(fromJSON(\'["success","skipped"]\'), ...)`, never with `!=` (a canceled or timed-out job '
             "is neither a failure nor a success)",
     "{file}:{line}: needs.{job}.result is tested with !=. Fix: name what you accept: "
     'contains(fromJSON(\'["success","skipped"]\'), needs.{job}.result).'),
    ("WF19", "No write to `$GITHUB_ENV` or `$GITHUB_PATH`, and no `${{` inside the `script:` of "
             "`actions/github-script`",
     "{file}:{line}: this lets data from outside choose what runs next. Fix: pass the value through env: to the "
     "one step that needs it."),
    ("WF20", "No `ACTIONS_ALLOW_UNSECURE_COMMANDS`; no `run-id` or `github-token` on `actions/download-artifact` "
             "(an artifact of another run is data that someone may have written); no dynamic access to secrets: "
             "`secrets[`, `toJSON(secrets)`, `toJson(secrets)`",
     "{file}:{line}: {what}. Fix: name the one secret a step needs, and download only the artifact of this run."),
    ("WF21", "Fail closed. The rules read by indentation, so a workflow that hides what they read is refused: a "
             "flow mapping or sequence as the value of `on`, `permissions`, `secrets`, `runs-on`, `uses` or "
             "`environment`; `permissions: write-all` or `read-all`; an anchor, an alias or a tag (`&x`, `*x`, "
             "`!!x`); a quoted key",
     "{file}:{line}: this is written so that the rules cannot read it. Fix: write it in block style, one key per "
     "line."),
])
MESSAGES = {code: rule.message for code, rule in RULES.items()}

GENERIC = frozenset(("WF01", "WF02", "WF03", "WF04", "WF05", "WF07", "WF11", "WF12", "WF14", "WF17", "WF19", "WF20", "WF21"))
LANES = frozenset(("WF06", "WF08", "WF09", "WF10", "WF13", "WF15", "WF16", "WF18"))
ALL = GENERIC | LANES

# The workflows the standard ships, by file name, and the family each belongs to: a file of the Lab that a caller calls
# (`catalog-*.yml`) has the rights of the file that calls it. Any other name has the rights of none.
FAMILY = {"ci.yml": "ci", "catalog-ci.yml": "ci", "publish.yml": "publish", "catalog-publish.yml": "publish",
          "nightly.yml": "nightly", "catalog-nightly.yml": "nightly", "e2e.yml": "e2e", "catalog-e2e.yml": "e2e",
          "versions.yml": "versions", "catalog-versions.yml": "versions"}
# The jobs that may hold a write permission, and which. A job that calls a reusable workflow holds the union of its file
# (the reusable form, 5.11: the callee cannot hold more than its caller)
MAY_WRITE = {
    "publish": {"images": ("packages",), "attest": ("packages", "id-token", "attestations"), "release": ("contents",)},
    "nightly": {"report": ("issues",)},
    "versions": {"report": ("issues",)},
}

# Only the root of the catalog is read as workflows: GitHub runs no other, and a folder of the content that holds a `.github` is
# content. A local action may be at any depth below `.github/actions/`.
_WORKFLOW_FILE = re.compile(r"\.github/workflows/[^/]+\.(?i:yml|yaml)\Z")
_ACTION_FILE = re.compile(r"\.github/actions/(?:[^/]+/)+action\.(?i:yml|yaml)\Z")
_REGULAR = ("100644", "100755")


# ---------------------------------------------------------------- scans that only move forward

_BREAKS = re.compile(r"\r\n|\r|\n")
# What a workflow may not contain: the controls, the line breaks that YAML takes and a line reading would not (next line, line
# and paragraph separators), the delete character, and a byte order mark in the middle of the file
_ODD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029\ufeff]")
_BLOCK_HEADER = re.compile(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?")
_FLOW_TOKEN = re.compile(r"""[\[\]{}'"]""")
_FLOW_SPLIT = re.compile(r"""[\[\]{}'",]""")
_COLON = re.compile(r":(?=[ \t]|\Z)")
_COMMENT = re.compile(r"[ \t]#")


# A quoted scalar from just after its opening quote to its closing one. Each alternative starts with a character the others
# exclude, so there is one way to read any text and a quote that never closes costs one pass, not one for each quote
_SINGLE = re.compile(r"[^']*(?:''[^']*)*'(?!')")
_DOUBLE = re.compile(r'[^"\\]*(?:\\.[^"\\]*)*"', re.S)


def _end_single(text: str, start: int) -> int:
    """The index of the quote that closes a single-quoted scalar whose content starts at `start`, or -1. `''` is a quote."""
    found = _SINGLE.match(text, start)
    return found.end() - 1 if found else -1


def _end_double(text: str, start: int) -> int:
    """The same for a double-quoted scalar: a backslash takes the character after it."""
    found = _DOUBLE.match(text, start)
    return found.end() - 1 if found else -1


def _flow_scan(text: str) -> Tuple[int, int]:
    """(the index of the bracket that closes text[0], the deepest level) of a flow collection on one line; -1 when it does not
    close within MAX_FLOW characters."""
    window = text[:MAX_FLOW]
    depth = deepest = pos = 0
    while True:
        found = _FLOW_TOKEN.search(window, pos)
        if found is None:
            return -1, deepest
        char, at = found.group(), found.start()
        if char in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif char in "]}":
            depth -= 1
            if depth <= 0:
                return at, deepest
        else:
            end = (_end_single if char == "'" else _end_double)(window, at + 1)
            if end < 0:
                return -1, deepest
            at = end
        pos = at + 1


def _flow_items(text: str) -> List[str]:
    """The items of a flow collection with its brackets, split at the commas of its own level, quotes respected."""
    inner = text[1:-1]
    items, start, depth, pos = [], 0, 0, 0
    while True:
        found = _FLOW_SPLIT.search(inner, pos)
        if found is None:
            break
        char, at = found.group(), found.start()
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ",":
            if depth == 0:
                items.append(inner[start:at].strip())
                start = at + 1
        else:
            end = (_end_single if char == "'" else _end_double)(inner, at + 1)
            if end < 0:
                break
            at = end
        pos = at + 1
    items.append(inner[start:].strip())
    return [item for item in items if item]


def _unquote(text: str) -> str:
    """A flow item as a word: its quotes taken off."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1].replace("''", "'") if text[0] == "'" else _decode_double([text[1:-1]])
    return text


def _flow_pairs(text: str) -> List[Tuple[str, str]]:
    """(key, value) of each item of a flow mapping, quotes taken off; an item with no colon has an empty value."""
    pairs = []
    for item in _flow_items(text):
        if item[:1] in ("'", '"'):
            end = (_end_single if item[0] == "'" else _end_double)(item, 1)
            if end > 0:
                rest = item[end + 1:].lstrip(" \t")
                pairs.append((_unquote(item[:end + 1]), _unquote(rest[1:].strip()) if rest[:1] == ":" else ""))
                continue
        found = _COLON.search(item)
        if found is None:
            pairs.append((item, ""))
        else:
            pairs.append((item[:found.start()].strip(), _unquote(item[found.end():].strip())))
    return pairs


_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r", "e": "\x1b",
            " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}
# One escape: `\xNN`, then the four-digit and the eight-digit forms, then any other character after a backslash. Each alternative
# starts with a different letter, so there is one way to read a text and the scan only moves forward
_ESCAPE = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|(.))", re.S)
# The escapes that spell `$` and `{`, for a scalar with more backslashes than MAX_ESCAPES (which WF21 refuses anyway). The
# patterns are written in two halves so that no tool that decodes escapes in source text can turn them into the characters
_DOLLAR = re.compile("\\\\(?:x24|u00" + "24|U000000" + "24)")
_BRACE = re.compile("\\\\(?:x7[bB]|u007[bB]|U0000007[bB])")


def _unescape(found) -> str:
    digits = found.group(1) or found.group(2) or found.group(3)
    if digits is not None:
        try:
            return chr(int(digits, 16))
        except (ValueError, OverflowError):    # beyond the last code point
            return found.group()
    code = found.group(4)
    return found.group() if code in ("x", "u", "U") else _ESCAPES.get(code, code)


def _decode_double(pieces: List[str]) -> str:
    """The text of a double-quoted scalar given by its lines: escapes decoded (`\\x7B` is `{`), a backslash at the end of a line
    joins it to the next, any other line break folds to a space. A script that hides `${{` or a secret that way is found by what
    it says. A scalar with more than MAX_ESCAPES backslashes only has the escapes that spell `$` and `{` decoded, which takes no
    loop; such a scalar is refused (WF21) and the text is only read as far as it is cheap."""
    if sum(piece.count("\\") for piece in pieces) > MAX_ESCAPES:
        text = _BRACE.sub("{", _DOLLAR.sub("$", "\n".join(pieces)))
        return re.sub(r"\\\n[ \t]*", "", text)
    out = []
    for number, piece in enumerate(pieces):
        text = piece if number == 0 else piece.lstrip(" \t")
        # a backslash that nothing escapes ends the line: the next line goes on without a space
        joined = (len(text) - len(text.rstrip("\\"))) % 2 == 1
        if joined:
            text = text[:-1]
        out.append(_ESCAPE.sub(_unescape, text) if "\\" in text else text)
        if not joined and number < len(pieces) - 1:
            out.append(" ")
    return "".join(out)


# ---------------------------------------------------------------- the outline

class _Node(object):
    """A value: a mapping (`entries`), a sequence (`items`), a scalar (`text`, and the lines it takes in `parts`), a flow
    collection on one line (`text`), or nothing (`null`). `style` says how a scalar is written."""
    __slots__ = ("kind", "line", "col", "style", "entries", "items", "parts", "text", "owner", "parent")

    def __init__(self, kind: str, line: int, col: int, style: str = "") -> None:
        self.kind, self.line, self.col, self.style = kind, line, col, style
        self.entries: List["_Entry"] = []
        self.items: List["_Node"] = []
        self.parts: List[Tuple[int, str]] = []
        self.text = ""
        self.owner: Optional["_Entry"] = None    # the entry this node is the value of
        self.parent: Optional["_Node"] = None    # the sequence this node is an item of


class _Entry(object):
    __slots__ = ("name", "line", "col", "value", "map", "quoted")

    def __init__(self, name: str, line: int, col: int, holder: _Node, quoted: bool) -> None:
        self.name, self.line, self.col, self.map, self.quoted = name, line, col, holder, quoted
        self.value = _Node("null", line, col)
        self.value.owner = self


class _Frame(object):
    __slots__ = ("kind", "col", "node", "pending", "names")

    def __init__(self, kind: str, col: int, node: _Node) -> None:
        self.kind, self.col, self.node = kind, col, node
        self.pending = None      # the entry or the item that has no value yet: a deeper line is its value
        self.names: Set[str] = set()


class _Stop(Exception):
    """The document is nested too deeply to be read."""


class _Reader(object):
    """Reads a workflow as an outline of mappings, sequences and scalars, and notes everything it cannot read for certain."""

    def __init__(self, text: str) -> None:
        self.bad: Dict[int, str] = {}
        self.content: List[Tuple[int, str]] = []    # (line, its text without the comment), for the rules that scan text
        self.root: Optional[_Node] = None
        self.stack: List[_Frame] = []
        self.started = False
        self.ended = False                           # a `...` was read
        self.dead: Dict[str, bool] = {}              # a quote that has no closing quote to the end of the file
        if text.startswith("\ufeff"):
            text = text[1:]
        self.lines = _BREAKS.split(text)
        if len(self.lines) > MAX_LINES:
            self.flag(MAX_LINES, "longer than %d lines" % MAX_LINES)
            del self.lines[MAX_LINES:]
        for number, row in enumerate(self.lines, 1):
            if _ODD.search(row):
                self.flag(number, "a control character, or a line break that YAML takes and a line reading would not")
        try:
            self._read()
        except _Stop:
            pass
        if self.root is None:
            self.root = _Node("null", 1, 0)

    def flag(self, line: int, reason: str) -> None:
        self.bad.setdefault(line, reason)

    # ------------------------------------------------------------ lines

    def _read(self) -> None:
        lines, i, count = self.lines, 0, len(self.lines)
        while i < count:
            row = lines[i]
            text = row.lstrip(" ")
            col = len(row) - len(text)
            if not text or text[0] == "#":
                i += 1
                continue
            if text[0] == "\t":
                inner = text.lstrip(" \t")
                if not inner or inner[0] == "#":
                    i += 1
                    continue
                self.flag(i + 1, "a tab in the indentation")
                col, text = len(row) - len(inner), inner
            if col == 0 and text[:3] in ("---", "...") and text[3:4] in ("", " ", "\t"):
                tail = text[3:].lstrip(" \t")
                if (text[:3] == "---" and self.started) or (tail and tail[0] != "#"):
                    self.flag(i + 1, "a second document, or a document marker with something after it")
                self.ended = self.ended or text[:3] == "..."
                i += 1
                continue
            if col == 0 and text[0] == "%":
                self.flag(i + 1, "a directive")
                i += 1
                continue
            if self.ended:
                self.flag(i + 1, "something after the end of the document")
            i = self._line(i, col, text)

    def _place(self, number: int, col: int, dash: bool) -> Optional[Tuple[_Frame, bool]]:
        """The frame a line belongs to, and whether the line is the value of that frame's pending entry or item."""
        stack = self.stack
        while True:
            if not stack:
                if self.started:
                    self.flag(number, "a line to the left of the document")
                    return None
                self.started = True
                node = _Node("seq" if dash else "map", number, col)
                frame = _Frame(node.kind, col, node)
                self.root = node
                stack.append(frame)
                if dash:
                    self.flag(number, "a sequence where a mapping belongs")
                return frame, False
            top = stack[-1]
            if col > top.col:
                if top.pending is None:
                    self.flag(number, "indentation that belongs to no key")
                    return None
                return top, True
            if col == top.col:
                if top.kind == "map":
                    if not dash:
                        return top, False
                    if top.pending is not None:
                        return top, True         # a sequence at the column of its key
                    self.flag(number, "a sequence item among the keys of a mapping")
                    return None
                if dash:
                    return top, False
                stack.pop()
                continue
            stack.pop()

    def _open(self, frame: _Frame, kind: str, number: int, col: int) -> _Frame:
        """Opens the mapping or sequence that is the value of the frame's pending entry or item."""
        if len(self.stack) >= MAX_DEPTH:
            self.flag(number, "nested more than %d levels" % MAX_DEPTH)
            raise _Stop()
        node = _Node(kind, number, col)
        self._give(frame, node)
        opened = _Frame(kind, col, node)
        self.stack.append(opened)
        return opened

    @staticmethod
    def _give(frame: _Frame, node: _Node) -> None:
        """Makes `node` the value of the frame's pending entry, or the pending item of its sequence."""
        pending = frame.pending
        if isinstance(pending, _Entry):
            pending.value = node
            node.owner = pending
        else:
            node.parent = frame.node
            frame.node.items[-1] = node
        frame.pending = None

    def _line(self, i: int, col: int, text: str) -> int:
        """Places the logical line lines[i], whose content `text` starts at column `col`. Returns the next line to read."""
        number = i + 1
        dash = text[0] == "-" and text[1:2] in ("", " ", "\t")
        slot = self._place(number, col, dash)
        if slot is None:
            return i + 1
        frame, child = slot
        if dash:
            if child:
                frame = self._open(frame, "seq", number, col)
            item = _Node("null", number, col)
            item.parent = frame.node
            frame.node.items.append(item)
            frame.pending = item
            rest = text[1:]
            inner = rest.lstrip(" \t")
            if not inner or inner[0] == "#":
                return i + 1
            return self._line(i, col + 1 + len(rest) - len(inner), inner)
        key = _split_key(text)
        if key is None:
            if not child:
                self.flag(number, "a line that is neither an entry nor an item")
                return i + 1
            here = len(self.content)          # the line comes before the lines its value goes on to take
            self.content.append((number, ""))
            node, following, keep = self._value(i, text, col, frame.col)
            self._give(frame, node)
            self.content[here] = (number, text[:keep])
            return following
        name, start, quoted = key
        if child:
            frame = self._open(frame, "map", number, col)
        entry = _Entry(name, number, col, frame.node, quoted)
        if name in frame.names:
            self.flag(number, "a key that appears twice")
        frame.names.add(name)
        frame.node.entries.append(entry)
        frame.pending = None
        if quoted:
            self.flag(number, "a quoted key")
        elif name.startswith("<<"):
            self.flag(number, "a merge key")
        rest = text[start:]
        inner = rest.lstrip(" \t")
        if not inner or inner[0] == "#":
            frame.pending = entry
            self.content.append((number, text[:start]))
            return i + 1
        here = len(self.content)
        self.content.append((number, ""))
        node, following, keep = self._value(i, inner, col + start + len(rest) - len(inner), col)
        entry.value = node
        node.owner = entry
        self.content[here] = (number, text[:start] + " " + inner[:keep])
        if node.kind == "flow" and name.lower() in SENSITIVE:
            self.flag(number, "a flow collection as the value of %s" % name)
        return following

    # ------------------------------------------------------------ values

    def _value(self, i: int, text: str, col: int, owner: int) -> Tuple[_Node, int, int]:
        """The scalar or flow collection that `text` starts, on lines[i]. `owner` is the column of its key or its dash: a block
        scalar takes the lines deeper than that, and so does a plain one. Returns the node, the next line to read, and how many
        characters of `text` are not a comment."""
        number, first = i + 1, text[0]
        if first in "|>":
            header = _BLOCK_HEADER.match(text)
            tail = text[header.end():] if header else "?"
            if header and (not tail.strip(" \t") or (tail[:1] in " \t" and tail.lstrip(" \t")[:1] == "#")):
                return self._block(i, col, owner, header.end())
            self.flag(number, "a block scalar header the rules do not know")
        elif first in "'\"":
            return self._quoted(i, text, col)
        elif first in "[{":
            found = self._flow(i, text, col)
            if found is not None:
                return found
        elif first in "&*!":
            self.flag(number, "an anchor, an alias or a tag")
        return self._plain(i, text, col, owner)

    def _block(self, i: int, col: int, owner: int, keep: int) -> Tuple[_Node, int, int]:
        node = _Node("scalar", i + 1, col, "block")
        lines, j = self.lines, i + 1
        while j < len(lines):
            row = lines[j]
            if row.strip() and len(row) - len(row.lstrip(" ")) <= owner:
                break
            node.parts.append((j + 1, row))
            self.content.append((j + 1, row))
            j += 1
        node.text = "\n".join(row for _, row in node.parts)
        return node, j, keep

    def _closing(self, i: int, find, quote: str) -> Optional[int]:
        """The line that holds the quote closing a scalar that line i opens, or None. A file that has no such quote from here
        on is remembered, so that a pile of open quotes does not read the file again for each."""
        if self.dead.get(quote):
            return None
        for j in range(i + 1, len(self.lines)):
            if find(self.lines[j], 0) >= 0:
                return j
        self.dead[quote] = True
        return None

    def _quoted(self, i: int, text: str, col: int) -> Tuple[_Node, int, int]:
        number, quote = i + 1, text[0]
        find = _end_single if quote == "'" else _end_double
        node = _Node("scalar", number, col, "single" if quote == "'" else "double")
        end = find(text, 1)
        keep, last = len(text), i
        if end >= 0:
            node.parts.append((number, text[:end + 1]))
            pieces, tail, keep = [text[1:end]], text[end + 1:], end + 1
        else:
            node.parts.append((number, text))
            pieces, tail = [text[1:]], ""
            close = self._closing(i, find, quote)
            if close is None:
                self.flag(number, "a quote that is never closed")
            else:
                for j in range(i + 1, close):
                    pieces.append(self.lines[j])
                    node.parts.append((j + 1, self.lines[j]))
                    self.content.append((j + 1, self.lines[j]))
                row = self.lines[close]
                end = find(row, 0)
                pieces.append(row[:end])
                node.parts.append((close + 1, row[:end + 1]))
                self.content.append((close + 1, row[:end + 1]))
                last, tail = close, row[end + 1:]
        if tail.strip(" \t") and tail.lstrip(" \t")[:1] != "#":
            self.flag(last + 1, "text after a quoted scalar")
        if quote == "'":
            node.text = " ".join(piece.strip() if k else piece for k, piece in enumerate(pieces)).replace("''", "'")
        else:
            node.text = _decode_double(pieces)
            if any("\\" in piece for piece in pieces):
                # GitHub reads the decoded text: the rules that scan the text of the file read it too, on the line the scalar starts
                self.content.append((number, node.text))
                if sum(piece.count("\\") for piece in pieces) > MAX_ESCAPES:
                    self.flag(number, "a double-quoted scalar with more than %d backslashes" % MAX_ESCAPES)
        return node, last + 1, keep

    def _flow(self, i: int, text: str, col: int) -> Optional[Tuple[_Node, int, int]]:
        number = i + 1
        close, deepest = _flow_scan(text)
        if close < 0:
            self.flag(number, "a flow collection that is not closed on its line, or is too long")
            return None
        node = _Node("flow", number, col, "flow")
        node.text = text[:close + 1]
        node.parts.append((number, node.text))
        tail = text[close + 1:]
        if tail.strip(" \t") and tail.lstrip(" \t")[:1] != "#":
            self.flag(number, "text after a flow collection")
        if deepest > 1:
            self.flag(number, "a flow collection inside a flow collection")
        elif node.text[0] == "{" and node.text[1:-1].strip():
            self.flag(number, "a flow mapping")
        items = _flow_items(node.text)
        if any(item[:1] in ("&", "*", "!") for item in items):
            self.flag(number, "an anchor, an alias or a tag")
        if "\\" in node.text and node.text[0] == "[":
            self.content.append((number, "\n".join(_unquote(item) for item in items)))    # what GitHub reads, as in _quoted
        return node, i + 1, close + 1

    def _plain(self, i: int, text: str, col: int, owner: int) -> Tuple[_Node, int, int]:
        node = _Node("scalar", i + 1, col, "plain")
        found = _COMMENT.search(text)
        keep = found.start() if found else len(text)
        node.parts.append((i + 1, text[:keep].rstrip(" \t")))
        lines, j, last = self.lines, i + 1, i + 1
        while j < len(lines):
            row = lines[j]
            inner = row.lstrip(" \t")
            if not inner:
                j += 1
                continue
            if inner[0] == "#" or len(row) - len(row.lstrip(" ")) <= owner:
                break
            found = _COMMENT.search(inner)
            piece = (inner[:found.start()] if found else inner).rstrip(" \t")
            if _COLON.search(piece):
                self.flag(j + 1, "a key under a value")
            node.parts.append((j + 1, piece))
            self.content.append((j + 1, piece))
            j += 1
            last = j
        node.text = " ".join(piece for _, piece in node.parts)
        return node, last, keep


def _split_key(text: str) -> Optional[Tuple[str, int, bool]]:
    """(the key, the index after its colon, whether it was quoted) when `text` starts a mapping entry, else None."""
    first = text[0]
    if first in "'\"":
        end = (_end_single if first == "'" else _end_double)(text, 1)
        if end < 0:
            return None
        after = text[end + 1:]
        inner = after.lstrip(" \t")
        if inner[:1] == ":" and inner[1:2] in ("", " ", "\t"):
            return text[1:end], end + 1 + len(after) - len(inner) + 1, True
        return None
    if first in "[{&*!|>%@`?":
        return None
    found = _COLON.search(text)
    if found is None:
        return None
    key = text[:found.start()].rstrip(" \t")
    if not key or len(key) > MAX_KEY or _COMMENT.search(key):
        return None
    return key, found.end(), False


# ---------------------------------------------------------------- the document

class _Job(NamedTuple):
    name: str
    line: int
    node: _Node
    entry: _Entry


class _Doc(object):
    """One file read: the outline, what the rules ask of it, and the reasons it could not read some of it."""

    def __init__(self, path: str, text: str, standard: Optional[dict] = None, is_action: bool = False) -> None:
        reader = _Reader(text)
        self.path = path
        self.lines = reader.lines
        self.root: _Node = reader.root
        self.kind = "action" if is_action else "workflow"
        self.name = path.rsplit("/", 1)[-1]
        self.family = None if is_action else FAMILY.get(self.name)
        self.standard = standard
        self.unreadable: Dict[int, str] = dict(reader.bad)
        self.content = reader.content
        self.entries: List[_Entry] = []
        self.leaves: List[Tuple[str, _Node]] = []    # (the key that holds it, in lower case; the value) of every scalar and flow
        self.jobs: List[_Job] = []
        self._blob: Optional[Tuple[str, List[int]]] = None
        self._collect()

    def _collect(self) -> None:
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.kind == "map":
                self.entries.extend(node.entries)
                stack.extend(entry.value for entry in reversed(node.entries))
                for entry in node.entries:
                    if entry.value.kind in ("scalar", "flow"):
                        self.leaves.append((entry.name.lower(), entry.value))
                    if "${{" in entry.name:    # an expression where a name belongs is text to read like any other
                        key = _Node("scalar", entry.line, entry.col, "plain")
                        key.parts, key.text = [(entry.line, entry.name)], entry.name
                        self.leaves.append((entry.name.lower(), key))
            elif node.kind == "seq":
                stack.extend(reversed(node.items))
                holder = node.owner.name.lower() if node.owner is not None else ""
                self.leaves.extend((holder, item) for item in node.items if item.kind in ("scalar", "flow"))
        self.entries.sort(key=lambda entry: (entry.line, entry.col))
        self.leaves.sort(key=lambda leaf: (leaf[1].line, leaf[1].col))
        if self.root.kind == "map" and self.kind == "workflow":
            for entry in self.root.entries:
                if entry.name == "jobs" and entry.value.kind == "map":
                    self.jobs.extend(_Job(job.name, job.line, job.value, job) for job in entry.value.entries
                                     if job.value.kind == "map")
        levels = [("step", self._steps())]
        if self.kind == "workflow":
            levels += [("workflow", [self.root]), ("job", [job.node for job in self.jobs])]
        for level, maps in levels:
            spelling = {name.lower(): name for name in KNOWN[level]}
            for node in maps:
                for entry in node.entries:
                    right = spelling.get(entry.name.lower())
                    if right is not None and right != entry.name:
                        self.unreadable.setdefault(entry.line, "%s is spelt with other letters than %s" % (entry.name, right))
        for entry in self.entries:
            if entry.name.lower() == "permissions" and entry.value.kind == "scalar" \
                    and entry.value.text.strip().lower() in ("write-all", "read-all"):
                self.unreadable.setdefault(entry.line, "permissions: %s" % entry.value.text.strip())

    def _steps(self) -> List[_Node]:
        out = []
        for entry in self.entries:
            if entry.name == "steps" and entry.value.kind == "seq":
                out.extend(item for item in entry.value.items if item.kind == "map")
        return out

    # ------------------------------------------------------------ what the rules ask

    @staticmethod
    def get(node: _Node, name: str) -> Optional[_Entry]:
        """The entry of a mapping with this exact name (the first): what a rule asks for."""
        if node.kind == "map":
            for entry in node.entries:
                if entry.name == name:
                    return entry
        return None

    @staticmethod
    def named(node: _Node, name: str) -> List[_Entry]:
        """The entries of a mapping with this name in any letter case: what a rule forbids."""
        if node.kind != "map":
            return []
        return [entry for entry in node.entries if entry.name.lower() == name]

    def top(self, name: str) -> Optional[_Entry]:
        return self.get(self.root, name)

    def events(self) -> List[Tuple[str, int]]:
        """(event, line) of every event `on:` names, in the spellings a workflow may use. The key is read in any letter case:
        an event hidden under `On:` is still an event."""
        found = []
        for entry in self.named(self.root, "on"):
            node = entry.value
            if node.kind == "scalar":
                found.append((node.text, entry.line))
            elif node.kind == "map":
                found.extend((each.name, each.line) for each in node.entries)
            elif node.kind == "seq":
                found.extend((item.text, item.line) for item in node.items if item.kind == "scalar")
            elif node.kind == "flow" and node.text.startswith("{"):
                found.extend((key, entry.line) for key, _ in _flow_pairs(node.text))
            elif node.kind == "flow":
                found.extend((_unquote(item), entry.line) for item in _flow_items(node.text))
        return found

    def blob(self) -> Tuple[str, List[int]]:
        """All the text that is not a comment as one string, and where each line starts in it."""
        if self._blob is None:
            starts, size = [], 0
            for _, text in self.content:
                starts.append(size)
                size += len(text) + 1
            self._blob = ("\n".join(text for _, text in self.content), starts)
        return self._blob

    def line_at(self, offset: int) -> int:
        """The line of the file that holds this offset of the blob."""
        starts = self.blob()[1]
        return self.content[max(bisect.bisect_right(starts, offset) - 1, 0)][0]

    def scalars(self) -> Iterable[Tuple[_Entry, _Node]]:
        for entry in self.entries:
            if entry.value.kind == "scalar":
                yield entry, entry.value

    @staticmethod
    def in_with(entry: _Entry) -> bool:
        """Whether the entry is an input of an action (a key of a `with:`)."""
        owner = entry.map.owner
        return owner is not None and owner.name.lower() == "with"

    @staticmethod
    def holder(entry: _Entry) -> Optional[_Node]:
        """The step (or job) mapping that holds the `with:`, `env:` or `secrets:` this entry is in."""
        owner = entry.map.owner
        return owner.map if owner is not None else None

    def action_of(self, step: Optional[_Node]) -> str:
        """The action a step uses, as written, in lower case, without its version."""
        uses = self.get(step, "uses") if step is not None else None
        if uses is None or uses.value.kind != "scalar":
            return ""
        return uses.value.text.split("@", 1)[0].strip().lower()


def _clip(value: str, limit: int = 80) -> str:
    """A value from the file as it may appear in a message: one line, printable, short."""
    value = "".join(char if char.isprintable() else "?" for char in " ".join(value.split()))
    return value if len(value) <= limit else value[:limit - 3] + "..."


_FIELD = re.compile(r"\{(file|line|event|action|job|label|name|permission|ref|what)\}")


def _say(code: str, file: str, line: int, fields: dict) -> str:
    """The message of a rule. The fields go in in one pass, so that a value that looks like a field stays a value."""
    values = {"file": _clip(file, 200), "line": str(line)}
    # `what` is the module's own words; every other field is a value from the file
    values.update({key: str(value) if key == "what" else _clip(str(value)) for key, value in fields.items()})
    return _FIELD.sub(lambda found: values.get(found.group(1), found.group()), MESSAGES[code])


# ---------------------------------------------------------------- the rules

_CHECKS: Dict[str, tuple] = {}


def _rule(code: str, kinds: Tuple[str, ...] = ("workflow", "action")):
    def register(function):
        _CHECKS[code] = (function, kinds)
        return function
    return register


def _shown(node: _Node) -> str:
    """What a value says, for a message."""
    if node.kind in ("scalar", "flow"):
        return node.text
    return {"null": "nothing", "map": "a mapping", "seq": "a list"}.get(node.kind, "")


def _permissions(doc: _Doc, node: _Node) -> List[Tuple[str, str, int]]:
    """(scope, its value in lower case, line) of every `permissions:` of a mapping, in any letter case and either style."""
    found = []
    for entry in doc.named(node, "permissions"):
        value = entry.value
        if value.kind == "map":
            found.extend((each.name, each.value.text.strip().lower(), each.line) for each in value.entries
                         if each.value.kind == "scalar")
        elif value.kind == "flow" and value.text.startswith("{"):
            found.extend((key, each.lower(), entry.line) for key, each in _flow_pairs(value.text))
    return found


# The mappings whose keys are names the author chose (a variable called RUN is not a step's `run:`)
_NAMES_OF_THE_AUTHOR = ("env", "with", "outputs", "inputs", "secrets")


def _run_scalars(doc: _Doc) -> Iterable[_Node]:
    """The scripts: the value of every `run:` that is not a name in an `env:` or a `with:`, in any letter case."""
    for entry in doc.entries:
        if entry.name.lower() == "run" and entry.value.kind == "scalar":
            owner = entry.map.owner
            if owner is None or owner.name.lower() not in _NAMES_OF_THE_AUTHOR:
                yield entry.value


def _script_lines(node: _Node) -> List[Tuple[int, str]]:
    """The lines of a script as the shell gets them: the lines as written, or the decoded text of a quoted scalar."""
    if node.style in ("single", "double"):
        return [(node.line, row) for row in node.text.split("\n")]
    return node.parts


@_rule("WF01", ("workflow",))
def _wf01(doc):
    for event, line in doc.events():
        if event not in EVENTS:
            yield line, {"event": event}


_LOCAL = re.compile(r"\./\S*\Z")
_PIN = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)((?:/[A-Za-z0-9_.-]+)*)@[0-9a-f]{40}\Z")


def _pinned(text: str) -> bool:
    """Whether a `uses:` is a folder of this repository or an action at a full commit."""
    if text.startswith("./"):
        return _LOCAL.match(text) is not None and ".." not in text.split("/") and "${{" not in text
    found = _PIN.match(text)
    if found is None:
        return False
    parts = [found.group(1), found.group(2)] + found.group(3).split("/")
    return all(part not in (".", "..") for part in parts)


@_rule("WF02")
def _wf02(doc):
    for entry in doc.entries:
        if entry.name.lower() == "uses":
            node = entry.value
            if not (node.kind == "scalar" and _pinned(node.text.strip())):
                yield entry.line, {"action": _shown(node)}


@_rule("WF03", ("workflow",))
def _wf03(doc):
    entry = doc.top("permissions")
    if entry is None:
        yield 1, {}
        return
    node = entry.value
    if not (node.kind == "map" and len(node.entries) == 1 and node.entries[0].name == "contents"
            and node.entries[0].value.kind == "scalar" and node.entries[0].value.text.strip() == "read"):
        yield entry.line, {}


@_rule("WF04")
def _wf04(doc):
    for node in _run_scalars(doc):
        lines = [number for number, text in node.parts if "${{" in text]
        if not lines and "${{" in node.text:
            lines = [node.line]
        for number in lines:
            yield number, {}


@_rule("WF05", ("workflow",))
def _wf05(doc):
    for job in doc.jobs:
        if doc.get(job.node, "uses") is not None:
            continue
        entry = doc.get(job.node, "timeout-minutes")
        if entry is None or entry.value.kind != "scalar" or not re.fullmatch(r"[1-9][0-9]{0,4}", entry.value.text.strip()):
            yield job.line, {"job": job.name}


_PLAN = re.compile(r"\$\{\{ (?i:fromjson)\(needs\.([A-Za-z0-9_-]+)\.outputs\.matrix\) \}\}\Z")


def _label(node: _Node) -> str:
    """What a `runs-on` says, for a message."""
    if node.kind == "scalar":
        return node.text
    if node.kind == "flow":
        return ", ".join(_unquote(item) for item in _flow_items(node.text)) if node.text[0] == "[" else node.text
    if node.kind == "seq":
        return ", ".join(item.text for item in node.items if item.kind == "scalar")
    if node.kind == "map":
        return ", ".join("%s: %s" % (entry.name, _label(entry.value)) for entry in node.entries)
    return ""


def _listed_runners(node: _Node) -> Optional[List[str]]:
    """The labels a matrix lists under the key `runner`, anywhere in it; None when one is written so that it cannot be read."""
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.kind == "seq":
            stack.extend(current.items)
        elif current.kind == "map":
            for entry in current.entries:
                value = entry.value
                if entry.name.lower() != "runner":
                    stack.append(value)
                elif value.kind == "scalar":
                    found.append(value.text.strip())
                elif value.kind == "flow" and value.text.startswith("["):
                    found.extend(_unquote(item) for item in _flow_items(value.text))
                elif value.kind == "seq" and all(item.kind == "scalar" for item in value.items):
                    found.extend(item.text.strip() for item in value.items)
                else:
                    return None
    return found


@_rule("WF06", ("workflow",))
def _wf06(doc):
    names = {job.name for job in doc.jobs}
    for job in doc.jobs:
        if doc.get(job.node, "uses") is not None:
            continue
        for entry in doc.named(job.node, "runs-on"):
            node, label = entry.value, _label(entry.value)
            if node.kind == "scalar" and label.strip() in RUNNERS:
                continue
            if node.kind == "scalar" and entry.name == "runs-on" and label.strip() == "${{ matrix.runner }}":
                strategy = doc.get(job.node, "strategy")
                matrix = doc.get(strategy.value, "matrix") if strategy is not None else None
                if matrix is not None:
                    plan = _PLAN.match(matrix.value.text.strip()) if matrix.value.kind == "scalar" else None
                    if plan is not None and plan.group(1) in names:
                        continue
                    listed = _listed_runners(matrix.value) if matrix.value.kind == "map" else None
                    if listed and all(each in RUNNERS for each in listed):
                        continue
            yield entry.line, {"job": job.name, "label": label}


@_rule("WF07")
def _wf07(doc):
    for entry in doc.entries:
        if entry.name.lower() != "uses" or entry.value.kind != "scalar":
            continue
        action = doc.action_of(entry.map)
        if action != "actions/checkout" and not action.startswith("actions/checkout/"):
            continue
        with_ = doc.get(entry.map, "with")
        flag = doc.get(with_.value, "persist-credentials") if with_ is not None else None
        if flag is None or flag.value.kind != "scalar" or flag.value.text.strip() != "false":
            yield entry.line, {}


_LAB_ACTION = re.compile(r"\./(?:catalog/)?\.github/actions/lab\Z")
_KEY = "${{ secrets.LAB_DEPLOY_KEY }}"
_NEEDS_KEY = "${{ secrets.NEEDS_DEPLOY_KEY }}"
_LICENSE = "${{ secrets.GRAVITEE_LICENSE }}"
_VERSIONS_KEY = "${{ secrets.VERSIONS_DEPLOY_KEY }}"
_PASSWORD = re.compile(r"\$\{\{ secrets\.(?:GITHUB_TOKEN|REGISTRY_TOKEN)(?: \|\| secrets\.(?:GITHUB_TOKEN|REGISTRY_TOKEN))? \}\}\Z")
# A name that is not part of a longer one: `load-secrets.sh` and `inputs.secrets` are not the context. GitHub names may hold a dash
_SECRET = re.compile(r"(?<![\w.-])secrets\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)", re.I)


def _blessed(doc: _Doc) -> Tuple[Set[int], Set[int]]:
    """The lines where a rule allows a secret, and the lines of the second and later steps that read the license."""
    allowed, first_by_step = set(), {}
    for entry, node in doc.scalars():
        text = node.text.strip()
        if len(node.parts) != 1 or node.style == "block" or not _SECRET.search(text):
            continue
        step, container = doc.holder(entry), entry.map.owner
        if step is None or container is None:
            continue
        name = container.name
        if entry.name == "key" and text == _KEY and name == "with":
            uses = doc.get(step, "uses")
            if uses is not None and uses.value.kind == "scalar" and _LAB_ACTION.match(uses.value.text.strip()):
                allowed.add(entry.line)
        elif entry.name == "LAB_DEPLOY_KEY" and text == _KEY and name == "secrets" and doc.get(step, "uses") is not None:
            allowed.add(entry.line)
        elif entry.name == "NEEDS_DEPLOY_KEY" and text == _NEEDS_KEY and name == "env" and doc.get(step, "run") is not None \
                and "catalog.py ci needs" in _text_of(doc.get(step, "run").value):
            allowed.add(entry.line)
        elif entry.name == "password" and name == "with" and doc.family == "publish" \
                and doc.action_of(step) == "docker/login-action" and _PASSWORD.match(text):
            allowed.add(entry.line)
        elif entry.name == "VERSIONS_DEPLOY_KEY" and text == _VERSIONS_KEY and name == "env" and doc.family == "versions" \
                and doc.get(step, "run") is not None and "catalog.py ci versions-release" in _text_of(doc.get(step, "run").value):
            allowed.add(entry.line)
        elif entry.name == "GRAVITEE_LICENSE" and text == _LICENSE and name == "env" and doc.family in ("e2e", "versions") \
                and (doc.get(step, "run") is not None or doc.get(step, "uses") is not None):
            first_by_step.setdefault(id(step), entry.line)
            allowed.add(entry.line)
    return allowed, set(sorted(first_by_step.values())[1:])


_WORD_SECRETS = re.compile("secrets", re.I)
_WORD_NEEDS = re.compile("needs", re.I)


def _reads_in(name: str, text: str) -> List[str]:
    """The secrets read in the expressions of a text. A body without the word is not scanned, which spares the work of blanking
    its strings."""
    found = []
    for expression in _expressions_in(name, text):
        if _WORD_SECRETS.search(expression):
            found.extend(each.group(1) for each in _SECRET.finditer(_blank_strings(expression)))
    return found


def _secret_reads(name: str, node: _Node) -> List[Tuple[int, str]]:
    """(line, secret) of every `secrets.X` in the expressions of a value. A secret is only read inside `${{ }}` or an `if:`, so a
    path called `secrets.yaml` or a word in a sentence is no read. The line is the one that holds the expression when it is on
    one line of the value, else the first line of the value."""
    found, names = [], set()
    for number, text in node.parts:
        for secret in _reads_in(name, text):
            found.append((number, secret))
            names.add(secret.lower())
    text = _text_of(node)
    if len(node.parts) == 1 and node.parts[0][1] == text:
        return found          # the value is what it says: no escape, no folded line
    for secret in _reads_in(name, text):
        if secret.lower() not in names:
            found.append((node.line, secret))
    return found


@_rule("WF08")
def _wf08(doc):
    allowed, extra = _blessed(doc)
    for job in doc.jobs:
        for entry in doc.named(job.node, "secrets"):
            if entry.value.kind == "scalar" and entry.value.text.strip().lower() == "inherit":
                yield entry.line, {"name": "inherit"}
    seen = set()
    for name, node in doc.leaves:
        for line, secret in _secret_reads(name, node):
            if (line in allowed and line not in extra) or (line, secret) in seen:
                continue
            seen.add((line, secret))
            yield line, {"name": secret}


@_rule("WF09", ("workflow",))
def _wf09(doc):
    table = MAY_WRITE.get(doc.family, {})
    union = {scope for scopes in table.values() for scope in scopes}
    for job in doc.jobs:
        may = union if doc.get(job.node, "uses") is not None else set(table.get(job.name, ()))
        for scope, value, line in _permissions(doc, job.node):
            if value == "write" and scope.lower() not in may:
                yield line, {"job": job.name, "permission": scope}


@_rule("WF10", ("workflow",))
def _wf10(doc):
    for job in doc.jobs:
        if doc.get(job.node, "uses") is not None or doc.get(job.node, "steps") is None:
            continue
        if not any(scope.lower() == "packages" and value == "write" for scope, value, _ in _permissions(doc, job.node)):
            continue
        environment = doc.get(job.node, "environment")
        if environment is None or environment.value.kind == "null":
            yield job.line, {"job": job.name}


@_rule("WF11", ("workflow",))
def _wf11(doc):
    if [event for event, _ in doc.events()] == ["workflow_call"]:
        return
    entry = doc.top("concurrency")
    node = entry.value if entry is not None else None
    if node is not None and node.kind == "scalar" and node.text.strip():
        return
    if node is not None and node.kind == "map":
        group = doc.get(node, "group")
        if group is not None and group.value.kind == "scalar" and group.value.text.strip():
            return
    yield (entry.line if entry is not None else 1), {}


_DOWNLOADERS = ("curl", "wget", "aria2c", "iwr", "irm", "invoke-webrequest", "invoke-restmethod")
_INTERPRETERS = ("sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "ash", "python", "perl", "ruby", "node", "nodejs",
                 "php", "pwsh", "powershell", "iex", "invoke-expression", "deno", "bun", "lua", "tclsh", "rscript")
# Programs that run what follows them, with the options of each that take the next word as their value, and how many words
# (not options) each takes before the command: `sudo -u root bash`, `nice -n 10 bash`, `timeout 10 bash`. The command is the
# first word after these, so that `sudo tar -C /opt/node` is not a shell
_WRAPPERS = {
    "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t", "--user", "--group", "--host", "--prompt",
             "--close-from", "--chdir", "--chroot", "--command-timeout", "--other-user", "--role", "--type"),
    "doas": ("-u", "-C"),
    "env": ("-u", "-C", "-a", "--unset", "--chdir", "--argv0"),
    "nice": ("-n", "--adjustment"),
    "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
    "xargs": ("-n", "-I", "-P", "-L", "-d", "-s", "-a", "-E", "--max-args", "--max-procs", "--max-lines", "--delimiter",
              "--max-chars", "--arg-file", "--eof", "--replace"),
    "time": ("-f", "-o", "--format", "--output"),
    "timeout": ("-s", "-k", "--signal", "--kill-after"),
    "exec": ("-a",),
    "command": (), "nohup": (), "setsid": (), "busybox": (),
}
_OWED = {"timeout": 1}
_PATH = r"(?:/(?:[A-Za-z0-9_.-]+/)*)?"      # a command called by an absolute path: /usr/bin/curl (not ./curl, not a URL)
_DOWNLOAD = re.compile(r"(?<![A-Za-z0-9_./:-])%s(?i:%s)(?![A-Za-z0-9_-])" % (_PATH, "|".join(_DOWNLOADERS)))
_SUBSTITUTION = re.compile(r"(?:<\(|\$\(|`)[ \t]*%s(?i:%s)(?![A-Za-z0-9_-])" % (_PATH, "|".join(_DOWNLOADERS)))
_SHELL_WORD = re.compile(r"(?<![A-Za-z0-9_./-])(?i:eval|source|(?:%s)[0-9.]*)(?![A-Za-z0-9_-])" % "|".join(_INTERPRETERS))
_SHELL_VARIABLE = re.compile(r"\$\{?(?:SHELL|BASH)\}?\Z")
_PIPE = re.compile(r"\|&?")
_PIECES = re.compile(r";|&&|\|\||\n")
_GROUP_END = re.compile(r"[^|]*[})][^|]*\|")     # a group closes before the first pipe
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_VERSION = re.compile(r"[0-9][0-9.]*\Z")
_QUOTES = re.compile(r"['\"\\]")


def _runs_a_shell(stage: str) -> bool:
    """Whether a stage of a pipeline is an interpreter, alone or behind wrappers (`sudo -E bash -s`, `env X=1 python3.11`), in
    the spellings that reach the same command: quoted (`'bash'`, `\\bash`), by path, as `$SHELL`, in a group (`(sh)`, `{ sh; }`)."""
    takes, owed, skip = (), 0, False
    for word in stage.split(None, 40):
        word = _QUOTES.sub("", word)
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in takes         # `-u root` takes the next word, `-I{}` and `--user=root` are whole
        else:
            word = word.strip("(){}")
            if not word or _ASSIGNMENT.match(word):
                continue
            if owed:
                owed -= 1
                continue
            base = word.rsplit("/", 1)[-1].lower()
            if base in _WRAPPERS:
                takes, owed = _WRAPPERS[base], _OWED.get(base, 0)
                continue
            return _VERSION.sub("", base) in _INTERPRETERS or _SHELL_VARIABLE.match(word) is not None
    return False


def _logical_lines(node: _Node) -> List[Tuple[int, str]]:
    """The lines a script runs, with a line that ends in a backslash or a pipe joined to the next one."""
    out, current, first = [], [], 0
    for number, text in _script_lines(node):
        stripped = text.rstrip()
        if not current:
            first = number
        if stripped.endswith("\\"):
            current.append(stripped[:-1] + " ")
        elif stripped.endswith("|") and not stripped.endswith("||"):
            current.append(stripped + " ")
        else:
            out.append((first, "".join(current) + text))
            current = []
    if current:
        out.append((first, "".join(current)))
    return out


def _downloads_into_a_shell(line: str) -> bool:
    """Whether a command line pipes a download into an interpreter, or hands one a download by substitution."""
    if _DOWNLOAD.search(line) is None:
        return False
    downloaded = False
    for piece in _PIECES.split(line):
        found = _DOWNLOAD.search(piece)
        if found is None:
            # `{ curl x; } | sh` and `(curl x; echo) | sh`: the pipe is after the `;` that ends the download
            if downloaded and _GROUP_END.match(piece) and any(_runs_a_shell(stage) for stage in _PIPE.split(piece)[1:]):
                return True
            continue
        downloaded = True
        substitution = _SUBSTITUTION.search(piece)
        if substitution is not None and (_SHELL_WORD.search(piece, 0, substitution.start()) or piece.lstrip().startswith(". ")):
            return True
        stages = _PIPE.split(piece[found.end():])
        if len(stages) > MAX_PIPES or any(_runs_a_shell(stage) for stage in stages[1:]):
            return True
    return False


@_rule("WF12")
def _wf12(doc):
    for node in _run_scalars(doc):
        for number, line in _logical_lines(node):
            if _downloads_into_a_shell(line):
                yield number, {}


_RUNTIME = re.compile(r"ACTIONS_RUNTIME_TOKEN|ACTIONS_CACHE_URL|ACTIONS_RESULTS_URL|ghaction-github-runtime", re.I)


@_rule("WF13")
def _wf13(doc):
    seen, by_node = set(), {id(each.node): each for each in doc.jobs}
    for entry in doc.entries:
        if entry.name.lower() != "permissions":
            continue
        job = by_node.get(id(entry.map))
        if doc.family == "publish" and job is not None and (job.name == "attest" or doc.get(job.node, "uses") is not None):
            continue
        for scope, value, line in _permissions(doc, entry.map):
            if scope.lower() == "id-token" and value == "write" and line not in seen:
                seen.add(line)
                yield line, {}
    for found in _RUNTIME.finditer(doc.blob()[0]):
        line = doc.line_at(found.start())
        if line not in seen:
            seen.add(line)
            yield line, {}


@_rule("WF14")
def _wf14(doc):
    for entry in doc.entries:
        if entry.name.lower() == "continue-on-error":
            node = entry.value
            if node.kind != "null" and not (node.kind == "scalar" and node.text.strip().lower() == "false"):
                yield entry.line, {}


_CALLEE = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/\.github/workflows/[A-Za-z0-9_./-]+@(\S*)\Z")


@_rule("WF15", ("workflow",))
def _wf15(doc):
    standard = doc.standard or {}
    repository, ref = standard.get("repository"), standard.get("ref")
    for job in doc.jobs:
        uses = doc.get(job.node, "uses")
        if uses is None or uses.value.kind != "scalar" or uses.value.text.strip().startswith("./"):
            continue
        found = _CALLEE.match(uses.value.text.strip())
        if not (found and isinstance(repository, str) and isinstance(ref, str)
                and found.group(1).lower() == repository.lower() and found.group(2) == ref):
            yield uses.line, {"ref": ref if isinstance(ref, str) else "COMMIT"}


@_rule("WF16", ("workflow",))
def _wf16(doc):
    for job in doc.jobs:
        if doc.get(job.node, "uses") is None:
            continue
        for entry in doc.named(job.node, "secrets"):
            node = entry.value
            if node.kind == "null":
                continue
            if node.kind == "map" and entry.name == "secrets" and all(
                    each.name == "LAB_DEPLOY_KEY" and each.value.kind == "scalar" and each.value.text.strip() == _KEY
                    for each in node.entries):
                continue
            yield entry.line, {}


_CACHE = re.compile(r"cache[-_](?:from|to)|type\s*=\s*(?:gha|registry)\b", re.I)
# The inputs that switch a cache on, and the actions that cache with no input at all unless it says false (the input to say it)
_CACHE_INPUTS = ("cache", "bundler-cache", "enable-cache")
_CACHED_BY_DEFAULT = {"actions/setup-go": "cache", "astral-sh/setup-uv": "enable-cache"}


def _is_off(entry: Optional[_Entry]) -> bool:
    return entry is not None and entry.value.kind == "scalar" and entry.value.text.strip().lower() == "false"


@_rule("WF17")
def _wf17(doc):
    seen = set()
    for entry in doc.entries:
        name, node = entry.name.lower(), entry.value
        if name == "uses" and node.kind == "scalar":
            action = node.text.split("@", 1)[0].strip().lower()
            if action == "actions/cache" or action.startswith("actions/cache/"):
                seen.add(entry.line)
            for each, switch in _CACHED_BY_DEFAULT.items():
                if action == each or action.startswith(each + "/"):
                    inputs = doc.get(entry.map, "with")
                    if not _is_off(doc.get(inputs.value, switch) if inputs is not None else None):
                        seen.add(entry.line)
        elif name in _CACHE_INPUTS and doc.in_with(entry) and node.kind != "null" \
                and not (node.kind == "scalar" and node.text.strip().lower() == "false"):
            seen.add(entry.line)
    for found in _CACHE.finditer(doc.blob()[0]):
        seen.add(doc.line_at(found.start()))
    for line in sorted(seen):
        yield line, {}


_RESULT = re.compile(r"(?<![\w.-])needs\s*\.\s*([A-Za-z0-9_-]+)\s*\.\s*result", re.I)
_INDEX = re.compile(r"\[\s*'([A-Za-z0-9_-]+)'\s*\]")
_PAREN = re.compile(r"[()]")
# What turns a test over: `!` or `==` or `!=` in front of a group or of a call, such as `!(a == b)` or `!contains(...)`
_TURNS_OVER = re.compile(r"(?:!=|==|!)\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?\(")
_NOT_AFTER = re.compile(r"\s*\)*\s*!=")                    # needs.x.result != y, also inside a call: toJSON(needs.x.result) != y
_NOT_BEFORE = re.compile(r"!=\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*\(\s*)*\Z")     # y != needs.x.result, also y != toJSON(needs.x.result)
_CHAINED = re.compile(r"\s*==\s*''\s*\)*\s*(?:==|!=)")     # needs.x.result == 'failure' == false
_NEEDS_AT = re.compile(r"needs\s*(?:\.\s*|\[\s*')([A-Za-z0-9_-]+)", re.I)
MAX_PARENS = 20000      # parentheses of one expression that are paired; the rest of a longer one counts as turned over
MAX_QUOTES = 4096       # quotes in one value whose strings are followed to find where an expression ends; more is read to the end


_EXPRESSION_TOKEN = re.compile(r"\$\{\{|\}\}|'")


def _expressions(text: str) -> List[str]:
    """The bodies of the `${{ }}` in a value; one that is not closed runs to the end. An expression ends at the first `}}`, and
    also at the first `}}` outside a string literal, which is where GitHub ends it: both bodies are read, so that a secret after
    `format('}}', ...)` is not cut off. The second body holds the first as its beginning, so nothing the first shows is lost."""
    out, pos = [], 0
    while True:
        start = text.find("${{", pos)
        if start < 0:
            break
        end = text.find("}}", start + 3)
        if end < 0:
            out.append(text[start + 3:])
            break
        out.append(text[start + 3:end])
        pos = end + 2
    if out and text.count("'") > MAX_QUOTES:
        # too many strings to follow one by one: from the first `${{` to the end is read as the expression, which holds every
        # reading of where it ends
        return [text[text.index("${{") + 3:]]
    seen, start, quoted = set(out), -1, False
    for found in _EXPRESSION_TOKEN.finditer(text) if out else ():
        token = found.group()
        if start < 0:
            if token == "${{":
                start, quoted = found.end(), False
        elif token == "'":
            quoted = not quoted
        elif token == "}}" and not quoted:
            body = text[start:found.start()]
            if body not in seen:
                seen.add(body)
                out.append(body)
            start = -1
    if start >= 0 and text[start:] not in seen:
        out.append(text[start:])
    return out


def _blank_strings(expression: str) -> str:
    """The expression with the text of its string literals taken out, so that a word in a string is not read as a context."""
    out, pos = [], 0
    while True:
        start = expression.find("'", pos)
        if start < 0:
            out.append(expression[pos:])
            return "".join(out)
        out.append(expression[pos:start] + "''")
        end = _end_single(expression, start + 1)
        if end < 0:
            return "".join(out)
        pos = end + 1


def _expressions_in(name: str, text: str) -> List[str]:
    """The expressions a text holds: the bodies of its `${{ }}`, all of it for an `if:` (whose value is an expression)."""
    return _expressions(text) if "${{" in text else ([text] if name == "if" else [])


def _text_of(node: _Node) -> str:
    """What GitHub reads in a value: a flow list has its quotes taken off and its escapes decoded."""
    if node.kind == "flow" and node.text[:1] == "[":
        return "\n".join(_unquote(item) for item in _flow_items(node.text))
    return node.text


def _expressions_of(name: str, node: _Node) -> List[str]:
    """The expressions a value holds. `name` is the key that holds it, in lower case."""
    return _expressions_in(name, _text_of(node))


def _turned_over(expression: str) -> List[Tuple[int, int]]:
    """The stretches of an expression that an operator turns over: the inside of `!(...)`, `!contains(...)`, `== (...)`. Found
    in one pass with a stack, and merged, so that a pile of `!contains(` costs what one costs."""
    opens = {each.end() - 1 for each in _TURNS_OVER.finditer(expression)}
    if not opens:
        return []
    spans, stack = [], []
    for count, each in enumerate(_PAREN.finditer(expression)):
        at = each.start()
        if count >= MAX_PARENS:
            spans.append((at, len(expression)))
            stack = []
            break
        if each.group() == "(":
            stack.append(at)
        elif stack:
            start = stack.pop()
            if start in opens:
                spans.append((start, at))
    spans.extend((start, len(expression)) for start in stack if start in opens)
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _negated(expression: str) -> List[str]:
    """The jobs whose result an expression tests the wrong way round: with `!=` (also behind a call, or on the left), inside a
    group or a call that `!`, `==` or `!=` turns over, or compared again after its own `==`. A text check of the spellings that
    are met, not a proof: the line `if:` of a publish job is read by the code owner too."""
    turned = _turned_over(expression)
    starts = [span[0] for span in turned]
    found = []
    for each in _RESULT.finditer(expression):
        start, end = each.start(), each.end()
        at = bisect.bisect_right(starts, start) - 1
        if (_NOT_AFTER.match(expression, end) or _CHAINED.match(expression, end)
                or _NOT_BEFORE.search(expression, max(start - 96, 0), start) or (at >= 0 and start < turned[at][1])):
            found.append(each.group(1))
    return found


def _first_lines(node: _Node) -> Dict[str, int]:
    """{job in lower case: the first line of a value that names it as `needs.<job>`}."""
    first: Dict[str, int] = {}
    for number, text in node.parts:
        for each in _NEEDS_AT.finditer(text):
            first.setdefault(each.group(1).lower(), number)
    return first


@_rule("WF18", ("workflow",))
def _wf18(doc):
    if doc.family != "publish":
        return
    seen = set()
    for name, node in doc.leaves:
        jobs = []
        for expression in _expressions_of(name, node):
            if _WORD_NEEDS.search(expression):
                # `needs['x']['result']` is `needs.x.result`; a string is read after that, so that a word in one is no context
                jobs.extend(_negated(_blank_strings(_INDEX.sub(r".\1", expression))))
        if not jobs:
            continue
        lines = _first_lines(node)
        for job in jobs:
            line = lines.get(job.lower(), node.line)
            if (line, job.lower()) not in seen:
                seen.add((line, job.lower()))
                yield line, {"job": job}


_ENV_FILE = re.compile(r"GITHUB_ENV|GITHUB_PATH|::set-env|::add-path", re.I)
# What the toolkit of github-script offers to write the same two files
_TOOLKIT = re.compile(r"\b(?:exportVariable|addPath)\b")


@_rule("WF19")
def _wf19(doc):
    seen = set()
    for node in _run_scalars(doc):
        for number, text in _script_lines(node):
            if _ENV_FILE.search(text) and not text.lstrip().startswith("#") and number not in seen:
                seen.add(number)
                yield number, {}
    for entry in doc.entries:
        if entry.name.lower() == "script" and entry.value.kind == "scalar" and doc.in_with(entry) \
                and doc.action_of(doc.holder(entry)) == "actions/github-script":
            node = entry.value
            lines = [number for number, text in node.parts if "${{" in text]
            if not lines and "${{" in node.text:
                lines = [node.line]       # the expression is spelt with escapes or over lines
            for number, text in _script_lines(node):
                if (_ENV_FILE.search(text) or _TOOLKIT.search(text)) and not text.lstrip().startswith("//"):
                    lines.append(number)
            for number in lines:
                if number not in seen:
                    seen.add(number)
                    yield number, {}


_UNSECURE = re.compile(r"ACTIONS_ALLOW_UNSECURE_COMMANDS", re.I)
_BRACKET = re.compile(r"(?<![\w.-])secrets\s*\[", re.I)
_WHOLE = re.compile(r"(?<![\w.-])secrets(?![\w-])(?!\s*[.\[])", re.I)


@_rule("WF20")
def _wf20(doc):
    found = {}
    for each in _UNSECURE.finditer(doc.blob()[0]):
        found.setdefault(doc.line_at(each.start()), "ACTIONS_ALLOW_UNSECURE_COMMANDS turns on the commands that let output "
                                                    "choose what runs")
    for entry in doc.entries:
        if entry.name.lower() in ("run-id", "github-token") and doc.in_with(entry) \
                and doc.action_of(doc.holder(entry)) == "actions/download-artifact":
            found.setdefault(entry.line, "%s on download-artifact reads an artifact of another run, which someone may have "
                                         "written" % entry.name)
    for name, node in doc.leaves:
        for expression in map(_blank_strings, filter(_WORD_SECRETS.search, _expressions_of(name, node))):
            if _BRACKET.search(expression):
                found.setdefault(node.line, "secrets[ reads a secret by a name that is computed")
            elif _WHOLE.search(expression):
                found.setdefault(node.line, "the secrets context is used as a whole and hands over every secret")
    for line in sorted(found):
        yield line, {"what": found[line]}


@_rule("WF21")
def _wf21(doc):
    for line in sorted(doc.unreadable):
        yield line, {}


# ---------------------------------------------------------------- entry points

def unreadable(text: str) -> Dict[int, str]:
    """Why each line the outline cannot read for certain is refused (WF21): {line: reason}. For tests and for `explain`."""
    return dict(_Doc("workflow.yml", text).unreadable)


def _is_action(path: str) -> bool:
    """Whether a file is a composite action and not a workflow. The folder decides, and the name only where the folder says
    nothing: a file of `.github/workflows/` is a workflow whatever it is called (`action.yml` too), because GitHub runs it as one."""
    if ".github/workflows/" in path:
        return False
    return path.startswith(".github/actions/") or path.rsplit("/", 1)[-1].lower() in ("action.yml", "action.yaml")


def _problems(path: str, text: str, rules: Iterable[str], standard: Optional[dict], is_action: bool) -> List[Problem]:
    doc = _Doc(path, text, standard, is_action)
    wanted, found = set(rules), []
    for code in sorted(wanted & set(_CHECKS)):
        function, kinds = _CHECKS[code]
        if doc.kind not in kinds:
            continue
        for count, (line, fields) in enumerate(function(doc)):
            if count >= MAX_PER_RULE:
                break
            found.append(Problem(code, "error", path, line, _say(code, path, line, fields)))
    return sorted(set(found), key=lambda each: (each.line, each.code, each.message))


def problems(path: str, text: str, rules: Iterable[str] = ALL, standard: Optional[dict] = None) -> List[Problem]:
    """The problems of one file. `path` decides what it is: a workflow (and the rights its name gives it) or an action.
    `rules` is a set of codes, GENERIC for the engine's own workflows. `standard` is `{"repository", "ref"}` of the Lab."""
    return _problems(path, text, rules, standard, _is_action(path))


def _lab_pin(tree) -> dict:
    """`lab.repository` and `lab.ref` of .github/standard.json; empty when the file cannot give them (SD04 says why)."""
    data = tree.read(".github/standard.json", STANDARD_LIMIT + 1)
    if data is None or len(data) > STANDARD_LIMIT:
        return {}
    try:
        lab = json.loads(data.decode("utf-8")).get("lab")
    except (ValueError, RecursionError, AttributeError):
        return {}
    return lab if isinstance(lab, dict) else {}


def check(tree, ctx=None) -> List[Problem]:
    """Every workflow and every local action of a catalog against both sets. Pure: it reads the tree and writes nothing."""
    found, standard, count = [], None, 0
    for entry in sorted(tree.entries(), key=lambda each: each.path):
        path = entry.path
        if not (_WORKFLOW_FILE.match(path) or _ACTION_FILE.match(path)):
            continue
        if standard is None:
            standard = _lab_pin(tree)
        text = None
        count += 1
        size = entry.size if isinstance(entry.size, int) else 0
        if entry.mode in _REGULAR and size <= FILE_LIMIT and count <= MAX_FILES:
            data = tree.read(path)
            if data is not None:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
        if text is None:
            found.append(Problem("WF21", "error", path, 1, _say("WF21", path, 1, {})))
            continue
        found.extend(_problems(path, text, ALL, standard, _ACTION_FILE.match(path) is not None))    # the place decides, as above
    return found


# ---------------------------------------------------------------- the readers of the engine's test

def run_blocks(text: str) -> List[Tuple[int, str]]:
    """(line number, line) of every line a `run:` runs: its value on the line itself, or the block under it."""
    found = []
    for node in _run_scalars(_Doc("workflow.yml", text)):
        found.extend(node.parts)
    return sorted(found)


def jobs(text: str) -> Dict[str, str]:
    """{job name: its text} of a workflow: the keys of `jobs:`, read by indentation whatever the indentation is."""
    doc = _Doc("workflow.yml", text)
    if not doc.jobs:
        return {}
    after = [entry.line for entry in doc.root.entries if entry.line > doc.jobs[-1].line]
    found = {}
    for number, job in enumerate(doc.jobs):
        end = doc.jobs[number + 1].line - 1 if number + 1 < len(doc.jobs) else (min(after) - 1 if after else len(doc.lines))
        found[job.name] = "\n".join(doc.lines[job.line:end])
    return found


def triggers(text: str) -> Set[str]:
    """The events of `on:`, in the spellings a workflow may use."""
    return {event for event, _ in _Doc("workflow.yml", text).events()}
