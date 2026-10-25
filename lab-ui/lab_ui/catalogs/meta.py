"""The x-gravitee block of a compose text, read and changed as a block: its top-level keys, in block style or in flow style (on
one line or several), the way YAML reads them. A key is never looked for by a pattern over the whole file, since the same words
may be in a value, a comment or another block of a file someone else wrote. Only the keys the Lab writes are changed here
(`copy_of`, `private_from`, `private_id`); their values are text.

    value(text, key)            the value of `key` in the block, unquoted, or None
    drop(text, key, override)   the text without `key` in the block; with `override`, a block that takes keys from elsewhere (`<<:`,
                                or the whole block an alias) says `key: ""`, which wins over what it takes
    put(text, key, value)       the text with `key: "value"` first in the block; None when there is no block
"""

import re

HEAD = re.compile(r"""(?m)^(?:"x-gravitee"|'x-gravitee'|x-gravitee)[ \t]*:""")
ANCHOR = re.compile(r"[ \t]*(?:&[^\s{\[,]+[ \t]*)?")
MERGE = "<<"


def _opens(text, index, start):
    """Whether the quote at `index` opens a quoted scalar: it does where a scalar starts (after the brace, a comma, a colon or the
    start of a line), not inside a plain one (`a partner's entries`). A colon right before it is part of a plain one (`a:'b`)
    unless a quoted key ends there (`"a":'b'`), as YAML reads a flow block."""
    back = index - 1
    while back >= start and text[back] in " \t":
        back -= 1
    if back >= start and text[back] == ":" and back == index - 1:
        return back - 1 >= start and text[back - 1] in "\"'"
    return back < start or text[back] in "{[,:\n"


def _scan(text, at):
    """The index where the flow collection opened at `at` closes, quotes, comments and nesting read as YAML reads them; -1."""
    depth, index, size = 0, at, len(text)
    while index < size:
        char = text[index]
        if char == '"' and _opens(text, index, at):
            index += 1
            while index < size and text[index] != '"':
                index += 2 if text[index] == "\\" else 1
        elif char == "'" and _opens(text, index, at):
            index += 1
            while index < size and not (text[index] == "'" and text[index + 1:index + 2] != "'"):
                index += 2 if text[index] == "'" else 1
        elif char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            newline = text.find("\n", index)
            index = size if newline < 0 else newline
            continue
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _flow_parts(text, start, end):
    """[(start, end)] of the entries of the flow mapping between `start` (after its brace) and `end` (its closing brace)."""
    parts, depth, index, begin = [], 0, start, start
    while index < end:
        char = text[index]
        if char in "\"'" and _opens(text, index, start):
            quote, index = char, index + 1
            while index < end and text[index] != quote:
                index += 2 if (quote == '"' and text[index] == "\\") or (quote == "'" and text[index:index + 2] == "''") else 1
        elif char == "#" and text[index - 1] in " \t\n":
            newline = text.find("\n", index, end)
            index = end if newline < 0 else newline
            continue
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append((begin, index))
            begin = index + 1
        index += 1
    if text[begin:end].strip():
        parts.append((begin, end))
    return parts


def _key(text, start, end):
    """(the key of the entry `text[start:end]`, unquoted, or None; where its value starts in `text`)."""
    index = start
    while True:   # blank space, and a comment before the key in a flow mapping on several lines
        while index < end and text[index] in " \t\n":
            index += 1
        if index < end and text[index] == "#":
            newline = text.find("\n", index, end)
            index = end if newline < 0 else newline
            continue
        break
    return _name_at(text, index, end)


