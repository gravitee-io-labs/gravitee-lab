"""Lints the seed files of playgrounds, read as the seeder reads them: each ${VAR} they use is one the playground's seed
service sets (or one the engine gives the files of an environment), no address is written out (it is a variable, so the
same files work against any platform), each file parses once its variables are replaced, and the folders of
seed/environments agree with themselves.

    python seedlint.py [FOLDER...] [--format text|github|json]

A FOLDER is a folder of playgrounds (each has a compose.yml with a seed service, and a seed/ folder) or one playground, or
both: it is read as a playground, and so is each folder under it, so that a compose file beside the playgrounds hides none of
them. With none, the folder is the one `LAB_PLAYGROUNDS` names (a catalog sets it), else the sample of the engine. A folder that
does not exist is nothing to lint, not an error: a catalog with add-ons and tests only has no `playgrounds/`. The exit code
is 0 when nothing is wrong, 1 when something is, 2 when the command line is wrong. `--format github` prints one workflow
annotation for each problem, on the file as the diff of the repository names it, escaped as the catalog tools escape theirs
(report.py of the Lab UI): `%`, CR and LF in the message, also `:` and `,` in a property, so that a file name cannot start a
command of its own.

`problems(playground_folder)` is the same check for one playground, and the functions the seeder's tests use on their own
(`unset_variables`, `literal_addresses`, `provided_to`, `environment_problems`) live here, so that the engine's tests, the
harness a catalog's tests import (testkit.py, which has no copy of them) and a catalog's CI run one set of checks.

An address is looked for twice. `literal_addresses(text)` reads the text of a file as it is. A YAML or JSON file that has a
backslash may also write an address in escapes (`"https:\\u002f\\u002fhost"` is `https://host` once the file is parsed, and
the text shows no `://`), so `escaped_addresses` reads the strings of the parsed document, the scalars of the composed YAML
and the strings of the JSON (and, inside them, the JSON texts that a string holds, as the `sharedConfiguration` of an
endpoint does), and finds what the text hides. The seeder replaces a variable by its default in the text, before the parser
reads it, so a default is read as the text it becomes. An address under a name that no name system resolves, `.test`,
`.example`, `.invalid` and example.com, .org and .net (RFC 2606 and 6761), cannot reach a service, so a documentation URL
under one is allowed there; the first rule, on the text as it is, has no such allowance. The strings are read as a parser reads
an address: the authority ends at `/`, `?` or `#` and nowhere else, so in `https://example.com'@elsewhere` the quote, a space or
a backslash join the user, and the host is the name after the `@`: a public host, a reserved name or a variable in front of an
`@` is no exemption for the address. A file whose strings the lint cannot read to the end (a variable where YAML wants a name,
JSON texts nested deeper than NESTED) is named, as it cannot be told whether it hides an address: the lint fails closed.

What is read is a stranger's pull request. Nothing of it runs: a file is parsed, never imported or executed. A link is
never followed, whether it is a file or folder of the tree or the folder the command is given, so a seed that links to a file
outside the folder reads nothing of it, and a file that is not a regular file, not UTF-8 or larger than LIMIT is named and
left unread. The patterns of the seeder (`common.VARIABLE`, the one of Compose) read to the end of a text for each default
they open and never close, which takes minutes to hours on a megabyte of `${A:-`: no match can end after the last `}`, so
only the text up to it is matched, and a file that opens many such defaults is named and not handed to the seeder's loader.
The loader of YAML copies the pairs of a mapping into each mapping that merges it with a `<<` key, and a file of twenty
such keys, each merging the one before it twice, makes a million pairs (thirty make the memory of the machine): the seeder's
own loader (`common.parse_yaml`) counts the pairs on the composed document, which builds no value, and refuses a file that
would make the loader copy more than `common.COPIED`; the lint names that file and does not load it either, and counts the
pairs of the front matter of a SKILL.md, which the seeder of the skills loads. Nothing is written. The module reads no setting
of the seed (the contract of the engine scans the seeder's modules for the settings they take): the environment is a parameter.

It reads the seed with the seeder's own loader (`common`), so it needs the seeder's requirements (pip install -r
seeder/requirements.txt, Python 3.10 or later) and nothing else. It is a tool of tests and of a catalog's CI, not of a seed
run: it stays out of the image (seeder/.dockerignore lists it, as it lists testkit.py), and a catalog's CI runs it from the
checkout of the Lab.
"""

import argparse
import contextlib
import json
import os
import re
import stat
import sys
from collections import namedtuple
from pathlib import Path

try:
    import yaml

    from . import common
except ImportError as e:
    raise ImportError("seedlint reads the seed as the seeder does and needs its requirements: pip install -r "
                      "seeder/requirements.txt (Python 3.10 or later), or run make setup") from e

# The folder of playgrounds the command lints when none is given and no catalog is named: a frozen copy in the engine
SAMPLES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "samples"
NAME = "LAB_PLAYGROUNDS"

TEXT = (".yaml", ".yml", ".json", ".md", ".gapl", ".cedar", ".txt")   # whatever the seed may read, not only what it parses
# An address that is not a variable would break on a platform that is not this one (a scheme is written in any case). The
# public hosts the files may name
LITERAL_URL = re.compile(r"(?i:https?)://(?!\$\{)([^\s\"'/\\}:]+)")
PUBLIC = re.compile(r"^(?:www\.w3\.org|json-schema\.org|[a-z0-9.-]*gravitee\.io|[a-z0-9.-]*huggingface\.co"
                    r"|generativelanguage\.googleapis\.com)$")
