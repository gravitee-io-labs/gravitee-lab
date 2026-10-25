"""Docker Compose from the command line: a file's configuration as JSON, its includes and variables, the project's
containers, what Compose would create each service as, the images Docker holds. Every command runs under the same
limits: a time after which it and what it started are ended, and an environment that lets Compose read local files
only. Also holds the environments of a command that reads or builds what someone else wrote (the catalog standard), and the cache of what is slow to ask again (registries, Docker)."""

import atexit
import calendar
import concurrent.futures.thread   # noqa: F401  (see _end: its exit hook has to be registered before ours)
import contextlib
import copy
import functools
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..paths import COMPOSE_FILE, CORE_FILE, ON_FILE, ROOT, write_atomic

COMPOSE = ["docker", "compose", "--progress", "plain"]

# How long after its last start a container that Docker restarted still counts as restarting, in seconds
RESTART_WINDOW = 300

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
COMMENT = re.compile(r"(?:^|\s)#(.*)$")
INCLUDE_ITEM = re.compile(r"""\s*-\s*(?:path:\s*)?(?:"([^"]+)"|'([^']+)'|([^\s#'"]+))\s*(?:#.*)?""")
# A key with nothing after it, with the dash of a list item before it or not: `- path:`, `env_file:`
KEY = re.compile(r"\s*(-\s*)?([A-Za-z_][\w-]*):\s*(?:#.*)?")
# A line of the long form of an include item that names its project directory
DIRECTORY = re.compile(r"\s*project_directory:")
# What the Lab writes in the on file for an item that runs on another platform: a copy of its file, written for that
# platform (retarget), included with the folder of the original as its project directory, so that its relative paths hold
TARGET_ITEM = re.compile(r"""\s*-\s*\{\s*path:\s*"(\.lab/targets/([a-z0-9-]+)/[^"]+)",\s*project_directory:\s*"([^"]+)"\s*\}\s*(?:#.*)?""")
TARGETS = ".lab/targets"
# What inspect tells of a container that ps does not, "|" between the fields: its name, whether it was killed for
# running out of memory, its restarts and its last start, the hash of the configuration Compose created it from, and
# the image it runs
INSPECT = ('{{.Name}}|{{.State.OOMKilled}}|{{.RestartCount}}|{{.State.StartedAt}}'
           '|{{index .Config.Labels "com.docker.compose.config-hash"}}|{{.Image}}')
# Then, when asked, one variable of its environment: only that entry is printed, the others stay in Docker
VARIABLE = '|{{range .Config.Env}}{{if eq (index (split . "=") 0) "%s"}}{{.}}{{end}}{{end}}'


class ComposeError(RuntimeError):
    """Docker Compose refused a command: the message is the last line it printed."""


class Cache:
    """Answers kept for a while, by key. Keys of what a job may change start with `local:`: they are dropped when a
    job ends."""

    def __init__(self):
        self._items, self._lock = {}, threading.Lock()

    def get(self, key, ttl, fn):
        with self._lock:
            hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = fn()
        if value is not None:   # a failure is asked again next time
            with self._lock:
                self._items[key] = (time.monotonic(), value)
        return value

    def drop(self, prefix=""):
        with self._lock:
            for key in [k for k in self._items if k.startswith(prefix)]:
                del self._items[key]


CACHE = Cache()


@functools.lru_cache(maxsize=None)
def _scratch_folder():
    folder = Path(tempfile.mkdtemp(prefix="gravitee-lab-"))
    atexit.register(shutil.rmtree, folder, True)
    return folder


# lru_cache does not keep two threads that ask at the same time from both running the function: each would make a folder
_scratch_lock = threading.Lock()


def scratch():
    """A folder of this run of the Lab UI, outside the project, made when first needed and removed on exit: the
    generated include files, and a folder without compose files to ask Docker by project name. Private, so that
    no other user of the machine writes what Compose reads there. Made again when the system clears its temp
    folder while the Lab UI runs. One folder for the threads that ask at the same time, the first time as well."""
    with _scratch_lock:
        if not _scratch_folder().is_dir():
            _scratch_folder.cache_clear()
        return _scratch_folder()


