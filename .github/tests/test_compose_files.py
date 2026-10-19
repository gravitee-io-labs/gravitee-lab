"""Conventions of the core's Compose file that need no running stack: what the gateway mounts and what the Alert Engine
says. The file is read as text. The conventions of the items are tested by the catalog that brings them."""

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


if __name__ == "__main__":
    unittest.main()
