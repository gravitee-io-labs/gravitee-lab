"""The default configuration: the catalogs this Lab lists, read from catalogs.default.json at the root of the engine. The file is the only place in the engine's code that holds a catalog address. Which catalogs are listed is
computed at every read from the file and never stored: a hand-edited .lab/catalogs.json can neither make a catalog
listed nor change the address of one that is.

The reader is tolerant and never raises. A file that is missing, not a regular file, over 64 KB, not UTF-8 or JSON, of
another schema or not of the shape reads as no list. An entry whose id, address, branch or roots are not what they must
be reads as no entry, because each of them decides where a download goes or what it is checked against: a damaged one is
never repaired into a different catalog. A damaged first entry reads as no list at all, since the first owns the bare
ids (paths.default_ns reads the same entry and must never be taken for the second). A name or a description that is not
text only loses its text: it is shown until the installed catalog.yml is read, and nothing depends on it.

This module imports no other module of the package at load time: the registry imports it, and git imports the
registry. It asks the registry, when `not_added` is called, which catalogs the person has: what the registry holds, not
a reading of .lab/catalogs.json of its own, so that the page and the rows it offers never disagree."""

import json
import re
import stat
import unicodedata
from typing import Iterable, List, Optional, Tuple

from .. import paths

PATH = paths.DEFAULT_FILE   # relative to paths.ROOT
SCHEMA = 1
LIMIT = 1 << 16   # a few entries; a bigger file is not one
NAME_LIMIT, DESCRIPTION_LIMIT = 40, 90   # the limits of a catalog.yml: what the file shows before one is read
# The entries a listed id can have: its download, the folder that stands in for it, its row
ENTRY_KINDS = ("git", "folder", "suggested")
_ID = re.compile(paths.NS)
_ROOT = re.compile(r"[0-9a-fA-F]{40}")

_seen = (None, [])   # (what the file looked like, the entries read from it)


def _shown(value, limit: int) -> str:
    """A text for a person: NFKC, control characters out, white space collapsed, clipped; empty for anything but
    text."""
    if not isinstance(value, str):
        return ""
    text = "".join(c for c in unicodedata.normalize("NFKC", value)
                   if c.isspace() or not unicodedata.category(c).startswith("C"))
    return " ".join(text.split())[:limit].rstrip()


def _address(value) -> Optional[str]:
    """The https address of a git repository, in the one spelling git is given it, or None: a full URL (never
    owner/repo, an ssh address or a folder) that the address grammar accepts, without a user name or a password."""
    from . import gitx   # here: the registry imports this module, and gitx imports the registry
    if not isinstance(value, str) or "://" not in value:
        return None
    try:
        address = gitx.parse_address(value)
    except Exception:  # noqa: BLE001 - whatever the grammar fails with, the address is refused
        return None
    return address.url if address.kind == "git" and address.url.startswith("https://") else None


def _branch(value):
    """(True, the branch or None) for a branch that is absent or a valid name; (False, None) for anything else."""
    from . import gitx   # as _address
    try:
        return True, gitx.asked_branch(value)
    except Exception:  # noqa: BLE001 - a name git cannot take
        return False, None


def _roots(value):
    """(True, the root commits, lower case, once each) for a list of 40-digit hex ids or no list; (False, []) for
    anything else: an anchor that is damaged is never taken for none."""
    if value is None:
        return True, []
    if not isinstance(value, list) or any(not isinstance(one, str) or not _ROOT.fullmatch(one) for one in value):
        return False, []
    return True, list(dict.fromkeys(one.lower() for one in value))


def _entry(raw) -> Optional[dict]:
    """A listed catalog as the file writes it, checked, or None. Keys: id, name, description, url, and branch and roots
    when the file gives them."""
    if not isinstance(raw, dict):
        return None
    uid, url = raw.get("id"), _address(raw.get("url"))
    if not (isinstance(uid, str) and _ID.fullmatch(uid) and uid != paths.LOCAL) or url is None:
        return None
    fine, branch = _branch(raw.get("branch"))
    anchored, roots = _roots(raw.get("roots"))
    if not (fine and anchored):
        return None
    entry = {"id": uid, "name": _shown(raw.get("name"), NAME_LIMIT) or uid,
             "description": _shown(raw.get("description"), DESCRIPTION_LIMIT), "url": url}
    if branch:
        entry["branch"] = branch
    if roots:
        entry["roots"] = roots
    return entry


