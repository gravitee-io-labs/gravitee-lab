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

    def record(self, reload):
        plan = self.folder / "plan.json"
        plan.write_text(json.dumps({"include": [JOB]}), encoding="utf-8")
        (self.folder / "results").mkdir(exist_ok=True)
        self.result("results/ok.json")
        return self.run_step(versions.record_main, str(self.catalog), "--plan", str(plan), "--results", str(self.folder / "results"),
                             project=BUILT, reload=reload)

    def test_the_record_writes_the_file_of_the_playground_in_the_catalog_given_and_reads_it_again(self):
        after = {"playgrounds": {"shop": {**BUILT["playgrounds"]["shop"], "tested": [SET, BUILT["playgrounds"]["shop"]["tested"][0]]}}}
        code, out, err = self.record(lambda: after)
        self.assertEqual((code, out, err), (0, "Shop: tested on APIM_VERSION 4.12.25\n", ""))
        self.assertEqual(self.compose.read_text(encoding="utf-8"), FILE.replace("  tested:\n", '''  tested:
    - APIM_VERSION: "4.12.25"
      APIM_REGISTRY: graviteeio
'''))

    def test_a_file_that_does_not_read_as_meant_is_put_back_and_nothing_is_pushed(self):
        for after in ({"playgrounds": {"shop": BUILT["playgrounds"]["shop"]}},
                      {"playgrounds": {"shop": {**BUILT["playgrounds"]["shop"], "name": "Shop 2", "tested": [SET, BUILT["playgrounds"]["shop"]["tested"][0]]}}}):
            with self.subTest(after=after):
                code, out, err = self.record(lambda after=after: after)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("nothing is written", err)
                self.assertEqual(self.compose.read_text(encoding="utf-8"), FILE)

    def test_a_file_written_another_way_is_left_out_and_said_and_the_others_are_recorded(self):
        self.compose.write_bytes(FILE.replace("\n", "\r\n").encode("utf-8"))
        target = self.folder / "output"
        target.write_text("", encoding="utf-8")
        with mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(target)}):
            code, out, err = self.record(lambda: BUILT)
        self.assertEqual((code, out), (0, ""))
        self.assertIn("playgrounds/shop/compose.yml is left out: its line ends are not LF", err)
        self.assertEqual("left_out=1\n", target.read_text(encoding="utf-8"))

    def git(self, *command):
        subprocess.run(["git", "-C", str(self.catalog), *command], check=True, capture_output=True)

    def test_the_release_raises_the_patch_number_past_the_newest_tag(self):
        (self.catalog / "catalog.yml").write_text("x-gravitee-catalog:\n  id: acme\n  version: 1.2.2\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
        code, out, _ = self.run_step(versions.release_main, str(self.catalog))
        self.assertEqual((code, out), (0, ""), "a catalog with no release is followed commit by commit: no tag")
        self.git("tag", "v1.2.2")
        code, out, _ = self.run_step(versions.release_main, str(self.catalog))
        self.assertEqual((code, out), (0, "1.2.3\n"))
        self.assertIn("version: 1.2.3", (self.catalog / "catalog.yml").read_text(encoding="utf-8"))

    def test_a_release_its_maintainers_prepared_takes_the_record_with_it(self):
        (self.catalog / "catalog.yml").write_text("x-gravitee-catalog:\n  id: acme\n  version: 1.3.0\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
        self.git("tag", "v1.2.2")
        code, out, _ = self.run_step(versions.release_main, str(self.catalog))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("version: 1.3.0", (self.catalog / "catalog.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
