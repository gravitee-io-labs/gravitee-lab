"""ST: the shape of a catalog's tree and its files, before any file is parsed.

The Lab drops some entries silently when it installs a catalog (`store.selected`, main spec). CI turns each
into an error so that the author sees what a person would lose. The two use one definition: `store.rejections` returns the
class of every dropped entry and `store.selected` is defined by it, so ST01 to ST07, ST09, ST10 and ST14 and ST15 are asked
of it, never decided again here. What it cannot know (a `.env` at the top of a catalog is not installed, so not dropped, and
no less a problem) is `store.refusal`'s, for the entries outside the installable places. The rest is this module's: case twins
over the whole tree (ST13), invisible characters (ST11), line endings (ST12), programs and archives (ST16), the files a
standard catalog has (ST17), its license (ST18), Git LFS (ST19), code in a seed (ST20), a scanner's configuration (ST21), the
files that run on a reader's machine (ST22), and a warning for a path in no place the Lab reads (ST08).

A file with a NUL byte is read like any other (`text_of`): only a file that is mostly not UTF-8 (an image, a font) is spared ST11
and, with any NUL, ST12. Paths are compared to a depth of nine folders: a deeper path is an ST04 error already.

Pure: it reads the tree and writes nothing, opens no link, runs nothing of what it reads."""

import re
from typing import Dict, List, Optional, Set, Tuple

from .. import store
from ..report import ERROR, WARNING, Problem
from ..tree import REGULAR
from . import AREAS, Context, basename, context, garbled, is_binary, line_of, newlines, problem, read_limit, say, table, text_of, \
    unit_of

