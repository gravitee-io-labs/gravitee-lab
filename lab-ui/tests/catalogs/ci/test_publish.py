"""`catalog.py ci publish-plan` and `catalog.py ci publish` (PB01 to PB08): the trusted lane of a catalog.

GitHub is a table of recorded replies (the checks of a commit, the runs of the workflow), the registry and buildx are a function that
remembers what was pushed, and the catalog is a real git repository, so the plan reads real history. Nothing here needs a network
or a Docker daemon. They prove what the standard says of the lane: a branch publishes what changed since the last run that
succeeded (a run that was dropped loses nothing), a tag publishes everything and a tag that exists is never moved, `latest` is the tip
of `main` and moves nowhere else, a pull request's ref publishes nothing, the gates say why, the seeder is never planned, the records
are the same bytes for the same inputs, a dry run prints the commands, and no secret is on a command line."""

import hashlib
import io
import json
import os
import re
import shlex
import unittest
from unittest import mock

from lab_ui.catalogs import ci, explain, settings
from lab_ui.catalogs.ci import main as ci_main
from lab_ui.catalogs.ci import publish
from tests.data.rules import support
from tests.fakedocker import FakeDocker, option, options

PREFIX = "ghcr.io/acme/lab-hub"
REPO = "acme/lab-hub"
SEEDER = "example.test/lab/seeder"
APPS = {"web": "playgrounds/bank/apps/web", "api": "playgrounds/bank/apps/api", "mock": "addons/mock/mock"}


def catalog_yml(version="1.2.0", images=PREFIX, license_="Apache-2.0"):
    return ("x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  description: Demos\n"
            f"  version: {version}\n  lab: 1\n" + (f"  license: {license_}\n" if license_ else "")
            + (f"  images: {images}\n" if images else ""))


def standard_json(platforms=("linux/amd64",)):
    return json.dumps({"form": "inline", "lab": {"ref": "0" * 40, "repository": "OWNER/lab"}, "platforms": list(platforms),
                       "standard": 1}, indent=2, sort_keys=True) + "\n"


def files(**more):
    base = {"catalog.yml": catalog_yml(), ".github/standard.json": standard_json(), "README.md": "# Hub\n",
            "playgrounds/bank/compose.yml": "services: {}\n", "playgrounds/bank/README.md": "# Bank\n",
            "playgrounds/bank/seed/apim/shop.json": "{}\n", "addons/mock/compose.yml": "services: {}\n"}
    for path in APPS.values():
        base[f"{path}/Dockerfile"] = "FROM busybox:1.36\n"
        base[f"{path}/app.py"] = "print(1)\n"
    base.update(more)
    return base


class Github:
    """Recorded replies of GitHub. `checks[sha]` is the list of replies the check runs of that commit give, one for each time it is
    asked (the last repeats); `runs` is the reply of the runs of the workflow."""

    def __init__(self):
        self.checks, self.runs, self.asked = {}, settings.Reply(404, {"message": "Not Found"}), []
        self.seen_checks = {}

    def __call__(self, method, path):
        self.asked.append((method, path))
        found = re.fullmatch(r"repos/([^/]+/[^/]+)/commits/([0-9a-f]{40})/check-runs\?check_name=Catalog%20CI%20result&per_page=100", path)
        if found:
            replies = self.checks.get(found.group(2), [])
            number = self.seen_checks.get(found.group(2), 0)
            self.seen_checks[found.group(2)] = number + 1
            if not replies:
                return settings.Reply(200, {"total_count": 0, "check_runs": []})
            return replies[min(number, len(replies) - 1)]
        if re.fullmatch(r"repos/[^/]+/[^/]+/actions/workflows/publish\.yml/runs\?branch=main&event=push&status=success&per_page=30", path):
            return self.runs
        raise AssertionError(f"a request the lane does not make: {method} {path}")

    def passed(self, sha, app=15368, number=1, name="Catalog CI result"):
        self.checks.setdefault(sha, []).append(settings.Reply(200, {"check_runs": [
            {"id": number, "name": name, "status": "completed", "conclusion": "success", "app": {"id": app}}]}))

    def check(self, sha, status="completed", conclusion="success", app=15368, number=1):
        reply = settings.Reply(200, {"check_runs": [
            {"id": number, "name": "Catalog CI result", "status": status, "conclusion": conclusion, "app": {"id": app}}]})
        self.checks.setdefault(sha, []).append(reply)

    def ran(self, *shas, event="push", conclusion="success"):
        self.runs = settings.Reply(200, {"workflow_runs": [{"head_sha": sha, "head_branch": "main", "event": event,
                                                            "conclusion": conclusion} for sha in shas]})


class Registry:
    """A registry and the buildx in front of it: `docker buildx build --push` stores each `--tag` with a digest made of the name and
    the commit of the labels, `imagetools inspect` answers what is stored, and every call is recorded."""

    def __init__(self):
        self.stored, self.calls, self.failures = {}, [], {}

    def digest(self, name, revision):
        return "sha256:" + hashlib.sha256(f"{name}@{revision}".encode()).hexdigest()

    def put(self, reference, revision, digest=None):
        name = reference.rsplit(":", 1)[0]
        self.stored[reference] = (digest or self.digest(name, revision), revision)

    def __call__(self, argv, timeout, cwd):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "version"]:
            return 0, "27.0.0\n", ""
        if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            reference = argv[-1]
            if reference not in self.stored:
                return 1, "", f"ERROR: {reference}: not found\n"
            digest, revision = self.stored[reference]
            labels = {"org.opencontainers.image.revision": revision} if revision else {}
            return 0, json.dumps({"name": reference, "manifest": {"mediaType": "application/vnd.oci.image.index.v1+json",
                                                                   "digest": digest},
                                  "image": {"linux/amd64": {"architecture": "amd64", "config": {"Labels": labels}}}}), ""
        if argv[:3] == ["docker", "buildx", "build"]:
            tags = options(argv, "--tag")
            queued = self.failures.get(tags[0].rsplit(":", 1)[0]) if tags else None
            if queued:
                return queued.pop(0)
            revision = next((label.partition("=")[2] for label in options(argv, "--label")
                             if label.startswith("org.opencontainers.image.revision=")), "")
            for tag in tags:
                self.put(tag, revision)
            return 0, "#1 DONE\n", ""
        return 0, "", ""

    def builds(self):
        return [call for call in self.calls if call[:3] == ["docker", "buildx", "build"]]


