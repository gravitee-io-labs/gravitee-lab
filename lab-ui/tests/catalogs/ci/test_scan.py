"""`catalog.py ci scan-published` and `ci scan-built` (SC01, SC02): the published images, and the images built for a change, have no
critical flaw that has a fix, and the rest is said.

Docker is a function that plays the registry, `docker save` and the scanner, so the tests prove what runs and what is kept from it:
the scanner is the one pinned by digest in the Lab, it fetches its databases in a step of its own that has no image and no secret, the
scan has no network, a read-only root, no capability and the user of the runner, the token is passed to `docker login` on standard input
(a seam here) and is on no command line and in no environment, only critical flaws with a fix are errors and the others are one warning
for each image, an image that is not published yet is said and is no fault, and the images built for a change are scanned as this
machine holds them, with no pull and no sign-in."""

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import ci, explain
from lab_ui.catalogs.ci import main as ci_main
from lab_ui.catalogs.ci import scan
from tests import Case
from tests.fakedocker import FakeDocker, options

PREFIX = "ghcr.io/acme/lab-hub"
SCANNER = "ghcr.io/aquasecurity/trivy:0.58.0@sha256:" + "ab" * 32
TOKEN = "ghs_" + "T" * 36


def catalog_yml(images=PREFIX):
    return ("x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  description: Demos\n  version: 1.0.0\n  lab: 1\n"
            + (f"  images: {images}\n" if images else ""))


def vulnerability(identity="CVE-2026-1000", package="openssl", version="3.0.1", fixed="3.0.2", severity="CRITICAL"):
    return {"VulnerabilityID": identity, "PkgName": package, "InstalledVersion": version, "FixedVersion": fixed, "Severity": severity}


def trivy_report(*vulnerabilities, target="alpine"):
    return {"SchemaVersion": 2, "Results": [{"Target": target, "Vulnerabilities": list(vulnerabilities)}]}


class Docker:
    """Plays docker: the registry (`pull` knows the images it was told), `save`, and the scanner, which answers the report it was
    told for the image it is given. Every call is recorded."""

    def __init__(self):
        self.calls, self.pullable, self.reports, self.failures, self.absent = [], set(), {}, {}, set()
        self.reading = None

    def __call__(self, argv, timeout, cwd):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "version"]:
            return 0, "27.0.0\n", ""
        if argv[:3] == ["docker", "image", "inspect"]:
            return (1, "", f"Error: No such image: {argv[-1]}") if argv[-1] in self.absent else (0, "", "")
        for word, answer in self.failures.items():
            if word in argv:
                return answer
        if argv[:2] == ["docker", "pull"]:
            reference = argv[-1]
            if reference.startswith("ghcr.io/aquasecurity") or reference in self.pullable:
                return 0, "", ""
            return 1, "", f"Error response from daemon: manifest unknown: {reference}"
        if argv[:2] == ["docker", "save"]:
            Path(options(argv, "--output")[0]).write_bytes(b"an archive of " + argv[-1].encode())
            self.reading = argv[-1]
            return 0, "", ""
        if argv[:2] == ["docker", "run"] and "--input" in argv:
            mounts = options(argv, "-v")
            archive = next(each for each in mounts if each.endswith(":/image.tar:ro")).split(":")[0]
            out = next(each for each in mounts if each.endswith(":/out")).split(":")[0]
            reference = Path(archive).read_bytes().decode().replace("an archive of ", "")
            document = self.reports.get(reference, trivy_report())
            Path(out, "report.json").write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
            return 0, "", ""
        return 0, "", ""

    def runs(self):
        return [call for call in self.calls if call[:2] == ["docker", "run"]]


