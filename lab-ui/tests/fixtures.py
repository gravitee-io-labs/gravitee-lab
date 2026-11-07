"""The fixture catalogs of the tests, and the helpers that put them in a temporary project folder.

`data/catalogs/<ns>/` holds synthetic catalogs: `fixture` (the default catalog of the tests, so its ids are bare),
`partner` (its Private neighbor), `lab-new` and `lab-old` (a Lab level above and below the one this Lab reads);
`data/local/` is the personal area. Nothing in them is real: no item, address or organization. The helpers copy them,
never link them (unless a test asks for a link), because a container writes a `.env` or a `.state` beside a unit and a
test must not touch the repository.

    project(case, paths, compose)       a project folder: the core, its anchors, the default file, `fixture` installed
    default(case, ns)                   catalogs.default.json
    install(case, ns, kind="git", ...)  a catalog on disk, in catalogs.json and in catalogs.lock (and a bare remote and,
                                        on request, the Lab's bare store; a folder with a store is a shadow)
    remote(case, ns)                    the bare repository of a catalog, and nothing else
    declare(case, ns)                   a catalog the person added and whose download has not ended
    lock(case, ns, audience)            the audience the lock of a catalog says
    local(case, name)                   a unit of data/local, in the personal area
    oldest_compose(case)                Compose answers as 5.0 does, whatever release is installed
    lock_held()                         another change holds the catalogs lock until the block ends

A key a helper does not take is a ValueError: written nowhere, it would leave a test reading a state it did not ask for."""

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, NamedTuple, Optional
from unittest import mock

from lab_ui import paths
from lab_ui.catalogs import store
from lab_ui.catalogs.install import _document
from lab_ui.docker import compose

from . import gitfixture

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CATALOGS = DATA / "catalogs"
LOCAL = DATA / "local"
CORE = HERE.parent.parent / "docker-compose.yml"   # the engine's own core file: the one every project folder copies

KINDS = ("addon", "setup", "playground")
FOLDER = {"addon": "addons", "setup": "setups", "playground": "playgrounds"}
SEEDER_PLACEHOLDER = "__SEEDER_IMAGE__"   # in a seed service of the fixtures; the core's seeder image takes its place
TEXT = (".yml", ".yaml", ".md", ".json")   # the files that may hold the placeholder
AUDIENCE = {"fixture": "public", "partner": "private", "lab-new": "public", "lab-old": "public"}   # what a lock says

# What install() takes besides the catalog; each group says where its keys go
ENTRY_KEYS = {"url", "host"}   # the entry of catalogs.json
CATALOG_KEYS = {"name", "version", "lab"}   # catalog.yml, and the lock's cache of it
LOCK_KEYS = {"audience", "pin", "error"}
CONTENT_KEYS = {"drop", "files"}   # paths left out of the copy; {path: text} added to it or replaced in it
PLACE_KEYS = {"link", "store"}   # a folder catalog as a link; the Lab's bare store


class Remote:
    """A bare git repository that serves a catalog (made by tests.gitfixture): its address, its tip, new commits. A test
    allows the `file` protocol with `gitfixture.isolate`; production never does."""

    def __init__(self, bare):
        self.bare = Path(bare)

    @property
    def url(self):
        return gitfixture.url(self.bare)

    def git(self, *args):
        """The output of a git command run on the bare repository, without its final newline."""
        return gitfixture.git(*args, repo=self.bare).strip()

    def head(self):
        return gitfixture.tip(self.bare)

    def commit(self, files=None, message="Update the catalog", remove=()):
        """A new commit on main: `files` ({path: text}) laid over its tree, the paths of `remove` (a file, or a folder
        with all it holds) taken out of it. Answers its id."""
        changes = dict(files or {})
        listed = self.git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
        for rel in remove:
            changes.update(dict.fromkeys(path for path in listed if path == rel or path.startswith(rel + "/")))
        return gitfixture.commit(self.bare, changes, message)


