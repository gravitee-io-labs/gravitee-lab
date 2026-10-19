"""The trust policy of catalogs: what an item of a catalog may ask of this machine, judged from the
configuration Compose resolves, and what it will do, shown before it runs. Pure: no disk, no Docker, no network, no clock.
Paths are compared through `context["realpath"]` (the caller hands in os.path.realpath, which is how a link cannot hide where
a bind leads; without it they are compared as written, normalized, and a link passes), and everything else is text and numbers
handed in, so a verdict is a function of its arguments.

The promise is narrow: an item cannot take over the host or Docker, read the person's credentials or the Lab's
tokens, change silently, or leak private content. The policy is default-deny: a key it does not know blocks, so a Compose
upgrade that adds a dangerous key fails closed. Every rule is class P (it protects the Lab and the person, always) except
T03 to T10 and the bind outside the allowed roots of T11, which are class H (the item takes more of this machine than a
container does): an H finding is allowed only when the catalog is listed or allowed (`host_ok`) and the item says why in
`x-gravitee.host_access`. No catalog is exempt by identity.

What judge reads:
- `unit`: {kind, id, name, meta (its x-gravitee block), references (the names its compose files read with ${NAME}, and the
  environment names they pass through: reads(); the files that env_files() names are read the same way), seed_references (the names its seed/** files read), includes (the
  project-relative targets its own file includes, None for one that leaves the project), seed (its seed service)}.
- `unit_config`, `core_config`: the unit with the core, and the core alone, as `docker compose config --format json` gives
  them. `own`: the services the unit defines that neither the core nor an included unit defines. `overlaid`: {service: {key:
  what its own file adds}} on a service of the core or of an included unit (overlays()).
- `roots` (build_roots()): {unit, state, secrets, plugins, closure, seeder, machine}. `env`: the variable names set in its
  .env and the root .env.
- `context`: {host_ok (the catalog is listed, or its entry says host: true), listed, local, ns, requires (the namespaces its
  catalog declares), shared_settings, core_variables, license_variable, closure_names, closure_services, seeder_images, home,
  level, contract, contract_min, realpath, screened (the text screen's reasons), error (why Compose could not read it),
  audience (the catalog's: "public" or "private") and closure_audiences (the audience of each catalog the unit's closure
  reaches)}. The last two are how a caller that has measured the audiences, the command line with `--online` for one, gets T24
  from judge itself; the Lab's own pipeline adds T24 with `add_reason` and gives neither. Judge measures nothing.
Every key is optional and absent means the narrowest reading: nothing allowed, no host access. The one exception is `realpath`:
absent, the paths are compared as written, so the caller that reads a disk must always hand it in.

What a caller builds around it: `build_roots` (the roots, the one function that reads the disk), `overlays` (the overlaid
services from compose.raw), `reads` and `seed_reads` (the settings a unit reads), `host_ok` (the one place that decides
`context["host_ok"]`), `prescreen`, `env_files` and `include_targets` (T19, before Compose is asked: what a text refuses, the
files it reads into the environment, which `reads` reads for T20, and the files it includes, which the closure follows;
`unescaped` is the text as they read its letters), then `add_reason` for T21 and T24 and `combine` for T25 once the units of
a project are judged.

What a caller says to a person: the reason lines are the Lab's (`RULES`, `line`) and are all a popover shows. The command line
and CI add the fix (`FIXES`, `fix`) after the reason, so a message is "reason. Fix: fix"; no fix names a place the Lab keeps
its own files, because the reader of a catalog never has them."""

import posixpath
import re

from .resolve import audience_blocked, rank, shown as _clean, target_of

# The service keys the policy checks or that cannot hurt: the keys the units use, and the harmless Compose keys. A key
# outside this set and HOST_KEYS blocks (T01). `x-` keys are extensions Compose ignores. Absent on purpose: volumes_from,
# models, post_start, pre_stop, develop (and extends, which Compose consumes: the text screen reads it).
SERVICE_KEYS = frozenset("""
annotations attach blkio_config build cap_drop cgroup command configs container_name cpu_count cpu_percent cpu_period
cpu_quota cpu_rt_period cpu_rt_runtime cpu_shares cpus cpuset credential_spec depends_on deploy dns dns_opt dns_search
domainname entrypoint environment expose external_links extra_hosts group_add healthcheck hostname image init ipc
isolation labels links logging mac_address mem_limit mem_reservation mem_swappiness memswap_limit network_mode networks
oom_kill_disable oom_score_adj pid pids_limit platform ports profiles pull_policy pull_refresh_after read_only restart
scale secrets security_opt shm_size stdin_open stop_grace_period stop_signal storage_opt tmpfs tty ulimits user
userns_mode uts volumes working_dir""".split())
# The keys that take more of this machine, each judged by a class H rule (T03 to T09): known to T01, so that a unit that
# uses one is told what it takes ("Runs with full privileges"), not that a key is unknown
HOST_KEYS = frozenset(("privileged", "use_api_socket", "provider", "devices", "device_cgroup_rules", "gpus", "cap_add",
                       "sysctls", "runtime", "cgroup_parent"))
# The keys that name a file Compose reads into the environment. Compose merges that content into `environment` before the
# JSON exists, so they are in the JSON only when it was asked not to (`config --no-env-resolution`), and then they are judged
# there (T19), the paths by their real place: a link in the unit's folder that leads out of it shows only here.
FILE_KEYS = ("env_file", "label_file")
TOP_KEYS = frozenset(("name", "services", "volumes", "networks", "secrets", "configs"))   # and `x-*` (T02)
# What a unit's file may add to a service of the core or of a unit it includes (T22)
OVERLAY_KEYS = ("environment", "extra_hosts", "depends_on", "labels", "volumes", "ports")
MOUNT_TYPES = ("bind", "volume", "tmpfs", "npipe", "image")   # the mounts Compose 5 prints; any other type is a feature the Lab has not seen
LOOPBACK = ("127.0.0.1", "::1")   # the only addresses a published port may bind (T17)
MAX_FILE = 1024 * 1024   # a file the text screen reads (T19)
# What no unit mounts, whatever it declares: the secrets and the Lab's state, and the code that runs on this machine
MACHINE_FILES = (".env", ".lab", "seeder", "lab-ui", "docker-compose.yml", "Makefile", ".git")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "host.docker.internal")   # where a console or a check may point (N03)
PINNED = re.compile(r"[0-9a-f]{40}(?::.*)?")   # what follows the # of a remote build context (T18)
# The build keys a unit may use (T18), and no other: a key that is not here reaches beyond the build (ssh, secrets, the
# cache, network, entitlements, tags, privileged), or takes the Dockerfile from the file (dockerfile_inline), or is one that a
# later Compose adds. The list is default-deny, and the CI stage that builds (BD02) holds the same one. `additional_contexts`
# is the one more key, for a build of the Lab's seeder alone
BUILD_KEYS = ("context", "dockerfile", "args", "target", "labels")
SEEDER_BUILD_KEYS = ("additional_contexts",)

# The reason lines of 8.3. A rule with several lines yields each that holds, except T06 and T26, which yield the first. `class`
# P, H, or N (a restriction that does not block).
RULES = {
    "T01": {"class": "P", "lines": ("Uses a Compose feature the Lab does not check: {key}",)},
    "T02": {"class": "P", "lines": ("Uses a Compose feature the Lab does not check: {key}",)},
    "T03": {"class": "H", "lines": ("Runs with full privileges",)},
    "T04": {"class": "H", "lines": ("Reaches Docker from inside a container",)},
    "T05": {"class": "H", "lines": ("Runs a program on this machine",)},
    "T06": {"class": "H", "lines": ("Uses the host network (network_mode)", "Uses host processes (pid)",
                                    "Shares host memory (ipc)", "Uses host namespaces (others)")},
    "T07": {"class": "H", "lines": ("Adds Linux capabilities",)},
    "T08": {"class": "H", "lines": ("Uses devices of this machine",)},
    "T09": {"class": "H", "lines": ("Lowers container isolation",)},
    "T10": {"class": "H", "lines": ("Mounts the Docker socket",)},
    "T11": {"class": "H", "lines": ("Mounts files outside its folder",)},
    "T12": {"class": "P", "lines": ("Writes to its own folder",)},
    "T13": {"class": "P", "lines": ("Mounts a volume that is not its own",)},
    "T14": {"class": "P", "lines": ("Mounts a volume that is not its own",)},
    "T15": {"class": "P", "lines": ("Joins a network that is not the Lab's",)},
    "T16": {"class": "P", "lines": ("Reads files outside its folder",)},
    "T17": {"class": "P", "lines": ("Publishes a port to the network",)},
    "T18": {"class": "P", "lines": ("Builds from outside its folder", "Builds from an unpinned source",
                                    "Builds with extra access")},
    "T19": {"class": "P", "lines": ("Includes content from outside the catalog", "Reads files outside its folder")},
    "T20": {"class": "P", "lines": ("Reads settings that are not its own: {names}",)},
    "T21": {"class": "P", "lines": ("Same name as {owner}",)},
    "T22": {"class": "P", "lines": ("Changes {label} beyond adding settings",)},
    "T23": {"class": "P", "lines": ("Includes content from outside the catalog",)},
    "T24": {"class": "P", "lines": ("Depends on private content",)},
    "T25": {"class": "P", "lines": ("Needs {name}, which is blocked",)},
    "T26": {"class": "P", "lines": ("Needs Lab level {level}, this Lab reads {min} to {max}",
                                    "Written for an older Lab (level {level})")},
    "T27": {"class": "P", "lines": ("Cannot be read: {last}",)},
    "T28": {"class": "P", "lines": ("Uses this machine without saying why: {service}",)},
    "N01": {"class": "N", "lines": ("Brings its own seed code",)},
    "N02": {"class": "N", "lines": ("Seed data reads a token",)},
    "N03": {"class": "N", "lines": ("Some of its addresses are not on this machine, so they are not opened",)},
    "N04": {"class": "N", "lines": ("Its gateway is not used: the Cloud pages do not offer it",)},
    "N05": {"class": "N", "lines": ("Its terminal is not offered by this catalog",)},
}
# What to do about a rule, for the author who reads it in `make check` or in CI (the standard's 6.10): one line for each rule,
# said after its reason as "Fix: ...". T01 to T27 are the standard's own words. The blanks are the ones `fix` fills: {key}
# (what the Lab does not check), {NS} (the namespace as a setting prefix), {name} (the unit that is blocked), {min} and {max}
# (the levels this Lab reads), {file} (the unit's file). No fix names a place under `.lab/`: a git install is read only, and
# the person who reads the fix is writing the catalog, where there is no such folder. A restriction (N01 to N05) blocks
# nothing: its fix says what lifts it.
FIXES = {
    "T01": "Remove `{key}`, or ask the Lab's maintainers to support it.",
    "T02": "Remove `{key}`, or ask the Lab's maintainers to support it.",
    "T03": "Remove `privileged`: a demo that needs it is a personal item.",
    "T04": "Remove `use_api_socket`.",
    "T05": "Remove `provider`; use a container.",
    "T06": "Publish a port on 127.0.0.1 instead of sharing the host's network, processes or memory.",
    "T07": "Remove `cap_add`; run the image as it is meant to run.",
    "T08": "Remove `devices`, `gpus`, `runtime` and device reservations.",
    "T09": "Keep only `no-new-privileges` in `security_opt`; remove `sysctls`.",
    "T10": "Remove the mount of the Docker socket.",
    "T11": "Move the files under the unit's folder, or use a named volume.",
    "T12": "Mount it `:ro`, or put the data under `.state/`.",
    "T13": "Use a named volume the unit declares itself.",
    "T14": "Use a named volume the unit declares itself.",
    "T15": "Attach the service to `internal` or `edge` only.",
    "T16": "Point `file:` at a file inside the unit.",
    "T17": "Write the port as `127.0.0.1:HOST:CONTAINER`.",
    "T18": "Put the build context under the unit, pin a remote context to a 40-character commit after `#`, and remove ssh, "
           "secrets and cache options. Only `context`, `dockerfile`, `args`, `target` and `labels` are accepted "
           "(default-deny; `additional_contexts` only for the Lab's seeder).",
    "T19": "Use a relative path inside the catalog.",
    "T20": "Prefix the setting with `{NS}_`, or read only `*_VERSION`, `*_REGISTRY`, `*_PULL_POLICY` and the shared settings.",
    "T21": "Prefix service, container and volume names with the catalog id.",
    "T22": "On a platform service, set only `environment`, `extra_hosts`, `depends_on`, `labels`, read-only binds from your "
           "folder and loopback ports.",
    "T23": "Include only units of the default catalog, of this catalog, or of catalogs declared in `requires.catalogs`.",
    "T24": "A Public catalog cannot include a Private one: move the shared part to a Public catalog.",
    "T25": "Fix {name} first.",
    "T26": "Set `lab:` in `catalog.yml` to a level this Lab reads ({min} to {max}), or move `lab.ref` in "
           "`.github/standard.json`.",
    "T27": "Run `docker compose -f {file} config` and fix what it prints.",
    "T28": "Say why in `x-gravitee.host_access`, one reason for each service that uses this machine, or remove what it takes.",
    "N01": "Run the Lab's seeder as it is: build its folder with no other key, set no entrypoint or command, and mount only "
           "`/seed` and `/state`.",
    "N02": "Do not read `APIM_TOKEN` or a secret in `seed/`: the Lab hands the seeder what it needs.",
    "N03": "Point `io.gravitee.console.*.url` and `requires.host[].check` at localhost, 127.0.0.1, [::1] or "
           "host.docker.internal.",
    "N04": "Remove `x-gravitee.gateway`: only a catalog the Lab lists, or one a person allowed to use their machine, may "
           "declare a gateway.",
    "N05": "Name one of the unit's own services, and not its seed, in `x-gravitee.terminal`.",
}
SEED_OWN = "Brings its own seed code"   # N01: why a place other than This machine is off for a playground (places.seed_env)
SEED_TOKEN = "Seed data reads a token"   # N02: the same for seed data that reads a token
NOT_OFFERED = "Not offered by this catalog"   # why the terminal pane and the run buttons are disabled (N05)
SHOWN = 3   # names a line spells out before it counts the rest
SOURCE_CUT = 120   # an image or the address of a build, as the preview shows it
NAME_CUT = 60   # the characters of one author's name in a reason line
FIELD_CUT = 240   # and of a whole field: three names, the count of the rest

