"""DF: Dockerfiles. A catalog's apps are built on a person's machine, so the build is read for what it asks of the
builder: another build frontend (a program that runs in the build), a file or a repository fetched with no checksum, a download
piped into a shell, a key or host access handed to a build step, a secret written in the image. Four more are warnings that go
into the summary of a review: a base image with no version or not pinned by digest, a final stage that runs as root, and a copy
from an image that is not a stage of the file.

The file is read as text: lines continued with the escape character are one instruction, the parser directives at the top are
read, a here-document belongs to the instruction that opens it, `ONBUILD` is looked through, and a base image or the source of
an `ADD` written with an `ARG` or an `ENV` of the file is resolved from its default. Nothing is executed or fetched. Every
pattern is bounded or linear, and a pipe is looked at from the pipe, so a line of a megabyte takes a fraction of a second.

Which files are Dockerfiles. Compose accepts any name for `build.dockerfile`, so a name is not enough: a file is judged when it is
named like one (`Dockerfile`, `Dockerfile.dev`, `app.Dockerfile`), when a build of the unit names it (`build.docker`,
`Dockerfile.yml`), and, as a net, when a file of a unit starts like one (a parser directive, `FROM`, or an `ARG` and a `FROM`).

Calibration: the 13 Dockerfiles of the first demo catalog held no error, DF22 14 times and DF23 12 times, which is why they are
warnings: a demo image that runs on a laptop and is not pinned or runs as root is a choice to look at, not a fault."""

import bisect
import posixpath
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from ..report import ERROR, WARNING, Problem
from ..tree import REGULAR
from . import PER_FILE, Context, basename, context, problem, read_limit, table, text_of, unit_of
from .images import known, services_of_tree, services_of_units
from .secrets import names_a_secret

RULES = table([
    ("DF01", ERROR, "# syntax= names something other than docker/dockerfile[:tag]",
     "{path}:{line}: # syntax= names another frontend. A build frontend is a program that runs in the build. Use "
     "docker/dockerfile or remove the line."),
    ("DF02", ERROR, "ADD of a URL or a .git address without --checksum=",
     "{path}:{line}: ADD of a URL without --checksum. Fix: download it in a RUN and check its digest, or add "
     "--checksum=sha256:..."),
    ("DF03", ERROR, "curl or wget piped to sh, bash, python, perl, node or sudo",
     "{path}:{line}: a download is piped into a shell. Fix: download it, check a digest, then run it."),
    ("DF04", ERROR, "RUN --security=insecure, --network=host, --mount=type=ssh, --mount=type=secret",
     "{path}:{line}: a build is given no key and no host access. Fix: remove the option."),
    ("DF05", ERROR, "ENV or ARG with a secret-looking name and a literal value",
     "{path}:{line}: {name} has a literal value. Fix: take it as a setting at start, not in the image."),
    ("DF21", WARNING, "A base image with no tag, or :latest",
     "{path}:{line}: the base image {image} has no version. Fix: pin a tag."),
    ("DF22", WARNING, "A base image not pinned by digest",
     "{path}:{line}: the base image {image} is not pinned by digest. Dependabot keeps name:tag@sha256:... current."),
    ("DF23", WARNING, "The final stage runs as root",
     "{path}: the final stage runs as root. Fix: add USER."),
    ("DF24", WARNING, "COPY --from=<an external image>",
     "{path}:{line}: copies from the image {image}. It is listed in the summary with the base images."),
])

_FRONTEND = re.compile(r"(?:docker\.io/)?docker/dockerfile(?::[\w.-]{1,128})?(?:@sha256:[0-9a-f]{64})?")
_DIRECTIVE = re.compile(r"#\s{0,8}([A-Za-z]{1,16})\s{0,8}=\s{0,8}(\S.{0,512})\Z")
_HEREDOC = re.compile(r"<<(-?)\s{0,4}([\"']?)([A-Za-z_][A-Za-z0-9_]{0,64})\2")
_SOURCE_URL = re.compile(r"(?:https?://|git@|git://|ssh://)|\.git(?:#|\Z)", re.I)
# A program that runs what follows it (`sudo -E bash`, `env FOO=1 sh`, `xargs sh`), then the interpreter, with the folders of its
# path in front (`/bin/sh`, `/usr/bin/env python3`)
_WRAP = (r"(?:(?:[\w.+/-]{1,100}/)?(?:sudo|doas|env|exec|xargs|busybox|command|nice|nohup|time|timeout|stdbuf|setsid)"
         r"(?:\s{1,8}-[A-Za-z0-9=.:_-]{1,40}|\s{1,8}[A-Za-z_][A-Za-z0-9_]{0,32}=\S{0,64}|\s{1,8}[0-9.]{1,10}[smhd]?){0,6}\s{1,8}){0,3}")