class Fixture(Case):

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.work = Path(holder.name).resolve()
        self.repo = self.work / "catalog"
        self.repo.mkdir()
        (self.repo / "catalog.yml").write_text(catalog_yml(), encoding="utf-8")
        self.lab = self.work / "lab"
        (self.lab / "seeder").mkdir(parents=True)
        (self.lab / "seeder" / "Dockerfile").write_text("FROM scratch\n")
        (self.lab / "docker-compose.yml").write_text("x-gravitee:\n  seeder:\n    image: example.test/lab/seeder\nservices: {}\n")
        for app in ("web", "api"):
            folder = self.repo / "playgrounds" / "bank" / "apps" / app
            folder.mkdir(parents=True)
            (folder / "Dockerfile").write_text("FROM busybox:1.36\n")
        (self.repo / "playgrounds" / "bank" / "compose.yml").write_text("services: {}\n")
        self.tools = self.work / "Dockerfile"
        self.tools.write_text(f"FROM {SCANNER} AS trivy\nFROM ghcr.io/gitleaks/gitleaks:v8@sha256:" + "cd" * 32 + " AS gitleaks\n")
        self.docker = Docker()
        self.docker.pullable = {f"{PREFIX}/bakery-web:latest", f"{PREFIX}/bakery-api:latest"}
        self.out, self.err = io.StringIO(), io.StringIO()
        self.logins = []

    def services(self):
        return {"bakery-web": {"image": f"{PREFIX}/bakery-web:latest", "build": {"context": str(self.repo / "playgrounds/bank/apps/web")}},
                "bakery-api": {"image": f"{PREFIX}/bakery-api:latest", "build": {"context": str(self.repo / "playgrounds/bank/apps/api")}},
                "bakery-seed": {"image": "example.test/lab/seeder:latest", "build": {"context": str(self.lab / "seeder")},
                              "labels": {"io.gravitee.role": "seed"}}}

    def loader(self):
        def load(folder, names):
            services = self.services()
            return ci.Loading([ci.Loaded("playground", "bank", "bank", "playgrounds/bank/compose.yml",
                                         str(self.repo / "playgrounds/bank"), {"services": services}, tuple(services), (), None)],
                              [], [])
        return load

    def login(self, registry, user, token, config):
        self.logins.append((registry, user, token, config))
        return 0, ""

    def run_stage(self, *argv, environ=None, **seams):
        seams.setdefault("runner", self.docker)
        seams.setdefault("login", self.login)
        seams.setdefault("lab", str(self.lab))
        seams.setdefault("dockerfile", self.tools)
        seams.setdefault("environ", {"GH_TOKEN": TOKEN} if environ is None else environ)
        with mock.patch.object(ci, "LOADER", self.loader()):
            return scan.main([str(self.repo), *argv], self.out, self.err, **seams)