RULES = table([
    ("ST01", ERROR, "Symbolic link (git mode 120000)",
     "{path} is a symbolic link. Fix: commit the file itself; the Lab does not install links."),
    ("ST02", ERROR, "Submodule (160000)",
     "{path} is a submodule. Fix: copy what you need into the catalog; the Lab installs no submodule."),
    ("ST03", ERROR, "A path segment outside [A-Za-z0-9._+-], equal to . or .., empty; a backslash or a control character",
     "{path} has a name the Lab cannot install. Fix: use letters, digits and . _ + - only."),
    ("ST04", ERROR, "Depth over 8, or a path over 200 characters",
     "{path} is too deep or too long. Fix: at most 8 folders and 200 characters."),
    ("ST05", ERROR, ".env, or .env.* other than .env.example",
     "{path} is an environment file. Fix: git rm --cached {path}, add it to .gitignore, and commit a .env.example with "
     "defaults. If it held a real secret, revoke the secret first: removing the file does not remove it from the history."),
    ("ST06", ERROR, "secrets/, .state/, or a name ending .key .pem .p12 .pfx .jks .keystore .crt .csr .srl",
     "{path} looks like a key, a certificate or machine state. Fix: make it with a script at start and ignore it. If it is "
     "real, revoke it first."),
    ("ST07", ERROR, "An agent-instruction file inside a unit (AGENTS.md CLAUDE.md GEMINI.md, .claude/ .agents/ .gemini/ "
                    ".cursor* .windsurf*, .github/copilot-instructions.md)",
     "{path} is an agent instruction file inside an item. Fix: keep it at the top of the catalog or in the kind folder; the "
     "Lab never installs it."),
    ("ST08", WARNING, "A path outside what the Lab reads and what the standard lists",
     "{path} is not in a place the Lab reads. Fix: if it should be installed, move it under addons/, setups/ or "
     "playgrounds/; if it is for maintainers, ignore this."),
    ("ST09", ERROR, "A file over 1 MB (.yml .yaml .md .json) or 2 MB (any other)",
     "{path} is {n} KB, the limit is {m} KB. Fix: shrink it, or link to it."),
    ("ST10", ERROR, "A unit over 2000 files or 25 MB; a catalog over 500 units, 10000 files or 100 MB",
     "{unit} holds {found}, the limit is {limit}. Fix: split it."),
    ("ST11", ERROR, "A zero-width, bidirectional or other invisible format character in a text file (Trojan Source)",
     "{path}:{line} holds an invisible character ({char}). Fix: remove it; a reader cannot see it."),
    ("ST12", WARNING, "CRLF in a text file other than Markdown",
     "{path} uses CRLF line endings. Fix: use LF; .gitattributes does for new files."),
    ("ST13", ERROR, "Two paths equal when case is folded",
     "{path} differs from {other} only by case. Fix: rename one; a Mac cannot hold both."),
    ("ST14", ERROR, "A unit folder or setup file name outside [a-z0-9-]+",
     "{path}: a unit is named with lowercase letters, digits and dashes."),
    ("ST15", ERROR, "A folder directly under addons/ or playgrounds/ without compose.yml, or a file under setups/ that is "
                    "not setups/<name>.yml",
     "{path} is not a complete unit. Fix: add compose.yml, or delete the folder."),
    ("ST16", ERROR, "A program or an archive (ELF, Mach-O, PE, zip, tar, gzip, jar, by magic number; images, fonts and PDFs "
                    "are allowed)",
     "{path} is a program, not source. Fix: add the source and build it in a Dockerfile."),
    ("ST17", ERROR, "A required file is missing (a warning for .github/PULL_REQUEST_TEMPLATE.md)",
     "{file} is missing. Fix: catalog.py upgrade --adopt adds what is missing."),
    ("ST18", ERROR, "LICENSE does not match catalog.yml license",
     "LICENSE does not match license: {spdx} in catalog.yml. Fix: put the text of {spdx} in LICENSE."),
    ("ST19", ERROR, "Git LFS (filter=lfs in .gitattributes)",
     ".gitattributes uses Git LFS. Fix: remove it; the Lab installs no LFS file."),
    ("ST20", WARNING, "Code in seed/** (.py .sh .js .rb, or an executable bit)",
     "{path} is code. A seed brings data; code makes this item \"Brings its own seed code\", and it cannot use a Cloud "
     "Token."),
    ("ST21", ERROR, "A secret-scanner configuration or ignore file anywhere in the catalog",
     "{path} configures a secret scanner. Fix: remove it; the scan that judges this change uses the Lab's own configuration, "
     "and a maintainer decides what it may skip."),
    ("ST22", ERROR, "A file that runs on the machine of whoever opens the folder",
     "{path} would run on a reader's machine. Fix: remove it; a unit is Compose files, data and the sources of its apps."),
])

# The codes store.rejections answers per file (ST10 and ST13 are decided here: one finding for the whole, not one per file)
FROM_STORE = ("ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST09", "ST14", "ST15")
# What a catalog has (ST17). A folder catalog (no .github/) has the first two only
REQUIRED = ("catalog.yml", "README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "REVIEWING.md", ".github/standard.json",
            ".github/CODEOWNERS")
REQUIRED_FOLDER = ("catalog.yml", "README.md")
ADVISED = (".github/PULL_REQUEST_TEMPLATE.md",)
# What a catalog may hold outside the three folders of units (ST08): files and folders at the top
TOP_FILES = ("catalog.yml", "README.md", "LICENSE", "NOTICE", "SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
             "REVIEWING.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "Makefile")
TOP_FOLDERS = ("docs", "tests", ".github", ".catalog", ".agents", ".claude", ".gemini", "addons", "setups", "playgrounds")
# The configuration of a secret scanner (ST21), by file name
SCANNERS = (".gitleaks.toml", ".gitleaksignore", ".trufflehogignore")
SCANNER_PREFIXES = (".secretlintrc", ".detect-secrets")
# Seed files that are code (ST20)
CODE_SUFFIXES = (".py", ".sh", ".js", ".rb")
# Files that run on the machine of whoever opens the folder (ST22)
RUNS_FOLDERS = (".devcontainer", ".husky", ".githooks")
RUNS_FILES = (".envrc", ".pre-commit-config.yaml", ".devcontainer.json")
MAKEFILES = ("makefile", "gnumakefile")
# Windows scripts need their carriage returns
CRLF_OK = (".md", ".markdown", ".bat", ".cmd")
# The first bytes of a program or an archive (ST16)
MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
         b"\xbe\xba\xfe\xca", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b\x08", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c",
         b"\x28\xb5\x2f\xfd")