# The names of what is secret (check.SECRET_NAME and check.NOT_SECRET, which a test keeps equal): a seed that reads one
# gets no token to hold (N02)
SECRET_NAME = re.compile(r"PASSWORD|PASSWD|PASSPHRASE|SECRET|API[_.-]?KEY|(?:^|[_.-])TOKEN$|(?<!PUBLIC)[_.-]KEY$", re.I)
NOT_SECRET = re.compile(r"(?:URL|URI|PATH|FILE|TYPE|ENDPOINT|ENABLED|HOST|PORT|METHOD|POLICY|REGEX|NAMESPACE|PROVIDER"
                        r"|TIMEOUT|TIMEOUTMS)$", re.I)
TOKEN_NAME = "APIM_TOKEN"   # what a deploy to a place hands the seed
SEED_MOUNTS = ("/seed", "/state")   # the only places a seed service mounts: its data, and what it records
LOADER_VARIABLE = re.compile(r"PYTHON|LD_|DYLD_|OPENSSL_|GCONV_|(?:PATH|HOME|LOCPATH|NLSPATH)$")


def _number(code):
    return code[0], int(code[1:])


def line(code, variant=0, **fields):
    """The reason line of a rule, with its blanks filled: line("T21", owner="Echo") is "Same name as Echo". The one way to
    word a reason that judge does not give itself (T21, T24). What an author wrote goes through a person's eyes and into the
    state the page polls, so a field is cleaned and cut."""
    return RULES[code]["lines"][variant].format(**{name: _clean(value, FIELD_CUT) if isinstance(value, str) else value
                                                   for name, value in fields.items()})


_BLANK = re.compile(r"\{(\w+)\}")


def fix(code, **fields):
    """The fix of a rule (FIXES), with the blanks the caller can fill: fix("T26", min=1, max=2). A blank it is given no field
    for stays as written. `NS` is also made from `ns`, the namespace as the Lab keeps it (acme-hub), as the prefix a setting
    takes (ACME_HUB). What an author wrote goes through a person's eyes, so a field is cleaned and cut like a reason's. An
    unknown code is a KeyError, as for `line`."""
    if "NS" not in fields and isinstance(fields.get("ns"), str):
        fields["NS"] = _env_name(fields["ns"])
    return _BLANK.sub(lambda found: _clean(str(fields[found.group(1)]), FIELD_CUT) if found.group(1) in fields
                      else found.group(0), FIXES[code])


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _true(value):
    """A flag as Compose reads it: true, or a text that says so."""
    return value is True or isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on")


def _spelled(items, limit=SHOWN):
    """Names as a line says them: the first few, then how many are left."""
    items = [_clean(item, NAME_CUT) for item in items]
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _repository(image):
    """An image without its tag or digest: registry/name."""
    name = str(image or "").split("@")[0]
    last = name.rsplit("/", 1)[-1]
    return name[:len(name) - len(last)] + last.split(":")[0]


def _env_name(text):
    """A namespace or a unit name as a setting prefix writes it: acme-hub -> ACME_HUB."""
    return re.sub(r"[^A-Z0-9]+", "_", str(text).upper()).strip("_")


def _unit_name(uid):
    return str(uid or "").rsplit("/", 1)[-1]


def _scrubbed(text, root=None):
    """A message of Compose for a person: no path of this machine. The project folder goes, the catalog's place
    (.lab/catalogs/<ns>/) with it, and any absolute path left is its last name."""
    text = str(text)
    if root:
        text = text.replace(root.rstrip("/") + "/", "")
    text = re.sub(r"\.lab/catalogs/[a-z0-9-]+/", "", text)
    return re.sub(r"(?<![\w.~:/-])(?:/[\w.@+~-]+)+/([\w.@+~-]+)", r"\1", text)


# ---------------------------------------------------------------------------------------------------------------------
# Paths: compared as the caller's realpath says, never touched here
# ---------------------------------------------------------------------------------------------------------------------

def _resolver(realpath=None):
    """A function from a path to the path compared: `realpath` (os.path.realpath in production, so that a link cannot hide
    where a bind leads) or, without it, the path as written, normalized. A relative path is relative to `base`, and none
    without one: Compose gives every bind and build context as an absolute path, so a relative one is a spelling that
    matches no root. An exotic spelling can only miss an allow-list (a false Blocked), never pass."""
    real = realpath if callable(realpath) else None
    cache = {}

    def resolve(path, base=None):
        if not isinstance(path, str) or not path or "\0" in path:
            return ""
        if not posixpath.isabs(path):
            if not base:
                return ""
            path = posixpath.join(base, path)
        if path not in cache:
            try:
                cache[path] = posixpath.normpath(real(path) if real else path)
            except (OSError, ValueError):
                cache[path] = posixpath.normpath(path)
        return cache[path]
    return resolve


def _inside(path, root):
    return bool(path) and bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))


def _beside(path, root):
    """Whether a path is a file of the Lab or holds one: inside the root or around it. Case does not matter, as it does not
    on the file system of a Mac: `.ENV` is the file `.env` there, and a false match is a false Blocked, the safe side."""
    path, root = path.casefold(), root.casefold()
    return _inside(path, root) or _inside(root, path)


def build_roots(root, folder, closure=(), products=(), machine=None, realpath=None):
    """The `roots` of judge for one unit, every path real. `root`: the project; `folder`: the unit's own folder (None for a
    setup); `closure`: the folders of the units of its include closure that sit in the same catalog (a unit of another
    catalog is never a root, so its .env, secrets and state stay unreadable to a stranger, even read-only); `products`: the
    names under plugins/; `machine`: the paths that no unit may mount whatever it declares (default: what holds the core's
    secrets, the tokens and the Lab's own state, .env and .lab, and what runs on this machine, MACHINE_FILES). `realpath` is
    os.path.realpath unless given:
    this is the one function here that reads the disk, and judge never calls it."""
    import os
    real = realpath or os.path.realpath

    def one(path):
        return str(real(str(path)))

    root = str(root)
    own = one(folder) if folder else None
    machine = [f"{root}/{name}" for name in MACHINE_FILES] if machine is None else machine
    return {"unit": own, "state": f"{own}/.state" if own else None, "secrets": f"{own}/secrets" if own else None,
            "plugins": [one(f"{root}/plugins/{name}") for name in products],
            "closure": [one(path) for path in closure if path], "seeder": one(f"{root}/seeder"),
            "machine": [one(path) for path in machine]}


def _roots(roots, resolve):
    """The roots once more through the resolver, so that both sides of a comparison are alike. state and secrets default to
    the unit's own."""
    roots = _dict(roots)

    def one(value):
        return resolve(value) if value else ""

    own = one(roots.get("unit"))
    return {"unit": own, "state": one(roots.get("state")) or (f"{own}/.state" if own else ""),
            "secrets": one(roots.get("secrets")) or (f"{own}/secrets" if own else ""),
            "plugins": [one(path) for path in _list(roots.get("plugins"))],
            "closure": [one(path) for path in _list(roots.get("closure"))], "seeder": one(roots.get("seeder")),
            "machine": [one(path) for path in _list(roots.get("machine"))]}


def _allowed(path, roots):
    """Whether a path is inside a root that a bind may reach: the unit's folder, a plugin folder, a folder of its closure."""
    return any(_inside(path, root) for root in [roots["unit"], *roots["plugins"], *roots["closure"]])


