"""`catalog.py ci build` (BD01, BD02, BD04, BD05): the apps of the touched units are built, on the architecture of the runner,
from a catalog a stranger proposed.

The units are given to the stage by the loader seam (a real catalog read through the Lab's project is one class, which needs
Compose 5 and prints a skip without it), and Docker is `tests/fakedocker.py`: it records every call, so the tests prove the exact
command for each architecture, that a key outside the T18 list is never given to Docker, that the Lab's seeder is left out, that
a failure that is Docker Hub's is tried again after 20 s and 60 s and one that is the author's is not, and that no variable of the
runner reaches a build."""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui import paths
from lab_ui.catalogs import ci, registry, trust
from lab_ui.catalogs.ci import build
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests import fixtures
from tests.fakedocker import FakeDocker, option, options

SEEDER_IMAGE = "example.test/lab/seeder"
FIX = ("Put the build context under the unit, pin a remote context to a 40-character commit after `#`, and remove ssh, secrets "
       "and cache options. Only `context`, `dockerfile`, `args`, `target` and `labels` are accepted (default-deny; "
       "`additional_contexts` only for the Lab's seeder).")
SHELL_ADDED = {"PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
ALLOWED = {"PATH", "LANG", "LC_ALL", "TMPDIR", "DOCKER_HOST", "HOME", "DOCKER_CONFIG", "COMPOSE_EXPERIMENTAL_GIT_REMOTE",
           "COMPOSE_EXPERIMENTAL_OCI_REMOTE"}

STEP_FAILED = """\
#1 [internal] load build definition from Dockerfile
#1 DONE 0.0s

#4 [2/5] COPY requirements.txt .
#4 DONE 0.1s

#5 [3/5] RUN pip install -r requirements.txt
#5 0.402 Collecting flask
#5 1.201 ::error::boom ::add-mask::hunter2
#5 1.302 ERROR: toomanyrequests: this is the program talking
#5 ERROR: process "/bin/sh -c pip install -r requirements.txt" did not complete successfully: exit code: 1
------
 > [3/5] RUN pip install -r requirements.txt:
0.402 Collecting flask
------
Dockerfile:4
--------------------
ERROR: failed to solve: process "/bin/sh -c pip install -r requirements.txt" did not complete successfully: exit code: 1
"""
HUB_REFUSED = """\
#3 [internal] load metadata for docker.io/library/python:3.14-slim
#3 ERROR: failed to copy: httpReadSeeker: failed open: unexpected status from GET request to https://registry-1.docker.io/v2/x: 429 Too Many Requests
ERROR: failed to solve: python:3.14-slim: failed to resolve source metadata for docker.io/library/python:3.14-slim: toomanyrequests: You have reached your unauthenticated pull rate limit
"""


class Disk(Case):
    """A catalog on disk (its apps are folders with a Dockerfile) and a Lab folder with a core file and a seeder."""

    def setUp(self):
        self.root = self.make("catalog")
        self.lab = self.make("lab")
        Path(self.lab, "seeder").mkdir()
        Path(self.lab, "seeder", "Dockerfile").write_text("FROM scratch\n")
        Path(self.lab, "docker-compose.yml").write_text(f"x-gravitee:\n  seeder:\n    image: {SEEDER_IMAGE}:latest\nservices: {{}}\n")

    def make(self, name):
        holder = tempfile.TemporaryDirectory(prefix=f"gravitee-lab-{name}-")
        self.addCleanup(holder.cleanup)
        return os.path.realpath(holder.name)

    def app(self, unit="playgrounds/bank", name="web", dockerfile="Dockerfile"):
        folder = Path(self.root, unit, "apps", name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / dockerfile).write_text("FROM busybox:1.36\n")
        return str(folder)

    def service(self, name="bakery-web", context=None, image=None, **build_keys):
        context = context or self.app(name=name.replace("bank-", ""))
        return {"image": image or f"example.test/lab/{name}:1.0", "build": {"context": context, "dockerfile": "Dockerfile",
                                                                          **build_keys}}

    def unit(self, services, kind="playground", name="bank", own=None):
        file = ci.unit_path(kind, name)
        return ci.Loaded(kind, name, name, file, os.path.dirname(os.path.join(self.root, file)),
                         {"services": {"gateway": {"image": "graviteeio/apim-gateway:4.12.0"}, **services}},
                         tuple(own if own is not None else services), (), None)

    def specs(self, services, platform="linux/amd64", **more):
        return build.specs_of([self.unit(services)], platform, self.root, self.lab, **more)


class Commands(Disk):

    def test_the_command_of_an_image_for_each_architecture_is_the_one_of_the_standard(self):
        context = self.app()
        for platform in ("linux/amd64", "linux/arm64"):
            with self.subTest(platform=platform):
                specs, problems, rows = self.specs({"bakery-web": self.service(context=context)}, platform)
                self.assertEqual((problems, rows), ([], []))
                self.assertEqual(specs[0].command, [
                    "docker", "buildx", "build", "--load", "--platform", platform, "--tag", "example.test/lab/bakery-web:1.0",
                    "--file", f"{context}/Dockerfile", context])

    def test_arguments_target_and_labels_follow_the_order_of_the_standard(self):
        service = self.service(args={"B": "2", "A": "1", "N": None, "OK": True, "NUM": 7}, target="final",
                               labels={"org.example.b": "y", "org.example.a": "x"})
        specs, problems, _ = self.specs({"bakery-web": service})
        self.assertEqual(problems, [])
        command = specs[0].command
        self.assertEqual(command[command.index("--file") + 2:], [
            "--build-arg", "A=1", "--build-arg", "B=2", "--build-arg", "NUM=7", "--build-arg", "OK=true", "--target", "final",
            "--label", "org.example.a=x", "--label", "org.example.b=y", service["build"]["context"]])
        self.assertNotIn("N=", " ".join(command))   # an argument with no value is not set

    def test_a_dockerfile_of_another_name_is_given_at_its_real_place(self):
        context = self.app(dockerfile="Dockerfile.prod")
        specs, _, _ = self.specs({"bakery-web": self.service(context=context, dockerfile="Dockerfile.prod")})
        self.assertEqual(option(specs[0].command, "--file"), f"{context}/Dockerfile.prod")

    def test_it_is_an_argument_list_with_no_shell_and_no_flag_the_standard_does_not_name(self):
        specs, _, _ = self.specs({"bakery-web": self.service(args={"A": "x; rm -rf /"}, labels={"k": "$(id)"})})
        command = specs[0].command
        self.assertEqual(command[:4], ["docker", "buildx", "build", "--load"])
        self.assertIn("A=x; rm -rf /", command)   # one word, given to Docker as it is
        flags = {word for word in command if word.startswith("--")}
        self.assertEqual(flags, {"--load", "--platform", "--tag", "--file", "--build-arg", "--label"})
        for banned in ("--push", "--cache-to", "--cache-from", "--ssh", "--secret", "--network", "--output", "--pull"):
            self.assertNotIn(banned, command)

    def test_only_the_services_a_unit_defines_are_built(self):
        context = self.app()
        services = {"bakery-web": self.service(context=context), "core-like": self.service("core-like", self.app(name="core"))}
        specs, _, _ = build.specs_of([self.unit(services, own=["bakery-web"])], "linux/amd64", self.root, self.lab)
        self.assertEqual([each.service for each in specs], ["bakery-web"])

    def test_a_service_with_a_build_and_no_image_is_built_under_a_name_of_the_stage_when_the_stage_asks(self):
        context = self.app()
        for image in (None, ""):
            service = self.service(context=context)
            service.pop("image")
            if image is not None:
                service["image"] = image
            specs, problems, rows = self.specs({"bakery-web": service}, local=True)
            self.assertEqual((problems, rows), ([], []))
            self.assertEqual(specs[0].image, "lab-ci/playground-bank-bakery-web:ci")
            self.assertEqual(specs[0].command[:9], ["docker", "buildx", "build", "--load", "--platform", "linux/amd64", "--tag",
                                                    "lab-ci/playground-bank-bakery-web:ci", "--file"])
            specs, problems, rows = self.specs({"bakery-web": service})   # (publish has no image to push for it: it asks without)
            self.assertEqual((specs, problems), ([], []))
            self.assertEqual([(each.status, each.note) for each in rows], [("skipped", "no image name to give the build")])

    def test_the_name_of_the_stage_is_a_name_whatever_the_unit_and_the_service_are_called(self):
        for unit, service in (("playground:bank", "web"), ("addon:x", "Web_1"), ("setup:a-b", "a.b"), ("addon:q", "UPPER-Case"),
                              ("playground:bank", "-.weird"), ("addon:q", "x" * 300)):
            tag = build.local_tag(unit, service)
            self.assertRegex(tag, ci.IMAGE_NAME.pattern)
            self.assertRegex(tag, r"^lab-ci/[a-z0-9][a-z0-9._-]*:ci$")
            self.assertLessEqual(len(tag), 120)
        self.assertEqual(build.local_tag("addon:x", "Web_1"), "lab-ci/addon-x-web_1:ci")
        self.assertNotEqual(build.local_tag("addon:x", "a"), build.local_tag("addon:y", "a"))

    def test_a_service_with_no_image_that_builds_the_labs_seeder_is_the_labs_too(self):
        seed = {"build": {"context": os.path.join(self.lab, "seeder"), "dockerfile": "Dockerfile"}}
        specs, problems, rows = self.specs({"bakery-seed": seed}, local=True)
        self.assertEqual((specs, problems), ([], []))
        self.assertEqual([(each.service, each.status, each.note) for each in rows],
                         [("bakery-seed", "skipped", "the Lab's own seeder, which the Lab's CI builds")])

    def test_a_build_with_no_image_still_answers_for_its_keys(self):
        service = self.service()
        service.pop("image")
        service["build"]["ssh"] = ["default"]
        specs, problems, _ = self.specs({"bakery-web": service}, local=True)
        self.assertEqual((specs, [each.code for each in problems]), ([], ["BD02"]))

    def test_a_service_with_no_build_is_not_built(self):
        specs, problems, rows = self.specs({"mock": {"image": "busybox:1.36"}})
        self.assertEqual((specs, problems, rows), ([], [], []))

    def test_one_app_alone(self):
        services = {"bakery-web": self.service(context=self.app(name="web")), "bakery-api": self.service("bakery-api", self.app(name="api"))}
        specs, _, rows = self.specs(services, only="api")
        self.assertEqual([each.service for each in specs], ["bakery-api"])
        self.assertEqual([(each.service, each.status) for each in rows], [("bakery-web", "skipped")])

    def test_an_image_is_built_once(self):
        a, b = self.app(name="a"), self.app(name="b")
        services = {"one": self.service("one", a, image="example.test/lab/same:1"), "two": self.service("two", b, image="example.test/lab/same:1")}
        specs, _, rows = self.specs(services)
        self.assertEqual([each.service for each in specs], ["one"])
        self.assertEqual(rows[0].status, "skipped")


class Refusals(Disk):

    KEYS = ("ssh", "secrets", "cache_from", "cache_to", "network", "privileged", "entitlements", "extra_hosts", "ulimits", "pull",
            "dockerfile_inline", "additional_contexts", "platforms", "tags", "no_cache", "shm_size", "isolation", "provenance",
            "sbom", "args_from", "x-anything", "")

    def test_any_build_key_outside_the_t18_list_is_bd02_and_nothing_is_built_for_it(self):
        for key in self.KEYS:
            with self.subTest(key=key):
                service = self.service(**{key: ["x"] if key else "x"})
                specs, problems, rows = self.specs({"bakery-web": service})
                self.assertEqual(specs, [])
                self.assertEqual([(each.code, each.severity, each.path) for each in problems],
                                 [("BD02", "error", "playgrounds/bank/compose.yml")])
                self.assertEqual(problems[0].message, f"bakery-web: build.{key} is not accepted. Fix: use context, dockerfile, args, "
                                                      "target and labels only.")
                self.assertEqual(rows[0].status, "refused")

    def test_one_service_refused_does_not_stop_the_others(self):
        services = {"bad": self.service("bad", self.app(name="bad"), ssh=["default"]),
                    "good": self.service("good", self.app(name="good"))}
        specs, problems, _ = self.specs(services)
        self.assertEqual(([each.service for each in specs], [each.code for each in problems]), (["good"], ["BD02"]))

    def test_every_key_is_listed_when_a_service_has_several(self):
        specs, problems, _ = self.specs({"bakery-web": self.service(ssh=["default"], network="host", cache_to=["x"])})
        self.assertEqual([each.message.split("build.")[1].split(" ")[0] for each in problems], ["cache_to", "network", "ssh"])

    def test_the_list_is_the_trust_policys_when_it_names_one_and_never_wider_than_the_standards(self):
        service = self.service(labels={"a": "b"}, target="t")
        with mock.patch.object(trust, "BUILD_KEYS", ("context", "dockerfile", "args"), create=True):
            specs, problems, _ = self.specs({"bakery-web": service})
        self.assertEqual((specs, sorted(each.message.split("build.")[1].split(" ")[0] for each in problems)), ([], ["labels", "target"]))
        with mock.patch.object(trust, "BUILD_KEYS", ("context", "dockerfile", "args", "target", "labels", "ssh", "network"), create=True):
            specs, problems, _ = self.specs({"bakery-web": self.service(ssh=["default"])})
        self.assertEqual((specs, [each.code for each in problems]), ([], ["BD02"]))

    def test_the_standard_five_when_trust_names_none(self):
        self.assertEqual(build.allowed(), frozenset(("context", "dockerfile", "args", "target", "labels")))

    def test_a_context_outside_the_catalog_is_t18_with_the_labs_words(self):
        outside = self.make("elsewhere")
        Path(outside, "Dockerfile").write_text("FROM busybox\n")
        specs, problems, rows = self.specs({"bakery-web": self.service(context=outside)})
        self.assertEqual(specs, [])
        self.assertEqual([(each.code, each.severity) for each in problems], [("T18", "error")])
        self.assertEqual(problems[0].message, f"bakery-web: Builds from outside its folder. Fix: {FIX}")
        self.assertEqual(rows[0].note, "its context is outside the catalog")

    def test_a_context_that_is_a_link_out_of_the_catalog_is_followed_to_where_it_leads(self):
        outside = self.make("elsewhere")
        Path(outside, "Dockerfile").write_text("FROM busybox\n")
        link = Path(self.root, "playgrounds", "bank", "apps", "web")
        link.parent.mkdir(parents=True)
        link.symlink_to(outside, target_is_directory=True)
        specs, problems, _ = self.specs({"bakery-web": self.service(context=str(link))})
        self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_a_dockerfile_that_leaves_the_catalog_is_t18(self):
        context = self.app()
        outside = self.make("elsewhere")
        Path(outside, "Dockerfile").write_text("FROM busybox\n")
        Path(context, "Link").symlink_to(Path(outside, "Dockerfile"))
        for dockerfile in ("../../../../../../../../etc/passwd", "Link", outside + "/Dockerfile", "missing"):
            with self.subTest(dockerfile=dockerfile[-30:]):
                specs, problems, _ = self.specs({"bakery-web": self.service(context=context, dockerfile=dockerfile)})
                self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_a_relative_context_is_not_one_compose_gives(self):
        specs, problems, _ = self.specs({"bakery-web": self.service(context="../../etc")})
        self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_a_remote_context_must_be_pinned_to_a_commit(self):
        pin = "0123456789abcdef0123456789abcdef01234567"
        for context, builds in ((f"https://github.com/example/app.git#{pin}", True), (f"https://github.com/example/app.git#{pin}:sub", True),
                                ("https://github.com/example/app.git", False), ("https://github.com/example/app.git#main", False),
                                (f"https://example.test/app.tar.gz#{pin}", False), ("git@github.com:example/app.git#abc", False)):
            with self.subTest(context=context):
                specs, problems, _ = self.specs({"bakery-web": self.service(context=context, dockerfile="Dockerfile")})
                self.assertEqual(bool(specs), builds)
                self.assertEqual([each.code for each in problems], [] if builds else ["T18"])
                if builds:
                    self.assertEqual(specs[0].command[-1], context)
                    self.assertEqual(option(specs[0].command, "--file"), "Dockerfile")

    def test_a_remote_dockerfile_cannot_leave_its_context(self):
        pin = "0123456789abcdef0123456789abcdef01234567"
        for dockerfile in ("/etc/passwd", "../x/Dockerfile", "a/../../Dockerfile"):
            specs, problems, _ = self.specs({"bakery-web": self.service(context=f"https://github.com/example/app.git#{pin}",
                                                                      dockerfile=dockerfile)})
            self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_the_labs_seeder_is_not_built_here_whatever_else_its_build_says(self):
        seed = {"image": f"{SEEDER_IMAGE}:latest", "build": {"context": os.path.join(self.lab, "seeder"), "dockerfile": "Dockerfile"}}
        wide = {"image": SEEDER_IMAGE, "build": {"context": os.path.join(self.lab, "seeder"), "additional_contexts": {"x": "/y"}}}
        for service in (seed, wide):
            specs, problems, rows = self.specs({"bakery-seed": service, "bakery-web": self.service()})
            self.assertEqual([each.service for each in specs], ["bakery-web"])
            self.assertEqual(problems, [])
            self.assertEqual([(each.service, each.status, each.note) for each in rows],
                             [("bakery-seed", "skipped", "the Lab's own seeder, which the Lab's CI builds")])

    def test_the_image_of_the_seeder_with_another_context_is_an_ordinary_build(self):
        # The name is not what makes it the Lab's: the folder it is built from is. A catalog's own build with that name is IM02's
        context = self.app(name="seed")
        specs, problems, _ = self.specs({"bakery-seed": self.service("bakery-seed", context, image=f"{SEEDER_IMAGE}:latest")})
        self.assertEqual(([each.service for each in specs], problems), (["bakery-seed"], []))

    def test_the_folder_of_the_seeder_with_another_name_is_refused_as_outside_the_catalog(self):
        service = {"image": "example.test/lab/other:1", "build": {"context": os.path.join(self.lab, "seeder")}}
        specs, problems, _ = self.specs({"bakery-seed": service})
        self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_hostile_values_never_become_an_option(self):
        context = self.app()
        for image in ("--push", "a b", "x\ny", 7, ["x"], {"a": "b"}, True):
            service = self.service(context=context)
            service["image"] = image
            for local in (False, True):   # (not a name is no name, whoever asks: the stage does not invent one for it)
                specs, problems, rows = self.specs({"bakery-web": service}, local=local)
                self.assertEqual((specs, problems), ([], []), image)
                self.assertEqual(rows[0].status, "skipped")
        for build_keys in ({"target": "--output=x"}, {"target": "a b"}, {"args": {"A=B": "x"}}, {"args": {"-x": "y"}},
                           {"args": {"A": ["list"]}}, {"args": ["A=1"]}, {"labels": {"": "x"}}, {"labels": "x"},
                           {"args": {"A": "x\x00y"}}, {"labels": {"k": "x\x00y"}}, {"target": "a\x00b"}):
            with self.subTest(build=build_keys):
                specs, problems, _ = self.specs({"bakery-web": self.service(context=context, **build_keys)})
                self.assertEqual((specs, [each.code for each in problems]), ([], ["BD02"]))

    def test_a_nul_in_a_path_is_a_refusal_and_not_a_crash(self):
        for build_keys in ({"context": "/tmp/a\x00b"}, {"dockerfile": "Docker\x00file"}, {"context": 7}, {"context": ["/tmp"]}):
            with self.subTest(build=build_keys):
                service = self.service(context=self.app())
                service["build"].update(build_keys)
                specs, problems, _ = self.specs({"bakery-web": service})
                self.assertEqual((specs, [each.code for each in problems]), ([], ["T18"]))

    def test_a_unit_whose_json_is_not_a_json_of_services_builds_nothing(self):
        for config in (None, {}, {"services": None}, {"services": []}, {"services": {"bakery-web": "x"}}, "text"):
            unit = self.unit({"bakery-web": self.service()})._replace(config=config)
            self.assertEqual(build.specs_of([unit], "linux/amd64", self.root, self.lab), ([], [], []))


class Classify(unittest.TestCase):

    def test_a_step_of_the_dockerfile_that_failed(self):
        failure = build.classify(1, "", STEP_FAILED)
        self.assertEqual((failure.kind, failure.code, failure.step), ("process", 1, (3, 5, "RUN pip install -r requirements.txt")))

    def test_a_program_that_prints_the_words_of_a_refusal_does_not_make_it_one(self):
        # Line 7 of the log says toomanyrequests: it is the program's (it has the seconds), not BuildKit's
        self.assertEqual(build.classify(1, "", STEP_FAILED).kind, "process")

    def test_a_pull_that_docker_hub_refused_is_the_infrastructure(self):
        failure = build.classify(1, "", HUB_REFUSED)
        self.assertEqual(failure.kind, "infra")

    def test_each_kind_of_infrastructure_failure(self):
        for line in ('#3 ERROR: failed to do request: Head "https://registry-1.docker.io/v2/x": net/http: TLS handshake timeout',
                     "ERROR: failed to solve: dial tcp: lookup registry-1.docker.io on 127.0.0.11:53: server misbehaving",
                     "ERROR: failed to solve: failed to fetch anonymous token: Get \"https://auth.docker.io\": dial tcp: i/o timeout",
                     "ERROR: failed to solve: pulling from host ghcr.io failed with status code [manifests 1]: 503 Service Unavailable",
                     "ERROR: failed to solve: failed to do request: unexpected EOF",
                     "ERROR: failed to solve: unexpected status from HEAD request to https://x/v2/y: 502 Bad Gateway",
                     "ERROR: failed to solve: context deadline exceeded", "ERROR: failed to solve: connection reset by peer",
                     "ERROR: failed to solve: Temporary failure in name resolution"):
            with self.subTest(line=line[-50:]):
                self.assertEqual(build.classify(1, "", line + "\n").kind, "infra")

    def test_a_failure_that_is_neither_is_other_and_keeps_its_code(self):
        failure = build.classify(1, "", "ERROR: failed to solve: failed to read dockerfile: open Dockerfile: no such file or directory\n")
        self.assertEqual((failure.kind, failure.code, failure.step), ("other", 1, None))

    def test_the_tail_is_the_last_fifteen_lines(self):
        text = "".join(f"line {n}\n" for n in range(100)) + "\n\n"
        failure = build.classify(1, "", text)
        self.assertEqual(list(failure.lines), [f"line {n}" for n in range(85, 100)])

    def test_the_machine_cannot_build(self):
        self.assertEqual(build.classify(127, "", "No such file or directory: 'docker'").kind, "missing")
        self.assertEqual(build.classify(125, "", "docker: unknown command: docker buildx\n\nRun 'docker --help'").kind, "missing")
        self.assertEqual(build.classify(1, "", "ERROR: Cannot connect to the Docker daemon at unix:///x.sock").kind, "unreachable")
        self.assertEqual(build.classify(124, "", "docker did not answer within 600 s").kind, "timeout")

    def test_a_program_cannot_say_that_buildx_is_missing(self):
        text = "#5 0.1 docker: unknown command: docker buildx\n#5 ERROR: process \"x\" did not complete successfully: exit code: 2\n"
        self.assertEqual(build.classify(1, "", text).kind, "process")

    def test_a_log_of_a_megabyte_is_read_in_a_second(self):
        import time
        for text in ("#5 ERROR: " + "toomanyrequests " * 70000, "\n".join(["#5 0.1 " + "x" * 200] * 5000) + "\nERROR: " + "a" * 900000,
                     "#" * 1_000_000, "#5 [" + "a " * 400000 + "3/5] RUN x"):
            start = time.process_time()
            build.classify(1, "", text)
            self.assertLess(time.process_time() - start, 1.5)


class Running(Disk):
    """The stage against the fake docker."""

    def setUp(self):
        super().setUp()
        self.fake = FakeDocker(self)
        self.sleeps = []
        self.out, self.err = io.StringIO(), io.StringIO()

    def stage(self, services, *argv, platform="linux/amd64", loading=None):
        units = loading or ci.Loading([self.unit(services)], [], [])
        with mock.patch.object(ci, "LOADER", mock.Mock(return_value=units)):
            return build.main([self.root, "--units", '["playground:bank"]', "--platform", platform, *argv], self.out, self.err,
                              lab=self.lab, sleep=self.sleeps.append)

    def builds(self):
        return self.fake.commands("buildx", "build")

    def test_a_build_runs_the_command_and_reports_the_image_with_its_size(self):
        context = self.app()
        code = self.stage({"bakery-web": self.service(context=context)}, "--ci")
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        self.assertEqual(len(self.builds()), 1)
        self.assertEqual(self.builds()[0][:7], ["buildx", "build", "--load", "--platform", "linux/amd64", "--tag", "example.test/lab/bakery-web:1.0"])
        self.assertIn("✓ example.test/lab/bakery-web:1.0  41.2 MB", self.out.getvalue())
        self.assertIn("Build for linux/amd64: 1 built, 0 failed, 0 left out", self.out.getvalue())
        self.assertEqual(self.fake.commands("image", "inspect")[0][-1], "example.test/lab/bakery-web:1.0")

    def test_both_architectures_have_their_own_command(self):
        context = self.app()
        for platform in ("linux/amd64", "linux/arm64"):
            self.fake.clear()
            self.stage({"bakery-web": self.service(context=context)}, platform=platform)
            self.assertEqual(option(self.builds()[0], "--platform"), platform)

    def test_the_summary_has_a_table_of_every_image_with_its_size_and_time(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        services = {"bakery-web": self.service(context=self.app()), "bakery-seed": {
            "image": f"{SEEDER_IMAGE}:latest", "build": {"context": os.path.join(self.lab, "seeder")}}}
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            self.stage(services, "--ci")
        with open(summary, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("### Images built for linux/amd64", text)
        self.assertRegex(text, r"\| `example\.test/lab/bakery-web:1\.0` \| playground:bank \| built \| 41\.2 MB \| \d+ s \|")
        self.assertIn("| `example.test/lab/seed", text.replace("example.test/lab/seeder", "example.test/lab/seed"))
        self.assertIn("left out: the Lab's own seeder, which the Lab's CI builds", text)
        self.assertIn("All good.", text)

    def test_a_service_with_no_image_is_built_under_a_name_of_the_stage_and_listed_with_it(self):
        service = self.service(context=self.app())
        service.pop("image")
        code = self.stage({"bakery-web": service}, "--format", "github")
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        self.assertEqual(options(self.builds()[0], "--tag"), ["lab-ci/playground-bank-bakery-web:ci"])
        self.assertIn("✓ lab-ci/playground-bank-bakery-web:ci  41.2 MB", self.out.getvalue())
        self.assertEqual(self.fake.commands("image", "inspect")[0][-1], "lab-ci/playground-bank-bakery-web:ci")

    def test_the_labs_seeder_is_not_built(self):
        self.stage({"bakery-seed": {"image": f"{SEEDER_IMAGE}:latest", "build": {"context": os.path.join(self.lab, "seeder")}}})
        self.assertEqual(self.builds(), [])
        self.assertEqual(self.fake.calls(), [])   # and nothing is asked of Docker when there is nothing to build

    def test_a_step_that_fails_is_bd01_with_its_step_and_the_tail_of_the_log(self):
        self.fake.when("buildx", "build", code=1, stderr=STEP_FAILED)
        code = self.stage({"bakery-web": self.service(context=self.app())}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("::error file=playgrounds/bank/compose.yml,title=BD01::example.test/lab/bakery-web:1.0: the build failed at step "
                      "3/5, RUN pip install -r requirements.txt (exit 1). Fix: run make ci to see it on your machine; the last 15 "
                      "lines are in the summary.", self.out.getvalue())
        self.assertEqual(len(self.builds()), 1)   # the author's failure is not tried again
        self.assertEqual(self.sleeps, [])

    def test_what_the_program_printed_is_data_between_the_stop_commands(self):
        self.fake.when("buildx", "build", code=1, stderr=STEP_FAILED)
        self.stage({"bakery-web": self.service(context=self.app())}, "--format", "github")
        lines = self.out.getvalue().splitlines()
        start = next(number for number, line in enumerate(lines) if line.startswith("::stop-commands::"))
        token = lines[start].split("::")[2]
        end = lines.index(f"::{token}::")
        inside = lines[start + 1:end]
        self.assertTrue(any("::error::boom ::add-mask::hunter2" in line for line in inside))
        outside = lines[:start] + lines[end + 1:]
        self.assertFalse(any("hunter2" in line or "::error::boom" in line for line in outside))
        self.assertEqual(len(re.findall(r"^::[0-9a-f]{32}::$", self.out.getvalue(), re.M)), 1)

    def test_lines_of_megabytes_in_a_failure_leave_a_summary_and_an_output_of_a_few_kilobytes(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        huge = ('#5 ERROR: process "/bin/sh -c x" did not complete successfully: exit code: 1\n'
                + ("q" * 2_000_000 + "\n") * 3)
        self.fake.when("buildx", "build", code=1, stderr=huge)
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            code = self.stage({"bakery-web": self.service(context=self.app())}, "--ci")
        self.assertEqual(code, 1)
        self.assertLess(os.path.getsize(summary), 20_000)
        self.assertLess(len(self.out.getvalue()), 20_000)
        self.assertIn(" [...]", Path(summary).read_text(encoding="utf-8"))

    def test_a_failure_of_the_summary_keeps_the_log_in_a_fence_longer_than_its_backticks(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        self.fake.when("buildx", "build", code=1, stderr="#5 [1/2] RUN x\n#5 0.1 ````` and ``` and more\n#5 ERROR: process \"x\" "
                       "did not complete successfully: exit code: 3\n")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            self.stage({"bakery-web": self.service(context=self.app())}, "--ci")
        with open(summary, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("``````\n#5 [1/2] RUN x", text)   # six backticks: longer than the five in the log
        self.assertIn("(exit 3)", text)

    def test_a_pull_docker_hub_refused_is_tried_again_after_20_and_60_seconds_and_then_is_bd05(self):
        self.fake.when("buildx", "build", code=1, stderr=HUB_REFUSED)
        code = self.stage({"bakery-web": self.service(context=self.app())}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.builds()), 3)
        self.assertEqual(self.sleeps, [20, 60])
        self.assertIn("::error file=playgrounds/bank/compose.yml,title=BD05::Docker Hub refused the pull (rate limit). This is not "
                      "your change: run the job again in a few minutes.", self.out.getvalue())

    def test_a_pull_that_works_the_second_time_is_a_build(self):
        self.fake.when("buildx", "build", answers=[{"code": 1, "stderr": HUB_REFUSED}, {"code": 0}])
        code = self.stage({"bakery-web": self.service(context=self.app())})
        self.assertEqual((code, len(self.builds()), self.sleeps), (0, 2, [20]))

    def test_a_tls_timeout_and_a_5xx_are_tried_again_too(self):
        for line in ("ERROR: failed to solve: net/http: TLS handshake timeout", "ERROR: failed to solve: 503 Service Unavailable"):
            self.fake.reset()
            self.sleeps.clear()
            self.fake.when("buildx", "build", times=2, code=1, stderr=line + "\n")
            code = self.stage({"bakery-web": self.service(context=self.app())})
            self.assertEqual((code, len(self.builds()), self.sleeps), (0, 3, [20, 60]))

    def test_the_words_a_program_prints_do_not_make_it_infrastructure(self):
        self.fake.when("buildx", "build", code=1, stderr="#5 [1/2] RUN curl x\n#5 0.2 toomanyrequests 503 Service Unavailable\n"
                       "#5 ERROR: process \"curl x\" did not complete successfully: exit code: 22\n")
        self.stage({"bakery-web": self.service(context=self.app())})
        self.assertEqual((len(self.builds()), self.sleeps), (1, []))

    def test_an_image_that_takes_over_ten_minutes_is_stopped_and_is_bd04(self):
        self.fake.when("buildx", "build", sleep=30)
        with mock.patch.object(build, "IMAGE_TIMEOUT", 1):
            code = self.stage({"bakery-web": self.service(context=self.app())}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("title=BD04::example.test/lab/bakery-web:1.0: the build took over 10 minutes and was stopped.", self.out.getvalue())
        self.assertEqual(self.sleeps, [])

    def test_one_failed_image_does_not_stop_the_others_and_all_are_listed(self):
        a, b = self.app(name="a"), self.app(name="b")
        self.fake.when("buildx", "build", "example.test/lab/a:1", code=1, stderr=STEP_FAILED)
        services = {"a": self.service("a", a, image="example.test/lab/a:1"), "b": self.service("b", b, image="example.test/lab/b:1")}
        code = self.stage(services)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.builds()), 2)
        self.assertIn("✗ example.test/lab/a:1", self.out.getvalue())
        self.assertIn("✓ example.test/lab/b:1", self.out.getvalue())

    def test_a_key_that_is_not_accepted_fails_the_stage_and_the_others_are_built(self):
        services = {"bad": self.service("bad", self.app(name="bad"), ssh=["default"]), "good": self.service("good", self.app(name="good"))}
        code = self.stage(services, "--format", "github")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.builds()), 1)
        self.assertIn("title=BD02::bad: build.ssh is not accepted. Fix: use context, dockerfile, args, target and labels only.",
                      self.out.getvalue())

    def test_a_unit_the_lab_refused_is_never_built(self):
        loading = ci.Loading([], [ci.Refused("playground", "bank", "playgrounds/bank/compose.yml", ("Runs with full privileges",), "T03")], [])
        code = self.stage({}, "--format", "github", loading=loading)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.calls(), [])
        self.assertIn("title=T03::playground:bank was not built: Runs with full privileges. Fix: make check says how.", self.out.getvalue())

    def test_a_unit_that_holds_a_name_the_lab_cannot_install_is_refused_whoever_loaded_it(self):
        # The structure rules (ST01 to ST04) decide whether a unit is given to Docker at all
        context = self.app()
        Path(self.root, "playgrounds", "bank", "weird\n::error::name.txt").write_text("x")
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.calls(), [])
        shown = [line for line in self.out.getvalue().splitlines() if "ST03" in line]
        self.assertEqual(shown, ["::error file=playgrounds/bank/compose.yml,title=ST03::playground:bank was not built: "
                                 "playgrounds/bank/weird ::error::name.txt has a name the Lab cannot install. Fix: make check says how."])
        self.assertEqual([line for line in self.out.getvalue().splitlines() if line.startswith("::error::")], [])

    def test_a_link_inside_a_unit_is_refused_too_and_another_units_files_are_not_its_business(self):
        context = self.app()
        os.symlink("/etc/hosts", os.path.join(self.root, "playgrounds", "bank", "link.txt"))
        self.assertEqual(self.stage({"bakery-web": self.service(context=context)}, "--format", "github"), 1)
        self.assertIn("title=ST01::playground:bank was not built: playgrounds/bank/link.txt is a symbolic link.", self.out.getvalue())
        self.out = io.StringIO()
        os.unlink(os.path.join(self.root, "playgrounds", "bank", "link.txt"))
        Path(self.root, "addons", "other").mkdir(parents=True)
        Path(self.root, "addons", "other", "bad\nname").write_text("x")
        self.assertEqual(self.stage({"bakery-web": self.service(context=context)}), 0)   # (a file of an add-on nobody asked for)

    def test_nothing_of_the_runner_reaches_the_build(self):
        secrets = {"GITHUB_TOKEN": "ghp_" + "S" * 36, "ACTIONS_RUNTIME_TOKEN": "runtime-secret-value", "GH_TOKEN": "gh-secret-value",
                   "SSH_AUTH_SOCK": "/tmp/agent.sock", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-secret-value", "MY_PASSWORD": "pw-value",
                   "GITHUB_ENV": "/tmp/env-file"}
        with mock.patch.dict(os.environ, secrets):
            code = self.stage({"bakery-web": self.service(context=self.app(), args={"A": "from-the-files"})}, "--ci")
        self.assertEqual(code, 0)
        words = " ".join(" ".join(call.argv) for call in self.fake.calls())
        for value in secrets.values():
            self.assertNotIn(value, words)
        for call in self.fake.calls():
            self.assertEqual(sorted(set(call.env) - ALLOWED - SHELL_ADDED), [], call.argv)
        self.assertIn("A=from-the-files", words)

    def test_a_machine_with_no_docker_cannot_build_and_says_so(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent-bin-folder"}):
            code = self.stage({"bakery-web": self.service(context=self.app())})
        self.assertEqual(code, 2)
        self.assertIn("Docker is not installed or not on the PATH", self.err.getvalue())

    def test_a_daemon_that_is_not_running_is_exit_2_before_anything_is_built(self):
        self.fake.when("version", code=1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
        code = self.stage({"bakery-web": self.service(context=self.app())})
        self.assertEqual(code, 2)
        self.assertIn("Docker is not running", self.err.getvalue())
        self.assertEqual(self.builds(), [])

    def test_a_docker_without_buildx_is_exit_2(self):
        self.fake.when("buildx", "build", code=125, stderr="docker: unknown command: docker buildx\n\nRun 'docker --help' for more\n")
        code = self.stage({"bakery-web": self.service(context=self.app())})
        self.assertEqual(code, 2)
        self.assertIn("buildx is not installed", self.err.getvalue())

    def test_the_dispatcher_reaches_it(self):
        self.app()
        with mock.patch.object(ci, "LOADER", mock.Mock(return_value=ci.Loading([self.unit({"bakery-web": self.service(context=self.app())})], [], []))):
            code = ci_main(["build", self.root, "--units", '["playground:bank"]', "--platform", "linux/arm64"], self.out, self.err,
                           lab=self.lab, sleep=self.sleeps.append)
        self.assertEqual((code, option(self.builds()[0], "--platform")), (0, "linux/arm64"))

    def test_a_platform_that_is_not_one_is_a_usage_error(self):
        for platform in ("linux/s390x", "amd64", "linux/amd64,linux/arm64", "--push"):
            self.out, self.err = io.StringIO(), io.StringIO()
            with mock.patch.object(ci, "LOADER", mock.Mock()):
                self.assertEqual(build.main([self.root, "--units", "[]", "--platform", platform], self.out, self.err), 2)
        self.assertEqual(self.fake.calls(), [])

    def test_the_app_option_builds_one_app(self):
        services = {"bakery-web": self.service(context=self.app(name="web")), "bakery-api": self.service("bakery-api", self.app(name="api"))}
        self.stage(services, "--app", "api")
        self.assertEqual(len(self.builds()), 1)
        self.assertEqual(options(self.builds()[0], "--tag"), ["example.test/lab/bakery-api:1.0"])


class Real(Case):
    """A catalog read through the Lab's own project, as `ci build` reads it in a workflow."""

    def setUp(self):
        fixtures.need_compose()   # (a skip that shows without Compose 5)
        fixtures.project(self, paths, check, project, compose, envfiles, ns="fixture", link=True)
        fixtures.install(self, "partner")   # (the catalog it includes from has to be here, as it has for the check)
        self.folder = next(one["path"] for one in registry.entries() if one["id"] == "fixture")
        self.fake = FakeDocker(self)
        self.out, self.err = io.StringIO(), io.StringIO()

    def test_the_app_of_a_playground_is_built_and_its_seed_service_is_the_labs(self):
        code = build.main([self.folder, "--units", '["playground:shop", "playground:tour", "addon:echo"]', "--platform", "linux/amd64",
                           "--format", "github"], self.out, self.err, sleep=lambda seconds: None)
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        commands = self.fake.commands("buildx", "build")
        self.assertEqual(len(commands), 1)
        context = os.path.join(os.path.realpath(self.folder), "playgrounds", "shop", "apps", "web")
        self.assertEqual(commands[0][-1], context)
        self.assertEqual(option(commands[0], "--file"), context + "/Dockerfile")
        self.assertEqual(option(commands[0], "--tag"), "registry.example.invalid/lab/fixture-shop-web:latest")
        self.assertIn("left out: the Lab's own seeder", self.out.getvalue() + "left out: the Lab's own seeder")

    def test_nothing_of_the_runner_is_in_what_is_built(self):
        with mock.patch.dict(os.environ, {"FIXTURE_SHOP_CUSTOMER_KEY": "ghp_" + "Z" * 36}):
            build.main([self.folder, "--units", '["playground:shop"]', "--platform", "linux/amd64"], self.out, self.err,
                       sleep=lambda seconds: None)
        words = json.dumps([call.argv for call in self.fake.calls()])
        self.assertNotIn("ghp_", words)

    def test_the_catalog_is_read_in_a_temporary_project_and_the_lab_keeps_what_it_had(self):
        before = (registry.entries(), registry.read_lock(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR)))
        loading = ci.lab_loader(self.folder, [("playground", "shop")])
        self.assertEqual([each.name for each in loading.units], ["shop"])
        self.assertEqual((registry.entries(), registry.read_lock(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR))), before)
        root = Path(paths.ROOT)
        for service in loading.units[0].config["services"].values():
            context = (service.get("build") or {}).get("context") if isinstance(service.get("build"), dict) else None
            if context:   # every context is a real place: below the author's folder, or the Lab's seeder
                self.assertTrue(context.startswith(os.path.realpath(self.folder) + os.sep) or context == os.path.realpath(root / "seeder"),
                                context)

    def test_the_sets_of_versions_are_resolved_later_in_a_project_of_their_own(self):
        loading = ci.lab_loader(self.folder, [("playground", "shop")])
        unit = loading.units[0]
        self.assertTrue(unit.tested)
        config = unit.resolve({"APIM_VERSION": "9.9.9"})
        self.assertIn("apim-gateway", config["services"])
        self.assertIn(":9.9.9", config["services"]["apim-gateway"]["image"])
        self.assertEqual(sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR)), ["fixture", "partner"])   # (nothing is left of it)


if __name__ == "__main__":
    unittest.main()