class Fixture(support.WithGit):

    def setUp(self):
        super().setUp()
        self.github = Github()
        self.registry = Registry()
        self.out, self.err = io.StringIO(), io.StringIO()
        self.slept = []
        self.lab = self.holder / "lab"
        (self.lab / "seeder").mkdir(parents=True)
        (self.lab / "seeder" / "Dockerfile").write_text("FROM scratch\n")
        (self.lab / "docker-compose.yml").write_text(f"x-gravitee:\n  seeder:\n    image: {SEEDER}:latest\nservices: {{}}\n")
        self.repo = self.work(files=files())
        self.first = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.first)
        self.output = self.holder / "github-output"

    # the catalog
    def folder(self, path):
        return str(self.repo / path)

    def units(self, names):
        """The units as the loader reads them: the bank builds two apps and has a seed that the Lab's seeder builds; the add-on
        builds one."""
        loaded = []
        for kind, name in names:
            if (kind, name) == ("playground", "bank"):
                services = {
                    "bakery-web": {"image": f"{PREFIX}/bakery-web:latest", "build": {"context": self.folder(APPS["web"]),
                                                                                 "dockerfile": "Dockerfile"}},
                    "bakery-api": {"image": f"{PREFIX}/bakery-api:latest", "build": {"context": self.folder(APPS["api"])}},
                    "bakery-seed": {"image": f"{SEEDER}:latest", "build": {"context": str(self.lab / "seeder")},
                                  "labels": {"io.gravitee.role": "seed"}}}
            elif (kind, name) == ("addon", "mock"):
                services = {"mock-api": {"image": f"{PREFIX}/mock-api:latest", "build": {"context": self.folder(APPS["mock"])}}}
            else:
                services = {}
            loaded.append(ci.Loaded(kind, name, name, ci.unit_path(kind, name), str(self.repo / ci.unit_path(kind, name)).rsplit("/", 1)[0],
                                    {"services": services}, tuple(services), (), None))
        return loaded

    def loader(self, refused=()):
        def load(folder, names):
            left_out = {(each.kind, each.name) for each in refused}
            return ci.Loading(self.units([each for each in names if each not in left_out]), list(refused), [])
        return load

    def commit(self, change, message="Change the apps", advance=True):
        """Writes files, commits them and (by default) moves origin/main to the commit, as a fetch does."""
        for path, content in change.items():
            self.put(self.repo, path, content)
        sha = super().commit(self.repo, message)
        if advance:
            self.git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        return sha

    def reset(self):
        """Back to the first commit, the outputs and the recorded replies cleared."""
        self.git(self.repo, "reset", "-q", "--hard", self.first)
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.first)
        self.github = Github()
        if self.output.exists():
            self.output.unlink()
        self.slept = []
        self.out, self.err = io.StringIO(), io.StringIO()

    def tag(self, name, sha=None):
        self.git(self.repo, "tag", name, sha or "HEAD")

    def head(self):
        return self.git(self.repo, "rev-parse", "HEAD").strip()

    # the runs
    def plan(self, *argv, ref="refs/heads/main", sha=None, repository=REPO, private="true", loading=None, event="push", **seams):
        sha = sha or self.head()
        seams.setdefault("gh", self.github)
        seams.setdefault("sleep", self.slept.append)
        seams.setdefault("lab", str(self.lab))
        env = {"GITHUB_OUTPUT": str(self.output), "GITHUB_EVENT_NAME": event}
        with mock.patch.object(ci, "LOADER", loading or self.loader()), mock.patch.dict(os.environ, env):
            return publish.plan_main([str(self.repo), "--ref", ref, "--sha", sha, "--repository", repository, "--private", private,
                                      "--github-output", *argv], self.out, self.err, **seams)

    def outputs(self):
        try:
            text = self.output.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        return dict(line.split("=", 1) for line in text.splitlines())

    def planned(self):
        found = self.outputs()
        return json.loads(found["images"]), found["count"], found["attest"]


def names(*apps):
    return sorted(f"{PREFIX}/{app}" for app in apps)