def _name_at(text, index, end):
    """(the key written at `index`, unquoted, or None; where its value starts): a quoted key, or a plain one up to its colon. Each
    character is looked at once: no text makes it take longer than reading it."""
    while index < end and text[index] in " \t":
        index += 1
    if index >= end or text[index] in ":#{}[],\n":
        return None, end
    quote = text[index] if text[index] in "\"'" else ""
    if quote:
        close, parts = index + 1, []
        while close < end and text[close] != quote:
            if quote == '"' and text[close] == "\\" and close + 1 < end:   # read with the rest: `\x5f` is `_`
                parts.append(text[close:close + 2])
                close += 2
                continue
            if quote == "'" and text[close:close + 2] == "''":
                parts.append("'")
                close += 2
                continue
            parts.append(text[close])
            close += 1
        if close >= end:
            return None, end
        name, after = _unescaped("".join(parts)) if quote == '"' else "".join(parts), close + 1
    else:
        after = index
        while after < end and text[after] not in ":#{}[],\n":
            after += 1
        name = text[index:after].rstrip(" \t")
    while after < end and text[after] in " \t":
        after += 1
    if after >= end or text[after] != ":" or (not quote and after + 1 < end and text[after + 1] not in " \t\n"):
        return None, end   # after a quoted key, its colon may touch the value (`"a":'b'`), as in JSON
    return name, after + 1


def _head(name):
    return HEAD if name == "x-gravitee" else re.compile(rf"""(?m)^(?:"{re.escape(name)}"|'{re.escape(name)}'|{re.escape(name)})[ \t]*:""")


def _block(text, name="x-gravitee"):
    """(style, start, end) of the block `name` (x-gravitee by default): "flow" (its braces at start and end), "lines" (the lines
    after its key), "alias" (the alias it is); None when the text has none."""
    head = _head(name).search(text)
    if head is None:
        return None
    at = ANCHOR.match(text, head.end()).end()
    if text.startswith("{", at):
        end = _scan(text, at)
        return ("flow", at, end) if end > 0 else None
    if text.startswith("*", at):
        stop = re.compile(r"[\s#]").search(text, at)
        return "alias", at, stop.start() if stop else len(text)
    newline = text.find("\n", at)
    if newline < 0:
        return "lines", len(text), len(text)
    start = index = newline + 1
    while index < len(text):
        end = text.find("\n", index)
        end = len(text) if end < 0 else end
        line = text[index:end]
        if line.strip() and not line.lstrip().startswith("#") and line[:1] not in " \t":
            break
        index = end + 1
    return "lines", start, min(index, len(text))


def _lines(text, start, end):
    """(indent, [(key, start, end)]) of the entries of a block of lines: a key at the block's indentation, with the lines under it."""
    entries, indent, index = [], None, start
    while index < end:
        stop = text.find("\n", index, end)
        stop = end if stop < 0 else stop + 1
        line = text[index:stop]
        content = line.strip()
        width = len(line) - len(line.lstrip(" \t"))
        if content and not content.startswith("#"):
            indent = width if indent is None else indent
            if width == indent:
                entries.append([_key(text, index, stop)[0], index, stop])
            elif entries:
                entries[-1][2] = stop
        elif entries and indent is not None:
            entries[-1][2] = stop
        index = stop
    return indent, [tuple(one) for one in entries]


def _entries(text, name="x-gravitee"):
    """(style, [(key, start, end)], block) of the block's entries."""
    block = _block(text, name)
    if block is None:
        return None, [], None
    style, start, end = block
    if style == "flow":
        return style, [(_key(text, one, two)[0], one, two) for one, two in _flow_parts(text, start + 1, end)], block
    if style == "alias":
        return style, [], block
    return style, _lines(text, start, end)[1], block


ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r", "e": "\x1b",
           " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}
ESCAPED = re.compile(r"\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)", re.S)


def _unescaped(raw):
    """A double-quoted scalar's text with its escapes read as YAML reads them."""
    def one(found):
        code = found.group(1)
        if code[0] in "xuU" and len(code) > 1:
            return chr(min(int(code[1:], 16), 0x10FFFF))
        return ESCAPES.get(code, code)
    return ESCAPED.sub(one, raw)


def _quoted(value):
    """`value` as a YAML double-quoted scalar: a backslash, a quote and what is not printable escaped, so that YAML reads it back
    as it is, spaces at its ends and all."""
    written = []
    for char in str(value):
        point = ord(char)
        if char in '\\"':
            written.append("\\" + char)
        elif char == "\n":
            written.append("\\n")
        elif char == "\t":
            written.append("\\t")
        elif point < 0x20 or 0x7F <= point <= 0x9F or char in "\u2028\u2029\ufeff" or 0xD800 <= point <= 0xDFFF:
            written.append(f"\\u{point:04x}" if not 0xD800 <= point <= 0xDFFF else "\\ufffd")
        else:
            written.append(char)
    return '"' + "".join(written) + '"'