# ---------------------------------------------------------------------------------------------------------------------
# The text screen (T19): what no resolved JSON shows, read before Compose is ever asked
# ---------------------------------------------------------------------------------------------------------------------
#
# Compose reads four keys as it loads a file: `include`, `extends` (with `file`), `env_file` and `label_file`. Their content
# is merged into the resolved JSON before any rule sees it, so the text is the only place to refuse them. The screen is not a
# YAML parser and does not pretend to be one. It rests on one fact: a key that Compose reads as one of those four words has
# those letters in the file, once the escapes of a double-quoted scalar are replaced, or it hides them in a construct no
# honest file uses (an explicit `?` key, an alias used as a key, a `!!binary` tag, a directive, a file that is not UTF-8). So:
#   - every place that spells a word, followed by a colon, is a key, in a comment, a quote or a command as much as anywhere
#     else: the screen never skips text because it looks quoted, since a guess about quotes is what an author can bend;
#   - the constructs that hide a word make the file unscreenable, which blocks it;
#   - the text is read every way an escape or a fold can change it (`_readings`): which of them YAML makes depends on a quote
#     the screen does not track, so a word counts if any reading has it, and one that a fold glued to the line before is refused;
#   - what follows a key is read by `_Lex`, which starts where the value starts and so needs no guess about what came before.
# A false Blocked costs an author one line; a miss costs the person a file. Only whole-line comments are skipped.
#
# The files an `include` names are another question: the walk of a closure follows them and gives Compose only what it
# followed, so what it follows must be what Compose reads. `include_targets` answers a name or None, from the text as
# written, for the few spellings it can read exactly, and None for every spelling where YAML may read another name than the
# letters say.

_ESCAPE = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8}))")
_BREAK = re.compile("\r\n|[\r\u0085\u2028\u2029]")   # the line breaks of YAML 1.1 and of the parser Compose uses
_KEY = re.compile(r"""(?<![\w.$-])(["']?)(include|extends|env_file|label_file)\1[ \t]*:""")
_LOOSE = re.compile(r"""(["']?)(include|extends|env_file|label_file)\1[ \t]*:""")   # as _KEY, on a line that a fold has glued to another
_NOT_TEXT = re.compile("[\x00\ufffd\ud800-\udfff]")   # what a file that is not UTF-8 (UTF-16 is read by Compose) leaves behind
_DIRECTIVE = re.compile(r"^%TAG", re.M)   # a tag handle can spell !!binary without the word
_EXPLICIT = re.compile(r"^[ \t]*(?:[-?][ \t]+)*\?(?=[ \t]|$)|[\[{,][ \t]*\?(?=[ \t]|$)", re.M)   # `? key`: the key may be anywhere
_ALIAS_KEY = re.compile(r"(?:^|(?<=[\s\[{,]))\*[^\s,\[\]{}:*]+[ \t]*:(?=[ \t]|$)", re.M)   # `*name : value`
_TAG = re.compile(r"(?:^|(?<=[\s\[{,:?-]))![^\s,\[\]{}]*", re.M)
_SPACE = re.compile(r"[ \t]*")
_STOP_BLOCK = re.compile(r":(?=[ \t]|$)|(?<=[ \t])#")   # where a plain scalar ends in a block: a key, a comment
_STOP_FLOW = re.compile(r"[,\[\]{}]|:(?=[ \t,\[\]{}]|$)|(?<=[ \t])#")   # and in a flow collection
_DOUBLE = re.compile(r'[^"\\]*(?:\\.[^"\\]*)*')
_SINGLE = re.compile(r"[^']*(?:''[^']*)*")
_PROPERTY = re.compile(r"[&!*][^\s,\[\]{}]*")
_SIMPLE_PROPERTY = re.compile(r"&[\w-]+|!(?:![\w-]+|[\w-]*)")   # an anchor, `!`, `!tag`, `!!str`: the properties Compose reads as they read
_LEAD = re.compile(r"([ \t]*(?:[-?][ \t]+)*)(?:[&!][^\s,\[\]{}]*[ \t]+)*")
# What may stand before a key on its line: the start of it, or a brace, bracket or comma of a flow collection, then the
# dashes, question marks and colons of the entries that open on this line, then properties. A word that follows anything else
# (prose in a quoted scalar, a comment) is no key.
_KEY_PLACE = re.compile(r"(?:^|[{\[,])[ \t]*(?:[-?:][ \t]+)*(?:[&!][^\s,\[\]{}]*[ \t]+)*$")
_NAME = re.compile(r"[\w./+@-]+")   # a file name an include may give: no space, quote, backslash, colon, comma, bracket or brace
_ESCAPED = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v",
            "N": "\u0085", "_": "\u00a0", "L": "\u2028", "P": "\u2029"}   # the other escapes stand for themselves
_REMOTE = ("git@", "github.com/", "oci://", "git://")
SEED_FOLDER = re.compile(r"(?:^|/)(?:addons|playgrounds)/[^/]+/seed/")   # a unit's own seed data: Compose never reads it
FLOW_LINES = 200   # the lines a collection or a quoted scalar may take before the screen gives up on reading it
SCREEN_CHARS = 1 << 18   # the characters the screen reads for the values of all the keys of one file, whatever the file does


def _char(found):
    """The character an escape stands for; a space for a code that is none."""
    try:
        return chr(int(next(group for group in found.groups() if group), 16))
    except (ValueError, OverflowError):
        return " "


def _plain(text):
    """The text as written, as YAML breaks its lines: `\\n` for every line break, and no byte order mark at its start (it is
    not part of the first line)."""
    plain = _BREAK.sub("\n", text)
    return plain[1:] if plain.startswith("\ufeff") else plain


def _decoded(plain, fold=True):
    """The lines of a text (`_plain`) with the escapes of a double-quoted scalar replaced, and for each whether that (or a
    fold) made it, so that it is not a line of the text as written: ([lines], [made]). With `fold`, a backslash, a line break
    and the indentation of the next line are one line: in a double-quoted scalar that is a line continued, anywhere else it is
    a backslash at the end of a line and the next line stands alone. So a word can be in a line that this joins to the one
    before it, as well as in one an escape spells (`"env_\\x66ile"`)."""
    if "\\" not in plain:
        lines = plain.split("\n")
        return lines, [False] * len(lines)
    groups = []   # [pieces, whether a fold made it, whether the backslash that ends them can fold the next line]
    for line in plain.split("\n"):
        if fold and groups and groups[-1][2]:
            joined = line.lstrip(" \t")
            group = groups[-1]
            group[0][-1] = group[0][-1][:-1]
            group[0].append(joined)
            group[1], group[2] = True, joined.endswith("\\")
        else:
            groups.append([[line], False, fold and line.endswith("\\")])
    lines, made = [], []
    for pieces, folded, _ in groups:
        text = "".join(pieces)
        spelled = _ESCAPE.sub(_char, text)
        for one in spelled.split("\n"):
            lines.append(one)
            made.append(folded or spelled != text)
    return lines, made


def unescaped(text):
    """The text as the screen reads its letters: every line break of YAML is `\\n`, the escapes of a double-quoted scalar are
    replaced and its folded lines joined, so that "env_\\x66ile" and "env_\\<newline>file" are the key they spell. What a
    caller that looks for a word in a compose file's text (a key, a name) reads it through, to find what Compose would; it
    reads the text as written too (a word in a line that this joins to the one before is in the text as written)."""
    return "\n".join(_decoded(_plain(text))[0])


def _readings(text):
    """The ways the screen reads a text, [(lines, made)] without the repeats: with its escapes replaced and its folds joined,
    as written, and with its escapes replaced alone. A word is in the text if it is in any of them: an escape or a fold can
    spell a key (`"inclu\\u0064e"`) and a fold that YAML does not make (a backslash at the end of a plain scalar's line)
    can hide a key that stands on the line after it, or an escaped one (`"env_\\x66ile"`) after that."""
    plain = _plain(text)
    lines = plain.split("\n")
    if "\\" not in plain:   # nothing to replace and nothing to fold
        return [(lines, [False] * len(lines))]
    found = [_decoded(plain), (lines, [False] * len(lines)), _decoded(plain, fold=False)]
    return [one for number, one in enumerate(found) if all(one[0] != other[0] for other in found[:number])]


def _blind(text):
    """Whether a text holds a construct that can spell a key without its letters: a tag directive, an explicit key (`? name`,
    in a block or a flow), an alias used as a key, or a tag that decodes its scalar (`!!binary`, spelled any way). None of
    them is in a unit written by hand."""
    if _DIRECTIVE.search(text) or _EXPLICIT.search(text) or _ALIAS_KEY.search(text):
        return True
    return any("<" in tag or "%" in tag or "bin" in tag.lower() for tag in (found.group() for found in _TAG.finditer(text)))


class _Lex:
    """The scalars of the text that follows a key, in the order YAML meets them, read from the place where the value starts:
    `tokens` are ("s", text) for a scalar, ("k", text) for one a colon follows, ("a", "") for an alias, ("|", "") where an
    entry ends. It reads a line at a time: a quoted scalar or a flow collection that is open at the end of a line goes on in
    the next (`quote`, `depth`). A quote opens a scalar only where a token starts, and a plain scalar takes everything up to
    its end, so a quote in the middle of a word or a sentence is the character itself, as in YAML.

    What ends a plain scalar depends on where the key is, which the text after it does not say: in a block (a mapping, a
    list) `,[]{}` are characters of the scalar, in a flow collection (`flow`) they end it. So the value is read both ways,
    and a file is held to what either reading finds. `bad`: a block scalar, which the screen does not read. `odd`: a spelling
    that YAML reads other than the letters say and that this does not follow: a colon with no key before it (`:,` is a plain
    scalar in a block and an empty key in a flow), a double-quoted scalar with an escape in it (the letters are not the name),
    a property other than an anchor or a plain tag. `end`: the index of the first line the value did not take (`_value`)."""

    def __init__(self, flow):
        self.tokens, self.depth, self.quote, self.pieces = [], 0, "", []
        self.flow, self.bad, self.json, self.last, self.cost = flow, False, False, None, 0
        self.odd, self.end = False, 0

    def alias(self):
        return any(kind == "a" for kind, _ in self.tokens)

    def cut(self):
        self.tokens.append(("|", ""))
        self.last = None

    def scalar(self, text):
        self.tokens.append(("s", text))
        self.last = len(self.tokens) - 1

    def feed(self, line):
        i, n = 0, len(line)
        while i < n:
            if self.quote:
                i = self.quoted(line, i)
                continue
            i = _SPACE.match(line, i).end()
            if i >= n:
                break
            char, json, self.json = line[i], self.json, False
            if char == "#" and (i == 0 or line[i - 1] in " \t"):
                break
            if char in "[{":
                self.depth, i = self.depth + 1, i + 1
                self.cut()
            elif char in "]}":
                self.depth, i = max(self.depth - 1, 0), i + 1
                self.cut()
            elif char == ",":
                i += 1
                self.cut()
            elif char == ":" and (i + 1 >= n or line[i + 1] in " \t,[]{}" or json):
                if self.last is not None:
                    self.tokens[self.last] = ("k", self.tokens[self.last][1])
                else:
                    self.odd = True
                self.last, i = None, i + 1
            elif char in "-?" and (i + 1 >= n or line[i + 1] in " \t"):
                i += 1
                self.cut()
            elif char in "\"'":
                self.quote, self.pieces, i = char, [], i + 1
            elif char in "|>":
                self.bad = True
                break
            elif char in "&!*":
                found = _PROPERTY.match(line, i)
                if char == "*":
                    self.tokens.append(("a", ""))
                    self.last = None
                elif not _SIMPLE_PROPERTY.fullmatch(found.group()):
                    self.odd = True
                i = found.end()
            else:
                i = self.plain(line, i)

    def plain(self, line, i):
        found = (_STOP_FLOW if self.flow or self.depth else _STOP_BLOCK).search(line, i)
        end = max(found.start() if found else len(line), i + 1)
        self.scalar(line[i:end].strip(" \t"))
        return end

    def quoted(self, line, i):
        end = (_DOUBLE if self.quote == '"' else _SINGLE).match(line, i).end()
        if end >= len(line) or line[end] != self.quote:   # it goes on in the next line
            self.pieces.append(line[i:])
            return len(line)
        self.pieces.append(line[i:end])
        if len(self.pieces) > 1:   # a folded line: the white space round the break goes, the rest of the scalar stays
            first, *middle, last = self.pieces
            text = " ".join([first.rstrip(" \t"), *(piece.strip(" \t") for piece in middle), last.lstrip(" \t")])
        else:
            text = self.pieces[0]
        if self.quote == '"':
            self.odd = self.odd or "\\" in text
            text = re.sub(r"\\(.)", lambda found: _ESCAPED.get(found.group(1), found.group(1)), text, flags=re.S)
        else:
            text = text.replace("''", "'")
        self.quote, self.json = "", True
        self.scalar(text)
        return end + 1

    def whole(self):
        return not (self.depth or self.quote or self.bad)

    def paths(self):
        """The scalars that are file names: those of an entry `path:` and those named by no key."""
        found, key = [], None
        for kind, text in self.tokens:
            if kind == "|":
                key = None
            elif kind == "k":
                key = text
            elif kind == "s" and key in (None, "path"):
                found.append(text)
        return found