class Scanning(Fixture):

    def test_a_critical_flaw_with_a_fix_is_sc01_the_exit_code_is_1_and_the_rest_is_one_sc02_warning_for_the_image(self):
        self.docker.reports[f"{PREFIX}/bakery-web:latest"] = trivy_report(
            vulnerability(), vulnerability("CVE-2026-2000", "zlib", "1.2.11", "1.2.12"), vulnerability("CVE-2026-3000", severity="HIGH"),
            vulnerability("CVE-2026-4000", fixed=""), vulnerability("CVE-2026-1000"))
        code = self.run_stage("--format", "json")
        self.assertEqual(code, 1)
        found = json.loads(self.out.getvalue())
        self.assertEqual([(each["code"], each["severity"], each["message"]) for each in found], [
            ("SC01", "error", f"{PREFIX}/bakery-web:latest: CVE-2026-1000 in openssl 3.0.1, fixed in 3.0.2. Fix: update the package or the "
                              "base image."),
            ("SC01", "error", f"{PREFIX}/bakery-web:latest: CVE-2026-2000 in zlib 1.2.11, fixed in 1.2.12. Fix: update the package or the "
                              "base image."),
            ("SC02", "warning", f"{PREFIX}/bakery-web:latest: 2 more flaws that fail nothing: 1 high and 1 critical with no fix yet "
                                "(CVE-2026-3000, CVE-2026-4000). Fix: update the base image or the packages once a fix is out.")])

    def test_images_with_nothing_to_fix_say_all_good(self):
        self.assertEqual(self.run_stage(), 0, self.err.getvalue())
        text = self.out.getvalue()
        self.assertIn("Published images: 2 scanned, 0 flaws to fix", text)
        self.assertTrue(text.endswith("All good\n"))
        self.assertEqual(sorted(call[-1] for call in self.docker.calls if call[:3] == ["docker", "pull", "--quiet"]),
                         [f"{PREFIX}/bakery-api:latest", f"{PREFIX}/bakery-web:latest"])

    def test_the_messages_are_the_words_of_the_standard(self):
        for code in ("SC01", "SC02"):
            row = next(row for row in explain.STAGES if row[0] == code)
            self.assertEqual((row[1], row[3]), scan.MESSAGES[code])

    def test_the_seeder_is_the_labs_and_is_not_scanned(self):
        self.run_stage()
        self.assertFalse([call for call in self.docker.calls if any("seeder" in word for word in call)])

    def test_the_tag_is_latest_unless_another_is_asked(self):
        self.docker.pullable = {f"{PREFIX}/bakery-web:1.2.0", f"{PREFIX}/bakery-api:1.2.0"}
        self.assertEqual(self.run_stage("--tag", "1.2.0"), 0)
        self.assertIn(f"Scanned {PREFIX}/bakery-web:1.2.0", self.out.getvalue())
        for bad in ("a b", "-x", "x;y", ""):
            self.err = io.StringIO()
            self.assertEqual(self.run_stage("--tag", bad), 2)

    def test_an_image_that_is_not_published_yet_is_said_and_is_no_fault(self):
        self.docker.pullable = {f"{PREFIX}/bakery-web:latest"}
        self.assertEqual(self.run_stage(), 0)
        text = self.out.getvalue()
        self.assertIn("1 scanned", text)
        self.assertIn(f"{PREFIX}/bakery-api:latest is not in the registry yet, so it was not scanned", text)
        self.assertNotIn("All good", text)

    def test_an_image_the_registry_refuses_is_not_a_verdict(self):
        self.docker.failures[f"{PREFIX}/bakery-api:latest"] = (1, "", "Error response from daemon: denied: permission_denied")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("could not be pulled: Error response from daemon: denied", self.err.getvalue())

    def test_a_catalog_that_names_no_registry_has_nothing_to_scan(self):
        (self.repo / "catalog.yml").write_text(catalog_yml(""), encoding="utf-8")
        self.assertEqual(self.run_stage(), 0)
        self.assertIn("catalog.yml names no registry in images", self.out.getvalue())
        self.assertEqual(self.docker.calls, [])

    def test_a_registry_that_is_not_ghcr_is_a_usage_error(self):
        (self.repo / "catalog.yml").write_text(catalog_yml("registry.example.test/acme/lab-hub"), encoding="utf-8")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("which is not ghcr.io/<owner>/<repository>", self.err.getvalue())

    def test_a_catalog_whose_unit_cannot_be_read_is_an_error_and_not_a_scan_of_a_part(self):
        blocked = ci.Refused("addon", "bad", "addons/bad/compose.yml", ("Mounts the Docker socket",), "T10")

        def load(folder, names):
            return ci.Loading([], [blocked], [])
        (self.repo / "addons" / "bad").mkdir(parents=True)
        (self.repo / "addons" / "bad" / "compose.yml").write_text("services: {}\n")
        with mock.patch.object(ci, "LOADER", load):
            code = scan.main([str(self.repo), "--format", "json"], self.out, self.err, runner=self.docker, login=self.login,
                             lab=str(self.lab), dockerfile=self.tools, environ={})
        self.assertEqual(code, 1)
        self.assertEqual([each["code"] for each in json.loads(self.out.getvalue())], ["T10"])


