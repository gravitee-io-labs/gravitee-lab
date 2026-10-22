"""The builds that lab_images.py prints and runs, without Docker: what it reads from Compose is given, and the items it
finds are given too, so that none of it needs an add-on or a playground in this checkout."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lab_images  # noqa: E402

LOCAL = {"context": "/repo/seeder", "dockerfile": "/repo/seeder/Dockerfile"}
REMOTE = {"context": "https://github.com/example/project.git#abc123:app", "dockerfile": "Dockerfile"}
ITEM = Path("/repo/addons/example")
CORE = {"x-gravitee": {"seeder": {"image": "ghcr.io/Owner/gravitee-lab/seeder"}}, "services": {}}


def dry_run(found, **environment):
    """The lines a --dry-run prints for these builds ({image name: build section}), of one item and the seeder."""
    environment = {"REGISTRY": "ghcr.io/Owner/gravitee-lab", **environment}
    out = io.StringIO()
    with mock.patch.dict(os.environ, environment, clear=True), \
            mock.patch.object(lab_images, "items", return_value=[ITEM]), \
            mock.patch.object(lab_images, "builds", return_value=found), \
            mock.patch.object(lab_images, "seeder", return_value={"seeder": LOCAL}), contextlib.redirect_stdout(out):
        lab_images.main(True)
    return out.getvalue().splitlines()


class LabelsTest(unittest.TestCase):
    def command(self, lines, image):
        return next(line for line in lines if line.startswith("docker buildx") and f"/{image}:latest" in line)

    def test_an_image_built_from_this_repository_states_its_license(self):
        command = self.command(dry_run({"seeder": LOCAL}), "seeder")
        self.assertIn("--label org.opencontainers.image.licenses=AGPL-3.0-only", command)

    def test_an_image_built_from_another_project_does_not(self):
        command = self.command(dry_run({"agent-lens": REMOTE}), "agent-lens")
        self.assertNotIn("org.opencontainers.image.licenses", command)

    def test_the_source_label_comes_with_the_repository(self):
        lines = dry_run({"seeder": LOCAL}, GITHUB_REPOSITORY="owner/gravitee-lab", GITHUB_SHA="0123456789abcdef")
        command = self.command(lines, "seeder")
        self.assertIn("--label org.opencontainers.image.source=https://github.com/owner/gravitee-lab", command)
        self.assertIn(":sha-0123456", command)

    def test_the_registry_is_lower_case_and_each_image_is_printed_once(self):
        lines = dry_run({"seeder": LOCAL, "agent-lens": REMOTE})
        self.assertEqual(["== ghcr.io/owner/gravitee-lab/agent-lens", "== ghcr.io/owner/gravitee-lab/seeder"],
                         [line for line in lines if line.startswith("==")])

    def test_the_output_does_not_change_between_runs(self):
        self.assertEqual(dry_run({"seeder": LOCAL, "agent-lens": REMOTE}),
                         dry_run({"agent-lens": REMOTE, "seeder": LOCAL}))


class CommandTest(unittest.TestCase):
    """The command of one image, which another builder of images imports with the labels and the tags."""

    def test_an_image_is_tagged_latest_and_with_the_commit_and_built_for_both_platforms(self):
        command = lab_images.command("ghcr.io/o/r", "seeder", LOCAL, "abc1234", None)
        self.assertEqual(["docker", "buildx", "build", "--platform", "linux/amd64,linux/arm64", "--push"], command[:6])
        self.assertEqual(["ghcr.io/o/r/seeder:latest", "ghcr.io/o/r/seeder:sha-abc1234"],
                         [command[i + 1] for i, word in enumerate(command) if word == "-t"])

    def test_the_context_comes_last_and_a_local_build_file_is_joined_to_it(self):
        command = lab_images.command("r", "app", {"context": "/repo/app", "dockerfile": "Dockerfile"}, "c", None)
        self.assertEqual("/repo/app", command[-1])
        self.assertEqual("/repo/app/Dockerfile", command[command.index("-f") + 1])

    def test_a_remote_build_file_is_given_as_it_is_and_build_arguments_follow(self):
        command = lab_images.command("r", "app", {**REMOTE, "args": {"A": "1", "B": "2"}}, "c", None)
        self.assertEqual("Dockerfile", command[command.index("-f") + 1])
        self.assertEqual(["A=1", "B=2"], [command[i + 1] for i, word in enumerate(command) if word == "--build-arg"])

    def test_a_build_has_no_cache(self):
        # As in the catalogs (WF17): nothing written by one build is read by the next
        command = lab_images.command("r", "app", LOCAL, "c", None)
        self.assertFalse([word for word in command if word.startswith("--cache")])


class EnvironmentTest(unittest.TestCase):
    def settings(self, dry_run=False, **environment):
        with mock.patch.dict(os.environ, environment, clear=True):
            return lab_images.settings(dry_run)

    def test_the_registry_is_required_to_push_and_not_to_print(self):
        with self.assertRaises(SystemExit) as caught:
            self.settings()
        self.assertIn("REGISTRY is not set", str(caught.exception))
        self.assertEqual(("ghcr.io/<owner>/gravitee-lab", "local", None), self.settings(dry_run=True))

    def test_the_repository_gives_the_source_and_the_commit_is_cut_to_seven_characters(self):
        found = self.settings(REGISTRY="GHCR.io/O/R", GITHUB_REPOSITORY="o/r", GITHUB_SHA="0123456789",
                              GITHUB_SERVER_URL="https://git.example")
        self.assertEqual(("ghcr.io/o/r", "0123456", "https://git.example/o/r"), found)


class ItemsTest(unittest.TestCase):
    """Which folders are items to build: discovery, apart from the build."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def item(self, *parts, compose=True):
        path = self.folder.joinpath(*parts)
        path.mkdir(parents=True)
        if compose:
            (path / "compose.yml").write_text("services: {}\n")
        return path

    def test_the_folders_with_a_compose_file_are_the_items_by_name(self):
        b, a = self.item("addons", "b"), self.item("addons", "a")
        self.item("addons", "no-compose", compose=False)
        (self.folder / "addons" / "README.md").write_text("a file, not an item\n")
        self.assertEqual([a, b], lab_images.items([self.folder / "addons"]))

    def test_the_personal_items_are_not_built(self):
        shared = self.item("addons", "shared")
        self.item("addons", "local")
        self.assertEqual([shared], lab_images.items([self.folder / "addons"]))

    def test_each_root_is_read_in_the_order_given(self):
        playground, addon = self.item("playgrounds", "p"), self.item("addons", "a")
        self.assertEqual([playground, addon], lab_images.items([self.folder / "playgrounds", self.folder / "addons"]))

    def test_a_root_that_is_not_there_holds_no_item(self):
        self.assertEqual([], lab_images.items([self.folder / "addons"]))
        (self.folder / "file").write_text("not a folder\n")
        self.assertEqual([], lab_images.items([self.folder / "file"]))
        self.assertEqual([], lab_images.items([]))

    def test_the_default_roots_are_the_add_ons_and_playgrounds_of_the_repository(self):
        addon, playground = self.item("addons", "a"), self.item("playgrounds", "p")
        self.item("setups", "s")
        with mock.patch.object(lab_images, "ROOT", self.folder):
            self.assertEqual([addon, playground], lab_images.items())

    def test_a_repository_with_no_item_folder_holds_none(self):
        with mock.patch.object(lab_images, "ROOT", self.folder):
            self.assertEqual([], lab_images.items())


