"""files: the files shown with one of them, on the small project of test_layers; an edited compose file checked and
saved, and a .env as a form, in a temporary project folder with docker compose replaced. Then the files of the catalogs
(Held, Scratch, Rendered): what a git catalog shows and refuses, and the copies that are made in the scratch folder,
never beside a catalog file."""

import contextlib
import fnmatch
import os
import posixpath
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.server import api
from lab_ui.docker import compose, envfiles
from lab_ui.library import files, layers
from lab_ui import paths
from lab_ui.catalogs import registry

from .. import Case, fixtures
from ..library.test_layers import lab, on, own

KAZOO, FEDERATION = ".lab/catalogs/local/addons/kazoo/compose.yml", ".lab/catalogs/local/addons/federation/compose.yml"
PROJECT = {"addons": {"kazoo": {"kind": "addon", "file": KAZOO, "needs": []},
                      "federation": {"kind": "addon", "file": FEDERATION, "needs": []}}, "playgrounds": {}}
WARNING = 'time="2026-01-01T10:00:00Z" level=warning msg="The \\"FEDERATION_ALPHA_APIM_TOKEN\\" variable is not set."'
EDITED = "services:\n  kazoo:\n    imgae: kazoo\n"


CORE = {"title": "Core", "on": True, "runs": True, "files": [
    {"path": "docker-compose.yml", "name": "docker-compose.yml", "kind": "compose"},
    {"path": ".env", "name": ".env", "kind": "env"}]}
ON_SECTION = {"title": "What is on", "on": True, "runs": True, "files": [
    {"path": ".lab/on.yml", "name": "on.yml", "kind": "compose"}]}


class Sections(Case):

    def setUp(self):
        self.project, self.state = lab(), None
        # On disk: the project's add-ons, setups and playgrounds, and an add-on whose file does not load. A guide is
        # listed when the project folder holds one: an empty folder here, whatever the real project holds.
        self.folder(paths, files)
        fixtures.default(self)
        for kind in paths.KINDS:
            for uid in self.project[f"{kind}s"]:
                self.found(kind, uid)
        self.found("addon", "broken")
        self.patch(layers, "current", lambda project: self.state)
        # What runs: the platform
        self.running = {name: {"state": "running"} for name in ("apim-gateway", "apim-management-api", "mongodb")}
        self.patch(compose, "containers", lambda *variable: (self.running, None))

    def found(self, kind, uid):
        """A unit found on disk: its compose file is there."""
        self.write(paths.unit_file(kind, uid), "")

    def sections(self, path, explicit=(), playground=None):
        """The sections shown with a file while these are on: [(title, on, the paths of its files)]."""
        self.state = on(self.project, explicit, playground)
        found = files.sections(self.project, path)
        self.assertEqual(found[0], CORE)
        return [(section["title"], section["on"], [file["path"] for file in section["files"]]) for section in found[1:]]

    def runs(self, path, explicit=(), playground=None):
        """{title: whether something of it runs} of the sections shown with a file."""
        self.state = on(self.project, explicit, playground)
        return {section["title"]: section["runs"] for section in files.sections(self.project, path)}

    def test_a_core_file_shows_everything_that_is_on(self):
        for path in ("docker-compose.yml", ".env"):
            self.assertEqual(self.sections(path), [])
            # The playground, then the add-ons in effect by name; a .env only for settings of its own
            self.assertEqual(self.sections(path, ["kazoo-desk", "hyssop"], "welcome"), [
                ("Welcome", True, [".lab/catalogs/fixture/playgrounds/welcome/compose.yml"]),
                ("AI Agent Lens", True, [".lab/catalogs/fixture/addons/agent-lens/compose.yml"]),
                ("Hybrid gateway", True, [".lab/catalogs/fixture/addons/hyssop/compose.yml", ".lab/catalogs/fixture/addons/hyssop/.env"]),
                ("Kazoo", True, [".lab/catalogs/fixture/addons/kazoo/compose.yml"]),
                ("Kazoo Desk", True, [".lab/catalogs/fixture/addons/kazoo-desk/compose.yml"])])

    def test_a_unit_shows_itself_then_the_add_ons_it_brings(self):
        kazoo = ("Kazoo", False, [".lab/catalogs/fixture/addons/kazoo/compose.yml"])
        self.assertEqual(self.sections(".lab/catalogs/fixture/addons/kazoo-desk/compose.yml"),
                         [("Kazoo Desk", False, [".lab/catalogs/fixture/addons/kazoo-desk/compose.yml"]), kazoo])
        self.assertEqual(self.sections(".lab/catalogs/fixture/setups/events.yml"), [
            ("Events", False, [".lab/catalogs/fixture/setups/events.yml"]), kazoo,
            ("Kazoo Desk", False, [".lab/catalogs/fixture/addons/kazoo-desk/compose.yml"])])
        self.assertEqual(self.sections(".lab/catalogs/fixture/playgrounds/welcome/compose.yml", ["jade"]), [
            ("Welcome", False, [".lab/catalogs/fixture/playgrounds/welcome/compose.yml"]),
            ("AI Agent Lens", False, [".lab/catalogs/fixture/addons/agent-lens/compose.yml"])])
        # A playground shows the setups it includes before the add-ons brought
        self.project["playgrounds"]["welcome"].update(setups=["events"],
                                                       addons=["agent-lens", "kazoo", "kazoo-desk"])
        self.assertEqual([title for title, _, _ in self.sections(".lab/catalogs/fixture/playgrounds/welcome/compose.yml")],
                         ["Welcome", "Events", "AI Agent Lens", "Kazoo", "Kazoo Desk"])
        self.assertEqual([title for title, _, _ in self.sections(".env", playground="welcome")][:2],
                         ["Welcome", "Events"])
        self.project["playgrounds"]["welcome"].update(setups=[], addons=["agent-lens"])
        # Each says whether it runs now, whatever else is on
        self.assertEqual([(title, is_on) for title, is_on, _ in self.sections(".lab/catalogs/fixture/setups/events.yml", ["kazoo"])],
                         [("Events", False), ("Kazoo", True), ("Kazoo Desk", False)])
        self.assertEqual([is_on for _, is_on, _ in self.sections(".lab/catalogs/fixture/setups/events.yml", ["kazoo-desk", "jade"])],
                         [True, True, True])

    def test_each_says_whether_something_of_it_runs(self):
        # Only then does applying it recreate anything: the editor offers Save & apply then
        self.running.update({"kazoo": {"state": "running"}, "agent-lens": {"state": "running"}})
        self.assertEqual(self.runs(".lab/catalogs/fixture/setups/events.yml", ["kazoo-desk"]),
                         {"Core": True, "Events": False, "Kazoo": True, "Kazoo Desk": False})
        self.assertEqual(self.runs(".lab/catalogs/fixture/playgrounds/welcome/compose.yml", playground="welcome"),
                         {"Core": True, "Welcome": False, "AI Agent Lens": True})
        # A setup's file never runs, even with all of it running: the on file names its add-ons
        self.running["kazoo-desk"] = {"state": "running"}
        self.assertFalse(self.runs(".lab/catalogs/fixture/setups/events.yml", ["kazoo-desk"])["Events"])
        # Kazoo runs without being on: an apply would leave it as it is, its file is saved only
        self.assertEqual(self.runs(".lab/catalogs/fixture/addons/kazoo/compose.yml"), {"Core": True, "Kazoo": False})
        # A playground without a service of its own runs while it is on and what it brings runs
        own(self.project)
        self.found("playground", "local/sandbox")
        self.running["whoami"] = {"state": "running"}
        self.assertTrue(self.runs(".lab/catalogs/local/playgrounds/sandbox/compose.yml", playground="local/sandbox")["Sandbox"])
        self.assertFalse(self.runs(".lab/catalogs/local/playgrounds/sandbox/compose.yml")["Sandbox"])
        # The core, while a service of the platform runs; nothing without Docker
        self.running = {name: found for name, found in self.running.items() if name not in self.project["services"]
                        or self.project["services"][name]["owner"]["kind"] != "platform"}
        self.assertFalse(self.runs("docker-compose.yml")["Core"])
        self.running = {}
        self.assertEqual(set(self.runs(".env", ["kazoo"]).values()), {False})

    def test_the_on_file_shows_with_everything_that_is_on(self):
        # It opens in the editor, its problem line links to it; with a file of the core, it shows too
        self.state = on(self.project, ["kazoo"])
        self.assertEqual(files.sections(self.project, ".lab/on.yml")[1:],
                         files.sections(self.project, "docker-compose.yml")[1:])
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n")
        for path in (".lab/on.yml", "docker-compose.yml", ".env"):
            found = files.sections(self.project, path)
            self.assertEqual((found[1], [section["title"] for section in found[2:]]), (ON_SECTION, ["Kazoo"]), path)
        self.assertNotIn(ON_SECTION, files.sections(self.project, ".lab/catalogs/fixture/addons/kazoo/compose.yml"))
        self.assertEqual(files.read(".lab/on.yml")["content"],
                         "include:\n  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n")

    def test_a_env_file_shows_what_its_add_on_shows(self):
        hyssop = [("Hybrid gateway", False, [".lab/catalogs/fixture/addons/hyssop/compose.yml", ".lab/catalogs/fixture/addons/hyssop/.env"])]
        self.assertEqual(self.sections(".lab/catalogs/fixture/addons/hyssop/.env"), hyssop)
        self.assertEqual(self.sections(".lab/catalogs/fixture/addons/hyssop/compose.yml"), hyssop)
        self.assertEqual(files.sections(self.project, ".lab/catalogs/fixture/addons/hyssop/.env")[1]["files"][1],
                         {"path": ".lab/catalogs/fixture/addons/hyssop/.env", "name": ".env", "kind": "env"})

    def test_what_belongs_to_this_machine_shows_like_the_rest(self):
        own(self.project)
        self.found("addon", "local/whoami")
        self.found("playground", "local/sandbox")
        self.project["addons"]["local/whoami"]["settings"] = self.project["addons"]["hyssop"]["settings"]
        whoami = ("Whoami", True, [".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/local/addons/whoami/.env"])
        self.assertEqual(self.sections(".lab/catalogs/local/playgrounds/sandbox/compose.yml", playground="local/sandbox"),
                         [("Sandbox", True, [".lab/catalogs/local/playgrounds/sandbox/compose.yml"]), whoami])
        self.assertEqual(self.sections(".lab/catalogs/local/addons/whoami/.env", ["local/whoami"]), [whoami])
        self.assertEqual(files._variables(self.project, ".lab/catalogs/local/addons/whoami/.env")[0]["key"], "HYSSOP_CLOUD_TOKEN")

    def test_a_playground_shows_its_guide_next_to_its_compose_file(self):
        guide = self.root / self.write(".lab/catalogs/fixture/playgrounds/welcome/guide.md", "# Welcome\n")
        welcome = [("Welcome", False, [".lab/catalogs/fixture/playgrounds/welcome/compose.yml", ".lab/catalogs/fixture/playgrounds/welcome/guide.md"]),
                    ("AI Agent Lens", False, [".lab/catalogs/fixture/addons/agent-lens/compose.yml"])]
        self.assertEqual(self.sections(".lab/catalogs/fixture/playgrounds/welcome/compose.yml"), welcome)
        # Opened itself, it shows the same
        self.assertEqual(self.sections(".lab/catalogs/fixture/playgrounds/welcome/guide.md"), welcome)
        self.assertEqual(files.sections(self.project, ".lab/catalogs/fixture/playgrounds/welcome/guide.md")[1]["files"][1],
                         {"path": ".lab/catalogs/fixture/playgrounds/welcome/guide.md", "name": "guide.md", "kind": "guide"})
        # Without one on disk, nothing is listed
        guide.unlink()
        self.assertEqual(self.sections(".lab/catalogs/fixture/playgrounds/welcome/compose.yml")[0][2], [".lab/catalogs/fixture/playgrounds/welcome/compose.yml"])

    def test_a_file_that_does_not_load_is_still_shown(self):
        # Under its id, so that it can be repaired
        self.assertEqual(self.sections(".lab/catalogs/fixture/addons/broken/compose.yml"),
                         [("broken", False, [".lab/catalogs/fixture/addons/broken/compose.yml"])])

    def test_what_is_not_a_file_of_the_project_is_refused(self):
        self.state = on(self.project)
        for path in (".lab/catalogs/fixture/addons/nothing/compose.yml", ".lab/catalogs/fixture/setups/events.env", "setups/.env", "../.env", "",
                     None):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "Not a file of this project"):
                files.sections(self.project, path)


