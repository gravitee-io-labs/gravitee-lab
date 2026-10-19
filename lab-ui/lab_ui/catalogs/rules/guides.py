"""GD: guides and READMEs. A third-party guide is Markdown rendered in the Lab's origin, which can drive Docker. The Lab
sanitizes it and a Content-Security-Policy is a release gate; these rules are the early warning for the author.

What is read: every `.md` file for its links (GD01, GD02), its raw HTML (GD03) and its images (GD04), and every `.svg` file, and
every `data:image/svg` source, for active content (GD05). Fenced code and inline code are not read, for they show a syntax and
are no link and no tag. A line that starts an HTML block (a `<div>`, say) ends the Markdown around it until a blank line, as the
renderer sees it, and `<pre>`, `<script>`, `<style>`, `<textarea>` and a comment until their closing line, so a fence inside one
hides nothing. A file with a NUL byte is read like any other (`text_of`). What the scan cannot tell it reads as the stricter thing.

Hostile input is what this module is written for. Every pattern is bounded or linear, a tag or a target is read for a few
thousand characters at most and two readings never overlap, so a guide of a megabyte on one line takes a fraction of a second.
Pure: it reads the tree and writes nothing, follows no link and fetches nothing."""

import base64
import bisect
import html
import posixpath
import re
from typing import Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import unquote

from ..report import ERROR, Problem
from ..tree import REGULAR
from . import OTHER_LIMIT, PER_FILE, TEXT_LIMIT, Context, basename, context, line_of, newlines, problem, read_limit, table, text_of

RULES = table([
    ("GD01", ERROR, "A relative link that does not resolve inside the catalog",
     "{path}:{line}: the link {target} does not resolve inside the catalog. Fix: link to a file that is there."),
    ("GD02", ERROR, "A javascript:, data: or vbscript: URL",
     "{path}:{line}: a {scheme}: link. Fix: use https or a relative path."),
    ("GD03", ERROR, "Raw <script, <iframe, <object, <embed, <form, an on*= attribute, srcdoc=, data:text/html, outside fenced "
                    "code and inline code",
     "{path}:{line} holds {what}. A guide is text and diagrams; the Lab runs nothing a guide brings. Fix: show it as code (a "
     "fenced block) or remove it."),
    ("GD04", ERROR, "An image source that is neither relative nor https:",
     "{path}:{line}: the image {src} is not relative or https."),
    ("GD05", ERROR, "An SVG file, or a data:image/svg source, that holds <script, <foreignObject, <iframe, a <use with an "
                    "external href, an on*= attribute or javascript:",
     "{path} is an SVG with active content ({what}). Fix: export it as a plain image, or remove the element."),
])