def _bad_place(value, folder, files):
    """Whether a path or an address names a place that the unit has no business reading: absolute, home, remote, a variable
    or, for an include, one that leaves the project (`files` False) and, for the files a service reads, one that leaves the
    unit's own folder (`files` True)."""
    value = value.strip()
    if not value:
        return False
    if "$" in value or "\0" in value or value.startswith(("/", "~", "\\")) or re.match(r"[A-Za-z]:[\\/]", value) \
            or "://" in value or value.startswith(_REMOTE):
        return True
    target = posixpath.normpath(posixpath.join(folder or ".", value))
    if files and folder:
        return not _inside(target, folder)
    return target == ".." or target.startswith("../")


def _value(lines, number, start, end, spent, flow):
    """The reading of the value of the key found at columns `start` to `end` of line `number`, a `_Lex` (see it for `flow`),
    and whether it is whole: the rest of its line, the lines of a collection or a quoted scalar it opens (wherever they are
    indented, up to FLOW_LINES), and the lines under it (indented deeper, or a list at the key's own indent). `spent` counts the
    characters read for all the keys of the file: a value that makes the screen read more than SCREEN_CHARS is not whole."""
    line, lex = lines[number], _Lex(flow)
    lex.end = number + 1
    lead = _LEAD.fullmatch(line[:start]) if start <= 1024 else None   # the key starts its line or its list item
    column = len(lead.group(1)) if lead else start   # its mapping starts at its properties, if it has any: &anchor !tag key:
    first = line[end:end + SCREEN_CHARS + 1]
    spent[0] += len(first)
    lex.feed(first)
    index = number + 1
    while index < len(lines) and spent[0] <= SCREEN_CHARS:
        follow = lines[index]
        text = follow.lstrip(" \t")
        if lex.depth or lex.quote:
            if index - number > FLOW_LINES:
                return lex, False
        elif not text:
            index += 1
            lex.end = index
            continue
        else:
            deeper = len(follow) - len(text)
            if not (deeper > column or deeper == column and lead and re.match(r"-(?:[ \t]|$)", text)):
                break
        follow = follow[:SCREEN_CHARS + 1]
        spent[0] += len(follow)
        lex.feed(follow)
        index += 1
        lex.end = index
    return lex, lex.whole() and spent[0] <= SCREEN_CHARS


def _scan(text, file, follow):
    """The T19 lines that hold, and the project-relative paths of the files named by `env_file` and `label_file` that stay in
    the unit's folder: ({lines}, [paths]). See prescreen. The text is read every way `_readings` says, and a file is held to
    each: on a line that an escape or a fold made, a word that is no key where it stands (a fold glued it to the line before)
    is refused, since nothing says where its value is."""
    include, files = RULES["T19"]["lines"]
    text = text if isinstance(text, str) else ""
    name = str(file or "")
    if len(text.encode("utf-8", "replace")) > MAX_FILE:
        return {files}, []
    if SEED_FOLDER.search(name) or not name.endswith((".yml", ".yaml")):
        return set(), []
    if _NOT_TEXT.search(text):
        return {files}, []
    found, paths, spent, folder = set(), [], [0], posixpath.dirname(name)
    for read, made in _readings(text):
        if _blind("\n".join(read)):
            found.add(files)
        lines = ["" if line.lstrip(" \t").startswith("#") else line for line in read]
        for number, line in enumerate(lines):
            at = {one.start() for one in _KEY.finditer(line)} if made[number] else ()
            for match in (_LOOSE if made[number] else _KEY).finditer(line):
                key = match.group(2)
                wanted = include if key == "include" else files
                if wanted in found:
                    continue
                if made[number] and match.start() not in at:   # a fold glued it to the line before: where is its value?
                    found.add(wanted)
                    continue
                if key in ("env_file", "label_file") and not follow:
                    found.add(files)
                    continue
                readings = [_value(lines, number, match.start(), match.end(), spent, flow) for flow in (False, True)]
                if spent[0] > SCREEN_CHARS:
                    found.add(files)
                    return found, []
                names = [value for lex, _ in readings for kind, value in lex.tokens if kind in ("s", "k")]
                if not all(whole for _, whole in readings) or any(lex.alias() for lex, _ in readings) \
                        or key == "extends" and any(("k", "file") in lex.tokens for lex, _ in readings):
                    found.add(wanted)
                elif key == "include":
                    if any(_bad_place(value, folder, False) for value in names):
                        found.add(include)
                elif key != "extends":
                    if readings[0][0].tokens != readings[1][0].tokens \
                            or any(_bad_place(value, folder, True) for value in names):
                        found.add(files)
                    else:
                        paths += [posixpath.normpath(posixpath.join(folder or ".", value))
                                  for value in readings[0][0].paths() if value.strip()]
    return found, sorted(set(paths))


def prescreen(text, file, follow=False):
    """The reasons (T19) for what a compose file's text gives away, before Compose reads it: an `include` that is absolute,
    `~`, remote, a variable or leaves the project; an `extends` of another file; an `env_file` or a `label_file` (any, unless
    `follow`); a file over 1 MB; a construct the screen cannot read (an alias in those places, a key spelled without its
    letters, a file that is not UTF-8, a file that takes too long to read). `file` is the project-relative path; seed data,
    which Compose never reads, is only held to the size. [] when clean.

    A file that an `env_file` or a `label_file` names is read by Compose into the environment of the service, `${NAME}`
    included, and this screen cannot see into it. So these two keys are refused outright, until the caller says it looks:
    with `follow=True` the keys are held to the unit's folder (an absolute, `~`, remote or variable name, or one that leaves
    the folder, is refused), and the caller must then put the text of every file `env_files()` names through `reads()` (T20)
    and read each at its real path, which must stay in the folder (a link in a folder catalog is the one thing no text shows)."""
    include, files = RULES["T19"]["lines"]
    found = _scan(text, file, follow)[0]
    return [line for line in (include, files) if line in found]


def env_files(text, file):
    """The project-relative paths of the files that the `env_file` and `label_file` of a compose file name and that stay in
    the unit's folder, sorted: what `prescreen(..., follow=True)` asks its caller to read. [] for a file that is not screened."""
    return _scan(text, file, True)[1]


def _block_list(lines, number, match, readings):
    """Whether the value of the `include` key at `match` in line `number` is spelled as YAML reads what the lexer reads: the
    key's own line closes what it opens, and either holds the whole value or leaves it to a block list whose rows are all
    entries that close what they open. A row that goes on with the row before it (YAML joins them into one name) is neither:
    only the row under one that ends in a key (`path:`) is indented deeper than the row before it. The item has no key but
    `path`, no alias and no spelling the lexer does not follow (`_Lex.odd`), and the two readings of the value (as a block,
    as a flow) are one."""
    (block, whole_block), (flowing, whole_flow) = readings
    if not (whole_block and whole_flow) or block.tokens != flowing.tokens or block.alias() or block.odd or flowing.odd \
            or any(kind == "k" and value != "path" for kind, value in block.tokens):
        return False
    probe = _Lex(False)
    probe.feed(lines[number][match.end():])
    if not probe.whole() or probe.odd:
        return False
    rows = [row for row in lines[number + 1:max(block.end, flowing.end)] if row.strip()]
    if any(kind != "|" for kind, _ in probe.tokens):   # `include: [a.yml]`: no row goes on after it
        return not rows
    before = None   # (indent, whether it ends in a key) of the row before
    for row in rows:
        entry, indent = row.lstrip(" \t"), len(row) - len(row.lstrip(" \t"))
        one = _Lex(False)
        one.feed(row)
        if not re.match(r"-(?:[ \t]|$)", entry) or not one.whole() or one.odd:
            return False
        if before is not None and indent > before[0] and not before[1]:
            return False
        before = (indent, bool(one.tokens) and one.tokens[-1][0] == "k")
    return True


def include_targets(text, file):
    """The project files that a compose file's text includes, as the screen reads the `include` keys it finds: the
    project-relative path of each plain file name, in a block list or a flow one, alone or after `path:`, in the order
    they are written and once each. [] for a text that includes nothing. None when the screen cannot say for all of them,
    and then the file is not followed and not given to Compose for what it includes. The walk of a closure follows exactly
    these files, so the answer is a name or None, never a guess about a name: a name is read from the text as written, only
    ever of the letters `[A-Za-z0-9_./+@-]` (a space, a quote, a backslash, a colon, a comma, a bracket, an escape or a fold
    could make YAML read another name than the letters say) and a value is read from a line that closes what it opens and a
    block list of entries that each close what they open. A name that is absolute, `~`, remote or leaves the project is
    None, as is a file that is not YAML or not text, over 1 MB, one that can spell a key without its letters, a value read
    two ways (the block reading and the flow one differ), a block scalar, an alias, a key of an item other than `path`, and
    an `include` that an escape or a fold spells. An `include:` that no key can be (prose in a quoted scalar, a comment) is
    no key. `file` is the project-relative path of the text: a name is relative to its folder.

    T19 (`prescreen`) is not this: it refuses what is outside the catalog and reads a long form with `required` as clean,
    while the walk cannot follow it and the unit is held. An author's check that wants to say so asks for None here."""
    text = text if isinstance(text, str) else ""
    name = str(file or "")
    if len(text.encode("utf-8", "replace")) > MAX_FILE or not name.endswith((".yml", ".yaml")) or _NOT_TEXT.search(text):
        return None
    reads = _readings(text)
    if any(_blind("\n".join(read)) for read, _ in reads):
        return None
    for read, made in reads:
        for line, said in zip(read, made):   # a key that only an escape or a fold spells: nothing says what it names
            if said and not line.lstrip(" \t").startswith("#") and any(one.group(2) == "include" for one in _LOOSE.finditer(line)):
                return None
    lines = ["" if line.lstrip(" \t").startswith("#") else line for line in _plain(text).split("\n")]
    found, spent, folder = [], [0], posixpath.dirname(name)
    for number, line in enumerate(lines):
        for match in _KEY.finditer(line):
            if match.group(2) != "include" or not _KEY_PLACE.search(line[:match.start()]):
                continue
            readings = [_value(lines, number, match.start(), match.end(), spent, flow) for flow in (False, True)]
            if not _block_list(lines, number, match, readings):
                return None
            for value in readings[0][0].paths():
                if value and (not _NAME.fullmatch(value) or _bad_place(value, folder, False)):
                    return None
                target = posixpath.normpath(posixpath.join(folder or ".", value)) if value else None
                if target and target not in found:
                    found.append(target)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# What a service takes: one vocabulary for a person and for an author
