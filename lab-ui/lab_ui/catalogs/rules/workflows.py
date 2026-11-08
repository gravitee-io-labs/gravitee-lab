"""The workflow rules of the catalog repository standard: what any workflow should satisfy, read as text by indentation with no
YAML library. A pull request writes the file that judges it, so the rules are only an early warning and a convenience for
contributors, never the gate (2.1 principle 3); they are the same rules in the engine's own test, in `catalog.py check` and in the
pre-flight of Share.

    check(tree, ctx) -> [Problem]            every workflow and local action of a catalog
    problems(path, text, rules)              one file, any set of codes

The reader is an outline by indentation (`_Reader`), not one regular expression per line: a rule that looks for `^      timeout`
at six spaces is blind to a file written with four. Whatever the outline cannot read with certainty is WF21, so a workflow that
hides what the rules look at is refused instead of passed: a flow mapping, an anchor, a tag, a quoted key, a duplicate key, a
block scalar with a header it does not know, an escape, a value that a quote or a plain scalar carries on to the next line, a
control character, a line break YAML takes and a line reading would not.

A file is a workflow or an action by its folder: everything in `.github/workflows/` is a workflow for GitHub, whatever it is
called, and so for the rules (a workflow called `action.yml` breaks WF01 like any other). The name decides only for a file
outside both folders.

What GitHub reads is what the rules read: a value the rules would have to decode or fold is refused (`s\\x65crets` is `secrets`
to GitHub), a secret is read inside `${{ }}` and in an `if:`, wherever a value can be (a key, a list item, a flow list), and a
file called `secrets.yaml` or a sentence that holds the word is no read.

Hostile input is the case this module is written for. It never executes, imports or follows anything of the tree it reads. A
line of 1 MB, a file of 20,000 short lines and a pile of quotes or brackets each take a fraction of a second. Every scan moves
forward: `str.find`, a regular expression with no nested repetition. What cannot be linear is bounded: nesting stops at MAX_DEPTH, a
flow collection longer than MAX_FLOW is not read, and a rule reports at most MAX_PER_RULE findings per file. A key that is spelt with
other letter case than the rules expect counts for what a rule forbids and not for what it asks: `Permissions:` does not make a
token read-only, and `Continue-On-Error: true` is still found. The rules are a text check of the spellings that are met, an early
warning and not a proof: the code owner reads the workflows too."""

import bisect
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from ..report import ERROR, Problem
from . import say, table
from .secrets import flow_items

MAX_LINES = 20000       # a workflow longer than this is not read: WF21
MAX_DEPTH = 40          # levels of nesting
MAX_FLOW = 2048         # characters of a flow collection `[a, b]` that are read
MAX_KEY = 512           # characters of a key
MAX_PER_RULE = 50       # findings of one rule in one file
MAX_PIPES = 1000        # pipes in one pipeline that starts with a download
MAX_FILES = 100         # workflows and actions that `check` reads in one tree; the others are WF21
FILE_LIMIT = 1024 * 1024

EVENTS = ("pull_request", "push", "schedule", "workflow_dispatch", "workflow_call")
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