class Branch(Fixture):
    """A push to main."""

    def test_only_the_images_whose_files_changed_since_the_last_successful_run_are_planned(self):
        self.github.ran(self.first)
        sha = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        self.github.passed(sha)
        self.assertEqual(self.plan(), 0, self.err.getvalue() + self.out.getvalue())
        self.assertEqual(self.planned(), ([f"{PREFIX}/bakery-web"], "1", "false"))
        self.assertIn("1 of 3 images have build files that changed since the last successful run (" + self.first[:7] + ")",
                      self.out.getvalue())

    def test_what_each_kind_of_change_rebuilds(self):
        cases = (
            ({f"{APPS['api']}/Dockerfile": "FROM busybox:1.37\n"}, ["bakery-api"]),
            ({"playgrounds/bank/compose.yml": "services: {}  # build args changed\n"}, ["bakery-api", "bakery-web"]),
            ({f"{APPS['mock']}/app.py": "print(3)\n"}, ["mock-api"]),
            ({"README.md": "# Hub, changed\n"}, []),
            ({"playgrounds/bank/seed/apim/shop.json": '{"a": 1}\n', "playgrounds/bank/README.md": "# Bank 2\n"}, []),
            ({"catalog.yml": catalog_yml(license_="MIT")}, ["bakery-api", "bakery-web", "mock-api"]),
            ({".github/standard.json": standard_json(("linux/amd64", "linux/arm64"))}, ["bakery-api", "bakery-web", "mock-api"]),
            ({".github/workflows/ci.yml": "name: CI\n"}, []))
        for change, expected in cases:
            with self.subTest(change=sorted(change)):
                self.reset()
                self.github.ran(self.first)
                sha = self.commit(change)
                self.github.passed(sha)
                self.assertEqual(self.plan(), 0, self.err.getvalue())
                self.assertEqual(self.planned(), ([f"{PREFIX}/{each}" for each in expected], str(len(expected)), "false"))

    def test_a_dropped_run_loses_nothing_the_next_one_covers_its_commits(self):
        self.github.ran(self.first)
        dropped = self.commit({f"{APPS['web']}/app.py": "print(2)\n"}, "Change the web app")
        sha = self.commit({f"{APPS['api']}/app.py": "print(3)\n"}, "Change the api")
        self.github.passed(sha)
        self.assertNotEqual(dropped, sha)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-api", f"{PREFIX}/bakery-web"])   # (the run of the first commit never happened)
        # and github.event.before is never looked at: the plan took the base from the runs of the workflow
        self.assertNotIn("before", " ".join(path for _, path in self.github.asked))

    def test_two_plans_in_a_row_where_the_first_run_never_happened_still_build_the_first_commits_change(self):
        self.github.ran(self.first)
        first = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        second = self.commit({f"{APPS['mock']}/app.py": "print(3)\n"})
        for sha in (first, second):
            self.github.passed(sha)
        # the plan of the first commit is the one that is never made; the second covers it
        self.assertEqual(self.plan(sha=second), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-web", f"{PREFIX}/mock-api"])

    def test_after_a_successful_run_the_base_moves_on(self):
        first = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        second = self.commit({f"{APPS['api']}/app.py": "print(3)\n"})
        self.github.ran(first, self.first)   # (newest first)
        self.github.passed(second)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-api"])

    def test_no_successful_run_means_every_image(self):
        self.github.runs = settings.Reply(200, {"workflow_runs": []})
        self.github.passed(self.first)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned(), (names("bakery-web", "bakery-api", "mock-api"), "3", "false"))
        self.assertIn("no run of this workflow has succeeded yet: every image is built", self.out.getvalue())

    def test_a_workflow_that_has_not_run_at_all_is_the_first_run(self):
        self.github.passed(self.first)   # (the runs are a 404 by default)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[1], "3")
        self.assertIn("this workflow has no run yet: every image is built", self.out.getvalue())

    def test_runs_that_failed_or_came_from_another_event_do_not_count(self):
        self.github.runs = settings.Reply(200, {"workflow_runs": [
            {"head_sha": self.first, "head_branch": "main", "event": "push", "conclusion": "failure"},
            {"head_sha": self.first, "head_branch": "feature", "event": "push", "conclusion": "success"},
            {"head_sha": self.first, "head_branch": "main", "event": "workflow_dispatch", "conclusion": "success"},
            {"head_sha": "not a sha", "head_branch": "main", "event": "push", "conclusion": "success"}]})
        sha = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        self.github.passed(sha)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[1], "3")

    def test_a_last_run_that_is_not_an_ancestor_of_the_commit_means_every_image(self):
        self.git(self.repo, "checkout", "-q", "-b", "other")
        other = self.commit({"README.md": "# other\n"}, "On another line", advance=False)
        self.git(self.repo, "checkout", "-q", "main")
        sha = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        self.github.ran(other)
        self.github.passed(sha)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[1], "3")
        self.assertIn("is not an ancestor of", self.out.getvalue())

    def test_a_manual_run_builds_every_image(self):
        self.github.ran(self.first)
        sha = self.commit({"README.md": "# Hub 2\n"})
        self.github.passed(sha)
        self.assertEqual(self.plan(event="workflow_dispatch"), 0)
        self.assertEqual(self.planned()[1], "3")
        self.assertIn("a manual run publishes every image", self.out.getvalue())

    def test_the_event_comes_from_the_environment_when_it_is_not_given(self):
        self.github.passed(self.first)
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}):
            with mock.patch.object(ci, "LOADER", self.loader()):
                code = publish.plan_main([str(self.repo), "--ref", "refs/heads/main", "--sha", self.first, "--repository", REPO,
                                          "--private", "true", "--format", "json"], self.out, self.err, gh=self.github,
                                         lab=str(self.lab), sleep=self.slept.append)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.out.getvalue())["count"], 3)

    def test_a_rerun_of_an_old_run_publishes_nothing_so_latest_never_moves_back(self):
        old = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        newer = self.commit({f"{APPS['api']}/app.py": "print(3)\n"})
        self.github.ran(newer, old, self.first)   # (a later run succeeded: it already holds `old`)
        self.github.passed(old)
        self.assertEqual(self.plan(sha=old), 0)
        self.assertEqual(self.planned(), ([], "0", "false"))
        self.assertIn(f"a run that succeeded after {old[:7]} ({newer[:7]}) has published it: nothing is published again",
                      self.out.getvalue())
        self.assertEqual([path for _, path in self.github.asked if "check-runs" in path], [], "it did not wait for anything")
        self.out = io.StringIO()
        self.assertEqual(self.plan(sha=old, event="workflow_dispatch"), 0)   # (a manual re-run of the old one too)
        self.assertEqual(self.planned()[1], "0")

    def test_a_run_that_started_late_still_publishes_its_commit_when_no_run_after_it_has_succeeded(self):
        self.github.ran(self.first)
        late = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        self.commit({f"{APPS['api']}/app.py": "print(3)\n"})   # (main moved on while this run waited its turn)
        self.github.passed(late)
        self.assertEqual(self.plan(sha=late), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-web"], "its own change; the run of the newer commit builds the rest")

    def test_the_same_commit_run_again_has_nothing_new_to_build(self):
        self.github.ran(self.first)
        self.github.passed(self.first)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[1], "0")

    def test_an_image_outside_the_prefix_is_an_error_and_nothing_is_planned(self):
        files_ = {"playgrounds/bank/apps/web/app.py": "x\n"}
        self.github.ran(self.first)
        sha = self.commit(files_)
        self.github.passed(sha)

        def other(folder, names_):
            loaded = self.units(names_)
            out = []
            for unit in loaded:
                services = {name: ({**service, "image": "ghcr.io/someone/else/bakery-web:latest"} if name == "bakery-web" else service)
                            for name, service in unit.config["services"].items()}
                out.append(unit._replace(config={"services": services}))
            return ci.Loading(out, [], [])
        self.assertEqual(self.plan(loading=other), 1)
        self.assertIn("IM02", self.out.getvalue())
        self.assertIn("the image ghcr.io/someone/else/bakery-web:latest is outside ghcr.io/acme/lab-hub", self.out.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_a_catalog_that_names_no_registry_publishes_nothing_and_asks_nothing(self):
        sha = self.commit({"catalog.yml": catalog_yml(images="")})
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned(), ([], "0", "false"))
        self.assertEqual(self.github.asked, [])
        self.assertIn("catalog.yml names no registry in images, so nothing is published", self.out.getvalue())
        self.assertTrue(sha)

    def test_the_seeder_is_never_planned(self):
        self.github.passed(self.first)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-api", f"{PREFIX}/bakery-web", f"{PREFIX}/mock-api"])
        self.assertFalse([name for name in self.planned()[0] if "seeder" in name])

    def test_a_unit_that_needs_a_setting_is_said_and_its_images_are_not_planned(self):
        needs = ci.Refused("addon", "mock", "addons/mock/compose.yml", (
            "Cannot be read: error while interpolating services.a.environment.KEY: required variable KEY is missing a value",))
        self.github.passed(self.first)
        self.assertEqual(self.plan(loading=self.loader([needs])), 0)
        self.assertEqual(self.planned()[0], [f"{PREFIX}/bakery-api", f"{PREFIX}/bakery-web"])
        self.assertIn("addon:mock needs settings, so its images are not planned", self.out.getvalue())

    def test_a_unit_the_trust_policy_blocks_stops_the_plan(self):
        blocked = ci.Refused("addon", "mock", "addons/mock/compose.yml", ("Mounts the Docker socket",), "T10")
        self.github.passed(self.first)
        self.assertEqual(self.plan(loading=self.loader([blocked])), 1)
        self.assertIn("T10", self.out.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_a_hostile_file_name_adds_no_output_and_no_line_of_the_plan(self):
        self.github.ran(self.first)
        sha = self.commit({"docs/a\n::error::x.txt": "x\n", "docs/%0A::set-output name=images::[].txt": "y\n",
                           f"{APPS['web']}/app.py": "print(2)\n"})
        self.github.passed(sha)
        self.assertEqual(self.plan(), 0, self.err.getvalue())
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual([line.split("=", 1)[0] for line in text.splitlines()], ["images", "count", "attest"])
        self.assertEqual(self.planned(), ([f"{PREFIX}/bakery-web"], "1", "false"))
        self.assertEqual([line for line in self.out.getvalue().splitlines() if line.startswith("::")], [])

    def test_a_name_inside_a_unit_that_the_lab_cannot_install_stops_the_plan_and_writes_nothing(self):
        self.github.ran(self.first)
        sha = self.commit({f"{APPS['web']}/a\n::error::x.txt": "x\n"})
        self.github.passed(sha)
        self.assertEqual(self.plan(), 1)
        self.assertIn("ST03 playground:bank was not read: ", self.out.getvalue())
        self.assertEqual(self.outputs(), {})
        self.assertEqual([line for line in self.out.getvalue().splitlines() if line.startswith("::")], [])

    def test_the_images_are_sorted_and_one_line_of_json(self):
        self.github.passed(self.first)
        self.plan()
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n"), 3)
        self.assertEqual([line.split("=", 1)[0] for line in text.splitlines()], ["images", "count", "attest"])
        self.assertEqual(json.loads(text.splitlines()[0].split("=", 1)[1]), sorted(self.planned()[0]))


class Gates(Fixture):
    """The check run of the commit, PB08, and the refusals."""

    def test_the_plan_waits_for_the_check_run_and_plans_when_it_succeeded(self):
        self.github.ran(self.first)
        sha = self.commit({f"{APPS['web']}/app.py": "print(2)\n"})
        self.github.check(sha, "queued", None)
        self.github.check(sha, "in_progress", None)
        self.github.check(sha)
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.slept, [20, 20])
        self.assertEqual(self.planned()[1], "1")
        self.assertIn(f"Waiting for Catalog CI result on {sha[:7]}: queued", self.out.getvalue())
        self.assertIn("in_progress", self.out.getvalue())

    def test_a_check_run_that_has_not_started_is_waited_for(self):
        self.github.ran(self.first)
        self.assertEqual(self.plan(clock=iter([0, 0, 25, 50, 1900]).__next__), 1)
        self.assertEqual(self.slept, [20, 20, 20])
        self.assertIn("PB03", self.out.getvalue())

    def test_pb03_a_check_that_ended_otherwise_says_no_at_once(self):
        for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out", "action_required", None):
            with self.subTest(conclusion=conclusion):
                self.reset()
                self.github.check(self.first, conclusion=conclusion)
                self.assertEqual(self.plan(), 1)
                self.assertIn(f"PB03 Catalog CI result has not passed on {self.first[:7]}. Fix: wait for it, or fix what failed.",
                              self.out.getvalue())
                self.assertEqual(self.slept, [])
                self.assertEqual(self.outputs(), {})

    def test_a_check_run_of_another_app_or_another_name_does_not_count(self):
        self.github.check(self.first, app=1234)
        self.github.passed(self.first, name="Something else")
        self.assertEqual(self.plan(clock=iter([0, 0, 2000]).__next__), 1)
        self.assertIn(f"PB03 Catalog CI result has not passed on {self.first[:7]}", self.out.getvalue())

    def test_the_newest_of_several_check_runs_is_the_one_that_counts(self):
        self.github.ran(self.first)
        self.github.checks[self.first] = [settings.Reply(200, {"check_runs": [
            {"id": 1, "name": "Catalog CI result", "status": "completed", "conclusion": "failure", "app": {"id": 15368}},
            {"id": 7, "name": "Catalog CI result", "status": "completed", "conclusion": "success", "app": {"id": 15368}}]})]
        self.assertEqual(self.plan(), 0)

    def test_a_token_that_cannot_read_the_checks_is_not_a_verdict(self):
        self.github.checks[self.first] = [settings.Reply(403, {"message": "Resource not accessible by integration"})]
        self.assertEqual(self.plan(), 2)
        self.assertIn("checks: read", self.err.getvalue())

    def test_a_github_that_does_not_answer_for_the_whole_time_is_not_a_verdict(self):
        self.github.checks[self.first] = [settings.Reply(0, {"message": "GitHub did not answer"})]
        self.assertEqual(self.plan(clock=iter([0, 0, 1000, 2000]).__next__), 2)
        self.assertIn("GitHub did not answer", self.err.getvalue())

    def test_the_polling_is_every_20_seconds_for_30_minutes(self):
        self.assertEqual((publish.POLL, publish.LIMIT), (20, 1800))

    def test_no_gh_is_not_a_verdict(self):
        def missing(method, path):
            raise settings.Unusable("The GitHub command-line tool gh is not installed. Install it from https://cli.github.com")
        self.assertEqual(self.plan(gh=missing), 2)
        self.assertIn("gh is not installed", self.err.getvalue())

    def test_pb08_a_head_commit_with_a_tool_credit_is_refused(self):
        cases = (("Add the bank API\n\nGenerated with " + support.tool() + " Code\n", {}),
                 ("Add the bank API\n\nCo-authored-by: " + support.tool() + " <" + support.tool_address() + ">\n", {}),
                 ("Add the bank API\n", {"GIT_AUTHOR_NAME": support.tool() + " Code", "GIT_AUTHOR_EMAIL": support.tool_address()}),
                 ("Add the bank API\n", {"GIT_COMMITTER_NAME": support.tool() + " Code", "GIT_COMMITTER_EMAIL": support.tool_address()}))
        for message, extra in cases:
            with self.subTest(message=message[:30], extra=sorted(extra)):
                self.reset()
                self.put(self.repo, "README.md", f"# {message[:10]} {len(extra)}\n")
                self.git(self.repo, "add", "-A")
                self.git(self.repo, "commit", "-q", "-m", message, extra=extra)
                sha = self.head()
                self.github.passed(sha)
                self.assertEqual(self.plan(), 1, self.out.getvalue())
                self.assertIn(f"PB08 The commit {sha[:7]} carries a tool credit in its message or its author. main is never rewritten. "
                              "Fix: find how it got there (the merge settings, V18) and write the next title without it.",
                              self.out.getvalue())
                self.assertEqual(self.outputs(), {})
                self.assertEqual(self.github.asked, [], "the credit is seen before anything is asked of GitHub")

    def test_a_head_commit_without_a_credit_passes_pb08(self):
        self.github.passed(self.first)
        self.assertEqual(self.plan(), 0)
        self.assertNotIn("PB08", self.out.getvalue())

    def test_a_person_called_like_a_tool_is_not_a_tool(self):
        self.put(self.repo, "README.md", "# Hub 2\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "Thank " + support.tool() + " for the idea", extra={
            "GIT_AUTHOR_NAME": support.tool() + " Martin", "GIT_AUTHOR_EMAIL": "martin@example.test"})
        sha = self.head()
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        self.github.passed(sha)
        self.assertEqual(self.plan(), 0)

    def test_refs_other_than_main_and_a_release_tag_publish_nothing(self):
        for ref in ("refs/pull/12/merge", "refs/pull/12/head", "refs/heads/feature", "refs/heads/main2", "refs/heads/main/x",
                    "refs/tags/1.2.0", "refs/tags/", "refs/tags/v", "refs/remotes/origin/main", "main", "", "refs/tags/v1.2.0 x",
                    "refs/tags/v1.2.0\n::error::x"):
            with self.subTest(ref=ref):
                self.assertEqual(self.plan(ref=ref), 2)
                self.assertIn("publish runs from refs/heads/main and from a release tag", self.err.getvalue())
                self.assertEqual(self.github.asked, [])
                self.assertEqual(self.outputs(), {})
                self.err = io.StringIO()

    def test_a_bad_commit_repository_or_flag_is_a_usage_error(self):
        for kwargs, words in (({"sha": "abc"}, "--sha is the commit"), ({"sha": "g" * 40}, "--sha is the commit"),
                              ({"repository": "acme"}, "--repository is OWNER/REPO"),
                              ({"repository": "acme/../x"}, "--repository is OWNER/REPO"),
                              ({"private": "yes"}, "--private is true or false"), ({"private": ""}, "--private is true or false"),
                              ({"event": None, "extra": ("--event", "Push;")}, "--event is the name of a GitHub event")):
            with self.subTest(kwargs=kwargs):
                extra = kwargs.pop("extra", ())
                kwargs.pop("event", None)
                self.err = io.StringIO()
                self.assertEqual(self.plan(*extra, **kwargs), 2)
                self.assertIn(words, self.err.getvalue())

    def test_the_attestation_is_asked_for_a_public_repository_only(self):
        self.github.passed(self.first)
        self.plan(private="false")
        self.assertEqual(self.planned()[2], "true")

    def test_nothing_is_written_to_the_outputs_when_a_gate_says_no(self):
        self.github.check(self.first, conclusion="failure")
        self.plan()
        self.assertFalse(self.output.exists())

    def test_the_plan_needs_the_outputs_of_a_workflow_step_to_write_them(self):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            code = publish.plan_main([str(self.repo), "--ref", "refs/heads/main", "--sha", self.first, "--repository", REPO,
                                      "--private", "true", "--github-output"], self.out, self.err, gh=self.github)
        self.assertEqual(code, 2)