# ---------------------------------------------------------------------------------------------------------------------

def _volumes(service):
    """The mounts of a service as mappings. The resolved JSON gives mappings; a text (src:dst:mode) is read as Compose does."""
    found = []
    for volume in _list(_dict(service).get("volumes")):
        if isinstance(volume, dict):
            found.append(volume)
        elif isinstance(volume, str):
            source, _, rest = volume.partition(":")
            modes = rest.rpartition(":")[2].split(",") if rest.count(":") else []
            found.append({"type": "bind" if source.startswith(("/", ".", "~")) else "volume", "source": source,
                          "target": rest.split(":")[0], "read_only": "ro" in modes})
    return found


def _is_socket(source, resolve):
    """A Docker socket: a source whose name ends .sock, as written or once its links are followed."""
    return any(posixpath.basename(str(path).rstrip("/")).endswith(".sock") for path in (source, resolve(source)) if path)


def _namespace(value):
    """Whether a namespace option shares one with this machine or with another container: host, or container:<id>."""
    return isinstance(value, str) and (value == "host" or value.startswith("container:"))


def _joins(value, members):
    """Whether a namespace option joins that of a service the unit does not have: service:<name> with a name outside
    `members` (its own services and those it brings). None for `members` says nobody knows, and nothing is joined."""
    return members is not None and isinstance(value, str) and value.startswith("service:") and value[8:] not in members


def _isolation(service):
    """The security options that are not the one harmless entry."""
    return [option for option in _list(service.get("security_opt"))
            if str(option).strip().lower() not in ("no-new-privileges", "no-new-privileges:true", "no-new-privileges=true")]


def _hits(service, members=None):
    """The class H findings of a service that its keys give away, T03 to T09: [(code, the variant of the line)]. A pid or an
    ipc that joins a service of the platform shows the processes and the memory of a container that holds its secrets, so it
    counts as a namespace of this machine; `members` names the services it may join."""
    found = []
    build = _dict(service.get("build"))
    if _true(service.get("privileged")) or _true(build.get("privileged")):
        found.append(("T03", 0))
    if service.get("use_api_socket") not in (None, False):
        found.append(("T04", 0))
    if service.get("provider") is not None:
        found.append(("T05", 0))
    variants = [number for number, key in enumerate(("network_mode", "pid", "ipc"))
                if _namespace(service.get(key)) or number and _joins(service.get(key), members)]
    if variants:
        found.append(("T06", variants[0]))
    elif any(_namespace(service.get(key)) for key in ("uts", "userns_mode", "cgroup")) or service.get("cgroup_parent"):
        found.append(("T06", 3))
    if service.get("cap_add"):
        found.append(("T07", 0))
    reserved = _dict(_dict(_dict(service.get("deploy")).get("resources")).get("reservations")).get("devices")
    if service.get("devices") or service.get("device_cgroup_rules") or service.get("gpus") or service.get("runtime") \
            or reserved:
        found.append(("T08", 0))
    if _isolation(service) or service.get("sysctls"):
        found.append(("T09", 0))
    return found


def elevations(service, roots=None, realpath=None, home=None, members=None):
    """What a class H service takes, in the words of 4.3, shared with `make check` so that the line a person reads and the
    line an author reads are one text: "runs privileged", "reaches Docker", "mounts the Docker socket", "shares the network of
    this machine", "adds the capabilities X", "reaches the devices of this machine", "mounts {path} of this machine". A bind
    is described only with `roots` (build_roots()): without them nothing says whether it leaves the unit's reach. `home`
    shortens a path under it to ~. `members`: the services the unit may join the processes and memory of (_hits)."""
    service = _dict(service)
    codes, what = dict(_hits(service, members)), []
    for code, words in (("T03", "runs privileged"), ("T04", "reaches Docker"), ("T05", "runs a program on this machine")):
        if code in codes:
            what.append(words)
    if service.get("network_mode") == "host":
        what.append("shares the network of this machine")
    elif _namespace(service.get("network_mode")):
        what.append("shares the network of another container")
    for key, words in (("pid", "shares the processes of this machine"), ("ipc", "shares the memory of this machine"),
                       ("uts", "shares the names of this machine"), ("userns_mode", "shares the users of this machine"),
                       ("cgroup", "shares the resource groups of this machine")):
        if _namespace(service.get(key)):
            what.append(words)
        elif key in ("pid", "ipc") and _joins(service.get(key), members):
            what.append(words.replace("this machine", "another container"))
    if service.get("cgroup_parent"):
        what.append("runs in a resource group of this machine")
    if "T07" in codes:
        what.append("adds the capabilities " + ", ".join(str(cap) for cap in _list(service.get("cap_add"))))
    if "T08" in codes:
        what.append("reaches the devices of this machine")
    if "T09" in codes:
        what.append("lowers its isolation")
    resolve = _resolver(realpath)
    home = resolve(home).rstrip("/") if home else ""
    reach = _roots(roots, resolve) if roots else None
    for volume in _volumes(service):
        source = volume.get("source")
        if volume.get("type") == "npipe" or volume.get("type") == "bind" and _is_socket(source, resolve):
            what.append("mounts the Docker socket")
        elif volume.get("type") == "bind" and source and reach and not _allowed(resolve(source), reach):
            path = resolve(source) or str(source)
            what.append(f"mounts {'~' + path[len(home):] if home and _inside(path, home) else path} of this machine")
    return list(dict.fromkeys(what))


# ---------------------------------------------------------------------------------------------------------------------
# What a unit adds to a service it does not own, and what it reads
# ---------------------------------------------------------------------------------------------------------------------

def _added(value, theirs):
    """Whether a definition holds something that none of these, the ones merged into it, holds: key by key in a mapping,
    item by item in a list."""
    if isinstance(value, dict):
        return any(_added(v, [t[k] for t in theirs if isinstance(t, dict) and k in t]) for k, v in value.items())
    if isinstance(value, list):
        return any(not any(isinstance(t, list) and item in t for t in theirs) for item in value)
    return value not in theirs


def _delta(value, theirs):
    """What a definition adds to the ones merged into it, in the shape it was written."""
    if isinstance(value, dict):
        return {k: _delta(v, [t[k] for t in theirs if isinstance(t, dict) and k in t])
                for k, v in value.items() if _added(v, [t[k] for t in theirs if isinstance(t, dict) and k in t])}
    if isinstance(value, list):
        return [item for item in value if not any(isinstance(t, list) and item in t for t in theirs)]
    return value


def overlays(raw, core_names, included=()):
    """The `overlaid` of judge: {service: {key: what the unit's own file adds}} for the services of `raw` (the unit as
    written, compose.raw) that the core (`core_names`) or an included unit (`included`: their raw configurations) defines.
    The `networks: default` that Compose adds to every service it reads alone is no setting of the unit."""
    mine = _dict(_dict(raw).get("services"))
    theirs = [_dict(_dict(other).get("services")) for other in included]
    found = {}
    for name, definition in mine.items():
        sources = [t[name] for t in theirs if isinstance(t.get(name), dict)]
        if not isinstance(definition, dict) or name not in core_names and not sources:
            continue
        added = {}
        for key, value in definition.items():
            if key == "networks" and value in ({"default": None}, ["default"]):
                continue
            above = [source[key] for source in sources if key in source]
            if _added(value, above):
                added[key] = _delta(value, above)
        if added:
            found[name] = added
    return found


# What a text writes as the empty value of an environment variable, a line at a time: `NAME: ""`, `NAME: ''` and `- NAME=`,
# with or without quotes round the name. Compose gives the container that empty string and reads nothing from the shell;
# `- NAME` and `NAME:` have no value, so the shell and the .env files give it one.
_EMPTY = re.compile(r"""[ \t]*(?:-[ \t]+)?(["']?)([A-Za-z_][\w.-]*)(?:\1[ \t]*:[ \t]*(?:""|'')|=\1)[ \t]*(?:(?<=[ \t])#.*)?""")
_WORD = re.compile(r"[\w.-]+")


def _written_empty(texts):
    """The names that the texts write as an explicit empty value and nowhere else: every line of them that has the name in it
    is such an entry. A name that is also spelled any other way (a bare list item, a flow list, a line this does not read) is
    not here, and neither is one the texts do not say, so that a doubt counts as a read. Read through `unescaped`, because
    "NAM\\x45" is NAME."""
    empty, other = set(), set()
    for text in texts:
        for line in unescaped(text).split("\n"):
            if line.lstrip(" \t").startswith("#"):
                continue
            found = _EMPTY.fullmatch(line)
            if found:
                empty.add(found.group(2))
            else:
                other.update(_WORD.findall(line))
    return empty - other


def reads(texts, raw=None, merged=()):
    """The settings a unit reads from this machine, for `unit["references"]`: the names its texts read with ${NAME}, and the
    environment names its services pass through without a value (`environment: [NAME]` and `NAME:` read NAME from the shell
    and the .env files as surely as ${NAME} does). A name of the unit's own settings costs nothing. Sorted.

    compose.raw gives `- NAME`, `NAME=` and `NAME: ""` the same empty value, and only the last two are a value of the unit's
    own, which reads nothing. So a name with an empty value counts as a read unless the texts write it, wherever they
    mention it, as an explicit empty value (_written_empty): `- NAME` is a read whether Compose gives None or "", and a
    name that no text says is a read too. `merged` are the texts of the files that Compose merged into `raw` besides the
    unit's own (the units it includes): they say how a name is written and are not read for ${NAME}, which is those
    units' own, judged on their own and through the closure (T25)."""
    from ..docker import compose
    names = {key for text in texts for key, _, _ in compose.references(text)}
    written = None
    for service in _dict(_dict(raw).get("services")).values():
        for key, value in _dict(_dict(service).get("environment")).items():
            if value is None:
                names.add(key)
            elif value == "":
                written = _written_empty([*texts, *merged]) if written is None else written
                if key not in written:
                    names.add(key)
    return sorted(names)


# What the seeder itself gives the seed files of an environment, with no value of the seed service's own: seeder/lab_seeder/common.py
# PROVIDED, which a test keeps equal
PROVIDED = ("ENV_ID", "ENV_NAME")
SEED_VARIABLE = re.compile(r"\$(\$)?\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")   # the seeder's own (seeder/lab_seeder/common.py)


def seed_reads(texts):
    """The settings the seed files of a unit read, for `unit["seed_references"]`: ${NAME} and ${NAME:-default} as the seeder
    replaces them (`$${NAME}` is the text itself, and `$name` or `$ref` in a definition is no variable), but for the names the
    seeder gives to a file itself (PROVIDED), which are no setting of this machine. Sorted."""
    return sorted({found.group(2) for text in texts for found in SEED_VARIABLE.finditer(text)
                   if not found.group(1) and found.group(2) not in PROVIDED})


