"""Nothing in the fixture catalogs is real, so no test leans on an item of a catalog. The hostile catalog is the trust tests'."""

import re
import unittest

from ..fixtures import DATA, SEEDER_PLACEHOLDER


class FixtureCatalogs(unittest.TestCase):

    def test_nothing_in_the_fixtures_names_a_real_address_organization_or_image(self):
        files = [file for file in sorted(DATA.rglob("*")) if file.is_file()
                 and file.relative_to(DATA).parts[0] in ("catalogs", "local", "expected.json", "README.md")
                 and file.relative_to(DATA).parts[:2] != ("catalogs", "hostile")]
        self.assertTrue(files)
        for file in files:
            text, where = file.read_text(encoding="utf-8"), file.relative_to(DATA).as_posix()
            for host in re.findall(r"https?://(?:[^/@\s]*@)?(\[[^\]]+\]|[^/:\s\"')]+)", text):
                own = host in {"localhost", "127.0.0.1", "[::1]", "host.docker.internal"} or "." not in host \
                    or host.endswith((".invalid", ".test", ".example"))
                self.assertTrue(own, f"{where}: {host}")
            for domain in re.findall(r"[\w.+-]+@([\w.-]+\.\w+)", text):
                self.assertTrue(domain.endswith((".invalid", ".test", ".example")), f"{where}: {domain}")
            for image in re.findall(r"(?m)^\s*image:\s*(\S+)", text) + re.findall(r"(?m)^FROM\s+(\S+)", text):
                repository = image.split(":")[0]
                own = repository in ("busybox", SEEDER_PLACEHOLDER) or repository.startswith("registry.example.invalid/")
                self.assertTrue(own, f"{where}: {image}")