_SHELL = (r"\s{0,8}" + _WRAP + r"(?:[\w.+/-]{1,100}/)?"
          r"(?:(?:ba|z|da|k|a|c|tc|fi)?sh\b|python[0-9.]{0,4}\b|perl\b|ruby\b|node\b|php\b|lua\b)")
_PIPE_SHELL = re.compile(r"(?<!\|)\|(?!\|)" + _SHELL)
_DOWNLOAD = re.compile(r"\b(?:curl|wget)\b")
# What ends a pipeline: the download and the shell are the two ends of one only if none of these is between them
_SEPARATOR = re.compile(r";|&&|\|\||\n")
_QUOTED = re.compile(r"\"[^\"]{0,2000}\"|'[^']{0,2000}'")
_SUBSTITUTION = re.compile(r"(?:(?<![\w.-])(?:(?:ba|z|da|k|a|c|tc|fi)?sh|python[0-9.]{0,4}|perl|ruby|node|eval|source)(?![\w.-])"
                           r"|(?<![\w./-])\.(?=\s))[^|;&\n]{0,200}?(?:\$\(|<\(|`)\s{0,8}(?:curl|wget)\b")
_ARG_USE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]{0,64})(?::?-([^}${]{0,256}))?\}|([A-Za-z_][A-Za-z0-9_]{0,64}))")
_PAIR = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]{0,255})=(\"[^\"]*\"|'[^']*'|\S+)")
_ROOT = ("root", "0")


class Instruction(NamedTuple):
    """One instruction of a Dockerfile: the line it starts on, its keyword in upper case, and everything after it (a continued
    line joined with a blank, a here-document's lines joined with line feeds)."""
    line: int
    keyword: str
    rest: str


DOCUMENTS = (".md", ".markdown", ".txt", ".json", ".yml", ".yaml", ".toml", ".png", ".svg")   # a file named after a Dockerfile that is not one


def is_dockerfile(path: str) -> bool:
    """Whether a file is a Dockerfile: `Dockerfile`, `Dockerfile.dev`, `app.Dockerfile`, in any case, unless it is a document."""
    name = basename(path).lower()
    if name.endswith(DOCUMENTS):
        return False
    return name.startswith("dockerfile") or name.endswith(".dockerfile")


DIRECTIVES = ("syntax", "escape", "check")


def instructions(text: str) -> Tuple[List[Instruction], Dict[str, Tuple[int, str]]]:
    """(instructions, directives) of a Dockerfile. Comments and empty lines are dropped; the parser directives at the very top
    (`# syntax=...`, `# escape=...`, `# check=...`) are returned as {name: (line, value)} and `escape` changes the continuation
    character. A here-document's text is part of the instruction that opens it. A line is continued while it ends with the
    escape character; comment lines and empty lines between two are skipped, as the builder does."""
    lines = text.split("\n")
    found: List[Instruction] = []
    directives: Dict[str, Tuple[int, str]] = {}
    escape, head, at = "\\", True, 0
    while at < len(lines):
        raw, number = lines[at].rstrip("\r"), at + 1
        at += 1
        stripped = raw.strip()
        if head:
            directive = _DIRECTIVE.match(stripped) if stripped.startswith("#") else None
            if directive and directive.group(1).lower() in DIRECTIVES:
                name, value = directive.group(1).lower(), directive.group(2).strip()
                if name not in directives:
                    directives[name] = (number, value)
                    if name == "escape" and value in ("`", "\\"):
                        escape = value
                continue
            head = False
        if not stripped or stripped.startswith("#"):
            continue
        parts = [stripped]
        while parts[-1].endswith(escape):
            parts[-1] = parts[-1][:-1].rstrip()
            while at < len(lines) and (not lines[at].strip() or lines[at].strip().startswith("#")):
                at += 1
            if at >= len(lines):
                break
            parts.append(lines[at].rstrip("\r").strip())
            at += 1
        joined = " ".join(part for part in parts if part)
        keyword, _, rest = joined.partition(" ")
        keyword, rest = keyword.upper(), rest.strip()
        if keyword in ("RUN", "COPY", "ADD", "ONBUILD"):
            for marker in _HEREDOC.finditer(joined[:65536]):
                strip_tabs, word = marker.group(1) == "-", marker.group(3)
                body = []
                while at < len(lines):
                    row = lines[at].rstrip("\r")
                    at += 1
                    if (row.lstrip("\t") if strip_tabs else row) == word:
                        break
                    body.append(row)
                rest += "\n" + "\n".join(body)
        found.append(Instruction(number, keyword, rest))
    return found, directives


