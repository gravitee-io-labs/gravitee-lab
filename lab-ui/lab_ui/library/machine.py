"""What this machine needs to run the lab, each need with its state and the one fix that answers it: Docker with Compose 5.0
or later, the memory Docker has, the free disk where Docker keeps its images, the ports the platform publishes, and Git for
the catalogs. The page asks again every few seconds while it shows them, so that a line turns right by itself once it is
fixed. A fix is a link to open, a command to copy, or, on a Mac and in WSL, Docker Desktop to start, which is run here.

    report()        {checks: [{id, state, title, detail, actions}], ready, problems}
    act(id)         runs a fix that runs here; CatalogError `machine_action` (400) for any other

A state is ok, warn (the lab starts, with a risk), error (it does not), working (a fix is on its way) or idle (known once
Docker runs). An action is {id, label, primary} and, for a link, `href`; for a command to copy, `copy`."""

import os
import platform as host
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

from .. import paths
from ..catalogs.registry import CatalogError
from ..docker import compose
from . import model

COMPOSE_MIN = (5, 0)
DISK_MIN_GB = 15.0
DOCKER_DESKTOP_MAC = {"arm64": "https://desktop.docker.com/mac/main/arm64/Docker.dmg",
                      "x86_64": "https://desktop.docker.com/mac/main/amd64/Docker.dmg"}
DOCKER_ENGINE = "https://docs.docker.com/engine/install/"
DOCKER_DESKTOP_WINDOWS = "https://docs.docker.com/desktop/setup/install/windows-install/"
ORBSTACK = "https://orbstack.dev/download"
TROUBLESHOOTING = "https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/troubleshooting.md"
WSL_MEMORY = f"{TROUBLESHOOTING}#windows-and-wsl-2"
# Where Docker Desktop for Windows keeps its commands, seen from WSL, when the PATH that Windows gives WSL does not name them
DESKTOP_WINDOWS_BIN = "/mnt/c/Program Files/Docker/Docker/resources/bin"
# What Docker Desktop's WSL integration links in a distribution it serves: a link left without its target is a distribution it
# served and lost, which it does each time the distribution restarts, until Docker Desktop restarts
DESKTOP_LINK = "/usr/bin/docker"
WINDOWS_DISK = "/mnt/c"
# A line of `netstat -ano -p TCP` for a port listened on, on the loopback or on every address of Windows
WINDOWS_LISTENING = re.compile(r"^\s*TCP\s+(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)\s+0\.0\.0\.0:0\s", re.MULTILINE)
# A lab folder on a disk of Windows, seen from WSL: the containers read it slowly, and it does not keep the permissions of
# the certificates and the .env
WINDOWS_DRIVE = re.compile(r"^/mnt/[a-z]/")
# Where Homebrew puts Compose on Apple silicon: Docker does not look there until the plugin is linked where it does
BREW_COMPOSE = "/opt/homebrew/lib/docker/cli-plugins/docker-compose"
LINK_COMPOSE = f"mkdir -p ~/.docker/cli-plugins && ln -sfn {BREW_COMPOSE} ~/.docker/cli-plugins/docker-compose"
# Folders a sync client watches, by how the folder it keeps begins and the name people know: the containers write into the lab's
# folder, and a sync client can lock or rewrite what they write. On a Mac they are under Library/CloudStorage ("GoogleDrive-me@x",
# "OneDrive-Contoso", "Box-Box"), iCloud's is Library/Mobile Documents; older ones are in the home ("Google Drive",
# "OneDrive - Contoso", "Dropbox (Acme)"). Box's "Box" is a common word: it counts only where a sync client puts it.
SYNCED = (("Mobile Documents", "iCloud Drive"), ("GoogleDrive", "Google Drive"), ("Google Drive", "Google Drive"),
          ("OneDrive", "OneDrive"), ("Dropbox", "Dropbox"))
CLONE = "git clone https://github.com/gravitee-io-labs/gravitee-lab.git ~/gravitee-lab"
FRESH = 3   # seconds a report is reused: the page asks every few seconds, and so may two tabs

_lock = threading.Lock()
_last = {"at": 0.0, "report": None}
_asked = {}   # a fix that takes a while, by check id: when it was asked
_agents = {}   # what an agent is given in WSL, by the lab's folder