def local_address(url):
    """Whether an address is on this machine: http or https, no user, on localhost, 127.0.0.1, [::1] or host.docker.internal.
    A spelling that a browser and a parser read differently (a backslash, a space, a user) is not."""
    return isinstance(url, str) and bool(re.fullmatch(
        r"https?://(?:" + "|".join(re.escape(host) for host in LOCAL_HOSTS) + r")(?::\d{1,5})?(?:[/?#][^\s\\]*)?", url,
        re.I))


def _source(build):
    """Where a build comes from: "local", or the address of its repository without scheme, user, .git or ref."""
    context = build if isinstance(build, str) else str(_dict(build).get("context") or "")
    if not context or posixpath.isabs(context):
        return "local"
    address = context.split("#")[0]
    scheme = re.match(r"[a-z][a-z0-9+.-]*://", address, re.I)
    address = address[scheme.end():] if scheme else address
    if "@" in address.split("/")[0]:
        address = address.rsplit("@", 1)[-1] if scheme else address.split("@", 1)[1]
    if not scheme and ":" in address.split("/")[0]:   # git@host:owner/repo
        address = address.replace(":", "/", 1)
    return re.sub(r"\.git$", "", address)


def _pinned(context):
    """Whether a remote build context is a git repository at a commit: a git address, then # and 40 hex digits."""
    address, _, ref = context.partition("#")
    git = address.endswith(".git") or address.startswith(("git@", "git://", "ssh://", "github.com/"))
    return bool(git and PINNED.fullmatch(ref))


def build_extras(build, seeder=False):
    """The keys of a build that T18 does not accept, sorted, as the text Compose printed them: every key but BUILD_KEYS, and
    `additional_contexts` unless the build is the Lab's seeder (`seeder`). The one list that the stage of the CI that builds
    refuses by (BD02). A build given as a text is a context and has none; one that is neither text nor a mapping is not a
    build the Lab can read, and has `build` itself as the key it does not accept."""
    if isinstance(build, str):
        return []
    if not isinstance(build, dict):
        return ["build"]
    allowed = BUILD_KEYS + (SEEDER_BUILD_KEYS if seeder else ())
    return sorted(str(key) for key in build if key not in allowed)


# ---------------------------------------------------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------------------------------------------------

def empty_surface():
    return {"images": [], "builds": [], "ports": [], "core": [], "reads": [], "plugins": [], "seed": None, "shell": None,
            "uses_machine": [], "needs": []}


def _verdict(reasons=(), notes=(), surface=None, codes=(), name=""):
    return {"state": "blocked" if reasons else "ok", "reasons": list(dict.fromkeys(reasons)),
            "notes": list(dict.fromkeys(notes)), "surface": surface if surface is not None else empty_surface(),
            "codes": list(dict.fromkeys(codes)), "name": _clean(name, NAME_CUT + 20)}


def add_reason(verdict, code, line):
    """The verdict with one more reason, for the rules that need what judge does not know: T21 (a clash, resolve.collisions)
    and T24 (the audience). Blocked from then on; a line already there is not said twice."""
    return dict(verdict, state="blocked", reasons=list(dict.fromkeys([*verdict["reasons"], line])),
                codes=list(dict.fromkeys([*verdict["codes"], code])))


def sum_surfaces(surfaces):
    """The surface of several units as one: the preview of a catalog, the start of an item and the units it brings. Lists
    are united in a stable order; the seed is custom when any is, the shell the first there is."""
    total, seeds = empty_surface(), [s["seed"] for s in surfaces if s.get("seed")]
    for surface in surfaces:
        for key in ("images", "reads", "plugins", "needs", "ports"):
            total[key] = sorted(set(total[key]) | set(surface.get(key) or ()))
        total["builds"] += [b for b in surface.get("builds") or () if b not in total["builds"]]
        total["uses_machine"] += [u for u in surface.get("uses_machine") or () if u not in total["uses_machine"]]
        for entry in surface.get("core") or ():
            seen = next((c for c in total["core"] if c["service"] == entry["service"]), None)
            if seen:
                seen["keys"] = sorted(set(seen["keys"]) | set(entry["keys"]))
            else:
                total["core"].append(dict(entry, keys=list(entry["keys"])))
        total["shell"] = total["shell"] or surface.get("shell")
    total["seed"] = "custom" if "custom" in seeds else ("official" if seeds else None)
    return total


def combine(verdicts):
    """The verdict of a closure (T25): the first verdict is the unit's own, the others are those of what it brings. A
    unit that brings a blocked unit is blocked: "Needs {name}, which is blocked" (the name is the verdict's own). Notes stay
    with the unit they are about. The surface is the sum of all of them, the surface of a start."""
    items = [v for v in verdicts if isinstance(v, dict)]
    if not items:
        return _verdict()
    own, codes, reasons = items[0], list(items[0].get("codes") or ()), list(items[0].get("reasons") or ())
    for other in items[1:]:
        if other.get("state") == "blocked":
            reasons.append(line("T25", name=other.get("name") or "a unit"))
            codes.append("T25")
    return _verdict(reasons, own.get("notes") or (), sum_surfaces([v.get("surface") or empty_surface() for v in items]),
                    codes, own.get("name", ""))


# ---------------------------------------------------------------------------------------------------------------------
# The restrictions that do not block
# ---------------------------------------------------------------------------------------------------------------------

def _seed_environment(service):
    """The names a service sets in its environment, whether Compose gave a mapping or a list of NAME=value."""
    environment = service.get("environment")
    return [str(name) for name in environment] if isinstance(environment, dict) else \
        [str(item).partition("=")[0] for item in _list(environment)]


def _runs_seeder(service, seeder, base, images, resolve):
    """Whether a seed service runs the Lab's seeder and nothing else: built from the seeder's own folder with its own
    Dockerfile and nothing more (or, with no build, an image of the repositories the core file names); no entrypoint, command,
    working folder, health check, config or secret that would run other code in it; and no mount but /seed and /state, which
    hold data. The environment is the author's, except the variables with which a folder it mounts could still become code:
    the interpreter's (PYTHON...), the loader's (LD_..., DYLD_...), the search path and the home folder."""
    if any(service.get(key) is not None for key in ("entrypoint", "command", "working_dir", "healthcheck")) \
            or service.get("configs") or service.get("secrets"):
        return False
    if any(volume.get("target") not in SEED_MOUNTS for volume in _volumes(service)) \
            or any(LOADER_VARIABLE.match(name) for name in _seed_environment(service)):
        return False
    build = service.get("build")
    if build is None:
        return _repository(service.get("image")) in images
    build = {"context": build} if isinstance(build, str) else build
    context = build.get("context") if isinstance(build, dict) else None
    return bool(seeder) and isinstance(context, str) and resolve(context, base) == seeder \
        and set(build) <= {"context", "dockerfile"} and build.get("dockerfile") in (None, "Dockerfile")


def seeder_ok(unit_config, roots, engine_seeder_images, realpath=None):
    """N01: whether every seed service (the label io.gravitee.role: seed) runs the Lab's seeder, and only it (_runs_seeder).
    A seed built from any other folder is the catalog's code, whatever it calls its image, and so is one that runs the
    seeder's folder with code of its own; neither holds a token. True with no seed service."""
    resolve = _resolver(realpath)
    seeder, base = resolve(_dict(roots).get("seeder")), _dict(roots).get("unit")
    images = {_repository(image) for image in engine_seeder_images or () if isinstance(image, str)} \
        if isinstance(engine_seeder_images, (list, tuple, set, frozenset)) else set()
    return all(_runs_seeder(_dict(service), seeder, base, images, resolve)
               for service in _dict(_dict(unit_config).get("services")).values()
               if _dict(_dict(service).get("labels")).get("io.gravitee.role") == "seed")


ALLOWANCE = True   # milestone M2: the allowance a person gives a catalog (host: true) is honored


def host_ok(listed, entry=None):
    """Whether the class H items of a catalog may use this machine: it is listed, or, once M2 ships, the person allowed
    it (`host: true` in its entry of catalogs.json, exactly). Until then only listing counts, and a class H item of any other
    catalog stays Blocked: the absence of the feature fails safe. The one place that decides `context["host_ok"]`."""
    return bool(listed) or ALLOWANCE and isinstance(entry, dict) and entry.get("host") is True


def declared_ok(context):
    """N04: whether the units of this catalog may declare `x-gravitee.gateway`: the catalog is listed, allowed (host: true) or
    the person's own. Otherwise the declaration is cleared before any reader sees it, so that no Cloud gateway token is ever
    handed to a catalog that was not trusted with one."""
    context = _dict(context)
    return bool(context.get("host_ok") or context.get("local"))