class Checked(Case):

    def setUp(self):
        self.folder(paths, compose, envfiles, files)
        (self.root / "generated").mkdir()
        self.patch(compose, "scratch", lambda: self.root / "generated")
        for path in ("docker-compose.yml", KAZOO, FEDERATION):
            self.write(path, "services: {}\n")
        self.copy = self.root / ".lab/catalogs/local/addons/kazoo/.compose.yml.check.yml"

    def validate(self, code, errors, project=PROJECT):
        """What validate() answers when Compose prints these lines, and what Compose was given."""
        given = {}

        def run(cmd, timeout):
            given.update(cmd=cmd, copy=self.copy.read_text(encoding="utf-8"), include=Path(cmd[-3]).read_text(encoding="utf-8"))
            return code, "", errors
        with mock.patch.object(compose, "run", side_effect=run):
            return files.validate(project, KAZOO, EDITED), given

    def test_the_edited_text_is_checked_in_place_of_its_file(self):
        answer, given = self.validate(0, "")
        self.assertEqual(answer, {"valid": True, "problems": []})
        self.assertEqual(given["copy"], EDITED)
        self.assertEqual(given["include"], f'include:\n  - "{self.copy}"\n  - "{self.root / FEDERATION}"\n')
        self.assertEqual(given["cmd"][-2:], ["config", "-q"])
        self.assertFalse(self.copy.exists())

    def test_what_includes_the_file_is_left_out(self):
        # Kazoo Desk and the welcome bring Kazoo as it is on disk: Compose would merge it with the edited text
        project = {"addons": {**PROJECT["addons"],
                              "kazoo-desk": {"kind": "addon", "file": ".lab/catalogs/local/addons/kazoo-desk/compose.yml",
                                                "needs": ["local/kazoo"]}},
                   "playgrounds": {"welcome": {"kind": "playground", "file": ".lab/catalogs/local/playgrounds/welcome/compose.yml",
                                                "addons": ["local/kazoo", "local/kazoo-desk"], "setups": []}}}
        _, given = self.validate(0, "", project)
        self.assertEqual(given["include"], f'include:\n  - "{self.copy}"\n  - "{self.root / FEDERATION}"\n')
        # A setup is brought by the playgrounds that include it
        self.assertEqual(files._including(project, ".lab/catalogs/local/setups/events.yml"), set())
        project["playgrounds"]["welcome"]["setups"] = ["local/events"]
        self.assertEqual(files._including(project, ".lab/catalogs/local/setups/events.yml"), {".lab/catalogs/local/playgrounds/welcome/compose.yml"})
        self.assertEqual(files._including(project, ".lab/catalogs/local/playgrounds/welcome/compose.yml"), set())

    def test_checking_again_leaves_one_generated_file(self):
        self.validate(0, "")
        self.validate(1, f"validating {self.copy}: broken")
        self.assertEqual(len(list((self.root / "generated").iterdir())), 1)

    def test_a_warning_about_another_file_is_not_an_error_of_this_one(self):
        refused = f"validating {self.copy}: services.kazoo additional properties 'imgae' not allowed"
        # A variable that another add-on needs and nobody set is that add-on's to say: not here, where the text does not use it
        answer, _ = self.validate(1, f"{WARNING}\n\n{refused}\n")
        self.assertFalse(answer["valid"])
        self.assertEqual([(p["severity"], p["line"]) for p in answer["problems"]], [("error", 3)])
        # The copy's name means nothing to the reader
        self.assertEqual(answer["problems"][0]["message"],
                         f"validating {KAZOO}: services.kazoo additional properties 'imgae' not allowed")
        answer, _ = self.validate(0, f"{WARNING}\n")
        self.assertEqual((answer["valid"], answer["problems"]), (True, []))

    def test_what_stops_compose_in_another_file_does_not_stop_this_one(self):
        """An include of a catalog that was removed, in a playground, is that playground's to repair: this text is loaded alone."""
        runs = []

        def run(cmd, timeout):
            runs.append(cmd)
            if len(runs) == 1:
                return 1, "", f"open {self.root}/.lab/catalogs/gone/addons/other/compose.yml: no such file or directory\n"
            return 0, "", ""
        with mock.patch.object(compose, "run", side_effect=run):
            answer = files.validate(PROJECT, KAZOO, EDITED)
        self.assertEqual(len(runs), 2, "the second run is this text with the core, nothing else")
        self.assertTrue(answer["valid"])
        self.assertEqual([(p["severity"], p["message"]) for p in answer["problems"]],
                         [("warning", "Another file does not load: open .lab/catalogs/gone/addons/other/compose.yml: no such file or directory")])

    def test_what_stops_compose_in_this_text_is_its_error_and_is_not_asked_again(self):
        runs = []

        def run(cmd, timeout):
            runs.append(cmd)
            return 1, "", f"validating {self.copy}: services.kazoo must be a mapping\n"
        with mock.patch.object(compose, "run", side_effect=run):
            answer = files.validate(PROJECT, KAZOO, EDITED)
        self.assertEqual((len(runs), answer["valid"]), (1, False))

    def test_a_text_that_does_not_load_alone_either_says_why(self):
        runs = []

        def run(cmd, timeout):
            runs.append(cmd)
            return 1, "", "services.kazoo.image must be a string\n"
        with mock.patch.object(compose, "run", side_effect=run):
            answer = files.validate(PROJECT, KAZOO, EDITED)
        self.assertEqual((len(runs), answer["valid"], [p["severity"] for p in answer["problems"]]), (2, False, ["error"]))

    def test_a_variable_the_edited_text_uses_is_said_when_it_is_not_set(self):
        used = "services:\n  kazoo:\n    image: kazoo\n    environment:\n      TOKEN: ${FEDERATION_ALPHA_APIM_TOKEN}\n"
        with mock.patch.object(compose, "run", return_value=(0, "", f"{WARNING}\n")), \
                mock.patch.object(compose, "all_files", return_value=[]):
            answer = files.validate(PROJECT, KAZOO, used)
        self.assertEqual((answer["valid"], [p["severity"] for p in answer["problems"]]), (True, ["warning"]))

    def test_save_refuses_with_the_errors_only(self):
        version = files._version(KAZOO)
        answer = (1, "", f"{WARNING}\nservices.kazoo must be a mapping\n")
        with mock.patch.object(compose, "run", return_value=answer), self.assertRaises(ValueError) as refused:
            files.save(PROJECT, KAZOO, EDITED, version)
        self.assertEqual(str(refused.exception), "Invalid compose file: services.kazoo must be a mapping")
        self.assertEqual((self.root / KAZOO).read_text(encoding="utf-8"), "services: {}\n")

    def test_a_guide_is_saved_without_compose(self):
        guide = self.write(".lab/catalogs/local/playgrounds/welcome/guide.md", "# Welcome\n")
        self.write(".lab/catalogs/local/playgrounds/welcome/compose.yml", "services: {}\n")
        with mock.patch.object(compose, "run", side_effect=AssertionError("Compose was called")):
            self.assertEqual(files.validate(PROJECT, guide, "not: [yaml"), {"valid": True, "problems": []})
            saved = files.save(PROJECT, guide, "# Welcome\n\nStep one\n", files._version(guide))
            # The guide of a playground that is not found is no file of the project
            with self.assertRaisesRegex(ValueError, "Not a compose file"):
                files.read(".lab/catalogs/local/playgrounds/other/guide.md")
        self.assertEqual((self.root / guide).read_text(encoding="utf-8"), "# Welcome\n\nStep one\n")
        self.assertEqual(saved["content"], "# Welcome\n\nStep one\n")

    def test_save_writes_what_compose_accepts(self):
        version = files._version(KAZOO)
        with mock.patch.object(compose, "run", return_value=(0, "", f"{WARNING}\n")):
            with self.assertRaisesRegex(RuntimeError, "changed on disk"):
                files.save(PROJECT, KAZOO, EDITED, "another version")
            saved = files.save(PROJECT, KAZOO, "services: {kazoo: {}}\n", version)
        self.assertEqual((self.root / KAZOO).read_text(encoding="utf-8"), "services: {kazoo: {}}\n")
        # What the editor shows next: the text saved, its version, the warnings Compose still has
        self.assertEqual(sorted(saved), ["content", "problems", "version"])
        # (the warning of a variable that another file needs is not said here: this text does not use it)
        self.assertEqual((saved["content"], saved["version"], [p["severity"] for p in saved["problems"]]),
                         ("services: {kazoo: {}}\n", files._version(KAZOO), []))

    def test_what_is_saved_while_compose_checks_the_text_is_not_written_over(self):
        version = files._version(KAZOO)

        def run(cmd, timeout):   # another tab, or an editor, saves the file while Compose checks this text
            (self.root / KAZOO).write_text("services: {theirs: {}}\n", encoding="utf-8")
            return 0, "", ""
        with mock.patch.object(compose, "run", side_effect=run), self.assertRaisesRegex(RuntimeError, "changed on disk"):
            files.save(PROJECT, KAZOO, EDITED, version)
        self.assertEqual((self.root / KAZOO).read_text(encoding="utf-8"), "services: {theirs: {}}\n")

    def test_a_settings_form_compares_its_version_with_no_other_write_of_a_env_between(self):
        project = {"core_variables": [{"key": "KIOSK_GREETING", "default": "hello", "required": False, "comment": "", "secret": False}],
                   "addons": {}}
        version = files.settings(project, ".env")["version"]
        saved = []
        with envfiles.LOCK:   # another write of a .env is under way: the form waits for it, then sees what it wrote
            thread = threading.Thread(target=lambda: saved.append(self.attempt(project, version)), daemon=True)
            thread.start()
            thread.join(0.2)
            self.assertTrue(thread.is_alive())
            envfiles.write(".env", {"KIOSK_GREETING": "theirs"})
        thread.join(10)
        self.assertEqual(saved, ["changed on disk"])
        self.assertEqual(envfiles.read(".env"), {"KIOSK_GREETING": "theirs"})

    def attempt(self, project, version):
        try:
            files.save_settings(project, ".env", {"KIOSK_GREETING": "mine"}, version)
        except RuntimeError as error:
            return "changed on disk" if "changed on disk" in str(error) else str(error)
        return "saved"

    def test_version_follows_the_content(self):
        (self.root / ".env").write_text("APIM_VERSION=4.12.20\n", encoding="utf-8")
        before = files._version(".env")
        self.assertEqual(before, files._version(".env"))
        envfiles.write(".env", {"APIM_VERSION": "4.13.0"})
        self.assertNotEqual(before, files._version(".env"))
        self.assertEqual(files._version(".lab/catalogs/local/addons/hyssop/.env"), files._version(".lab/catalogs/local/addons/none/.env"))

    def test_the_on_file_is_checked_as_compose_reads_it(self):
        # On the core, as the second file: it may hold more than an include list
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/local/addons/kazoo/compose.yml\n")
        given = {}

        def run(cmd, timeout):
            given.update(cmd=cmd, copy=(self.root / ".lab/.on.yml.check.yml").read_text(encoding="utf-8"))
            return 0, "", ""
        with mock.patch.object(compose, "run", side_effect=run):
            self.assertEqual(files.validate(PROJECT, ".lab/on.yml", "services: {}\n"),
                             {"valid": True, "problems": []})
        self.assertEqual(given["cmd"][-6:], ["-f", "docker-compose.yml", "-f",
                                             ".lab/.on.yml.check.yml", "config", "-q"])
        self.assertEqual(given["copy"], "services: {}\n")
        self.assertFalse((self.root / ".lab/.on.yml.check.yml").exists())

    def test_settings_form(self):
        project = {"core_variables": [], "addons": {"kazoo": {"env_file": ".lab/catalogs/local/addons/kazoo/.env", "settings": [
            {"key": "KAZOO_TOKEN", "default": None, "required": True, "comment": "Access token", "secret": True},
            {"key": "KAZOO_THEME", "default": "dark", "required": False, "comment": "Theme", "secret": False}]}}}
        (self.root / ".lab/catalogs/local/addons/kazoo/.env").write_text("KAZOO_TOKEN=abc\n", encoding="utf-8")
        form = files.settings(project, ".lab/catalogs/local/addons/kazoo/.env")
        self.assertEqual(sorted(form), ["sections", "version"])
        self.assertEqual([(s["title"], s["groups"][0]["vars"][0]["value"]) for s in form["sections"]],
                         [("Secrets", "abc"), ("Settings", "")])
        # Only a .env of the project is a form: without a file, it is not the core's
        for file in (".lab/catalogs/local/addons/other/.env", "", None):
            with self.subTest(file=file), self.assertRaisesRegex(ValueError, "Not an env file of this project"):
                files.settings(project, file)
        self.assertEqual(files.settings(project, ".env"), {"sections": [], "version": files._version(".env")})