def system():
    """mac, wsl, linux or other"""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "wsl" if "microsoft" in host.uname().release.lower() else "linux"
    return "other"


def _action(id_, label, primary=False, **more):
    return {"id": id_, "label": label, "primary": primary, **more}


def _check(id_, state, title, detail, actions=()):
    return {"id": id_, "state": state, "title": title, "detail": detail, "actions": list(actions)}


def _version(text):
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:3]) if numbers else None


def _dotted(version):
    return ".".join(str(n) for n in version)


def _waiting(what, seconds=120):
    at = _asked.get(what)
    return at is not None and time.monotonic() - at < seconds


def docker_app():
    """The app that runs Docker on a Mac, by its path and the name people know; None elsewhere or without one. The path, not
    the name: an app just copied into Applications is not known by its name to `open -a` yet."""
    if system() != "mac":
        return None
    places = [Path("/Applications"), Path.home() / "Applications"]
    for bundle, name in (("Docker.app", "Docker Desktop"), ("OrbStack.app", "OrbStack")):
        for place in places:
            if (place / bundle).is_dir():
                return str(place / bundle), name
    return None


def _opening(app):
    """Whether the app that runs Docker is open (its first start asks for its license and a password, which takes as long
    as the person does), or was asked to open a moment ago"""
    return _waiting("docker") or compose.run(["pgrep", "-f", f"{app[0]}/Contents/MacOS/"], timeout=5)[0] == 0


def desktop_cli():
    """In WSL, the docker.exe of Docker Desktop for Windows, through which the Lab asks its state, starts it and reconnects
    it: on the PATH that Windows gives WSL, else where it installs; None elsewhere or without one"""
    if system() != "wsl":
        return None
    folders = [folder for folder in os.environ.get("PATH", "").split(os.pathsep) if folder.startswith("/mnt/")]
    for folder in folders + [DESKTOP_WINDOWS_BIN]:
        if (Path(folder) / "docker.exe").is_file():
            return str(Path(folder) / "docker.exe")
    return None


def _desktop(title):
    """The Docker row in WSL when Docker does not answer in it: Docker Desktop for Windows not installed, not running,
    starting, or running without this distribution, which it lost (a link of its own is left) or was never told to serve"""
    cli = desktop_cli()
    if not cli:
        return _check("docker", "error", title, "Docker is not installed", _install_docker())
    distro = os.environ.get("WSL_DISTRO_NAME") or "this distribution"
    rc, out, _ = compose.run([cli, "desktop", "status"], timeout=15)
    found = re.search(r"^Status\s+(\w+)", out, re.MULTILINE) if rc == 0 else None
    status = found.group(1).lower() if found else None
    if status != "running":
        if _waiting("docker") or status == "starting":
            return _check("docker", "working", title, "Docker Desktop is starting")
        return _check("docker", "error", title, "Docker Desktop is not running",
                      [_action("start-desktop", "Start Docker Desktop", True)])
    if _waiting("docker", 60):
        return _check("docker", "working", title, f"Docker Desktop is connecting to {distro}")
    if os.path.islink(DESKTOP_LINK):   # a restart serves it again, and stops what runs: said first
        rc, out, _ = compose.run([cli, "ps", "-q"], timeout=15)
        running = len(out.split()) if rc == 0 else 0
        said = f": Reconnect restarts it, which stops the {running} containers that run" if running else ""
        return _check("docker", "error", title, f"Docker Desktop lost {distro}{said}",
                      [_action("reconnect-desktop", "Reconnect", True)])
    return _check("docker", "error", title,
                  f"Docker Desktop does not serve {distro}: turn it on in Settings > Resources > WSL integration",
                  [_action("open-desktop", "Open Docker Desktop", True)])


def _info():
    """What Docker says of itself, or None when it does not answer"""
    rc, out, _ = compose.run(["docker", "info", "--format",
                              "{{.ServerVersion}}|{{.OperatingSystem}}|{{.MemTotal}}|{{.DockerRootDir}}"], timeout=15)
    fields = out.strip().split("|")
    if rc != 0 or len(fields) != 4 or not fields[0]:
        return None
    return {"version": fields[0], "system": fields[1], "memory": int(fields[2]) if fields[2].isdigit() else None,
            "root": fields[3]}