# The names that no name system resolves (RFC 2606 and 6761): .test, .example and .invalid under at least one label, and
# example.com, .org and .net with the names under them. A bare `test` or `example` is not one: a service of a Compose network
# may be named so. A trailing dot is the same name
RESERVED = re.compile(r"^(?:[a-z0-9_-]+\.)+(?:test|example|invalid)\.?$|^(?:[a-z0-9_-]+\.)*example\.(?:com|org|net)\.?$")
# What may follow a host that is the whole of the authority: a port, and then the end of it. A colon followed by anything
# else is no port: it opens the password of a user (`https://example.com:x@elsewhere`) when the text is read as it is
PORT = re.compile(r":\d{0,5}(?![^\s\"'/?#\\}])")
# The user in front of the host of an address, up to the last `@` of the authority. A parser ends the authority at `/`, `?` or `#`
# and at nothing else: the quote, the space, the tab or the backslash after a name are part of the user, and the host is the name
# after the `@` (`https://example.com'@elsewhere` goes to `elsewhere`)
USER = re.compile(r"((?i:https?)://)[^/?#]*@")
# A text that is JSON when it starts so (common.is_json_text): the platform parses a string like that once more
JSON_TEXT = re.compile(r"\s*[\[{]")
NESTED = 3                 # the JSON texts inside one another that are read (a string of a document, a string of that, and so on)
# The pattern of Compose for a value of the seed service: ${NAME}, ${NAME-default}, ${NAME:-default}, and $$ for a dollar
COMPOSE_VARIABLE = re.compile(r"\$(?:\$|\{[A-Za-z_]\w*(?::?-([^}]*))?\})")
# A default opened and, when nothing closes it, read to the end of the text by every pattern of the seeder
OPENING = re.compile(r"\$\$?\{[A-Za-z_][A-Za-z0-9_]*:-")

SMALL = 256 * 1024         # the most an image of a theme may weigh: a theme is one document of the database
LIMIT = 1024 * 1024        # the most of a file that is read, as the Lab's readers do (Tree.read)
RENDERED = 8 * 1024 * 1024  # the most a file may weigh once its variables are replaced
SLOW = 20_000_000          # characters a file may make the seeder's patterns read for defaults that nothing closes
COPIED = common.COPIED     # the pairs the loader of YAML may copy for the merge keys of a file (about 0.3 s: the real files need 300)
MANY = 20                  # the findings of one kind that are told for a file; the others are counted
SHOWN = 500                # the problems the command prints, the others are counted
MESSAGE = 400              # the characters of a message that are kept: a message may quote a stranger's file
UNCLOSED = ("opens defaults, ${NAME:- , that nothing closes: the seed would read on to the end of the file for each. "
            "Fix: close each with }")
MERGES = common.MERGES
UNREAD = ("has escapes in strings that the lint cannot read to the end (a variable where YAML wants a name or a tag, JSON text "
          f"nested more than {NESTED} deep), so it cannot tell whether an address is written in them. Fix: keep a variable to the "
          "values, and the JSON texts flat")
# The front matter of a SKILL.md, as the seeder of the skills (aim_catalog.skill_files) reads it
FRONT_MATTER = re.compile(r"---\n(.*?)\n---", re.S)
# What a program that prints a line takes for a command of the runner: GitHub's workflow commands, the new and the old one
COMMANDS = ("::", "##[")

Problem = namedtuple("Problem", "check path line message")
Report = namedtuple("Report", "problems playgrounds files")   # the playgrounds that have a seed, the seed files read
TITLES = {"variable": "Seed variable", "address": "Seed address", "read": "Seed file", "parse": "Seed file",
          "environment": "Seed environment", "playground": "Seed service"}


class Unreadable(common.SeedError):
    """A file the lint will not read, and why: a link, a special file, a large one, text that is not UTF-8."""

    def __init__(self, path, why):
        super().__init__(f"{path}: {why}")
        self.path, self.why = path, why


# --- Reading a stranger's files ---------------------------------------------------------

def _read(path):
    """(text, None) of a regular file that is not a link and holds at most LIMIT bytes of UTF-8, else (None, why not)."""
    try:
        info = os.lstat(path)
    except OSError as e:
        return None, f"cannot be read: {e.strerror or e}"
    if stat.S_ISLNK(info.st_mode):
        return None, "is a link. Fix: a seed holds files, not links: copy the file"
    if not stat.S_ISREG(info.st_mode):
        return None, "is not a regular file"
    if info.st_size > LIMIT:
        return None, f"is {info.st_size // 1024} KB, more than the {LIMIT // 1024} KB that is read. Fix: make it smaller"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(LIMIT + 1)
    except OSError as e:
        return None, f"cannot be read: {e.strerror or e}"
    if len(data) > LIMIT:
        return None, f"is more than the {LIMIT // 1024} KB that is read. Fix: make it smaller"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "is not UTF-8 text. Fix: save it as UTF-8"
    return text.replace("\r\n", "\n").replace("\r", "\n"), None


def _walk(seed):
    """(files, odd) under a seed folder: its text files (a TEXT suffix, regular), and the entries that are a link, or a
    special file with a TEXT suffix, as (path, why). A link is not followed, whether it names a file or a folder."""
    found, odd, pending = [], [], [Path(seed)]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                odd.append((path, "is a link. Fix: a seed holds files, not links: copy the file"))
            elif entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                if path.suffix in TEXT:
                    found.append(path)
            elif path.suffix in TEXT:
                odd.append((path, "is not a regular file"))
    return sorted(found), sorted(odd)


def seed_paths(folder):
    """The text files of a playground's seed folder, as paths."""
    return _walk(Path(folder) / "seed")[0]


