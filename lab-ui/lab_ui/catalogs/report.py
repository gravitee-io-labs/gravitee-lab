"""One output for CI, `make check` and Share: what a rule found, and how it is said.

A finding is a `Problem(code, severity, path, line, message)`. Three renderings of a list of them:

    text     what `make check` prints: a line per file with a mark, its problems under it, then the verdict
    github   workflow commands: an annotation on the line of the file, in the diff of the pull request
    json     a list of {"code", "severity", "path", "line", "message"}, keys sorted, the same bytes on every run

and one Markdown summary for `$GITHUB_STEP_SUMMARY`. Everything a stranger wrote (a file name, a line of a Dockerfile, the
output of a build) is data: it is escaped, so that it cannot be taken for a workflow command, a table cell, a link or a
line of the report. GitHub shows about ten annotations of each level for a step, so the first ten are annotations and all
of them are in the summary.

Pure: nothing here reads a file, runs a command or writes. Standard library only."""

import json
import secrets
from typing import Dict, Iterable, Iterator, List, NamedTuple, Sequence

ERROR, WARNING, INFO = "error", "warning", "info"
FORMATS = ("text", "github", "json")
# GitHub shows about ten annotations of each level for a step: the first ten here, the rest in the summary
PER_LEVEL = 10
# Rows of the summary table: a step summary may weigh 1 MiB, and a pull request can add thousands of findings
SUMMARY_ROWS = 100
# The workflow command each severity is
LEVELS = {ERROR: "error", WARNING: "warning", INFO: "notice"}
MARKS = {ERROR: "✗", WARNING: "!", INFO: "i"}
BREAKS = "\r\n"   # the characters an annotation keeps, to escape them


class Problem(NamedTuple):
    """One finding. `path` is relative to the catalog ("" for the catalog as a whole), `line` starts at 1 (0 when no line
    applies), `message` says where, what, and the fix."""
    code: str
    severity: str
    path: str
    line: int
    message: str


# ---------------------------------------------------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------------------------------------------------

def escape_data(text) -> str:
    """The data of a workflow command: `%`, CR and LF would end or change it."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text) -> str:
    """A property of a workflow command (file, title): also `:` and `,`, which separate the properties."""
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def printable(text, keep=()) -> str:
    """A text for a terminal or a log: a control character, an escape sequence, a line break and a character that cannot be
    written (the lone surrogate of a file name that is not UTF-8) become a space or U+FFFD, except the characters of
    `keep`, so that no name can write to the screen of the person who reads the report or start a line of its own."""
    text = str(text)
    if text.isprintable():
        return text
    return "".join(c if c.isprintable() or c in keep else ("�" if "\ud800" <= c <= "\udfff" else " ") for c in text)


def _plain(text: str) -> str:
    """Words of a stranger's text with nothing that makes a link, an image, a tag or code."""
    out = text.replace("\\", "\\\\")
    for char in "|`[]<>":
        out = out.replace(char, "\\" + char)
    return out


def _linkable(word: str) -> bool:
    """Whether a word would be turned into a link by GitHub's Markdown without any mark of a link: an address (`http://...`,
    `www.`) or a mail address."""
    low = word.lower()
    return "://" in low or "www." in low or "@" in low


def _cell(text) -> str:
    """A cell of a Markdown table made of a stranger's text: no line, no pipe, and nothing that makes a link, an image, a tag or
    code. A word that GitHub would link by itself (an address that a contributor chose through a file name or the source of an
    image) is put in a code span, where nothing is a link, so a pull request cannot put a clickable address in the summary of the
    maintainers' run; in a span a pipe is `\\|` and a backtick is dropped, and nothing else is special."""
    words = printable(text).split(" ")
    return " ".join("`" + word.replace("`", "'").replace("|", "\\|") + "`" if _linkable(word) else _plain(word) for word in words)


# ---------------------------------------------------------------------------------------------------------------------
# Renderings
# ---------------------------------------------------------------------------------------------------------------------