RULES = table((code, ERROR, rule, message) for code, rule, message in (
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
    ("WF07", "Every `actions/checkout` says `persist-credentials: false`",
     "{file}:{line}: actions/checkout leaves its token in .git. Add persist-credentials: false."),
    ("WF11", "A `concurrency:` group exists (not in a reusable file)",
     "{file}: add a concurrency group, so that a push does not pile runs up."),
    ("WF12", "No download piped into a shell",
     "{file}:{line}: a download is piped into a shell. Download it, check a digest, then run it."),
    ("WF13", "`id-token: write` appears in the job `attest` of `publish.yml` only, and no step exports the runtime token of "
             "Actions (`ACTIONS_RUNTIME_TOKEN`, `ACTIONS_CACHE_URL`, `ACTIONS_RESULTS_URL`, `ghaction-github-runtime`)",
     "{file}: the identity token and the runtime token of Actions go to the attest job of publish.yml only."),
    ("WF14", "No `continue-on-error: true`",
     "{file}:{line}: continue-on-error hides a failure. Make the step decide its own exit code."),
    ("WF17", "No cache of any kind: no `actions/cache`, no `cache:` input of a `setup-*` action, no `cache-from` "
             "or `cache-to`, no `type=gha` or `type=registry`",
     "{file}:{line}: a cache lets a pull request write what main later reads. Remove it."),
    ("WF19", "No write to `$GITHUB_ENV` or `$GITHUB_PATH`, and no `${{` inside the `script:` of "
             "`actions/github-script`",
     "{file}:{line}: this lets data from outside choose what runs next. Fix: pass the value through env: to the "
     "one step that needs it."),
    ("WF20", "No `ACTIONS_ALLOW_UNSECURE_COMMANDS`; no `run-id` or `github-token` on `actions/download-artifact` "
             "(an artifact of another run is data that someone may have written); no dynamic access to secrets: "
             "`secrets[`, `toJSON(secrets)`, `toJson(secrets)`, `secrets: inherit`",
     "{file}:{line}: {what}. Fix: name the one secret a step needs, and download only the artifact of this run."),
    ("WF21", "Fail closed. The rules read by indentation, so a workflow that hides what they read is refused: a "
             "flow mapping or sequence as the value of `on`, `permissions`, `secrets`, `runs-on`, `uses` or "
             "`environment`; `permissions: write-all` or `read-all`; an anchor, an alias or a tag (`&x`, `*x`, "
             "`!!x`); a quoted key; a backslash in a double-quoted scalar or a flow list; a quote not closed on its line; a "
             "plain scalar that goes on to the next line",
     "{file}:{line}: this is written so that the rules cannot read it. Fix: write it in block style, one key per "
     "line."),
))

GENERIC = frozenset(RULES)

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


# ---------------------------------------------------------------- the outline

class _Node(object):
    """A value: a mapping (`entries`), a sequence (`items`), a scalar (`text`, and the lines it takes in `parts`), a flow
    collection on one line (`text`), or nothing (`null`)."""
    __slots__ = ("kind", "line", "col", "entries", "items", "parts", "text", "owner")

    def __init__(self, kind: str, line: int, col: int) -> None:
        self.kind, self.line, self.col = kind, line, col
        self.entries: List["_Entry"] = []
        self.items: List["_Node"] = []
        self.parts: List[Tuple[int, str]] = []
        self.text = ""
        self.owner: Optional["_Entry"] = None    # the entry this node is the value of


class _Entry(object):
    __slots__ = ("name", "line", "col", "value", "map")

    def __init__(self, name: str, line: int, col: int, holder: _Node) -> None:
        self.name, self.line, self.col, self.map = name, line, col, holder
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
        entry = _Entry(name, number, col, frame.node)
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
        scalar takes the lines deeper than that. Returns the node, the next line to read, and how many characters of `text` are not
        a comment."""
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
        return self._plain(i, text, col)

    def _block(self, i: int, col: int, owner: int, keep: int) -> Tuple[_Node, int, int]:
        node = _Node("scalar", i + 1, col)
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

    def _quoted(self, i: int, text: str, col: int) -> Tuple[_Node, int, int]:
        number, quote = i + 1, text[0]
        end = (_end_single if quote == "'" else _end_double)(text, 1)
        keep = end + 1 if end >= 0 else len(text)
        inner = text[1:end] if end >= 0 else text[1:]
        node = _Node("scalar", number, col)
        node.parts.append((number, text[:keep]))
        if end < 0:
            self.flag(number, "a quote that is not closed on its line")
        elif text[keep:].strip(" \t") and text[keep:].lstrip(" \t")[:1] != "#":
            self.flag(number, "text after a quoted scalar")
        if quote == '"' and "\\" in inner:
            self.flag(number, "a backslash in a double-quoted scalar")
        node.text = inner.replace("''", "'") if quote == "'" else inner
        return node, i + 1, keep

    def _flow(self, i: int, text: str, col: int) -> Optional[Tuple[_Node, int, int]]:
        number = i + 1
        close, deepest = _flow_scan(text)
        if close < 0:
            self.flag(number, "a flow collection that is not closed on its line, or is too long")
            return None
        node = _Node("flow", number, col)
        node.text = text[:close + 1]
        node.parts.append((number, node.text))
        tail = text[close + 1:]
        if tail.strip(" \t") and tail.lstrip(" \t")[:1] != "#":
            self.flag(number, "text after a flow collection")
        if deepest > 1:
            self.flag(number, "a flow collection inside a flow collection")
        elif node.text[0] == "{" and node.text[1:-1].strip():
            self.flag(number, "a flow mapping")
        if any(item.strip()[:1] in ("&", "*", "!") for item in flow_items(node.text[1:-1])):
            self.flag(number, "an anchor, an alias or a tag")
        if "\\" in node.text:
            self.flag(number, "a backslash in a flow collection")
        return node, i + 1, close + 1

    @staticmethod
    def _plain(i: int, text: str, col: int) -> Tuple[_Node, int, int]:
        """One line: what goes on deeper on the next one belongs to no key, and `_place` says so."""
        node = _Node("scalar", i + 1, col)
        found = _COMMENT.search(text)
        keep = found.start() if found else len(text)
        node.text = text[:keep].rstrip(" \t")
        node.parts.append((i + 1, node.text))
        return node, i + 1, keep


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


class _Doc(object):
    """One file read: the outline, what the rules ask of it, and the reasons it could not read some of it."""

    def __init__(self, path: str, text: str, is_action: bool = False) -> None:
        reader = _Reader(text)
        self.root: _Node = reader.root
        self.kind = "action" if is_action else "workflow"
        self.name = path.rsplit("/", 1)[-1]
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
                        key = _Node("scalar", entry.line, entry.col)
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
                    self.jobs.extend(_Job(job.name, job.line, job.value) for job in entry.value.entries
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
        """(event, line) of every event `on:` names, in block style (a flow collection there is WF21). The key is read in any
        letter case: an event hidden under `On:` is still an event."""
        found = []
        for entry in self.named(self.root, "on"):
            node = entry.value
            if node.kind == "scalar":
                found.append((node.text, entry.line))
            elif node.kind == "map":
                found.extend((each.name, each.line) for each in node.entries)
            elif node.kind == "seq":
                found.extend((item.text, item.line) for item in node.items if item.kind == "scalar")
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


def _say(code: str, file: str, line: int, fields: dict) -> str:
    """The message of a rule. `what` is the module's own words; every other field is a value from the file."""
    values = {key: str(value) if key == "what" else _clip(str(value)) for key, value in fields.items()}
    return say(RULES[code].message, file=_clip(file, 200), line=line, **values)


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