class Installed(NamedTuple):
    """What install() put on disk: the catalog and how, where, its two entries, and for git its remote and commit."""
    ns: str
    kind: str
    path: Path
    entry: dict
    lock: dict
    remote: Optional[Any]
    commit: Optional[str]


def _meta_of(text):
    """The scalars of the x-gravitee-catalog block of a catalog.yml, {id, name, description, version, license, schema,
    lab} (the last two as integers), without the comment a line ends with. Nothing else of the block is read."""
    found, inside = {}, False
    for line in text.split("\n"):
        if re.fullmatch(r"x-gravitee-catalog:[ \t]*", line):
            inside = True
        elif inside and line[:1] not in (" ", ""):
            break
        elif inside:
            # a value in quotes, or a plain one up to the comment a line may end with
            m = re.fullmatch(r"""  ([a-z_]+):[ \t]*("[^"]*"|'[^']*'|[^\s{\[#"'](?:[^#]*[^\s#])?)(?:[ \t]+#.*)?[ \t]*""", line)
            if m:
                key, value = m.groups()
                value = value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value
                found[key] = int(value) if key in ("schema", "lab") and value.lstrip("-").isdigit() else value
    return found


def catalog_meta(ns):
    """The block of the catalog.yml of a fixture catalog (_meta_of)."""
    return _meta_of((_base(ns) / "catalog.yml").read_text(encoding="utf-8"))


def _base(ns):
    """The folder of a fixture catalog, or of the personal area ("local")."""
    return LOCAL if ns == "local" else CATALOGS / ns


def unit_file(ns, kind, name):
    """The project-relative file of a unit, the line the on file holds: .lab/catalogs/<ns>/..."""
    return f".lab/catalogs/{ns}/" + (f"setups/{name}.yml" if kind == "setup" else f"{FOLDER[kind]}/{name}/compose.yml")


def units(ns):
    """[(kind, name)] of a fixture catalog or of the personal area ("local"), by kind then name: a folder that holds a
    compose.yml, or a setup file."""
    found = []
    for kind in KINDS:
        folder = _base(ns) / FOLDER[kind]
        for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
            if kind == "setup" and entry.is_file() and entry.suffix == ".yml":
                found.append((kind, entry.stem))
            elif kind != "setup" and (entry / "compose.yml").is_file():
                found.append((kind, entry.name))
    return found


def expected():
    """expected.json: {"kind:id": {verdict, reasons, notes, surface, closure, ports, lint}} for fixture and partner."""
    return json.loads((DATA / "expected.json").read_text(encoding="utf-8"))


def _requires(ns):
    """The catalogs a fixture catalog declares under requires.catalogs of its catalog.yml, sorted."""
    found, inside = [], False
    for line in (_base(ns) / "catalog.yml").read_text(encoding="utf-8").split("\n"):
        if re.fullmatch(r"    catalogs:[ \t]*", line):
            inside = True
        elif inside:
            declared = re.match(r"      ([a-z][a-z0-9-]*):", line)
            if declared:
                found.append(declared.group(1))
            elif line[:6] != "      ":
                break
    return sorted(found)


def _skip(reason):
    """A skip that shows: a line on standard error, whether or not the run is verbose, then the skip itself."""
    print(f"SKIP {reason}", file=sys.stderr, flush=True)
    raise unittest.SkipTest(reason)