@contextlib.contextmanager
def _with_environment(values):
    """The environment of this process is exactly `values` inside, and as it was after."""
    saved = dict(os.environ)
    os.environ.clear()
    try:
        os.environ.update(values)   # inside: a name or a value the system refuses stops it half done, and the environment comes back
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _brief(value, quoted=False):
    """A value of a file as a message says it: a text as it is (`quoted`: with its quotes), anything else as str() says it when
    it holds a few values. A document can repeat itself through aliases, and the text of one would not end."""
    if isinstance(value, str):
        return repr(value) if quoted else value
    count, pending = 0, [value]
    while pending:
        node = pending.pop()
        count += 1
        if count > 60:
            return "..."
        if isinstance(node, dict):
            pending.extend(node.keys())
            pending.extend(node.values())
        elif isinstance(node, (list, tuple, set)):
            pending.extend(node)
    return str(value)


def _short(text, limit=MESSAGE):
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --- Variables and addresses ------------------------------------------------------------

def _closed(text):
    """(head, tail) of a text: up to its last `}` and after it. Every pattern of the seeder needs a `}` to match, except
    Compose's `$$`, so nothing is lost by matching the head alone, and a default that opens in the tail is never read on to the
    end by each of the others."""
    cut = text.rfind("}") + 1
    return text[:cut], text[cut:]


def _variables(text):
    """The matches of common.VARIABLE in a text. The pattern runs on the head of the text only (see _closed), where its time is
    linear in the text; on the whole text it is quadratic when many defaults are opened and none is closed."""
    return list(common.VARIABLE.finditer(_closed(text)[0]))


def _cost(text):
    """How many characters the seeder's patterns would read for the defaults of a text that nothing closes."""
    cut = text.rfind("}") + 1
    return len(OPENING.findall(text, cut)) * (len(text) - cut)


def _number(text):
    return 0


def _parsed(text):
    """`json.loads(text)`, but for the numbers: only the strings of a document are read here, and the loader of a number takes a
    time that grows with the square of its digits on the versions of Python that have no limit for them."""
    return json.loads(text, parse_int=_number, parse_float=_number)


def _strings(document):
    """Every text of a JSON document, keys included: the seeder replaces the variables of each one on its own."""
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            pending.extend(node.keys())
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)


def _slow(path, text):
    """How many characters the seeder's loader would read for the defaults that nothing closes, when it is more than SLOW
    (a JSON file is read string by string, so its strings are counted as the loader reads them), else 0."""
    texts = [text]
    if str(path).endswith(".json"):
        try:
            texts = list(_strings(_parsed(text)))
        except (ValueError, RecursionError):
            pass   # not a document: the loader replaces the variables of its text, as it is
    cost = sum(_cost(one) for one in texts)
    return cost if cost > SLOW else 0


def _first_uses(matches, environment, provided=()):
    """[(name, offset)] of the variables of a text that nothing sets, at their first use without a default, in the order
    they appear."""
    seen, found = set(), []
    for m in matches:
        name = m.group(2)
        if m.group(1) or m.group(3) is not None or name in seen:
            continue
        seen.add(name)
        if name not in provided and not environment.get(name):
            found.append((name, m.start()))
    return found


def unset_variables(text, environment, provided=()):
    """The ${VAR} of a file that has no default in the file and no value from the seed service. `$${VAR}` is a literal. The
    names in `provided` are the engine's own (the files of an environment have common.PROVIDED): they need no value."""
    return {name for name, _ in _first_uses(_variables(text), environment, provided)}


def provided_to(path, folder):
    """The names the engine gives a file of the seed: those of an environment for the files under seed/environments, none for
    the others."""
    return common.PROVIDED if path.relative_to(folder / "seed").parts[:1] == ("environments",) else ()


def _reserved(host, text, end):
    """Whether the host of an address, which ends at `end` of the text, is under a name that nothing resolves (RESERVED) and is
    the host of the address: the colon that follows it opens a port, not the password of a user in front of another host."""
    return bool(RESERVED.match(host)) and (text[end:end + 1] != ":" or PORT.match(text, end) is not None)


def _addresses(text, reserved=False, strict=False):
    """[(address, offset)] of the addresses of a text that are not a variable, public hosts apart, at their first use. With
    `reserved`, the names that nothing resolves (RESERVED) are allowed too. With `strict`, the text is read as a parser reads its
    addresses: the user in front of a host is dropped (USER), so that what stands for a host is the name after the last `@` of the
    authority, and the exemptions (a public host, a reserved name, a name that starts with a variable) hold for that name; the
    offsets are those of the text without the users."""
    if strict:
        text = USER.sub(r"\1", text)
    seen, found = set(), []
    for m in LITERAL_URL.finditer(text):
        host = m.group(1).lower()
        if m.group(0) not in seen and not PUBLIC.match(host) and not (reserved and _reserved(host, text, m.end(1))):
            seen.add(m.group(0))
            found.append((m.group(0), m.start()))
    return found


def literal_addresses(text):
    """The addresses of a file that are not a variable, public hosts apart. The text is read as it is: see `escaped_addresses`
    for what it hides."""
    return {address for address, _ in _addresses(text)}


# --- What a file writes in escapes ------------------------------------------------------

def _masked(text, mark, defaults=False):
    """The text with each variable replaced by `mark`, a character that stands for it: a variable stands where the format has
    no room for one (`[${A}]` in a flow collection, a default with a colon), and the text would not parse. With `defaults`, a
    default stays after it, as the seeder puts it in the text before the parser reads it. `$${NAME}` is the page's own and
    stays."""
    pieces, last = [], 0
    for m in _variables(text):
        if not m.group(1):
            pieces += (text[last:m.start()], mark + (m.group(3) or "") if defaults else mark)
            last = m.end()
    return "".join(pieces) + text[last:]