# The mappings whose keys are names the author chose (a variable called RUN is not a step's `run:`)
_NAMES_OF_THE_AUTHOR = ("env", "with", "outputs", "inputs", "secrets")


def _run_scalars(doc: _Doc) -> Iterable[_Node]:
    """The scripts: the value of every `run:` that is not a name in an `env:` or a `with:`, in any letter case."""
    for entry in doc.entries:
        if entry.name.lower() == "run" and entry.value.kind == "scalar":
            owner = entry.map.owner
            if owner is None or owner.name.lower() not in _NAMES_OF_THE_AUTHOR:
                yield entry.value


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
        for number, text in node.parts:
            if "${{" in text:
                yield number, {}


@_rule("WF05", ("workflow",))
def _wf05(doc):
    for job in doc.jobs:
        if doc.get(job.node, "uses") is not None:
            continue
        entry = doc.get(job.node, "timeout-minutes")
        if entry is None or entry.value.kind != "scalar" or not re.fullmatch(r"[1-9][0-9]{0,4}", entry.value.text.strip()):
            yield job.line, {"job": job.name}


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
_SHELL_WORD = re.compile(r"(?<![A-Za-z0-9_.-])(?i:eval|source|(?:%s)[0-9.]*)(?![A-Za-z0-9_-])" % "|".join(_INTERPRETERS))
_SHELL_VARIABLE = re.compile(r"\$\{?(?:SHELL|BASH)\}?\Z")
_PIPE = re.compile(r"\|&?")
_PIECES = re.compile(r";|&&|\|\||\n")
_QUOTED = re.compile(r"\"[^\"]{0,2000}\"|'[^']{0,2000}'")    # a `;` inside quotes is a letter of a word, and ends no pipeline
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
            word = word.strip("(){}[]")
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
    for number, text in node.parts:
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


def downloads_into_a_shell(text: str) -> bool:
    """Whether a command, or the lines of a script, pipes a download into an interpreter, or hands one a download by substitution."""
    if _DOWNLOAD.search(text) is None:
        return False
    downloaded = False
    for piece in _PIECES.split(_QUOTED.sub(lambda found: _PIECES.sub(" ", found.group()), text)):
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
            if downloads_into_a_shell(line):
                yield number, {}


