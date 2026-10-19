"""The rules of the catalog repository standard, one module for each family: `structure` (ST), `secrets` (SE),
`commits` (CR), `guides` (GD), `dockerfiles` (DF), `images` (IM), and, from other packages, `workflows` (WF) and `codeowners`
(CO). A rule module is pure: `check(tree, ctx) -> [Problem]` reads a `Tree` (and the commits of a change through `ctx`), never
writes, runs nothing of what it reads and takes no longer than a second for a line of a megabyte. Each module also exports
`RULES`, its table of `Rule(code, severity, rule, message)`: the message is the text of with `{field}` places, the
one text CI, `make check`, Share and `explain` all use.

This file holds what the families share:

- `Rule`, `say` and `problem`: the table row and how a message is made of it. Only a lower-case `{field}` that the rule names
  is replaced, so a message can hold `${NAME:-}` or `${PREFIX_NAME}` as they are.
- `Context` and `context(tree)`: what a rule knows besides the tree (the catalog's `catalog.yml` and `standard.json`, the
  commits of the change, the Lab's seeder), read once.
- `outline(text)`: the block-style YAML of a Compose file as a tree of keys and values, with no YAML library and no
  recursion, for the rules that must know where in a file a key is (SE04, IM).
- The places of a catalog: its units and its Compose files."""

import bisect
import json
import re
from typing import Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

from ..report import ERROR, WARNING, Problem

# The most a rule reads of a file, which is what ST09 lets it be: 1 MB for the files the Lab reads as text (`TEXT_SUFFIXES`), 2 MB
# for any other. A file over its limit is an error of its own, so no file is left unread without a finding
TEXT_LIMIT = 1024 * 1024
OTHER_LIMIT = 2 * 1024 * 1024
TEXT_SUFFIXES = (".yml", ".yaml", ".md", ".json")
# A rule reports at most this many findings of one code in one file
PER_FILE = 100
# The folders of a catalog that hold units , and the kind of each
AREAS = {"addons": "addon", "setups": "setup", "playgrounds": "playground"}
# The Compose files of a unit and of the catalog
COMPOSE_NAMES = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
FAMILIES = ("SD", "ST", "SE", "CR", "WF", "DF", "GD", "CO", "IM")
# The families of content hygiene: a finding that already existed at the base commit is a warning
HYGIENE = ("ST", "GD", "DF", "IM")


class Rule(NamedTuple):
    """A row of the table of a family: the code, its severity, the rule as words it, and the message."""
    code: str
    severity: str
    rule: str
    message: str


def table(rows: Iterable[Tuple[str, str, str, str]]) -> Dict[str, Rule]:
    """{code: Rule} of rows of (code, severity, rule, message)."""
    return {code: Rule(code, severity, rule, message) for code, severity, rule, message in rows}


_PLACE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def say(message: str, **fields) -> str:
    """The message with each `{field}` that `fields` names replaced by its value. A place that is not named stays as it is,
    which is how `${NAME:-}` and `${PREFIX_NAME}` come through, and a value is never read for places of its own."""
    return _PLACE.sub(lambda found: str(fields[found.group(1)]) if found.group(1) in fields else found.group(0), message)


def problem(rules: Dict[str, Rule], code: str, path: str, line: int = 0, severity: Optional[str] = None,
            template: Optional[str] = None, **fields) -> Problem:
    """The Problem of a rule at a place, its message made of the table's (or of `template`, for a rule that words itself two
    ways) with `fields`. `path` and `line` are fields too."""
    rule = rules[code]
    return Problem(code, severity or rule.severity, path, int(line or 0),
                   say(template or rule.message, path=path, line=line, **fields))


# ---------------------------------------------------------------------------------------------------------------------
# What a rule knows besides the tree
# ---------------------------------------------------------------------------------------------------------------------