class Tag(Fixture):
    """A release tag."""

    def setUp(self):
        super().setUp()
        self.sha = self.commit({"catalog.yml": catalog_yml("1.4.0")}, "Release 1.4.0")
        self.tag("v1.4.0")

    def tagged(self, **kwargs):
        kwargs.setdefault("ref", "refs/tags/v1.4.0")
        kwargs.setdefault("sha", self.sha)
        return self.plan(**kwargs)

    def test_a_tag_publishes_every_image_whatever_changed(self):
        self.github.ran(self.first)
        self.github.passed(self.sha)
        self.assertEqual(self.tagged(), 0, self.out.getvalue() + self.err.getvalue())
        self.assertEqual(self.planned(), (names("bakery-web", "bakery-api", "mock-api"), "3", "false"))
        self.assertEqual([path for _, path in self.github.asked if "workflows" in path], [], "a tag asks nothing of earlier runs")

    def test_pb01_the_version_in_catalog_yml_has_to_be_the_tag(self):
        self.github.passed(self.sha)
        self.assertEqual(self.tagged(ref="refs/tags/v1.5.0"), 1)
        self.assertIn("PB01 The version in catalog.yml (1.4.0) is not the tag (1.5.0). Fix: bump the version, then tag again.",
                      self.out.getvalue())
        self.assertEqual(self.github.asked, [], "a gate that needs no network is read before the wait")

    def test_pb01_a_tag_that_is_not_a_plain_version_is_refused_by_the_same_gate(self):
        for tag in ("refs/tags/v1.4.0-rc1", "refs/tags/v1.4", "refs/tags/v1.4.0.1", "refs/tags/v01.4.0x"):
            with self.subTest(tag=tag):
                self.out = io.StringIO()
                self.assertEqual(self.tagged(ref=tag), 1)
                self.assertIn("PB01", self.out.getvalue())

    def test_pb02_the_commit_has_to_be_on_main(self):
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.first)   # (the tag's commit is past main)
        self.github.passed(self.sha)
        self.assertEqual(self.tagged(), 1)
        self.assertIn("PB02 The tag v1.4.0 is not on main.", self.out.getvalue())
        self.assertEqual(self.github.asked, [])

    def test_pb02_a_main_that_cannot_be_read_is_not_taken_for_a_yes(self):
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.tagged(), 1)
        self.assertIn("PB02", self.out.getvalue())

    def test_pb03_the_check_run_of_the_commit_has_to_have_succeeded(self):
        self.github.check(self.sha, conclusion="failure")
        self.assertEqual(self.tagged(), 1)
        self.assertIn(f"PB03 Catalog CI result has not passed on {self.sha[:7]}.", self.out.getvalue())
        self.assertEqual(self.outputs(), {})

    def test_pb05_images_is_the_repository_of_this_run(self):
        self.github.passed(self.sha)
        for repository in ("acme/other", "ACME/Lab-Hub-x"):
            with self.subTest(repository=repository):
                self.out = io.StringIO()
                self.assertEqual(self.tagged(repository=repository), 1)
                self.assertIn(f"PB05 catalog.yml images is {PREFIX}; this repository publishes to ghcr.io/{repository.lower()}.",
                              self.out.getvalue())
        self.out = io.StringIO()
        self.github.passed(self.sha)
        self.assertEqual(self.tagged(repository="Acme/Lab-Hub"), 0, "the repository is compared in lower case")

    def test_every_gate_that_fails_is_said_together(self):
        self.assertEqual(self.tagged(ref="refs/tags/v9.9.9", repository="acme/other"), 1)
        text = self.out.getvalue()
        for code in ("PB01", "PB05"):
            self.assertIn(code, text)

    def test_a_tag_of_a_catalog_that_names_no_registry_still_has_its_gates_and_publishes_no_image(self):
        sha = self.commit({"catalog.yml": catalog_yml("1.6.0", images="")}, "Release 1.6.0")
        self.tag("v1.6.0")
        self.github.passed(sha)
        self.assertEqual(self.plan(ref="refs/tags/v1.6.0", sha=sha), 0)
        self.assertEqual(self.planned(), ([], "0", "false"))
        self.out = io.StringIO()
        self.assertEqual(self.plan(ref="refs/tags/v1.7.0", sha=sha), 1)   # (the version gate holds)
        self.assertIn("PB01", self.out.getvalue())

    def test_a_pull_requests_ref_is_never_a_tag_of_anything(self):
        for ref in ("refs/pull/12/merge", "refs/pull/12/head"):
            self.assertEqual(self.tagged(ref=ref), 2)
            self.assertEqual(self.outputs(), {})