def _parsed(raw: bytes) -> List[dict]:
    """The entries of the text of the file, in order; [] for anything that is not a list of the schema this Lab
    reads."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return []
    schema = data.get("schema") if isinstance(data, dict) else None
    catalogs = data.get("catalogs") if type(schema) is int and schema == SCHEMA else None
    if not isinstance(catalogs, list) or not catalogs:
        return []
    first = _entry(catalogs[0])
    if first is None:   # it owns the bare ids: nothing after it takes them over
        return []
    found = [first]
    for raw_entry in catalogs[1:]:
        entry = _entry(raw_entry)
        if entry is not None and all(entry["id"] != one["id"] for one in found):
            found.append(entry)
    return found


def _copied(entries: List[dict]) -> List[dict]:
    """The entries as new objects: what a caller changes is never what the next read finds."""
    return [{key: list(value) if isinstance(value, list) else value for key, value in entry.items()}
            for entry in entries]


def read() -> List[dict]:
    """The catalogs the engine lists, in the order of the file (the order is the precedence, and the first owns the bare
    ids): [{id, name, description, url, branch?, roots?}], each checked. [] when the file is unreadable, of an unknown
    schema or damaged at its first entry. Read again only when the file changes (path, modified time, size, inode),
    because every state poll asks."""
    global _seen
    file = paths.ROOT / PATH
    try:
        info = file.stat()
        if not stat.S_ISREG(info.st_mode):   # a pipe would block the read
            return []
        stamp = (str(file), info.st_mtime_ns, info.st_size, info.st_ino)
        if _seen[0] != stamp:
            with open(file, "rb") as handle:
                raw = handle.read(LIMIT + 1)
            _seen = (stamp, _parsed(raw) if len(raw) <= LIMIT else [])
    except OSError:
        return []
    return _copied(_seen[1])


def _tracked(uid) -> Optional[dict]:
    return next((entry for entry in read() if entry["id"] == uid), None)


def listed(entry) -> bool:
    """Whether a catalog entry is one the engine lists: its id is in the file, and it is the download of that catalog,
    the folder that stands in for it or its row (kind git, folder or suggested). Computed from the file alone: no flag
    of the entry (`listed`, `host`, a stored address) is looked at, so editing .lab/catalogs.json cannot forge it."""
    return isinstance(entry, dict) and entry.get("kind") in ENTRY_KINDS and _tracked(entry.get("id")) is not None


def effective(entry) -> Tuple[Optional[str], Optional[str]]:
    """(url, branch) an entry is read from: for a listed git entry the ones of the file (a change of address in the
    engine reaches every clone), whatever the entry stored; otherwise its own, None where it has none. A listed entry
    that gives no branch asks for the default branch of the remote."""
    if not isinstance(entry, dict):
        return None, None
    tracked = _tracked(entry.get("id")) if entry.get("kind") == "git" else None
    if tracked is not None:
        return tracked["url"], tracked.get("branch")
    url, branch = entry.get("url"), entry.get("branch")
    return (url if isinstance(url, str) else None), (branch if isinstance(branch, str) else None)


def _added() -> List[str]:
    """The ids of the catalogs the person has: the entries the registry reads from .lab/catalogs.json, the same list the
    Catalogs page is drawn from. An entry the registry drops, as damaged, is not one, so that the listed catalog it
    stood for is offered again by a Not added row (the row is the reset). None at all when the registry cannot say:
    a listed catalog is then offered again, which is safe, since Add refuses one already added."""
    from . import registry   # here: the registry imports this module
    try:
        return [one["id"] for one in registry.entries() if isinstance(one, dict) and isinstance(one.get("id"), str)]
    except Exception:  # noqa: BLE001 - the registry is another module's: whatever it fails with, it says nothing
        return []


def not_added(added: Optional[Iterable[str]] = None) -> List[dict]:
    """The listed catalogs that .lab/catalogs.json does not hold (the person removed one, or a later engine lists a new
    one), as the rows of kind `suggested` that the Catalogs page draws with one Add: [{id, kind, listed, name,
    description, url, branch?}], in the order of the file. `added`: the ids already known to the caller, which spares a
    second read of that file."""
    have = {one for one in (_added() if added is None else added) if isinstance(one, str)}
    rows = []
    for entry in read():
        if entry["id"] not in have:
            row = {"id": entry["id"], "kind": "suggested", "listed": True, "name": entry["name"],
                   "description": entry["description"], "url": entry["url"]}
            if "branch" in entry:
                row["branch"] = entry["branch"]
            rows.append(row)
    return rows
