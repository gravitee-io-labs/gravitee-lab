"""Every relative link of the documents of the project reaches a file, and every anchor reaches a heading."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = ("*.md", "docs/*.md", "lab-ui/*.md", "seeder/*.md", ".agents/skills/*/SKILL.md")


def slug(heading: str) -> str:
    """The anchor GitHub makes of a heading: lower case, no code marks or punctuation but - and _, spaces as dashes."""
    heading = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading))


def headings(path: Path) -> set:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    return {slug(found) for found in re.findall(r"^#{1,6}\s+(.*)$", text, re.M)}


class Links(unittest.TestCase):
    def test_every_relative_link_and_anchor_resolves(self):
        broken = []
        for pattern in PATTERNS:
            for file in sorted(ROOT.glob(pattern)):
                text = re.sub(r"```.*?```", "", file.read_text(encoding="utf-8"), flags=re.S)
                for found in re.finditer(r"\]\(([^)\s]+)\)", text):
                    link = found.group(1)
                    if re.match(r"(https?:|mailto:)", link):
                        continue
                    path, _, fragment = link.partition("#")
                    target = (file.parent / path).resolve() if path else file
                    where = f"{file.relative_to(ROOT)}: {link}"
                    if not target.exists():
                        broken.append(f"{where} (no such file)")
                    elif fragment and target.suffix == ".md" and fragment not in headings(target):
                        broken.append(f"{where} (no such heading)")
        self.assertEqual([], broken)


if __name__ == "__main__":
    unittest.main()