def _install_docker():
    if system() == "mac":
        machine = "arm64" if host.machine() == "arm64" else "x86_64"
        return [_action("download-docker", "Download Docker Desktop", True, href=DOCKER_DESKTOP_MAC[machine]),
                _action("get-orbstack", "Get OrbStack", href=ORBSTACK)]
    if system() == "wsl":
        return [_action("docker-windows", "Install Docker Desktop", True, href=DOCKER_DESKTOP_WINDOWS)]
    return [_action("docker-engine", "Install Docker Engine", True, href=DOCKER_ENGINE)]


def _docker():
    """The Docker row, and what Docker says of itself when it answers"""
    title = "Docker"
    # In WSL without Docker Desktop's integration, `docker` is Docker Desktop's command for Windows: it only says so
    windows = system() == "wsl" and (shutil.which("docker") or "").startswith("/mnt/")
    if windows or compose.run(["docker", "--version"], timeout=10)[0] == 127:
        if system() == "wsl":
            return _desktop(title), None
        app = docker_app()
        if app:
            if _opening(app):
                return _check("docker", "working", title, f"{app[1]} is starting"), None
            return _check("docker", "error", title, f"{app[1]} is installed and has not been started yet",
                          [_action("open-docker", f"Open {app[1]}", True)]), None
        return _check("docker", "error", title, "Docker is not installed", _install_docker()), None
    info = _info()
    if info is None and desktop_cli():
        return _desktop(title), None
    if info is None:
        app = docker_app()
        name = app[1] if app else "Docker"
        if app and _opening(app):
            return _check("docker", "working", title, f"{name} is starting"), None
        if app:
            actions = [_action("open-docker", f"Open {name}", True)]
        elif system() in ("linux", "wsl"):   # in WSL without Docker Desktop, Docker Engine runs in the distribution
            actions = [_action("start-docker", "Copy the command", True, copy="sudo systemctl start docker")]
        else:
            actions = []
        return _check("docker", "error", title, f"{name} is not running", actions), None
    rc, out, _ = compose.run(["docker", "compose", "version", "--short"], timeout=15)
    found = _version(out) if rc == 0 else None
    engine = ("Docker Desktop" if "Docker Desktop" in info["system"] else "OrbStack" if info["system"] == "OrbStack"
              else "Docker Engine")
    if found is None and Path(BREW_COMPOSE).is_file():
        return _check("docker", "error", title, "Compose is installed, and Docker does not find it",
                      [_action("link-compose", "Copy the command", True, copy=LINK_COMPOSE)]), info
    if found is None or found[:2] < COMPOSE_MIN:
        app = docker_app()
        said = f"Compose {_dotted(found)} is too old" if found else "Compose is missing"
        actions = [_action("open-docker", f"Open {app[1]}", True)] if app else \
            [_action("docker-engine", "Update Docker", True, href=DOCKER_ENGINE)]
        return _check("docker", "error", title, f"{said}: update {engine} to get Compose 5.0 or later", actions), info
    return _check("docker", "ok", title, f"{engine} {info['version']}, Compose {_dotted(found)}"), info


def _memory(info):
    title = "Memory for Docker"
    if not info or not info["memory"]:
        return _check("memory", "idle", title, "Known once Docker runs")
    shown = f"{info['memory'] / 2 ** 30:.0f} GB"
    if info["memory"] >= model.LOW_MEMORY:
        return _check("memory", "ok", title, shown)
    app = docker_app()
    if app:
        return _check("memory", "warn", title, f"{shown}: give it 8 GB in the settings of {app[1]}",
                      [_action("open-docker", f"Open {app[1]}")])
    if system() == "wsl":
        return _check("memory", "warn", title, f"{shown}: give WSL 8 GB in .wslconfig",
                      [_action("wsl-memory", "How to", href=WSL_MEMORY)])
    return _check("memory", "warn", title, f"{shown}: the platform needs 8 GB")


