"""CR: the commits of a change: who made them and what they say.

A squash merge copies names into the message of `main`, and a pull request's body can write `Co-authored-by` trailers that
GitHub turns into real ones, so a tool's name must not travel from a branch into `main` as an author, a committer or a
co-author (CR01, an error), and a credit line in a message is worth saying (CR02, a warning: the squash drops it).

What counts as a tool is the tool's own address or credit form, never a first name alone: a person called Claude is not a
tool, `Claude <noreply@anthropic.com>` and `Claude Code` are. The list below is the one list: the template's test of what the
standard renders imports it (`is_tool`, `credited`, `CREDITS`). A commit is named by its role and its first seven characters,
never by an address, and its message is never printed.

Pure: it reads the commits it is given (`ctx.history.commits()`), writes nothing, and a message of a megabyte costs a fraction
of a second (a line is read at most `LINE` characters in, and the first `LINES` lines)."""

import re
from typing import List, Optional, Tuple

from ..report import ERROR, WARNING, Problem
from . import Context, context, problem, table

RULES = table([
    ("CR01", ERROR, "A commit of BASE..HEAD whose author, committer or Co-authored-by trailer names a tool",
     "commit {sha7}: its {role} names a tool. Fix: rewrite the commit under your own name (git commit --amend --reset-author "
     "for the last one), or squash your branch into one commit of yours."),
    ("CR02", WARNING, "A commit message of BASE..HEAD with a tool credit line",
     "commit {sha7}: its message carries a tool credit. The squash drops it from main. Fix: edit the message if you want it "
     "gone."),
])

LINES = 2000    # lines of a message that are read
LINE = 2000     # characters of a line that are read
BOTS = r"(?:copilot[\w-]*|devin-ai-integration|claude|gemini-code-assist|cursor|codex|openai[\w-]*|sweep-ai)"

# The addresses of tools that write commits or put themselves in them, in lower case
ADDRESSES = re.compile(
    r"noreply@anthropic\.com|cursoragent@cursor\.com|noreply@cursor\.(?:com|sh)|noreply@openai\.com|codex@openai\.com|"
    r"noreply@aider\.chat|copilot@github\.com|"
    r"(?:\d{1,12}\+)?" + BOTS + r"(?:\[bot\])?@users\.noreply\.github\.com")
# The credit forms of a name: the tool and what it calls itself (a first name alone is none), and the bots of the AI agents
NAMES = re.compile(
    r"claude code|claude (?:opus|sonnet|haiku)[\w .-]{0,40}|github copilot|copilot swe agent|cursor agent|openai codex|chatgpt|"
    r"gemini (?:cli|code assist)|devin ai|" + BOTS + r"\[bot\]|[\w .-]{1,80}\(aider(?: [\w .-]{0,40}| \([\w .-]{0,40}\))?\)", re.I)
# A line that credits a tool: "Generated with ...", "Written by ...", a link to the tool's page
CREDITS = re.compile(
    r"^\W{0,8}(?:generated|created|written|authored|assisted|made|built|produced|coded)\s{1,8}(?:with|by|using|via)\s{1,8}"
    r"\W{0,4}(?:claude|anthropic|github copilot|copilot|cursor|codex|chatgpt|openai|gemini|aider|devin|windsurf|cline|"
    r"codeium|tabnine|amazon q)\b|\bclaude\.(?:com|ai)/(?:claude-)?code\b", re.I)
_TRAILER = re.compile(r"^\s{0,3}co-authored-by\s{0,3}:\s{0,3}(.{0,200}?)\s{0,3}<([^<>\n]{0,200})>\s{0,3}$", re.I)


def is_tool(name: str, email: str) -> bool:
    """Whether an identity (a name and an address) is a tool's: the address of one, or the name in a form a tool credits itself
    with. A person's first name alone is not."""
    return bool(ADDRESSES.fullmatch((email or "").strip().lower()) or NAMES.fullmatch((name or "").strip()[:200]))


def credited(text: str) -> Optional[str]:
    """The first line of a text that credits a tool, or None. A `Co-authored-by` trailer is CR01's, not a credit line."""
    for line in text.splitlines()[:LINES]:
        line = line[:LINE]
        if CREDITS.search(line) and not _TRAILER.match(line):
            return line.strip()
    return None


def trailers(message: str) -> List[Tuple[str, str]]:
    """[(name, address)] of the `Co-authored-by` trailers of a message."""
    found = []
    for line in message.splitlines()[:LINES]:
        match = _TRAILER.match(line[:LINE + 400])
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """CR01 and CR02 over the commits of `ctx.history`; nothing without one."""
    ctx = ctx if ctx is not None else context(tree)
    if ctx.history is None:
        return []
    found: List[Problem] = []
    for commit in ctx.history.commits():
        short = commit.sha[:7]
        roles = []
        if is_tool(commit.author, commit.author_email):
            roles.append("author")
        if is_tool(commit.committer, commit.committer_email):
            roles.append("committer")
        if any(is_tool(name, email) for name, email in trailers(commit.message)):
            roles.append("Co-authored-by trailer")
        found += [problem(RULES, "CR01", "", 0, sha7=short, role=role) for role in roles]
        if credited(commit.message):
            found.append(problem(RULES, "CR02", "", 0, sha7=short))
    return found


__all__ = ["ADDRESSES", "CREDITS", "NAMES", "RULES", "check", "credited", "is_tool", "trailers"]