def _scalars(text):
    """([(value, line)], whole) of the scalars of a YAML text, keys too, with their escapes resolved, in the order of the
    documents. They are read from the composed nodes, which hold no value of their own: an alias is not read again, and a text
    that repeats itself is no work. A text that does not compose is read as far as it does (`whole` is then False): the parse
    rule says why it stops."""
    pending, whole = [], True
    try:
        for root in yaml.compose_all(text, Loader=yaml.SafeLoader):
            pending.append(root)
    except Exception:  # noqa: BLE001 - whatever a stranger's text makes the parser do ends the list; the parse rule reports it
        whole = False
    pending.reverse()
    found, seen = [], set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            found.append((node.value, node.start_mark.line + 1))
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(reversed(node.value))
        elif isinstance(node, yaml.MappingNode):
            pending.extend(reversed([child for pair in node.value for child in pair]))
    return found, whole


def _decoded(path, text):
    """([(string, line)], whole) of a YAML or JSON file as the loader reads its strings, with the escapes resolved, when the
    text has a backslash: a text without one writes nothing in escapes. A JSON file is read as JSON, as the seeder reads it
    (line 0: it has none), the variables of each string unreplaced: the seeder replaces them after the escapes are resolved.
    YAML is read as the seeder reads it, with the variables replaced in its text: a variable is parsed as a character that
    stands for it and put back as `${`, so that a host that starts with one is a variable here as it is in the text, and the
    default of the variable stays, as the seeder puts it in the text. A default that the parser cannot take in its place is
    masked. A JSON file that is no document until its variables are replaced (a bare `${PORT}`) is read as YAML, as a scalar of
    YAML is a string of JSON for everything a seed holds, with each variable masked: the seeder escapes a default for JSON
    there, so that the escapes of a default are not resolved. `whole` is False when the strings could not be read to the end."""
    if "\\" not in text or not str(path).endswith((".yaml", ".yml", ".json")):
        return [], True
    in_json = str(path).endswith(".json")
    if in_json:
        try:
            return [(each, 0) for each in _strings(_parsed(text))], True
        except Exception:  # noqa: BLE001 - not JSON, or deeper than the parser reads: the YAML reader goes as far as it can
            pass
    used = set(text)
    mark = next((chr(code) for code in range(0xE000, 0xF900) if chr(code) not in used), None)   # the private use area
    if mark is None:
        return _scalars(text)
    masked = _masked(text, mark, defaults=not in_json)
    found, whole = _scalars(masked)
    if not whole and not in_json:   # a default that the parser cannot take in the place of the variable
        plain = _masked(text, mark)
        if plain != masked:
            found, whole = _scalars(plain)
    return [(value.replace(mark, "${"), line) for value, line in found], whole


def _texts(value, state, depth=0):
    """The text of a string of a document and, one after another, those of the JSON texts inside it: a string that is itself
    a JSON object or array (the `sharedConfiguration` of an endpoint) is parsed again by the platform, and its escapes hide an
    address as well. `state[0]` becomes False for a JSON text nested deeper than NESTED, which is not read."""
    yield value
    if JSON_TEXT.match(value):
        try:
            inner = _parsed(value)
        except Exception:  # noqa: BLE001 - not JSON, or deeper than the parser reads: it is a text like another
            return
        if isinstance(inner, (dict, list)):
            if depth >= NESTED:
                state[0] = False
                return
            for each in _strings(inner):
                yield from _texts(each, state, depth + 1)


def _escaped(path, text, known=()):
    """([(address, line)], whole) of the addresses that a YAML or JSON file writes in escapes, at their first use, but those of
    `known` (the ones the text shows), public hosts and the names that nothing resolves apart. `whole` is False when the file
    has strings that could not be read to the end."""
    decoded, whole = _decoded(path, text)
    seen, found, state = set(known), [], [True]
    for value, line in decoded:
        for string in _texts(value, state):
            if "://" in string:   # nothing else is an address
                for address, _ in _addresses(string, reserved=True, strict=True):
                    if address not in seen:
                        seen.add(address)
                        found.append((address, line))
    return found, whole and state[0]


def escaped_addresses(path, text):
    """The addresses of a YAML or JSON file that its text does not show: they are written in escapes (`https:\\u002f\\u002fhost`),
    and a name that nothing resolves (`.test`, `.example`, `.invalid`, example.com, .org and .net) is not one. `path` says
    which kind of file it is."""
    return {address for address, _ in _escaped(path, text, {a for a, _ in _addresses(text)})[0]}


# --- The seed service of a playground ---------------------------------------------------

def as_mapping(value):
    """A compose `environment` or `labels`, written as a mapping or as a list of NAME=value."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, (list, tuple)):
        return {}
    return {name: text for name, _, text in (item.partition("=") for item in value if isinstance(item, str))}


def compose_value(value):
    """A value as the container gets it when the host sets nothing: ${NAME:-default} is its default, $$ a dollar."""
    head, tail = _closed(value)
    return (COMPOSE_VARIABLE.sub(lambda m: "$" if m.group(0) == "$$" else (m.group(1) or ""), head)
            + tail.replace("$$", "$"))


def seed_service(compose):
    """The service of a playground's compose file that carries the label io.gravitee.role: seed, or None."""
    text, why = _read(compose)
    if why:
        raise Unreadable(compose, why)
    try:
        document = common.parse_yaml(text, compose)
    except common.MergeError:
        raise Unreadable(compose, MERGES) from None
    services = document.get("services") if isinstance(document, dict) else None
    services = services if isinstance(services, dict) else {}
    return next((s for s in services.values()
                 if isinstance(s, dict) and as_mapping(s.get("labels")).get("io.gravitee.role") == "seed"), None)


