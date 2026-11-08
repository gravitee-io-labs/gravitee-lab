"""Conventions of the core's Compose file that need no running stack: what the gateway mounts and what the health checks run.
The file is read as text. The conventions of the items are tested by the catalog that brings them."""

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


class HealthChecksTest(unittest.TestCase):
    """A setting is never pasted into the shell of a check or into a command line: a password with a space, an & or a $ would
    be split there, run or expanded. No check outlasts its own time."""

    def setUp(self):
        self.text = (ROOT / "docker-compose.yml").read_text()

    def test_no_setting_is_pasted_into_a_shell(self):
        for test in re.findall(r"^\s+test: (.*)$", self.text, re.M):
            self.assertNotRegex(test, r"(?<!\$)\$\{", test)   # $${...} is the container's own variable, read as it is
        for command in re.findall(r"^\s+command: (.*)$", self.text, re.M):
            if not command.startswith(("[", "*")):   # a list is no shell, an alias is the frames' command
                self.assertNotRegex(command, r"(?<!\$)\$\{", command)

    def test_a_check_asks_for_no_more_than_its_own_time(self):
        timeout = int(re.search(r"^x-healthcheck: &healthcheck\n(?:  .*\n)*?  timeout: (\d+)s", self.text, re.M).group(1))
        for asked in re.findall(r"[?&]timeout=(\d+)s", self.text):
            self.assertLess(int(asked), timeout)


if __name__ == "__main__":
    unittest.main()