class MainTest(unittest.TestCase):
    """What main builds: the items it finds, then the seeder, whatever the items say."""

    def run_main(self, found=None, roots=None, items=()):
        """(the lines printed, the items `builds` was called with, the roots `items` was called with)."""
        out, asked = io.StringIO(), []
        with mock.patch.dict(os.environ, {"REGISTRY": "ghcr.io/o/r"}, clear=True), \
                mock.patch.object(lab_images, "items", return_value=list(items)) as listed, \
                mock.patch.object(lab_images, "builds", side_effect=lambda item: asked.append(item) or found or {}), \
                mock.patch.object(lab_images, "seeder", return_value={"seeder": LOCAL}), \
                contextlib.redirect_stdout(out):
            lab_images.main(True, roots)
        return out.getvalue().splitlines(), asked, listed.call_args

    def test_with_no_item_the_seeder_alone_is_built(self):
        lines, asked, _ = self.run_main()
        self.assertEqual([], asked)
        self.assertEqual(["== ghcr.io/o/r/seeder"], [line for line in lines if line.startswith("==")])

    def test_builds_is_called_for_each_item_in_order(self):
        _, asked, _ = self.run_main(items=[Path("/repo/addons/a"), Path("/repo/playgrounds/p")])
        self.assertEqual([Path("/repo/addons/a"), Path("/repo/playgrounds/p")], asked)

    def test_the_roots_are_handed_to_the_discovery(self):
        _, _, call = self.run_main(roots=["/catalog/addons"])
        self.assertEqual((["/catalog/addons"],), call.args)

    def test_no_roots_means_the_defaults(self):
        _, _, call = self.run_main()
        self.assertEqual((None,), call.args)

    def test_the_seeder_of_the_repository_wins_over_the_one_an_item_builds(self):
        found = {"seeder": {"context": "/elsewhere/seeder", "dockerfile": "Dockerfile"}}
        lines, _, _ = self.run_main(found, items=[ITEM])
        command = next(line for line in lines if line.startswith("docker buildx"))
        self.assertTrue(command.endswith(" /repo/seeder"), command)

    def test_a_build_that_fails_stops_the_run(self):
        with mock.patch.dict(os.environ, {"REGISTRY": "ghcr.io/o/r"}, clear=True), \
                mock.patch.object(lab_images, "items", return_value=[]), \
                mock.patch.object(lab_images, "seeder", return_value={"a": LOCAL, "b": LOCAL}), \
                mock.patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, "buildx")) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                lab_images.main(False)
        self.assertEqual(1, run.call_count)


