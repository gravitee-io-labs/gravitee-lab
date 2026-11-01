"""Conventions of the core's Compose file that need no running stack: what the gateway mounts, what the Alert Engine
says and what the health checks run. The file is read as text. The conventions of the items are tested by the catalog that
brings them."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def service_block(text, name):
    """The lines of a service in docker-compose.yml, up to the next service."""
    return re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][\w-]*:\n|^# |^[a-z]|\Z)", text, re.M | re.S).group(1)


class GatewayMountsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = service_block((ROOT / "docker-compose.yml").read_text(), "apim-gateway")

    def test_the_gateway_mounts_the_keystore_and_the_ca_certificate_one_by_one(self):
        mounts = re.findall(r"^\s+- (\./certs/mtls\S*)$", self.gateway, re.M)
        self.assertEqual(["./certs/mtls/server.p12:/opt/graviteeio-gateway/mtls/server.p12:ro",
                          "./certs/mtls/ca.pem:/opt/graviteeio-gateway/mtls/ca.pem:ro"], mounts)

    def test_what_the_gateway_reads_is_what_it_mounts(self):
        paths = re.findall(r"^\s+gravitee_servers_1_ssl_(?:keystore|truststore)_path: (\S+)$", self.gateway, re.M)
        self.assertEqual(["/opt/graviteeio-gateway/mtls/server.p12", "/opt/graviteeio-gateway/mtls/ca.pem"], paths)
        for path in paths:
            self.assertIn(f":{path}:ro", self.gateway)


class AlertEngineTest(unittest.TestCase):
    def test_it_says_that_it_needs_a_license(self):
        block = service_block((ROOT / "docker-compose.yml").read_text(), "alert-engine")
        description = re.search(r"io\.gravitee\.description: (.*)", block).group(1)
        self.assertIn("needs an Enterprise license", description)


class HealthChecksTest(unittest.TestCase):
    """A setting is never pasted into the shell of a check or into a command line: a password with a space, an & or a $ would
    be split there, run or expanded. A JVM has minutes for its first start, and no check outlasts its own time."""

    def setUp(self):
        self.text = (ROOT / "docker-compose.yml").read_text()

    def test_no_setting_is_pasted_into_a_shell(self):
        for test in re.findall(r"^\s+test: (.*)$", self.text, re.M):
            self.assertNotRegex(test, r"(?<!\$)\$\{", test)   # $${...} is the container's own variable, read as it is
        for command in re.findall(r"^\s+command: (.*)$", self.text, re.M):
            if not command.startswith(("[", "*")):   # a list is no shell, an alias is the frames' command
                self.assertNotRegex(command, r"(?<!\$)\$\{", command)

    def test_a_jvm_has_five_minutes_for_its_first_start(self):
        anchor = re.search(r"^x-jvm-healthcheck: &jvm-healthcheck\n((?:  .*\n)+)", self.text, re.M).group(1)
        self.assertIn("  start_period: 300s\n", anchor)
        for name in ("alert-engine", "apim-management-api", "apim-gateway", "am-management-api", "am-gateway"):
            self.assertIn("      <<: *jvm-healthcheck\n", service_block(self.text, name), name)

    def test_a_check_asks_for_no_more_than_its_own_time(self):
        timeout = int(re.search(r"^x-healthcheck: &healthcheck\n(?:  .*\n)*?  timeout: (\d+)s", self.text, re.M).group(1))
        for asked in re.findall(r"[?&]timeout=(\d+)s", self.text):
            self.assertLess(int(asked), timeout)

    def test_mongodb_starts_node_js_twice_a_minute_once_it_runs(self):
        self.assertIn("      interval: 30s", service_block(self.text, "mongodb"))
        self.assertRegex(self.text, r"x-healthcheck: &healthcheck\n(?:  .*\n)*?  start_interval: 2s")

    def test_a_mongodb_that_does_not_answer_is_unhealthy_within_minutes(self):
        # Docker gives up after its start period and `retries` failed checks in a row, one every `interval`
        check = self.healthcheck("mongodb")
        self.assertLessEqual(check["start_period"] + check["retries"] * check["interval"], 300, check)   # it never answers
        self.assertLessEqual(check["retries"] * check["interval"], 180, check)   # it stops answering once it ran

    def healthcheck(self, name):
        """The timing of a service's health check, in seconds and counts: the shared anchor, with what the service sets."""
        anchor = re.search(r"^x-healthcheck: &healthcheck\n((?:  .*\n)+)", self.text, re.M).group(1)
        own = re.search(r"^    healthcheck:\n((?:      .*\n)+)", service_block(self.text, name), re.M).group(1)
        timing = r"^\s+(interval|retries|start_period): (\d+)s?\b"
        return {key: int(value) for key, value in re.findall(timing, anchor + own, re.M)}


if __name__ == "__main__":
    unittest.main()
