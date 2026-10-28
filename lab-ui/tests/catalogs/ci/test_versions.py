"""`catalog.py ci versions-plan`, `versions-record` and `versions-release`: the steps of the module versions.yml. Only what the plan
asked and passed is recorded, in the file of the playground of the catalog given, and the release raises the patch number."""

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs.ci import versions

SET = {"APIM_VERSION": "4.12.25", "APIM_REGISTRY": "graviteeio"}
JOB = {"item": "playground:shop", "versions": json.dumps(SET, sort_keys=True), "name": "Shop on APIM_VERSION 4.12.25"}
FILE = """x-gravitee:
  kind: playground
  name: Shop
  tested:
    - APIM_VERSION: "4.13.1"
      APIM_REGISTRY: graviteeio
  consoles: [shop]
"""
BUILT = {"playgrounds": {"shop": {"name": "Shop", "catalog": "acme", "tested": [{"APIM_VERSION": "4.13.1", "APIM_REGISTRY": "graviteeio"}],
                                  "requires": {"images": {"APIM_VERSION": "4.12"}}}}}


class Steps(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.catalog = self.folder / "catalog"
        (self.catalog / "playgrounds" / "shop").mkdir(parents=True)
        self.compose = self.catalog / "playgrounds" / "shop" / "compose.yml"
        self.compose.write_text(FILE, encoding="utf-8")
        found = mock.patch.object(versions, "_catalog", side_effect=lambda path: (Path(path).resolve(), "acme"))
        found.start()
        self.addCleanup(found.stop)

    def result(self, name, item=JOB["item"], found=SET, code=0):
        path = self.folder / name
        path.write_text(json.dumps({"item": item, "versions": json.dumps(found), "code": code}), encoding="utf-8")
        return str(path)

    def run_step(self, main, *argv, **seams):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), stdout=out, stderr=err, **seams)
        return code, out.getvalue(), err.getvalue()

    def test_the_plan_names_each_job(self):
        tags = {"APIM_VERSION": ["4.13.1", "4.12.25", "4.11.31"]}
        code, out, _ = self.run_step(versions.plan_main, str(self.catalog), project=BUILT, tags=lambda built, key, registry: tags[key])
        self.assertEqual((code, out), (0, "Shop on APIM_VERSION 4.12.25\n"), "4.11 is below what it requires")

    def test_only_a_job_of_the_plan_that_ended_well_counts(self):
        err = io.StringIO()
        found = versions.passed([JOB], [self.result("ok.json"), self.result("failed.json", code=1),
                                        self.result("other.json", item="playground:bank"),
                                        self.result("more.json", found={**SET, "PATH": "/tmp"})], err)
        self.assertEqual(found, {"playground:shop": [SET]})
        self.assertEqual(err.getvalue().count("Not a job of the plan"), 2)

    def test_the_record_writes_the_file_of_the_playground_in_the_catalog_given(self):
        plan = self.folder / "plan.json"
        plan.write_text(json.dumps({"include": [JOB]}), encoding="utf-8")
        (self.folder / "results").mkdir()
        self.result("results/ok.json")
        code, out, _ = self.run_step(versions.record_main, str(self.catalog), "--plan", str(plan), "--results", str(self.folder / "results"),
                                     project=BUILT)
        self.assertEqual((code, out), (0, "Shop: tested on APIM_VERSION 4.12.25\n"))
        self.assertEqual(self.compose.read_text(encoding="utf-8"), FILE.replace("  tested:\n", '''  tested:
    - APIM_VERSION: "4.12.25"
      APIM_REGISTRY: graviteeio
'''))

    def test_the_release_raises_the_patch_number_past_the_newest_tag(self):
        (self.catalog / "catalog.yml").write_text("x-gravitee-catalog:\n  id: acme\n  version: 1.2.0\n", encoding="utf-8")
        for command in (["init", "-q"], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x"],
                        ["tag", "v1.2.2"]):
            subprocess.run(["git", "-C", str(self.catalog), *command], check=True, capture_output=True)
        with mock.patch.dict("os.environ", {"GITHUB_OUTPUT": ""}):
            code, out, _ = self.run_step(versions.release_main, str(self.catalog))
        self.assertEqual((code, out), (0, "1.2.3\n"))
        self.assertIn("version: 1.2.3", (self.catalog / "catalog.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