SHOWN = 80                                       # characters of a target a message shows
_FENCE = re.compile(r"( {0,3})(`{3,}|~{3,})(.*)\Z")
_BLOCK = re.compile(r" {0,3}<(?:[A-Za-z][A-Za-z0-9-]{0,30}(?=[\s/>]|\Z)|/[A-Za-z]|!--|\?|![A-Z])")
# The HTML blocks that CommonMark ends at a closing line, not at a blank line (types 1 to 5): `<pre>`, `<script>`, `<style>`
# and `<textarea>` up to a line that holds one of their closing tags, a comment up to `-->`, `<?` up to `?>`, a declaration up to
# `>`, CDATA up to `]]>`. Whatever is inside is raw HTML, a fence included.
_RAW = (
    (re.compile(r" {0,3}<(?:pre|script|style|textarea)(?:[ \t>]|\Z)", re.I), re.compile(r"</(?:pre|script|style|textarea)>", re.I)),
    (re.compile(r" {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r" {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r" {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
    (re.compile(r" {0,3}<![A-Za-z]"), re.compile(r">")),
)
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:")
_RUNS = re.compile(r"`+")
_INLINE = re.compile(r"\]\(\s{0,4}(?:<([^<>\n]{0,2000})>|((?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.){0,500}\)){1,2000}))", re.S)
_REFERENCE = re.compile(r"^ {0,3}\[[^\]\n]{1,200}\]:[ \t]{0,8}(?:<([^<>\n]{0,2000})>|(\S{1,2000}))", re.M)
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]{0,2000})>")
_ATTRIBUTE = re.compile(r"(?<![A-Za-z0-9_:-])(href|src|xlink:href)\s*=\s*(?:\"([^\"<>]{0,2000})\"|'([^'<>]{0,2000})'|"
                        r"([^\s\"'<>=`]{1,2000}))", re.I)
_TAGS = re.compile(r"<(script|iframe|object|embed|form)(?![A-Za-z0-9_-])", re.I)
_TAGSTART = re.compile(r"<[A-Za-z]")
_EVENT = re.compile(r"(?<![A-Za-z0-9_-])(on[a-z]{3,20})\s*=", re.I)
_SRCDOC = re.compile(r"(?<![A-Za-z0-9_-])srcdoc\s*=", re.I)
_SVG_TAG = re.compile(r"<[A-Za-z][^<>]{0,4000}>")
_USE = re.compile(r"<use\b[^<>]{0,4000}>", re.I)
_USE_HREF = re.compile(r"(?:xlink:)?href\s{0,10}=\s{0,10}[\"']?\s{0,10}([^\s\"'>]{1,500})", re.I)
_SVG_EVENT = re.compile(r"[\s\"'/]on[a-z]{3,20}\s{0,10}=", re.I)


def _raw_end(line: str) -> Tuple[bool, Optional["re.Pattern"]]:
    """(whether the line starts an HTML block that ends at a closing line, the pattern of that line, or None when this very
    line holds it)."""
    for start, end in _RAW:
        opened = start.match(line)
        if opened:
            return True, (None if end.search(line, opened.end()) else end)
    return False, None


def mask(text: str) -> str:
    """The text with fenced code and inline code replaced by blanks (line feeds kept), so that what is left is what a renderer
    reads as Markdown and HTML. A fence opens on a line of at most three blanks, three or more backticks or tildes (a backtick
    fence has no backtick in its info string) and ends on a line of the same character, at least as many, and nothing else; one
    that is not closed runs to the end. A line that starts an HTML block hides no fence until the block ends: the next blank
    line, or, for `<pre>`, `<script>`, `<style>`, `<textarea>` and a comment, their closing line (a blank line inside does not
    end them)."""
    out: List[str] = []
    fence: Optional[Tuple[str, int]] = None
    in_html = False
    until: Optional["re.Pattern"] = None
    for line in text.split("\n"):
        if fence is not None:
            out.append(" " * len(line))
            found = _FENCE.match(line)
            if found and found.group(2)[0] == fence[0] and len(found.group(2)) >= fence[1] and not found.group(3).strip():
                fence = None
            continue
        if until is not None:
            out.append(line)
            if until.search(line):
                until = None
            continue
        if not line.strip():
            in_html = False
            out.append(line)
            continue
        if not in_html:
            found = _FENCE.match(line)
            if found and not (found.group(2)[0] == "`" and "`" in found.group(3)):
                fence = (found.group(2)[0], len(found.group(2)))
                out.append(" " * len(line))
                continue
            started, until = _raw_end(line)
            if started:
                out.append(line)   # raw HTML: nothing in it is code
                continue
            in_html = bool(_BLOCK.match(line))
        out.append(_blank_spans(line) if "`" in line and not in_html else line)
    return "\n".join(out)


def _blank_spans(line: str) -> str:
    """A line with its inline code spans (`a`, ``a`b``) replaced by blanks: a run of backticks opens a span that the next run
    of the same length closes; one with no closer is just backticks."""
    runs = [(found.start(), found.end()) for found in _RUNS.finditer(line)]
    by_length: Dict[int, List[int]] = {}
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)
    pieces: List[str] = []
    done, index = 0, 0
    while index < len(runs):
        length = runs[index][1] - runs[index][0]
        same = by_length[length]
        position = same.index(index) if len(same) < 8 else _locate(same, index)
        if position + 1 < len(same):
            closer = same[position + 1]
            pieces.append(line[done:runs[index][0]])
            pieces.append(" " * (runs[closer][1] - runs[index][0]))
            done = runs[closer][1]
            index = closer + 1
        else:
            index += 1
    pieces.append(line[done:])
    return "".join(pieces)