class Context(NamedTuple):
    """What the rules are given besides the `Tree`. Every field has a default, so a rule works with `Context()`.

    catalog        the scalars of the `x-gravitee-catalog` block of `catalog.yml` (`id`, `license`, `images`, ...)
    standard       `.github/standard.json` as a mapping, or None
    git            whether this is a repository with the standard (it has `.github/`); a folder catalog needs less
    history        a `tree.History` (or anything with `commits()` and `added()`): the commits of the change, or None
    units          the resolved units of the project (`images.units_of`), when the caller has loaded them with Compose
    seeder_images  the repositories (no tag) of `x-gravitee.seeder.image` of the Lab's core file
    seeder_paths   where the Lab's seeder is from the root of a catalog (an installed catalog is three folders below the Lab)
    is_seeder      `f(unit_folder, context) -> bool` instead of seeder_paths, for a caller that knows the Lab's real folders
    private        identifiers of private catalogs (SE06, when Share targets a Public catalog)
    online         the checks that need GitHub were asked for
    ci             the run is CI's
    repository     OWNER/REPO of this repository, when known (IM04)
    base           the commit the change is measured from, or ""
    partial        the tree is a part of a catalog (the files Share staged), so the files a whole catalog has are not asked for
    notes          a list a rule appends a sentence to for each thing it could not read (it becomes `Result.skipped`), or None
    """
    catalog: Optional[dict] = None
    standard: Optional[dict] = None
    git: bool = True
    history: Optional[object] = None
    units: Optional[list] = None
    seeder_images: Tuple[str, ...] = ()
    seeder_paths: Tuple[str, ...] = ("../../../seeder",)
    is_seeder: Optional[Callable] = None
    private: Tuple[str, ...] = ()
    online: bool = False
    ci: bool = False
    repository: str = ""
    base: str = ""
    partial: bool = False
    notes: Optional[list] = None

    def meta(self, key: str) -> str:
        """A scalar of `catalog.yml`, or ""."""
        value = (self.catalog or {}).get(key)
        return value if isinstance(value, str) else ""


def catalog_meta(text: str) -> dict:
    """The scalars of the `x-gravitee-catalog` block of a `catalog.yml`: {key: text}. Read by indentation; a key that holds a
    mapping or a list (`requires`, `shared_settings`) is left out. What cannot be read is an empty answer."""
    found = {}
    outline_ = outline(text)
    for node in outline_.root.children:
        if node.key == "x-gravitee-catalog":
            for child in node.children:
                if child.key and not child.children and child.value and not child.value.startswith(("[", "{", "*")):
                    found[child.key] = scalar(child.value)
    return found


def context(tree, **given) -> Context:
    """The Context of a tree: `catalog` from its `catalog.yml`, `standard` from `.github/standard.json` and `git` from whether
    the tree has a `.github/`, each unless `given`. Anything else in `given` is a field of Context."""
    if "catalog" not in given:
        data = tree.read("catalog.yml", TEXT_LIMIT)
        given["catalog"] = catalog_meta(data.decode("utf-8", "replace")) if data is not None else {}
    paths = [each.path for each in tree.entries()]
    if "standard" not in given:
        data = tree.read(".github/standard.json", 4097)
        standard = None
        if data is not None and len(data) <= 4096:
            try:
                standard = json.loads(data.decode("utf-8"))
            except (ValueError, RecursionError):
                standard = None
        given["standard"] = standard if isinstance(standard, dict) else None
    if "git" not in given:
        given["git"] = given["standard"] is not None or any(path.startswith(".github/") for path in paths)
    return Context(**given)


# ---------------------------------------------------------------------------------------------------------------------
# The places of a catalog
# ---------------------------------------------------------------------------------------------------------------------

def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def read_limit(path: str) -> int:
    """The most a rule reads of the file at `path`: what ST09 lets it be (a test keeps this equal to the Lab's own limits)."""
    return TEXT_LIMIT if path.lower().endswith(TEXT_SUFFIXES) else OTHER_LIMIT