HEAD = 4096   # the bytes read from a file to see what it is
# The folders of a path that are looked at when paths are compared: a path of more than 8 folders is an ST04 error already, and
# a path of thousands would cost the square of its depth
FOLDERS = 9

# Invisible format characters (category Cf) and the bidirectional controls of Trojan Source (CVE-2021-42574). The soft hyphen
# and a byte order mark at the start of a file are the two a reader may meet in good faith, and are left alone
INVISIBLE = re.compile("[" +
                       "\U00000600-\U00000605\U0000061c\U000006dd\U0000070f\U00000890-\U00000891\U000008e2\U0000180e" +
                       "\U0000200b-\U0000200f\U0000202a-\U0000202e\U00002060-\U00002064\U00002066-\U0000206f\U0000feff" +
                       "\U0000fff9-\U0000fffb\U000110bd\U000110cd\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001" +
                       "\U000e0020-\U000e007f"
                       "]")

# What LICENSE has to say for the licenses the standard can tell apart (ST18): every phrase, in a text with its blanks folded
FINGERPRINTS = {
    "agpl-3.0-only": ("gnu affero general public license", "version 3, 19 november 2007"),
    "apache-2.0": ("apache license", "version 2.0, january 2004"),
    "mit": ("permission is hereby granted, free of charge, to any person obtaining a copy",),
}


def program(head: bytes) -> bool:
    """Whether the start of a file is a program or an archive (ST16): by magic number, never by name."""
    if head.startswith(MAGIC) or head[257:262] == b"ustar" or (head[:3] == b"BZh" and head[4:10] == b"1AY&SY"):
        return True
    if head[:2] == b"MZ":
        offset = int.from_bytes(head[0x3c:0x40], "little")
        return (0x40 <= offset <= len(head) - 4 and head[offset:offset + 4] == b"PE\0\0") or b"\0" in head[:0x40]
    return False


def case_twins(paths: List[str]) -> List[Tuple[str, str]]:
    """[(path, other)]: the places of a list of paths that a file system which does not tell `a` from `A` would write as one.
    A path stands for the folders above it as well: two spellings of one lowercase path, at any depth, are the same place
    there. The later spelling is reported once, at the folder where they part, with the first one."""
    first: Dict[str, str] = {}
    found: List[Tuple[str, str]] = []
    done: Set[Tuple[str, str]] = set()
    for path in paths:
        parts = path.split("/", FOLDERS)[:FOLDERS + 1]
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            key = prefix.lower()
            seen = first.setdefault(key, prefix)
            if seen != prefix:
                if (prefix, seen) not in done:
                    done.add((prefix, seen))
                    found.append((prefix, seen))
                break
    return found


def _folder(path: str) -> str:
    """The unit folder a path is in, as ST14 and ST15 name it: `playgrounds/x` for what is below it, else the path itself."""
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 3 and parts[0] in AREAS and parts[0] != "setups" else path


def _scope_folder(scope: str) -> str:
    """The folder of an overflow's scope (`addon:echo` is `addons/echo`, `setup:x` is `setups/x.yml`)."""
    kind, _, name = scope.partition(":")
    folder = store.FOLDERS.get(kind, "")
    return f"{folder}/{name}.yml" if kind == "setup" else f"{folder}/{name}"


def _size(count: int, unit: str) -> str:
    return f"{count // (1024 * 1024)} MB" if unit == "bytes" else f"{count} {unit}"