class TheScannerAndWhatItIsGiven(Fixture):

    def test_the_scanner_is_the_one_pinned_by_digest_in_the_labs_tools(self):
        self.run_stage()
        for call in self.docker.runs():
            self.assertIn(SCANNER, call)

    def test_the_databases_come_in_a_step_that_has_the_network_and_nothing_else(self):
        self.run_stage()
        downloads = [call for call in self.docker.runs() if "--download-db-only" in call or "--download-java-db-only" in call]
        self.assertEqual(len(downloads), 2)
        for call in downloads:
            self.assertNotIn("--network", call)
            self.assertEqual(len(options(call, "-v")), 1)
            self.assertTrue(options(call, "-v")[0].endswith(":/cache"))
            self.assertNotIn("-e", [word for word in call if word == "-e" and call[call.index(word) + 1] != "HOME=/tmp"])
            self.assertIn("--read-only", call)
            self.assertIn("ALL", options(call, "--cap-drop"))
            self.assertFalse(any("/image" in word or "/repo" in word for word in call))

    def test_the_scan_has_no_network_a_read_only_root_no_capability_and_the_user_of_the_runner(self):
        self.run_stage()
        scans = [call for call in self.docker.runs() if "--input" in call]
        self.assertEqual(len(scans), 2)
        for call in scans:
            self.assertEqual(options(call, "--network"), ["none"])
            self.assertIn("--read-only", call)
            self.assertEqual(options(call, "--cap-drop"), ["ALL"])
            self.assertEqual(options(call, "--security-opt"), ["no-new-privileges"])
            self.assertEqual(options(call, "--pids-limit"), ["256"])
            self.assertEqual(options(call, "--user"), [f"{os.getuid()}:{os.getgid()}"])
            self.assertEqual([word for word in options(call, "-v") if word.endswith(":ro")], [
                next(word for word in options(call, "-v") if word.endswith(":/image.tar:ro"))])
            tail = call[call.index(SCANNER) + 1:]
            self.assertEqual(tail[:3], ["image", "--input", "/image.tar"])
            for flag, value in (("--severity", "CRITICAL,HIGH"), ("--format", "json"), ("--output", "/out/report.json"),
                                ("--exit-code", "0"), ("--cache-dir", "/cache"), ("--scanners", "vuln")):
                self.assertEqual(options(tail, flag), [value])
            for flag in ("--skip-db-update", "--skip-java-db-update"):
                self.assertIn(flag, tail)
            self.assertNotIn("--ignore-unfixed", tail, "a flaw with no fix is said, so the scanner is asked for it")
            self.assertFalse([word for word in call if "docker.sock" in word or word == "--privileged"])

    def test_the_scanner_is_given_no_token_and_no_catalog(self):
        self.run_stage()
        words = json.dumps(self.docker.runs())
        self.assertNotIn(TOKEN, words)
        self.assertNotIn(str(self.repo), words)
        self.assertNotIn("GH_TOKEN", words)

    def test_the_scan_works_in_folders_of_its_own_that_are_gone_afterwards(self):
        seen = []
        original = self.docker.__call__

        def watching(argv, timeout, cwd):
            for word in argv:
                if "gravitee-lab-scan-" in word:
                    seen.append(word.split(":")[0])
            return original(argv, timeout, cwd)
        self.run_stage(runner=watching)
        self.assertTrue(seen)
        for place in seen:
            self.assertFalse(os.path.exists(place), place)
            self.assertTrue(place.startswith(str(Path(tempfile.gettempdir()).resolve())) or "gravitee-lab-scan-" in place)

    def test_a_report_the_scanner_did_not_write_or_that_is_not_json_is_not_a_clean_scan(self):
        self.docker.reports[f"{PREFIX}/bakery-web:latest"] = "not json at all"
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("the report of the scan is not JSON", self.err.getvalue())

    def test_a_scanner_that_fails_is_not_a_clean_scan(self):
        self.docker.failures["--input"] = (1, "", "FATAL failed to analyze the image")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("did not run (exit 1): FATAL failed to analyze the image", self.err.getvalue())

    def test_a_database_that_cannot_be_fetched_is_not_a_clean_scan(self):
        self.docker.failures["--download-db-only"] = (1, "", "FATAL db download failed: no route to host")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("could not fetch its db database", self.err.getvalue())

    def test_an_image_that_cannot_be_saved_is_not_a_clean_scan(self):
        self.docker.failures["save"] = (1, "", "Error response from daemon: no space left on device")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("could not be saved to scan it", self.err.getvalue())

    def test_the_scanner_that_is_not_pinned_yet_is_not_a_clean_scan(self):
        self.tools.write_text("FROM ghcr.io/aquasecurity/trivy:0.58.0@sha256:" + "0" * 64 + " AS trivy\n")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("is not pinned yet", self.err.getvalue())
        self.err = io.StringIO()
        self.tools.write_text("FROM ghcr.io/aquasecurity/trivy:latest AS trivy\n")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("not pinned by digest", self.err.getvalue())
        self.err = io.StringIO()
        self.tools.write_text("FROM ghcr.io/gitleaks/gitleaks:v8@sha256:" + "ab" * 32 + " AS gitleaks\n")
        self.assertEqual(self.run_stage(), 2)
        self.assertIn("no FROM line named trivy", self.err.getvalue())
        self.assertEqual(self.docker.calls, [])

    def test_the_labs_own_tools_file_names_the_scanner_or_the_stage_says_what_is_missing(self):
        """Today's Dockerfile of the tools has no trivy line: the stage says so and runs nothing, and the line is a task of the owner
        of that file (the digest is resolved with `docker buildx imagetools inspect`, which needs a network)."""
        with mock.patch.object(ci, "LOADER", self.loader()):
            code = scan.main([str(self.repo)], self.out, self.err, runner=self.docker, login=self.login, lab=str(self.lab),
                             environ={})
        text = self.err.getvalue()
        if "no FROM line named trivy" in text:
            self.assertEqual((code, self.docker.calls), (2, []))
        else:
            self.assertIn(code, (0, 1, 2))