class RenderTest(unittest.TestCase):
    """What is asked of Compose, in a scratch repository and with `docker` given."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        include = self.folder / ".lab-images.tmp"
        for name, value in (("ROOT", self.folder), ("INCLUDE_FILE", include)):
            patcher = mock.patch.object(lab_images, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.include = include
        self.calls = []

    def docker(self, stdout="{}", code=0, stderr=""):
        def run(command, **options):
            included = self.include.read_text(encoding="utf-8") if self.include.exists() else None
            self.calls.append((command, options, included))
            return subprocess.CompletedProcess(command, code, stdout, stderr)
        return mock.patch.object(subprocess, "run", side_effect=run)

    def test_the_core_alone_is_read_with_no_include_file(self):
        with self.docker('{"services": {}}'), mock.patch.dict(os.environ, {"COMPOSE_FILE": "x.yml", "A": "b"}):
            self.assertEqual({"services": {}}, lab_images.render())
        command, options, included = self.calls[0]
        self.assertEqual(["docker", "compose", "-f", "docker-compose.yml", "config", "--format", "json"], command)
        self.assertIsNone(included)
        self.assertEqual(self.folder, options["cwd"])
        self.assertEqual("b", options["env"]["A"])
        self.assertNotIn("COMPOSE_FILE", options["env"])

    def test_an_item_is_included_for_the_read_and_the_file_is_removed_after(self):
        with self.docker():
            lab_images.render(self.folder / "addons" / "kafka")
        command, _, included = self.calls[0]
        self.assertEqual(["-f", "docker-compose.yml", "-f", ".lab-images.tmp"], command[2:6])
        self.assertEqual('include:\n  - "addons/kafka/compose.yml"\n', included)
        self.assertFalse(self.include.exists())

    def test_an_item_outside_the_repository_is_included_by_a_path_that_leads_there(self):
        outside = self.folder.parent / "elsewhere" / "kafka"
        with self.docker():
            lab_images.render(outside)
        self.assertEqual('include:\n  - "../elsewhere/kafka/compose.yml"\n', self.calls[0][2])

    def test_a_name_that_yaml_would_read_as_something_else_reaches_compose_as_the_name(self):
        for name in ("my: dir #1", "it's", 'quote"d', "back\\slash", "- dash", "&anchor", "!tag", "{braces}", "[list]",
                     "ünï 日本", "100%", "a\tb", "a\nb\n  - evil"):
            with self.subTest(name=name), self.docker():
                lab_images.render(self.folder / "addons" / name)
                text = self.calls[-1][2]
                # One entry, a double-quoted scalar on one line: a newline in the name cannot add another entry
                self.assertEqual(["include:", "  - " + json.dumps(f"addons/{name}/compose.yml", ensure_ascii=False)],
                                 text.splitlines(), text)
                self.assertEqual(f"addons/{name}/compose.yml", json.loads(text.splitlines()[1][len("  - "):]))

    def test_a_dollar_in_a_name_is_doubled_so_that_compose_does_not_interpolate_it(self):
        with self.docker():
            lab_images.render(self.folder / "addons" / "a$b")
        self.assertEqual('include:\n  - "addons/a$$b/compose.yml"\n', self.calls[0][2])

    def test_a_name_is_written_as_utf_8_whatever_the_locale_is(self):
        self.assertEqual('include:\n  - "addons/日本/compose.yml"\n'.encode("utf-8"),
                         lab_images.include_file("addons/日本"))

    def test_a_name_that_is_not_text_is_refused_before_anything_is_written_or_run(self):
        with self.docker(), self.assertRaises(SystemExit) as caught:
            lab_images.render(self.folder / "addons" / "bad\udcffname")
        self.assertIn("is not valid text", str(caught.exception))
        self.assertIn("addons/bad\\udcffname", str(caught.exception))
        self.assertEqual([], self.calls)
        self.assertFalse(self.include.exists())

    def test_the_include_file_is_removed_when_docker_cannot_be_run(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("docker")), \
                self.assertRaises(FileNotFoundError):
            lab_images.render(self.folder / "addons" / "kafka")
        self.assertFalse(self.include.exists())

    def test_compose_that_cannot_read_an_item_stops_the_run_with_its_words(self):
        with self.docker(code=1, stderr="  no such file \n"), self.assertRaises(SystemExit) as caught:
            lab_images.render(self.folder / "addons" / "kafka")
        self.assertEqual("addons/kafka: Compose cannot read it: no such file", str(caught.exception))
        self.assertFalse(self.include.exists())

    def test_compose_that_cannot_read_the_core_names_the_core(self):
        with self.docker(code=1, stderr="invalid"), self.assertRaises(SystemExit) as caught:
            lab_images.render()
        self.assertEqual("docker-compose.yml: Compose cannot read it: invalid", str(caught.exception))

    def test_builds_are_the_services_with_a_build_section_and_an_image(self):
        services = {"seed": {"image": "ghcr.io/o/r/seeder:latest", "build": LOCAL},
                    "web": {"image": "nginx"},
                    "app": {"build": {"context": "."}},
                    "api": {"image": "ghcr.io/o/r/api", "build": REMOTE}}
        with mock.patch.object(lab_images, "render", return_value={"services": services}):
            self.assertEqual({"seeder": LOCAL, "api": REMOTE}, lab_images.builds(self.folder / "addons" / "x"))


class SeederTest(unittest.TestCase):
    """The seeder is not an item: its name is the core's and its folder is the repository's."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "seeder").mkdir()
        (self.folder / "seeder" / "Dockerfile").write_text("FROM scratch\n")

    def seeder(self, core):
        with mock.patch.object(lab_images, "ROOT", self.folder), \
                mock.patch.object(lab_images, "SEEDER", self.folder / "seeder"), \
                mock.patch.object(lab_images, "render", return_value=core):
            return lab_images.seeder()

    def test_it_is_built_from_the_seeder_folder_under_the_name_the_core_gives(self):
        self.assertEqual({"seeder": {"context": str(self.folder / "seeder"), "dockerfile": "Dockerfile"}},
                         self.seeder(CORE))

    def test_the_name_is_the_last_part_of_the_image_without_its_tag(self):
        core = {"x-gravitee": {"seeder": {"image": "registry.example/team/lab-seed:1.2"}}}
        self.assertEqual(["lab-seed"], list(self.seeder(core)))

    def test_a_core_that_names_no_seeder_image_stops_the_run(self):
        for core in ({}, {"x-gravitee": None}, {"x-gravitee": {}}, {"x-gravitee": {"seeder": None}},
                     {"x-gravitee": {"seeder": {"image": ""}}}, {"x-gravitee": {"seeder": {"image": 3}}}):
            with self.subTest(core=core), self.assertRaises(SystemExit) as caught:
                self.seeder(core)
            self.assertIn("x-gravitee.seeder.image is not set", str(caught.exception))

    def test_a_missing_dockerfile_stops_the_run(self):
        (self.folder / "seeder" / "Dockerfile").unlink()
        with self.assertRaises(SystemExit) as caught:
            self.seeder(CORE)
        self.assertIn("seeder/Dockerfile is missing", str(caught.exception))