def seed_environment(service):
    """The variables a seed service sets, as the seed reads them."""
    return {str(name): compose_value("" if value is None else _brief(value))
            for name, value in as_mapping(service.get("environment")).items()}


def _holdable(name, value):
    """Whether the environment of a process can hold this variable, as the lint sets it for the time it loads a file."""
    if not name or "=" in name or "\0" in name or "\0" in value:
        return False
    try:
        os.fsencode(name)
        os.fsencode(value)
    except (UnicodeError, ValueError):
        return False
    return True


def _seeded(folder):
    """(environment, problems) of a playground: the variables its seed service sets when it has a seed folder and a seed
    service, else None for the environment; and what keeps it from being read."""
    compose, seed = folder / "compose.yml", folder / "seed"
    if not os.path.lexists(compose):
        return None, []
    try:
        service = seed_service(compose)
        environment = seed_environment(service) if service else None
    except Unreadable as e:
        return None, [Problem("playground", str(compose), 0, e.why)]
    except Exception as e:  # noqa: BLE001 - whatever a stranger's compose file makes the parser do is a finding, never a crash
        return None, [Problem("playground", str(compose), _line_of(e), "does not parse: " + _short(_flat(e)))]
    if environment is None:
        return None, []
    if os.path.islink(seed):
        return None, [Problem("playground", str(seed), 0, "is a link. Fix: a seed holds files, not links: copy the folder")]
    if not os.path.isdir(seed):
        return None, []
    for name, value in environment.items():
        if not _holdable(name, value):
            return None, [Problem("playground", str(compose), 0,
                                  f"sets {_short(repr(name), 60)}, which no environment can hold: a name is not empty and has no "
                                  "= and no NUL byte, a value has no NUL byte. Fix: correct it")]
    return environment, []


def _playgrounds(root):
    """The folders under `root` that may be playgrounds (a link is no folder here), in name order, and the links."""
    try:
        with os.scandir(root) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
    except OSError:
        return [], []
    return ([Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)],
            [Path(e.path) for e in entries if e.is_symlink() and os.path.isdir(e.path)])


def seeded_playgrounds(root):
    """(folder, environment of its seed service) of each shared playground of `root` that has a seed folder and a seed
    service. A compose file that cannot be read stops it, as the test that asks must not miss a playground."""
    for folder in _playgrounds(root)[0]:
        environment, trouble = _seeded(folder)
        if trouble:
            raise common.SeedError(f"{trouble[0].path}: {trouble[0].message}")
        if environment is not None:
            yield folder, environment


# --- The folders of seed/environments ---------------------------------------------------

def _flat(error):
    return " ".join(str(error).split())


def _line_of(error):
    """The line of a parse error, when it says one."""
    mark = getattr(error, "problem_mark", None)
    return mark.line + 1 if mark is not None else int(getattr(error, "lineno", 0) or 0)


def _present(path):
    """Whether a file is there to read: a link is, and is refused when it is read."""
    return path.is_file() or path.is_symlink()


def _referred(folder, base, name):
    """(file, why) a file of `base` names with `name`: the file, when it is a regular file of `folder` that no link leads
    to, else why not ("outside", "link" or "missing")."""
    if not isinstance(name, str) or not name or "\0" in name:
        return None, "missing"
    root = Path(os.path.normpath(folder))
    try:
        parts = Path(os.path.normpath(os.path.join(base, name))).relative_to(root).parts
    except ValueError:
        return None, "outside"
    if ".." in parts:
        return None, "outside"
    here = root
    for part in parts:
        here = here / part
        try:
            if stat.S_ISLNK(os.lstat(here).st_mode):
                return None, "link"
        except OSError:
            return None, "missing"
    return (here, None) if here.is_file() else (None, "missing")


def _loaded(path, provided=None):
    """common.load(path, provided) for a file that `_read` accepted and whose defaults are closed, once its variables are
    replaced (the environment is the caller's): it raises Unreadable when it is YAML that would make the loader copy minutes of
    pairs for its merge keys. The variables may be what brings the keys, and the loader counts the pairs of the replaced text."""
    try:
        return common.load(path, provided=provided)
    except common.MergeError:
        raise Unreadable(path, MERGES) from None


def _load(path, provided=None):
    """common.load(path) for a file that is safe to hand to it: a regular file, no link, of a size the lint reads, whose
    text does not make the patterns of the seed read to the end of it for each default, nor the loader copy minutes of pairs."""
    text, why = _read(path)
    if why is None and _slow(path, text):
        why = UNCLOSED
    if why:
        raise Unreadable(path, why)
    return _loaded(path, provided)