class Messages(unittest.TestCase):

    def test_the_messages_are_the_words_of_the_standard(self):
        self.assertEqual(publish.finding("PB01", a="1.4.0", b="1.5.0").message,
                         "The version in catalog.yml (1.4.0) is not the tag (1.5.0). Fix: bump the version, then tag again.")
        self.assertEqual(publish.finding("PB02", tag="v1.4.0").message, "The tag v1.4.0 is not on main.")
        self.assertEqual(publish.finding("PB03", sha7="abc1234").message,
                         "Catalog CI result has not passed on abc1234. Fix: wait for it, or fix what failed.")
        self.assertEqual(publish.finding("PB04", image="ghcr.io/o/r/x", version="1.4.0").message,
                         "ghcr.io/o/r/x:1.4.0 was published from another commit. Fix: bump the version.")
        self.assertEqual(publish.finding("PB05", x="a", y="b").message, "catalog.yml images is a; this repository publishes to b.")
        self.assertEqual(publish.finding("PB06", image="ghcr.io/o/r/x").message,
                         "ghcr.io/o/r/x carries a license label but is built from another project's source.")
        table = {code: (severity, message) for code, severity, _, message in explain.STAGES if code.startswith("PB")}
        for code, (severity, message) in publish.MESSAGES.items():
            self.assertEqual((severity, message), table[code], code)

    def test_the_stage_words_reach_the_modules(self):
        self.assertEqual(ci.STAGES["publish-plan"], ("publish", "plan_main"))
        self.assertEqual(ci.STAGES["publish"], ("publish", "main"))
        self.assertTrue(callable(publish.plan_main) and callable(publish.main))
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ci_main(["publish-plan"], out, err), 2)   # (missing arguments are a usage error, not a crash)
        self.assertEqual(ci_main(["publish", "--images", "[]"], io.StringIO(), io.StringIO()), 2)