def compose_5():
    """Whether Compose 5 or later is here, which is what reads the core and the items; its CLI only, no daemon."""
    if not shutil.which("docker"):
        return False
    done = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    major = done.stdout.strip().lstrip("v").split(".")[0]
    return done.returncode == 0 and major.isdigit() and int(major) >= 5


@unittest.skipUnless(compose_5(), "Docker Compose 5 or later is not installed")
class CoreSeederTest(unittest.TestCase):
    """The core file of this repository, read by Compose: the seeder has a name, and it is built where units find it."""

    def test_the_seeder_of_the_core_is_built_from_the_seeder_folder(self):
        found = lab_images.seeder()
        self.assertEqual(["seeder"], list(found))
        self.assertEqual(str(lab_images.ROOT / "seeder"), found["seeder"]["context"])
        self.assertTrue((lab_images.ROOT / "seeder" / found["seeder"]["dockerfile"]).is_file())

    def test_an_engine_with_no_item_prints_the_seeder_build_alone(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"REGISTRY": "ghcr.io/o/r"}), \
                mock.patch.object(lab_images, "items", return_value=[]), contextlib.redirect_stdout(out):
            lab_images.main(True)
        lines = out.getvalue().splitlines()
        self.assertEqual(["== ghcr.io/o/r/seeder"], [line for line in lines if line.startswith("==")])
        self.assertEqual(1, len([line for line in lines if line.startswith("docker buildx")]))


