"""`catalog.py ci e2e-plan` and `catalog.py ci e2e-gate` (E2E01 to E2E05): the plan of the module that holds the CI license.

The catalog is a real git repository on `main`, and the pull request is a clone of it with a branch of commits on it, the way the
workflow checks it out into its own folder. The plan reads that folder as data. These tests prove what the standard says of the module:
one job for each item and each set of versions; an item's own service never reads the license (a marker that only the run knows is put
in its place and looked for in the unit's own services, so an alias, a block scalar or a build argument cannot hide it, and an overlay on
a core service is the one thing that may read it); a pull request is tried only from main; a head that moved is refused, in the plan and
again in the job that holds the license; a word that is not an item, starts with a dash or is an option is refused; and a change to
`.github/`, `.catalog/`, the Makefile or `catalog.yml` is not tried until a maintainer has merged it."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui import paths
from lab_ui.catalogs import ci, explain
from lab_ui.catalogs.ci import e2e
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests.data.rules import support

MAIN = "refs/heads/main"


def catalog_yml(ns="acme-hub", extra=""):
    return (f"x-gravitee-catalog:\n  schema: 1\n  id: {ns}\n  name: Acme Hub\n  description: Demos\n  version: 1.0.0\n  lab: 1\n{extra}")


UNITS = {"playgrounds/bank/compose.yml": "services: {}\n", "playgrounds/bank/README.md": "# Bank\n",
         "playgrounds/bank/apps/web/app.py": "print(1)\n", "addons/mock/compose.yml": "services: {}\n",
         "addons/hybrid/compose.yml": "services: {}\n", "setups/pair.yml": "include: []\n"}
BASE = {"catalog.yml": catalog_yml(), "README.md": "# Hub\n", "Makefile": "include .catalog/standard.mk\n",
        ".catalog/standard.mk": "# Managed\n", ".github/workflows/ci.yml": "name: CI\n", **UNITS}


def unit(kind, name, tested=(), own=(), leaks=(), resolve="auto", folder=""):
    """A loaded unit. `leaks` are the services (own or not) whose resolved definition holds the license when it is set, which is what a
    service that reads the variable looks like to the plan."""
    services = {service: {"image": "example.test/lab/x:1"} for service in own}
    services["apim-gateway"] = {"image": "graviteeio/apim-gateway:4.12.0"}

    def read(variables):
        marker = variables.get("GRAVITEE_LICENSE", "")
        return {"services": {name_: ({**definition, "environment": {"LICENSE": marker}} if name_ in leaks else definition)
                             for name_, definition in services.items()}}
    return ci.Loaded(kind, name, name, ci.unit_path(kind, name), folder, {"services": services}, tuple(own), tuple(tested), None,
                     read if resolve == "auto" else resolve)


class Fixture(support.WithGit):

    def setUp(self):
        super().setUp()
        self.repo = self.work(files=BASE)
        self.main_sha = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.main_sha)
        self.output = self.holder / "github-output"
        self.out, self.err = io.StringIO(), io.StringIO()
        self.table = {}
        self.event = None

    # the pull request: a clone of main with a branch
    def clone(self, change=None, message="Change", name="change"):
        """The pull request: a clone of main (so `origin/main` is there) with `change` ({path: text or None}) committed on a branch."""
        folder = self.holder / name
        self.git(self.holder, "clone", "-q", str(self.repo), name)
        self.git(folder, "checkout", "-q", "-b", "pr")
        for path, content in (change or {}).items():
            if content is None:
                (folder / path).unlink()
            else:
                self.put(folder, path, content)
        self.git(folder, "add", "-A")
        self.git(folder, "commit", "-q", "--allow-empty", "-m", message)
        self.change = folder
        self.head = self.git(folder, "rev-parse", "HEAD").strip()
        return folder

    def loaded(self, key, **more):
        kind, _, name = key.partition(":")
        return unit(kind, name, **more)

    def loader(self):
        def load(folder, names):
            wanted = [(kind, name) for kind, name in names]
            return ci.Loading([self.table.get(f"{kind}:{name}", unit(kind, name)) for kind, name in wanted
                               if f"{kind}:{name}" not in getattr(self, "refused", {})],
                              [self.refused[f"{kind}:{name}"] for kind, name in wanted if f"{kind}:{name}" in getattr(self, "refused", {})], [])
        return load

    def plan(self, *argv, words=(), change=True, sha=None, ref=MAIN, default_ns="acme-hub", **seams):
        extra = ["--github-output"]
        if change and getattr(self, "change", None):
            extra += ["--change", str(self.change), "--sha", sha or self.head]
        extra += list(argv)
        if words:
            extra += ["--"] + list(words)
        env = {"GITHUB_OUTPUT": str(self.output), "GITHUB_REF": ref if ref is not None else ""}
        if self.event:
            env["GITHUB_EVENT_PATH"] = self.event
        with mock.patch.object(ci, "LOADER", self.loader()), mock.patch.dict(os.environ, env), \
                mock.patch.object(paths, "default_ns", return_value=default_ns):
            return e2e.plan_main([str(self.repo), *extra], self.out, self.err, **seams)

    def outputs(self):
        try:
            return dict(line.split("=", 1) for line in self.output.read_text(encoding="utf-8").splitlines())
        except FileNotFoundError:
            return {}

    def jobs(self):
        return json.loads(self.outputs()["matrix"])["include"]


class Matrix(Fixture):

    def test_one_job_for_each_item_and_each_set_of_versions_or_one_on_the_defaults(self):
        self.table = {"playground:bank": self.loaded("playground:bank", tested=({"APIM_VERSION": "4.12.0"}, {"APIM_VERSION": "4.13.0"})),
                      "addon:mock": self.loaded("addon:mock")}
        self.assertEqual(self.plan("--all", change=False), 0, self.err.getvalue() + self.out.getvalue())
        self.assertEqual(self.jobs(), [
            {"item": "addon:hybrid", "name": "addon:hybrid (core defaults)", "tested": ""},
            {"item": "addon:mock", "name": "addon:mock (core defaults)", "tested": ""},
            {"item": "playground:bank", "name": "playground:bank (versions 0)", "tested": "0"},
            {"item": "playground:bank", "name": "playground:bank (versions 1)", "tested": "1"},
            {"item": "setup:pair", "name": "setup:pair (core defaults)", "tested": ""}])
        self.assertEqual(self.outputs()["count"], "5")

    def test_the_outputs_are_two_lines_of_one_line_json_and_a_number(self):
        self.assertEqual(self.plan("--all", change=False), 0)
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual([line.split("=", 1)[0] for line in text.splitlines()], ["matrix", "count"])
        self.assertEqual(text.count("\n"), 2)
        self.assertEqual(set(json.loads(self.outputs()["matrix"])), {"include"})

    def test_an_item_of_a_catalog_that_is_not_the_first_is_named_with_the_catalog(self):
        self.assertEqual(self.plan("--all", change=False, default_ns="gravitee"), 0)
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:@acme-hub/hybrid", "addon:@acme-hub/mock",
                                                                  "playground:@acme-hub/bank", "setup:@acme-hub/pair"])
        self.assertEqual(self.jobs()[2]["name"], "playground:@acme-hub/bank (core defaults)")

    def test_a_scheduled_run_tries_every_item_of_main_and_asks_no_pull_request(self):
        self.assertEqual(self.plan("--all", change=False, ref="refs/heads/main"), 0)
        self.assertEqual(self.outputs()["count"], "4")

    def test_changed_items_are_the_ones_the_pull_request_touches(self):
        self.clone({"playgrounds/bank/apps/web/app.py": "print(2)\n", "addons/mock/README.md": "# Mock\n", "docs/notes.md": "x\n"})
        self.assertEqual(self.plan("--changed"), 0, self.out.getvalue() + self.err.getvalue())
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:mock", "playground:bank"])

    def test_the_pull_request_is_read_where_it_was_checked_out_and_not_where_main_is(self):
        self.clone({"addons/new-one/compose.yml": "services: {}\n"})
        asked = []
        original = self.loader()

        def load(folder, names):
            asked.append(folder)
            return original(folder, names)
        with mock.patch.object(ci, "LOADER", load), mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output), "GITHUB_REF": MAIN}), \
                mock.patch.object(paths, "default_ns", return_value="acme-hub"):
            code = e2e.plan_main([str(self.repo), "--changed", "--change", str(self.change), "--sha", self.head, "--github-output"],
                                 self.out, self.err)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [str(self.change)])
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:new-one"])

    def test_an_item_the_pull_request_deletes_has_nothing_to_try(self):
        self.clone({"addons/mock/compose.yml": None, "addons/mock/README.md": "# gone\n"})
        (self.change / "addons/mock/README.md").unlink()
        self.git(self.change, "add", "-A")
        self.git(self.change, "commit", "-q", "-m", "More")
        self.head = self.git(self.change, "rev-parse", "HEAD").strip()
        self.assertEqual(self.plan("--changed"), 0)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.outputs()["count"], "0")

    def test_a_pull_request_that_touches_no_item_has_no_job(self):
        self.clone({"docs/guide.md": "# guide\n"})
        self.assertEqual(self.plan("--changed"), 0)
        self.assertEqual((self.jobs(), self.outputs()["count"]), ([], "0"))
        self.assertIn("no item to try", self.out.getvalue())

    def test_named_items_are_tried_whatever_changed(self):
        self.clone({"docs/guide.md": "# guide\n"})
        self.assertEqual(self.plan("--items", words=["playground:bank", "addon:@acme-hub/mock", "playground:bank"]), 0)
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:mock", "playground:bank"])

    def test_changed_needs_a_pull_request_to_measure(self):
        self.assertEqual(self.plan("--changed", change=False), 2)
        self.assertIn("--changed measures a pull request", self.err.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_a_matrix_of_more_jobs_than_github_runs_is_refused(self):
        files = {f"addons/a{number}/compose.yml": "services: {}\n" for number in range(300)}
        for path, text in files.items():
            self.put(self.repo, path, text)
        self.assertEqual(self.plan("--all", change=False), 2)
        self.assertIn("a matrix of GitHub runs 256 at most", self.err.getvalue())

    def test_the_stage_words_reach_the_modules(self):
        self.assertEqual(ci.STAGES["e2e-plan"], ("e2e", "plan_main"))
        self.assertEqual(ci.STAGES["e2e-gate"], ("e2e", "gate_main"))
        self.assertEqual(ci_main(["e2e-plan"], io.StringIO(), io.StringIO()), 2)
        self.assertEqual(ci_main(["e2e-gate"], io.StringIO(), io.StringIO()), 2)


class License(Fixture):
    """E2E01."""

    def test_an_own_service_that_reads_the_license_is_refused_and_nothing_is_planned(self):
        self.table = {"playground:bank": self.loaded("playground:bank", own=("bakery-web", "bakery-api"), leaks=("bakery-web",))}
        self.assertEqual(self.plan("--all", change=False), 1)
        self.assertIn("E2E01", self.out.getvalue())
        self.assertIn("playground:bank: bakery-web reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it.",
                      self.out.getvalue())
        self.assertNotIn("bakery-api reads", self.out.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_every_own_service_that_reads_it_is_said(self):
        self.table = {"addon:mock": self.loaded("addon:mock", own=("a", "b", "c"), leaks=("a", "c"))}
        self.plan("--all", change=False)
        text = self.out.getvalue()
        self.assertIn("addon:mock: a reads GRAVITEE_LICENSE.", text)
        self.assertIn("addon:mock: c reads GRAVITEE_LICENSE.", text)
        self.assertNotIn("b reads", text)

    def test_an_overlay_on_a_core_service_may_read_it(self):
        self.table = {"addon:hybrid": self.loaded("addon:hybrid", own=("hybrid-agent",), leaks=("apim-gateway",))}
        self.assertEqual(self.plan("--all", change=False), 0, self.out.getvalue())
        self.assertIn("addon:hybrid (core defaults)", [job["name"] for job in self.jobs()])

    def test_the_item_is_named_the_way_the_lab_names_it(self):
        self.table = {"addon:mock": self.loaded("addon:mock", own=("a",), leaks=("a",))}
        self.plan("--all", change=False, default_ns="gravitee")
        self.assertIn("addon:@acme-hub/mock: a reads GRAVITEE_LICENSE.", self.out.getvalue())

    def test_the_marker_is_made_for_the_run_and_is_never_the_license(self):
        seen = []

        def resolve(variables):
            seen.append(dict(variables))
            return {"services": {}}
        self.table = {"addon:mock": self.loaded("addon:mock", own=("a",), resolve=resolve)}
        with mock.patch.dict(os.environ, {"GRAVITEE_LICENSE": "the-real-license-" + "x" * 30}):
            self.plan("--all", change=False)
            self.plan("--all", change=False)
        self.assertEqual(len(seen), 2)
        self.assertEqual(list(seen[0]), ["GRAVITEE_LICENSE"])
        self.assertNotEqual(seen[0]["GRAVITEE_LICENSE"], seen[1]["GRAVITEE_LICENSE"])
        for each in seen:
            self.assertRegex(each["GRAVITEE_LICENSE"], r"LABMARK-[0-9a-f]{24}")
            self.assertNotIn("the-real-license", each["GRAVITEE_LICENSE"])

    def test_a_marker_in_any_part_of_an_own_service_is_a_read(self):
        shapes = {"command": {"command": ["sh", "-c", "echo {marker}"]}, "build argument": {"build": {"args": {"K": "{marker}"}}},
                  "env_file inlined": {"environment": {"A": "x", "K": "{marker}"}}, "label": {"labels": {"k": "{marker}"}},
                  "nested": {"deploy": {"resources": {"x": ["{marker}"]}}}}
        for label, shape in shapes.items():
            with self.subTest(shape=label):
                def resolve(variables, shape=shape):
                    text = json.dumps(shape).replace("{marker}", variables["GRAVITEE_LICENSE"])
                    return {"services": {"a": json.loads(text)}}
                self.table = {"addon:mock": self.loaded("addon:mock", own=("a",), resolve=resolve)}
                self.out = io.StringIO()
                self.assertEqual(self.plan("--all", change=False), 1)
                self.assertIn("addon:mock: a reads GRAVITEE_LICENSE.", self.out.getvalue())

    def test_a_unit_that_cannot_be_read_again_cannot_be_tried_with_a_license(self):
        self.table = {"addon:mock": self.loaded("addon:mock", own=("a",), resolve=None)}
        self.assertEqual(self.plan("--all", change=False), 2)
        self.assertIn("cannot be read again with the license variable set", self.err.getvalue())

        def explode(variables):
            raise RuntimeError("Compose said no\n::error::forged")
        self.table = {"addon:mock": self.loaded("addon:mock", own=("a",), resolve=explode)}
        self.err = io.StringIO()
        self.assertEqual(self.plan("--all", change=False), 2)
        self.assertIn("could not be read again to look for the license", self.err.getvalue())
        self.assertNotIn("\n::error::", self.err.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_the_summary_says_which_units_reference_the_license_at_all(self):
        self.put(self.repo, "addons/hybrid/compose.yml", "services:\n  apim-gateway:\n    environment:\n      K: ${GRAVITEE_LICENSE:-}\n")
        self.put(self.repo, "addons/mock/compose.yml", "services:\n  mock:\n    environment:\n      K: $${GRAVITEE_LICENSE}\n")
        self.put(self.repo, "playgrounds/bank/seed/note.txt", "uses $GRAVITEE_LICENSE_FILE and ${GRAVITEE_LICENSE_X}\n")
        self.put(self.repo, "setups/pair.yml", "include: []\n")
        self.table = {"addon:hybrid": self.loaded("addon:hybrid", folder=str(self.repo / "addons/hybrid"))}
        self.assertEqual(self.plan("--all", change=False), 0, self.out.getvalue())
        self.assertIn("Surface reads: license (addon:hybrid)", self.out.getvalue())
        self.assertNotIn("Surface reads: license (addon:mock)", self.out.getvalue())
        self.assertNotIn("Surface reads: license (playground:bank)", self.out.getvalue())

    def test_a_reference_in_another_file_of_the_units_folder_counts_for_the_summary(self):
        self.put(self.repo, "addons/mock/extra.yml", "services:\n  apim-gateway:\n    environment:\n      K: ${GRAVITEE_LICENSE}\n")
        self.table = {"addon:mock": self.loaded("addon:mock", folder=str(self.repo / "addons/mock"))}
        self.plan("--all", change=False)
        self.assertIn("Surface reads: license (addon:mock)", self.out.getvalue())

    def test_the_reference_is_read_the_way_compose_reads_it(self):
        for text, expected in (("${GRAVITEE_LICENSE}", True), ("${GRAVITEE_LICENSE:-}", True), ("$GRAVITEE_LICENSE", True),
                               ("$${GRAVITEE_LICENSE}", False), ("$$$GRAVITEE_LICENSE", True), ("$$$${GRAVITEE_LICENSE}", False),
                               ("${GRAVITEE_LICENSE_FILE}", False), ("GRAVITEE_LICENSE", False), ("gravitee_license_key: x", False),
                               ("${gravitee_license}", False), ("a ${OTHER:-${GRAVITEE_LICENSE}} b", True),
                               ("{GRAVITEE_LICENSE}", False), ("GRAVITEE_LICENSE$", False), ("$GRAVITEE_LICENSE_", False),
                               ("$GRAVITEE_LICENSE.", True), ("x$GRAVITEE_LICENSE", True), ("", False)):
            with self.subTest(text=text):
                self.assertEqual(e2e.references_license(text), expected)

    def test_a_line_of_a_megabyte_is_read_in_under_a_second(self):
        for text, expected in (("$" * 1_000_000, False), ("$" * 999_999 + "GRAVITEE_LICENSE", True), ("$" * 1_000_000 + "GRAVITEE_LICENSE", False),
                               ("${GRAVITEE_LICENSE" * 50_000, True), ("$${GRAVITEE_LICENSE" * 50_000, False),
                               ("{" * 1_000_000 + "GRAVITEE_LICENSE", False), ("GRAVITEE_LICENSE_" * 60_000, False),
                               ("x" * 1_000_000, False)):
            with self.subTest(start=text[:14], size=len(text)):
                found, spent = support.timed(e2e.references_license, text)
                self.assertEqual(found, expected)
                self.assertLess(spent, 1.0)

    def test_a_unit_that_needs_a_setting_and_does_not_mention_the_license_is_planned_on_the_defaults(self):
        needs = ci.Refused("addon", "mock", "addons/mock/compose.yml", (
            "Cannot be read: error while interpolating services.a.environment.KEY: required variable KEY is missing a value",))
        self.refused = {"addon:mock": needs}
        self.assertEqual(self.plan("--all", change=False), 0, self.out.getvalue() + self.err.getvalue())
        self.assertIn("addon:mock (core defaults)", [job["name"] for job in self.jobs()])
        self.assertIn("addon:mock needs settings: it is planned on the defaults and the run will say it was not tried", self.out.getvalue())

    def test_a_unit_that_needs_a_setting_and_mentions_the_license_stops_the_plan(self):
        needs = ci.Refused("addon", "mock", "addons/mock/compose.yml", (
            "Cannot be read: error while interpolating services.a.environment.KEY: required variable KEY is missing a value",))
        self.refused = {"addon:mock": needs}
        self.put(self.repo, "addons/mock/compose.yml", "services:\n  a:\n    environment:\n      K: ${KEY:?set it}\n      L: ${GRAVITEE_LICENSE}\n")
        self.assertEqual(self.plan("--all", change=False), 2)
        self.assertIn("addon:mock needs a setting Compose cannot resolve without one, and its files reference GRAVITEE_LICENSE",
                      self.err.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_a_unit_the_trust_policy_blocks_ends_the_plan_and_is_never_left_out(self):
        self.refused = {"addon:mock": ci.Refused("addon", "mock", "addons/mock/compose.yml", ("Mounts the Docker socket",), "T10")}
        self.assertEqual(self.plan("--all", change=False), 1)
        self.assertIn("T10", self.out.getvalue())
        self.assertIn("was not tried", self.out.getvalue())
        self.assertEqual(self.outputs(), {})


class Inputs(Fixture):
    """E2E02 to E2E05."""

    def test_e2e02_a_pull_request_is_tried_from_the_workflow_of_main(self):
        self.clone({"addons/mock/README.md": "x\n"})
        for ref in ("refs/heads/feature", "refs/pull/12/merge", "refs/heads/main2", "refs/heads/maine", "main", "", None,
                    "refs/tags/v1.0.0"):
            with self.subTest(ref=ref):
                self.out = io.StringIO()
                self.assertEqual(self.plan("--changed", ref=ref), 1)
                self.assertIn("E2E02", self.out.getvalue())
                self.assertIn("A pull request is tried from the workflow of main. Fix: run this workflow on main.", self.out.getvalue())
                self.assertEqual(self.outputs(), {})
        self.assertEqual(self.plan("--changed", ref="refs/heads/main"), 0)

    def test_the_ref_may_be_given_and_main_alone_is_not_asked_for_it(self):
        self.assertEqual(self.plan("--all", change=False, ref="refs/heads/anything"), 0)
        self.clone({"addons/mock/README.md": "x\n"})
        with mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/feature", "GITHUB_OUTPUT": str(self.output)}), \
                mock.patch.object(ci, "LOADER", self.loader()), mock.patch.object(paths, "default_ns", return_value="acme-hub"):
            code = e2e.plan_main([str(self.repo), "--changed", "--change", str(self.change), "--sha", self.head, "--ref", MAIN,
                                  "--github-output"], self.out, self.err)
        self.assertEqual(code, 0)

    def test_e2e03_a_head_that_moved_is_not_the_commit_that_was_read(self):
        self.clone({"addons/mock/README.md": "x\n"})
        read = self.head
        self.git(self.change, "commit", "-q", "--allow-empty", "-m", "Pushed after the maintainer read it")
        moved = self.git(self.change, "rev-parse", "HEAD").strip()
        self.head = moved
        self.assertEqual(self.plan("--changed", sha=read), 1)
        self.assertIn(f"E2E03 The head of pull request ? is {moved[:7]}, not the commit you read ({read[:7]}). Fix: read the new "
                      "commits, then run again with the new one.", self.out.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_e2e03_names_the_pull_request_the_dispatch_carried(self):
        self.clone({"addons/mock/README.md": "x\n"})
        event = self.holder / "event.json"
        event.write_text(json.dumps({"inputs": {"pr": "345", "sha": "x"}}), encoding="utf-8")
        self.event = str(event)
        self.assertEqual(self.plan("--changed", sha="a" * 40), 1)
        self.assertIn("The head of pull request 345 is", self.out.getvalue())
        self.out = io.StringIO()
        self.assertEqual(self.plan("--changed", "--pr", "77", sha="a" * 40), 1)
        self.assertIn("The head of pull request 77 is", self.out.getvalue())

    def test_e2e03_names_the_pull_request_the_checkout_fetched(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.git(self.change, "update-ref", "refs/remotes/pull/9/head", self.head)
        self.assertEqual(self.plan("--changed", sha="b" * 40), 1)
        self.assertIn("The head of pull request 9 is", self.out.getvalue())

    def test_a_number_that_is_not_digits_is_never_put_in_a_message(self):
        self.clone({"addons/mock/README.md": "x\n"})
        event = self.holder / "event.json"
        event.write_text(json.dumps({"inputs": {"pr": "12\n::error::x"}}), encoding="utf-8")
        self.event = str(event)
        self.plan("--changed", sha="a" * 40)
        self.assertIn("pull request ? is", self.out.getvalue())

    def test_e2e04_a_bad_input_is_refused_and_says_which(self):
        self.clone({"addons/mock/README.md": "x\n"})
        for kwargs, argv, which in (({"sha": "abc"}, ("--changed",), "sha"), ({"sha": "A" * 40}, ("--changed",), "sha"),
                                    ({"sha": "g" * 40}, ("--changed",), "sha"), ({"sha": "a" * 41}, ("--changed",), "sha"),
                                    ({}, ("--changed", "--pr", "12ab"), "pr"), ({}, ("--changed", "--pr", "12345678"), "pr"),
                                    ({}, ("--changed", "--pr", ""), "pr"), ({}, ("--changed", "--pr", "-1"), "pr")):
            with self.subTest(kwargs=kwargs, argv=argv):
                self.out = io.StringIO()
                self.assertEqual(self.plan(*argv, **kwargs), 1)
                self.assertIn(f"E2E04 {which} is not what it says. Fix: pr is digits only; sha is 40 hexadecimal characters; items are "
                              "words that name an item and do not start with a dash.", self.out.getvalue())
                self.assertEqual(self.outputs(), {})

    def test_e2e04_a_word_that_does_not_name_an_item_or_starts_with_a_dash_is_refused(self):
        self.clone({"docs/guide.md": "x\n"})
        for words in (["--all"], ["-x"], ["playground:nothing"], ["bank"], ["playground:"], [":bank"], ["playground:Bank"],
                      ["playground:bank;rm"], ["playground:bank\naddon:mock"], ["playground:@other/bank"], ["playground:@acme-hub/"],
                      ["playground:bank", "--items"], ["addon:mock extra"], ["playground:../bank"], ["PLAYGROUND:bank"], [""],
                      ["a" * 300], ["playground:" + "a" * 101]):
            with self.subTest(words=words):
                self.out, self.err = io.StringIO(), io.StringIO()
                self.assertEqual(self.plan("--items", words=words), 1, self.err.getvalue())
                self.assertIn("E2E04 items is not what it says.", self.out.getvalue())
                self.assertEqual(self.outputs(), {})

    def test_e2e04_no_word_at_all_and_too_many_words_are_refused(self):
        self.clone({"docs/guide.md": "x\n"})
        self.assertEqual(self.plan("--items", words=[]), 1)
        self.assertIn("E2E04", self.out.getvalue())
        self.out = io.StringIO()
        self.assertEqual(self.plan("--items", words=["addon:mock"] * (e2e.MAX_WORDS + 1)), 1)
        self.assertIn("E2E04", self.out.getvalue())

    def test_what_comes_after_two_dashes_is_never_an_option(self):
        self.clone({"docs/guide.md": "x\n"})
        self.assertEqual(self.plan("--items", words=["--github-output", "--all"]), 1)
        self.assertIn("E2E04 items", self.out.getvalue())
        self.assertEqual(e2e.split_words(["a", "--items", "--", "--all", "x", "--"]), (["a", "--items"], ["--all", "x", "--"]))
        self.assertEqual(e2e.split_words(["a"]), (["a"], []))

    def test_words_after_two_dashes_for_anything_but_items_are_a_usage_error(self):
        self.clone({"docs/guide.md": "x\n"})
        self.assertEqual(self.plan("--all", words=["addon:mock"]), 2)
        self.assertIn("words after -- are the items of --items", self.err.getvalue())

    def test_the_change_and_the_commit_go_together(self):
        for argv in (["--all", "--change", str(self.repo)], ["--all", "--sha", "a" * 40]):
            self.err = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)}):
                self.assertEqual(e2e.plan_main([str(self.repo), *argv], self.out, self.err), 2)
            self.assertIn("--change and --sha go together", self.err.getvalue())

    def test_e2e05_a_change_to_what_runs_as_code_is_not_tried(self):
        for path in (".github/workflows/e2e.yml", ".github/actions/lab/action.yml", ".catalog/standard.mk", "Makefile", "catalog.yml"):
            with self.subTest(path=path):
                self.reset()
                self.clone({path: "# changed\n" if path != "catalog.yml" else catalog_yml(extra="  license: MIT\n")})
                self.out = io.StringIO()
                self.assertEqual(self.plan("--changed"), 1)
                self.assertIn(f"E2E05 The change touches {path}, which this workflow does not try. Fix: merge it first, a maintainer "
                              "reads it as code, then try the items.", self.out.getvalue())
                self.assertEqual(self.outputs(), {})

    def reset(self):
        import shutil
        if getattr(self, "change", None) and self.change.exists():
            shutil.rmtree(self.change)
        if self.output.exists():
            self.output.unlink()
        self.change = None

    def test_e2e05_says_every_path_it_refuses_up_to_five(self):
        self.clone({f".github/workflows/w{number}.yml": "x\n" for number in range(8)})
        self.assertEqual(self.plan("--all"), 1)
        self.assertIn("The change touches .github/workflows/w0.yml, .github/workflows/w1.yml, .github/workflows/w2.yml, "
                      ".github/workflows/w3.yml, .github/workflows/w4.yml and 3 more, which this workflow does not try.",
                      self.out.getvalue())

    def test_e2e05_applies_to_every_way_of_choosing_items(self):
        self.clone({"Makefile": "# changed\n"})
        for mode, words in (("--all", ()), ("--changed", ()), ("--items", ["addon:mock"])):
            with self.subTest(mode=mode):
                self.out = io.StringIO()
                self.assertEqual(self.plan(mode, words=words), 1)
                self.assertIn("E2E05", self.out.getvalue())

    def test_e2e05_only_what_the_pull_request_changed_counts_not_what_main_gained_since(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.put(self.repo, ".github/workflows/ci.yml", "name: CI 2\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "Main moves")
        tip = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.git(self.change, "fetch", "-q", "origin")
        self.assertNotEqual(self.git(self.change, "rev-parse", "origin/main").strip(), self.main_sha)
        self.assertEqual(self.git(self.change, "rev-parse", "origin/main").strip(), tip)
        self.assertEqual(self.plan("--changed"), 0, self.out.getvalue())
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:mock"])

    def test_the_merge_base_falls_back_on_the_main_checkout_when_the_pull_requests_has_no_main(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.git(self.change, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.plan("--changed"), 0, self.out.getvalue() + self.err.getvalue())

    def test_a_pull_request_that_cannot_be_compared_with_main_is_not_a_verdict(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.git(self.change, "update-ref", "-d", "refs/remotes/origin/main")
        with mock.patch.object(e2e, "head_of", side_effect=lambda folder: self.head if folder == str(self.change) else None):
            self.assertEqual(self.plan("--changed"), 2)
        self.assertIn("cannot be compared with main", self.err.getvalue())

    def test_a_folder_that_is_not_one_is_a_usage_error(self):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)}):
            self.assertEqual(e2e.plan_main(["/no/such/folder", "--all"], self.out, self.err), 2)
            self.assertEqual(e2e.plan_main([str(self.repo), "--all", "--change", "/no/such", "--sha", "a" * 40], self.out, self.err), 2)

    def test_the_outputs_need_a_workflow_step(self):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": ""}), mock.patch.object(ci, "LOADER", self.loader()):
            self.assertEqual(e2e.plan_main([str(self.repo), "--all", "--github-output"], self.out, self.err), 2)

    def test_hostile_names_in_the_pull_request_never_reach_the_outputs(self):
        self.clone({"addons/a\nb/compose.yml": "services: {}\n", "addons/::error::x/compose.yml": "services: {}\n",
                    "docs/::error::\nname.txt": "x\n", "docs/%0A::error::y.md": "x\n", "addons/mock/README.md": "x\n"})
        self.assertEqual(self.plan("--changed"), 0, self.out.getvalue() + self.err.getvalue())
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual([line.split("=", 1)[0] for line in text.splitlines()], ["matrix", "count"])
        self.assertEqual([job["item"] for job in self.jobs()], ["addon:mock"])
        self.assertNotIn("::error::", self.out.getvalue())

    def test_the_messages_are_the_words_of_the_standard(self):
        table = {code: (severity, message) for code, severity, _, message in explain.STAGES if code.startswith("E2E")}
        for code, (severity, message) in e2e.MESSAGES.items():
            self.assertEqual((severity, message), table[code], code)
        self.assertEqual(e2e.finding("E2E01", item="addon:x", service="s").message,
                         "addon:x: s reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it.")
        self.assertEqual(e2e.head_mismatch("12", "a" * 40, "b" * 40).message,
                         "The head of pull request 12 is aaaaaaa, not the commit you read (bbbbbbb). Fix: read the new commits, then run "
                         "again with the new one.")


class Gate(Fixture):
    """E2E03 again, in the job that holds the license."""

    def gate(self, *argv, sha=None, **seams):
        sha = sha or self.head
        return e2e.gate_main([str(self.repo), "--change", str(self.change), "--sha", sha, *argv], self.out, self.err, **seams)

    def test_the_head_that_was_read_passes(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.assertEqual(self.gate(), 0, self.err.getvalue())
        self.assertIn("The head of the pull request is the commit that was read", self.out.getvalue())

    def test_a_head_that_moved_between_the_plan_and_the_job_is_refused(self):
        self.clone({"addons/mock/README.md": "x\n"})
        read = self.head
        self.git(self.change, "commit", "-q", "--allow-empty", "-m", "Pushed in between")
        moved = self.git(self.change, "rev-parse", "HEAD").strip()
        self.assertEqual(self.gate(sha=read), 1)
        self.assertIn(f"E2E03 The head of pull request ? is {moved[:7]}, not the commit you read ({read[:7]}).", self.out.getvalue())
        self.out = io.StringIO()
        self.assertEqual(self.gate("--pr", "42", sha=read), 1)
        self.assertIn("The head of pull request 42 is", self.out.getvalue())

    def test_a_commit_that_is_not_40_hexadecimal_characters_is_e2e04(self):
        self.clone({"addons/mock/README.md": "x\n"})
        self.assertEqual(self.gate(sha="abc1234"), 1)
        self.assertIn("E2E04 sha is not what it says.", self.out.getvalue())
        self.out = io.StringIO()
        self.assertEqual(self.gate("--pr", "x1"), 1)
        self.assertIn("E2E04 pr is not what it says.", self.out.getvalue())

    def test_a_folder_that_is_not_a_repository_has_no_head_and_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            code = e2e.gate_main([str(self.repo), "--change", folder, "--sha", "a" * 40], self.out, self.err)
        self.assertEqual(code, 1)
        self.assertIn("E2E03", self.out.getvalue())

    def test_a_folder_that_is_not_there_is_a_usage_error(self):
        self.assertEqual(e2e.gate_main([str(self.repo), "--change", "/no/such", "--sha", "a" * 40], self.out, self.err), 2)

    def test_the_command_line_reaches_the_gate_and_the_json_form_is_a_list(self):
        self.clone({"addons/mock/README.md": "x\n"})
        out = io.StringIO()
        code = ci_main(["e2e-gate", str(self.repo), "--change", str(self.change), "--sha", "c" * 40, "--format", "json"], out, io.StringIO())
        self.assertEqual(code, 1)
        self.assertEqual([each["code"] for each in json.loads(out.getvalue())], ["E2E03"])


class Real(Case):
    """The marker through the Lab's own reading of a catalog and the real Compose (a skip that shows without Compose 5)."""

    def setUp(self):
        from lab_ui.library import check, project
        from lab_ui.docker import compose, envfiles
        from lab_ui.catalogs import registry
        from tests import fixtures
        fixtures.need_compose()
        fixtures.project(self, paths, check, project, compose, envfiles, ns="fixture", link=True)
        fixtures.install(self, "partner")
        self.folder = next(one["path"] for one in registry.entries() if one["id"] == "fixture")

    def plan(self, item):
        out, err = io.StringIO(), io.StringIO()
        written = Path(self.folder).parent / "github-output.txt"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(written)}):
            code = e2e.plan_main([self.folder, "--github-output", "--items", "--", item], out, err)
        return code, out.getvalue(), err.getvalue(), written

    def write_units(self):
        leaky = ("x-gravitee: {kind: addon, name: Leaky, category: Other, description: Leaks}\nservices:\n  fixture-leaky:\n"
                 "    image: busybox:1.36\n    container_name: gio-fixture-leaky\n    command: [\"sleep\", \"1\"]\n"
                 "    environment: {FIXTURE_LEAKY: \"${GRAVITEE_LICENSE:-}\"}\n    networks: [internal]\n")
        overlay = ("x-gravitee: {kind: addon, name: Overlay, category: Other, description: Overlay}\nservices:\n  apim-gateway:\n"
                   "    environment: {FIXTURE_OVERLAY: \"${GRAVITEE_LICENSE:-}\"}\n")
        for name, text in (("leaky", leaky), ("overlay", overlay)):
            folder = Path(self.folder, "addons", name)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "compose.yml").write_text(text, encoding="utf-8")

    def test_a_service_of_a_unit_that_reads_the_license_is_found_by_the_real_compose(self):
        self.write_units()
        code, out, err, written = self.plan("addon:leaky")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("E2E01 addon:leaky: fixture-leaky reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it.", out)
        self.assertIn("Surface reads: license (addon:leaky)", out)
        self.assertFalse(written.exists())

    def test_an_overlay_on_a_core_service_may_read_it_and_the_summary_says_so(self):
        self.write_units()
        code, out, err, written = self.plan("addon:overlay")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Surface reads: license (addon:overlay)", out)
        self.assertEqual(json.loads(dict(line.split("=", 1) for line in written.read_text(encoding="utf-8").splitlines())["matrix"]),
                         {"include": [{"item": "addon:overlay", "name": "addon:overlay (core defaults)", "tested": ""}]})

    def test_a_unit_that_never_names_the_license_reads_nothing_and_says_nothing(self):
        code, out, err, _ = self.plan("addon:echo")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("Surface reads", out)

    def test_the_sets_of_versions_a_playground_was_tested_with_are_one_job_each(self):
        code, out, err, written = self.plan("playground:shop")
        self.assertEqual((code, err), (0, ""), out)
        jobs = json.loads(dict(line.split("=", 1) for line in written.read_text(encoding="utf-8").splitlines())["matrix"])["include"]
        self.assertEqual([(job["item"], job["tested"]) for job in jobs], [("playground:shop", "0")])


if __name__ == "__main__":
    unittest.main()