def _environment_findings(folder, variables, found=None):
    """[(path under the folder, what is wrong)] in one folder of seed/environments, read as the seed reads it (its variables
    replaced, ENV_ID and ENV_NAME given): the problems the files have with one another. They are added to `found` as they
    come, so that a caller that stops on a file of a shape nobody expected keeps those that came before it."""
    found = [] if found is None else found
    id = folder.name

    def rendered(path, given):
        try:
            with _with_environment(variables):
                return _load(path, given)
        except Unreadable as e:
            says(path, e.why)
            return {}

    def says(path, text):
        found.append((str(path.relative_to(folder)), text))

    def specs(*parts, suffixes=(".yaml", ".yml")):
        folders = folder.joinpath(*parts)
        if folders.is_symlink():
            says(folders, "is a link. Fix: a seed holds files, not links: copy the folder")
            return []
        return sorted(p for p in folders.iterdir() if p.name.endswith(suffixes)) if folders.is_dir() else []

    path = folder / "environment.yaml"
    if not _present(path):
        found.append(("environment.yaml", "missing"))
        return found
    identity = rendered(path, {"ENV_ID": id})
    if identity.get("id") != id:
        says(path, f"id is {_brief(identity.get('id'), quoted=True)}, the folder is {id!r}")
    if not identity.get("name"):
        says(path, "name is missing")
    if not re.fullmatch(r"[a-z0-9-]+", id) or id == "default":
        says(path, "an environment is named with lowercase letters, digits and dashes, and is not the platform's own")
    given = {"ENV_ID": id, "ENV_NAME": identity.get("name") or ""}

    users = set()
    if _present(folder / "users.yaml"):
        for user in rendered(folder / "users.yaml", given).get("users") or []:
            if not (user.get("source") and user.get("sourceId")):
                says(folder / "users.yaml", f"a user needs a source and a sourceId: {_brief(user)}")
            users.add(user.get("sourceId"))
    hrids = set()
    for path in specs("groups"):
        group = rendered(path, given)
        hrids.add(group.get("hrid"))
        if not (group.get("hrid") and group.get("name")):
            says(path, "a group needs a hrid and a name")
        for member in group.get("members") or []:
            if not (member.get("source") and member.get("sourceId")):
                says(path, f"a member needs a source and a sourceId: {_brief(member)}")
            elif member["sourceId"] not in users and not member.get("external"):
                says(path, f"{_brief(member['sourceId'])} is no user of users.yaml: add it there, or flag the member `external: true`")
    apis, cross_ids = {}, set()
    for path in specs("apis", suffixes=(".json",)):
        definition = rendered(path, given)
        api = definition.get("api") or {}
        apis[api.get("name")] = {plan.get("name"): plan for plan in definition.get("plans") or []}
        if api.get("crossId"):
            cross_ids.add(api["crossId"])
        if "id" in api or any("id" in plan for plan in definition.get("plans") or []):
            says(path, "an API of an environment has no id and its plans none: the same feed is in many environments, and ids are global")
        for hrid in api.get("groups") or []:
            if hrid not in hrids:
                says(path, f"api.groups names {_brief(hrid)}, which is no group of the folder")
    applications = {}
    for path in specs("applications"):
        application = rendered(path, given)
        if application.get("name") in applications:
            says(path, f"two applications are named {_brief(application.get('name'))}: the seed would write each over the other on every run")
        applications[application.get("name")] = application
        for hrid in application.get("groups") or []:
            if hrid not in hrids:
                says(path, f"groups names {_brief(hrid)}, which is no group of the folder")
    for path in specs("subscriptions"):
        for sub in rendered(path, given).get("subscriptions") or []:
            where = f"{_brief(sub.get('application'))} → {_brief(sub.get('api'))} ({_brief(sub.get('plan'))})"
            plan = (apis.get(sub.get("api")) or {}).get(sub.get("plan"))
            if sub.get("application") not in applications:
                says(path, f"{where}: no such application in the folder")
            elif sub.get("api") not in apis:
                says(path, f"{where}: no such API in the folder")
            elif not plan:
                says(path, f"{where}: no such plan on that API")
            else:
                says_about_subscription(path, where, sub, plan, says)
    for path in specs("portal", "pages"):
        page = rendered(path, given)
        if page.get("file"):
            _, why = _referred(folder, path.parent, page["file"])
            if why == "outside":
                says(path, f"file {_brief(page['file'])} is outside the folder")
            elif why:
                says(path, f"file {_brief(page['file'])} does not exist")
    theme = folder / "portal" / "theme.yaml"
    if _present(theme):
        for what, asset in (rendered(theme, given).get("assets") or {}).items():
            image, why = _referred(folder, theme.parent, asset)
            if why == "outside":
                says(theme, f"assets.{_brief(what)} {_brief(asset)} is outside the folder")
            elif why:
                says(theme, f"assets.{_brief(what)} {_brief(asset)} does not exist")
            elif image.stat().st_size > SMALL:
                says(theme, f"assets.{_brief(what)} {_brief(asset)} weighs more than {SMALL // 1024} KB")
    for path in specs("portal", "listings"):
        for item in rendered(path, given).get("apis") or []:
            if item.get("apiHrid") not in cross_ids:
                says(path, f"apiHrid {_brief(item.get('apiHrid'))} is the crossId of no API of the folder")
    return found


def environment_problems(folder, variables):
    """What is wrong in one folder of seed/environments, read as the seed reads it (its variables replaced, ENV_ID and ENV_NAME
    given): the problems the files have with one another, one text each."""
    return [f"{path}: {text}" for path, text in _environment_findings(folder, variables)]


def says_about_subscription(path, where, sub, plan, says):
    """What the platform refuses of a subscription to this plan, from its file."""
    pushes = plan.get("mode") == "PUSH"
    configuration = sub.get("consumerConfiguration")
    if pushes and not configuration:
        says(path, f"{where}: a push plan needs a consumerConfiguration")
    if configuration and not pushes:
        says(path, f"{where}: only a push plan takes a consumerConfiguration")
    token = ((configuration or {}).get("entrypointConfiguration") or {}).get("auth", {}).get("token")
    if token is not None and not (isinstance(token, dict) and token.get("value")):
        says(path, f"{where}: the token of a webhook is an object, token: {{value: ...}}")
    if sub.get("pending") and plan.get("validation") != "MANUAL":
        says(path, f"{where}: a subscription stays pending only on a plan that validates by hand")
    if sub.get("pending") and sub.get("apiKey"):
        says(path, f"{where}: a pending subscription takes no apiKey")
    if plan.get("commentRequired") and not sub.get("comment"):
        says(path, f"{where}: the plan asks for a comment")
    if sub.get("apiKey") and (plan.get("security") or {}).get("type") != "API_KEY":
        says(path, f"{where}: an apiKey needs a plan with API keys")


