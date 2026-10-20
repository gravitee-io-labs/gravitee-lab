"""`catalog.py ci release-notes PATH --tag T --records DIR --notes FILE --images FILE`: the notes of a GitHub release, and the
`images.json` that goes with it. The `release` job of `publish.yml` runs it with no token in its environment, after `publish` pushed the
images (`--records` is the folder the artifact `records` was downloaded to, or has no `records.json` when nothing was built).

The notes come from two deterministic sources and nothing else:

- **the units added or removed**: the units of the tree at the previous tag and at this one, compared;
- **for each unit that changed**: up to five commit subjects, clipped to 160 characters, of `git log --format=%s <previous>..<tag> --
  <the unit's folder>`. That is the function the Lab's Update dialog calls for "What's new" (`store.notes`, unit by unit `store.log`:
  merges, `Bump`, `Merge` and `Revert` commits left out, control and format characters removed, the `(#N)` a squash adds dropped,
  clipped to 160), and an update from one release to the next reads the same commits, so the pull request title, which a squash
  makes the subject, is the contract: a plain imperative sentence, and what a person reads on GitHub is what the Update dialog
  shows them.

Layout: `## {name} {version}`, `Lab level {lab}`, then `### New`, `### Changed` (by unit) and `### Removed`, each only when it has
something. The previous tag is the greatest `vX.Y.Z` below this one that leads to it; the first release has none, and every unit is new.

A subject is a stranger's text in Markdown on GitHub, so each is written with its Markdown made plain, and a word that GitHub would
turn into a mention, a link or an issue is put in a code span: a pull request title cannot add a link, an image or a mention to the
notes of a release. Nothing here depends on the clock or on the order of anything: the same tree and the same records give the same
bytes, and `images.json` is `{"images": [{digest, name, platforms, tags}], "schema": 1, "version": "X.Y.Z"}` with the images by name, for a
future registry. Nothing is read from the network.

Exit 0 it wrote them, 2 it cannot (a tag this clone does not have, records that are not what `publish` writes).

`catalog.py ci unreleased PATH` is the same notes from the last release to HEAD: what the Lab does not offer yet, since it offers a
catalog's releases and not its main branch. It says so (a warning in `--format github`, the notes in the summary with `--ci`) and
exits 0: a change merged and not released is a choice of the owners, not a fault. 2 when git cannot read the clone.

Python 3.9 and later, standard library only."""

import json
import os
import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

from ... import paths
from .. import gitx, report, store, tree as trees
from ..rules import catalog_meta
from . import Unavailable, Usage, entry, parsed, parser, plan as planning, write_summary

TAG = re.compile(r"v(\d{1,6})\.(\d{1,6})\.(\d{1,6})")
SUBJECTS = 5
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = re.compile(r"[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9][a-z0-9._-]*)+")
WORD = re.compile(r"[^\s]+")
SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+!|~])")
LINKISH = re.compile(r"@|://|www\.|^#\d|^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#\d", re.I)
SHOWN = 160
NAME_SHOWN = 40
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")


class Release(NamedTuple):
    """What the notes are made from: the tag and its commit, the previous tag and its commit (None for the first), the units new,
    changed (each with its subjects) and removed."""
    tag: str
    sha: str
    previous: Optional[str]
    previous_sha: Optional[str]
    name: str
    version: str
    lab: str
    new: List[str]
    changed: List[Tuple[str, List[str]]]
    removed: List[str]


def version_of(tag: str) -> Tuple[int, int, int]:
    found = TAG.fullmatch(tag)
    if not found:
        raise ValueError(f"not a version tag: {tag!r}")
    return int(found.group(1)), int(found.group(2)), int(found.group(3))


def plain(text: str) -> str:
    """A stranger's words as Markdown that renders as the words and nothing more: the characters Markdown reads are written with a
    backslash, and a word that GitHub would turn into a mention, an address or a reference to an issue goes in a code span."""
    text = " ".join(report.printable(text).split())
    out = []
    for word in WORD.findall(text):
        out.append("`" + word.replace("`", "'") + "`" if LINKISH.search(word) else SPECIAL.sub(r"\\\1", word))
    return " ".join(out)


def tags_before(root: str, sha: str, tag: Optional[str]) -> Optional[str]:
    """The greatest `vX.Y.Z` tag below `tag` (any, when None) that leads to `sha`, or None. Tags that are not three numbers are not
    releases."""
    try:
        done = gitx.run(["tag", "--list", "--merged", sha, "v*"], cwd=os.path.realpath(root), env=trees.QUIET, timeout=60)
    except (gitx.CatalogError, gitx.OutputLimit):
        raise Unavailable("git could not list the tags of this clone") from None
    if done.code != 0:
        raise Unavailable("git could not list the tags that lead to " + sha[:7])
    here = version_of(tag) if tag else (1 << 30,) * 3
    older = []
    for name in done.out.decode("utf-8", "replace").split("\n"):
        name = name.strip()
        if TAG.fullmatch(name) and version_of(name) < here:
            older.append((version_of(name), name))
    return max(older)[1] if older else None