def unit_of(path: str) -> Optional[Tuple[str, str]]:
    """(kind, name) of the unit a path belongs to, or None: `addons/<name>/...` and `playgrounds/<name>/...` below the folder
    and `setups/<name>.yml`. The guides directly under a kind folder belong to no unit."""
    parts = path.split("/")
    kind = AREAS.get(parts[0])
    if kind == "setup":
        return (kind, parts[1][:-4]) if len(parts) == 2 and parts[1].endswith(".yml") else None
    if kind and len(parts) >= 3:
        return kind, parts[1]
    return None


def is_compose(path: str) -> bool:
    """Whether a file is a Compose file of a catalog: the `compose.yml` of a unit (or any `docker-compose.yml` and
    `*.compose.yml`), a setup, or `catalog.yml`."""
    name = basename(path).lower()
    if name in COMPOSE_NAMES or name.endswith((".compose.yml", ".compose.yaml")) or path == "catalog.yml":
        return True
    return path.startswith("setups/") and unit_of(path) is not None


def is_binary(data: bytes) -> bool:
    """Whether a file's start holds a NUL byte. It is a sign of something that may not be text, never a reason to leave a file
    unread: GitHub shows no diff for such a file and a Dockerfile, a script or a Markdown file still works with a NUL in it, so
    a stranger can hide a secret, a `curl | sh` or a Trojan Source character behind one. A rule reads `text_of`; this says only
    that a hygiene rule about line endings (ST12) has nothing to say."""
    return b"\0" in data[:8192]


def text_of(data: bytes) -> str:
    """The text of a file as the rules read it: UTF-8 with what is not valid replaced, and every NUL byte taken out, so that a
    secret kept behind a NUL, or an ASCII text saved as UTF-16 without a mark, is read like any other (the line feeds stay where
    they were, so a line number is the same). A file that starts with a UTF-16 byte order mark is read as UTF-16. Nothing is
    skipped for being binary: the patterns of the security rules are too precise to match the bytes of an image by chance."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") and len(data) > 2:
        return data[2:].decode("utf-16-le" if data[:2] == b"\xff\xfe" else "utf-16-be", "replace").replace("\0", "")
    return data.replace(b"\0", b"").decode("utf-8", "replace")


def garbled(text: str) -> bool:
    """Whether a decoded file is mostly not UTF-8 (a quarter or more of it is the replacement character): an image, a font,
    an archive. Random bytes spell every invisible character by chance, so ST11 leaves such a file alone when it holds NULs."""
    return text.count("�") * 4 >= len(text) > 0


def texts(tree, limit: Optional[int] = None) -> Iterator[Tuple[str, str]]:
    """(path, text) of every regular file of a tree that is at most `limit` bytes (by default what `read_limit` says of its
    name), in the order of the listing, as `text_of` reads it. A file that is larger or cannot be read is not given."""
    for entry in tree.entries():
        wanted = limit if limit is not None else read_limit(entry.path)
        if entry.mode not in ("100644", "100755") or entry.size > wanted:
            continue
        data = tree.read(entry.path, wanted)
        if data is None:
            continue
        yield entry.path, text_of(data)


def line_of(offsets: List[int], position: int) -> int:
    """The number (from 1) of the line that holds the character at `position`, given the offsets of the line feeds."""
    return bisect.bisect_left(offsets, position) + 1


def newlines(text: str) -> List[int]:
    """The offsets of the line feeds of a text, for `line_of`."""
    out, at = [], text.find("\n")
    while at != -1:
        out.append(at)
        at = text.find("\n", at + 1)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Block-style YAML as an outline
# ---------------------------------------------------------------------------------------------------------------------

MAX_LINE = 65536     # a line longer than this is not read as YAML
MAX_NODES = 200000   # nodes of one file
MAX_JOIN = 100       # lines a flow collection or a quoted scalar may continue over
_BLOCK = re.compile(r"[|>][+-]?[0-9]?[+-]?")


class Node:
    """A line of a YAML file: its `key` (None for a list item that is a plain value), the `value` text after the colon
    (comment removed, quotes kept; "" when the value is the block below), its `line`, its `indent` (the column of the key) and
    its `children`. `anchor` is the `&name` it carries, `item` whether it came from a list item."""
    __slots__ = ("key", "value", "line", "indent", "children", "anchor", "item")

    def __init__(self, key, value, line, indent, anchor=None, item=False):
        self.key, self.value, self.line, self.indent = key, value, line, indent
        self.anchor, self.item, self.children = anchor, item, []

    def get(self, key: str) -> Optional["Node"]:
        for child in self.children:
            if child.key == key:
                return child
        return None

    def __repr__(self):
        return f"Node({self.key!r}, {self.value!r}, line {self.line}, {len(self.children)} children)"


class Outline(NamedTuple):
    root: Node
    anchors: Dict[str, Node]


def strip_comment(text: str) -> str:
    """A line without its comment: a `#` that follows a blank outside quotes ends it."""
    if "#" not in text:
        return text.rstrip()
    quote, at, previous = None, 0, " "
    while at < len(text):
        char = text[at]
        if quote:
            if char == "\\" and quote == '"':
                at += 1
            elif char == quote:
                quote = None
        elif char in "\"'" and previous in " \t[{,:":
            quote = char
        elif char == "#" and previous in " \t":
            return text[:at].rstrip()
        previous = char
        at += 1
    return text.rstrip()


def scalar(value: str) -> str:
    """A scalar without its quotes (`"a"` and `'a'` are `a`)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('\\"', '"')
    return value


