"""library/apps.py: the apps of this machine that Open in offers, on a Mac, in WSL and on Linux, from what is installed (stood in
for here), and the command that opens a file or a folder of the project in each; an app that is not here and a path the editor does
not show are refused before anything runs. Which paths are opened is files.openable's, in test_files."""

import os
from pathlib import Path

from lab_ui.library import apps

from .. import Case

ROOT = Path("/Users/me/gravitee-lab")
KAZOO = ".lab/catalogs/gravitee/addons/kazoo"


class Apps(Case):
    def setUp(self):
        self.system = "mac"
        self.bundles, self.commands, self.ran, self.started = set(), set(), [], []
        self.answer = (0, "", "")
        self.patch(apps.machine, "system", side_effect=lambda: self.system)
        self.patch(apps.machine, "bundle", side_effect=lambda name: f"/Applications/{name}" if name in self.bundles else None)
        self.patch(apps.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name in self.commands else None)
        self.patch(apps.compose, "run", side_effect=lambda cmd, timeout=120, **_: self.ran.append(cmd) or self.answer)
        self.patch(apps, "detached", side_effect=self.started.append)
        self.patch(apps.files, "openable", side_effect=lambda path: (ROOT / path, path == KAZOO))
        self.patch(os, "environ", {})

    def names(self):
        return [(app["id"], app["name"]) for app in apps.found()]

    def test_on_a_mac_finder_then_the_editors_whose_app_is_installed(self):
        self.assertEqual(self.names(), [("files", "Finder")])
        self.bundles |= {"Cursor.app", "Visual Studio Code.app", "IntelliJ IDEA CE.app"}
        self.commands |= {"zed"}   # a command alone is not an app of a Mac
        self.assertEqual(self.names(), [("files", "Finder"), ("vscode", "VS Code"), ("cursor", "Cursor"), ("intellij", "IntelliJ IDEA")])

    def test_in_wsl_the_explorer_of_windows_then_the_editors_whose_command_windows_puts_on_the_path(self):
        self.system = "wsl"
        self.assertEqual(self.names(), [])
        self.commands |= {"explorer.exe", "code", "cursor"}
        self.bundles |= {"Zed.app"}
        self.assertEqual(self.names(), [("files", "File Explorer"), ("vscode", "VS Code"), ("cursor", "Cursor")])

    def test_on_linux_the_file_manager_only_on_a_desktop(self):
        self.system = "linux"
        self.commands |= {"xdg-open", "subl"}
        self.assertEqual(self.names(), [("sublime", "Sublime Text")], "no display: no file manager to show")
        self.patch(os, "environ", {"WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual(self.names(), [("files", "File manager"), ("sublime", "Sublime Text")])

    def test_on_a_mac_open_starts_the_app_and_says_when_it_did_not(self):
        self.bundles |= {"Visual Studio Code.app"}
        apps.open_in("files", f"{KAZOO}/compose.yml")
        apps.open_in("files", KAZOO)
        apps.open_in("vscode", KAZOO)
        self.assertEqual(self.ran, [["open", "-R", f"{ROOT}/{KAZOO}/compose.yml"], ["open", "-a", "Finder", f"{ROOT}/{KAZOO}"],
                                    ["open", "-a", "/Applications/Visual Studio Code.app", f"{ROOT}/{KAZOO}"]])
        self.answer = (1, "", "The application cannot be opened.\n")
        refused = self.refusal(lambda: apps.open_in("vscode", KAZOO))
        self.assertEqual((refused.code, refused.status, refused.message), ("open_in", 400, "The application cannot be opened."))
        self.assertEqual(self.started, [])

    def test_in_wsl_the_explorer_opens_the_path_as_windows_names_it_and_an_editor_its_own(self):
        self.system = "wsl"
        self.commands |= {"explorer.exe", "code"}
        self.answer = (0, "\\\\wsl.localhost\\Ubuntu\\home\\me\\gravitee-lab\\.env\n", "")
        apps.open_in("files", ".env")
        apps.open_in("vscode", KAZOO)
        self.assertEqual(self.started, [["explorer.exe", "/select,\\\\wsl.localhost\\Ubuntu\\home\\me\\gravitee-lab\\.env"],
                                        ["/usr/bin/code", f"{ROOT}/{KAZOO}"]])
        self.assertEqual(self.ran, [["wslpath", "-w", f"{ROOT}/.env"]])

    def test_on_linux_the_file_manager_opens_the_folder_of_a_file(self):
        self.system = "linux"
        self.commands |= {"xdg-open"}
        self.patch(os, "environ", {"DISPLAY": ":0"})
        apps.open_in("files", f"{KAZOO}/compose.yml")
        self.assertEqual(self.started, [["xdg-open", f"{ROOT}/{KAZOO}"]])

    def test_an_app_that_is_not_here_or_does_not_start_is_said(self):
        for app in ("vscode", "terminal", None, ["files"]):
            with self.subTest(app=app), self.assertRaisesRegex(ValueError, "This app is not on this machine"):
                apps.open_in(app, KAZOO)
        self.system = "linux"
        self.commands |= {"code"}
        self.patch(apps, "detached", side_effect=OSError(13, "Permission denied"))
        refused = self.refusal(lambda: apps.open_in("vscode", KAZOO))
        self.assertEqual((refused.code, refused.message), ("open_in", "VS Code did not open: Permission denied"))
        self.assertEqual(self.ran, [])


class Detached(Case):
    def test_it_is_started_apart_from_the_lab_ui_with_nothing_to_read_or_answer(self):
        popen = self.patch(apps.subprocess, "Popen")
        apps.detached(["code", "/home/me/gravitee-lab"])
        self.assertEqual(popen.call_args.args[0], ["code", "/home/me/gravitee-lab"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual({popen.call_args.kwargs[stream] for stream in ("stdin", "stdout", "stderr")}, {apps.subprocess.DEVNULL})