def units_at(git_dir: str, sha: str):
    """The tree at a commit and its units: {Unit: compose file}."""
    tree = trees.GitTree(git_dir, sha)
    return tree, planning.listing(tree.entries()).units


def gather(root: str, tag: str, seams: Optional[dict] = None) -> Release:
    """Everything the notes say, from the repository at `root`."""
    seams = seams or {}
    sha = trees.resolve(root, "refs/tags/" + tag)
    if sha is None:
        raise Unavailable(f"the tag {report.printable(tag)} is not in this clone (does the checkout fetch the tags?)")
    git_dir = trees.git_dir_of(root)
    if git_dir is None:
        raise Unavailable(f"{report.printable(root)} is not a git repository")
    previous = tags_before(root, sha, tag)
    tree, (new, changed, removed) = between(root, git_dir, previous, sha, seams)
    meta = catalog_meta(tree.text("catalog.yml") or "")
    return Release(tag, sha, previous, trees.resolve(root, "refs/tags/" + previous) if previous else None, meta.get("name", ""),
                   meta.get("version", "") or tag[1:], meta.get("lab", ""), new, changed, removed)


def between(root: str, git_dir: str, previous: Optional[str], sha: str, seams: dict):
    """(the tree at `sha`, (new, changed, removed)): the units of `sha` against those of the tag `previous` (every unit is new without
    one), and the notes of each unit that changed."""
    previous_sha = trees.resolve(root, "refs/tags/" + previous) if previous else None
    tree, now = units_at(git_dir, sha)
    before = units_at(git_dir, previous_sha)[1] if previous_sha else {}
    new = sorted(unit.key for unit in set(now) - set(before))
    removed = sorted(unit.key for unit in set(before) - set(now))
    changed: List[Tuple[str, List[str]]] = []
    if previous_sha:
        moved = planning.changed_files(root, previous_sha, sha)
        if moved is None:
            raise Unavailable("git could not say what changed between " + previous + " and " + sha[:7])
        touched = {planning.unit_of(path) for path in moved} - {None}
        keys = sorted(unit.key for unit in touched & set(now) & set(before))
        try:
            changed = [(key, list(subjects)) for key, subjects in (seams.get("notes") or store.notes)(git_dir, previous_sha, sha,
                                                                                                         keys, SUBJECTS)]
        except (gitx.CatalogError, ValueError) as error:
            raise Unavailable("git could not read the commits of the units that changed: "
                              + report.printable(getattr(error, "message", None) or str(error))) from None
    return tree, (new, changed, removed)