def have_git():
    """Whether git runs. On macOS /usr/bin/git is a shim that opens an installer when the Command Line Tools are
    missing: it is run only after `xcode-select -p` says they are there."""
    git = shutil.which("git")
    if not git:
        return False
    try:
        if platform.system() == "Darwin" and os.path.realpath(git) == "/usr/bin/git":
            tools = subprocess.run(["xcode-select", "-p"], capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
            if tools.returncode:
                return False
        done = subprocess.run([git, "--version"], capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_compose = []   # the answer of have_compose(), once


def have_compose():
    """Whether `docker compose` is there and is version 5 or later. Rendering files needs no daemon."""
    if not _compose:
        found = False
        if shutil.which("docker"):
            try:
                done = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=20)
                number = re.search(r"(\d+)\.\d+", done.stdout)
                found = done.returncode == 0 and bool(number) and int(number.group(1)) >= 5
            except (OSError, subprocess.TimeoutExpired):
                found = False
        _compose.append(found)
    return _compose[0]


def _as_oldest(value):
    if isinstance(value, list):
        return [_as_oldest(one) for one in value] if value else None
    if isinstance(value, dict):
        return {key: _as_oldest(one) for key, one in value.items()}
    return value


def oldest_compose(case):
    """Compose answers `config --format json` the way 5.0 does, the oldest the Lab accepts and the one CI installs, whatever
    release this machine has: an empty list under an x- key is null there (5.6 prints [])."""
    real = compose.run

    def run(cmd, *args, **how):
        rc, out, err = real(cmd, *args, **how)
        if rc == 0 and "config" in cmd and "json" in cmd:
            config = json.loads(out)
            out = json.dumps({key: _as_oldest(value) if key.startswith("x-") else value for key, value in config.items()})
        return rc, out, err
    case.patch(compose, "run", run)


def need_git():
    if not have_git():
        _skip("git is not installed, or is the macOS shim without the Command Line Tools")


def need_compose():
    if not have_compose():
        _skip("the Docker CLI with Compose 5 or later is not installed")


@contextlib.contextmanager
def lock_held():
    """Another thread holds the catalogs lock until the block ends, as another change or another Lab would, and a change does not
    wait long for it. The modules are read here because a helper of the fixtures is no reason for every test to load them."""
    from lab_ui.catalogs import registry, service
    taken, free = threading.Event(), threading.Event()

    def hold():
        with registry.flock():
            taken.set()
            free.wait(30)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    taken.wait(10)
    try:
        with mock.patch.object(service, "PATIENCE", 0.05), mock.patch.object(registry, "FLOCK_WAIT", 0.05):
            yield
    finally:
        free.set()
        thread.join(10)


def _scratch(case, name):
    """A folder of the case's own, outside its project folder and removed with it: the remotes of the git catalogs and
    the folders that linked catalogs point to. The same folder for the same name."""
    held = case.__dict__.setdefault("_fixture_scratch", {})
    if name not in held:
        holder = tempfile.TemporaryDirectory()
        case.addCleanup(holder.cleanup)
        held[name] = Path(holder.name).resolve()
    return held[name]


def _write(file, text, mode=0o644):
    file.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(file, text, mode=mode)


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def core(case):
    """The engine's files a project folder holds: the real core (this repository's docker-compose.yml), the anchors a
    unit may reach (seeder/Dockerfile, plugins/apim, plugins/am) and .lab/catalogs/."""
    _write(case.root / paths.CORE_FILE, CORE.read_text(encoding="utf-8"))
    _write(case.root / "seeder" / "Dockerfile", "FROM scratch\n")
    for product in ("apim", "am"):
        (case.root / "plugins" / product).mkdir(parents=True, exist_ok=True)
    (case.root / paths.LAB_DIR / "catalogs").mkdir(parents=True, exist_ok=True)


def seeder_image(case):
    """The image a seed service has in the project folder: x-gravitee.seeder.image of its core file, or of the engine's in a
    folder that holds none."""
    file = case.root / paths.CORE_FILE
    text = (file if file.is_file() else CORE).read_text(encoding="utf-8")
    return re.search(r"(?m)^  seeder:\n    image: (\S+)", text).group(1)


def project(case, *modules, ns="fixture", kind="folder", **patch):
    """A project folder for the case (case.folder(*modules): `modules` are the ones whose ROOT it replaces) with the
    engine's files (core), `ns` installed (install) and the default file naming it (default). For a catalog that comes
    from a bare repository (`kind="git"`) the default file lists the address of that repository and its branch, because
    the address of a listed catalog is the tracked one: the entry and the file agree. Answers the folder."""
    case.folder(*modules)
    core(case)
    done = install(case, ns, kind, **patch)
    served = (done.entry.get("shadows") if kind == "folder" else done.entry) if done.remote else None
    default(case, ns, **{key: served[key] for key in ("url", "branch") if served and served.get(key)})
    return case.root


def default(case, ns="fixture", **entry):
    """catalogs.default.json at the root of the project folder, listing `ns` as the default catalog: name and description
    come from its catalog.yml, the address is a synthetic https one that never answers (a test that serves the catalog
    gives `url=`). `entry` sets keys in the entry."""
    meta = catalog_meta(ns)
    _write(case.root / "catalogs.default.json", _json({"schema": 1, "catalogs": [
        {"id": ns, "name": meta.get("name", ns), "description": meta.get("description", ""),
         "url": f"https://example.invalid/{ns}.git", "branch": "main", **entry}]}))


def read_config(case):
    """.lab/catalogs.json as a dict, None while it does not exist."""
    file = case.root / paths.LAB_DIR / "catalogs.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None


def read_lock(case):
    """.lab/catalogs.lock as a dict, None while it does not exist."""
    file = case.root / paths.LAB_DIR / "catalogs.lock"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None


def _put_entry(case, entry):
    """The entry in catalogs.json: where an entry of the same id stands, else last. It is dated by its place in the array,
    so that array order is also the order of the dates. Answers the entry."""
    config = read_config(case) or {"schema": 1, "catalogs": []}
    entries = config["catalogs"]
    place = next((i for i, one in enumerate(entries) if one["id"] == entry["id"]), len(entries))
    entry = {**entry, "added": f"2026-10-16T09:{place:02d}:00Z"}
    entries[place:place + 1] = [entry]
    _write(case.root / paths.LAB_DIR / "catalogs.json", _json(config), mode=0o600)
    return entry


def _put_lock(case, ns, fields):
    """The entry in catalogs.lock. Answers the entry."""
    lock_ = read_lock(case) or {"schema": 1, "catalogs": {}}
    lock_["catalogs"][ns] = fields
    _write(case.root / paths.LAB_DIR / "catalogs.lock", _json(lock_), mode=0o600)
    return fields


def _check_patch(patch, allowed):
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise ValueError(f"no such key: {', '.join(unknown)}; the keys are {', '.join(sorted(allowed))}")


def _blank_lock(ns):
    """A lock entry before anything was downloaded."""
    return {"pin": None, "previous": None, "tip": None, "roots": [], "audience": AUDIENCE.get(ns, "private"),
            "name": None, "version": None, "lab": None, "installed": None, "checked": None, "error": None,
            "pending": None}


def _tree(case, ns):
    """{path: bytes} of the files of a fixture catalog, the seeder placeholder replaced by the image of the core."""
    base, image = _base(ns), seeder_image(case).encode()
    return {file.relative_to(base).as_posix(): file.read_bytes().replace(SEEDER_PLACEHOLDER.encode(), image)
            for file in sorted(base.rglob("*")) if file.is_file()}


def _patched(tree, patch):
    """The tree with `drop` and `files` applied, then the name, version and lab of the patch written in catalog.yml."""
    tree = dict(tree)
    for rel in patch.get("drop", ()):
        for path in [path for path in tree if path == rel or path.startswith(rel + "/")]:
            del tree[path]
    for rel, text in (patch.get("files") or {}).items():
        tree[rel] = text if isinstance(text, bytes) else text.encode("utf-8")
    fields = {key: patch[key] for key in ("name", "version", "lab") if patch.get(key) is not None}
    if fields:
        text = tree["catalog.yml"].decode("utf-8")
        for key, value in fields.items():
            plain = isinstance(value, int) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]*", str(value))
            shown = str(value) if plain else json.dumps(value)
            text = re.sub(rf"(?m)^(  {key}:[ \t]*)[^\n]*$", lambda m: f"{m.group(1)}{shown}", text, count=1)
        tree["catalog.yml"] = text.encode("utf-8")
    return tree