def _locate(sorted_indexes: List[int], index: int) -> int:
    return bisect.bisect_left(sorted_indexes, index)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"


def normalized(target: str) -> str:
    """A target as a browser reads its scheme: character references decoded, and the blanks and control characters that a
    browser drops removed, in lower case."""
    return re.sub(r"[\x00-\x20\x7f]", "", html.unescape(target[:2000])).lower()


def scheme_of(target: str) -> str:
    """The scheme a target has ("" for a relative one), as a browser reads it."""
    found = _SCHEME.match(normalized(target))
    return found.group()[:-1] if found else ""


def is_relative(target: str) -> bool:
    """Whether a target is a path (no scheme, not `//host`, not `\\host`)."""
    norm = normalized(target)
    return bool(norm) and not _SCHEME.match(norm) and not norm.startswith(("//", "\\", "/\\"))


CAP = 5000   # matches of one pattern that are looked at: a file with that many is red already
FOLDERS = 9  # the folders of a path that are known to a link: a path with more is an ST04 error already


def _blank_lines(masked: str) -> List[int]:
    """The offsets of the blank lines of a masked text (a line feed followed by another, with or without a carriage return)."""
    found = []
    for mark in ("\n\n", "\n\r\n"):
        at = masked.find(mark)
        while at != -1:
            found.append(at)
            at = masked.find(mark, at + 1)
    return sorted(found)


def _in_tag(starts: List[int], blanks: List[int], position: int) -> bool:
    """Whether a name followed by `=` at `position` is inside a tag: the nearest `<` followed by a letter before it (`starts` are
    all of them, in order) is in the same paragraph, with no blank line between (`<img`, not `a < b`). However far the tag
    starts, so that a handler cannot be put out of reach: the two lists are searched, not the text."""
    index = bisect.bisect_right(starts, position) - 1
    if index < 0:
        return False
    gap = bisect.bisect_right(blanks, position) - 1
    return gap < 0 or blanks[gap] < starts[index]


def scan(masked: str) -> Iterator[Tuple[str, int, str]]:
    """(kind, offset, value) of everything in a masked text that the rules look at besides the inline links (`inline`): a `link`
    or `image` with its target, and `html` with what it is (for GD03)."""
    for match in _REFERENCE.finditer(masked):
        yield "link", match.start(), match.group(1) if match.group(1) is not None else match.group(2)
    for match in _AUTOLINK.finditer(masked):
        yield "link", match.start(), match.group(1)
    if "<" in masked or "=" in masked:
        for count, match in enumerate(_ATTRIBUTE.finditer(masked)):
            if count > CAP * 100:
                break
            value = next(group for group in match.groups()[1:] if group is not None)
            start = masked.rfind("<", max(0, match.start() - 1000), match.start())
            tag = re.match(r"<([A-Za-z][A-Za-z0-9-]*)", masked[start:start + 40]) if start != -1 else None
            image = bool(tag and tag.group(1).lower() == "img" and match.group(1).lower() == "src")
            yield "image" if image else "link", match.start(), value
        for count, match in enumerate(_TAGS.finditer(masked)):
            if count > CAP:
                break
            name = match.group(1).lower()
            yield "html", match.start(), f"{'an' if name[0] in 'aeiou' else 'a'} <{name}> tag"
        for count, match in enumerate(_SRCDOC.finditer(masked)):
            if count > CAP:
                break
            yield "html", match.start(), "a srcdoc= attribute"
        starts = [each.start() for each in _TAGSTART.finditer(masked)]
        blanks = _blank_lines(masked) if starts else []
        done = 0
        for match in (_EVENT.finditer(masked) if starts else ()):
            if done > CAP:
                break
            if _in_tag(starts, blanks, match.start()):
                done += 1
                yield "html", match.start(), f"an {match.group(1).lower()}= attribute"
    if "data:" in masked.lower():
        low = masked.lower()
        at = low.find("data:text/html")
        count = 0
        while at != -1 and count < CAP:
            yield "html", at, "a data:text/html URL"
            at = low.find("data:text/html", at + 14)
            count += 1