def _unquoted(raw):
    raw = raw.strip()
    comment = re.search(r"[ \t]#", raw)
    if raw[:1] == '"' and raw.rfind('"') > 0:
        return _unescaped(raw[1:raw.rfind('"')])
    if raw[:1] == "'" and raw.rfind("'") > 0:
        return raw[1:raw.rfind("'")].replace("''", "'")
    return (raw[:comment.start()] if comment else raw).strip()


def value(text, key):
    """The value of `key` in the x-gravitee block, unquoted, or None."""
    _, entries, _ = _entries(text or "")
    for name, start, end in entries:
        if name == key:
            return _unquoted(text[_key(text, start, end)[1]:end])
    return None


def replaced(text, key, literal, name="x-gravitee"):
    """The text with the value of `key` in the block `name` written as `literal` (a scalar as YAML reads it: a number, say), what
    follows it on its line (a comment, the end of the line) kept; None when the block has no such key, or says it in a way this
    reader does not change (more than one, an alias, a value on lines of its own)."""
    style, entries, block = _entries(text or "", name)
    found = [(start, end) for one, start, end in entries if one == key]
    if block is None or style == "alias" or len(found) != 1:
        return None
    start, end = found[0]
    at = _key(text, start, end)[1]
    if style == "flow":
        value_end = end
    else:
        line_end = text.find("\n", at, end)
        line_end = end if line_end < 0 else line_end
        comment = re.search(r"[ \t]#", text[at:line_end])
        value_end = at + comment.start() if comment else line_end
        if text[value_end - 1:value_end] == "\r":
            value_end -= 1
        if text[at:value_end].strip() == "" or text[line_end:end].strip():   # a value on lines of its own
            return None
    space = text[at:value_end][:len(text[at:value_end]) - len(text[at:value_end].lstrip(" \t"))] or " "
    trail = text[at:value_end][len(text[at:value_end].rstrip(" \t")):]
    return text[:at] + space + literal + trail + text[value_end:]


def drop(text, key, override=False):
    """The text without every `key` of the x-gravitee block; with `override`, `key: ""` stays when the block takes keys from
    elsewhere (a merge, or the whole block an alias), so that what it takes cannot bring it."""
    style, entries, block = _entries(text or "")
    if block is None:
        return text
    if style == "alias":
        start, end = block[1], block[2]
        return text[:start] + "{" + MERGE + ": " + text[start:end] + ", " + key + ': ""}' + text[end:] if override else text
    gone = [(start, end) for name, start, end in entries if name == key]
    merged = override and any(name == MERGE for name, _, _ in entries)
    if style == "flow":
        kept = [text[start:end] for name, start, end in entries if name != key]
        inner = ",".join(kept)
        if gone and text[block[1] + 1:block[1] + 2] != " ":   # the first entry gone: the next starts where it did
            inner = inner.lstrip(" ")
        if merged:
            inner = f' {key}: ""' + ("," + inner if inner.strip() else "")
        return text[:block[1] + 1] + inner + text[block[2]:]
    for start, end in reversed(gone):
        text = text[:start] + text[end:]
    if merged:
        style, entries, block = _entries(text)
        indent = _lines(text, block[1], block[2])[0] or 2
        text = text[:block[1]] + " " * indent + f'{key}: ""\n' + text[block[1]:]
    return text


def put(text, key, value_):
    """The text with `key: "value"` first in its x-gravitee block (any other `key` dropped); None when it has no block."""
    style, entries, block = _entries(text or "")
    if any(name == key for name, _, _ in entries):   # read again only when there was one to drop
        text = drop(text, key)
        style, entries, block = _entries(text or "")
    if block is None or style == "alias":
        return None
    quoted = _quoted(value_)
    if style == "flow":
        rest = text[block[1] + 1:block[2]]
        return text[:block[1] + 1] + f"{key}: {quoted}" + (", " + rest.lstrip(" ") if rest.strip() else "") + text[block[2]:]
    first = text[entries[0][1]:entries[0][2]] if entries else ""
    indent = (len(first) - len(first.lstrip(" \t"))) or 2
    return text[:block[1]] + " " * indent + f"{key}: {quoted}\n" + text[block[1]:]