def _disk(info):
    """Free space where Docker keeps its images: its own folder on Linux, the home disk on a Mac, where its disk image is, and
    in WSL the disk of Windows, where the disks of WSL and of Docker Desktop are (the free space of the distribution's own
    disk says nothing: it grows as it is written)"""
    title = "Disk space"
    if info and system() == "linux" and info.get("root"):
        where = Path(info["root"])
    elif system() == "wsl" and Path(WINDOWS_DISK).is_dir():
        where = Path(WINDOWS_DISK)
    else:
        where = Path.home()
    try:
        free = shutil.disk_usage(where).free / 1e9
    except OSError:
        return _check("disk", "idle", title, "Unknown")
    if free >= DISK_MIN_GB:
        return _check("disk", "ok", title, f"{free:.0f} GB free")
    return _check("disk", "warn", title, f"{free:.0f} GB free: the first start downloads about 15 GB")


def core_ports():
    """The ports of this machine that the platform publishes: {port: the name of its service}"""
    from . import project   # the project reads the machine's state too: imported when asked
    try:
        services = project.load()["services"]
    except Exception:  # noqa: BLE001 - a core that does not load has its own notice
        return {}
    ports = {}
    for name, service in services.items():
        if (service.get("owner") or {}).get("kind") != "platform":
            continue
        for port in service.get("ports") or []:
            if str(port).isdigit():
                ports[int(port)] = service.get("label") or name
    return ports


def _lab_ports():
    """The ports the lab's own containers hold now"""
    rc, out, _ = compose.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={compose.project_name()}",
                              "--format", "{{.Ports}}"], timeout=10)
    return {int(p) for p in re.findall(r":(\d+)->", out)} if rc == 0 else set()


