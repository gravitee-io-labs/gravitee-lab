"""library/machine.py: what this machine needs to run the lab, each need with its state and its fix, from what the commands
answer (stood in for here): Docker not there, there and stopped, with a Compose older than 5.0, short of memory; the ports of
the platform held by another program; Git missing; in WSL, Docker Desktop for Windows stopped, lost or never told to serve the
distribution, and a lab on a disk of Windows. A fix runs here on a Mac and in WSL; a link or a command to copy otherwise."""

from pathlib import Path
from unittest import mock

from lab_ui.catalogs.registry import CatalogError
from lab_ui.library import machine

from .. import Case

HEALTHY = {
    ("docker", "--version"): (0, "Docker version 29.8.2", ""),
    ("docker", "info"): (0, "29.8.2|Docker Desktop|8320028672|/var/lib/docker\n", ""),
    ("docker", "compose"): (0, "5.6.0\n", ""),
    ("docker", "ps"): (0, "127.0.0.1:9200->9200/tcp\n", ""),
    ("git", "--version"): (0, "git version 2.43.2", ""),
    ("/usr/bin/xcode-select", "-p"): (0, "/Library/Developer/CommandLineTools", ""),
    ("pgrep", "-f"): (1, "", ""),   # the app that runs Docker is not open
}
DESKTOP = "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"   # Docker Desktop's command for Windows, seen from WSL