def _blob(data):
    """The id git gives a file of this content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _put_files(target, tree, readonly):
    """These files written under `target`, read-only or not."""
    target.mkdir(parents=True, exist_ok=True)
    for rel, data in tree.items():
        file = target / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        file.chmod(0o444 if readonly else 0o644)


def _remote(case, ns, tree):
    """A bare repository with `tree` committed on main, the address a git catalog is added from. gitfixture fixes the
    author and the dates, so the commit id is the same on every run."""
    bare = _scratch(case, "remotes") / f"{ns}.git"
    shutil.rmtree(bare, ignore_errors=True)
    return Remote(gitfixture.make_repo(bare, tree, message=f"Add the {ns} catalog"))


def remote(case, ns, **patch):
    """A bare repository that serves fixture catalog `ns` (its address is `.url`), with nothing installed and no entry
    written: what the Add of a catalog and the first download start from. `patch`: name, version, lab, `drop` and `files`,
    as install() takes them. Answers a Remote."""
    _check_patch(patch, CATALOG_KEYS | CONTENT_KEYS)
    need_git()
    return _remote(case, ns, _patched(_tree(case, ns), patch))


def _make_store(root, ns, remote_):
    """The Lab's own bare store of a catalog, .lab/cache/git/<ns>.git, with `refs/lab/tip` fetched from the remote. It has
    no origin and no branch of its own: the Lab names the address on each fetch."""
    bare = root / paths.LAB_DIR / "cache" / "git" / f"{ns}.git"
    shutil.rmtree(bare, ignore_errors=True)
    store.init(bare)
    gitfixture.git("fetch", "--quiet", "--no-tags", "--", str(remote_.bare), "+refs/heads/main:refs/lab/tip", repo=bare)


def install(case, ns, kind="folder", **patch):
    """Catalog `ns` of the fixtures installed in the case's project folder (project()) the way the Lab leaves one, with
    its entry in .lab/catalogs.json and its entry in .lab/catalogs.lock. Answers an Installed.

    kind="folder": the files are copied to .lab/catalogs/<ns>/, writable, and the entry is a folder entry. `link=True`
    copies them to a folder of the case's own instead and makes .lab/catalogs/<ns> a link to it, as a folder catalog is.
    `store=True` makes the folder the shadow of a git catalog: the catalog is also served by a bare repository,
    the Lab's bare store holds its commit, the lock names it and says `shadowed`, and the entry says what it
    `shadows` (the address of that repository, its branch).

    kind="git" (needs git: a visible skip without): the files are committed to a bare repository, which is the entry's
    address (a file:// one: production accepts none), and installed the way the Lab installs them: what the store's
    filter keeps, read-only, with .installed.json and a lock whose pin is that commit. `store=True` also makes the Lab's
    own bare store, .lab/cache/git/<ns>.git, with refs/lab/tip (_make_store).

    `patch`: the keys of the entry (url, host); name, version and lab, which are written in catalog.yml, so that the
    repository and the manifest agree, and cached in the lock; the keys of the lock (audience: that of AUDIENCE, private
    for a catalog it does not know; pin, error); `drop` (paths left out of the copy); `files` ({path: text} added or
    replaced, which is how a hostile file gets in). Installing again replaces everything of the first time and keeps the
    entry's place."""
    _check_patch(patch, ENTRY_KEYS | CATALOG_KEYS | LOCK_KEYS | CONTENT_KEYS | PLACE_KEYS)
    tree = _patched(_tree(case, ns), patch)
    meta = _meta_of(tree["catalog.yml"].decode("utf-8")) if "catalog.yml" in tree else {}
    entry, remote_, commit = {"id": ns, "kind": kind}, None, None
    held = {**_blank_lock(ns), "name": meta.get("name"), "version": meta.get("version"), "lab": meta.get("lab")}
    shadow = kind == "folder" and bool(patch.get("store"))
    if kind == "git" or shadow:
        need_git()
        remote_ = _remote(case, ns, tree)   # before anything is replaced: a failure here leaves the folder as it was
        commit = remote_.head()
        held.update({"pin": commit, "tip": commit, "installed": "2026-10-16T09:00:30Z", "checked": "2026-10-16T09:01:00Z",
                     "roots": remote_.git("rev-list", "--max-parents=0", commit).split()})
    target = case.root / paths.LAB_DIR / "catalogs" / ns
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    shutil.rmtree(target, ignore_errors=True)
    if kind == "folder":
        place = target
        if patch.get("link"):
            place = _scratch(case, "authors") / ns
            shutil.rmtree(place, ignore_errors=True)
            target.symlink_to(place, target_is_directory=True)
        _put_files(place, tree, False)
        entry["path"] = str(place)
        if shadow:
            entry["shadows"] = {"url": remote_.url, "branch": "main", "added": "2026-10-16T09:00:00Z"}
            held["shadowed"] = True
    else:
        kept, ignored = store.selected(store.Entry("100644", _blob(data), len(data), rel) for rel, data in sorted(tree.items()))
        files = {entry_.path: tree[entry_.path] for entry_ in kept}
        _put_files(target, files, True)
        shas = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
        _write(target / ".installed.json", _json(_document(ns, commit, kept, ignored, shas)))
        entry.update({"url": remote_.url, "branch": "main", "auto": True})
    if patch.get("store"):
        _make_store(case.root, ns, remote_)
    entry.update({key: patch[key] for key in ENTRY_KEYS & set(patch)})
    held.update({key: patch[key] for key in LOCK_KEYS & set(patch)})
    held = _put_lock(case, ns, held)   # the entry is written last: it is what makes the catalog one the person has
    return Installed(ns, kind, target, _put_entry(case, entry), held, remote_, commit)


def declare(case, ns, **patch):
    """A catalog in .lab/catalogs.json and .lab/catalogs.lock that has never been downloaded: no files, `pin: null`, and
    the `error` (a code of the catalogue of errors) the last attempt ended with, if any. It is the row "Downloading" or "Try again".
    `patch`: url, host and the keys of the lock. Answers its entry."""
    _check_patch(patch, ENTRY_KEYS | LOCK_KEYS)
    entry = {"id": ns, "kind": "git", "url": f"https://example.invalid/{ns}.git", "branch": "main", "auto": True}
    entry.update({key: patch[key] for key in ENTRY_KEYS & set(patch)})
    _put_lock(case, ns, {**_blank_lock(ns), **{key: patch[key] for key in LOCK_KEYS & set(patch)}})
    return _put_entry(case, entry)


def lock(case, ns, audience):
    """The audience the lock entry of a catalog says."""
    _put_lock(case, ns, {**read_lock(case)["catalogs"][ns], "audience": audience})


def local(case, name):
    """A unit of data/local copied to the personal area of the project folder, .lab/catalogs/local/: its folder with
    everything in it. Writable, with no manifest and no entry anywhere: the Lab never installs it and never removes it.
    Answers the path of the copy."""
    area, image = case.root / paths.LAB_DIR / "catalogs" / "local", seeder_image(case).encode()
    base = next(LOCAL / FOLDER[kind] / unit for kind, unit in units("local") if unit == name)
    for file in sorted(f for f in base.rglob("*") if f.is_file()):
        target = area / file.relative_to(LOCAL)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.read_bytes().replace(SEEDER_PLACEHOLDER.encode(), image))
    return area / base.relative_to(LOCAL)