def _taken(port):
    """Whether another program listens on this port of 127.0.0.1"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _windows_ports():
    """In WSL, the TCP ports that a program of Windows listens on, on the loopback or on every address: Docker Desktop
    publishes the containers' ports there, where the distribution does not see them. Read from netstat, whose lines are
    the same in every language of Windows but their headings: a listening line has the remote address 0.0.0.0:0."""
    if system() != "wsl":
        return set()
    rc, out, _ = compose.run(["netstat.exe", "-ano", "-p", "TCP"], timeout=10)
    return {int(port) for port in WINDOWS_LISTENING.findall(out)} if rc == 0 else set()


def _ports(info):
    title = "Ports"
    if not info:
        return _check("ports", "idle", title, "Known once Docker runs")
    ports = core_ports()
    if not ports:
        return _check("ports", "idle", title, "Known once the platform's file loads")
    ours = _lab_ports() if info else set()
    windows = _windows_ports()
    busy = sorted(port for port in ports if port not in ours and (port in windows or _taken(port)))
    if not busy:
        return _check("ports", "ok", title, f"No other program uses the {len(ports)} ports of the platform")
    named = ", ".join(f"{port} ({ports[port]})" for port in busy[:4]) + (" and more" if len(busy) > 4 else "")
    verb = "is" if len(busy) == 1 else "are"
    return _check("ports", "warn", title, f"{named} {verb} used by another program: stop it before the platform starts")


def _folder():
    """The lab's folder, a line only when a sync client watches it or, in WSL, when it is on a disk of Windows: one more line
    to read is one too many otherwise"""
    if system() == "wsl" and WINDOWS_DRIVE.match(paths.ROOT.resolve().as_posix()):
        return _check("folder", "warn", "Lab folder", "On a disk of Windows, which the containers read slowly",
                      [_action("clone-outside", "Copy the command", True, copy=CLONE)])
    parts = paths.ROOT.resolve().parts
    for at, part in enumerate(parts):
        name = next((name for start, name in SYNCED if part.startswith(start)), None)
        if not name and (part == "Box" or part.startswith("Box-")) and at and parts[at - 1] in ("CloudStorage", Path.home().name):
            name = "Box"
        if name:
            return _check("folder", "warn", "Lab folder", f"In {name}, whose sync can lock the files the containers write",
                          [_action("clone-outside", "Copy the command", True, copy=CLONE)])
    return None


def _git():
    title = "Git"
    clt_stub = system() == "mac" and shutil.which("git") == "/usr/bin/git" and \
        compose.run(["/usr/bin/xcode-select", "-p"], timeout=5)[0] != 0
    found = None if clt_stub else compose.run(["git", "--version"], timeout=10)
    version = _version(found[1]) if found and found[0] == 0 else None
    if version:
        return _check("git", "ok", title, f"Git {_dotted(version)}")
    if system() == "mac":
        if _waiting("git", 3600):
            return _check("git", "working", title, "Installing Apple's Command Line Tools: follow the window macOS opened")
        return _check("git", "error", title, "Git is missing: the catalogs cannot be downloaded",
                      [_action("install-clt", "Install", True)])
    return _check("git", "error", title, "Git is missing: the catalogs cannot be downloaded",
                  [_action("install-git", "Copy the command", True, copy="sudo apt install git")])


def report(fresh=False):
    """Every check, the newest within FRESH seconds"""
    with _lock:
        if not fresh and _last["report"] and time.monotonic() - _last["at"] < FRESH:
            return _last["report"]
    docker, info = _docker()
    checks = [docker, _memory(info), _disk(info), _ports(info), _git(), _folder()]
    checks = [check for check in checks if check]
    problems = sum(1 for c in checks if c["state"] in ("error", "warn", "working"))
    result = {"checks": checks, "ready": all(c["state"] in ("ok", "warn", "idle") for c in checks), "problems": problems,
              "system": system()}
    with _lock:
        _last.update(at=time.monotonic(), report=result)
    return result


# What each fix of Docker Desktop asks its command, in WSL: a restart serves again the distributions it lost. Open runs
# the app, two folders above its command, which shows the window of the one that runs
DESKTOP_ACTIONS = {"start-desktop": ("start", "--detach"), "reconnect-desktop": ("restart", "--detach"), "open-desktop": ()}
DESKTOP_APP = "Docker Desktop.exe"


def act(action):
    """Runs a fix that runs on this machine: open the app that runs Docker, start or reconnect Docker Desktop from WSL, or
    Apple's installer of Git"""
    cli = desktop_cli()
    if action == "open-docker" and docker_app():
        rc, _, err = compose.run(["open", docker_app()[0]], timeout=20)
        if rc != 0:
            raise CatalogError("machine_action", err.strip() or "The app did not open", 400)
        _asked["docker"] = time.monotonic()
    elif action in DESKTOP_ACTIONS and cli:
        command = [str(Path(cli).parents[2] / DESKTOP_APP)] if action == "open-desktop" else \
            [cli, "desktop", *DESKTOP_ACTIONS[action]]
        rc, _, err = compose.run(command, timeout=30)
        if rc != 0:
            raise CatalogError("machine_action", err.strip() or "Docker Desktop did not answer", 400)
        if action != "open-desktop":
            _asked["docker"] = time.monotonic()
    elif action == "install-clt" and system() == "mac":
        _asked["git"] = time.monotonic()
        compose.run(["/usr/bin/xcode-select", "--install"], timeout=20)
    else:
        raise CatalogError("machine_action", f"{action} is not a fix this machine runs", 400)
    with _lock:
        _last["report"] = None
    return report(fresh=True)


def agent():
    """What an AI agent is given to work on the lab: the folder to open, and how it runs the lab's commands. In WSL the agents
    are apps of Windows (the Claude app, Codex, Cursor): they open the folder by its Windows path and run the commands in
    the distribution through wsl.exe, as Windows has no make or Python and its git refuses a folder of WSL. Elsewhere the
    folder alone."""
    root = str(paths.ROOT)
    if system() != "wsl":
        return {"folder": root, "note": ""}
    if root not in _agents:   # the state asks every few seconds, and the answer stays while the Lab UI runs
        rc, out, _ = compose.run(["wslpath", "-w", root], timeout=10)
        distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
        _agents[root] = {"folder": out.strip() if rc == 0 and out.strip() else root,
                         "note": f"The lab runs in WSL: run its commands (make, git, python3) in {distro}, as "
                                 f"wsl.exe -d {distro} --cd {root} -- followed by the command."}
    return _agents[root]


def summary():
    """For the state: whether something keeps the platform from starting, and how many lines need a look"""
    found = report()
    return {"ready": found["ready"], "problems": found["problems"]}