def _terminal_services(value):
    """The services an `x-gravitee.terminal` names, or None when it names none that the policy can read."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _terminal_services(value.get("service"))
    if isinstance(value, list) and value:
        names = [(_terminal_services(item) or [None])[0] for item in value]
        return None if None in names else names
    return None


def terminal_ok(unit, own, closure_services):
    """N05: whether the terminal a unit asks for (`x-gravitee.terminal`, or the unit's `terminal`) opens a shell in a service
    that is the unit's own or one of its closure, and is not its seed service (`unit["seed"]`: a name or a list). A unit with
    no terminal is fine; one the policy cannot read is not."""
    unit = _dict(unit)
    terminal = unit["terminal"] if "terminal" in unit else _dict(unit.get("meta")).get("terminal")
    if terminal is None:
        return True
    names, seed = _terminal_services(terminal), unit.get("seed")
    seeds = {seed} if isinstance(seed, str) else set(seed or ())
    return bool(names) and all(name in set(own or ()) | set(closure_services or ()) and name not in seeds for name in names)


# ---------------------------------------------------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------------------------------------------------

def _unknown(key):
    return key not in SERVICE_KEYS and key not in HOST_KEYS and key not in FILE_KEYS and not str(key).startswith("x-")


def _bad_volume(name, definition, project):
    """T14 on a volume the unit declares: a volume it does not own, or one that is a folder of this machine in disguise."""
    options = _dict(definition.get("driver_opts"))
    return bool(_true(definition.get("external")) or definition.get("driver") not in (None, "", "local")
                or "device" in options or "bind" in str(options.get("o", "")).lower()
                or str(options.get("type", "")).lower() in ("nfs", "cifs", "none")
                or project and definition.get("name") not in (None, f"{project}_{name}"))


def _bad_network(name, definition, project):
    """T15 on a network the unit declares: one that is not a bridge of its own."""
    return bool(_true(definition.get("external")) or definition.get("driver") in ("host", "macvlan", "ipvlan")
                or "parent" in _dict(definition.get("driver_opts"))
                or project and definition.get("name") not in (None, f"{project}_{name}"))


class _Run:
    """One judgement: the inputs, the findings of each rule, the surface. Built and read by judge and surface alone."""

    def __init__(self, unit, unit_config, core_config, own, overlaid, roots, env, context):
        self.unit, self.context = _dict(unit), _dict(context)
        self.kind, self.meta = self.unit.get("kind"), _dict(self.unit.get("meta"))
        self.config = unit_config if isinstance(unit_config, dict) else None
        self.core = _dict(core_config)
        self.local, self.host_ok = bool(self.context.get("local")), bool(self.context.get("host_ok"))
        self.realpath = self.context.get("realpath")
        self.resolve = _resolver(self.realpath)
        self.roots = _roots(roots, self.resolve)
        self.services = _dict(_dict(self.config).get("services"))
        self.own = [name for name in _list(own) if isinstance(name, str) and isinstance(self.services.get(name), dict)]
        self.overlaid = {name: ({str(key): value for key, value in keys.items()} if isinstance(keys, dict)
                                else {str(key): None for key in _list(keys)})
                         for name, keys in _dict(overlaid).items() if isinstance(name, str)}
        self.env = {name for name in env or () if isinstance(name, str)}
        self.members = set(self.own) | {name for name in _list(self.context.get("closure_services")) if isinstance(name, str)}
        self.lines = {}   # code -> reason lines, in the order found
        self.hits = {}   # service -> [(code, variant)]: its class H findings
        self.odd = []   # keys of a mount that the policy cannot place: T01
        core_volumes = _dict(self.core.get("volumes"))
        self.declared = {name for name in _dict(_dict(self.config).get("volumes")) if name not in core_volumes}   # its own volumes
        self.taken = self.dns()
        self.project = _dict(self.config).get("name") or self.core.get("name")
        core_networks = set(_dict(self.core.get("networks"))) | {"internal", "edge", "default"}
        theirs = _dict(self.core.get("networks"))
        self.networks_ok = core_networks | {
            name for name, definition in _dict(_dict(self.config).get("networks")).items()
            if theirs.get(name) != definition and not _bad_network(name, _dict(definition), self.project)}

    def say(self, code, variant=0, **fields):
        found, text = self.lines.setdefault(code, []), line(code, variant, **fields)
        if text not in found:
            found.append(text)

    def hit(self, service, code, variant=0):
        if (code, variant) not in self.hits.setdefault(service, []):
            self.hits[service].append((code, variant))

    def dns(self):
        """The names that other services of the lab network answer to and that this unit's own services must not: the
        services and containers of the core, and the services (and their containers) of the units it includes."""
        found = set()
        for services in (_dict(self.core.get("services")),
                         {name: service for name, service in self.services.items() if name not in self.own}):
            for name, service in services.items():
                found.add(name)
                if isinstance(_dict(service).get("container_name"), str):
                    found.add(service["container_name"])
        return found

    # -- the rules on the resolved JSON ---------------------------------------------------------------------------

    def scan(self):
        """T01 to T18, T22: everything the resolved JSON says."""
        if self.kind == "setup":   # a setup defines no service: one that does would run unjudged
            self.keys()
            if self.own or self.overlaid:
                self.say("T01", key="services")
            return
        for name in self.own:
            for code, variant in _hits(self.services[name], self.members):
                self.hit(name, code, variant)
            self.mounts(name, self.services[name])
            self.read_files(self.services[name])
            self.attachments(self.services[name])
            self.ports(self.services[name])
            self.build(self.services[name])
        self.keys()
        self.top()
        self.overlay()

    def keys(self):
        """T01 and T02."""
        seen = [key for name in self.own for key in self.services[name] if _unknown(key)]
        seen += [key for keys in self.overlaid.values() for key in keys if _unknown(key)] + self.odd
        if seen:
            self.say("T01", key=_spelled(dict.fromkeys(seen)))
        top = [key for key in self.config if key not in TOP_KEYS and not str(key).startswith("x-")]
        if top:
            self.say("T02", key=_spelled(top))

    def mounts(self, name, service):
        """T10 to T14 on what a service mounts."""
        roots, core_volumes = self.roots, _dict(self.core.get("volumes"))
        if service.get("volumes_from"):
            self.say("T13")
        for volume in _volumes(service):
            kind, source = volume.get("type"), volume.get("source")
            if kind not in MOUNT_TYPES or kind == "bind" and not source:   # default-deny: a mount it cannot place is no mount
                self.odd.append("volumes.type" if kind not in MOUNT_TYPES else "volumes.source")
            elif kind == "npipe" or kind == "bind" and _is_socket(source, self.resolve):
                self.hit(name, "T10")
            elif kind == "volume" and source in core_volumes:   # the core's data, mounted under a name of its own
                self.say("T14")
            elif kind == "bind" and source:
                path = self.resolve(source)
                if not _allowed(path, roots):
                    if any(_beside(path, one) for one in roots["machine"]):
                        self.say("T11")   # the Lab's own files, or a folder that holds them: no declaration allows it
                    else:
                        self.hit(name, "T11")
                elif not _true(volume.get("read_only")) and (
                        _inside(path, roots["unit"]) and not (_inside(path, roots["state"]) or _inside(path, roots["secrets"]))
                        or any(_inside(path, folder) for folder in roots["closure"])):
                    self.say("T12")

    def read_files(self, service):
        """T19 on the files a service reads into its environment, when the JSON still names them: each must be inside the unit's
        folder once its links are followed."""
        for key in FILE_KEYS:
            entries = service.get(key)
            for entry in entries if isinstance(entries, list) else [entries] if entries else []:
                path = entry.get("path") if isinstance(entry, dict) else entry
                if not isinstance(path, str) or not _inside(self.resolve(path, self.roots["unit"]), self.roots["unit"]):
                    self.say("T19", 1)

    def attachments(self, service):
        """T15 on the networks a service joins, and on the network of a service of the platform that it takes as its own."""
        attached = service.get("networks")
        aliases = [alias for options in (attached.values() if isinstance(attached, dict) else ())
                   for alias in _list(_dict(options).get("aliases")) if isinstance(alias, str)]   # answers to its container
        if any(network not in self.networks_ok for network in (list(attached) if isinstance(attached, (dict, list)) else [])) \
                or _joins(service.get("network_mode"), self.members) or any(alias in self.taken for alias in aliases):
            self.say("T15")

    def ports(self, service):
        """T17 on the ports a service publishes: loopback only. A port with no address is published to every interface."""
        for port in _list(service.get("ports")):
            if isinstance(port, str):
                host = "127.0.0.1" if port.startswith("127.0.0.1:") else ("::1" if port.startswith("[::1]:") else None)
            else:
                host = _dict(port).get("host_ip")
            if host not in LOOPBACK:
                self.say("T17")
                return

    def build(self, service):
        """T18 on how a service is built: where from (the unit's folder, the Lab's seeder, or a git repository at a commit),
        and with which keys. The keys are default-deny (BUILD_KEYS): any other blocks, whatever its value, and so does a build
        that is neither a mapping nor a text. `additional_contexts` is accepted for the Lab's seeder alone, and each of its
        contexts is held to the same rule as the context."""
        build = service.get("build")
        if build is None:
            return
        if not isinstance(build, (str, dict)):
            self.say("T18", 2)
            return
        build = {"context": build} if isinstance(build, str) else build
        context, roots = str(build.get("context") or ""), self.roots
        seeder = roots["seeder"]
        from_seeder = False
        if context and not posixpath.isabs(context):   # Compose gives a local context as an absolute path
            if not _pinned(context):
                self.say("T18", 1)
        else:
            path = self.resolve(context, roots["unit"]) if context else roots["unit"]
            from_seeder = bool(seeder) and path == seeder
            dockerfile = build.get("dockerfile")
            if not (_inside(path, roots["unit"]) or from_seeder):
                self.say("T18", 0)
            if isinstance(dockerfile, str) and dockerfile:
                where = self.resolve(dockerfile, path)
                if not (_inside(where, roots["unit"]) or from_seeder and _inside(where, seeder)):
                    self.say("T18", 0)
        extra = build.get("additional_contexts")
        for value in (list(extra.values()) if isinstance(extra, dict) else
                      [str(item).partition("=")[2] for item in _list(extra)]):
            self.extra_context(str(value))
        image = _repository(service.get("image"))
        taken = {_repository(_dict(other).get("image")) for other in _dict(self.core.get("services")).values()}
        if build_extras(build, from_seeder) or image and image in taken:   # a built image must not take the core's name
            self.say("T18", 2)

    def extra_context(self, value):
        """T18 on one of the extra build contexts."""
        if value.startswith(("docker-image://", "target:", "service:")):
            return
        if not posixpath.isabs(value):
            if not _pinned(value):
                self.say("T18", 1)
            return
        path = self.resolve(value)
        if not (_inside(path, self.roots["unit"]) or self.roots["seeder"] and path == self.roots["seeder"]):
            self.say("T18", 0)

    def top(self):
        """T14 to T16 on what the unit declares at the top of its file: what the core does not declare already."""
        def added(key):
            theirs = _dict(self.core.get(key))
            return {name: _dict(definition) for name, definition in _dict(self.config.get(key)).items()
                    if theirs.get(name) != definition}

        if any(_bad_volume(name, definition, self.project) for name, definition in added("volumes").items()):
            self.say("T14")
        if any(_bad_network(name, definition, self.project) for name, definition in added("networks").items()):
            self.say("T15")
        unit = self.roots["unit"]
        for key in ("secrets", "configs"):
            if any(definition.get("file") is not None and not _inside(self.resolve(definition["file"]), unit)
                   or definition.get("environment") or _true(definition.get("external"))
                   for definition in added(key).values()):
                self.say("T16")

    def overlay(self):
        """T22: what the unit adds to a service it does not own. Judged from what its own file sets (`overlaid`) and, apart from
        that, from how its resolved configuration differs from the core's: an overlay that the caller did not find is found
        here. A unit that brings another sees that unit's settings too, and holds them to the same rule."""
        beyond = [name for name, added in self.overlaid.items() if self.beyond(added, [self.roots["unit"]])]
        theirs = _dict(self.core.get("services"))
        for name, mine in self.services.items():
            if name not in beyond and name not in self.own and isinstance(theirs.get(name), dict) \
                    and self.changed(mine, theirs[name]):
                beyond.append(name)
        if beyond:
            self.say("T22", label=_spelled(self.label(name) for name in beyond))

    def changed(self, mine, theirs):
        """Whether a service of the core, as the unit's configuration has it, is changed beyond adding settings."""
        if not isinstance(mine, dict):
            return True
        if any(mine.get(key) != theirs.get(key) for key in set(mine) | set(theirs) if key not in OVERLAY_KEYS):
            return True
        added = {key: [item for item in _list(mine.get(key)) if item not in _list(theirs.get(key))] for key in ("volumes", "ports")}
        return self.beyond(added, [self.roots["unit"], *self.roots["closure"]])

    def beyond(self, added, folders):
        """Whether what is added to a service is more than settings: a key outside OVERLAY_KEYS, a volume that is neither a
        read-only bind of one of `folders` nor a named volume the unit declares itself, a port that is not on the loopback."""
        if any(key not in OVERLAY_KEYS for key in added):
            return True
        for volume in _volumes({"volumes": added.get("volumes")}):
            if not (volume.get("type") == "volume" and volume.get("source") in self.declared   # T14 judges its declaration
                    or volume.get("type") == "bind" and _true(volume.get("read_only"))
                    and any(_inside(self.resolve(volume.get("source")), folder) for folder in folders)):
                return True
        return any(not (isinstance(port, str) and port.startswith(("127.0.0.1:", "[::1]:"))
                        or isinstance(port, dict) and port.get("host_ip") in LOOPBACK) for port in _list(added.get("ports")))

    def label(self, name):
        """What a person calls a service: its io.gravitee.name label, else its group, else its name."""
        for source in (_dict(self.core.get("services")), self.services):
            labels = _dict(_dict(source.get(name)).get("labels"))
            if labels.get("io.gravitee.name") or labels.get("io.gravitee.group"):
                return _clean(labels.get("io.gravitee.name") or labels["io.gravitee.group"], NAME_CUT + 20)
        return _clean(name, NAME_CUT + 20)

    # -- the rules that do not need the JSON -----------------------------------------------------------------------

    def settings(self):
        """T20: the settings the unit reads that are not its own."""
        context = self.context
        mine = [f"{_env_name(context['ns'])}_"] if context.get("ns") else []
        if context.get("listed"):   # a listed catalog's unit also reads the settings that start with the id of the items it brings (ACME_SHOP_ for a unit named acme-shop)
            mine += [f"{_env_name(n)}_" for n in [_unit_name(self.unit.get("id")), *_list(context.get("closure_names"))] if n]
        shared = {n for n in _list(context.get("shared_settings")) if isinstance(n, str)}
        core = {n for n in _list(context.get("core_variables")) if isinstance(n, str)}
        names, defined = {n for n in _list(self.unit.get("references")) if isinstance(n, str)}, self.defined()
        # A seed reads the environment of its own container, which the unit's file defines: a name it defines there is its own
        names |= {n for n in _list(self.unit.get("seed_references")) if isinstance(n, str) and n not in defined}
        foreign = [n for n in names if not (n.startswith(tuple(mine)) or n.endswith(("_VERSION", "_REGISTRY", "_PULL_POLICY"))
                                            or n in core or n in shared)]
        if foreign:   # what is really set here comes first: it is what would leak
            self.say("T20", names=_spelled(sorted(foreign, key=lambda n: (n not in self.env, n))))

    def defined(self):
        """The environment names that the unit's own services define."""
        return {key for name in self.own for key in _dict(self.services[name].get("environment"))}

    def includes(self):
        """T23: an include outside the allowed set. A personal unit may include anything."""
        ns, declared = self.context.get("ns"), {n for n in _list(self.context.get("requires")) if isinstance(n, str)}
        allowed = {"addon", "setup"} if self.kind == "playground" else {"addon"}
        for target in _list(self.unit.get("includes")):
            found = target_of(target)
            if not found or found[1] not in allowed or found[0] != ns and found[0] not in declared:
                self.say("T23")
                return

    def audience(self):
        """T24 from the audiences a caller measured (`audience` of the unit's catalog, `closure_audiences` of the catalogs its
        closure reaches): a public unit depends on public units only. Nothing is said without both, which is how the Lab's own
        pipeline calls it (it adds T24 once every unit is judged), and how the command line calls it offline: an audience
        that was not measured is not guessed. What is given is read as 6.4 reads it: a word that is not "public" is private,
        and so is a list of audiences that is not a list."""
        context = self.context
        if "audience" not in context or "closure_audiences" not in context:
            return
        theirs = context["closure_audiences"]
        theirs = theirs if isinstance(theirs, (list, tuple, set, frozenset)) else ["private"]   # a doubt is private
        mine = rank(context["audience"] if isinstance(context["audience"], str) else None)
        if any(audience_blocked(mine, rank(one if isinstance(one, str) else None)) for one in theirs):
            self.say("T24")

    def compat(self):
        """T26: the Lab level the unit needs against what this Lab reads."""
        context = self.context
        low = context["contract_min"] if isinstance(context.get("contract_min"), int) else 1
        high = context["contract"] if isinstance(context.get("contract"), int) else 1
        level = context.get("level")
        if isinstance(level, int) and not isinstance(level, bool):
            if level > high:
                self.say("T26", 0, level=level, min=low, max=high)
            elif level < low:
                self.say("T26", 1, level=level)

    def host(self):
        """The class H findings: each blocks with its own line, or is allowed when the catalog may and the unit said why."""
        declared = {name for name, why in _dict(self.meta.get("host_access")).items()
                    if isinstance(name, str) and isinstance(why, str) and why.strip()}
        first = {}   # code -> the first variant of its line
        for name in self.own:
            for code, variant in self.hits.get(name, []):
                first[code] = min(first.get(code, variant), variant)
        if not self.host_ok:
            for code, variant in sorted(first.items(), key=lambda item: _number(item[0])):
                self.say(code, variant)
            return
        silent = [name for name in self.own if self.hits.get(name) and name not in declared]
        if silent:
            self.say("T28", service=_spelled(silent))

    # -- N01 to N05 and the surface ---------------------------------------------------------------------------------

    def root(self):
        """The project folder, as far as the roots say: the seeder sits in it."""
        return posixpath.dirname(self.roots["seeder"]) if self.roots["seeder"] else ""

    def seeds(self):
        return [name for name in self.own if _dict(self.services[name].get("labels")).get("io.gravitee.role") == "seed"]

    def official(self):
        return seeder_ok(self.config, self.roots, self.context.get("seeder_images"), self.realpath)

    def terminal(self):
        unit = dict(self.unit, meta=self.meta, seed=self.seeds())
        return terminal_ok(unit, self.own, set(self.context.get("closure_services") or ()) | set(self.own))

    def addresses(self):
        """N03: a console or a host check that is not on this machine."""
        urls = [value for name in self.own for key, value in _dict(self.services[name].get("labels")).items()
                if re.fullmatch(r"io\.gravitee\.console\.[a-z0-9-]+\.url", str(key))]
        urls += [check.get("check") for check in _list(_dict(self.meta.get("requires")).get("host")) if isinstance(check, dict)]
        return any(not local_address(url) for url in urls)

    def gateway(self):
        return isinstance(self.meta.get("gateway"), dict) and bool(self.meta["gateway"])

    def notes(self):
        """The lines of N01 to N05."""
        seed = [n for n in _list(self.unit.get("seed_references")) if isinstance(n, str)]
        defined, found = self.defined(), []
        if self.kind == "playground" and not self.official():
            found.append(line("N01"))
        if self.kind == "playground" and any(
                n == TOKEN_NAME or SECRET_NAME.search(n) and not NOT_SECRET.search(n) and n not in defined for n in seed):
            found.append(line("N02"))
        if self.addresses():
            found.append(line("N03"))
        if self.gateway() and not declared_ok(self.context):
            found.append(line("N04"))
        if self.meta.get("terminal") is not None and not self.terminal():
            found.append(line("N05"))
        return found

    def surface(self):
        """What the unit will do, from the same JSON as the verdict."""
        found, context = empty_surface(), self.context
        declared = _dict(self.meta.get("host_access"))
        images, ports, plugins = set(), set(), set()
        for name in self.own:
            service = self.services[name]
            if service.get("build") is not None:
                found["builds"].append({"service": name, "source": _clean(_source(service["build"]), SOURCE_CUT)})
            elif service.get("image"):
                images.add(_clean(_repository(service["image"]), SOURCE_CUT))
            for port in _list(service.get("ports")):
                published = str(_dict(port).get("published", ""))
                if published.isdigit():
                    ports.add(int(published))
            for volume in _volumes(service):
                path = self.resolve(volume.get("source")) if volume.get("type") == "bind" else ""
                plugins.update(posixpath.basename(root) for root in self.roots["plugins"] if _inside(path, root))
            if self.hits.get(name):
                found["uses_machine"].append({
                    "service": name, "label": self.label(name),
                    "what": elevations(service, self.roots, self.realpath, context.get("home"), self.members),
                    "why": _clean(declared[name]) if isinstance(declared.get(name), str) else ""})
        found["images"], found["ports"], found["plugins"] = sorted(images), sorted(ports), sorted(plugins)
        found["core"] = [{"service": name, "label": self.label(name), "keys": sorted(added)}
                         for name, added in self.overlaid.items() if added]
        names = {n for n in _list(self.unit.get("references")) if isinstance(n, str)}
        if isinstance(context.get("license_variable"), str) and context["license_variable"] in names:
            found["reads"].append("license")
        if self.gateway() and declared_ok(context):
            found["reads"].append("gateway token")
        if not context.get("listed"):   # a listed catalog's settings are implied by the configuration
            # the shared ones it declares, and a setting of this machine that only its namespace lets it read: T20 passes a name
            # that starts with the namespace, so a catalog named like what the person keeps in .env (openai, gemini) is shown
            mine = f"{_env_name(context['ns'])}_" if context.get("ns") else None
            found["reads"] += sorted(_clean(n, NAME_CUT + 20) for n in names if n in _list(context.get("shared_settings"))
                                     or mine and n.startswith(mine) and n in self.env)
        if self.kind == "playground" and self.seeds():
            found["seed"] = "official" if self.official() else "custom"
        shell = _terminal_services(self.meta.get("terminal"))
        found["shell"] = shell[0] if shell and self.terminal() else None
        found["needs"] = sorted({t[0] for t in map(target_of, _list(self.unit.get("includes")))
                                 if t and t[0] not in (context.get("ns"), "local")})
        return found

    def run(self):
        """The verdict."""
        name = self.unit.get("name") or ""
        if self.context.get("screened"):
            return _verdict(self.context["screened"], (), None, ["T19"], name)
        if self.config is not None:
            self.scan()
            self.host()
        self.settings()
        self.includes()
        self.audience()
        self.compat()
        if self.config is None:
            self.say("T27", last=_clean(_scrubbed(self.context.get("error") or "unreadable", self.root()), 160))
        if self.local:   # a unit of this machine is not blocked by the policy: only its restrictions and its surface remain
            self.lines = {}
        codes = sorted(self.lines, key=_number)
        return _verdict([line for code in codes for line in self.lines[code]],
                        self.notes() if self.config is not None else (),
                        self.surface() if self.config is not None else None, codes, name)


def judge(unit, unit_config, core_config, own, overlaid, roots, env, context):
    """The verdict of a unit: {state: ok or blocked, reasons: the lines of 8.3 in the order of the rules, notes: the
    lines of N01 to N05, surface: what it will do, codes: the rules that gave a reason, name: the unit's}. Pure, and it never
    raises: input it cannot read is a unit that "Cannot be read".

    A unit of this machine (`context["local"]`) is not blocked by the policy; its restrictions (N01 to N03, N05) and its
    surface remain. A unit whose text the screen refused (`context["screened"]`) is blocked with those reasons alone; one
    that Compose could not read (`unit_config` None, `context["error"]`) is judged on its includes and its level, and
    cannot be read."""
    try:
        return _Run(unit, unit_config, core_config, own, overlaid, roots, env, context).run()
    except Exception as error:   # noqa: BLE001 hostile input blocks one unit, it never breaks the project
        return _verdict([line("T27", last=f"unexpected {type(error).__name__}")], (), None, ["T27"],
                        _dict(unit).get("name") or "")


def surface(unit_config, core_config, own, overlaid, roots, context):
    """What a unit will do, for the preview: the `surface` of its verdict. `context["unit"]` is the unit judge reads,
    for what only it says (the reasons it gives for using this machine, its terminal, its gateway, its includes)."""
    if not isinstance(unit_config, dict):
        return empty_surface()
    context = _dict(context)
    run = _Run(context.get("unit"), unit_config, core_config, own, overlaid, roots, (), context)
    run.scan()
    return run.surface()