def annotation(problem: Problem) -> str:
    """The workflow command of a problem: one line, whatever the problem holds."""
    properties = []
    if problem.path:
        properties.append("file=" + escape_property(printable(problem.path, BREAKS)))
        if problem.line and int(problem.line) > 0:
            properties.append(f"line={int(problem.line)}")
    properties.append(f"title={escape_property(printable(problem.code))}")
    level = LEVELS.get(problem.severity, "notice")
    return "::" + level + " " + ",".join(properties) + "::" + escape_data(printable(problem.message, BREAKS))


def ordered(problems: Iterable[Problem], verbose: bool = True) -> List[Problem]:
    """The problems in the order they are shown: by file, line and code, with the same finding once. Without `verbose` the
    information is left out."""
    seen, out = set(), []
    for problem in sorted(problems, key=lambda p: (p.path, int(p.line or 0), p.code, p.message)):
        if problem in seen or (problem.severity == INFO and not verbose):
            continue
        seen.add(problem)
        out.append(problem)
    return out


def counts(problems: Iterable[Problem]) -> Dict[str, int]:
    """{severity: how many} for the three severities."""
    found = {ERROR: 0, WARNING: 0, INFO: 0}
    for problem in problems:
        found[problem.severity] = found.get(problem.severity, 0) + 1
    return found


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def verdict(problems: Sequence[Problem], skipped: Sequence[str] = ()) -> str:
    """The last line of a check: `All good` only when nothing was found and nothing was skipped (a run that skipped
    something never says it is fine), else the counts and the command that explains the first code."""
    found = counts(problems)
    parts = []
    if found[ERROR]:
        parts.append(_plural(found[ERROR], "problem"))
    if found[WARNING]:
        parts.append(_plural(found[WARNING], "warning"))
    if skipped:
        parts.append(_plural(len(skipped), "check") + " skipped")
    if not parts:
        return "All good"
    line = ", ".join(parts)
    shown = [p for p in problems if p.severity in (ERROR, WARNING)]
    if shown:
        first = sorted(shown, key=lambda p: (p.severity != ERROR, p.path, int(p.line or 0), p.code))[0]
        line += f". Run make explain CODE={printable(first.code)} for more."
    return line


def as_text(problems: Sequence[Problem], checked: Iterable[str] = (), skipped: Sequence[str] = (), verbose: bool = False,
            header: str = "") -> str:
    """What `make check` prints: `header`, then a line for each file (`✓` fine, `!` warnings only, `✗` an error) with its
    problems indented under it, then the lines of what was not checked and the verdict. `checked` are the files to list
    when they are fine."""
    shown = ordered(problems, verbose)
    by_path: Dict[str, List[Problem]] = {}
    for problem in shown:
        by_path.setdefault(problem.path, []).append(problem)
    lines = [printable(line) for line in header.split("\n")] if header else []
    names = list(dict.fromkeys(list(checked) + sorted(by_path)))
    for name in names:
        mine = by_path.get(name, [])
        mark = "✗" if any(p.severity == ERROR for p in mine) else "!" if mine else "✓"
        lines.append(f"{mark} {printable(name) or 'the catalog'}")
        lines += [f"    {printable(p.code)} {printable(p.message)}" for p in mine]
    lines += [f"Not checked: {printable(why)}" for why in skipped]
    lines.append("")
    lines.append(verdict(shown, skipped))
    return "\n".join(lines) + "\n"


def as_github(problems: Sequence[Problem], per_level: int = PER_LEVEL, verbose: bool = False) -> str:
    """At most `per_level` annotations of each level, errors first; one notice says how many were left to the summary."""
    shown = ordered(problems, verbose)
    out, given = [], {ERROR: 0, WARNING: 0, INFO: 0}
    for severity in (ERROR, WARNING, INFO):
        for problem in (p for p in shown if p.severity == severity):
            if given[severity] < per_level:
                out.append(annotation(problem))
            given[severity] += 1
    for severity in (ERROR, WARNING, INFO):
        if given[severity] > per_level:
            out.append(f"::notice::{given[severity] - per_level} more {LEVELS[severity]}s are in the summary of the run")
    return "\n".join(out) + ("\n" if out else "")