class Publishing(Fixture):
    """The build and the push."""

    def setUp(self):
        super().setUp()
        self.records = self.holder / "records.json"

    def publish(self, images, *argv, ref="refs/heads/main", sha=None, runner=None, loading=None, **seams):
        sha = sha or self.head()
        seams.setdefault("lab", str(self.lab))
        seams.setdefault("sleep", self.slept.append)
        env = {"GITHUB_OUTPUT": str(self.output)}
        with mock.patch.object(ci, "LOADER", loading or self.loader()), mock.patch.dict(os.environ, env):
            return publish.main([str(self.repo), "--images", json.dumps(images), "--ref", ref, "--sha", sha, "--records",
                                 str(self.records), "--github-output", *argv], self.out, self.err,
                                runner=runner or self.registry, **seams)

    def test_a_branch_pushes_sha_and_latest_for_the_platforms_of_the_repository(self):
        sha = self.head()
        code = self.publish([f"{PREFIX}/bakery-web"])
        self.assertEqual(code, 0, self.out.getvalue() + self.err.getvalue())
        web = self.folder(APPS["web"])
        self.assertEqual(self.registry.builds(), [[
            "docker", "buildx", "build", "--platform", "linux/amd64", "--push", "--provenance=mode=max", "--sbom=true",
            "--tag", f"{PREFIX}/bakery-web:sha-{sha[:7]}", "--tag", f"{PREFIX}/bakery-web:latest",
            "--label", "org.opencontainers.image.source=https://github.com/acme/lab-hub",
            "--label", f"org.opencontainers.image.revision={sha}", "--label", f"org.opencontainers.image.version=sha-{sha[:7]}",
            "--label", "org.opencontainers.image.licenses=Apache-2.0", "--file", f"{os.path.realpath(web)}/Dockerfile",
            os.path.realpath(web)]])

    def test_both_platforms_when_the_repository_says_so(self):
        self.commit({".github/standard.json": standard_json(("linux/arm64", "linux/amd64"))})
        self.publish([f"{PREFIX}/bakery-web"])
        build = self.registry.builds()[0]
        self.assertEqual(option(build, "--platform"), "linux/amd64,linux/arm64")   # (always the canonical order)

    def test_a_standard_that_cannot_be_read_publishes_nothing(self):
        self.commit({".github/standard.json": "{\n"})
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"]), 1)
        self.assertIn("SD04", self.out.getvalue())
        self.assertEqual(self.registry.builds(), [])

    def test_a_tag_pushes_the_version_and_the_commit_and_never_latest(self):
        sha = self.commit({"catalog.yml": catalog_yml("1.4.0")}, "Release 1.4.0")
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/mock-api"], ref="refs/tags/v1.4.0", sha=sha), 0)
        for build in self.registry.builds():
            tags = options(build, "--tag")
            self.assertEqual(tags[0].rsplit(":", 1)[1], "1.4.0")
            self.assertEqual(tags[1].rsplit(":", 1)[1], f"sha-{sha[:7]}")
            self.assertEqual(len(tags), 2)
            self.assertFalse([tag for tag in tags if tag.endswith(":latest")])
            self.assertIn("org.opencontainers.image.version=1.4.0", options(build, "--label"))
        order = [(call[-1] if call[:4] == ["docker", "buildx", "imagetools", "inspect"] else "build:" + options(call, "--tag")[0])
                 for call in self.registry.calls if call[:3] in (["docker", "buildx", "imagetools"], ["docker", "buildx", "build"])]
        self.assertEqual(order, [f"{PREFIX}/bakery-web:1.4.0", f"build:{PREFIX}/bakery-web:1.4.0", f"{PREFIX}/bakery-web:1.4.0",
                                 f"{PREFIX}/mock-api:1.4.0", f"build:{PREFIX}/mock-api:1.4.0", f"{PREFIX}/mock-api:1.4.0"],
                         "the version tag is looked at before an image is built, and its digest after")

    def test_latest_is_only_ever_pushed_by_the_main_branch(self):
        sha = self.head()
        self.publish([f"{PREFIX}/bakery-web"])
        self.assertIn(f"{PREFIX}/bakery-web:latest", self.registry.stored)
        self.registry = Registry()
        self.tag("v1.2.0")
        self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0", sha=sha)
        self.assertNotIn(f"{PREFIX}/bakery-web:latest", self.registry.stored)
        self.assertIn(f"{PREFIX}/bakery-web:1.2.0", self.registry.stored)

    def test_a_tag_that_exists_from_this_commit_is_reused_and_not_built_again(self):
        sha = self.head()
        self.registry.put(f"{PREFIX}/bakery-web:1.2.0", sha, "sha256:" + "ab" * 32)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/bakery-api"], ref="refs/tags/v1.2.0"), 0)
        self.assertEqual([options(build, "--tag")[0] for build in self.registry.builds()], [f"{PREFIX}/bakery-api:1.2.0"])
        records = json.loads(self.records.read_text(encoding="utf-8"))
        self.assertEqual([(each["name"], each["digest"]) for each in records],
                         [(f"{PREFIX}/bakery-api", self.registry.digest(f"{PREFIX}/bakery-api", sha)),
                          (f"{PREFIX}/bakery-web", "sha256:" + "ab" * 32)])
        self.assertIn(f"{PREFIX}/bakery-web:1.2.0 is there already, from this commit: not built again", self.out.getvalue())

    def test_pb04_a_tag_that_exists_from_another_commit_is_never_moved(self):
        self.registry.put(f"{PREFIX}/bakery-web:1.2.0", "1" * 40, "sha256:" + "cd" * 32)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/bakery-api"], ref="refs/tags/v1.2.0"), 1)
        self.assertIn(f"PB04 {PREFIX}/bakery-web:1.2.0 was published from another commit. Fix: bump the version.", self.out.getvalue())
        self.assertEqual([options(build, "--tag")[0] for build in self.registry.builds()], [f"{PREFIX}/bakery-api:1.2.0"],
                         "the others go on")
        self.assertEqual(self.registry.stored[f"{PREFIX}/bakery-web:1.2.0"][1], "1" * 40)
        self.assertEqual([each["name"] for each in json.loads(self.records.read_text(encoding="utf-8"))], [f"{PREFIX}/bakery-api"])

    def test_pb04_an_image_with_no_revision_label_cannot_be_shown_to_be_this_commits(self):
        self.registry.put(f"{PREFIX}/bakery-web:1.2.0", "", "sha256:" + "cd" * 32)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0"), 1)
        self.assertIn("PB04", self.out.getvalue())
        self.assertEqual(self.registry.builds(), [])

    def test_an_image_whose_answer_shows_no_label_is_this_commits_when_its_commit_tag_is_the_same_manifest(self):
        sha = self.head()
        self.registry.put(f"{PREFIX}/bakery-web:1.2.0", "", "sha256:" + "cd" * 32)
        self.registry.put(f"{PREFIX}/bakery-web:sha-{sha[:7]}", "", "sha256:" + "cd" * 32)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0"), 0, self.out.getvalue())
        self.assertEqual(self.registry.builds(), [])
        self.assertEqual(json.loads(self.records.read_text(encoding="utf-8"))[0]["digest"], "sha256:" + "cd" * 32)
        self.registry.put(f"{PREFIX}/bakery-web:sha-{sha[:7]}", "", "sha256:" + "ee" * 32)   # (another manifest under the commit's tag)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0"), 1)
        self.assertIn("PB04", self.out.getvalue())

    def test_a_registry_that_cannot_be_asked_about_a_version_is_not_taken_for_one_that_has_none(self):
        def refusing(argv, timeout, cwd):
            if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
                return 1, "", "ERROR: failed to do request: Head https://ghcr.io/v2/acme/lab-hub/bakery-web/manifests/1.2.0: net/http: TLS handshake timeout"
            return self.registry(argv, timeout, cwd)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0", runner=refusing), 2)
        self.assertIn("the registry could not be asked about", self.err.getvalue())
        self.assertEqual(self.registry.builds(), [])

    def test_a_package_that_does_not_exist_yet_may_be_answered_with_a_refusal_of_access(self):
        def first_time(argv, timeout, cwd):
            if argv[:4] == ["docker", "buildx", "imagetools", "inspect"] and argv[-1].endswith(":1.2.0") \
                    and not self.registry.builds():
                return 1, "", "ERROR: denied: requested access to the resource is denied"
            return self.registry(argv, timeout, cwd)
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref="refs/tags/v1.2.0", runner=first_time), 0, self.err.getvalue())
        self.assertEqual(len(self.registry.builds()), 1)

    def test_pb06_a_license_label_on_an_image_built_from_another_projects_source_is_refused(self):
        remote = "https://github.com/someone/project.git#" + "a" * 40

        def loading(folder, names_):
            loaded = self.units(names_)
            return ci.Loading([unit._replace(config={"services": {"bakery-web": {
                "image": f"{PREFIX}/bakery-web:latest", "build": {"context": remote, "labels": {
                    "org.opencontainers.image.licenses": "MIT"}}}}}, own=("bakery-web",)) if unit.name == "bank" else unit
                               for unit in loaded], [], [])
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], loading=loading), 1)
        self.assertIn(f"PB06 {PREFIX}/bakery-web carries a license label but is built from another project's source.", self.out.getvalue())
        self.assertEqual(self.registry.builds(), [])

    def test_an_image_built_from_another_projects_source_gets_no_license_label_of_ours(self):
        remote = "https://github.com/someone/project.git#" + "a" * 40

        def loading(folder, names_):
            loaded = self.units(names_)
            return ci.Loading([unit._replace(config={"services": {"bakery-web": {
                "image": f"{PREFIX}/bakery-web:latest", "build": {"context": remote}}}}, own=("bakery-web",)) if unit.name == "bank" else unit
                               for unit in loaded], [], [])
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], loading=loading), 0, self.out.getvalue())
        build = self.registry.builds()[0]
        self.assertFalse([label for label in options(build, "--label") if "licenses" in label])
        self.assertEqual(build[-1], remote)
        self.assertEqual(option(build, "--file"), "Dockerfile")

    def test_a_catalog_without_a_license_puts_none_on_its_images(self):
        self.commit({"catalog.yml": catalog_yml(license_="")})
        self.publish([f"{PREFIX}/bakery-web"])
        self.assertFalse([label for label in options(self.registry.builds()[0], "--label") if "licenses" in label])

    def test_the_labels_of_the_standard_win_over_the_ones_of_a_build(self):
        def loading(folder, names_):
            return ci.Loading([unit._replace(config={"services": {"bakery-web": {
                "image": f"{PREFIX}/bakery-web:latest", "build": {"context": self.folder(APPS["web"]), "labels": {
                    "org.opencontainers.image.revision": "forged", "org.example.keep": "yes"}}}}}, own=("bakery-web",))
                               if unit.name == "bank" else unit for unit in self.units(names_)], [], [])
        self.publish([f"{PREFIX}/bakery-web"], loading=loading)
        labels = options(self.registry.builds()[0], "--label")
        self.assertIn("org.example.keep=yes", labels)
        self.assertNotIn("org.opencontainers.image.revision=forged", labels)
        self.assertEqual([each for each in labels if each.startswith("org.opencontainers.image.revision=")],
                         [f"org.opencontainers.image.revision={self.head()}"])

    def test_the_build_arguments_and_the_target_come_from_the_tree_and_are_recorded_as_such(self):
        def loading(folder, names_):
            return ci.Loading([unit._replace(config={"services": {"bakery-web": {
                "image": f"{PREFIX}/bakery-web:latest", "build": {"context": self.folder(APPS["web"]), "target": "final",
                                                                "args": {"B": "2", "A": "1"}}}}}, own=("bakery-web",))
                               if unit.name == "bank" else unit for unit in self.units(names_)], [], [])
        self.publish([f"{PREFIX}/bakery-web"], loading=loading)
        build = self.registry.builds()[0]
        self.assertEqual(options(build, "--build-arg"), ["A=1", "B=2"])
        self.assertEqual(option(build, "--target"), "final")

    def test_only_the_images_named_are_built_and_only_names_the_plan_could_have_made(self):
        self.publish([f"{PREFIX}/mock-api"])
        self.assertEqual([options(build, "--tag")[0].rsplit(":", 1)[0] for build in self.registry.builds()], [f"{PREFIX}/mock-api"])
        for images, words in (("not json", "--images is not JSON"), ('{"a": 1}', "must be a JSON list"), ('[1]', "must be a JSON list"),
                              ('["ghcr.io/acme/lab-hub/BANK"]', "is not an image name without a tag"),
                              ('["ghcr.io/acme/lab-hub/bakery-web:latest"]', "is not an image name without a tag"),
                              ('["-x"]', "is not an image name without a tag"), ('["a b"]', "is not an image name without a tag"),
                              (json.dumps([f"{PREFIX}/nothing-builds-this"]), "which this tree does not build as an image")):
            with self.subTest(images=images):
                self.registry = Registry()
                self.err = io.StringIO()
                with mock.patch.object(ci, "LOADER", self.loader()):
                    code = publish.main([str(self.repo), "--images", images, "--ref", "refs/heads/main", "--sha", self.head(),
                                         "--records", str(self.records)], self.out, self.err, runner=self.registry, lab=str(self.lab))
                self.assertEqual(code, 2)
                self.assertIn(words, self.err.getvalue())
                self.assertEqual(self.registry.builds(), [])

    def test_the_seeder_is_never_published_even_when_the_plan_names_it(self):
        self.assertEqual(self.publish([SEEDER]), 2)
        self.assertEqual(self.registry.builds(), [])

    def test_a_pull_requests_ref_is_refused(self):
        for ref in ("refs/pull/12/merge", "refs/heads/feature", "refs/tags/v1.2"):
            self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], ref=ref), 2)
        self.assertEqual(self.registry.calls, [])

    def test_the_records_are_the_same_bytes_for_the_same_inputs_whatever_the_order(self):
        sha = self.head()
        names_ = [f"{PREFIX}/mock-api", f"{PREFIX}/bakery-web", f"{PREFIX}/bakery-api"]
        self.publish(names_)
        first = self.records.read_bytes()
        first_output = self.output.read_text(encoding="utf-8")
        self.registry, self.output = Registry(), self.holder / "again"
        self.publish(list(reversed(names_)))
        self.assertEqual(self.records.read_bytes(), first)
        self.assertEqual(self.output.read_text(encoding="utf-8"), first_output)
        records = json.loads(first.decode("utf-8"))
        self.assertEqual([each["name"] for each in records], sorted(names_))
        self.assertEqual(records[0], {"digest": self.registry.digest(f"{PREFIX}/bakery-api", sha), "name": f"{PREFIX}/bakery-api",
                                      "platforms": ["linux/amd64"], "tags": [f"sha-{sha[:7]}", "latest"]})
        self.assertTrue(first.endswith(b"\n"))
        self.assertEqual(first, json.dumps(records, indent=2, sort_keys=True, ensure_ascii=True).encode() + b"\n")
        for forbidden in ("created", "time", "date"):
            self.assertNotIn(forbidden, first.decode("utf-8").lower().replace("-", ""))

    def test_the_output_for_the_attestation_is_one_line_of_names_and_digests(self):
        self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/bakery-api"])
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n"), 1)
        key, _, value = text.rstrip("\n").partition("=")
        self.assertEqual(key, "records")
        self.assertEqual([sorted(each) for each in json.loads(value)], [["digest", "name"], ["digest", "name"]])
        self.assertEqual([each["name"] for each in json.loads(value)], [f"{PREFIX}/bakery-api", f"{PREFIX}/bakery-web"])

    def test_nothing_to_publish_writes_empty_records(self):
        self.assertEqual(self.publish([]), 0)
        self.assertEqual(self.records.read_text(encoding="utf-8"), "[]\n")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "records=[]\n")
        self.assertEqual(self.registry.calls, [])

    def test_a_build_that_failed_is_bd01_with_its_last_lines_and_the_others_go_on(self):
        failed = ("#5 [3/5] RUN pip install -r requirements.txt\n#5 ERROR: process \"/bin/sh -c pip install\" did not complete "
                  "successfully: exit code: 1\n")
        self.registry.failures[f"{PREFIX}/bakery-web"] = [(1, "", failed)]
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/bakery-api"]), 1)
        self.assertIn("BD01", self.out.getvalue())
        self.assertIn("The last", self.out.getvalue())
        self.assertEqual([each["name"] for each in json.loads(self.records.read_text(encoding="utf-8"))], [f"{PREFIX}/bakery-api"])
        self.assertEqual(self.slept, [])

    def test_a_refusal_of_docker_hub_is_tried_twice_more_and_is_not_the_authors(self):
        refused = "ERROR: failed to solve: toomanyrequests: You have reached your unauthenticated pull rate limit\n"
        self.registry.failures[f"{PREFIX}/bakery-web"] = [(1, "", "#3 ERROR: " + refused)] * 3
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"]), 1)
        self.assertEqual(self.slept, [20, 60])
        self.assertIn("BD05 Docker Hub refused the pull (rate limit). This is not your change: run the job again in a few minutes.",
                      self.out.getvalue())
        self.assertEqual(len(self.registry.builds()), 3)

    def test_a_docker_that_is_not_there_is_not_a_verdict(self):
        def none(argv, timeout, cwd):
            return 127, "", "[Errno 2] No such file or directory: 'docker'"
        self.assertEqual(self.publish([f"{PREFIX}/bakery-web"], runner=none), 2)
        self.assertIn("Docker is not installed", self.err.getvalue())

    def test_the_dry_run_prints_the_commands_and_runs_nothing(self):
        sha = self.head()
        code = self.publish([f"{PREFIX}/bakery-web", f"{PREFIX}/mock-api"], "--dry-run")
        self.assertEqual(code, 0, self.err.getvalue())
        self.assertEqual(self.registry.calls, [])
        self.assertFalse(self.records.exists())
        lines_ = self.out.getvalue().splitlines()
        self.assertEqual(lines_[0], f"== {PREFIX}/bakery-web")
        command = shlex.split(lines_[1])
        self.assertEqual(command[:3], ["docker", "buildx", "build"])
        self.assertIn("--push", command)
        self.assertEqual(options(command, "--tag"), [f"{PREFIX}/bakery-web:sha-{sha[:7]}", f"{PREFIX}/bakery-web:latest"])
        self.assertEqual(lines_[2], f"== {PREFIX}/mock-api")

    def test_the_dry_run_of_a_tag_says_it_would_look_at_the_version_first(self):
        self.tag("v1.2.0")
        self.publish([f"{PREFIX}/bakery-web"], "--dry-run", ref="refs/tags/v1.2.0")
        self.assertIn(f"# first asked whether it exists: docker buildx imagetools inspect --format '{{{{json .}}}}' {PREFIX}/bakery-web:1.2.0",
                      self.out.getvalue())
        self.assertEqual(self.registry.calls, [])

    def test_no_secret_is_on_a_command_line_or_in_the_environment_of_a_command(self):
        secrets_ = {"GH_TOKEN": "gho_" + "A" * 36, "GITHUB_TOKEN": "ghs_" + "B" * 36, "REGISTRY_TOKEN": "tok_" + "C" * 30,
                    "ACTIONS_RUNTIME_TOKEN": "D" * 40, "LAB_DEPLOY_KEY": "key-" + "E" * 30, "SSH_AUTH_SOCK": "/tmp/agent.sock"}
        fake = FakeDocker(self)
        (fake.folder / "real.json").write_text("null", encoding="utf-8")
        inspect_answer = json.dumps({"manifest": {"digest": "sha256:" + "ab" * 32},
                                     "image": {"config": {"Labels": {"org.opencontainers.image.revision": self.head()}}}})
        fake.when("imagetools", "inspect", stdout=inspect_answer)
        with mock.patch.dict(os.environ, secrets_):
            with mock.patch.object(ci, "LOADER", self.loader()), mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)}):
                code = publish.main([str(self.repo), "--images", json.dumps([f"{PREFIX}/bakery-web"]), "--ref", "refs/heads/main",
                                     "--sha", self.head(), "--records", str(self.records), "--github-output"], self.out, self.err,
                                    lab=str(self.lab), sleep=self.slept.append)
        self.assertEqual(code, 0, self.err.getvalue() + self.out.getvalue())
        self.assertTrue(fake.commands("buildx", "build"))
        text = json.dumps([call.argv for call in fake.calls()]) + self.records.read_text(encoding="utf-8") + self.output.read_text(encoding="utf-8") \
            + self.out.getvalue() + self.err.getvalue()
        for name, value in secrets_.items():
            self.assertNotIn(value, text, name)
            for call in fake.calls():
                self.assertNotIn(name, call.env)
        for call in fake.calls():
            self.assertFalse([name for name in call.env if name.startswith(("ACTIONS_", "GITHUB_", "GH_"))])

    def test_the_stage_with_the_real_docker_command_line_on_the_path(self):
        fake = FakeDocker(self)
        (fake.folder / "real.json").write_text("null", encoding="utf-8")
        fake.when("imagetools", "inspect", stdout=json.dumps({"manifest": {"digest": "sha256:" + "ab" * 32}}))
        with mock.patch.object(ci, "LOADER", self.loader()):
            code = publish.main([str(self.repo), "--images", json.dumps([f"{PREFIX}/bakery-web"]), "--ref", "refs/heads/main", "--sha",
                                 self.head(), "--records", str(self.records)], self.out, self.err, lab=str(self.lab))
        self.assertEqual(code, 0, self.err.getvalue() + self.out.getvalue())
        self.assertEqual(len(fake.commands("buildx", "build")), 1)
        self.assertEqual(json.loads(self.records.read_text(encoding="utf-8"))[0]["digest"], "sha256:" + "ab" * 32)