class Drafts(Case):
    """What Compose would make each service from, were a change saved: the copies it reads in place of the files."""

    def setUp(self):
        self.folder(compose, envfiles, files, paths)
        fixtures.default(self)
        (self.root / "generated").mkdir()
        self.patch(compose, "scratch", lambda: self.root / "generated")
        self.project, self.state = lab(), None
        self.patch(layers, "current", lambda project: self.state or on(project))
        for path, text in (("docker-compose.yml", "services: {}\n"), (".lab/catalogs/local/addons/kazoo/compose.yml", "services: {}\n"),
                           (".lab/catalogs/local/addons/kazoo-desk/compose.yml", "include:\n  - ../kazoo/compose.yml\n"),
                           (".lab/catalogs/local/addons/jade/compose.yml", "services: {}\n"), (".lab/catalogs/local/addons/hyssop/compose.yml", ""),
                           (".lab/catalogs/local/playgrounds/welcome/compose.yml",
                            "include:\n  - ../../addons/kazoo-desk/compose.yml\n")):
            self.write(path, text)
        self.read = []   # what Compose was given: (its -f arguments, the copies there were, the values it was given)

        def config(files_=(), env=None, dotenv=None):
            copies = {path.relative_to(self.root).as_posix(): path.read_text(encoding="utf-8")
                      for path in self.root.rglob(".*.effects.yml")}
            self.read.append((list(files_), copies, env))
            return {"kazoo": "new"}
        self.patch(compose, "config_hashes", config)

    def on_file(self, *entries):
        self.write(".lab/on.yml", "include:\n" + "".join(f"  - {entry}\n" for entry in entries))

    def test_a_file_of_the_core(self):
        self.on_file(".lab/catalogs/local/addons/jade/compose.yml")
        self.assertEqual(files.draft_hashes(self.project, "docker-compose.yml", "edited"), ({"kazoo": "new"}, True))
        args, copies, _ = self.read[-1]
        self.assertEqual(args, ["-f", ".docker-compose.yml.effects.yml", "-f", ".lab/on.yml"])
        self.assertEqual(copies, {".docker-compose.yml.effects.yml": "edited"})
        self.assertEqual(list(self.root.rglob(".*.effects.yml")), [])   # removed once read
        files.draft_hashes(self.project, ".lab/on.yml", "include: []\n")
        args, copies, _ = self.read[-1]
        self.assertEqual(args, ["-f", "docker-compose.yml", "-f", ".lab/.on.yml.effects.yml"])
        self.assertEqual(copies, {".lab/.on.yml.effects.yml": "include: []\n"})

    def test_the_copies_are_not_for_git(self):
        # A copy is removed once read; one a stopped lab_ui left behind is git-ignored all the same, as is the copy a
        # check reads
        ignored = [line.strip() for line in (paths.LAB_UI_DIR.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#")]
        for copy in (files._copy(".lab/catalogs/local/addons/kazoo/compose.yml"), ".lab/catalogs/local/addons/kazoo/.compose.yml.check.yml"):
            name = copy.split("/")[-1]
            self.assertTrue(any(fnmatch.fnmatch(name, pattern) for pattern in ignored), name)

    def test_a_file_the_on_file_includes_through_others(self):
        # The welcome includes Kazoo Desk, which includes Kazoo: each on the way is a copy that includes the next
        self.on_file(".lab/catalogs/local/playgrounds/welcome/compose.yml", ".lab/catalogs/local/addons/jade/compose.yml")
        self.assertEqual(files.draft_hashes(self.project, ".lab/catalogs/local/addons/kazoo/compose.yml", "edited"),
                         ({"kazoo": "new"}, True))
        args, copies, _ = self.read[-1]
        self.assertEqual(copies, {
            ".lab/catalogs/local/addons/kazoo/.compose.yml.effects.yml": "edited",
            ".lab/catalogs/local/addons/kazoo-desk/.compose.yml.effects.yml": "include:\n  - ../kazoo/.compose.yml.effects.yml\n",
            ".lab/catalogs/local/playgrounds/welcome/.compose.yml.effects.yml":
                "include:\n  - ../../addons/kazoo-desk/.compose.yml.effects.yml\n"})
        generated = (self.root / args[-1]).read_text(encoding="utf-8")
        self.assertEqual(generated, f'include:\n  - "{self.root}/.lab/catalogs/local/playgrounds/welcome/.compose.yml.effects.yml"\n'
                                    f'  - "{self.root}/.lab/catalogs/local/addons/jade/compose.yml"\n')
        self.assertEqual(args[:2], ["-f", "docker-compose.yml"])

    def test_a_file_nothing_on_includes_changes_nothing(self):
        self.on_file(".lab/catalogs/local/addons/jade/compose.yml")
        self.assertEqual(files.draft_hashes(self.project, ".lab/catalogs/local/addons/kazoo/compose.yml", "edited"), (None, True))
        (self.root / ".lab/on.yml").unlink()
        self.assertEqual(files.draft_hashes(self.project, ".lab/catalogs/local/addons/kazoo/compose.yml", "edited"), (None, True))
        self.assertEqual(self.read, [])

    def test_what_cannot_be_known(self):
        # An on file with the user's own changes; a text that does not load
        self.on_file(".lab/catalogs/local/addons/kazoo/compose.yml")
        self.state = on(self.project, ["kazoo"], locked=layers.LOCKED)
        self.assertEqual(files.draft_hashes(self.project, ".lab/catalogs/local/addons/kazoo/compose.yml", "edited"), (None, False))
        self.state = None
        self.patch(compose, "config_hashes", lambda files_=(), env=None, dotenv=None: None)
        self.assertEqual(files.draft_hashes(self.project, ".lab/catalogs/local/addons/kazoo/compose.yml", "broken"), (None, False))

    def test_values_of_a_settings_form(self):
        # The core's .env: a private copy with the values as a save writes them; Compose reads it in place
        self.write(".env", "# The license\nGRAVITEE_LICENSE=old\nAPIM_VERSION=4.12\n")
        self.project["core_variables"] = [
            {"key": "GRAVITEE_LICENSE", "default": "", "required": False, "comment": "", "secret": True},
            {"key": "APIM_VERSION", "default": "4.12", "required": False, "comment": "", "secret": False}]
        copies = []
        self.patch(compose, "config_hashes", lambda files_=(), env=None, dotenv=None: copies.append(
            (env, dotenv)) or {"kazoo": "new"})
        self.assertEqual(files.env_hashes(self.project, ".env",
                                          {"GRAVITEE_LICENSE": "new key", "APIM_VERSION": "4.12"}),
                         ({"kazoo": "new"}, True))
        self.assertEqual(copies, [(None, "# The license\nGRAVITEE_LICENSE='new key'\n")])
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"), "# The license\nGRAVITEE_LICENSE=old\nAPIM_VERSION=4.12\n")
        # An add-on's: its values given to Compose, which reads them before its .env; as they are
        given = []
        self.patch(compose, "config_hashes", lambda files_=(), env=None, dotenv=None: given.append(env) or {"x": "y"})
        hyssop = self.project["addons"]["hyssop"]["env_file"]
        self.assertEqual(files.env_hashes(self.project, hyssop, {"HYSSOP_CLOUD_TOKEN": "t"}), ({"x": "y"}, True))
        self.assertEqual(given, [{"HYSSOP_CLOUD_TOKEN": "t"}])
        self.assertEqual(files.env_hashes(self.project, hyssop, {"HYSSOP_CLOUD_TOKEN": "${OTHER}"}), (None, False))
        with self.assertRaisesRegex(ValueError, "Only secrets and settings"):
            files.env_hashes(self.project, hyssop, {"OTHER": "x"})

    def test_an_add_on_s_values_that_would_reach_another_file(self):
        # Compose reads the values it is given for every file: one that another file refers to, or that the core's
        # .env sets, would change more than the add-on, which saving its .env does not
        given = []
        self.patch(compose, "config_hashes", lambda files_=(), env=None, dotenv=None: given.append(env) or {"x": "y"})
        hyssop = self.project["addons"]["hyssop"]["env_file"]
        self.write(".lab/catalogs/fixture/addons/hyssop/compose.yml", "services:\n  hyssop-gateway:\n    environment:\n"
                                                "      TOKEN: ${HYSSOP_CLOUD_TOKEN}\n")
        self.assertEqual(files.env_hashes(self.project, hyssop, {"HYSSOP_CLOUD_TOKEN": "t"}), ({"x": "y"}, True))
        self.write(".lab/catalogs/fixture/addons/jade/compose.yml", "services:\n  jade:\n    environment:\n      T: ${HYSSOP_CLOUD_TOKEN:-}\n")
        self.assertEqual(files.env_hashes(self.project, hyssop, {"HYSSOP_CLOUD_TOKEN": "t"}), (None, False))
        self.write(".lab/catalogs/fixture/addons/jade/compose.yml", "services: {}\n")
        self.write(".env", "HYSSOP_CLOUD_TOKEN=core\n")
        self.assertEqual(files.env_hashes(self.project, hyssop, {"HYSSOP_CLOUD_TOKEN": "t"}), (None, False))
        self.assertEqual(given, [{"HYSSOP_CLOUD_TOKEN": "t"}])

    def test_what_a_change_may_recreate_when_it_cannot_be_known(self):
        self.write(self.project["addons"]["kazoo"]["file"])
        self.write(self.project["addons"]["hyssop"]["file"])
        self.state = on(self.project, ["kazoo"])
        self.assertEqual(files.affected(self.project, ".lab/catalogs/fixture/addons/kazoo/compose.yml"),
                         ["kazoo", "kazoo-ui", "apim-gateway"])
        self.assertEqual(files.affected(self.project, ".lab/catalogs/fixture/addons/hyssop/.env"), ["hyssop-gateway"])
        self.assertEqual(sorted(files.affected(self.project, ".env")),
                         ["apim-gateway", "apim-management-api", "kazoo", "kazoo-ui", "mongodb"])


# ---------------------------------------------------------------------------------------------------------------------
# The files of the catalogs
# ---------------------------------------------------------------------------------------------------------------------

ECHO = fixtures.unit_file("fixture", "addon", "echo")   # .lab/catalogs/fixture/addons/echo/compose.yml
SHOP, PAIR = fixtures.unit_file("fixture", "playground", "shop"), fixtures.unit_file("fixture", "setup", "pair")
GUIDE = SHOP.replace("compose.yml", "guide.md")
ECHO_ENV = ECHO.replace("compose.yml", ".env")
UNIT = {"addons": {}, "playgrounds": {}}   # a project that knows no unit: the file being checked is the only one
SETTING = {"key": "FIXTURE_ECHO_GREETING", "default": "hello", "required": False, "comment": "What it says first",
           "secret": False}
ALL = (ECHO, SHOP, GUIDE, PAIR)
NOT_FILES = ["", None, 5, [], {}, "..", "/etc/passwd", "\0", "addons/x/../../compose.yml", ECHO + "\0", ECHO + "/",
             ECHO.replace("fixture", ".stage-fixture"), ECHO.replace("compose.yml", "certs/compose.yml"),
             ECHO.replace("echo", "ECHO"), ECHO.replace("echo", "../echo"), PAIR + "/.env",
             PAIR.replace("pair.yml", ".env"), ECHO.replace("fixture", "local").replace("echo", "nothing"),
             "addons/echo/compose.yml", "./" + ECHO, ECHO.replace("fixture", "fixture/.."),
             ECHO.replace("/addons/", "//addons/")]


def tree(root):
    """{project-relative path: kind} of everything under a folder, to see that a call left it as it was."""
    root = Path(root)
    return {path.relative_to(root).as_posix(): "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
            for path in sorted(root.rglob("*"))}


class Git:
    """What every install from git is: the compose files and guides of its units are read, never checked or saved, and
    their settings are this machine's. Mixed into a case that makes the install (`install()`)."""

    def setUp(self):
        self.install()
        self.inputs = self.patch(registry, "inputs",
                                 return_value={"fixture": {"kind": "git", "name": "Fixture catalog"}})
        self.run = self.patch(compose, "run", side_effect=AssertionError("Compose was called"))
        self.patch(layers, "current", lambda project: {"locked": None, "playground": None, "effective": []})

    def refused(self, call, *args, reason="Part of Fixture catalog"):
        with self.assertRaises(files.ReadOnly) as raised:
            call(UNIT, *args)
        self.assertEqual(str(raised.exception), reason)
        return raised.exception

    def test_the_files_are_read(self):
        for path in ALL:
            self.assertEqual(files.read(path)["content"], (self.root / path).read_text(encoding="utf-8"))

    def test_checking_and_saving_are_refused_with_the_name_of_the_catalog_as_a_conflict(self):
        for path in ALL:
            before = (self.root / path).read_bytes()
            with self.subTest(path):
                version = files._version(path)
                error = self.refused(files.validate, path, "services: {}\n")
                self.refused(files.save, path, "services: {}\n", version)
                self.refused(files.draft_hashes, path, "services: {}\n")
                self.assertEqual((self.root / path).read_bytes(), before)
                # The API answers a RuntimeError with 409, as for a file that changed on disk
                self.assertEqual(next(code for kinds, code in api.ERRORS if isinstance(error, kinds)), 409)
        self.run.assert_not_called()

    def test_the_settings_form_is_edited(self):
        project = {"core_variables": [], "addons": {"echo": {"env_file": ECHO_ENV, "settings": [SETTING]}}}
        before = tree(self.root)
        form = files.settings(project, ECHO_ENV)
        self.assertEqual(form["sections"][0]["groups"][0]["vars"], [{"key": "FIXTURE_ECHO_GREETING", "value": "",
                                                                     "default": "hello", "required": False}])
        saved = files.save_settings(project, ECHO_ENV, {"FIXTURE_ECHO_GREETING": "bonjour tout le monde"},
                                    form["version"])
        self.assertEqual((self.root / ECHO_ENV).read_text(encoding="utf-8"),
                         "FIXTURE_ECHO_GREETING='bonjour tout le monde'\n")
        self.assertEqual(oct((self.root / ECHO_ENV).stat().st_mode & 0o777), "0o600")
        self.assertEqual(saved["sections"][0]["groups"][0]["vars"][0]["value"], "bonjour tout le monde")
        self.assertEqual(set(tree(self.root)) - set(before), {ECHO_ENV})
        files.save_settings(project, ECHO_ENV, {"FIXTURE_ECHO_GREETING": "hello"}, saved["version"])   # the default
        self.assertFalse((self.root / ECHO_ENV).exists())


class Held(Git, Case):
    """The install, as a copy of the fixture catalog with the manifest that a git install has."""

    def install(self):
        fixtures.project(self, paths, compose, envfiles, files)
        self.write(".lab/catalogs/fixture/.installed.json", "{}\n")   # what makes a folder the install of a git catalog

    def test_without_a_name_from_the_registry_the_namespace_stands_in(self):
        hostile = [{}, None, [], "text", {"fixture": None}, {"fixture": {"name": 5}}, {"fixture": {"name": ["x"]}},
                   {"fixture": {"name": ""}}, {"fixture": {"name": "  \n\x00 "}}, {"other": {"name": "Other"}}]
        for rows in hostile:
            with self.subTest(rows):
                self.inputs.return_value = rows
                self.refused(files.validate, ECHO, "services: {}\n", reason="Part of fixture")
        self.inputs.side_effect = RuntimeError("the registry is broken")
        self.refused(files.validate, ECHO, "services: {}\n", reason="Part of fixture")

    def test_a_name_is_text_for_a_person(self):
        self.inputs.return_value = {"fixture": {"kind": "git", "name": "<img src=x onerror=1>\n\x00\u202e" + "x" * 300}}
        with self.assertRaises(files.ReadOnly) as raised:
            files.validate(UNIT, ECHO, "services: {}\n")
        text = str(raised.exception)
        self.assertTrue(text.startswith("Part of <img src=x onerror=1> x"))
        self.assertTrue(text.isprintable() and len(text) <= len("Part of ") + 80)

    def test_the_registry_can_say_a_catalog_is_from_git_and_a_folder_without_a_manifest_is_not(self):
        os.remove(self.root / ".lab/catalogs/fixture/.installed.json")   # damaged: the manifest is gone
        self.refused(files.validate, ECHO, "services: {}\n")
        self.inputs.return_value = {"fixture": {"kind": "folder"}}
        self.patch(compose, "run", return_value=(0, "", ""))
        self.assertTrue(files.validate(UNIT, ECHO, "services: {}\n")["valid"])

    def test_a_link_to_a_folder_is_never_an_install_even_if_the_folder_holds_a_manifest(self):
        fixtures.project(self, paths, compose, envfiles, files, link=True)
        self.write(".lab/catalogs/fixture/.installed.json", "{}\n")   # written in the author's folder, through the link
        self.assertTrue((self.root / ".lab/catalogs/fixture").is_symlink())
        self.inputs.return_value = {"fixture": {"kind": "folder", "name": "Fixture catalog"}}
        self.patch(compose, "run", return_value=(0, "", ""))
        saved = files.save(UNIT, ECHO, "services: {}\n", files._version(ECHO))
        self.assertEqual(saved["content"], "services: {}\n")
        self.assertEqual(files.validate(UNIT, GUIDE, "# Guide\n"), {"valid": True, "problems": []})
        self.assertEqual(files.validate(UNIT, PAIR, "services: {}\n"), {"valid": True, "problems": []})

    def test_the_files_of_a_folder_catalog_are_edited(self):
        fixtures.project(self, paths, compose, envfiles, files)   # a copy of the folder: no manifest
        self.inputs.return_value = {"fixture": {"kind": "folder", "name": "Fixture catalog"}}
        self.patch(compose, "run", return_value=(0, "", ""))
        saved = files.save(UNIT, ECHO, "services: {}\n", files._version(ECHO))
        self.assertEqual(saved["content"], "services: {}\n")
        self.assertEqual(files.validate(UNIT, GUIDE, "# Guide\n"), {"valid": True, "problems": []})

    def test_the_items_of_this_machine_are_edited(self):
        fixtures.local(self, "scratch")
        path = fixtures.unit_file("local", "addon", "scratch")
        self.patch(compose, "run", return_value=(0, "", ""))
        self.assertEqual(files.validate(UNIT, path, "services: {}\n"), {"valid": True, "problems": []})
        self.assertEqual(files.save(UNIT, path, "services: {}\n", files._version(path))["content"], "services: {}\n")

    def test_the_sections_say_which_files_are_only_read(self):
        sandbox = fixtures.unit_file("local", "playground", "sandbox")
        fixtures.local(self, "sandbox")
        fixtures.install(self, "partner", "folder")
        self.inputs.return_value = {"fixture": {"kind": "git", "name": "Fixture catalog"},
                                    "partner": {"kind": "folder", "name": "Partner Bank"}}

        def unit(kind, uid, name, **more):
            return {"id": uid, "kind": kind, "name": name, "needs": [], "members": [], "addons": [], "setups": [],
                    "settings": [], "env_file": None, **more}
        project = {"addons": {"echo": unit("addon", "echo", "Echo", settings=[SETTING], env_file=ECHO_ENV),
                              "@partner/ledger": unit("addon", "@partner/ledger", "Ledger"),
                              "local/scratch": unit("addon", "local/scratch", "Scratch")},
                   "setups": {}, "services": {},
                   "playgrounds": {"local/sandbox": unit("playground", "local/sandbox", "Sandbox",
                                                         addons=["echo", "@partner/ledger", "local/scratch"])}}
        self.patch(layers, "current", lambda one: {"playground": None, "effective": []})
        self.patch(compose, "containers", lambda *variable: ({}, None))
        found = files.sections(project, sandbox)
        self.assertEqual([(section["title"], section.get("readonly"), section.get("reason")) for section in found], [
            ("Core", None, None), ("Sandbox", None, None), ("Echo", True, "Part of Fixture catalog"),
            ("Ledger", None, None), ("Scratch", None, None)])
        # What can be edited of a section that is only read: its settings, which are a form
        echo = found[2]
        self.assertEqual([(file["kind"], file["path"]) for file in echo["files"]],
                         [("compose", ECHO), ("env", ECHO_ENV)])
        self.assertEqual(sorted(echo), ["files", "on", "readonly", "reason", "runs", "title"])
        self.assertEqual(sorted(found[1]), ["files", "on", "runs", "title"])   # nothing is added to an editable section
        self.assertEqual(self.inputs.call_count, 1, "the registry is asked once for the whole pane")

    def test_a_project_with_no_catalog_has_nothing_held(self):
        self.folder(paths, compose, envfiles, files)
        self.write(KAZOO, "services: {}\n")
        self.patch(compose, "run", return_value=(0, "", ""))
        saved = files.save(PROJECT, KAZOO, "services: {kazoo: {}}\n", files._version(KAZOO))
        self.assertEqual(saved["content"], "services: {kazoo: {}}\n")
        self.inputs.assert_not_called()


class Installed(Git, Case):
    """The install a real clone makes: a bare repository, the manifest, read-only files."""

    def install(self):
        fixtures.need_git()
        fixtures.project(self, paths, compose, envfiles, files, kind="git")

    def test_the_files_of_the_install_are_read_only_on_disk_too(self):
        self.assertTrue((self.root / ".lab/catalogs/fixture/.installed.json").is_file())
        self.assertEqual((self.root / ECHO).stat().st_mode & 0o222, 0)


class Scratch(Case):
    """A file of a catalog is checked and previewed from a copy in the scratch folder, read from the folder of the file:
    nothing is written beside it. Compose is replaced by a function that looks at what it was given."""

    def setUp(self):
        fixtures.project(self, paths, compose, envfiles, files)
        holder = tempfile.TemporaryDirectory()   # outside the project folder, as the Lab UI's scratch folder is
        self.addCleanup(holder.cleanup)
        self.scratch = Path(holder.name).resolve()
        self.patch(compose, "scratch", lambda: self.scratch)
        self.patch(registry, "inputs", return_value={})
        self.patch(layers, "current", lambda project: {"locked": None})
        self.seen = []

    def folder_of(self, path):
        return self.root / posixpath.dirname(path)

    def in_scratch(self):
        """{name: text} of what the scratch folder holds now."""
        return {file.name: file.read_text(encoding="utf-8") for file in self.scratch.iterdir()}

    def watch(self, path, code=0, err=""):
        """A Compose that records what was in the unit's folder and in the scratch folder when it ran."""
        def run(cmd, timeout=120, cwd=None, env=None):
            self.seen.append({"cmd": cmd, "beside": sorted(os.listdir(self.folder_of(path))),
                              "scratch": self.in_scratch()})
            return code, "", err
        return self.patch(compose, "run", run)

    def test_a_file_is_checked_from_a_copy_in_the_scratch_folder(self):
        before = tree(self.root)
        beside = sorted(os.listdir(self.folder_of(ECHO)))
        self.watch(ECHO)
        self.assertEqual(files.validate(UNIT, ECHO, EDITED), {"valid": True, "problems": []})
        (seen,) = self.seen
        self.assertEqual(seen["beside"], beside)   # nothing was put beside the file while Compose read it
        copies = {name: text for name, text in seen["scratch"].items() if name.startswith("check-")}
        copy = next(name for name in copies if not name.endswith(".include.yml"))
        self.assertEqual(copies[copy], EDITED)
        # It is included from the folder of the file, which is what its relative paths, includes and .env are read from
        self.assertEqual(copies[copy.replace(".yml", ".include.yml")],
                         f'include:\n  - path: "{self.scratch / copy}"\n    project_directory: "{self.folder_of(ECHO)}"\n')
        self.assertEqual(seen["cmd"][-2:], ["config", "-q"])
        self.assertEqual(tree(self.root), before)
        self.assertEqual(sorted(file.name for file in self.scratch.iterdir() if file.name.startswith("check-")), [])

    def test_what_else_is_on_is_checked_with_it_and_the_file_once(self):
        project = {"addons": {"echo": {"kind": "addon", "file": ECHO, "needs": []},
                              "other": {"kind": "addon", "file": "addons/other/compose.yml", "needs": []}},
                   "playgrounds": {}}
        self.watch(ECHO)
        files.validate(project, ECHO, EDITED)
        generated = [name for name in self.seen[0]["scratch"] if name.startswith("all-")]
        (name,) = generated
        self.assertEqual(self.seen[0]["scratch"][name], f'include:\n  - "{self.root / "addons/other/compose.yml"}"\n')
        arguments = self.seen[0]["cmd"]
        self.assertEqual(arguments.count(str(self.scratch / name)), 1)
        self.assertTrue(arguments[arguments.index("config") - 1].endswith(".include.yml"))   # the checked file: last

    def test_the_name_of_the_copy_is_the_name_of_the_file_in_what_compose_says(self):
        def refused(cmd, timeout=120, cwd=None, env=None):
            copy = next(arg for arg in cmd if arg.endswith(".include.yml")).replace(".include.yml", ".yml")
            return 1, "", f"validating {copy}: services.fixture-echo additional properties 'imgae' not allowed"
        self.patch(compose, "run", refused)
        found = files.validate(UNIT, ECHO, EDITED)
        self.assertFalse(found["valid"])
        self.assertEqual(found["problems"][0]["message"],
                         f"validating {ECHO}: services.fixture-echo additional properties 'imgae' not allowed")
        self.assertEqual(found["problems"][0]["severity"], "error")

    def test_a_setup_of_a_folder_catalog_is_checked_the_same_way(self):
        self.watch(PAIR)
        self.assertTrue(files.validate(UNIT, PAIR, "include: []\n")["valid"])
        (seen,) = self.seen
        include = next(text for name, text in seen["scratch"].items() if name.endswith(".include.yml"))
        self.assertIn(f'project_directory: "{self.root / ".lab/catalogs/fixture/setups"}"', include)

    def test_a_file_of_this_machine_and_the_core_are_still_checked_from_a_copy_beside_them(self):
        fixtures.local(self, "scratch")
        path = fixtures.unit_file("local", "addon", "scratch")
        self.watch(path)
        files.validate(UNIT, path, EDITED)
        self.assertIn(".compose.yml.check.yml", self.seen[0]["beside"])
        self.assertFalse([name for name in self.seen[0]["scratch"] if name.startswith("check-")])
        self.watch("docker-compose.yml")
        files.validate(UNIT, "docker-compose.yml", "services: {}\n")
        self.assertEqual(tree(self.root).get(".docker-compose.yml.check.yml"), None)

    def preview(self, path, content, *listed):
        """What Compose is given for a preview of a file while the on file lists these: (the -f arguments, the files of
        the scratch folder and the folders of the project, as they stood when it ran). Compose is replaced."""
        self.write(".lab/on.yml", "include:\n" + "".join(f"  - {file}\n" for file in listed))
        given, before = [], tree(self.root)

        def hashes(arguments=(), env=None, dotenv=None):
            given.append({"files": list(arguments), "scratch": self.in_scratch(),
                          "beside": {folder: sorted(os.listdir(self.root / folder)) for folder in
                                     {posixpath.dirname(file) for file in (path, *listed)}}})
            return {"fixture-echo": "abc"}
        self.patch(compose, "config_hashes", hashes)
        self.assertEqual(files.draft_hashes(UNIT, path, content), ({"fixture-echo": "abc"}, True))
        (seen,) = given
        self.assertEqual(tree(self.root), before)   # nothing was written beside a file, and nothing is left
        self.assertEqual(self.in_scratch(), {})
        return seen

    def test_a_preview_of_a_file_the_on_file_includes_is_read_from_a_copy_in_the_scratch_folder(self):
        beside = sorted(os.listdir(self.folder_of(ECHO)))
        seen = self.preview(ECHO, EDITED, ECHO, SHOP)
        self.assertEqual(seen["beside"][posixpath.dirname(ECHO)], beside)
        self.assertEqual(sorted(name.split("-")[0] for name in seen["scratch"]), ["draft", "draft"])
        copy = next(name for name in seen["scratch"] if not name.endswith(".on.yml"))
        self.assertEqual(seen["scratch"][copy], EDITED)
        # The on file's list, written again with the copy where the file was, read from the folder of the file
        listing = next(name for name in seen["scratch"] if name.endswith(".on.yml"))
        self.assertEqual(seen["files"], ["-f", "docker-compose.yml", "-f", str(self.scratch / listing)])
        self.assertEqual(seen["scratch"][listing],
                         f'include:\n  - path: "{self.scratch / copy}"\n    project_directory: "{self.folder_of(ECHO)}"\n'
                         f'  - "{self.root / SHOP}"\n')

    def test_a_preview_keeps_the_place_of_the_file_in_the_on_file(self):
        # Where a file stands decides what Compose makes of a service that several files change
        seen = self.preview(SHOP, "services: {}\n", ECHO, SHOP, PAIR)
        listing = next(text for name, text in seen["scratch"].items() if name.endswith(".on.yml"))
        copy = next(name for name in seen["scratch"] if not name.endswith(".on.yml"))
        self.assertEqual(listing,
                         f'include:\n  - "{self.root / ECHO}"\n  - path: "{self.scratch / copy}"\n'
                         f'    project_directory: "{self.folder_of(SHOP)}"\n  - "{self.root / PAIR}"\n')

    def test_what_other_files_include_is_copied_to_the_scratch_folder_too(self):
        # The on file includes the setup, which includes echo: the setup is copied with its item written again to a
        # file that includes the copy of echo in place, and the on file lists the copy of the setup in place
        seen = self.preview(ECHO, EDITED, PAIR)
        self.assertEqual(sorted(name.split(".", 1)[1] for name in seen["scratch"]), ["include.yml", "on.yml", "yml", "yml"])
        names = sorted(seen["scratch"])
        wrapper = next(name for name in names if name.endswith(".include.yml"))
        copy = wrapper.replace(".include.yml", ".yml")
        self.assertEqual(seen["scratch"][copy], EDITED)
        self.assertEqual(seen["scratch"][wrapper],
                         f'include:\n  - path: "{self.scratch / copy}"\n    project_directory: "{self.folder_of(ECHO)}"\n')
        pair = next(name for name in names if name.endswith(".yml") and name not in (copy, wrapper)
                    and not name.endswith(".on.yml"))
        relative = os.path.relpath(self.scratch / wrapper, self.folder_of(PAIR))
        self.assertEqual(seen["scratch"][pair], (self.root / PAIR).read_text(encoding="utf-8").replace(
            "../addons/echo/compose.yml", relative))
        listing = next(text for name, text in seen["scratch"].items() if name.endswith(".on.yml"))
        self.assertEqual(listing, f'include:\n  - path: "{self.scratch / pair}"\n'
                                  f'    project_directory: "{self.folder_of(PAIR)}"\n')
        self.assertFalse([name for folder in seen["beside"].values() for name in folder if name.endswith(".effects.yml")])

    def test_a_file_of_this_machine_that_a_catalog_file_includes_is_not_copied_beside_either(self):
        fixtures.local(self, "scratch")
        mine = fixtures.unit_file("local", "addon", "scratch")
        (self.root / ECHO).write_text("include:\n  - ../../../local/addons/scratch/compose.yml\n", encoding="utf-8")
        seen = self.preview(mine, EDITED, ECHO)
        self.assertFalse([name for folder in seen["beside"].values() for name in folder if name.endswith(".effects.yml")])
        self.assertEqual(len(seen["scratch"]), 4)   # the copy of each, the file that includes the copy of mine, the list

    def test_a_catalog_file_that_a_file_of_this_machine_includes_is_not_copied_beside_either(self):
        fixtures.local(self, "scratch")
        mine = fixtures.unit_file("local", "addon", "scratch")
        (self.root / mine).write_text("include:\n  - ../../../fixture/addons/echo/compose.yml\n", encoding="utf-8")
        seen = self.preview(ECHO, EDITED, mine)
        self.assertFalse([name for folder in seen["beside"].values() for name in folder if name.endswith(".effects.yml")])

    def test_a_file_that_includes_it_and_cannot_be_read_leaves_the_preview_unknown(self):
        # Not UTF-8: nothing is known, nothing is written, and nothing raises; a file that is gone includes nothing
        self.write(".lab/on.yml", f"include:\n  - {PAIR}\n  - {SHOP}\n")
        self.patch(compose, "config_hashes", side_effect=AssertionError("Compose was asked"))
        (self.root / PAIR).write_bytes(b"include:\n  - ../addons/echo/compose.yml\n\xff\xfe\n")
        before = tree(self.root)
        self.assertEqual(files.draft_hashes(UNIT, ECHO, EDITED), (None, False))
        self.assertEqual(files.draft_hashes(UNIT, SHOP, "services: {}\n"), (None, False))
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.in_scratch(), {})
        (self.root / PAIR).unlink()
        self.assertEqual(files.draft_hashes(UNIT, ECHO, EDITED), (None, True))   # nothing on includes it any more

    def test_a_file_of_this_machine_is_previewed_from_a_copy_beside_it_as_before(self):
        fixtures.local(self, "scratch")
        mine = fixtures.unit_file("local", "addon", "scratch")
        self.write(".lab/on.yml", f"include:\n  - {mine}\n")
        seen = []

        def hashes(arguments=(), env=None, dotenv=None):
            seen.append(sorted(os.listdir(self.folder_of(mine))))
            return {}
        self.patch(compose, "config_hashes", hashes)
        files.draft_hashes(UNIT, mine, EDITED)
        self.assertIn(".compose.yml.effects.yml", seen[0])
        self.assertNotIn(".compose.yml.effects.yml", os.listdir(self.folder_of(mine)))

    def test_a_text_that_is_not_text_is_refused_before_anything_is_written(self):
        before = tree(self.root)
        self.patch(compose, "run", side_effect=AssertionError("Compose was called"))
        for content in (None, 5, [], {}, b"services: {}", ("x",)):
            for path in (ECHO, "docker-compose.yml", GUIDE):
                with self.subTest(content=content, path=path):
                    for call in (lambda: files.validate(UNIT, path, content),
                                 lambda: files.save(UNIT, path, content, files._version(path)),
                                 lambda: files.draft_hashes(UNIT, path, content)):
                        with self.assertRaisesRegex(ValueError, "The content must be text"):
                            call()
        self.assertEqual(tree(self.root), before)


class Shapes(Case):
    """What names a unit is the shape of its path and one look at its compose file: no listing of the units."""

    def setUp(self):
        fixtures.project(self, paths, compose, envfiles, files)
        fixtures.local(self, "scratch")
        fixtures.local(self, "sandbox")

    def test_a_unit_is_found_without_listing_any_folder(self):
        mine = fixtures.unit_file("local", "addon", "scratch")
        sandbox = fixtures.unit_file("local", "playground", "sandbox")
        cases = {ECHO: ("addon", "echo"), ECHO_ENV: ("addon", "echo"), PAIR: ("setup", "pair"),
                 SHOP: ("playground", "shop"), GUIDE: ("playground", "shop"),
                 SHOP.replace("compose.yml", ".env"): ("playground", "shop"), mine: ("addon", "local/scratch"),
                 mine.replace("compose.yml", ".env"): ("addon", "local/scratch")}
        listing = AssertionError("a folder was listed")
        names = [(paths, "ids"), (paths, "files"), (Path, "glob"), (Path, "rglob"), (os, "listdir"), (os, "scandir")]
        with contextlib.ExitStack() as stack:
            for where, name in names:
                stack.enter_context(mock.patch.object(where, name, side_effect=listing))
            for path, unit in cases.items():
                with self.subTest(path):
                    self.assertEqual(files._unit_of(path), unit)
            self.assertTrue(files._is_guide(GUIDE))
            self.assertTrue(files._is_guide(sandbox.replace("compose.yml", "guide.md")))
            self.assertFalse(files._is_guide(ECHO))
            self.assertFalse(files._is_guide(GUIDE.replace("shop", "tour").replace("guide", "other")))

    def test_what_a_unit_has_beside_its_compose_file_depends_on_its_kind(self):
        # An add-on has no guide, a setup has no folder and no .env, and a unit that is not there has nothing
        for path in (ECHO.replace("compose.yml", "guide.md"), PAIR.replace(".yml", "/.env"),
                     PAIR.replace("pair.yml", ".env"), ECHO.replace("echo", "nothing").replace("compose.yml", ".env"),
                     GUIDE.replace("shop", "nothing")):
            with self.subTest(path), self.assertRaisesRegex(ValueError, "Not a file of this project"):
                files._unit_of(path)
        self.assertFalse(files._is_guide(ECHO.replace("compose.yml", "guide.md")))
        self.assertFalse(files._is_guide(GUIDE.replace("shop", "nothing")))

    def test_what_is_not_a_file_of_a_unit_is_refused_by_every_call_with_a_value_error_or_not_found(self):
        self.patch(compose, "run", side_effect=AssertionError("Compose was called"))
        self.patch(layers, "current", lambda project: {"locked": None, "playground": None, "effective": []})
        for path in NOT_FILES:
            with self.subTest(path=path):
                for call in (lambda: files._unit_of(path), lambda: files.sections(UNIT, path), lambda: files.read(path),
                             lambda: files.validate(UNIT, path, "x"), lambda: files.save(UNIT, path, "x", "v"),
                             lambda: files.draft_hashes(UNIT, path, "x")):
                    with self.assertRaises((ValueError, paths.NotFound)):
                        call()

    def test_the_variables_of_the_other_files_are_looked_for_in_every_unit_file(self):
        project = {"core_variables": [], "playgrounds": {},
                   "addons": {"echo": {"env_file": ECHO_ENV, "settings": [SETTING], "file": ECHO}}}
        self.patch(compose, "config_hashes", lambda files_=(), env=None, dotenv=None: {"x": "y"})
        with mock.patch.object(paths, "files", wraps=paths.files) as listed:
            self.assertEqual(files.env_hashes(project, ECHO_ENV, {"FIXTURE_ECHO_GREETING": "x"}), ({"x": "y"}, True))
        listed.assert_called_once_with()
        # a catalog's unit that reads the setting makes the values unknowable, as a file of the project does
        self.write(PAIR.replace("pair.yml", "../addons/other/compose.yml").replace("setups/../", ""),
                   "services:\n  other:\n    environment:\n      A: ${FIXTURE_ECHO_GREETING:-}\n")
        self.assertEqual(files.env_hashes(project, ECHO_ENV, {"FIXTURE_ECHO_GREETING": "x"}), (None, False))


class Rendered(Case):
    """Compose itself, on the files (a CLI is needed, a daemon is not): a catalog file checked from the scratch folder
    is read as it is in its own folder."""

    LINK = {}   # what the catalog is installed as: a copy in the project folder, or a link to the author's folder

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, compose, envfiles, files, **self.LINK)
        self.patch(registry, "inputs", return_value={})
        self.patch(layers, "current", lambda project: {"locked": None})
        self.text = (self.root / ECHO).read_text(encoding="utf-8")

    def errors(self, found):
        return [problem["message"] for problem in found["problems"] if problem["severity"] == "error"]

    def snapshot(self):
        """The project folder and the folder the catalog is in, which is another one when the catalog is a link."""
        return tree(self.root), tree((self.root / ".lab/catalogs/fixture").resolve())

    def test_a_file_is_valid_or_not_as_it_is_in_its_folder(self):
        before = self.snapshot()
        found = files.validate(UNIT, ECHO, self.text.replace("hello", "bonjour"))
        self.assertEqual((found["valid"], self.errors(found)), (True, []))
        found = files.validate(UNIT, ECHO, self.text.replace("image:", "imgae:"))
        self.assertFalse(found["valid"])
        self.assertIn("imgae", self.errors(found)[0])
        self.assertIn(ECHO, self.errors(found)[0])   # the file, not the copy
        self.assertEqual(self.snapshot(), before)

    def test_what_a_file_includes_is_read_from_its_folder(self):
        gone = self.text + "include:\n  - ../nothing/compose.yml\n"
        found = files.validate(UNIT, ECHO, gone)
        self.assertFalse(found["valid"])
        self.assertIn(".lab/catalogs/fixture/addons/nothing/compose.yml", self.errors(found)[0])
        found = files.validate(UNIT, ECHO, self.text + "include:\n  - ../echo-core/compose.yml\n")
        self.assertNotIn("no such file", " ".join(self.errors(found)))

    def test_the_env_file_beside_the_file_applies(self):
        needs = self.text.replace("hello}", "hello}\n      NEEDED: ${FIXTURE_ECHO_NEEDED:?set in the .env}")
        self.assertIn("NEEDED", needs)
        found = files.validate(UNIT, ECHO, needs)
        self.assertFalse(found["valid"])
        self.assertIn("FIXTURE_ECHO_NEEDED", " ".join(self.errors(found)))
        self.write(ECHO_ENV, "FIXTURE_ECHO_NEEDED=yes\n")
        found = files.validate(UNIT, ECHO, needs)
        self.assertEqual((found["valid"], self.errors(found)), (True, []))

    def test_a_preview_gives_the_hashes_of_the_file_in_place(self):
        self.write(ECHO_ENV, "FIXTURE_ECHO_VERSION=1.37\n")
        self.write(".lab/on.yml", f"include:\n  - {ECHO}\n")
        in_place = compose.config_hashes()
        self.assertIn("fixture-echo", in_place)
        before = self.snapshot()
        self.assertEqual(files.draft_hashes(UNIT, ECHO, self.text), (in_place, True))
        changed = self.text.replace("hello", "bonjour")
        hashes, exact = files.draft_hashes(UNIT, ECHO, changed)
        self.assertTrue(exact)
        self.assertEqual({name for name in hashes if hashes[name] != in_place[name]}, {"fixture-echo"})
        self.assertEqual(self.snapshot(), before)

    def overlays(self, *names):
        """Add-ons of the fixture catalog that each add a port, a volume and a variable to the core's gateway,
        in the on file in this order. Answers their files."""
        found = []
        for number, name in enumerate(names):
            found.append(self.write(
                f".lab/catalogs/fixture/addons/{name}/compose.yml",
                f"services:\n  apim-gateway:\n    ports:\n      - \"127.0.0.1:{9300 + number}:{9300 + number}\"\n"
                f"    volumes:\n      - {name}-data:/data/{name}\n    environment:\n      OVERLAY_{name.upper()}: {name}\n"
                f"volumes:\n  {name}-data:\n"))
        self.write(".lab/on.yml", "include:\n" + "".join(f"  - {file}\n" for file in found))
        return found

    def test_a_preview_keeps_the_place_of_a_file_among_those_that_change_the_same_service(self):
        # Where the file stands in the on file decides what Compose makes of the service they all change: the preview
        # of an unchanged text is the hashes of the files in place at every position, and one of a change differs in
        # the service it changes alone
        for names in (("ova",), ("ova", "ovb"), ("ovb", "ova"), ("ova", "ovb", "ovc")):
            listing = self.overlays(*names)
            in_place, before = compose.config_hashes(), self.snapshot()
            self.assertIn("apim-gateway", in_place)
            for path in listing:
                text = (self.root / path).read_text(encoding="utf-8")
                with self.subTest(names=names, path=path):
                    self.assertEqual(files.draft_hashes(UNIT, path, text), (in_place, True))
                    hashes, exact = files.draft_hashes(UNIT, path, text.replace("OVERLAY_", "CHANGED_"))
                    self.assertTrue(exact)
                    self.assertEqual({name for name in hashes if hashes[name] != in_place[name]}, {"apim-gateway"})
            self.assertEqual(self.snapshot(), before)

    def test_a_preview_of_a_file_that_other_files_include_gives_the_hashes_of_the_files_in_place(self):
        # echo-core includes echo and changes the gateway: edit echo, which only echo-core includes, and each position
        # of the files that change the gateway around it
        echo_core = fixtures.unit_file("fixture", "addon", "echo-core")
        listing = self.overlays("ova", "ovb")
        for listed in ((echo_core,), (listing[0], echo_core, listing[1]), (listing[1], echo_core, listing[0]),
                       (ECHO, echo_core, listing[0]), (echo_core, ECHO)):
            self.write(".lab/on.yml", "include:\n" + "".join(f"  - {file}\n" for file in listed))
            in_place, before = compose.config_hashes(), self.snapshot()
            self.assertIn("fixture-echo", in_place)
            for path in {*listed, ECHO}:
                text = (self.root / path).read_text(encoding="utf-8")
                with self.subTest(listed=listed, path=path):
                    self.assertEqual(files.draft_hashes(UNIT, path, text), (in_place, True))
            hashes, exact = files.draft_hashes(UNIT, ECHO, self.text.replace("hello", "bonjour"))
            self.assertTrue(exact)
            self.assertEqual({name for name in hashes if hashes[name] != in_place[name]}, {"fixture-echo"})
            self.assertEqual(self.snapshot(), before)

    def test_a_preview_of_a_file_between_those_that_change_the_gateway_gives_the_hashes_of_the_files_in_place(self):
        # A setup that includes echo and the ledger of the partner catalog; a file of this machine that includes a file of a
        # catalog; a file of this machine, read with a copy beside it where one of a catalog is read with a copy in the
        # scratch folder, and the two ways agree
        fixtures.install(self, "partner")
        fixtures.local(self, "scratch")
        viewer = self.write(".lab/catalogs/local/addons/viewer/compose.yml",
                            "services:\n  viewer:\n    image: busybox\ninclude:\n  - ../../../fixture/addons/echo/compose.yml\n")
        pair, ledger = fixtures.unit_file("fixture", "setup", "pair"), fixtures.unit_file("partner", "addon", "ledger")
        first, last = self.overlays("ova", "ovb")
        for middle, previewed, services in ((pair, (ECHO, ledger), {"fixture-echo", "partner-ledger"}),
                                            (viewer, (ECHO,), {"viewer", "fixture-echo"}),
                                            (fixtures.unit_file("local", "addon", "scratch"), (), set())):
            self.write(".lab/on.yml", f"include:\n  - {first}\n  - {middle}\n  - {last}\n")
            in_place, before = compose.config_hashes(), self.snapshot()
            self.assertLessEqual(services, set(in_place))
            for path in (*previewed, middle, first, last):
                with self.subTest(middle=middle, path=path):
                    self.assertEqual(files.draft_hashes(UNIT, path, (self.root / path).read_text(encoding="utf-8")),
                                     (in_place, True))
            self.assertEqual(self.snapshot(), before)


class Linked(Rendered):
    """The same on a catalog that is a link to the author's folder, the maintainers' loop: a path is read as the lab
    names it, through the link, and nothing is written in the author's folder."""

    LINK = {"link": True}

    def test_the_catalog_is_a_link_to_a_folder_outside_the_project(self):
        folder = self.root / ".lab/catalogs/fixture"
        self.assertTrue(folder.is_symlink())
        self.assertNotIn(self.root, folder.resolve().parents)


if __name__ == "__main__":
    unittest.main()