def _split(content: str) -> Optional[Tuple[str, str]]:
    """(key, rest) of a `key: value` line, or None when the line is a plain value."""
    if not content or content[0] in "[{*&!|>%@`":
        return None
    if content[0] in "\"'":
        end = content.find(content[0], 1)
        while end != -1 and content[0] == "'" and content[end + 1:end + 2] == "'":
            end = content.find("'", end + 2)
        if end == -1:
            return None
        rest = content[end + 1:].lstrip(" ")
        return (content[1:end], rest[1:].strip()) if rest.startswith(":") and rest[1:2] in ("", " ", "\t") else None
    head = content[:1100]
    at = head.find(":")
    while at != -1:
        if at + 1 == len(content) or content[at + 1] in " \t":
            return content[:at].strip(), content[at + 1:].strip()
        at = head.find(":", at + 1)
    return None


def _depth(text: str, depth: int = 0, quote: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """(the depth of brackets, the quote that is open) after `text`, which goes on from a state: so a flow collection that runs
    over many lines is scanned once, not again from its start for each line."""
    for char in text:
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
    return depth, quote


def _balanced(text: str) -> bool:
    """Whether the brackets of a flow collection or the quote of a scalar are closed."""
    if text[:1] in ("[", "{"):
        depth, quote = _depth(text)
        return depth <= 0 and quote is None
    if text[:1] in ("'", '"'):
        return len(text) >= 2 and text.endswith(text[0]) and not text.endswith("\\" + text[0])
    return True


def outline(text: str) -> Outline:
    """The block-style YAML of `text` as a tree. It is a reader for the Compose files of a catalog, not a YAML parser: it
    knows mappings, lists, anchors, aliases, merge keys, comments, block scalars (their text is skipped), and flow collections
    and quoted scalars that run over lines (read as one value). Anything it cannot read is left out, never guessed. It uses no
    recursion and stops at MAX_NODES, so the file of a stranger costs time in proportion to its size."""
    root = Node(None, "", 0, -1)
    stack = [root]
    anchors: Dict[str, Node] = {}
    lines = text.split("\n")
    skip: Optional[int] = None
    at, count = 0, 0
    while at < len(lines):
        raw, number = lines[at].rstrip("\r"), at + 1
        at += 1
        stripped = raw.lstrip(" ")
        if not stripped or stripped[0] == "#":
            continue
        indent = len(raw) - len(stripped)
        if skip is not None:
            if indent > skip:
                continue
            skip = None
        if len(stripped) > MAX_LINE or stripped[0] == "\t":
            continue
        if stripped[:3] in ("---", "...") and stripped[3:4] in ("", " ", "\t"):
            del stack[1:]
            continue
        content = strip_comment(stripped)
        dash = indent
        item = False
        while content == "-" or content.startswith("- "):
            item = True
            rest = content[1:].lstrip(" ")
            indent += len(content) - len(rest)
            content = rest
        if item and not content:
            node = Node(None, "", number, dash, item=True)
            _attach(stack, node, dash, True)
            continue
        found = _split(content)
        key, value = found if found else (None, content)
        anchor = None
        if value.startswith("&"):
            tag, _, value = value.partition(" ")
            anchor = tag[1:]
            value = value.strip()
        while value.startswith("!"):
            _, _, value = value.partition(" ")
            value = value.strip()
        if _BLOCK.fullmatch(value or "x"):
            skip = indent if key is not None else dash
        elif value and not _balanced(value):
            flow = value[:1] in ("[", "{")
            depth, quote = _depth(value) if flow else (0, None)
            for _ in range(MAX_JOIN):
                if at >= len(lines):
                    break
                more = strip_comment(lines[at].strip())
                value += " " + more
                at += 1
                if flow:
                    depth, quote = _depth(" " + more, depth, quote)
                    if depth <= 0 and quote is None:
                        break
                elif _balanced(value):
                    break
        node = Node(key, value, number, indent if key is not None else dash, anchor, item)
        _attach(stack, node, indent if key is not None else dash, key is None)
        if anchor:
            anchors[anchor] = node
        count += 1
        if count > MAX_NODES:
            break
    return Outline(root, anchors)


def _attach(stack: List[Node], node: Node, indent: int, scalar_item: bool) -> None:
    """Puts a node under the node of the stack that holds it: the nearest one that starts to the left of it, or, for a plain
    list item, the key at the same column that introduces the list (`key:` then `- item` at the same indent)."""
    while len(stack) > 1:
        top = stack[-1]
        if top.indent > indent or (top.indent == indent and not (scalar_item and top.key is not None and not top.value)):
            stack.pop()
        else:
            break
    stack[-1].children.append(node)
    stack.append(node)


def walk(root: Node) -> Iterator[Tuple[Node, Tuple[str, ...]]]:
    """Every node below `root` with the keys that lead to it, parents first, without recursion."""
    pending = [(child, (child.key or "",)) for child in reversed(root.children)]
    while pending:
        node, path = pending.pop()
        yield node, path
        pending.extend((child, path + (child.key or "",)) for child in reversed(node.children))


def merged(node: Node, anchors: Dict[str, Node]) -> Dict[str, Node]:
    """The keys of a mapping node as {key: node}, with what its merge keys (`<<: *name`) bring in, the node's own keys first
    and winning. Only a mapping that an anchor of the same file defines can be merged; one level of merges is followed, then
    the anchors' own merges, up to a depth that cannot loop."""
    out: Dict[str, Node] = {}
    seen = set()
    pending = [node]
    depth = 0
    while pending and depth < 16:
        level, pending = pending, []
        for current in level:
            for child in current.children:
                if child.key == "<<":
                    names = re.findall(r"\*([A-Za-z0-9_.-]+)", child.value)
                    pending.extend(anchors[name] for name in names if name in anchors and name not in seen)
                    seen.update(names)
                elif child.key is not None and child.key not in out:
                    out[child.key] = child
        depth += 1
    return out


__all__ = ["AREAS", "COMPOSE_NAMES", "Context", "ERROR", "FAMILIES", "HYGIENE", "Node", "Outline", "PER_FILE", "Problem", "Rule",
           "OTHER_LIMIT", "TEXT_LIMIT", "TEXT_SUFFIXES", "WARNING", "basename", "catalog_meta", "context", "garbled",
           "is_binary", "is_compose", "line_of", "merged", "newlines", "outline", "problem", "read_limit", "say", "scalar",
           "strip_comment", "table", "text_of", "texts", "unit_of", "walk"]