def is_image(masked: str, offset: int) -> bool:
    """Whether the inline link whose `](` is at `offset` is an image: the `[` before it has a `!` before that."""
    bracket = masked.rfind("[", max(0, offset - 2000), offset)
    return bracket > 0 and masked[bracket - 1] == "!"


def resolves(base: str, target: str, files: Set[str], folders: Set[str]) -> Optional[bool]:
    """Whether a relative target of a file in `base` (a folder of the catalog, "" for the top) leads to a file or a folder of
    the catalog; None when there is nothing to resolve (only a fragment or a query). A `/x` target is from the top, as GitHub
    reads it, and `..` past the top does not resolve."""
    path = unquote(re.split(r"[?#]", target, maxsplit=1)[0])
    if not path:
        return None
    wants_folder = path.endswith("/")
    joined = path.lstrip("/") if path.startswith("/") else posixpath.join(base, path)
    joined = posixpath.normpath(joined)
    if joined == ".":
        return True
    if joined == ".." or joined.startswith("../") or "\0" in joined:
        return False
    return joined in folders or (not wants_folder and joined in files)


def _svg_text(source: str) -> Optional[str]:
    """The text of a `data:image/svg...` source: base64 decoded, or percent-decoded; None if it cannot be read."""
    head, _, body = source.partition(",")
    if not _:
        return None
    if ";base64" in head.lower():
        try:
            return base64.b64decode(body[:2_800_000] + "=" * (-len(body) % 4), validate=False)[:TEXT_LIMIT].decode("utf-8", "replace")
        except ValueError:
            return None
    return unquote(body)


def svg_active(text: str) -> Optional[str]:
    """What active content an SVG holds, or None: a script, a foreignObject (HTML inside), an iframe, an embed or an object, a
    `use` that takes its shape from outside the file, an event attribute, or a `javascript:` URL."""
    low = text.lower()
    for literal, what in (("<script", "<script>"), ("<foreignobject", "<foreignObject>"), ("<iframe", "<iframe>"),
                          ("<embed", "<embed>"), ("<object", "<object>")):
        if literal in low:
            return what
    if "<use" in low:
        for tag in _USE.finditer(text):
            for ref in _USE_HREF.finditer(tag.group()):
                if not ref.group(1).startswith("#"):
                    return "<use> with an external href"
    if "javascript:" in normalized_all(low):
        return "javascript: URL"
    if " on" in low or '"on' in low or "'on" in low or "/on" in low:
        for tag in _SVG_TAG.finditer(text):
            if _SVG_EVENT.search(tag.group()[:4000]):
                return "an event attribute"
    return None


def normalized_all(low: str) -> str:
    """A text with the blanks and control characters removed, so that `java script:` is found like `javascript:`."""
    return re.sub(r"[\x00-\x20\x7f]", "", html.unescape(low[:OTHER_LIMIT]))


def _judge(kind: str, target: str, base: str, files: Set[str], folders: Set[str]) -> List[Tuple[str, dict]]:
    """[(code, fields)] that a link or an image source earns, from GD01 to GD05 (the one that is about a file, GD05, has no line)."""
    target = target.strip()
    scheme = scheme_of(target)
    out: List[Tuple[str, dict]] = []
    if scheme in ("javascript", "vbscript") or (scheme == "data" and kind == "link"):
        return [("GD02", {"scheme": scheme})]
    if kind == "image":
        if scheme and scheme != "https":
            out.append(("GD04", {"src": _clip(target)}))
            if scheme == "data" and normalized(target).startswith("data:image/svg"):
                what = svg_active(_svg_text(target) or "")
                if what:
                    out.append(("GD05", {"what": what}))
            return out
        if not scheme and not is_relative(target):
            return [("GD04", {"src": _clip(target)})]
    if scheme or not is_relative(target) or "${" in target or "{{" in target:
        return out
    if resolves(base, target, files, folders) is False:
        out.append(("GD01", {"target": _clip(target)}))
    return out