def notes_text(found: Release) -> str:
    """The Markdown of the release, in the layout of 5.9."""
    shown_name = plain(found.name[:NAME_SHOWN]) if found.name else "Catalog"
    lines = [f"## {shown_name} {plain(found.version)}", ""]
    if found.lab:
        lines += [f"Lab level {plain(found.lab)}", ""]
    if found.new:
        lines += ["### New", ""] + [f"- {unit}" for unit in found.new] + [""]
    if found.changed:
        lines += ["### Changed", ""]
        for unit, subjects in found.changed:
            lines.append(f"- {unit}")
            lines += [f"  - {plain(subject[:SHOWN])}" for subject in subjects]
        lines.append("")
    if found.removed:
        lines += ["### Removed", ""] + [f"- {unit}" for unit in found.removed] + [""]
    if not (found.new or found.changed or found.removed):
        lines += ["No add-on, setup or playground changed.", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def unreleased_text(previous: Optional[str], new: List[str], changed: List[Tuple[str, List[str]]], removed: List[str],
                    version: str = "") -> str:
    """One line for a person: what changed since the release `previous`, and the command that releases it; empty when nothing did.
    With no release, the version catalog.yml says is the one to tag."""
    keys = new + [key for key, _ in changed] + removed
    if previous is None:
        return ("No release yet: the Lab offers the newest commit of main until there is one. Release it: make release VERSION="
                f"{version or '1.0.0'} TAG=1")
    if not keys:
        return ""
    major, minor, _ = version_of(previous)
    shown = ", ".join(keys[:10]) + (f" and {len(keys) - 10} more" if len(keys) > 10 else "")
    return (f"{len(keys)} {'item' if len(keys) == 1 else 'items'} changed since {previous} and the Lab does not offer "
            f"{'it' if len(keys) == 1 else 'them'} until the next release: {shown}. Release: make release VERSION={major}.{minor + 1}.0")


def read_records(where: str) -> List[dict]:
    """The records `publish` wrote: a JSON list in `where/records.json` (or `where` itself, a file), [] when there is none. Anything
    that is not what `publish` writes is Unavailable: the release must not carry an `images.json` that was made of something else."""
    file = Path(where)
    if file.is_dir():
        file = file / "records.json"
    try:
        data = file.read_bytes() if file.is_file() and file.stat().st_size <= 16 * 1024 * 1024 else None
    except OSError:
        data = None
    if data is None:
        return []
    try:
        records = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Unavailable("the records of the images are not JSON") from None
    if not isinstance(records, list):
        raise Unavailable("the records of the images are not a list")
    clean = []
    for each in records:
        if not (isinstance(each, dict) and isinstance(each.get("name"), str) and IMAGE.fullmatch(each["name"])
                and isinstance(each.get("digest"), str) and DIGEST.fullmatch(each["digest"])
                and isinstance(each.get("tags"), list) and all(isinstance(tag, str) and 0 < len(tag) <= 128 for tag in each["tags"])
                and isinstance(each.get("platforms"), list) and all(isinstance(one, str) and len(one) <= 32 for one in each["platforms"])):
            raise Unavailable("a record of the images is not what publish writes")
        clean.append({"digest": each["digest"], "name": each["name"], "platforms": list(each["platforms"]),
                      "tags": list(each["tags"])})
    return sorted(clean, key=lambda one: one["name"])


def images_text(records: Sequence[dict], version: str) -> str:
    """images.json: the images by name, with their digest, tags and platforms; sorted keys, ASCII, one final line feed."""
    return json.dumps({"images": list(records), "schema": 1, "version": version}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write(file: str, text: str) -> None:
    paths.write_atomic(Path(os.path.abspath(file)), text)


@entry
def main(argv, out, err, **seams) -> int:
    """`ci release-notes`."""
    found = parser("release-notes", "The notes of a release and its images.json.")
    found.add_argument("--tag", required=True, help="the release tag: vX.Y.Z")
    found.add_argument("--records", required=True, help="the folder the records of the images were downloaded to")
    found.add_argument("--notes", required=True, help="the file the notes are written to")
    found.add_argument("--images", required=True, help="the file images.json is written to")
    args = parsed(found, argv)
    if not TAG.fullmatch(args.tag):
        raise Usage(f"--tag is a release tag, vX.Y.Z: {report.printable(args.tag)[:40]} is not one")
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    release = gather(root, args.tag, seams)
    records = read_records(args.records)
    notes = notes_text(release)
    write(args.notes, notes)
    write(args.images, images_text(records, release.version))
    if args.format != "json":
        out.write(f"Notes for {report.printable(args.tag)}: {len(release.new)} new, {len(release.changed)} changed, "
                  f"{len(release.removed)} removed, since {report.printable(release.previous) if release.previous else 'the start'}; "
                  f"{len(records)} images\n")
    else:
        out.write(json.dumps({"changed": [unit for unit, _ in release.changed], "images": len(records), "new": release.new,
                              "previous": release.previous, "removed": release.removed, "tag": release.tag},
                             indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    if args.ci:
        write_summary("### Release notes\n\n" + notes)
    return 0


@entry
def unreleased_main(argv, out, err, **seams) -> int:
    """`ci unreleased`."""
    found = parser("unreleased", "What changed since the last release, which the Lab does not offer yet.")
    args = parsed(found, argv)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    git_dir = trees.git_dir_of(root)
    sha = trees.resolve(root, "HEAD") if git_dir else None
    if sha is None:
        raise Unavailable(f"{report.printable(root)} is not a git repository with a commit")
    previous = tags_before(root, sha, None)
    tree, (new, changed, removed) = between(root, git_dir, previous, sha, seams)
    meta = catalog_meta(tree.text("catalog.yml") or "")
    version = meta.get("version", "") if isinstance(meta.get("version"), str) and VERSION.fullmatch(meta["version"]) else ""
    line = unreleased_text(previous, new, changed, removed, version)
    if args.format == "json":
        out.write(json.dumps({"changed": [key for key, _ in changed], "new": new, "previous": previous, "removed": removed},
                             indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    elif line and args.format == "github":   # a catalog with no release is one way to be: said, not warned of
        out.write(f"::{'notice' if previous is None else 'warning'} title=Unreleased::{line.replace('%', '%25')}\n")
    else:
        out.write((line or f"Nothing changed since {previous}.") + "\n")
    if args.ci and line:
        write_summary("### Not released yet\n\n" + line + "\n\n" + notes_text(Release(
            "HEAD", sha, previous, None, meta.get("name", ""), "(next)", meta.get("lab", ""), new, changed, removed)))
    return 0


__all__ = ["Release", "between", "gather", "images_text", "main", "notes_text", "plain", "read_records", "tags_before",
           "unreleased_main", "unreleased_text", "version_of"]