class Places(unittest.TestCase):

    def image(self, *watches):
        return publish.Image(f"{PREFIX}/x", "playground:bank", "x", "playgrounds/bank/compose.yml", "/c", "/c/Dockerfile", (), "", (), False,
                             tuple(watches))

    def test_a_place_is_touched_by_itself_or_by_anything_below_it(self):
        image = self.image("playgrounds/bank/compose.yml", "playgrounds/bank/apps/web", "playgrounds/bank/apps/web/Dockerfile")
        for changed, expected in ((["playgrounds/bank/apps/web/app.py"], True), (["playgrounds/bank/apps/web/deep/er/x"], True),
                                  (["playgrounds/bank/apps/web"], True), (["playgrounds/bank/compose.yml"], True),
                                  (["playgrounds/bank/apps/webx/app.py"], False), (["playgrounds/bank/apps"], False),
                                  (["playgrounds/bank/README.md"], False), (["playgrounds"], False), ([], False), (["README.md"], False)):
            with self.subTest(changed=changed):
                self.assertEqual(publish.affected(image, changed), expected)

    def test_a_context_that_is_the_top_of_the_catalog_is_touched_by_any_change(self):
        self.assertTrue(publish.affected(self.image("."), ["anything/at/all"]))
        self.assertFalse(publish.affected(self.image("."), []))

    def test_many_images_and_many_paths_cost_one_pass(self):
        changed = [f"docs/folder{number}/file{number}.md" for number in range(100_000)]
        images = [self.image(f"apps/app{number}", f"apps/app{number}/Dockerfile") for number in range(2000)]
        places = publish.touched_places(changed)
        found, spent = support.timed(lambda: [publish.affected(each, changed, places) for each in images])
        self.assertEqual(found.count(True), 0)
        self.assertLess(spent, 2.0)