def as_json(problems: Sequence[Problem], verbose: bool = True) -> str:
    """A list of {"code", "severity", "path", "line", "message"}, keys sorted, ASCII only: the same bytes on every run."""
    rows = [{"code": p.code, "severity": p.severity, "path": p.path, "line": int(p.line or 0), "message": p.message}
            for p in ordered(problems, verbose)]
    return json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render(problems: Sequence[Problem], fmt: str = "text", checked: Iterable[str] = (), skipped: Sequence[str] = (),
           verbose: bool = False, header: str = "") -> str:
    """The report as the text to print, in `text`, `github` or `json`. `checked` (the files to list when they are fine),
    `skipped` (what was not checked), `verbose` (the information too) and `header` apply to `text`."""
    if fmt == "json":
        return as_json(problems, verbose)
    if fmt == "github":
        return as_github(problems, verbose=verbose)
    if fmt == "text":
        return as_text(problems, checked, skipped, verbose, header)
    raise ValueError(f"not a format: {fmt!r} (text, github or json)")


def summary(problems: Sequence[Problem], command: str, skipped: Sequence[str] = (), title: str = "") -> str:
    """The Markdown of `$GITHUB_STEP_SUMMARY`. The first line says how many things are wrong and the one command that
    reproduces them; then a table of every finding up to SUMMARY_ROWS (the log has the rest); then what was not checked."""
    shown = ordered(problems, False)
    found = counts(shown)
    wrong = found[ERROR] + found[WARNING]
    heading = f"### {_cell(title)}\n\n" if title else ""
    if wrong:
        said = [_plural(found[ERROR], "problem")] if found[ERROR] else []
        if found[WARNING]:
            said.append(_plural(found[WARNING], "warning"))
        first = f"{' and '.join(said)} to look at. To see them on your machine, run `{printable(command).replace('`', '')}`."
    elif skipped:
        first = f"Nothing is wrong, but {_plural(len(skipped), 'check')} did not run. To run them on your machine, " \
                f"run `{printable(command).replace('`', '')}`."
    else:
        first = "All good."
    lines = [heading + first]
    if shown:
        lines += ["", "| | Where | Code | What to change |", "|---|---|---|---|"]
        for problem in shown[:SUMMARY_ROWS]:
            where = _cell(problem.path or "the catalog") + (f":{int(problem.line)}" if problem.line and problem.path else "")
            lines.append(f"| {MARKS.get(problem.severity, 'i')} | {where} | {_cell(problem.code)} | {_cell(problem.message)} |")
        if len(shown) > SUMMARY_ROWS:
            lines += ["", f"{len(shown) - SUMMARY_ROWS} more are in the log of the step."]
    if skipped:
        lines += ["", "Not checked:"] + [f"- {_cell(why)}" for why in skipped]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# What a stranger's program printed
# ---------------------------------------------------------------------------------------------------------------------

def untrusted(lines: Iterable[str]) -> Iterator[str]:
    """The lines of output that a stranger's code made (a build log, a test run) between `::stop-commands::<token>` and
    `::<token>::`, with a token that is new for each call and that nothing could have guessed: while it is open the runner
    obeys no workflow command, so `::add-mask::` or `::error::` printed by a Dockerfile do nothing. Every line ends with a
    line feed. If the source fails the region is closed before the error goes on."""
    token = secrets.token_hex(16)
    closing = f"::{token}::\n"
    yield f"::stop-commands::{token}\n"
    try:
        for line in lines:
            line = str(line)
            yield line if line.endswith("\n") else line + "\n"
    except GeneratorExit:
        raise
    except Exception:
        yield closing
        raise
    yield closing


def untrusted_text(text: str) -> str:
    """`untrusted` for a text that is already whole."""
    return "".join(untrusted(str(text).splitlines(True)))