# Compose fetches a remote include (a Git repository, an OCI artifact) by itself when a file it reads names one, with
# the person's own credentials. The Lab reads files it did not write, so it never lets Compose do that. A remote build
# context is not an include and is not affected
NO_REMOTE = {"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "false", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "false"}


def _waiting():
    """Whether the layout of an existing machine is being moved to the catalogs (layout.waiting()). The module is
    looked up when asked, as it needs this one; a checkout without it is not waiting."""
    name = "lab_ui.library.layout"
    try:
        layout = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:   # a module it needs is missing: that is a fault to show, not a layout to ignore
            raise
        return False
    return bool(layout.waiting())


def environment():
    """The shell's environment, for a command, with the files Compose reads: the core and what is on, whatever the
    shell or .env says, and no remote include (NO_REMOTE). While the layout is waiting (_waiting), the core alone:
    the on file may name files that are not where they were, and the Lab's own commands must not fail on it. It is the
    only time Compose reads fewer files than .env says."""
    return {**os.environ, "COMPOSE_FILE": CORE_FILE if _waiting() else COMPOSE_FILE, **NO_REMOTE}


# What a command that reads or builds the files of a stranger (a catalog's pull request) is told of the shell: these
# variables, and no other, so that `${ACTIONS_RUNTIME_TOKEN}`, a token or the agent's socket in a file it reads is
# empty. No COMPOSE_* of the shell is carried (a COMPOSE_FILE, COMPOSE_PROFILES or COMPOSE_ENV_FILES would change
# which files Compose reads: the callers name them with -f), except the two flags that keep it from fetching an
# include (NO_REMOTE), always off
ALLOWED = ("PATH", "LANG", "LC_ALL", "TMPDIR", "DOCKER_HOST")
# The Compose plugin is a file of this name in a folder where the Docker CLI looks for plugins
PLUGIN = "docker-compose"
# The Docker CLI looks for a plugin in the folders its configuration lists (`cliPluginsExtraDirs` of the `config.json`
# in its configuration folder), then in `cli-plugins` of that folder (`$DOCKER_CONFIG`, else `~/.docker`), then in
# these, in this order (docker/cli, cli-plugins/manager)
SYSTEM_PLUGINS = ("/usr/local/lib/docker/cli-plugins", "/usr/local/libexec/docker/cli-plugins",
                  "/usr/lib/docker/cli-plugins", "/usr/libexec/docker/cli-plugins")
# Docker Desktop's own, which its CLI reaches through the links it makes in the person's folder (`~` is theirs). The CLI
# does not look here: they are the Lab's last resort, so that a Desktop whose links are missing is found, and wider
# than the CLI's own list
DESKTOP_PLUGINS = ("/Applications/Docker.app/Contents/Resources/cli-plugins",
                   "~/Applications/Docker.app/Contents/Resources/cli-plugins")
# The longest configuration of Docker that is read, for the list of folders it holds and nothing else
CONFIG_LIMIT = 1024 * 1024


def _absolute(folder):
    """A folder of the real environment as an absolute path, else None: a relative name would be looked for in the
    folder the command runs in, which is the catalog's."""
    return folder if isinstance(folder, str) and os.path.isabs(folder) else None


def _extra_plugins(config):
    """The folders that the Docker configuration in this folder lists for plugins (`cliPluginsExtraDirs`), the
    absolute ones only. Nothing else of the file is read: its credentials stay where they are."""
    try:
        with open(os.path.join(config, "config.json"), "rb") as handle:
            found = json.loads(handle.read(CONFIG_LIMIT))
    except (OSError, ValueError, RecursionError):
        return []
    listed = found.get("cliPluginsExtraDirs") if isinstance(found, dict) else None
    return [folder for folder in listed if _absolute(folder)] if isinstance(listed, list) else []


def _plugin_folders():
    """The folders where `docker compose` would look for its plugin in the real environment, in the order of the
    Docker CLI, the absolute ones only: the folders the configuration lists (`cliPluginsExtraDirs`), `cli-plugins` of
    the configuration folder, the system's (SYSTEM_PLUGINS); then, the Lab's own last resort, Docker Desktop's
    (DESKTOP_PLUGINS). The configuration folder is `$DOCKER_CONFIG` when the shell sets it, and then `~/.docker` is not
    looked in at all, as the CLI does not; else `~/.docker`. A `$DOCKER_CONFIG` that is not absolute is the folder the
    command runs in for the CLI, which is the catalog's: no configuration folder is searched then."""
    named = os.environ.get("DOCKER_CONFIG") or None
    home = _absolute(os.path.expanduser("~"))
    if named:
        config = _absolute(named)
    else:
        config = os.path.join(home, ".docker") if home else None
    folders = _extra_plugins(config) if config else []
    folders += [os.path.join(config, "cli-plugins")] if config else []
    folders += SYSTEM_PLUGINS
    folders += [os.path.expanduser(folder) for folder in DESKTOP_PLUGINS]
    return [folder for folder in folders if _absolute(folder)]


def plugin():
    """The Compose plugin that `docker compose` runs in the real environment: the first file named `docker-compose` that
    can be run, in the folders the Docker CLI looks in and in its order (_plugin_folders; Docker Desktop's last). None
    when this machine has none, which is what "Docker Compose not found" says. It is the file that the scrubbed
    environment links to."""
    for folder in _plugin_folders():
        found = os.path.join(folder, PLUGIN)
        if os.path.isfile(found) and os.access(found, os.X_OK):
            return found
    return None


_places_lock = threading.Lock()


def _scrubbed_places(found):
    """(HOME, DOCKER_CONFIG) of the scrubbed environment: two folders inside the scratch folder of this run, one for
    the run and not one for each command, made again when they are not there. HOME is empty. DOCKER_CONFIG holds an
    empty `config.json` (made empty again if a command wrote in it), and `cli-plugins/docker-compose`, a link to the
    plugin `found` (plugin()), none when there is none: Compose is a plugin of the Docker CLI, found under the
    configuration folder, so a configuration folder without the link hides it."""
    base = scratch() / "scrubbed"
    home, config = base / "home", base / "docker"
    with _places_lock:
        os.makedirs(base, mode=0o700, exist_ok=True)
        for folder in (home, config, config / "cli-plugins"):
            os.makedirs(folder, mode=0o700, exist_ok=True)
        settings = config / "config.json"
        try:
            empty = settings.read_text(encoding="utf-8") == "{}\n" and not settings.is_symlink()
        except (OSError, ValueError):
            empty = False
        if not empty:
            if os.path.lexists(settings):
                os.unlink(settings)
            write_atomic(settings, "{}\n", mode=0o600)
        link = config / "cli-plugins" / PLUGIN
        try:
            linked = os.readlink(link)
        except OSError:
            linked = None
        if linked != found and found != str(link):   # (a plugin that is the link already: the shell is a scrubbed one)
            if os.path.lexists(link):
                os.unlink(link)
            if found:
                os.symlink(found, link)
    return home, config


def _allowed():
    """The variables of the real environment that ALLOWED names, those it has."""
    return {name: os.environ[name] for name in ALLOWED if name in os.environ}


def scrubbed_environment():
    """The environment of `docker compose config` on files that someone else wrote (a catalog's pull request, a unit
    from a catalog that was not read yet): ALLOWED, the two flags that keep Compose from fetching an include
    (NO_REMOTE), a HOME that is an empty folder of this run, and a DOCKER_CONFIG of its own whose empty
    `config.json` holds none of the person's credentials and whose `cli-plugins` links to the Compose plugin of the
    real environment (_scrubbed_places; `plugin()` says which, and None when there is none: then `docker compose` is
    not found, and that is what to report). So `${ACTIONS_RUNTIME_TOKEN}` or any token in a file reads empty,
    `${HOME}` reads a temporary path and no agent socket is there. Only for `config`: a build, an `up`, a `run` and
    the smoke get build_environment(). Pass it as `environ` to run(), config_json() and the like; the files are
    named with -f, as COMPOSE_FILE is not carried."""
    home, config = _scrubbed_places(plugin())
    return {**_allowed(), "HOME": str(home), "DOCKER_CONFIG": str(config), **NO_REMOTE}


def build_environment():
    """The environment of a build, an `up`, a `run` and the smoke on files that someone else wrote: the same
    variables as scrubbed_environment() (ALLOWED, NO_REMOTE) with the real HOME and DOCKER_CONFIG, because a
    developer's build needs the person's credentials for private base images, and the untrusted lane of a catalog's
    CI has none to leak. No ACTIONS_*, no token, no agent socket, no variable of the shell that is not on the list.
    A HOME or a DOCKER_CONFIG that is not an absolute path is dropped, not passed: a command that runs in the catalog's
    folder would read its configuration, and the plugins and credential helpers it names, from the catalog (the Docker CLI
    takes the folder it runs in for a relative DOCKER_CONFIG, and a tool a relative HOME). The CLI then falls back to the
    account's own home, as it does with no HOME at all. The same for the folders of PATH: an empty one and a relative one
    (`.`, `bin`) are the folder the command runs in, where a program the catalog calls `docker` would be found before the
    person's. Only the absolute ones stay, in their order; a PATH with none is not passed, and the command looks where a
    command looks with no PATH, in the system's folders."""
    kept = {name: os.environ[name] for name in ("HOME", "DOCKER_CONFIG") if _absolute(os.environ.get(name))}
    environment = {**_allowed(), **kept, **NO_REMOTE}
    if "PATH" in environment:
        folders = [folder for folder in environment["PATH"].split(os.pathsep) if os.path.isabs(folder)]
        if folders:
            environment["PATH"] = os.pathsep.join(folders)
        else:
            del environment["PATH"]
    return environment


# Commands being run, to end with their process group if the Lab UI exits before they do
_running = set()
# Whether the Lab UI is exiting (_end): no command is started from then on
_ending = False


def _stop(process):
    """Ends a command and everything it started. `docker compose` is a CLI that starts the Compose plugin: killing
    the CLI alone leaves the plugin running, and a file that it reads without end (`env_file: /dev/zero`) makes it
    fill the machine's memory. Each command leads a process group of its own (run), and the group is killed."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:   # the group is gone already
        process.kill()


def stop_running():
    """Ends every command that runs, with what it started. A command leads a session of its own (run), so the Ctrl+C
    or the hang-up that ends the Lab UI no longer reaches it: the Lab UI ends it."""
    for process in list(_running):
        _stop(process)


def _end():
    """What the Lab UI does as it exits: ends the commands that run (stop_running) and starts none, also those that a
    pool of threads still holds in its queue and runs to the end. Python waits for the threads of a pool before it
    calls the functions of `atexit`, and those threads run the commands: this is also registered with the exit hook of
    the threads, which runs before that wait, after the pool's own (hooks run last registered first)."""
    global _ending
    _ending = True
    stop_running()
    git = sys.modules.get("lab_ui.catalogs.gitx")   # only if git was ever run
    if git is not None:
        git.stop_running()


atexit.register(_end)
if hasattr(threading, "_register_atexit"):   # CPython 3.9 and later
    threading._register_atexit(_end)


def stop_on_signals():
    """Makes a hang-up (the terminal closed) and a termination of the Lab UI end what runs (_end) before the Lab UI ends
    as it would have. A command leads a session of its own (run), so these signals, which the terminal sends to the
    Lab UI's process group, no longer reach it, and Python does not run its exit functions for them. A signal that the
    Lab UI ignores (`nohup`) or handles stays as it is. Called once by what starts the Lab UI, from the main thread."""
    def ending(number, frame):
        _end()
        signal.signal(number, signal.SIG_DFL)
        signal.raise_signal(number)
    for name in ("SIGHUP", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is not None and signal.getsignal(number) == signal.SIG_DFL:
            signal.signal(number, ending)


def run(cmd, timeout=120, cwd=None, env=None, environ=None, given=None):
    """Runs a command from the project root: (exit code, output, errors). It gets no input but `given`, the text it reads
    whole: a question it asks would wait on the terminal the Lab UI was started from, and it has no terminal to ask on. Past `timeout` seconds
    it is ended with everything it started (_stop): (124, "", what happened). While the Lab UI exits it is not run:
    (130, "", what happened). `env`: variables set for it, which Compose reads before what the .env files hold.
    `environ`: the whole environment it gets in place of environment(): scrubbed_environment() for a command that reads
    files someone else wrote, build_environment() for one that builds them."""
    if _ending:
        return 130, "", f"{cmd[0]} was not run: the Lab UI is ending"
    try:
        process = subprocess.Popen(cmd, cwd=cwd or ROOT,
                                   env={**(environment() if environ is None else environ), "PWD": str(cwd or ROOT), **(env or {})},
                                   stdin=subprocess.DEVNULL if given is None else subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as error:
        return 127, "", str(error)
    _running.add(process)
    if _ending:   # the Lab UI began to exit while it started: _end did not see it
        _stop(process)
    try:
        with process:
            try:
                out, err = process.communicate(given, timeout=timeout)
            except subprocess.TimeoutExpired:
                _stop(process)
                return 124, "", f"{cmd[0]} did not answer within {timeout} s"
            except BaseException:   # also an interrupt: nothing is left running
                _stop(process)
                raise
    finally:
        _running.discard(process)
    return process.returncode, out, err


def _last_line(err):
    lines = [line.strip() for line in err.splitlines() if line.strip()]
    return lines[-1] if lines else "docker compose failed"


def _config(file_args, *options, timeout=120, environ=None):
    more = {} if environ is None else {"environ": environ}
    rc, out, err = run([*COMPOSE, *file_args, "config", "--format", "json", *options], timeout=timeout, **more)
    if rc != 0:
        if environ is not None and missing(err):
            # A Docker without the plugin answers the Lab's own command line with a usage text whose last line says
            # nothing of Compose (`Run 'docker --help' ...`): said as what it is, so that missing() knows it again
            raise ComposeError(NO_COMPOSE)
        raise ComposeError(_last_line(err))
    return json.loads(out)


def _mapping(value):
    """labels and environment as a mapping: Compose leaves the list form (KEY=value) as written."""
    return dict(item.partition("=")[::2] for item in value) if isinstance(value, list) else value


def _port(port):
    """A port as a mapping: {target, published, host_ip}, the last two when it names them. Compose leaves the short form
    as written when it holds a variable."""
    if isinstance(port, dict):
        return port
    text, host_ip = port.split("/")[0], None
    address = re.match(r"\[([^\]]*)\]:", text)   # an IPv6 address is in brackets
    if address:
        host_ip, text = address.group(1), text[address.end():]
    *host, target = re.split(r":(?![-?+])", text)   # not the colon of ${VAR:-default}
    if len(host) > 1 and host_ip is None:
        host_ip, host = host[0], host[1:]
    found = {"target": int(target) if target.isdigit() else target}
    if host and host[-1]:
        found["published"] = host[-1]
    if host_ip:
        found["host_ip"] = host_ip
    return found


# A pull_policy line: what leads to its value, the value, then the comment that may follow
POLICY = re.compile(r"(\s*pull_policy:[ \t]*)([^#]*?)([ \t]*(?:#.*)?)")


def _policies_written(text):
    """A compose file's text with each `pull_policy` that names a variable written as the default of that variable
    (`missing`, Compose's own, when it has none). Compose 5.5.1 and later refuse `${VAR:-never}` there when they do not
    interpolate, which is how raw() reads a file."""
    def written(line):
        found = POLICY.fullmatch(line)
        if not (found and "$" in found.group(2)):
            return line
        defaults = [default.strip("\"' ") for _, default, _ in references(found.group(2))
                    if default and "$" not in default]
        return f"{found.group(1)}{defaults[-1] if defaults else 'missing'}{found.group(3)}"
    return "\n".join(written(line) for line in text.split("\n"))


def _closure(path, skip=()):
    """A project file and the project files it includes, and theirs, in the order they are found. A file in `skip` is
    not followed."""
    found, todo = [], [path]
    while todo:
        file = todo.pop(0)
        if file not in found:
            found.append(file)
            todo += [target for target in includes(file) if target not in skip]
    return found


def _items(file, text):
    """The items of the `include` list in the text of a project file (_include_items), those that a copy can be
    written for: not the files that an item lists under its `path:`, which are read where they are."""
    return _include_items(text, ROOT / file, ROOT if file == ON_FILE else None, nested=False)


def _extent(lines, number):
    """How many lines the list item that starts at `number` takes: its own, and those of the keys of its long form
    (`project_directory`, `env_file`) below it, which are indented deeper than its dash. Blank lines and comments
    between them go with it; those after the last one do not."""
    dash, last = lines[number].index("-"), number
    for after in range(number + 1, len(lines)):
        text = lines[after].strip()
        if text and not text.startswith("#"):
            if len(lines[after]) - len(lines[after].lstrip()) <= dash:
                break
            last = after
    return last - number + 1


@contextlib.contextmanager
def _readable(path, skip=()):
    """What raw() tells Compose to read: the file itself (`-f path`), or, when it or a file it includes has a
    `pull_policy` variable (_policies_written) or an include in `skip`, copies in a folder removed afterwards: each
    with its pull policies written as their defaults and without the items of its include list that name a file in
    `skip` (a list left empty is read as none). A copy is read from the folder of its original, so that its relative
    paths are the same, and what includes a file includes its copy: with the folder of the original as its project
    directory, unless the item names one, which is kept (it is relative to the same folder). Answers (the arguments,
    {copy: the file it stands for})."""
    skip = frozenset(skip)
    try:
        texts = {file: (ROOT / file).read_text(encoding="utf-8") for file in _closure(path, skip)
                 if (ROOT / file).is_file()}
    except (OSError, ValueError):   # being saved, or not text: Compose says what is wrong with it
        texts = {}
    changed = {file: _policies_written(text) for file, text in texts.items()}
    if changed == texts and not (skip and any(target in skip for file, text in texts.items()
                                              for _, _, target in _items(file, text))):
        yield ["-f", path], {}
        return
    with tempfile.TemporaryDirectory(dir=scratch()) as folder:
        copies = {file: Path(folder) / f"{number}.yml" for number, file in enumerate(texts)}
        for file, text in changed.items():
            written = text.split("\n")
            lines = list(written)
            for number, _, target in _items(file, text):
                if lines[number] is None:   # a line of an item left out
                    continue
                if target in skip:   # left out whole, so that the line numbers of the others hold
                    for dropped in range(number, number + _extent(written, number)):
                        lines[dropped] = None
                elif target in copies:
                    indent = lines[number][:len(lines[number]) - len(lines[number].lstrip())]
                    named = any(DIRECTORY.match(line) for line in written[number + 1:number + _extent(written, number)])
                    lines[number] = (f"{indent}- path: {json.dumps(str(copies[target]))}" if named else
                                     f"{indent}- path: {json.dumps(str(copies[target]))}\n"
                                     f"{indent}  project_directory: {json.dumps(str((ROOT / target).parent))}")
            copies[file].write_text("\n".join(line for line in lines if line is not None), encoding="utf-8")
        yield (["--project-directory", str((ROOT / path).parent), "-f", str(copies[path])],
               {str(copy): str(ROOT / file) for file, copy in copies.items()})


def _text_of(file):
    try:
        return Path(file).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def raw(path, skip=(), timeout=120):
    """A compose file as written, with what it includes merged in. Not interpolated: ${VAR} references are kept,
    except in a `pull_policy`, which Compose 5.5.1 and later refuse (_policies_written). Not checked for consistency:
    a file read alone may refer to services of the core. Labels, environment and ports come in one shape, whichever
    way the file writes them. `skip`: project files that it includes and that are not there, which are left out so
    that it still loads and says what it is. `timeout`: seconds Compose has to answer, else ComposeError."""
    before = None if skip else _text_of(ROOT / path)
    with _readable(path, skip) as (arguments, originals):
        try:
            config = _config(arguments, "--no-interpolate", "--no-consistency", timeout=timeout)
        except ComposeError as error:   # Compose names the copy it read: the file is the original
            message = str(error)
            for written, original in originals.items():
                message = message.replace(written, original)
            raise ComposeError(message) from None
    if before is not None and _text_of(ROOT / path) == before:   # what x_gravitee() answers of that text there: not asked again
        _remember((hashlib.sha256(before.encode("utf-8", "surrogatepass")).hexdigest(), str((ROOT / path).parent)),
                  config.get("x-gravitee"))
    for service in (config.get("services") or {}).values():
        for key in ("labels", "environment"):
            if key in service:
                service[key] = _mapping(service[key])
        if service.get("ports"):
            service["ports"] = [_port(port) for port in service["ports"]]
    return config


ALONE_TIMEOUT = 15   # seconds Compose has to read one item's text: a file that takes longer is one that cannot be read
ALONE_OUTPUT = 4 << 20   # the most its answer may hold: more is no item, but a file that makes Compose write without end
READ_KEPT = 512   # readings of an x-gravitee block remembered (_said_by_compose): more than the items of a large Lab
_said_by_compose = {}   # {(sha-256 of a compose text, its folder): its x-gravitee as Compose read it there}, oldest first
_said_lock = threading.Lock()


def x_gravitee(text, folder):
    """The x-gravitee block of a compose text as Compose reads it in `folder`, where the item is: what it includes is found from
    there as when the Lab reads the item (an included file never brings an x- key of its own: Compose keeps those of the file it
    reads), with the scrubbed environment, no interpolation and no remote include. Compose reads the text from its input, with
    `folder` as the project directory, so that nothing is written anywhere. A copy of what it answered, None for no block.
    ComposeError when Compose cannot read it, takes more than ALONE_TIMEOUT seconds, or answers more than ALONE_OUTPUT: never a
    wait without end. The same text in the same folder is read once, and a file raw() read is not read again (_remember)."""
    key = (hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(), str(folder))
    with _said_lock:
        if key in _said_by_compose:
            return copy.deepcopy(_said_by_compose[key])
    rc, out, err = run([*COMPOSE, "--project-directory", str(folder), "-f", "-", "config", "--format", "json", "--no-interpolate",
                        "--no-consistency"], timeout=ALONE_TIMEOUT, cwd=folder if os.path.isdir(folder) else None,
                       environ=scrubbed_environment(), given=_policies_written(text))
    if rc != 0:
        raise ComposeError(_last_line(err))
    if len(out) > ALONE_OUTPUT:
        raise ComposeError("Compose answered more than an item can hold")
    try:
        found = json.loads(out).get("x-gravitee")
    except (ValueError, AttributeError):
        raise ComposeError("Compose answered what is not a compose file") from None
    _remember(key, found)
    return copy.deepcopy(found)


def _remember(key, found):
    with _said_lock:
        _said_by_compose.pop(key, None)
        _said_by_compose[key] = copy.deepcopy(found)
        while len(_said_by_compose) > READ_KEPT:
            del _said_by_compose[next(iter(_said_by_compose))]


def config_json(file_args, timeout=15, environ=None):
    """These files as Compose runs them, variables replaced by their values, whole: the services, and the volumes,
    networks, secrets and configs it declares, in the shapes Compose gives them (a mount is typed `bind` once its
    source is known to be a folder, which only the interpolated form tells). What a verdict on a file written by someone
    else reads, so the time it has is short: past it, ComposeError. `environ`: the environment to read them in, in place
    of the Lab's (scrubbed_environment() for files that a catalog's author wrote and CI judges)."""
    return _config(file_args, timeout=timeout, environ=environ)


def resolved(file_args):
    """The services of these files as Compose runs them, variables replaced by their values."""
    return _config(file_args).get("services", {})


def all_files(paths, replace=None):
    """The `-f` arguments for the core with these add-on and playground files on top. Compose has no optional
    include, so a generated file outside the project includes them. `replace` swaps a file for another: an edited
    copy being checked."""
    replace = replace or {}
    core = ["-f", replace.get(CORE_FILE, CORE_FILE)]
    if not paths:
        return core
    text = "include:\n" + "".join(f"  - {json.dumps(str(ROOT / replace.get(p, p)))}\n" for p in paths)
    target = scratch() / f"all-{hashlib.sha256(text.encode()).hexdigest()[:12]}.yml"
    if not target.is_file():
        write_atomic(target, text)
    return [*core, "-f", str(target)]


def target_copy(original, platform):
    """Where the copy of a project file written for a platform is kept: .lab/targets/<platform>/<path, / as __>."""
    return f"{TARGETS}/{platform}/{original.replace('/', '__')}"


def _original(copy):
    """The project file that a copy (target_copy) was made of."""
    return copy.rsplit("/", 1)[1].replace("__", "/")


def targets(path):
    """The files of an include list that are copies written for a platform: {the project file they were made of: the
    platform}. What the on file says of where each item runs; an item that is not here runs on this machine."""
    file = ROOT / path
    found = {}
    for line in (file.read_text(encoding="utf-8").split("\n") if file.is_file() else []):
        match = TARGET_ITEM.fullmatch(line)
        if match:
            found[_original(match.group(1))] = match.group(2)
    return found


def retarget(text, names):
    """The text of a compose file with a service named again wherever it is named: its own entry, what depends on it and
    a host that its address names. `names`: {the service as the file says it: the service of the platform}. A name is
    replaced whole: gio-apim-gateway, a container name, stays."""
    for old, new in names.items():
        text = re.sub(rf"(?<![\w.-]){re.escape(old)}(?![\w-])", new, text)
    return text


HEADER = "# Written for another platform by the Lab UI, from the file it names: "


def write_target(original, platform, names):
    """Writes the copy of a project file for a platform (retarget): the file to include in its place. Its first line says
    how it was made, so that it can be made again when the file changes. Answers its path."""
    copy = target_copy(original, platform)
    said = ",".join(f"{old}={new}" for old, new in sorted(names.items()))
    (ROOT / copy).parent.mkdir(parents=True, exist_ok=True)
    write_atomic(ROOT / copy, f"{HEADER}{original} ({said})\n" + retarget((ROOT / original).read_text(encoding="utf-8"), names))
    return copy


def target_names(copy):
    """How a copy was made (write_target): {the service as the file says it: the service of the platform}; empty for a
    file that is none."""
    try:
        first = (ROOT / copy).read_text(encoding="utf-8").split("\n", 1)[0]
    except OSError:
        return {}
    found = re.fullmatch(re.escape(HEADER) + r"\S+ \((.*)\)", first)
    return dict(pair.split("=", 1) for pair in found.group(1).split(",") if "=" in pair) if found else {}


def _include_items(text, file, base=None, nested=True):
    """The items of a compose file's `include` list: (line number, the path as written, the project file it names, or
    None outside the project). The next top-level key ends the list. An item in long form holds keys below its dash,
    and a list under one of them (`env_file`) is not a list of items; but the files of its `path:`, when it lists
    them, are, unless `nested` is False."""
    inside, column, key = False, None, None   # the column of the dashes of the items, the last key opened without a value
    for number, line in enumerate(text.split("\n")):
        indent = len(line) - len(line.lstrip())
        if re.match(r"include:\s*(#.*)?$", line):
            inside, column, key = True, None, None
            continue
        if re.match(r"[^\s#-]", line):
            inside = False
        if not inside:
            continue
        opened = KEY.fullmatch(line)
        if opened:   # `- path:` starts an item whose files are listed below, `env_file:` opens a list of something else
            dash, name = opened.groups()
            column = indent if dash and column is None else column
            key = name if not dash or indent <= column else None
            continue
        target = TARGET_ITEM.fullmatch(line)
        item = target or INCLUDE_ITEM.fullmatch(line)
        if not item:
            continue
        column = indent if column is None else column
        if indent <= column:   # an item of the list; deeper, a line of the list of a key of the item above
            key = None
        elif not (nested and key == "path"):
            continue
        if target:
            yield number, target.group(1), _original(target.group(1))
        else:
            written = next(filter(None, item.groups()))
            path = Path(os.path.normpath((base or file.parent) / written))
            yield number, written, path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else None


def includes(path):
    """The project files a compose file includes itself, each written relative to the file that includes it; those
    of the on file relative to the project, as Compose reads a file it is given."""
    file = ROOT / path
    text = file.read_text(encoding="utf-8") if file.is_file() else ""
    base = ROOT if path == ON_FILE else None
    return [target for _, _, target in _include_items(text, file, base) if target]


def retargeted(path, text, targets):
    """A compose file's text with the items of its `include` list that name one of `targets` ({project file: the file
    to include instead}) written again, relative to its folder: a copy next to it includes copies in their place."""
    file, lines = ROOT / path, text.split("\n")
    for number, written, target in list(_include_items(text, file)):
        if target in targets:
            relative = Path(os.path.relpath(ROOT / targets[target], file.parent)).as_posix()
            lines[number] = lines[number].replace(written, relative, 1)
    return "\n".join(lines)


def _closing(text, start):
    """Where the brace opened at `start` closes; the end of the text when it never does."""
    depth = 0
    for i in range(start, len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return i
    return len(text)


def _braced(body):
    """What ${...} holds: a name, then nothing, `:-default`, `-default`, `:?error`, `?error`, `:+other` or `+other`."""
    m = NAME.match(body)
    if not m:
        return []
    key, rest = m.group(), body[m.end():].lstrip(":")
    if rest[:1] == "-":
        return [(key, rest[1:], False)] + references(rest[1:])
    if rest[:1] == "+":
        return [(key, None, False)] + references(rest[1:])
    return [(key, None, True)]


def references(text):
    """The variables a text refers to, in order: (key, default or None, required). A default may refer to variables
    too (${A:-${B:-1}}): they follow. `$$` is a dollar sign, not a reference."""
    found, i = [], text.find("$")
    while 0 <= i < len(text) - 1:
        if text[i + 1] == "$":
            i += 1
        elif text[i + 1] == "{":
            end = _closing(text, i + 1)
            found += _braced(text[i + 2:end])
            i = end
        else:
            m = NAME.match(text, i + 1)
            found += [(m.group(), None, True)] if m else []
        i = text.find("$", i + 1)
    return found


def interpolated(text, lookup):
    """A text with its variables replaced the way Compose does: ${VAR}, ${VAR:-default} (the default also when the
    value is empty) and ${VAR-default} (only when it is not set), a default that refers to variables too, `$$` a dollar
    sign. `lookup(name)` gives a value, None when the variable is set nowhere. A ${VAR} set nowhere stays as it is
    written, so that what shows is the name of the setting, not an empty value; so does any other form."""
    found, i = [], 0
    while i < len(text):
        end = _closing(text, i + 1) if text.startswith("${", i) else len(text)   # a brace never closed is plain text
        if text.startswith("$$", i):
            found.append("$")
            i += 2
        elif end < len(text):
            found.append(_replaced(text[i + 2:end], text[i:end + 1], lookup))
            i = end + 1
        else:
            found.append(text[i])
            i += 1
    return "".join(found)


def _replaced(body, written, lookup):
    """What a ${...} holds, `body`, is replaced by (see interpolated); `written` is how it stands in the text."""
    m = NAME.match(body)
    if not m:
        return written
    value, rest = lookup(m.group()), body[m.end():]
    if rest.startswith(":-"):
        return value if value else interpolated(rest[2:], lookup)
    if rest.startswith("-"):
        return interpolated(rest[1:], lookup) if value is None else value
    return written if rest or value is None else value


def outside(text, char):
    """Where a character stands outside ${...}: a default may hold a colon or a slash."""
    found, depth = [], 0
    for i, c in enumerate(text):
        depth += (c == "{") - (c == "}")
        if c == char and depth == 0:
            found.append(i)
    return found


def _heading(lines, number):
    """The comment line heading the block a line is in: the nearest above, as deep or deeper, before a blank line."""
    indent = len(lines[number]) - len(lines[number].lstrip())
    for above in reversed(lines[:number]):
        text = above.strip()
        if not text or len(above) - len(above.lstrip()) < indent:
            return ""
        if text.startswith("#"):
            return text.lstrip("#").strip()
    return ""


def variables(path):
    """The variables a compose file refers to, in order of first appearance: [{key, default, required, comment}].
    The comment is what describes the variable: the one ending the line of its first use, or the one heading that
    block. Required: some reference has no default."""
    found, lines = {}, (ROOT / path).read_text(encoding="utf-8").split("\n")
    for number, line in enumerate(lines):
        m = COMMENT.search(line)
        code, comment = (line[:m.start()], m.group(1).strip()) if m else (line, "")
        for key, default, required in references(code):
            if key not in found:
                found[key] = {"key": key, "default": None, "required": False,
                              "comment": comment or _heading(lines, number)}
            if found[key]["default"] is None:
                found[key]["default"] = default
            found[key]["required"] = found[key]["required"] or required
    return list(found.values())


_name = {}


def project_name():
    """The core file's `name:`, the Compose project every container belongs to. Read again when the file changes.
    While the file does not load, or is not there (being saved, synced or renamed), the last name read still finds
    the containers; without one, Compose's error is raised, and not asked for again until the file changes."""
    try:
        stat = (ROOT / CORE_FILE).stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:   # not there: Compose's error says so
        key = None
    if not _name or _name["key"] != key:
        try:
            _name.update(key=key, value=raw(CORE_FILE)["name"])
        except ComposeError as error:
            _name.update(key=key, error=str(error))
    if "value" not in _name:
        raise ComposeError(_name["error"])
    return _name["value"]


def by_name():
    """Compose asked by project name, without the files: it then acts on every container of the project, also those
    of an add-on that was turned off since."""
    return [*COMPOSE, "-p", project_name()]


def digest(value):
    """A value as the Lab UI keeps it when it only compares it: a secret is never held longer than that."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _inspect(names, variable=None):
    """What only inspect tells of containers: {name: {oom, restarts, config_hash, image_id}}. `oom`: it was killed
    for running out of memory. `restarts`: the times Docker started it again after it died, counted while the last
    one is recent. `config_hash`: the hash of the configuration Compose created it from, `image_id` the image it
    runs. With `variable`, a variable of its environment: `variable` holds the digest of its value, None when it has
    none. A container removed since is left out."""
    if not names:
        return {}
    fields = INSPECT + (VARIABLE % variable if variable else "")
    _, out, _ = run(["docker", "container", "inspect", "--format", fields, *names], timeout=20)
    found = {}
    for parts in (line.split("|", 6) for line in out.splitlines()):
        if len(parts) < (7 if variable else 6):
            continue
        name, oom, restarts, started, config_hash, image = parts[:6]
        age = time.time() - calendar.timegm(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))
        found[name.lstrip("/")] = {"oom": oom == "true", "restarts": int(restarts) if age < RESTART_WINDOW else 0,
                                   "config_hash": config_hash or None, "image_id": image or None}
        if variable:
            _, set_, value = parts[6].partition("=")
            found[name.lstrip("/")]["variable"] = digest(value) if set_ else None
    return found


def ps(variable=None):
    """The project's containers, by service: {state, health, status, image, exit_code, name, ports, oom, restarts,
    config_hash, image_id, variable} (_inspect: `variable` names the variable of their environment to report a digest
    of). Asked by project name from a folder without compose files, so containers of what was turned off since are
    listed too."""
    command = [*by_name(), "ps", "-a", "--format", "json"]
    rc, out, err = run(command, timeout=30, cwd=scratch())
    if rc != 0 and "No such container" in err:   # removed while Compose was listing them: a job recreates it
        rc, out, err = run(command, timeout=30, cwd=scratch())
    if rc != 0:
        raise ComposeError(_last_line(err))
    containers = {}
    for line in filter(None, map(str.strip, out.splitlines())):
        c = json.loads(line)   # one container per line
        published = {p["PublishedPort"] for p in c.get("Publishers") or [] if p.get("PublishedPort")}
        containers[c["Service"]] = {"state": c.get("State"), "health": c.get("Health") or "",
                                    "status": c.get("Status"), "image": c.get("Image"),
                                    "exit_code": c.get("ExitCode"), "name": c.get("Name"),
                                    "ports": [str(port) for port in sorted(published)]}
    inspected = _inspect([c["name"] for c in containers.values()], variable)
    for c in containers.values():
        c.update(inspected.get(c["name"], {"oom": False, "restarts": 0, "config_hash": None, "image_id": None}))
    return containers


def containers(variable=None):
    """ps() for what shows even when Docker does not answer: (containers, why it did not). While the core file
    has not loaded once, the project's name is not known and Docker is not asked: the problem is the file's, not
    Docker's."""
    try:
        project_name()
    except ComposeError:
        return {}, None
    try:
        return ps(variable) if variable else ps(), None
    except ComposeError as error:
        return {}, str(error)


def config_hashes(files=(), env=None, dotenv=None):
    """What Compose would create each service as: {service: the hash of its configuration}, which it labels the
    containers it creates with (com.docker.compose.config-hash). Of these `-f` arguments, else of the project as `up`
    reads it: the core, the on file and what it includes, with their .env files. `env`: values that Compose reads
    before what the .env files hold, as they are; `dotenv`: the text of the core's .env, to read in its place (a
    private copy, removed once read: it holds secrets). None when the files do not load."""
    copy = scratch() / f"draft-{os.urandom(4).hex()}.env" if dotenv is not None else None
    try:
        if copy:
            write_atomic(copy, dotenv, mode=0o600)
        rc, out, _ = run([*COMPOSE, *(["--env-file", str(copy)] if copy else []), *files, "config", "--hash", "*"],
                         timeout=60, env=env)
    finally:
        if copy:
            copy.unlink(missing_ok=True)
    if rc != 0:
        return None
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def reference(image):
    """An image as Docker lists the ones it holds: repository:tag, without Docker Hub's own prefixes, `latest` when it
    names no tag."""
    name = re.sub(r"^(docker\.io/)?(library/)?", "", image)
    return name if re.search(r"[:@][^/]*$", name) else f"{name}:latest"


def image_ids():
    """The images Docker holds: {repository:tag: image id}; None when Docker does not answer. A pull or a build may
    move a tag to another image: asked again after every job."""
    def ask():
        rc, out, _ = run(["docker", "images", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"], timeout=30)
        return dict(line.split(" ", 1) for line in out.splitlines() if " " in line) if rc == 0 else None
    return CACHE.get("local:image-ids", 10, ask)


_same = {}


def same_image(container, image, running):
    """Whether a container that runs image id `running` runs what image id `image` holds for its platform. Docker's
    image store records each build in an image's id: built again from unchanged sources, a tag names another id for
    the same image, which Compose keeps running. What it runs is told by the manifest it was made from; an image
    built here has no other id to compare it with. Kept for good: an id always names the same content."""
    key = (image, running)
    if key not in _same:
        rc, out, _ = run(["docker", "container", "inspect", "--format", "{{json .ImageManifestDescriptor}}", container],
                         timeout=20)
        made = json.loads(out) if rc == 0 and out.strip() not in ("", "null") else None
        platform = (made or {}).get("platform") or {}
        if not made or not platform.get("os"):
            return False   # no manifest to compare: another id is another image
        wanted = "/".join(filter(None, (platform["os"], platform.get("architecture"), platform.get("variant"))))
        rc, out, _ = run(["docker", "image", "inspect", "--platform", wanted, "--format", "{{.Id}}", image], timeout=20)
        if rc != 0:
            return False   # not known now: asked again next time
        _same[key] = out.strip() == made["digest"]
    return _same[key]


def image_key(service):
    """The image a service's files are read from, as shell() reads them, by its id: the one its container runs while it
    runs, else the one its configured image names here; that name when Docker holds no such image. What is read
    from an image is kept under it: once the container runs another one, or the tag moves, it is read again."""
    rc, out, _ = run(["docker", "container", "inspect", "--format", "{{.State.Running}} {{.Image}}",
                      service["container"]], timeout=20)
    running, _, image = out.strip().partition(" ")
    if rc == 0 and running == "true" and image:
        return image
    rc, out, _ = run(["docker", "image", "inspect", "--format", "{{.Id}}", service["image"]], timeout=20)
    return out.strip() if rc == 0 and out.strip() else service["image"]


def unreachable(error):
    """Whether what Docker printed says that it does not answer at all."""
    return bool(re.search(r"Cannot connect to the Docker daemon|error during connect|Is the docker daemon running"
                          r"|failed to connect to the docker API", error or ""))


# What a Docker CLI that has no Compose plugin says to `docker compose ...` (measured: Docker 29, in a configuration
# folder without the plugin); the usage text that follows it is not looked at
NO_COMPOSE = "docker: unknown command: docker compose"
# A line that starts with one of these: the Docker CLI's own words, or the error of starting `docker` at all. Compose
# puts a name from a file inside a sentence, with its newlines escaped (`service "a" depends on undefined service
# "..."`, measured), so a stranger's file cannot start a line with these words and make a refusal of its own read as a
# missing Compose
_SIGNS = (
    NO_COMPOSE,                                         # `docker compose version`: the CLI knows no such command
    "unknown flag: --progress",                         # the Lab's COMPOSE, `docker compose --progress plain`, and a
    "unknown shorthand flag: 'f' in -f",                # `-f` of the files: the CLI reads them as its own flags
    "docker: 'compose' is not a docker command",        # the CLI of an older Docker
    "[Errno 2] No such file or directory: 'docker'",    # no `docker` to run
)
# A few blanks may come before them
INDENT = 64


def missing(error):
    """Whether what a command printed says that Docker Compose is not there: no `docker` to run, or a Docker without the
    Compose plugin, which says `docker: unknown command: docker compose` to `docker compose version`, `unknown flag:
    --progress` to the Lab's own command line (COMPOSE), and `'compose' is not a docker command` if it is an older one.
    What "Docker Compose not found" is reported from, apart from plugin() (None: there is no plugin to link) and a
    run with the scrubbed environment (config_json raises NO_COMPOSE for it); a file that Compose refuses is not that.
    Reads at the start of a line: each place a phrase is found is looked at for the characters just before it (INDENT
    and one more, for the line break), so a text of any size is read in one pass."""
    text = error or ""
    for sign in _SIGNS:
        at = text.find(sign)
        while at != -1:
            _, newline, before = text[max(0, at - INDENT - 1):at].rpartition("\n")
            if (newline or at <= INDENT) and not before.strip(" \t"):
                return True
            at = text.find(sign, at + 1)
    return False


def unread(image, said):
    """Why what an image holds could not be read, in plain words, from what Docker said (shell()): it does not
    answer, or the image is not here; None for anything else."""
    if unreachable(said):
        return "Docker is not reachable"
    if "No such image" in (said or ""):
        return f"{image} is not pulled"
    return None


def shell(service, args, timeout=120):
    """Runs `sh` with these arguments where a service's files are: in its container, or in its image when Docker
    cannot run it there (the container is missing, stopped, restarting or paused). An image that is not here is not
    pulled for it: a question must not start a download. Answers what run() does."""
    rc, out, err = run(["docker", "exec", service["container"], "sh", *args], timeout)
    if rc != 0 and ("Error response from daemon" in err or "No such container" in err):   # not the script failing
        return run(["docker", "run", "--rm", "--pull", "never", "--entrypoint", "sh", service["image"], *args],
                   timeout)
    return rc, out, err


def loaded():
    """The services of the files Compose reads now, the core's with what is on: an add-on that is off has none here, and
    `pull` or `up` of its service says "no such service". None when Compose cannot say."""
    def ask():
        code, out, _ = run(["docker", "compose", "config", "--services"], 60)
        return set(out.split()) if code == 0 else None
    return CACHE.get("local:loaded-services", 5, ask)


def docker_memory():
    """The memory Docker has, or None when Docker is not reachable."""
    def ask():
        rc, out, _ = run(["docker", "info", "--format", "{{json .}}"], timeout=20)
        info = json.loads(out) if rc == 0 else {}
        return info.get("MemTotal") if info.get("ServerVersion") else None
    return CACHE.get("docker-memory", 30, ask)