def flags(rest: str) -> Tuple[List[Tuple[str, str]], str]:
    """The leading `--name[=value]` words of an instruction as [(name, value)] (a flag may be given twice), then the rest. The
    text is walked once, whatever the number of flags."""
    given: List[Tuple[str, str]] = []
    at = 0
    while rest.startswith("--", at):
        end = rest.find(" ", at)
        word = rest[at:end] if end != -1 else rest[at:]
        at = end + 1 if end != -1 else len(rest)
        while rest.startswith(" ", at):
            at += 1
        name, _, value = word[2:].partition("=")
        given.append((name.lower(), value))
    return given, rest[at:]


def downloads_to_shell(text: str) -> List[int]:
    """The offsets in a text of a download that is run as it arrives: `curl ... | sh`, `curl ... | gunzip | /bin/sh`, `wget ... |
    sudo bash`, `bash -c "$(curl ...)"`, `eval "$(curl ...)"`, `sh <(wget ...)` and `. <(curl ...)`. The pipes that go to a shell
    are found first and each is looked at backward, for the last download within 2000 characters with no `;`, `&&`, `||` or line
    between, so the search never starts again from every `curl`."""
    found: List[int] = []
    starts = [each.start() for each in _DOWNLOAD.finditer(text)]
    if starts:
        for pipe in _PIPE_SHELL.finditer(text):
            at = pipe.start()
            index = bisect.bisect_right(starts, at) - 1
            if index >= 0 and at - starts[index] <= 2000:
                # filters in between (`| tee f | gunzip |`) are the same pipeline; a `;` inside quotes is a letter of a word
                if not _SEPARATOR.search(_QUOTED.sub("", text[starts[index]:at])):
                    found.append(starts[index])
                    if len(found) >= 100:
                        return sorted(set(found))
    tried = 0
    for marker in ("$(", "<(", "`"):
        at = text.find(marker)
        while at != -1 and tried < 2000:
            tail = text[at + len(marker):at + len(marker) + 12].lstrip()
            if tail.startswith(("curl", "wget")):
                tried += 1
                if _SUBSTITUTION.search(text[max(0, at - 240):at + len(marker) + 12]):
                    found.append(at)
            at = text.find(marker, at + 1)
    return sorted(set(found))


def _resolve(image: str, args: Dict[str, str]) -> Optional[str]:
    """A base image with the `ARG`s of the file put in, or None when one has no default."""
    if len(image) > 1024:
        return None
    missing = False

    def put(found):
        nonlocal missing
        name = found.group(1) or found.group(3)
        value = args.get(name)
        if value is None:
            value = found.group(2)
        if value is None:
            missing = True
            return found.group(0)
        return value

    out = _ARG_USE.sub(put, image)
    return None if missing or "$" in out else out


def _expanded(word: str, variables: Dict[str, str]) -> str:
    """A word of an instruction with the ARG and ENV of the file put in (`${BASE}/a.tgz`), or as it is when one has no value."""
    if "$" not in word:
        return word
    return _resolve(word, variables) or word


def _parts(image: str) -> Tuple[str, bool]:
    """(the tag of an image, whether it is pinned by digest)."""
    name = image.split("@", 1)[0]
    return name.rsplit("/", 1)[-1].partition(":")[2], "@" in image


def _user(rest: str) -> str:
    words = rest.split()
    return words[0].split(":", 1)[0] if words else ""