_RUNTIME = re.compile(r"ACTIONS_RUNTIME_TOKEN|ACTIONS_CACHE_URL|ACTIONS_RESULTS_URL|ghaction-github-runtime", re.I)
_WORD_SECRETS = re.compile("secrets", re.I)


@_rule("WF13")
def _wf13(doc):
    attest = {id(job.node) for job in doc.jobs if job.name == "attest"} if doc.name == "publish.yml" else ()
    for entry in doc.entries:
        if entry.name.lower() == "permissions" and entry.value.kind == "map" and id(entry.map) not in attest:
            for each in entry.value.entries:
                if each.name.lower() == "id-token" and each.value.kind == "scalar" and each.value.text.strip().lower() == "write":
                    yield each.line, {}
    for found in _RUNTIME.finditer(doc.blob()[0]):
        yield doc.line_at(found.start()), {}


@_rule("WF14")
def _wf14(doc):
    for entry in doc.entries:
        if entry.name.lower() == "continue-on-error":
            node = entry.value
            if node.kind != "null" and not (node.kind == "scalar" and node.text.strip().lower() == "false"):
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


def _expressions_of(name: str, node: _Node) -> List[str]:
    """The expressions a value holds. `name` is the key that holds it, in lower case."""
    return _expressions_in(name, node.text)


_ENV_FILE = re.compile(r"GITHUB_ENV|GITHUB_PATH|::set-env|::add-path", re.I)
# What the toolkit of github-script offers to write the same two files
_TOOLKIT = re.compile(r"\b(?:exportVariable|addPath)\b")


@_rule("WF19")
def _wf19(doc):
    seen = set()
    for node in _run_scalars(doc):
        for number, text in node.parts:
            if _ENV_FILE.search(text) and not text.lstrip().startswith("#") and number not in seen:
                seen.add(number)
                yield number, {}
    for entry in doc.entries:
        if entry.name.lower() == "script" and entry.value.kind == "scalar" and doc.in_with(entry) \
                and doc.action_of(doc.holder(entry)) == "actions/github-script":
            node = entry.value
            lines = [number for number, text in node.parts if "${{" in text]
            for number, text in node.parts:
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
    for job in doc.jobs:
        for entry in doc.named(job.node, "secrets"):
            if entry.value.kind == "scalar" and entry.value.text.strip().lower() == "inherit":
                found[entry.line] = "secrets: inherit hands over every secret"
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

def _is_action(path: str) -> bool:
    """Whether a file is a composite action and not a workflow. The folder decides, and the name only where the folder says
    nothing: a file of `.github/workflows/` is a workflow whatever it is called (`action.yml` too), because GitHub runs it as one."""
    if ".github/workflows/" in path:
        return False
    return path.startswith(".github/actions/") or path.rsplit("/", 1)[-1].lower() in ("action.yml", "action.yaml")


def _problems(path: str, text: str, rules: Iterable[str], is_action: bool) -> List[Problem]:
    doc = _Doc(path, text, is_action)
    wanted, found = set(rules), []
    for code in sorted(wanted & set(_CHECKS)):
        function, kinds = _CHECKS[code]
        if doc.kind not in kinds:
            continue
        for count, (line, fields) in enumerate(function(doc)):
            if count >= MAX_PER_RULE:
                break
            found.append(Problem(code, ERROR, path, line, _say(code, path, line, fields)))
    return sorted(set(found), key=lambda each: (each.line, each.code, each.message))


def problems(path: str, text: str, rules: Iterable[str] = GENERIC) -> List[Problem]:
    """The problems of one file, for the rules of `rules`. `path` decides what it is: a workflow or an action."""
    return _problems(path, text, rules, _is_action(path))


def check(tree, ctx=None) -> List[Problem]:
    """Every workflow and every local action of a catalog. Pure: it reads the tree and writes nothing."""
    found, count = [], 0
    for entry in sorted(tree.entries(), key=lambda each: each.path):
        path = entry.path
        if not (_WORKFLOW_FILE.match(path) or _ACTION_FILE.match(path)):
            continue
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
            found.append(Problem("WF21", ERROR, path, 1, _say("WF21", path, 1, {})))
            continue
        found.extend(_problems(path, text, GENERIC, _ACTION_FILE.match(path) is not None))    # the place decides, as above
    return found