def _target(first: str, second: str) -> str:
    """The target of an inline link from the two groups of `_INLINE`, with its backslash escapes undone."""
    target = first or second
    return re.sub(r"\\(.)", r"\1", target) if "\\" in target else target


def check_markdown(path: str, text: str, files: Set[str], folders: Set[str]) -> List[Problem]:
    """GD01 to GD05 for one Markdown file. The inline links are judged one target at a time, and only those with something to
    say are found again to give their line, so a file with half a million links takes the time of reading it."""
    found: List[Problem] = []
    count: Dict[str, int] = {}
    base = posixpath.dirname(path)
    seen: Set[Tuple[str, int, str]] = set()
    masked = mask(text)
    offsets = newlines(masked)
    cache: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {}

    def judge(kind: str, value: str) -> List[Tuple[str, dict]]:
        key = (kind, value)
        if key not in cache:
            cache[key] = _judge(kind, value, base, files, folders)
        return cache[key]

    def add(code: str, line: int, fields: dict):
        key = (code, line, repr(sorted(fields.items())))
        if key in seen or count.get(code, 0) >= PER_FILE:
            return
        seen.add(key)
        count[code] = count.get(code, 0) + 1
        found.append(problem(RULES, code, path, line, **fields))

    if "](" in masked:
        wanted: Dict[str, bool] = {}   # target -> whether its verdict depends on being an image
        for first, second in set(_INLINE.findall(masked)):
            value = _target(first, second)
            if not value:
                continue
            both = bool(scheme_of(value)) or not is_relative(value)
            if judge("link", value) or (both and judge("image", value)):
                wanted[value] = both
        if wanted:
            taken: Dict[str, int] = {}
            for match in _INLINE.finditer(masked):
                value = _target(match.group(1) or "", match.group(2) or "")
                if value in wanted and taken.get(value, 0) < PER_FILE:
                    taken[value] = taken.get(value, 0) + 1
                    kind = "image" if wanted[value] and is_image(masked, match.start()) else "link"
                    for code, fields in judge(kind, value):
                        add(code, 0 if code == "GD05" else line_of(offsets, match.start()), fields)
    for kind, offset, value in scan(masked):
        if kind == "html":
            add("GD03", line_of(offsets, offset), {"what": value})
            continue
        for code, fields in judge(kind, value):
            add(code, 0 if code == "GD05" else line_of(offsets, offset), fields)
    return sorted(found, key=lambda each: (each.line, each.code, each.message))


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """GD01 to GD05 over every Markdown file and every SVG file of a tree."""
    ctx = ctx if ctx is not None else context(tree)
    entries = tree.entries()
    files = {each.path for each in entries}
    folders: Set[str] = set()
    for path in files:
        parts = path.split("/", FOLDERS)   # the last piece is the file, or all that is deeper: a path of thousands of folders
        folders.update("/".join(parts[:count]) for count in range(1, len(parts)))   # would cost the square of its depth
    found: List[Problem] = []
    for entry in entries:
        name = basename(entry.path).lower()
        if entry.mode not in REGULAR or entry.size > read_limit(entry.path):
            continue
        if name.endswith((".md", ".markdown")):
            data = tree.read(entry.path, read_limit(entry.path))
            if data is not None:
                found += check_markdown(entry.path, text_of(data), files, folders)
        elif name.endswith(".svg"):
            data = tree.read(entry.path, read_limit(entry.path))
            if data is not None:
                what = svg_active(text_of(data))
                if what:
                    found.append(problem(RULES, "GD05", entry.path, what=what))
    return found


__all__ = ["RULES", "check", "check_markdown", "is_relative", "mask", "resolves", "scan", "scheme_of", "svg_active"]