def check_text(path: str, text: str) -> List[Problem]:
    """DF01 to DF05 and DF21 to DF24 for the text of one Dockerfile."""
    found: List[Problem] = []
    count: Dict[str, int] = {}

    def add(code: str, line: int, **fields):
        if count.get(code, 0) < PER_FILE:
            count[code] = count.get(code, 0) + 1
            found.append(problem(RULES, code, path, line, **fields))

    parsed, directives = instructions(text)
    if "syntax" in directives and not _FRONTEND.fullmatch(directives["syntax"][1]):
        add("DF01", directives["syntax"][0])
    known: Dict[str, int] = {}      # the stages so far, by name (lower case) and by number: the position of each
    users: List[str] = []           # the USER each stage ends with, "" while it has none
    args: Dict[str, str] = {}       # the ARG of the file before its first FROM: the base images are written with them
    variables: Dict[str, str] = {}  # every ARG and ENV with a value, as far as the instruction that reads it: an ADD source
    last = 0
    started = False
    for item in parsed:
        keyword, rest, line = item.keyword, item.rest, item.line
        if keyword == "ONBUILD":
            nested, _, inner = rest.partition(" ")
            keyword, rest = nested.upper(), inner.strip()
        if keyword == "ARG" and not started:
            args.update({name: value.strip("'\"") for name, value in _PAIR.findall(rest)})
        if keyword == "FROM":
            started = True
            words = flags(rest)[1].split()
            if not words:
                continue
            image = words[0]
            name = words[2].lower() if len(words) >= 3 and words[1].lower() == "as" else ""
            parent = known.get(image.lower())
            resolved = _resolve(image, args) if "$" in image else image
            if parent is None and resolved not in (None, "scratch"):
                tag, digest = _parts(resolved)
                if not digest and (not tag or tag == "latest"):
                    add("DF21", line, image=resolved)
                elif not digest:
                    add("DF22", line, image=resolved)
            users.append(users[parent] if parent is not None else "")
            if name:
                known[name] = len(users) - 1
            known[str(len(users) - 1)] = len(users) - 1
            last = line
        elif keyword == "ADD":
            given, tail = flags(rest)
            first = tail.split("\n", 1)[0]
            if "<<" not in first and all(name != "checksum" for name, _ in given):
                if any(_SOURCE_URL.search(_expanded(word, variables)) for word in first.split()[:-1]):
                    add("DF02", line)
        elif keyword == "RUN":
            given, body = flags(rest)
            if any((name == "security" and value == "insecure") or (name == "network" and value == "host")
                   or (name == "mount" and re.search(r"(?:\A|,)type=(?:ssh|secret)(?:,|\Z)", value)) for name, value in given):
                add("DF04", line)
            if downloads_to_shell(body):
                add("DF03", line)
        elif keyword in ("ENV", "ARG"):
            pairs = _PAIR.findall(rest)
            if not pairs and keyword == "ENV" and len(rest.split(None, 1)) == 2:
                pairs = [tuple(rest.split(None, 1))]
            for name, value in pairs:
                variables[name] = _expanded(value.strip("'\""), variables)
                if value.strip("'\"") and "$" not in value and names_a_secret(name):
                    add("DF05", line, name=name)
        elif keyword == "COPY":
            for name, value in flags(rest)[0]:
                if name == "from" and value and value.lower() not in known:
                    add("DF24", line, image=value)
        elif keyword == "USER" and users:
            users[-1] = _user(rest)
    if users and users[-1] in ("", *_ROOT):
        found.append(problem(RULES, "DF23", path, last))
    return found


SNIFF = 4096   # the bytes of a file read to see whether it starts like a Dockerfile
_FIRST = re.compile(r"(FROM|ARG)[ \t]", re.I)
# Files that are never Dockerfiles, so that they are not read to see: images, fonts, documents, archives
NEVER = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".zip",
         ".gz", ".tgz", ".tar", ".md", ".markdown")


def looks_like_dockerfile(head: str) -> bool:
    """Whether the start of a text is the start of a Dockerfile: a parser directive (`# syntax=`, `# escape=`, `# check=`) or `FROM`
    as the first instruction, or `ARG` and a `FROM` after it. A comment or a blank line before it does not matter."""
    for line in head.split("\n", 80)[:80]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            directive = _DIRECTIVE.match(stripped)
            if directive and directive.group(1).lower() in DIRECTIVES:
                return True
            continue
        if not _FIRST.match(stripped):
            return False
        return stripped[:4].upper() == "FROM" or bool(re.search(r"(?im)^[ \t]*FROM[ \t]", head))
    return False


def built_files(tree, ctx: Context) -> Set[str]:
    """The paths of the files the builds of the units name as their Dockerfile, from the Compose files as text (or the units the
    caller loaded): `build: {context: ., dockerfile: build.docker}` is `<folder>/build.docker`. A build from an address, a context
    that holds a variable with no default and a path that leaves the catalog name none."""
    rows = services_of_units(ctx.units) if ctx.units is not None else services_of_tree(tree)
    found: Set[str] = set()
    for row in rows:
        if not row.built or not row.context:
            continue
        where, name = known(row.context), known(row.dockerfile or "Dockerfile")
        if where is None or name is None or "://" in where or posixpath.isabs(where) or posixpath.isabs(name):
            continue
        joined = posixpath.normpath(posixpath.join(row.folder, where, name))
        if joined != ".." and not joined.startswith("../"):
            found.add(joined)
    return found


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """DF01 to DF24 over every Dockerfile of a tree: the files named like one, the files a build of the unit names, and the files
    of a unit that start like one."""
    ctx = ctx if ctx is not None else context(tree)
    entries = [each for each in tree.entries() if each.mode in REGULAR and each.size <= read_limit(each.path)]
    named = built_files(tree, ctx) if any(unit_of(each.path) is not None for each in entries) else set()
    found: List[Problem] = []
    for entry in entries:
        if not (is_dockerfile(entry.path) or entry.path in named):
            if unit_of(entry.path) is None or entry.path.lower().endswith(NEVER):
                continue
            head = tree.read(entry.path, SNIFF)
            if head is None or not looks_like_dockerfile(text_of(head)):
                continue
        data = tree.read(entry.path, read_limit(entry.path))
        if data is not None:
            found += check_text(entry.path, text_of(data))
    return found


__all__ = ["RULES", "built_files", "check", "check_text", "downloads_to_shell", "instructions", "is_dockerfile",
           "looks_like_dockerfile"]
