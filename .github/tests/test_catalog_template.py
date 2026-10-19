"""The template of a catalog repository (lab-ui/templates/catalog), as the engine's CI sees it: what `catalog.py new` renders is a
catalog that passes its own rules on day one, with the engine's pins, the engine's Dependabot reaching the template's workflows,
and nothing of the engine's owner, address or any tool in a file. The rules and the renderer are the Lab's own modules
(`lab_ui.catalogs.standard`, `lab_ui.catalogs.rules.workflows`); this file reads the template as text and renders it, and runs
nothing of it. No network, no Docker."""

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lab-ui"))

from lab_ui.catalogs import audit, standard  # noqa: E402
from lab_ui.catalogs.rules import commits, workflows  # noqa: E402

TEMPLATE = ROOT / "lab-ui" / "templates" / "catalog"
MANAGED = TEMPLATE / "managed"
PIN = re.compile(r"^\s*(?:-\s+)?uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\b", re.M)
PARAMS = {"id": "acme-hub", "name": "Acme Hub", "maintainers": "@acme/maintainers", "lab_repository": "acme/gravitee-lab",
          "lab_ref": "0123456789abcdef0123456789abcdef01234567", "license": "Apache-2.0"}
EVERYTHING = {"images": "ghcr.io/acme/lab-hub", "with": ["e2e"]}


def pins(folder: Path) -> dict:
    """{action: {commit, ...}} of every `uses:` of a full commit under the workflows and the actions of a folder."""
    found = {}
    for file in sorted([*(folder / ".github" / "workflows").glob("*.yml"), *(folder / ".github" / "actions").glob("*/action.yml")]):
        for action, commit in PIN.findall(file.read_text(encoding="utf-8")):
            found.setdefault(action, set()).add(commit)
    return found


class Pins(unittest.TestCase):

    def test_the_template_pins_every_action_to_a_full_commit(self):
        for file in sorted(MANAGED.rglob("*.yml")):
            for number, line in enumerate(file.read_text(encoding="utf-8").split("\n"), 1):
                if re.match(r"\s*(?:-\s+)?uses:", line) and "uses: ./" not in line:
                    self.assertRegex(line, PIN, f"{file.relative_to(TEMPLATE)}:{number}")

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

    def test_both_directories_hold_a_workflow_or_an_action(self):
        self.assertTrue(list((MANAGED / ".github" / "workflows").glob("*.yml")))
        self.assertTrue(list((MANAGED / ".github" / "actions").glob("*/action.yml")))

    def test_the_catalogs_own_dependabot_file_has_a_cooldown_and_the_three_ecosystems(self):
        text = (MANAGED / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("github-actions", "docker", "pip"):
            self.assertIn(f"package-ecosystem: {ecosystem}", text)
        self.assertEqual(text.count("default-days: 7"), 3)


class Rules(unittest.TestCase):

    def files(self, level):
        extra = EVERYTHING if level >= 3 else {}
        return standard.render_files({**PARAMS, **extra}, level)

    def test_every_managed_workflow_and_action_passes_every_workflow_rule_at_every_level(self):
        std = {"repository": PARAMS["lab_repository"], "ref": PARAMS["lab_ref"]}
        seen = 0
        for level in (1, 2, 3):
            for path, data in self.files(level).items():
                if path.startswith(".github/workflows/") or path.endswith("/action.yml"):
                    seen += 1
                    self.assertEqual(workflows.problems(path, data.decode("utf-8"), workflows.ALL, std), [], f"{path} at level {level}")
        self.assertGreaterEqual(seen, 6)

    def test_the_engines_generic_rules_hold_on_the_template_too(self):
        for path in sorted(MANAGED.rglob("*.yml")):
            relative = str(path.relative_to(MANAGED))
            if relative.startswith(".github/workflows/") or relative.endswith("/action.yml"):
                self.assertEqual(workflows.problems(relative, path.read_text(encoding="utf-8"), workflows.GENERIC), [], relative)

    def test_a_new_catalog_passes_every_rule_that_needs_no_docker_on_day_one(self):
        with tempfile.TemporaryDirectory() as folder:
            standard.create(Path(folder) / "hub", PARAMS)
            result = audit.run(Path(folder) / "hub", audit.Options(seeder_paths=("seeder",)))
        self.assertEqual([(each.code, each.path) for each in result.problems], [])
        self.assertEqual(result.skipped, [])

    def test_catalog_py_new_renders_what_the_template_renders(self):
        with tempfile.TemporaryDirectory() as folder:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = standard.main_new([str(Path(folder) / "hub"), "--id", "acme-hub", "--name", "Acme Hub", "--maintainers",
                                          "@acme/maintainers", "--license", "Apache-2.0", "--lab-repo", PARAMS["lab_repository"],
                                          "--lab-ref", PARAMS["lab_ref"]])
            self.assertEqual((code, err.getvalue()), (0, ""))
            made = {str(path.relative_to(Path(folder) / "hub")): path.read_bytes() for path in (Path(folder) / "hub").rglob("*")
                    if path.is_file()}
        self.assertEqual(made, standard.render_new(PARAMS))

    def test_the_golden_file_is_the_template(self):
        self.assertEqual(standard.golden_problems(), [], "refresh it: python3 -m tests.catalogs.test_standard --write, from lab-ui")


class Clean(unittest.TestCase):
    """No owner of the engine, no address of a catalog, no private name and no tool credit in the template."""

    OWNERS = re.compile(r"dobl1|graviteesource|gravitee-io|gravitee-lab-catalog|internal\.gravitee", re.I)
    LINKS = ("https://spdx.org/licenses/", "https://github.com/__LAB_REPOSITORY__", "http://localhost", "http://www.apache.org/licenses/",
             "https://www.gnu.org/licenses/", "https://fsf.org/", "https://www.apache.org")

    def files(self):
        for path in sorted(TEMPLATE.rglob("*")):
            if path.is_file():
                yield str(path.relative_to(TEMPLATE)), path.read_text(encoding="utf-8")

    def test_no_engine_owner_and_no_private_name(self):
        for name, text in self.files():
            self.assertIsNone(self.OWNERS.search(text), name)

    def test_no_address_but_the_labs_placeholder_and_the_texts_of_licenses(self):
        for name, text in self.files():
            for url in re.findall(r"https?://[^\s)>\"'`]+", text):
                self.assertTrue(url.startswith(self.LINKS), f"{name}: {url}")

    def test_no_tool_credit_and_no_co_author_trailer(self):
        trailer = "co-" + "authored-by"
        generated = "generated " + "with"
        for name, text in self.files():
            self.assertNotIn(trailer, text.lower(), name)
            self.assertNotIn(generated, text.lower(), name)
            self.assertIsNone(commits.NAMES.search(text), name)
            self.assertIsNone(commits.ADDRESSES.search(text.lower()), name)
            for line in text.split("\n"):
                self.assertIsNone(commits.CREDITS.search(line), f"{name}: {line}")

    def test_the_rendered_files_are_as_clean_as_the_template(self):
        for level in (1, 3):
            extra = EVERYTHING if level >= 3 else {}
            for path, data in standard.render_files({**PARAMS, **extra}, level).items():
                text = data.decode("utf-8")
                self.assertIsNone(self.OWNERS.search(text), path)
                self.assertIsNone(commits.NAMES.search(text), path)


if __name__ == "__main__":
    unittest.main()