class Built(Fixture):
    """`ci scan-built`: the images `ci build` built for the touched units, scanned as this machine holds them."""

    UNITS = json.dumps(["playground:bank"])

    def run_built(self, *argv, **seams):
        seams.setdefault("runner", self.docker)
        seams.setdefault("login", self.login)
        seams.setdefault("lab", str(self.lab))
        seams.setdefault("dockerfile", self.tools)
        with mock.patch.object(ci, "LOADER", self.loader()):
            return scan.built_main([str(self.repo), "--units", self.UNITS, *argv], self.out, self.err, **seams)

    def test_the_images_built_are_scanned_where_they_are_with_no_pull_and_no_sign_in_even_with_a_token_around(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": TOKEN}):
            self.assertEqual(self.run_built(), 0, self.err.getvalue())
        self.assertEqual(self.logins, [])
        self.assertFalse([call for call in self.docker.calls if call[:3] == ["docker", "pull", "--quiet"]])
        self.assertEqual(sorted(call[-1] for call in self.docker.calls if call[:2] == ["docker", "save"]),
                         [f"{PREFIX}/bakery-api:latest", f"{PREFIX}/bakery-web:latest"])
        self.assertIn("Built images: 2 scanned, 0 flaws to fix", self.out.getvalue())
        self.assertTrue(self.out.getvalue().endswith("All good\n"))

    def test_a_critical_flaw_with_a_fix_in_an_image_the_change_built_fails_the_stage_with_the_words_of_the_nightly(self):
        self.docker.reports[f"{PREFIX}/bakery-api:latest"] = trivy_report(vulnerability("GHSA-6q48-6c7w-9gw2", "certifi", "2026.1.1",
                                                                                         "2026.10.19"))
        self.assertEqual(self.run_built("--format", "json"), 1)
        self.assertEqual([(each["code"], each["message"]) for each in json.loads(self.out.getvalue())], [
            ("SC01", f"{PREFIX}/bakery-api:latest: GHSA-6q48-6c7w-9gw2 in certifi 2026.1.1, fixed in 2026.10.19. Fix: update the package or "
                     "the base image.")])

    def test_the_scan_of_the_images_built_runs_the_same_hardened_scanner_with_no_network(self):
        self.run_built()
        scans = [call for call in self.docker.runs() if "--input" in call]
        self.assertEqual(len(scans), 2)
        for call in scans:
            self.assertIn(SCANNER, call)
            self.assertEqual(options(call, "--network"), ["none"])
            self.assertEqual(call[:len(scan.HARDENED) + 2], ["docker", "run", *scan.HARDENED])

    def test_an_image_that_was_not_built_here_is_said_and_the_build_is_what_fails(self):
        self.docker.absent = {f"{PREFIX}/bakery-api:latest"}
        self.assertEqual(self.run_built(), 0)
        text = self.out.getvalue()
        self.assertIn("Built images: 1 scanned", text)
        self.assertIn(f"{PREFIX}/bakery-api:latest was not built here, so it was not scanned", text)
        self.assertNotIn("All good", text)

    def test_the_seeder_is_the_labs_and_is_not_scanned(self):
        self.run_built()
        self.assertFalse([call for call in self.docker.calls if any("seeder" in word for word in call)])

    def test_one_app_alone_is_scanned_when_asked(self):
        self.assertEqual(self.run_built("--app", "web"), 0)
        self.assertEqual([call[-1] for call in self.docker.calls if call[:2] == ["docker", "save"]], [f"{PREFIX}/bakery-web:latest"])

    def test_a_change_that_builds_nothing_scans_nothing_and_needs_no_docker(self):
        def load(folder, names):
            return ci.Loading([ci.Loaded("addon", "plain", "plain", "addons/plain/compose.yml", str(self.repo / "addons/plain"),
                                         {"services": {"plain": {"image": "busybox:1.36"}}}, ("plain",), (), None)], [], [])
        with mock.patch.object(ci, "LOADER", load):
            code = scan.built_main([str(self.repo), "--units", '["addon:plain"]'], self.out, self.err, runner=self.docker,
                                   lab=str(self.lab), dockerfile=self.tools)
        self.assertEqual((code, self.docker.calls), (0, []))
        self.assertIn("the change builds no image, so nothing is scanned", self.out.getvalue())

    def test_a_unit_the_policy_refuses_is_an_error_and_nothing_is_scanned(self):
        blocked = ci.Refused("addon", "bad", "addons/bad/compose.yml", ("Mounts the Docker socket",), "T10")

        def load(folder, names):
            return ci.Loading([], [blocked], [])
        with mock.patch.object(ci, "LOADER", load):
            code = scan.built_main([str(self.repo), "--units", '["addon:bad"]', "--format", "json"], self.out, self.err,
                                   runner=self.docker, lab=str(self.lab), dockerfile=self.tools)
        self.assertEqual(code, 1)
        self.assertEqual([each["code"] for each in json.loads(self.out.getvalue())], ["T10"])
        self.assertEqual(self.docker.calls, [])

    def test_a_scanner_that_is_not_pinned_yet_is_a_scan_that_could_not_run(self):
        self.tools.write_text("FROM ghcr.io/aquasecurity/trivy:0.58.0@sha256:" + "0" * 64 + " AS trivy\n")
        self.assertEqual(self.run_built(), 2)
        self.assertIn("is not pinned yet", self.err.getvalue())


class SigningIn(Fixture):

    def test_the_token_is_read_from_the_environment_and_goes_to_the_login_alone(self):
        self.run_stage(environ={"GH_TOKEN": TOKEN, "GITHUB_TOKEN": "other"}, runner=self.docker)
        self.assertEqual(len(self.logins), 1)
        registry, user, token, config = self.logins[0]
        self.assertEqual((registry, token), ("ghcr.io", TOKEN))
        self.assertEqual(user, "acme")   # (the owner of the repository, when the run names no actor)
        self.assertIn("gravitee-lab-scan-", config)
        for call in self.docker.calls:
            self.assertNotIn(TOKEN, " ".join(call))

    def test_the_actor_of_the_run_is_the_user_when_there_is_one(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTOR": "octocat"}):
            self.run_stage()
        self.assertEqual(self.logins[0][1], "octocat")

    def test_the_github_token_is_the_second_choice_and_no_token_means_no_login(self):
        self.run_stage(environ={"GITHUB_TOKEN": "second"})
        self.assertEqual(self.logins[0][2], "second")
        self.logins.clear()
        self.run_stage(environ={})
        self.assertEqual(self.logins, [])

    def test_the_login_comes_after_the_databases_and_before_the_first_image(self):
        self.run_stage()
        order = []
        for call in self.docker.calls:
            if "--download-db-only" in call:
                order.append("db")
            if call[:3] == ["docker", "pull", "--quiet"]:
                order.append("pull")
        self.assertEqual(order, ["db", "pull", "pull"])

    def test_a_sign_in_that_fails_is_not_a_clean_scan(self):
        def refuse(registry, user, token, config):
            return 1, "Error response from daemon: Get https://ghcr.io/v2/: denied\nsecond line"
        self.assertEqual(self.run_stage(login=refuse), 2)
        self.assertIn("the sign-in to ghcr.io failed", self.err.getvalue())
        self.assertNotIn(TOKEN, self.err.getvalue())

    def test_the_real_login_passes_the_token_on_standard_input_and_in_no_argument_or_variable(self):
        fake = FakeDocker(self)
        (fake.folder / "real.json").write_text("null", encoding="utf-8")   # (no call of this test reaches the real Docker)
        calls = []
        done = mock.Mock(returncode=0, stderr="")

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return done
        with mock.patch.object(scan.subprocess, "run", run):
            code, _ = scan.login("ghcr.io", "octocat", TOKEN, "/tmp/config")
        argv, kwargs = calls[0]
        self.assertEqual(code, 0)
        self.assertEqual(argv, ["docker", "login", "ghcr.io", "--username", "octocat", "--password-stdin"])
        self.assertEqual(kwargs["input"], TOKEN)
        self.assertEqual(kwargs["env"]["DOCKER_CONFIG"], "/tmp/config")
        self.assertNotIn(TOKEN, json.dumps({key: str(value) for key, value in kwargs["env"].items()}))
        self.assertFalse([name for name in kwargs["env"] if name.startswith(("ACTIONS_", "GITHUB_", "GH_"))])
        self.assertEqual(fake.calls(), [])

    def test_the_real_stage_over_a_docker_on_the_path_keeps_every_secret_out_of_every_call(self):
        fake = FakeDocker(self)
        (fake.folder / "real.json").write_text("null", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GH_TOKEN": TOKEN, "GITHUB_TOKEN": TOKEN, "ACTIONS_RUNTIME_TOKEN": "r" * 30}):
            with mock.patch.object(ci, "LOADER", self.loader()):
                scan.main([str(self.repo)], self.out, self.err, lab=str(self.lab), dockerfile=self.tools, login=self.login)
        self.assertTrue(fake.commands("run"))
        for call in fake.calls():
            self.assertNotIn(TOKEN, " ".join(call.argv))
            self.assertFalse(set(call.env) & {"GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"})


class Reports(unittest.TestCase):

    def test_only_critical_flaws_with_a_fix_are_errors_each_once_in_order_and_a_low_one_is_not_even_said(self):
        document = {"Results": [
            {"Vulnerabilities": [vulnerability("CVE-2026-2", "b"), vulnerability("CVE-2026-1", "a"), vulnerability("CVE-2026-2", "b")]},
            {"Vulnerabilities": [vulnerability("CVE-2026-0", "c", severity="LOW"), vulnerability("CVE-2026-9", "d", fixed="  ")]},
            {"Target": "no list"}, "not a result", {"Vulnerabilities": None}]}
        found = scan.findings_of("ghcr.io/o/r/x:latest", document)
        self.assertEqual([(each.code, each.message.split(" ")[1]) for each in found],
                         [("SC01", "CVE-2026-1"), ("SC01", "CVE-2026-2"), ("SC02", "1")])
        self.assertIn("1 critical with no fix yet (CVE-2026-9)", found[-1].message)
        self.assertNotIn("CVE-2026-0", found[-1].message)

    def test_the_rest_names_its_first_identifiers_and_counts_the_others(self):
        document = trivy_report(*[vulnerability(f"CVE-2026-{number}", f"p{number}", severity="HIGH") for number in range(10, 18)])
        found = scan.findings_of("x", document)
        self.assertEqual([each.code for each in found], ["SC02"])
        self.assertEqual(found[0].message, "x: 8 more flaws that fail nothing: 8 high (CVE-2026-10, CVE-2026-11, CVE-2026-12, "
                                           "CVE-2026-13, CVE-2026-14 and 3 more). Fix: update the base image or the packages once a "
                                           "fix is out.")
        self.assertEqual(found[0].severity, "warning")

    def test_what_a_report_holds_that_is_not_what_it_should_be_is_left_out_not_shown(self):
        document = {"Results": [{"Vulnerabilities": [
            vulnerability("bad id\n::error::x"), vulnerability("CVE-1", 7), vulnerability("CVE-2", "p", None, "1"),
            vulnerability("CVE-3", "p", "1", ["x"]), vulnerability("CVE-4", "", "1", "2"), "string", None,
            vulnerability("CVE-5", "q\n::error::\x1b[31m" + "x" * 300, "1\n2", "2\r3")]}]}
        found = scan.findings_of("ghcr.io/o/r/x:latest", document)
        self.assertEqual(len([each for each in found if each.code == "SC01"]), 1)
        for each in found:
            self.assertNotIn("\n", each.message)
            self.assertNotIn("\x1b", each.message)
            self.assertLess(len(each.message), 400)

    def test_a_report_that_is_not_a_report_has_no_findings(self):
        for document in (None, [], "x", 3, {"Results": "x"}, {"Results": [None]}):
            self.assertEqual(scan.findings_of("x", document), [])

    def test_a_flood_of_findings_is_cut(self):
        flood = {"Results": [{"Vulnerabilities": [vulnerability(f"CVE-2026-{number}", f"p{number}") for number in range(2000)]}]}
        self.assertEqual(len(scan.findings_of("x", flood)), scan.FINDINGS)
        flood = {"Results": [{"Vulnerabilities": [vulnerability(f"CVE-2026-{number}", f"p{number}", severity="HIGH")
                                                  for number in range(5000)]}]}
        self.assertEqual([each.code for each in scan.findings_of("x", flood)], ["SC02"])


class Dispatch(unittest.TestCase):

    def test_the_command_line_reaches_the_stages(self):
        self.assertEqual(ci.STAGES["scan-published"], ("scan", "main"))
        self.assertEqual(ci.STAGES["scan-built"], ("scan", "built_main"))
        self.assertEqual(ci_main(["scan-published", "/no/such/folder"], io.StringIO(), io.StringIO()), 2)
        self.assertEqual(ci_main(["scan-built", "/no/such/folder", "--units", "[]"], io.StringIO(), io.StringIO()), 2)

    def test_the_hardening_of_every_container_is_the_same(self):
        self.assertEqual(scan.HARDENED[:2], ["--rm", "--read-only"])
        self.assertIn("no-new-privileges", scan.HARDENED)
        self.assertEqual(re.findall(r"--cap-drop ALL", " ".join(scan.HARDENED)), ["--cap-drop ALL"])


if __name__ == "__main__":
    unittest.main()