# --- One playground, and a folder of them -----------------------------------------------

def _parse(path, text, matches, environment, provided):
    """(check, message, line) when a seed file does not parse once its variables are replaced, or would take the loader
    minutes to, else None."""
    longest = max((len(value) for value in environment.values()), default=0)
    if len(text) + len(matches) * longest > RENDERED:
        return ("parse", f"would weigh more than {RENDERED // 1048576} MB once its variables are replaced. Fix: use fewer, "
                         "or shorter values", 0)
    try:
        with _with_environment(environment):
            _loaded(path, dict.fromkeys(provided, "x"))
    except Unreadable as e:
        return "read", e.why, 0
    except Exception as e:  # noqa: BLE001 - whatever a stranger's file makes the loader do is a finding, never a crash
        return "parse", "does not parse once its variables are replaced: " + _short(_flat(e)), _line_of(e)
    return None


def _address_problems(path, text):
    """(problems, whole) of the addresses a seed file writes out, as its text shows them and, for a YAML or JSON file with a
    backslash, as its strings have them once the escapes are resolved (those say so). MANY are told, the others counted.
    `whole` is False when the strings of the file could not be read to the end."""
    shown = _addresses(text)
    hidden, whole = _escaped(path, text, {address for address, _ in shown})
    fix = "Fix: an address is a ${VARIABLE} set by the seed service."
    found = [Problem("address", str(path), text.count("\n", 0, offset) + 1, f"{_short(address, 100)} is written out. {fix}")
             for address, offset in shown[:MANY]]
    found += [Problem("address", str(path), line, f"{_short(address, 100)} is written out in escapes. {fix}")
              for address, line in hidden[:max(0, MANY - len(shown))]]
    if len(shown) + len(hidden) > MANY:
        found.append(Problem("address", str(path), 0, f"{len(shown) + len(hidden) - MANY} more addresses are written out."))
    return found, whole


def _front_matter(text):
    """The front matter of a SKILL.md, as the seeder of the skills reads it, else an empty text."""
    head = FRONT_MATTER.match(text)
    return head.group(1) if head else ""


def _lint(folder):
    """The Report of one playground."""
    environment, out = _seeded(folder)
    if environment is None:
        return Report(out, 0, 0)
    found, odd = _walk(folder / "seed")
    unread, read = set(), 0
    for path, why in odd:
        out.append(Problem("read", str(path), 0, why))
        unread.add(path)
    for path in found:
        text, why = _read(path)
        if why:
            out.append(Problem("read", str(path), 0, why))
            unread.add(path)
            continue
        read += 1
        matches = _variables(text)
        provided = provided_to(path, folder)
        blocked = False
        if _slow(path, text):
            out.append(Problem("read", str(path), 0, UNCLOSED))
            blocked = True
        else:
            unset = _first_uses(matches, environment, provided)
            for name, offset in unset[:MANY]:
                out.append(Problem("variable", str(path), text.count("\n", 0, offset) + 1,
                                   f"{name} has no value: the seed service sets none. Fix: set it, with a value, in the seed "
                                   f"service's environment, or give it a default (${{{name}:-default}})."))
            if len(unset) > MANY:
                out.append(Problem("variable", str(path), 0, f"{len(unset) - MANY} more variables have no value."))
            blocked = bool(unset)
        told, whole = _address_problems(path, text)
        out.extend(told)
        if path.name == "SKILL.md" and common.merged_pairs(_front_matter(text)) > COPIED:
            out.append(Problem("read", str(path), 0, MERGES))
        if not blocked and path.suffix in (".yaml", ".yml", ".json"):
            failure = _parse(path, text, matches, environment, provided)
            if failure:
                out.append(Problem(failure[0], str(path), failure[2], failure[1]))
                blocked = True
            elif not whole:   # it parses, and the lint could not read its strings: the parse rule has nothing to say about that
                out.append(Problem("read", str(path), 0, UNREAD))
        if blocked:
            unread.add(path)
    # The files of an environment agree with one another only once each of them can be read
    environments = folder / "seed" / "environments"
    if environments.is_dir() and not environments.is_symlink():
        for each in _playgrounds(environments)[0]:
            if any(each in path.parents for path in unread):
                continue
            told = []
            try:
                _environment_findings(each, environment, told)
            except Exception as e:  # noqa: BLE001 - a stranger's file of any shape is a finding, never a crash
                told.append(("", "cannot be checked: " + f"{type(e).__name__}: {_flat(e)}"))
            out.extend(Problem("environment", str(each / relative) if relative else str(each), 0, _short(text))
                       for relative, text in told)
    return Report(sorted(out, key=lambda p: (p.path, p.line, p.check, p.message)), 1, read)


def _is_a_link(folder):
    """The problem of a folder that is given and is a link, else None. The folder the command is given is the tree of a stranger
    like the ones under it: a link there leads anywhere, and a lint that follows it reads what it was not given."""
    if os.path.islink(folder):
        return Problem("playground", str(folder), 0, "is a link. Fix: a folder is not a link: copy it")
    return None


def problems(playground_folder):
    """What is wrong in the seed of one playground, read as the seed reads it: [Problem(check, path, line, message)], by
    path and line. A folder with no compose file, no seed service or no seed folder has nothing to lint."""
    folder = Path(playground_folder)
    trouble = _is_a_link(folder)
    return [trouble] if trouble else _lint(folder).problems