def _from_store(entries) -> Tuple[List[Problem], Set[str]]:
    """The findings that are `store.rejections`' and `store.overflows`' and the paths they are about."""
    listing = [store.Entry(each.mode, "0" * 40, each.size, each.path) for each in entries]
    found: List[Problem] = []
    flagged: Set[str] = set()
    unit_codes: Set[Tuple[str, str]] = set()
    for entry, code in store.rejections(listing):
        if code not in FROM_STORE:
            continue
        flagged.add(entry.path)
        if code in ("ST14", "ST15"):
            where = _folder(entry.path)
            if (code, where) in unit_codes:
                continue
            unit_codes.add((code, where))
            found.append(problem(RULES, code, where))
        elif code == "ST09":
            base = basename(entry.path).lower()
            limit = store.TEXT_FILE if base.endswith(store.TEXT_SUFFIXES) else store.OTHER_FILE
            found.append(problem(RULES, code, entry.path, n=-(-entry.size // 1024), m=limit // 1024))
        else:
            found.append(problem(RULES, code, entry.path))
    for over in store.overflows(listing):
        where = _scope_folder(over.scope) if over.scope else ""
        label = where or "The catalog"
        found.append(problem(RULES, "ST10", where, unit=label, found=_size(over.found, over.measure),
                             limit=_size(over.limit, over.measure) if over.measure == "bytes" else over.limit))
    return found, flagged


def _outside(entries, skip: Set[str]) -> Tuple[List[Problem], Set[str]]:
    """ST01 to ST06 and ST09 for what the Lab does not install either (a `.env` at the top, a key under `docs/`). The
    agents' files there are the standard's own (ST07 is about a unit)."""
    found: List[Problem] = []
    flagged: Set[str] = set()
    for entry in entries:
        parts = entry.path.split("/")
        if entry.path in skip or entry.path == "catalog.yml" or parts[0] in AREAS:
            continue
        code = store.refusal(store.Entry(entry.mode, "0" * 40, entry.size, entry.path))
        if code is None or code == "ST07":
            continue
        flagged.add(entry.path)
        if code == "ST09":
            base = basename(entry.path).lower()
            limit = store.TEXT_FILE if base.endswith(store.TEXT_SUFFIXES) else store.OTHER_FILE
            found.append(problem(RULES, code, entry.path, n=-(-entry.size // 1024), m=limit // 1024))
        else:
            found.append(problem(RULES, code, entry.path))
    return found, flagged


def _place(entries, flagged: Set[str]) -> List[Problem]:
    """ST08: a path in no place the Lab reads and the standard does not list."""
    found = []
    for entry in entries:
        path = entry.path
        parts = path.split("/")
        if path in flagged or parts[0] in AREAS or parts[0] in TOP_FOLDERS or path in TOP_FILES:
            continue
        if any("/".join(parts[:count]) in flagged for count in range(1, min(len(parts), FOLDERS + 1))):
            continue   # a folder that is already an error (case twins) is not also a warning for each file below it
        if len(parts) == 1 and path.startswith("."):
            continue
        if runs_on_reader(path):
            continue
        found.append(problem(RULES, "ST08", path))
    return found


def runs_on_reader(path: str) -> bool:
    """ST22: whether a file runs on the machine of whoever opens the folder: an editor or shell hook, a development container,
    or a Makefile inside a unit (the one at the top of the catalog is the standard's own, SD10)."""
    low = path.lower()   # a Mac and a Windows machine read `.VSCODE/tasks.json` as `.vscode/tasks.json`
    parts = low.split("/")
    name = parts[-1]
    if name in RUNS_FILES or any(part in RUNS_FOLDERS for part in parts[:-1]):
        return True
    if len(parts) >= 2 and parts[-2] == ".vscode" and name == "tasks.json":
        return True
    return name in MAKEFILES and unit_of(low) is not None


def scanner_file(path: str) -> bool:
    """ST21: whether a file configures a secret scanner."""
    name = basename(path).lower()
    return name in SCANNERS or name.startswith(SCANNER_PREFIXES)


def _license(text: str, spdx: str) -> bool:
    """Whether the text of a LICENSE is the license named by `spdx`: the phrases of its fingerprint, for the three the
    standard can tell apart; any text for another."""
    wanted = FINGERPRINTS.get(spdx.lower())
    if wanted is None:
        return True
    folded = " ".join(text.lower().split())
    return all(phrase in folded for phrase in wanted)


def _contents(tree, entries, ctx: Context) -> List[Problem]:
    """The rules that read a file's content: ST11, ST12, ST16, ST18 and ST19."""
    found: List[Problem] = []
    for entry in entries:
        if entry.mode not in REGULAR:
            continue
        path, name = entry.path, basename(entry.path)
        small = entry.size <= read_limit(path)
        data = tree.read(path, read_limit(path) if small else HEAD)
        if data is None:
            continue
        if program(data[:HEAD]):
            found.append(problem(RULES, "ST16", path))
            continue
        if not small:
            continue
        # A NUL byte is no reason to leave a file unread (GitHub shows no diff for it, a build or a guide still reads it): the
        # text is read without its NULs. Only a file that is mostly not UTF-8 (an image, a font) is spared ST11, for random
        # bytes spell invisible characters by chance, and ST12, a rule about line endings, has nothing to say of it
        text = text_of(data)
        binary = is_binary(data)
        if not text.isascii() and not (binary and garbled(text)):
            hit = INVISIBLE.search(text, 1 if text.startswith("\U0000feff") else 0)
            if hit:
                found.append(problem(RULES, "ST11", path, line_of(newlines(text), hit.start()), char=f"U+{ord(hit.group()):04X}"))
        if not binary and b"\r\n" in data and not name.lower().endswith(CRLF_OK):
            found.append(problem(RULES, "ST12", path))
        if name == ".gitattributes":
            lfs = re.search(r"(?i)\bfilter\s{0,8}=\s{0,8}lfs\b", text)
            if lfs:
                found.append(problem(RULES, "ST19", path, line_of(newlines(text), lfs.start())))
    spdx = ctx.meta("license")
    if spdx and spdx.lower() in FINGERPRINTS and tree.exists("LICENSE"):
        data = tree.read("LICENSE", 256 * 1024)
        if data is not None and not _license(data.decode("utf-8", "replace"), spdx):
            found.append(problem(RULES, "ST18", "LICENSE", spdx=spdx))
    return found


def _files(entries, ctx: Context) -> List[Problem]:
    """ST17, ST20, ST21 and ST22: by name and by mode."""
    found: List[Problem] = []
    have = {each.path for each in entries}
    for path in (() if ctx.partial else REQUIRED if ctx.git else REQUIRED_FOLDER):
        if path not in have:
            found.append(problem(RULES, "ST17", path, file=path))
    if ctx.git and not ctx.partial:
        found += [problem(RULES, "ST17", path, 0, WARNING, file=path) for path in ADVISED if path not in have]
    for entry in entries:
        path = entry.path
        if scanner_file(path):
            found.append(problem(RULES, "ST21", path))
        if runs_on_reader(path):
            found.append(problem(RULES, "ST22", path))
        if unit_of(path) is not None and "seed" in path.split("/")[:-1] and entry.mode in REGULAR \
                and (path.lower().endswith(CODE_SUFFIXES) or entry.mode == "100755"):
            found.append(problem(RULES, "ST20", path))
    return found


def check(tree, ctx: Optional[Context] = None) -> List[Problem]:
    """ST01 to ST22 over a tree."""
    ctx = ctx if ctx is not None else context(tree)
    entries = tree.entries()
    found, flagged = _from_store(entries)
    more, outside = _outside(entries, flagged)
    found += more
    flagged |= outside
    for path, other in case_twins([each.path for each in entries]):
        found.append(problem(RULES, "ST13", path, other=other))
        flagged.add(path)
    found += _contents(tree, entries, ctx)
    found += _files(entries, ctx)
    flagged |= {each.path for each in found if each.path and each.severity == ERROR}
    found += _place(entries, flagged)
    return found


__all__ = ["RULES", "check", "case_twins", "program", "runs_on_reader", "scanner_file", "say"]
