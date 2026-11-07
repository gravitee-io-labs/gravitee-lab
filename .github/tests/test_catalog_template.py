"""The template of a catalog repository against the engine's own files, read as text: the pins they share, one commit for each action,
and the engine's Dependabot reaching the template. What the template renders and whether it passes its rules is
`lab-ui/tests/catalogs/test_standard.py`'s."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGED = ROOT / "lab-ui" / "templates" / "catalog" / "managed"
PIN = re.compile(r"^\s*(?:-\s+)?uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\b", re.M)


def pins(folder: Path) -> dict:
    """{action: {commit, ...}} of every `uses:` of a full commit under the workflows and the actions of a folder."""
    found = {}
    for file in sorted([*(folder / ".github" / "workflows").glob("*.yml"), *(folder / ".github" / "actions").glob("*/action.yml")]):
        for action, commit in PIN.findall(file.read_text(encoding="utf-8")):
            found.setdefault(action, set()).add(commit)
    return found


class Pins(unittest.TestCase):

    def test_the_pin_of_an_action_the_engine_uses_too_is_the_engines(self):
        engine, template = pins(ROOT), pins(MANAGED)
        shared = sorted(set(engine) & set(template))
        self.assertIn("actions/checkout", shared)
        self.assertIn("actions/setup-python", shared)
        for action in shared:
            self.assertEqual(template[action], engine[action], f"{action}: the template and the engine pin another commit")

    def test_one_commit_for_each_action_in_the_template(self):
        for action, commits_ in pins(MANAGED).items():
            self.assertEqual(len(commits_), 1, f"{action} is pinned to {len(commits_)} commits in the template")


class Dependabot(unittest.TestCase):

    def test_the_engines_dependabot_reaches_the_template(self):
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        block = text.split("package-ecosystem: github-actions", 1)[1].split("package-ecosystem:", 1)[0]
        listed = re.findall(r'^\s*-\s+"([^"]+)"', block, re.M)
        for directory in ("/lab-ui/templates/catalog/managed", "/lab-ui/templates/catalog/managed/.github/actions/*"):
            self.assertIn(directory, listed)

    def test_the_catalogs_own_dependabot_file_has_a_cooldown_and_the_three_ecosystems(self):
        text = (MANAGED / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("github-actions", "docker", "pip"):
            self.assertIn(f"package-ecosystem: {ecosystem}", text)
        self.assertEqual(text.count("default-days: 7"), 3)


if __name__ == "__main__":
    unittest.main()