@unittest.skipUnless(compose_5(), "Docker Compose 5 or later is not installed")
class OddNamesTest(unittest.TestCase):
    """The folders of a catalog are named by their authors: Compose reads each one, whatever its name holds."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "docker-compose.yml").write_text("name: scratch\nservices:\n  core:\n    image: busybox\n")
        for name, value in (("ROOT", self.folder), ("INCLUDE_FILE", self.folder / ".lab-images.tmp")):
            patcher = mock.patch.object(lab_images, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def item(self, name, image):
        """An add-on folder called `name` that builds `image`."""
        path = self.folder / "addons" / name
        path.mkdir(parents=True)
        (path / "compose.yml").write_text(
            f"services:\n  app:\n    image: ghcr.io/o/r/{image}:latest\n    build:\n      context: .\n",
            encoding="utf-8")
        return path

    def test_an_item_is_built_whatever_its_folder_is_called(self):
        names = ("my: dir #1", "it's", 'quote"d', "back\\slash", "ünï 日本", "- dash", "&anchor", "!tag", "{braces}",
                 "[list]", "100%", "${HOME}", "a b")
        for number, name in enumerate(names):
            with self.subTest(name=name):
                item = self.item(name, f"built-{number}")
                self.assertEqual([f"built-{number}"], list(lab_images.builds(item)))
                self.assertFalse(lab_images.INCLUDE_FILE.exists())

    def test_a_dollar_in_a_folder_name_is_not_interpolated_into_another_folder(self):
        self.item("a", "decoy")
        item = self.item("a$b", "right")
        self.assertEqual(["right"], list(lab_images.builds(item)))


class ContextTest(unittest.TestCase):
    def test_a_context_is_remote_when_it_is_fetched(self):
        for context in ("https://github.com/example/project.git", "http://example.com/a.tar", "git@github.com:a/b.git"):
            self.assertTrue(lab_images.is_remote(context), context)
        for context in ("/repo/seeder", "."):
            self.assertFalse(lab_images.is_remote(context), context)


if __name__ == "__main__":
    unittest.main()