class Machine(Case):
    def setUp(self):
        self.answers = dict(HEALTHY)
        self.ran = []

        def run(cmd, timeout=120, **_):
            self.ran.append(cmd)
            return self.answers.get(tuple(cmd[:2]), (0, "", ""))

        self.patch(machine.compose, "run", side_effect=run)
        self.patch(machine.compose, "project_name", return_value="gravitee")
        self.patch(machine, "system", return_value="mac")
        self.patch(machine, "docker_app", return_value=("/Applications/Docker.app", "Docker Desktop"))
        self.patch(machine, "core_ports", return_value={9200: "Elasticsearch", 27017: "MongoDB"})
        self.patch(machine.paths, "ROOT", Path("/Users/me/gravitee-lab"))   # where the tests run is no sync client's
        self.patch(machine, "_taken", return_value=False)
        self.patch(machine.shutil, "which", side_effect=lambda name: f"/opt/homebrew/bin/{name}")
        machine._asked.clear()
        machine._last.update(at=0.0, report=None)

    def check(self, check_id):
        return next(c for c in machine.report(fresh=True)["checks"] if c["id"] == check_id)

    def test_a_ready_machine_says_each_need_met_with_what_it_found(self):
        found = machine.report(fresh=True)
        self.assertEqual([(c["id"], c["state"]) for c in found["checks"]],
                         [("docker", "ok"), ("memory", "ok"), ("disk", found["checks"][2]["state"]), ("ports", "ok"), ("git", "ok")])
        self.assertEqual(found["checks"][0]["detail"], "Docker Desktop 29.8.2, Compose 5.6.0")
        self.assertEqual(found["checks"][1]["detail"], "8 GB", "8 GB set in Docker Desktop reads 7.75 GiB")
        self.assertEqual(found["checks"][3]["detail"], "No other program uses the 2 ports of the platform")
        self.assertTrue(found["ready"])

    def test_docker_not_installed_offers_its_download_and_a_mac_without_its_app_too(self):
        self.answers[("docker", "--version")] = (127, "", "[Errno 2] No such file or directory: 'docker'")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]), ("error", "Docker Desktop is installed and has not been started yet"))
        self.assertEqual([a["id"] for a in docker["actions"]], ["open-docker"])
        self.patch(machine, "docker_app", return_value=None)
        docker = self.check("docker")
        self.assertEqual(docker["detail"], "Docker is not installed")
        self.assertEqual([(a["label"], "href" in a) for a in docker["actions"]], [("Download Docker Desktop", True), ("Get OrbStack", True)])
        self.assertFalse(machine.report(fresh=True)["ready"])

    def test_docker_that_does_not_answer_is_opened_here_on_a_mac_and_said_as_starting_while_it_starts(self):
        self.answers[("docker", "info")] = (1, "", "Cannot connect to the Docker daemon")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"], docker["actions"][0]["label"]),
                         ("error", "Docker Desktop is not running", "Open Docker Desktop"))
        self.assertEqual((self.check("memory")["state"], self.check("ports")["detail"]), ("idle", "Known once Docker runs"))
        self.answers[("open", "/Applications/Docker.app")] = (0, "", "")
        machine.act("open-docker")
        self.assertIn(["open", "/Applications/Docker.app"], self.ran)
        self.assertEqual((self.check("docker")["state"], self.check("docker")["detail"]), ("working", "Docker Desktop is starting"))

    def test_an_app_just_installed_is_opened_by_its_path_and_said_as_starting_only_once_it_opened(self):
        self.answers[("docker", "--version")] = (127, "", "")
        self.answers[("open", "/Applications/Docker.app")] = (1, "", "The application cannot be opened.")
        with self.assertRaises(CatalogError):
            machine.act("open-docker")
        self.assertEqual(self.check("docker")["detail"], "Docker Desktop is installed and has not been started yet")
        self.answers[("open", "/Applications/Docker.app")] = (0, "", "")
        machine.act("open-docker")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"], docker["actions"]), ("working", "Docker Desktop is starting", []))
        self.assertFalse(machine.report(fresh=True)["ready"], "the platform waits for Docker")

    def test_docker_desktop_open_for_its_first_start_is_starting_however_long_the_person_takes(self):
        self.answers[("docker", "--version")] = (127, "", "")
        self.answers[("pgrep", "-f")] = (0, "4242\n", "")   # opened by the person, not from here: no wait was asked
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]), ("working", "Docker Desktop is starting"))
        self.assertIn(["pgrep", "-f", "/Applications/Docker.app/Contents/MacOS/"], self.ran)
        self.answers[("docker", "--version")] = (0, "Docker version 29.8.2", "")
        self.answers[("docker", "info")] = (1, "", "Cannot connect to the Docker daemon")
        self.assertEqual(self.check("docker")["detail"], "Docker Desktop is starting", "its daemon not answering yet")

    def test_on_linux_the_command_that_starts_docker_is_given_to_copy(self):
        self.patch(machine, "system", return_value="linux")
        self.patch(machine, "docker_app", return_value=None)
        self.answers[("docker", "info")] = (1, "", "Cannot connect to the Docker daemon")
        docker = self.check("docker")
        self.assertEqual(docker["actions"], [{"id": "start-docker", "label": "Copy the command", "primary": True,
                                              "copy": "sudo systemctl start docker"}])

    def in_wsl(self, status="running", linked=False):
        """WSL with Docker Desktop for Windows in this state, and its command for Windows as the `docker` of the distribution
        until its integration links the real one"""
        self.patch(machine, "system", return_value="wsl")
        self.patch(machine, "docker_app", return_value=None)
        self.patch(machine, "desktop_cli", return_value=DESKTOP)
        self.patch(machine.shutil, "which", side_effect=lambda name: "/mnt/c/Program Files/Docker/Docker/resources/bin/docker")
        self.patch(machine.os.path, "islink", side_effect=lambda path: linked and path == machine.DESKTOP_LINK)
        self.answers[(DESKTOP, "desktop")] = (0, f"Name                Value\nStatus              {status}\n", "")
        self.start(mock.patch.dict(machine.os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}))

    def test_in_wsl_docker_desktop_for_windows_is_started_from_here_and_said_as_starting(self):
        self.in_wsl(status="stopped")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"], docker["actions"][0]["label"]),
                         ("error", "Docker Desktop is not running", "Start Docker Desktop"))
        machine.act("start-desktop")
        self.assertIn([DESKTOP, "desktop", "start", "--detach"], self.ran)
        self.assertEqual((self.check("docker")["state"], self.check("docker")["detail"]), ("working", "Docker Desktop is starting"))
        self.assertFalse(machine.report(fresh=True)["ready"], "the platform waits for Docker")

    def test_in_wsl_docker_desktop_that_lost_the_distribution_is_reconnected_by_a_restart(self):
        self.in_wsl(linked=True)   # the link of its integration is left: it served Ubuntu, and Ubuntu restarted since
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"], docker["actions"][0]["label"]),
                         ("error", "Docker Desktop lost Ubuntu", "Reconnect"))
        machine.act("reconnect-desktop")
        self.assertIn([DESKTOP, "desktop", "restart", "--detach"], self.ran)
        self.assertEqual(self.check("docker")["detail"], "Docker Desktop is connecting to Ubuntu")

    def test_in_wsl_a_reconnect_that_stops_containers_says_so_first(self):
        self.in_wsl(linked=True)
        self.answers[(DESKTOP, "ps")] = (0, "4f2a9c\n81be07\n", "")   # another project's containers run in Docker Desktop
        self.assertEqual(self.check("docker")["detail"],
                         "Docker Desktop lost Ubuntu: Reconnect restarts it, which stops the 2 containers that run")

    def test_in_wsl_docker_desktop_that_never_served_the_distribution_says_where_to_turn_it_on(self):
        self.in_wsl()
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]),
                         ("error", "Docker Desktop does not serve Ubuntu: turn it on in Settings > Resources > WSL integration"))
        machine.act("open-desktop")
        self.assertIn(["/mnt/c/Program Files/Docker/Docker/Docker Desktop.exe"], self.ran, "its window, not a restart")
        self.assertEqual(self.check("docker")["state"], "error", "nothing is on its way until the person turns it on")

    def test_in_wsl_the_docker_of_the_distribution_that_does_not_answer_is_docker_desktop_s_to_start(self):
        self.in_wsl(status="stopped")
        self.patch(machine.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}")
        self.answers[("docker", "info")] = (1, "", "Cannot connect to the Docker daemon")
        self.assertEqual(self.check("docker")["detail"], "Docker Desktop is not running")
        self.patch(machine, "desktop_cli", return_value=None)   # Docker Engine in the distribution
        docker = self.check("docker")
        self.assertEqual((docker["detail"], docker["actions"][0]["copy"]), ("Docker is not running", "sudo systemctl start docker"))

    def test_in_wsl_without_docker_desktop_its_install_is_the_fix(self):
        self.in_wsl()
        self.patch(machine, "desktop_cli", return_value=None)
        self.answers[("docker", "--version")] = (127, "", "")
        docker = self.check("docker")
        self.assertEqual((docker["detail"], docker["actions"][0]["href"]), ("Docker is not installed", machine.DOCKER_DESKTOP_WINDOWS))

    def test_in_wsl_the_disk_is_the_one_of_windows_and_a_lab_on_it_is_a_warning(self):
        seen = []
        self.patch(machine.shutil, "disk_usage", side_effect=lambda where: seen.append(Path(where)) or type("U", (), {"free": 9e11})())
        self.patch(machine, "system", return_value="wsl")
        self.patch(machine, "WINDOWS_DISK", str(self.folder()))
        self.check("disk")
        self.assertEqual(seen[-1], Path(machine.WINDOWS_DISK), "the distribution's own disk grows as it is written")
        self.assertNotIn("folder", [c["id"] for c in machine.report(fresh=True)["checks"]])
        self.patch(machine.paths, "ROOT", Path("/mnt/c/Users/me/gravitee-lab"))
        folder = self.check("folder")
        self.assertEqual((folder["state"], folder["detail"], folder["actions"][0]["copy"]),
                         ("warn", "On a disk of Windows, which the containers read slowly", machine.CLONE))

    def test_a_compose_older_than_5_keeps_the_platform_from_starting(self):
        self.answers[("docker", "compose")] = (0, "2.29.7", "")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]),
                         ("error", "Compose 2.29.7 is too old: update Docker Desktop to get Compose 5.0 or later"))

    def test_compose_from_homebrew_that_docker_does_not_find_is_linked_with_one_command(self):
        self.answers[("docker", "compose")] = (1, "", "docker: unknown command: docker compose")
        self.folder()
        self.patch(machine, "BREW_COMPOSE", str(self.root / "missing"))
        self.assertEqual(self.check("docker")["detail"], "Compose is missing: update Docker Desktop to get Compose 5.0 or later")
        installed = self.root / "docker-compose"
        installed.write_text("")
        self.patch(machine, "BREW_COMPOSE", str(installed))
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]), ("error", "Compose is installed, and Docker does not find it"))
        self.assertEqual([(a["label"], "copy" in a) for a in docker["actions"]], [("Copy the command", True)])

    def test_a_lab_folder_a_sync_client_watches_is_a_warning_with_the_clone_to_copy_and_no_line_otherwise(self):
        self.patch(machine.paths, "ROOT", Path("/Users/me/gravitee-lab"))
        self.assertNotIn("folder", [c["id"] for c in machine.report(fresh=True)["checks"]])
        for root, name in (("/Users/me/Library/CloudStorage/GoogleDrive-me@example.com/My Drive/lab", "Google Drive"),
                           ("/Users/me/Library/Mobile Documents/com~apple~CloudDocs/lab", "iCloud Drive"),
                           ("/mnt/c/Users/me/OneDrive/lab", "OneDrive"),
                           ("/Users/me/OneDrive - Contoso/lab", "OneDrive"), ("/Users/me/Dropbox (Acme)/lab", "Dropbox"),
                           ("/Users/me/Library/CloudStorage/Box-Box/lab", "Box"), ("/Users/me/Google Drive/lab", "Google Drive")):
            self.patch(machine.paths, "ROOT", Path(root))
            folder = self.check("folder")
            self.assertEqual((folder["state"], folder["detail"]), ("warn", f"In {name}, whose sync can lock the files the containers write"))
            self.assertEqual(folder["actions"][0]["copy"], machine.CLONE)
        self.assertTrue(machine.report(fresh=True)["ready"], "a warning: the platform still starts")
        self.patch(machine.paths, "ROOT", Path("/Users/me/src/Box/gravitee-lab"))   # a folder of that name, no sync client's
        self.assertNotIn("folder", [c["id"] for c in machine.report(fresh=True)["checks"]])

    def test_short_memory_is_a_warning_that_lets_the_platform_start(self):
        self.answers[("docker", "info")] = (0, f"29.8.2|Docker Desktop|{6 * 2 ** 30}|/var/lib/docker", "")
        memory = self.check("memory")
        self.assertEqual((memory["state"], memory["detail"]), ("warn", "6 GB: give it 8 GB in the settings of Docker Desktop"))
        self.assertTrue(machine.report(fresh=True)["ready"])

    def test_a_port_another_program_holds_is_named_with_its_service_and_one_the_lab_holds_is_not(self):
        self.patch(machine, "_taken", return_value=True)
        ports = self.check("ports")
        self.assertEqual((ports["state"], ports["detail"]),
                         ("warn", "27017 (MongoDB) is used by another program: stop it before the platform starts"))

    def test_git_missing_on_a_mac_opens_apples_installer(self):
        self.answers[("git", "--version")] = (127, "", "not found")
        git = self.check("git")
        self.assertEqual((git["state"], git["actions"][0]["id"]), ("error", "install-clt"))
        machine.act("install-clt")
        self.assertIn(["/usr/bin/xcode-select", "--install"], self.ran)
        self.assertEqual(self.check("git")["state"], "working")

    def test_a_fix_that_does_not_run_here_is_refused(self):
        with self.assertRaises(CatalogError) as refused:
            machine.act("download-docker")
        self.assertEqual((refused.exception.code, refused.exception.status), ("machine_action", 400))

    def test_the_disk_is_where_docker_keeps_its_images(self):
        seen = []
        self.patch(machine.shutil, "disk_usage", side_effect=lambda where: seen.append(Path(where)) or type("U", (), {"free": 9e9})())
        self.patch(machine, "system", return_value="linux")
        disk = self.check("disk")
        self.assertEqual((disk["state"], disk["detail"]), ("warn", "9 GB free: the first start downloads about 15 GB"))
        self.assertEqual(seen[-1], Path("/var/lib/docker"))
