"""The commands that the documentation tells a person to run exist: every `make TARGET` of a Markdown file of the engine is a target
of the Makefile (a target that was removed leaves its page behind otherwise)."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
         *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "lab-ui").glob("*.md")), *sorted((ROOT / "seeder").glob("*.md"))]
MAKE = re.compile(r"`make ([a-z][a-z0-9-]*)")
# The targets of a catalog's Makefile, which the pages about catalogs name
CATALOG_MAKEFILE = ROOT / "lab-ui" / "templates" / "catalog" / "managed" / ".catalog" / "standard.mk"


def targets(makefile):
    listed = subprocess.run(["make", "-pRrq", "-f", str(makefile), ":"], capture_output=True, text=True, cwd=ROOT).stdout
    return set(re.findall(r"^([a-z][a-z0-9-]*):", listed, re.M))


class Commands(unittest.TestCase):
    def test_every_make_target_the_documentation_names_is_in_the_makefile(self):
        have, missing = targets(ROOT / "Makefile") | targets(CATALOG_MAKEFILE), []
        for file in FILES:
            if not file.is_file():
                continue
            for name in sorted(set(MAKE.findall(file.read_text(encoding="utf-8")))):
                if name not in have:
                    missing.append(f"{file.relative_to(ROOT)}: make {name}")
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
