"""library/machine.py: what this machine needs to run the lab, each need with its state and its fix, from what the commands
answer (stood in for here): Docker not there, there and stopped, with a Compose older than 5.0, short of memory; the ports of
the platform held by another program; Git missing. A fix runs here only on a Mac; a link or a command to copy otherwise."""

from pathlib import Path

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
}


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
        self.patch(machine, "docker_app", return_value=("Docker", "Docker Desktop"))
        self.patch(machine, "core_ports", return_value={9200: "Elasticsearch", 27017: "MongoDB"})
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
        self.answers[("open", "-a")] = (0, "", "")
        machine.act("open-docker")
        self.assertIn(["open", "-a", "Docker"], self.ran)
        self.assertEqual((self.check("docker")["state"], self.check("docker")["detail"]), ("working", "Docker Desktop is starting"))

    def test_on_linux_the_command_that_starts_docker_is_given_to_copy(self):
        self.patch(machine, "system", return_value="linux")
        self.patch(machine, "docker_app", return_value=None)
        self.answers[("docker", "info")] = (1, "", "Cannot connect to the Docker daemon")
        docker = self.check("docker")
        self.assertEqual(docker["actions"], [{"id": "start-docker", "label": "Copy the command", "primary": True,
                                              "copy": "sudo systemctl start docker"}])

    def test_a_compose_older_than_5_keeps_the_platform_from_starting(self):
        self.answers[("docker", "compose")] = (0, "2.29.7", "")
        docker = self.check("docker")
        self.assertEqual((docker["state"], docker["detail"]),
                         ("error", "Compose 2.29.7 is too old: update Docker Desktop to get Compose 5.0 or later"))

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
