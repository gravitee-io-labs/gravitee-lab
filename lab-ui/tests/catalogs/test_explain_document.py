"""docs/catalog-rules.md is the table of `catalog.py explain`, written out: the page a failed check leads to says what the command says.
Write it again with WRITE_RULES_DOC=1 when a rule changes."""

import os
import unittest
from pathlib import Path

from lab_ui.catalogs import explain

FILE = Path(__file__).resolve().parents[3] / "docs" / "catalog-rules.md"


class Document(unittest.TestCase):
    def test_the_page_is_the_table(self):
        found = explain.document()
        if os.environ.get("WRITE_RULES_DOC"):
            FILE.write_text(found, encoding="utf-8")
        self.assertEqual(FILE.read_text(encoding="utf-8"), found, "run the tests with WRITE_RULES_DOC=1 to write docs/catalog-rules.md again")

    def test_every_code_has_its_heading_and_every_family_a_name(self):
        text = explain.document()
        for code in explain.codes():
            self.assertIn(f"\n### {code}\n", text)
        for code in explain.codes():
            family = code.rstrip("0123456789")
            self.assertIn(family, explain.FAMILY_NAMES, f"{code} has no family name")


if __name__ == "__main__":
    unittest.main()