def lint(folder):
    """The Report of a folder that is one playground, or a folder of them, or both: the folder is linted as a playground, and
    so is each folder under it. A compose file beside the playgrounds (a stray, or a decoy) is no reason to leave them unread.
    A folder that does not exist has nothing to lint."""
    folder = Path(folder)
    trouble = _is_a_link(folder)
    if trouble:
        return Report([trouble], 0, 0)
    found, links = _playgrounds(folder)
    own = _lint(folder)
    reports = [own] + [_lint(each) for each in found]
    if not own.playgrounds:   # the links of a folder that is a playground are the playground's own business
        reports.append(Report([Problem("playground", str(each), 0, "is a link. Fix: a playground is a folder, not a link: copy it")
                               for each in links], 0, 0))
    return Report(sorted((p for r in reports for p in r.problems), key=lambda p: (p.path, p.line, p.check, p.message)),
                  sum(r.playgrounds for r in reports), sum(r.files for r in reports))


# --- What the command prints ------------------------------------------------------------

def escape_data(text):
    """A message: `%`, CR and LF would end or change the command."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text):
    """A property (file, title): also `:` and `,`, which separate properties."""
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(problem):
    """The workflow command for a problem: one line, whatever the file is named."""
    properties = [f"file={escape_property(problem.path)}"]
    if problem.line:
        properties.append(f"line={int(problem.line)}")
    properties.append(f"title={escape_property(TITLES[problem.check])}")
    return f"::error {','.join(properties)}::{escape_data(problem.message)}"


def _plain(text):
    """A text on one line that a terminal shows as it is: what cannot be printed is a question mark."""
    return "".join(c if c.isprintable() else "?" for c in str(text))


def _named(path):
    """A path at the start of a line of text. A runner takes a line that starts with `::` or `##[` for a command of its own, and
    a path is a stranger's (a folder named `::error::x`, printed relative to where it runs): one that starts so gets `./`."""
    shown = _plain(path)
    return "./" + shown if shown.lstrip().startswith(COMMANDS) else shown


def render(found, fmt="text"):
    """The problems as text lines (path:line: message), as GitHub annotations, or as JSON, one string."""
    if fmt == "json":
        return json.dumps([p._asdict() for p in found], indent=2, sort_keys=True)
    if fmt == "github":
        return "\n".join(annotation(p) for p in found)
    return "\n".join(f"{_named(p.path)}{':' + str(p.line) if p.line else ''}: {_plain(p.message)}" for p in found)


def playgrounds_root(variables):
    """The folder of playgrounds to lint when none is given: the one `LAB_PLAYGROUNDS` names in `variables` (a mapping, the
    environment of the caller; empty is unset), else the sample of the engine."""
    named = variables.get(NAME)
    return Path(named).expanduser().resolve() if named else SAMPLES


def _in_repository(path):
    """A path as GitHub names the files of a diff: relative to the work tree that holds it (the nearest folder above it with a
    .git, a folder or the file of a linked work tree), else as it is. A workflow that checks a catalog out into a folder of its
    own prints `catalog/playgrounds/...`, and the annotation belongs on `playgrounds/...`."""
    here = Path(os.path.abspath(path))
    for parent in here.parents:
        if os.path.lexists(parent / ".git"):
            return here.relative_to(parent).as_posix()
    return str(path)


def _n(count, noun):
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _shown(folder):
    """A folder as the command prints it: relative to where it runs when it is under it, as the runner of a workflow wants."""
    folder = Path(folder)
    if folder.is_absolute():
        try:
            return folder.relative_to(Path.cwd())
        except ValueError:
            return folder
    return folder


def _parser():
    parser = argparse.ArgumentParser(prog="seedlint.py", description="Lints the seed files of playgrounds, as the seeder reads them.",
                                     epilog=f"With no FOLDER: the folder ${NAME} names, else the sample of the engine. Exit code "
                                            "0: nothing wrong, 1: problems, 2: wrong command line.")
    parser.add_argument("folders", nargs="*", metavar="FOLDER", help="a folder of playgrounds, or one playground")
    parser.add_argument("--format", choices=("text", "github", "json"), default="text")
    return parser


def main(argv=None, variables=None, out=None):
    """The command. `variables` is the environment (os.environ), `out` where it prints (sys.stdout)."""
    args = _parser().parse_intermixed_args(argv)
    variables = os.environ if variables is None else variables
    out = sys.stdout if out is None else out
    folders = [_shown(folder) for folder in args.folders] or [_shown(playgrounds_root(variables))]
    for folder in folders:
        if os.path.lexists(folder) and not os.path.isdir(folder):
            print(f"{folder} is not a folder", file=sys.stderr)
            return 2
    lines, found = [], []
    for folder in folders:
        if not os.path.isdir(folder):
            lines.append(f"No folder {folder}: nothing to lint.")
            continue
        report = lint(folder)
        found.extend(report.problems)
        read = f"{_n(report.files, 'seed file')} of {_n(report.playgrounds, 'playground')} ({folder})"
        if report.problems:
            lines.append(f"{_n(len(report.problems), 'problem')} in {read}")
        elif report.playgrounds:
            lines.append(f"All good: {read}")
        else:
            lines.append(f"Nothing to lint: no playground of {folder} has a seed.")
    if args.format == "json":
        text = render(found, "json")
    else:
        shown = found[:SHOWN]
        if args.format == "github":
            shown = [problem._replace(path=_in_repository(problem.path)) for problem in shown]
        text = render(shown, args.format)
        if len(found) > len(shown):
            lines.insert(0, f"{_n(len(found) - len(shown), 'more problem')} not shown.")
    for line in ([text] if text else []) + ([] if args.format == "json" else lines):
        out.write(line.encode("utf-8", "replace").decode("utf-8") + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
