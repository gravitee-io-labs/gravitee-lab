"""The apps of this machine that open what the editor shows, for its Open in: the file manager (Finder on a Mac, File Explorer of
Windows in WSL, the one xdg-open starts on a Linux desktop) and the code editors installed here, and the opening of a path of the
project in one. Only an app found here is offered, and only a path the editor shows is opened (files.openable).

    found()               [{id, name}]: the file manager first, then the editors in the order of EDITORS
    open_in(id, path)     opens a path of the project in one; ValueError for an app that is not here or a path it does not open,
                          CatalogError `open_in` (400) when the app does not start
    detached(command)     starts an app of the person's desktop and leaves it, as the page is opened in their browser"""

import os
import shutil
import subprocess

from .. import paths
from ..catalogs.registry import CatalogError
from ..docker import compose
from . import files, machine

FILES = "files"   # the id of the file manager, whatever its name on this system
# The code editors, by id: the name people know, the bundles of the app on a Mac, and the command its install puts on the PATH of
# Linux. In WSL it is the command that an editor of Windows adds to the PATH that Windows gives WSL, which opens a path of the
# distribution through the editor's WSL remote.
EDITORS = (("vscode", "VS Code", ("Visual Studio Code.app",), "code"),
           ("cursor", "Cursor", ("Cursor.app",), "cursor"),
           ("windsurf", "Windsurf", ("Windsurf.app",), "windsurf"),
           ("zed", "Zed", ("Zed.app",), "zed"),
           ("sublime", "Sublime Text", ("Sublime Text.app",), "subl"),
           ("intellij", "IntelliJ IDEA", ("IntelliJ IDEA.app", "IntelliJ IDEA CE.app"), "idea"))


def _file_manager():
    """The name of the file manager of this machine, or None: in WSL the Explorer of Windows, on Linux the one of the desktop,
    which xdg-open starts and which a machine without a desktop does not have"""
    system = machine.system()
    if system == "mac":
        return "Finder"
    if system == "wsl":
        return "File Explorer" if shutil.which("explorer.exe") else None
    desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    return "File manager" if system == "linux" and desktop and shutil.which("xdg-open") else None


def _editor(bundles, command):
    """How an editor is started here: ("bundle", its path) on a Mac, ("command", its path) on Linux and in WSL; None when it is not
    installed"""
    if machine.system() == "mac":
        found = next(filter(None, map(machine.bundle, bundles)), None)
        return ("bundle", found) if found else None
    found = shutil.which(command)
    return ("command", found) if found else None


def found():
    """The apps of this machine that Open in offers: the file manager, then each editor installed"""
    manager = _file_manager()
    return ([{"id": FILES, "name": manager}] if manager else []) + \
        [{"id": id_, "name": name} for id_, name, bundles, command in EDITORS if _editor(bundles, command)]


def detached(command):
    """Starts an app of the person's desktop and leaves it: it outlives the Lab UI, and its answer is not waited for (a code
    editor's command may wait for its window, and explorer.exe answers 1 when it did what was asked)"""
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=paths.ROOT)


def _windows(where):
    """A path of the distribution as Windows names it (\\\\wsl.localhost\\<distribution>\\...), for the Explorer"""
    rc, out, err = compose.run(["wslpath", "-w", str(where)], timeout=10)
    if rc != 0 or not out.strip():
        raise CatalogError("open_in", err.strip() or f"Windows does not name {where}", 400)
    return out.strip()


def _command(app, where, folder):
    """The command that opens a file or a folder in an app: a file is shown selected in its folder by Finder and the Explorer,
    and its folder is opened by the file manager of Linux, which has no common way to select one"""
    if app == FILES:
        system = machine.system()
        if system == "mac":
            return ["open", "-a", "Finder", str(where)] if folder else ["open", "-R", str(where)]
        if system == "wsl":
            return ["explorer.exe", _windows(where) if folder else f"/select,{_windows(where)}"]
        return ["xdg-open", str(where if folder else where.parent)]
    how, found = next(_editor(bundles, command) for id_, _, bundles, command in EDITORS if id_ == app)
    return ["open", "-a", found, str(where)] if how == "bundle" else [found, str(where)]


def open_in(app, path):
    """Opens a file or a folder that the editor shows in an app of this machine. On a Mac `open` says at once whether the app
    started; elsewhere the app is started and left."""
    names = {one["id"]: one["name"] for one in found()}
    if not isinstance(app, str) or app not in names:
        raise ValueError("This app is not on this machine")
    where, folder = files.openable(path)
    command = _command(app, where, folder)
    if command[0] == "open":
        rc, _, err = compose.run(command, timeout=20)
        if rc != 0:
            raise CatalogError("open_in", err.strip() or f"{names[app]} did not open", 400)
        return {}
    try:
        detached(command)
    except OSError as error:
        raise CatalogError("open_in", f"{names[app]} did not open: {error.strerror or error}", 400) from error
    return {}
