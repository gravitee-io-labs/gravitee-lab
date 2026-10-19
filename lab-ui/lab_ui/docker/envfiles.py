"""The .env files, the core's and each add-on's: read, changed in place or as they would be, quoted the way Compose
reads them."""

import re

from ..paths import ENV_FILE, ROOT, write_atomic

LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SINGLE_QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'")
DOUBLE_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _lines(path):
    file = ROOT / path
    return file.read_text(encoding="utf-8").split("\n") if file.is_file() else []


def _unquote(text):
    """A value as Compose reads it: without its quotes and their escapes, or cut at a trailing comment."""
    text = text.strip()
    m = SINGLE_QUOTED.match(text)
    if m:
        return m.group(1).replace("\\'", "'")
    m = DOUBLE_QUOTED.match(text)
    if m:
        return re.sub(r'\\(["\\$])', r"\1", m.group(1))
    return re.split(r"\s+#", text)[0]


def read(path):
    """The variables of a .env file, unquoted. ${VAR} references are kept as written."""
    return {m.group(1): _unquote(m.group(2)) for m in map(LINE.match, _lines(path)) if m}


def quote(value):
    """A value written so that Compose reads it back unchanged: quoted when it holds spaces, #, quotes, $ or \\."""
    if not re.search(r"[\s#'\"$\\]", value):
        return value
    if not re.search(r"['\\]", value):
        return f"'{value}'"   # single quotes keep everything as it is, but cannot hold a quote or end with a backslash
    return '"' + re.sub(r'(["\\$])', r"\\\1", value) + '"'


def _changed(path, changes):
    """The lines of a .env with these variables set (None removes one), its comments and order kept; new ones go at
    the end."""
    lines = _lines(path)
    while lines and not lines[-1]:
        lines.pop()
    added = {key for key, value in changes.items() if value is not None}
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and m.group(1) in changes:
            value = changes[m.group(1)]
            lines[i] = None if value is None else f"{m.group(1)}={value}"
            added.discard(m.group(1))
    return [line for line in lines if line is not None] + [f"{key}={changes[key]}" for key in sorted(added)]


def changed(path, changes):
    """The text of a .env as write() would leave it, written nowhere."""
    lines = _changed(path, changes)
    return "\n".join(lines).strip("\n") + "\n" if any(line.strip() for line in lines) else ""


def write(path, changes):
    """Sets variables (None removes one), keeping the file's comments and order; new ones go at the end. Values are
    written as given: quote() the ones that are not references. An add-on's .env left without a variable is deleted."""
    file = ROOT / path
    if file != ROOT / ENV_FILE and not any(LINE.match(line) for line in _changed(path, changes)):
        if file.is_file():
            file.unlink()
        return
    write_atomic(file, changed(path, changes), mode=0o600)   # it holds secrets