class Parts(unittest.TestCase):

    def test_the_pieces_of_a_build_command_are_read_back_from_the_one_that_ci_build_makes(self):
        from lab_ui.catalogs.ci import build
        command = build.command_of("ghcr.io/o/r/x:1", "linux/amd64", "/c/Dockerfile", "/c", {"B": "2", "A": "1"}, "final",
                                   {"k": "v", "a": "b"})
        self.assertEqual(publish.parts_of(command), {"tag": "ghcr.io/o/r/x:1", "file": "/c/Dockerfile",
                                                     "args": ["A=1", "B=2"], "target": "final", "labels": ["a=b", "k=v"],
                                                     "context": "/c"})
        bare = build.command_of("x:1", "linux/arm64", "/c/Dockerfile", "/c", {}, "", {})
        self.assertEqual(publish.parts_of(bare)["args"], [])

    def test_a_command_of_many_arguments_is_read_in_one_pass(self):
        from lab_ui.catalogs.ci import build
        args = {f"A{number}": "x" for number in range(60_000)}
        command = build.command_of("x:1", "linux/amd64", "/c/Dockerfile", "/c", args, "", {})
        found, spent = support.timed(publish.parts_of, command)
        self.assertEqual(len(found["args"]), 60_000)
        self.assertLess(spent, 1.0)

    def test_the_last_part_of_an_image(self):
        for image, expected in (("ghcr.io/o/r/bakery-web:latest", "bakery-web"), ("ghcr.io/o/r/bakery-web", "bakery-web"),
                                ("bakery-web", "bakery-web"), ("localhost:5000/a/b@sha256:" + "a" * 64, "b")):
            self.assertEqual(publish.last_part(image), expected)

    def test_the_labels_of_a_registry_answer_are_found_wherever_buildx_put_them(self):
        answer = {"Image": {"linux/amd64": {"config": {"Labels": {"org.opencontainers.image.revision": "f" * 40}}}}}
        self.assertEqual(publish.revision_in(answer), "f" * 40)
        self.assertEqual(publish.revision_in({"image": {"Config": {"labels": {"x": "y"}}}}), None)
        self.assertEqual(publish.revision_in([{"a": [{"Labels": {"org.opencontainers.image.revision": "a" * 40}}]}]), "a" * 40)
        deep = {}
        node = deep
        for _ in range(5000):
            node["x"] = {}
            node = node["x"]
        self.assertIsNone(publish.revision_in(deep))
        self.assertEqual(publish.dig({"Manifest": {"Digest": "d"}}, "manifest", "digest"), "d")
        self.assertIsNone(publish.dig({"manifest": 3}, "manifest", "digest"))


if __name__ == "__main__":
    unittest.main()
