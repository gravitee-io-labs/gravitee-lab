"""`make check` and `catalog.py check`: the engine's profile (the core, the skills and the agents' files, and nothing of a
catalog, with its output pinned) and the catalog's profile (the rules of every item, the names, the paths, the ports, what
catalog.yml says and whether `lab` is the true minimum, and the verdict of the trust policy on each unit; the images a catalog
builds are the audit's, with the units it is given), and how a folder is read where it is: the temporary link, the scrubbed
environment, the units for the audit and the findings as problems with a code.

The rules of one item are tested on projects made by hand (no Docker); a whole catalog is read the way the Lab reads it, from
the fixture catalogs of tests/data with the real core (Compose renders the files, no daemon; a skip that shows without
Compose 5). The `lint` of tests/data/expected.json is compared with what the check says, and the reasons of
expected-hostile.json with what the policy gives through the project (the meta-tests of the fixtures)."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui.gravitee import frames, license, platforms
from lab_ui import paths
from lab_ui.catalogs import manifest, registry

from .. import Case, fixtures
from .test_project import catalog_inputs, member, port, service, variable

REPO = fixtures.CORE.parent   # the engine's own tree: the contract.lock that the check reads
REAL_LOCK = check._lock   # the reader of the lock, before a test puts another in its place
CORE = paths.CORE_FILE
COMPOSE = unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")


def unit_lint(ns, project, inputs):
    """{"kind:id": [the findings about the unit that are no Blocked reason]} for the units of catalog `ns`, the ids as
    the oracle of the fixtures names them: what `expected.json` calls `lint`."""
    found = check.catalog_problems(ns, project, inputs)
    out = {}
    for kind, uid, unit in check.catalog_units(ns, project):
        where = check._shown(unit["file"], ns)
        out[f"{kind}:{uid}"] = [line for file, line in found if file == where and not line.startswith("Blocked: ")]
    return out


def unit(ns, kind, name):
    return fixtures.unit_file(ns, kind, name)


KAFKA, JADE = unit("fixture", "addon", "kafka"), unit("fixture", "addon", "jade")
INSPECTOR, WHOAMI = unit("fixture", "addon", "agent-lens"), unit("local", "addon", "whoami")
WELCOME, EVENTS = unit("fixture", "playground", "welcome"), unit("fixture", "setup", "events")
MINE, HYBRID = unit("local", "setup", "mine"), unit("fixture", "addon", "hybrid")
LEDGER = unit("partner", "addon", "ledger")
MISNAMED = "the Lab UI does not see it: name it with lowercase letters, digits and dashes"
PERSONAL = "what belongs to this machine is not shared with the project"
ALWAYS = "restart: always starts a stopped container again when Docker restarts: use unless-stopped"
LITERAL = "is a literal: write ${NAME:-default} so that it is a setting (no password in a compose file)"


class Machine(Case):
    """A machine with the default catalog `fixture` and some units, made by hand: no disk but the files a rule looks for, no
    Docker. `built` is project.build of what gather() would read, `given` those inputs."""

    def setUp(self):
        self.folder(check, paths, compose)
        fixtures.default(self, "fixture")

    def machine(self, units, core=None, **more):
        """The machine with these units (member()). `core` changes the core file's raw configuration before the project is
        built; `more` is what else gather() reads (guides, variables, catalogs)."""
        self.given = catalog_inputs(units, **more)
        gateway = self.given["raw"][CORE]["services"]["apim-gateway"]
        gateway["image"] = "${APIM_GATEWAY_REGISTRY:-graviteeio}/apim-gateway:${APIM_GATEWAY_VERSION:-4.12}"   # an image the Images page knows
        if core:
            core(self.given["raw"][CORE])
        self.built = project.build(self.given)
        self.patch(compose, "includes", lambda path: self.given["includes"].get(path, []))
        self.patch(check, "loaded", lambda: (self.built, self.given))
        for file in self.given["raw"]:   # the file of each unit, and a README next to each that has a folder
            found = paths.unit_of(file)
            if found:
                self.write(file, "")
                if found[0] != "setup":
                    self.write(f"{paths.unit_folder(*found)}/README.md", "# It")
        return self.built

    def context(self, ns="fixture", declared=()):
        return {"catalog": ns, "declared": list(declared), "products": ()}

    def lines(self, kind, uid, shared=True, **context):
        """What is wrong with a unit of the machine."""
        return list(check._unit(kind, uid, self.built, self.given["raw"], shared, context or None))


class Misnamed(Case):

    def test_a_file_the_lab_ui_does_not_see_is_a_problem(self):
        root = self.folder(files=dict.fromkeys((
            "addons/kafka/compose.yml", "addons/My_Addon/compose.yml", "addons/.hidden/compose.yml",
            "addons/AGENTS.md", "addons/empty/README.md",
            "setups/events.yml", "setups/Team_Demo.yml", "setups/.mine.yml", "setups/notes.md",
            "playgrounds/welcome/compose.yml", "playgrounds/_scratch/compose.yml", "playgrounds/Work Shop/compose.yml"), ""))
        self.assertEqual(list(check._misnamed(root)), [(path, MISNAMED) for path in (
            "addons/My_Addon/compose.yml", "setups/Team_Demo.yml", "playgrounds/Work Shop/compose.yml",
            "playgrounds/_scratch/compose.yml")])

    def test_a_catalog_with_none_of_the_folders_has_nothing_to_name(self):
        self.assertEqual(list(check._misnamed(self.folder())), [])

    def test_local_is_the_name_of_this_machine_not_one_to_share(self):
        root = self.folder(files=dict.fromkeys(("addons/local/compose.yml", "setups/local.yml",
                                                "playgrounds/local/compose.yml", "addons/kafka/compose.yml"), ""))
        self.assertEqual(list(check._misnamed(root)), [
            ("addons/local/compose.yml", "a add-on cannot be named local: it would read as one of this machine"),
            ("setups/local.yml", "a setup cannot be named local: it would read as one of this machine"),
            ("playgrounds/local/compose.yml", "a playground cannot be named local: it would read as one of this machine")])


def skill(name, description="Creates an add-on", head=None):
    """A skill file: its frontmatter (`head` in place of it), then its text."""
    return (head if head is not None else f"---\nname: {name}\ndescription: {description}\n---\n") \
        + "\n1. Read addons/AGENTS.md\n"


def stub(name, description="Creates an add-on", body=None):
    """The stub of a skill: the same frontmatter, then what sends Claude Code to it."""
    body = body if body is not None else f"Read `.agents/skills/{name}/SKILL.md` and follow it."
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def skill_file(name):
    return f".agents/skills/{name}/SKILL.md"


def stub_file(name):
    return f".claude/skills/{name}/SKILL.md"


class Skills(Case):

    def problems(self, files):
        self.folder(check, files=files)
        return list(check._skills())

    def test_a_skill_with_its_stub_is_fine(self):
        folded = "---\nname: share-item\ndescription: >\n  Shares a personal item.\n  Use it to move one.\n---\n"
        self.assertEqual(self.problems({
            skill_file("new-addon"): skill("new-addon"), stub_file("new-addon"): stub("new-addon"),
            skill_file("share-item"): skill("share-item", head=folded),
            stub_file("share-item"): stub("share-item", "Shares a personal item. Use it to move one.")}), [])
        self.assertEqual(self.problems({}), [])

    def test_each_skill_has_its_stub_with_the_same_name_and_description(self):
        self.assertEqual(self.problems({
            skill_file("new-addon"): skill("new-addon"), stub_file("new-addon"): stub("new-addon"),
            skill_file("new-setup"): skill("new-setup"),
            stub_file("new-setup"): "Read `.agents/skills/new-setup/SKILL.md` and follow it.\n",
            skill_file("share-item"): skill("share-item"),
            skill_file("new-playground"): skill("new-playground"),
            stub_file("new-playground"): stub("new-playground", "Makes an add-on")}), [
            (skill_file("new-playground"), f"{stub_file('new-playground')} must have the same name and description"),
            (skill_file("new-setup"), f"{stub_file('new-setup')} must have the same name and description"),
            (skill_file("share-item"),
             f"{stub_file('share-item')} is missing: the stub that sends Claude Code to this skill")])

    def test_a_stub_says_only_to_read_its_skill(self):
        for body in ("Read the skill and follow it.", "Read `.agents/skills/new-addon/SKILL.md` and follow it.\nMore."):
            with self.subTest(body=body):
                self.assertEqual(self.problems({skill_file("new-addon"): skill("new-addon"),
                                                stub_file("new-addon"): stub("new-addon", body=body)}), [
                    (stub_file("new-addon"),
                     "after its frontmatter, write only: Read `.agents/skills/new-addon/SKILL.md` and follow it.")])

    def test_a_stub_needs_its_skill(self):
        self.assertEqual(self.problems({stub_file("other"): stub("other")}), [
            (stub_file("other"),
             "sends Claude Code to .agents/skills/other/SKILL.md, which does not exist: remove it")])

    def test_a_skill_says_its_folder_and_what_it_does(self):
        for text, message in (
                (skill("new-addon", head="# No frontmatter\n"),
                 "begin it with a frontmatter between two --- lines, with name: new-addon and a description"),
                (skill("add-on"), "name must be new-addon, the name of its folder, not add-on"),
                (skill("", head="---\ndescription: Creates\n---\n"),
                 "name must be new-addon, the name of its folder, not nothing"),
                (skill("", head="---\nname: new-addon\n---\n"),
                 "description is missing: say what the skill does and when to use it")):
            with self.subTest(message=message):
                found = self.problems({skill_file("new-addon"): text, stub_file("new-addon"): stub("new-addon")})
                self.assertEqual(found[0], (skill_file("new-addon"), message))


class AgentFiles(Case):

    def test_each_agents_file_has_a_claude_file_that_only_imports_it(self):
        self.folder(check, files={
            "AGENTS.md": "", "CLAUDE.md": "@AGENTS.md\n", "lab-ui/AGENTS.md": "", "lab-ui/CLAUDE.md": "@AGENTS.md",
            "docs/AGENTS.md": "", "docs/kafka/AGENTS.md": "", "docs/kafka/CLAUDE.md": "See AGENTS.md\n",
            "templates/acme/AGENTS.md": "", "templates/acme/CLAUDE.md": "@AGENTS.md\n\n",
            # Not files of the project: this machine's, the plugins, hidden folders and dependencies
            "local/mine/AGENTS.md": "", "plugins/apim/AGENTS.md": "", ".lab/AGENTS.md": "",
            "templates/acme/apps/web/node_modules/AGENTS.md": ""})
        self.assertEqual(list(check._agents()), [
            ("docs/AGENTS.md", "docs/CLAUDE.md is missing: create it with the single line @AGENTS.md"),
            ("docs/kafka/CLAUDE.md", "it must hold only the line @AGENTS.md, so that Claude Code reads AGENTS.md")])

    def test_gemini_cli_reads_agents_md(self):
        good = '{"context": {"fileName": ["AGENTS.md"]}, "ui": {"theme": "dark"}}'
        for text, message in (
                (None, 'is missing: create it with {"context": {"fileName": ["AGENTS.md"]}}'),
                ("{", "is not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
                ('{"context": {"fileName": ["AGENTS.md", "GEMINI.md"]}}',
                 'context.fileName must be ["AGENTS.md"], so that Gemini CLI reads AGENTS.md'),
                ('{"context": {}}', 'context.fileName must be ["AGENTS.md"], so that Gemini CLI reads AGENTS.md'),
                ("[]", 'context.fileName must be ["AGENTS.md"], so that Gemini CLI reads AGENTS.md'),
                (good, None)):
            with self.subTest(text=text):
                self.folder(check, files={".gemini/settings.json": text} if text else {})
                self.assertEqual(list(check._gemini()), [(".gemini/settings.json", message)] if message else [])


class Includes(Case):
    """What a unit includes: units of the right kind, that are there, of its own catalog or one it declares."""

    def setUp(self):
        self.folder(check, paths, compose)
        fixtures.default(self, "fixture")

    def found(self, kind, included, shared=True, catalog="fixture", declared=(), name="it"):
        """What is wrong with a file of this kind that includes these, all of them on disk."""
        for path in included:
            self.write(path, "")
        with mock.patch.object(compose, "includes", return_value=included):
            return list(check._includes(kind, unit("fixture", kind, name), shared, catalog, declared))

    def test_a_playground_includes_add_ons_and_setups(self):
        self.assertEqual(self.found("playground", [KAFKA, EVENTS]), [])
        self.assertEqual(self.found("playground", [unit("fixture", "playground", "other")]),
                         ["includes playgrounds/other/compose.yml: only add-ons and setups can be included"])

    def test_what_is_shared_includes_nothing_of_this_machine(self):
        own = [MINE, WHOAMI]
        self.assertEqual(self.found("playground", own, catalog=None),
                         [f"includes {path}: {PERSONAL}" for path in own])
        self.assertEqual(self.found("setup", [WHOAMI], catalog=None), [f"includes {WHOAMI}: {PERSONAL}"])
        # What is of this machine includes what it wants, of the kinds a file may include
        self.assertEqual(self.found("playground", own, shared=False, catalog=None), [])
        self.assertEqual(self.found("addon", own, shared=False, catalog=None),
                         [f"includes {MINE}: only add-ons can be included"])

    def test_an_add_on_and_a_setup_include_add_ons_only(self):
        for kind in ("addon", "setup"):
            self.assertEqual(self.found(kind, [KAFKA, EVENTS]), ["includes setups/events.yml: only add-ons can be included"])

    def test_what_is_included_is_there_and_the_line_says_where_the_unit_would_be(self):
        with mock.patch.object(compose, "includes", return_value=[KAFKA, EVENTS, ".lab/addons/kafka/compose.yml"]):
            self.assertEqual(list(check._includes("playground", unit("fixture", "playground", "it"), True, "fixture")), [
                "includes addons/kafka/compose.yml, which does not exist: from here a unit of this catalog is "
                "../../addons/kafka/compose.yml",
                "includes setups/events.yml, which does not exist: from here a unit of this catalog is ../../setups/events.yml",
                "includes .lab/addons/kafka/compose.yml, which does not exist: from here a unit of this catalog is "
                "../../addons/kafka/compose.yml"])
            self.assertEqual(list(check._includes("setup", unit("fixture", "setup", "it"), True, "fixture"))[0],
                             "includes addons/kafka/compose.yml, which does not exist: from here a unit of this catalog is "
                             "../addons/kafka/compose.yml")
        # Outside a catalog (an item about to be shared) the line is the file as written, and no advice
        with mock.patch.object(compose, "includes", return_value=[KAFKA]):
            self.assertEqual(list(check._includes("setup", "setups/events.yml", True)),
                             [f"includes {KAFKA}, which does not exist"])

    def test_what_comes_from_a_catalog_is_not_shared_into_another(self):
        other = unit("partner", "addon", "ledger")
        said = "what comes from a catalog is not shared into another"
        self.assertEqual(self.found("playground", [other]), [f"includes partner/addons/ledger/compose.yml: {said}"])
        self.assertEqual(self.found("setup", [other], name="two"),
                         [f"includes partner/addons/ledger/compose.yml: {said}"])
        # Declared in catalog.yml: it is theirs to include; the catalog's own is always fine
        self.assertEqual(self.found("playground", [other, KAFKA], declared=("partner",)), [])
        self.assertEqual(self.found("playground", [other], declared=("stranger",)),
                         [f"includes partner/addons/ledger/compose.yml: {said}"])

    def test_a_unit_of_another_catalog_includes_its_own_and_the_default_catalog_only_when_it_says_so(self):
        mine = unit("acme-hub", "addon", "bank")
        self.assertEqual(self.found("playground", [mine], catalog="acme-hub"), [])
        self.assertEqual(self.found("playground", [KAFKA], catalog="acme-hub"),
                         ["includes fixture/addons/kafka/compose.yml: what comes from a catalog is not shared into another"])
        self.assertEqual(self.found("playground", [KAFKA], catalog="acme-hub", declared=("fixture",)), [])


class Items(Machine):
    """The rules of one item that the project hands over."""

    def test_each_shared_service_says_what_it_is(self):
        config = {"services": {"kafka": {"labels": {"io.gravitee.description": "A broker"}}, "kafka-ui": {"labels": {}},
                               "kafka-client": {}}}
        self.assertEqual(list(check._labeled(config, ["kafka", "kafka-ui", "kafka-client"])), [
            "kafka-ui: the label io.gravitee.description is missing",
            "kafka-client: the label io.gravitee.description is missing"])

    def test_a_playground_brings_what_its_setups_bring(self):
        built = self.machine([
            member("fixture", "addon", "jade", ["jade"], says={"conflicts": {"agent-lens": "both take the exporter"}}),
            member("fixture", "addon", "agent-lens", ["agent-lens"]),
            member("fixture", "setup", "tracing", include=[("fixture", "addon", "jade")]),
            member("fixture", "playground", "welcome", ["acme-website"],
                   include=[("fixture", "setup", "tracing"), ("fixture", "addon", "agent-lens")])])
        found = list(check._together(built, check._brings(built["playgrounds"]["welcome"])))
        self.assertEqual(found, ["brings agent-lens and jade, which cannot run together: both take the exporter"])

    def test_x_gravitee_that_is_not_a_mapping_is_reported_not_raised(self):
        built = self.machine([member("fixture", "addon", "kafka", ["kafka"])])
        self.given["raw"][KAFKA]["x-gravitee"] = "kafka"
        built = project.build(self.given)
        self.built = built
        self.assertEqual(self.lines("addon", "kafka"), [
            "x-gravitee.kind must be addon", "x-gravitee.name is missing", "x-gravitee.description is missing",
            "x-gravitee.category is missing"])
        self.assertIn({"file": KAFKA, "message": "x-gravitee must be a mapping", "display": "addons/kafka/compose.yml"},
                      built["catalog_problems"]["fixture"])
        self.assertEqual(built["problems"], [], "a catalog's file is no problem of the banner")

    def test_a_key_that_x_gravitee_does_not_know_is_said_not_ignored(self):
        # A key of another version, or a typo, does nothing: the person is told, whatever the key was called
        meta = {"kind": "playground", "name": "Welcome", "description": "x", "consoles": [], "start": "http://localhost:8110", "console": []}
        known = ", ".join(sorted(check.META))
        self.assertEqual(list(check._metadata("playground", meta, [], False)), [
            f"x-gravitee.console is not a key of x-gravitee: {known}",
            f"x-gravitee.start is not a key of x-gravitee: {known}"])
        # Every key the project reads is known: the files of the project say nothing more
        every = {key: None for key in check.META if check.KEY_OF.get(key, "playground") == "playground"}
        self.assertEqual(list(check._metadata("playground", every | {"kind": "playground", "name": "x",
                                                                      "description": "x"}, [], False)), [])

    def test_a_key_that_only_one_kind_reads_is_said_of_the_others(self):
        # A gateway is an add-on's
        for kind in ("addon", "setup", "playground"):
            meta = {"kind": kind, "name": "x", "description": "x", "category": "AI", "gateway": {}}
            with self.subTest(kind=kind):
                self.assertEqual(list(check._metadata(kind, meta, ["AI"], False)),
                                 [] if kind == "addon" else ["x-gravitee.gateway is a key of add-ons only"])

    def test_where_a_playground_can_be_deployed_is_computed_so_a_file_that_declares_it_is_told(self):
        said = "x-gravitee.deploys_to is no longer a key: compatibility with a platform is computed"
        known = ", ".join(sorted(check.META))
        self.assertNotIn("deploys_to", check.META)
        for kind in ("addon", "setup", "playground"):
            meta = {"kind": kind, "name": "x", "description": "x", "category": "AI", "deploys_to": ["cloud"]}
            with self.subTest(kind=kind):
                self.assertEqual(list(check._metadata(kind, meta, ["AI"], False)), [said])
        # Whatever it holds, and once: it is no more a key than a typo is, and says why
        meta = {"kind": "playground", "name": "x", "description": "x", "deploys_to": None, "deploy": "cloud"}
        self.assertEqual(list(check._metadata("playground", meta, [], False)),
                         [f"x-gravitee.deploy is not a key of x-gravitee: {known}", said])

    def test_what_is_shared_has_a_category_like_an_add_on(self):
        for kind in ("setup", "playground"):
            meta = {"kind": kind, "name": "x", "description": "x"}
            with self.subTest(kind=kind):
                self.assertEqual(list(check._metadata(kind, meta, ["AI"], False)), [])
                self.assertEqual(list(check._metadata(kind, meta, ["AI"], True)), ["x-gravitee.category is missing"])
                self.assertEqual(list(check._metadata(kind, {**meta, "category": "AI"}, ["AI"], True)), [])
                self.assertEqual(list(check._metadata(kind, {**meta, "category": "Mine"}, ["AI"], True)),
                                 ["x-gravitee.category must be one of: AI"])
        meta = {"kind": "addon", "name": "x", "description": "x"}   # an add-on's, whether it is shared or not
        self.assertEqual(list(check._metadata("addon", meta, ["AI"], False)), ["x-gravitee.category is missing"])

    def test_a_port_with_a_variable_says_how_to_write_it_on_127_0_0_1(self):
        ports = [compose._port(written) for written in (
            "${UI_PORT:-8085}:8080", "127.0.0.1:${UI_PORT:-8086}:8080", "${BIND:-127.0.0.1}:8087:8080",
            "[::1]:8088:8080", "8089:8080/udp", "8090")]
        self.assertEqual(list(check._loopback(ports)), [
            "publishes port ${UI_PORT:-8085} on every interface: write 127.0.0.1:${UI_PORT:-8085}:8080",
            "publishes port 8087 on ${BIND:-127.0.0.1}: write 127.0.0.1:8087:8080",
            "publishes port 8088 on ::1: write 127.0.0.1:8088:8080",
            "publishes port 8089 on every interface: write 127.0.0.1:8089:8080",
            "publishes a port for 8090 on every interface: write 127.0.0.1::8090"])

    def test_a_shared_playground_says_what_a_person_reads_before_starting_it(self):
        good = {"tags": ["API", "Gateway"], "duration": "35 min", "first_start": "1 h 30 min"}
        self.assertEqual(list(check._listing(good)), [])
        for tags, said in ((None, "is missing"), ([], "is missing"), ([""], "is missing"), ("API", "is missing"),
                           (["API", "API"], "lists a tag twice")):
            with self.subTest(tags=tags):
                self.assertEqual([line.split(" ", 1)[0] + " " + line.split(" ", 1)[1].split(":")[0] for line in check._listing({**good, "tags": tags})],
                                 [f"x-gravitee.tags {said}"])
        self.assertEqual(list(check._listing({**good, "duration": None, "first_start": None})), [
            "x-gravitee.duration is missing: how long its guide takes, as 20 min or 1 h 30 min",
            "x-gravitee.first_start is missing: how long its first start takes, as 20 min or 1 h 30 min"])
        for written in ("a while", "35", "35 minutes", "1h", "1 h 5", "1,5 h"):
            with self.subTest(duration=written):
                self.assertEqual(list(check._listing({**good, "duration": written})),
                                 [f"x-gravitee.duration is {written}: write it as 20 min, 1 h or 1 h 30 min"])

    def test_what_is_shared_has_a_readme_next_to_its_file(self):
        self.write(".lab/catalogs/fixture/addons/kafka/README.md", "# Kafka")
        self.write(".lab/catalogs/fixture/addons/jade/compose.yml", "")
        self.assertEqual(list(check._readme("addon", "kafka")), [])
        self.assertEqual(list(check._readme("addon", "jade")),
                         ["README.md is missing next to it: what it adds and how to use it"])

    def test_a_conflict_names_an_add_on_and_says_why(self):
        known = ["jade", "kafka", "@acme-hub/ledger", "@acme-hub/kafka"]
        with mock.patch.object(paths, "ids", return_value=known):
            found = list(check._conflicts({"catalog": "fixture", "conflicts": {
                "jade": "", "kafka": "same ports", "nothing": "why", "@acme-hub/ledger": "both bind it"}}))
            self.assertEqual(found, ["conflicts with jade: say why", "conflicts with nothing, which is not an add-on"])
            # A bare id is the add-on of the same catalog if there is one, else the default catalog's
            theirs = {"catalog": "acme-hub", "conflicts": {"kafka": "x", "jade": "", "ledger": "x", "nothing": "x"}}
            self.assertEqual(list(check._conflicts(theirs)), [
                "conflicts with jade: say why", "conflicts with nothing, which is not an add-on"])
            self.assertEqual(check._conflict_id("kafka", "acme-hub", set(known)), "@acme-hub/kafka")
            self.assertEqual(check._conflict_id("jade", "acme-hub", set(known)), "jade")
            self.assertEqual(check._conflict_id("@fixture/kafka", "acme-hub", set(known)), "kafka", "qualified is that catalog's")
            self.assertEqual(check._conflict_id("@acme-hub/kafka", "fixture", set(known)), "@acme-hub/kafka")
            self.assertIsNone(check._conflict_id("ledger", "fixture", set(known)))

    def test_two_add_ons_of_a_catalog_that_is_not_the_first_conflict_by_the_bare_id_they_name(self):
        # `conflicts: {echo: why}` in a unit of @acme-hub names @acme-hub/echo: the ports they share are no clash, and a playground
        # that brings both is told, in the words of the author
        twin = member("acme-hub", "addon", "twin", ["twin"], says={"conflicts": {"echo": "both take 8191"}})
        echo = member("acme-hub", "addon", "echo", ["echo"])
        for one in (twin, echo):
            one[1]["services"][one[1]["x-gravitee"]["name"].lower()]["ports"] = [port(8191)]
        play = member("acme-hub", "playground", "play", ["play-app"], include=[("acme-hub", "addon", "twin"), ("acme-hub", "addon", "echo")])
        built = self.machine([twin, echo, play], catalogs={"acme-hub": {"name": "Acme Hub"}})
        self.assertEqual(list(check._ports(built, self.given["raw"])), [])
        self.assertEqual(list(check._together(built, check._brings(built["playgrounds"]["@acme-hub/play"]))), [
            "brings @acme-hub/echo and @acme-hub/twin, which cannot run together: both take 8191"])
        self.assertEqual(list(check._conflicts(built["addons"]["@acme-hub/twin"])), [], "and the line says what the author wrote")
        built["addons"]["@acme-hub/twin"]["conflicts"] = {"echoes": ""}
        self.assertEqual(list(check._conflicts(built["addons"]["@acme-hub/twin"])), [
            "conflicts with echoes, which is not an add-on"])

    def test_the_license_a_unit_requires_is_the_ids_of_its_features(self):
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        self.assertEqual(list(check._requires({"license": ["apim-reporter-tcp", "Agent management"]}, built)),
                         ["x-gravitee.requires.license: Agent management is not the id of a license feature"])
        # In the mapping form, each says what needs it
        requires = {"license": {"am-authorizationengine-openfga": "Step 4", "am-policy-authzen": None,
                                "apim-reporter-tcp": " "}}
        self.assertEqual(list(check._requires(requires, built)), [
            "x-gravitee.requires.license: am-policy-authzen: say what needs it",
            "x-gravitee.requires.license: apim-reporter-tcp: say what needs it"])
        self.assertEqual(list(check._license({"license": {"apim-reporter-tcp": "Traces"}})), [])

    LEVEL = "x-gravitee.requires.lab must be a whole number from 1: older Gravitee Labs read it, and an item does without it"

    def test_the_level_a_unit_was_written_for_is_a_whole_number_from_1(self):
        for level in (1, 3, 99):
            self.assertEqual(list(check._level({"lab": level})), [])
        for level in (0, -1, "1", 1.5, True, None, [1]):
            with self.subTest(level=level):
                self.assertEqual(list(check._level({"lab": level})), [self.LEVEL])
        for requires in (None, {}, {"plugins": {}}, "lab", ["lab"]):
            self.assertEqual(list(check._level(requires)), [], requires)   # no level said, or requires written otherwise
        self.assertIn("lab", check.REQUIRES)
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        self.assertEqual(list(check._requires({"lab": "x"}, built)), [], "said once, for every kind, by the item")

    def test_every_kind_of_item_is_told_a_level_that_is_none(self):
        # The Lab reads `requires.lab` of an add-on and of a setup as it reads a playground's: a bad one is ignored there
        for level in ("x", 1.5, True, 0, -3, [1]):
            with self.subTest(level=level):
                self.machine([
                    member("fixture", "addon", "kafka", ["kafka"], says={"requires": {"lab": level}}),
                    member("fixture", "setup", "tracing", include=[("fixture", "addon", "kafka")], says={"requires": {"lab": level}}),
                    member("fixture", "playground", "welcome", ["acme-website"], says={"requires": {"lab": level}})])
                for kind, uid in (("addon", "kafka"), ("setup", "tracing"), ("playground", "welcome")):
                    self.assertEqual(self.lines(kind, uid).count(self.LEVEL), 1, f"{kind} {level!r}")
        self.machine([member("fixture", "addon", "kafka", ["kafka"], says={"requires": {"lab": 1}}),
                      member("fixture", "setup", "tracing", include=[("fixture", "addon", "kafka")], says={"requires": "no"})])
        self.assertNotIn(self.LEVEL, self.lines("addon", "kafka"))
        self.assertNotIn(self.LEVEL, self.lines("setup", "tracing"))

    def test_what_a_playground_requires_is_what_its_checks_and_its_start_act_on(self):
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        requires = {"plugins": {"apim": ["gravitee-policy-x"], "apimm": ["gravitee-policy-y"]},
                    "host": [{"name": "Ollama", "check": "https://localhost:11434/api/tags"},
                             {"name": "LLM", "check": "localhost:8000"}],
                    "addons": ["kafka"], "images": {"APIM_GATEWAY_VERSION": "4.13", "APIM_VERSIONS": "4.13"}}
        built["products"] = {"apim": {}}
        self.assertEqual(list(check._requires(requires, built)), [
            "x-gravitee.requires: unknown addons (add-ons and setups are included)",
            "x-gravitee.requires.plugins: apimm is not a product that loads plugins",
            "x-gravitee.requires.images: APIM_VERSIONS is not the version variable of an image",
            "x-gravitee.requires.host: the check of LLM must be an http or https URL"])
        # Without the core, no product is known: its problem is the one to fix
        self.assertEqual(list(check._requires({"plugins": {"apimm": []}}, {**built, "core_loaded": False})), [])

    def test_the_versions_a_playground_was_tested_with_are_version_variables_of_images(self):
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        self.assertEqual(list(check._tested([{"APIM_GATEWAY_VERSION": "4.13.0-alpha.3"}], built)), [])
        self.assertEqual(list(check._tested([{"APIM_GATEWAY_VERSION": "4.13.0-alpha.3", "APIM_GATEWAY_REGISTRY": "x"}],
                                            built)), [])
        self.assertEqual(list(check._tested([{"APIM_GATEWAY_VERSION": "4.13.0-alpha.3", "NOPE_VERSION": "1"}], built)),
                         ["x-gravitee.tested: NOPE_VERSION is not the version or the registry variable of an image"])

    def test_the_action_links_of_a_guide_name_an_id_of_lowercase_letters_digits_and_hyphens(self):
        good = "## 1. One\n\nClick [Ask Ace](action:ask-ace-accounts), then [Leak](action:leak-an-iban) and [Ask Ace](action:ask-ace-accounts).\n"
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": good}))), [])
        bad = "## 1. One\n\n[A](action:Ask-Ace) [B](action:ask_ace) [C](action:) [D](action:-a) [E](action:ask ace) [F](https://x.test/action:Z)\n"
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": bad}))), [
            "guide.md: action:Ask-Ace is no action, an id is lowercase letters, digits and hyphens",
            "guide.md: action:ask_ace is no action, an id is lowercase letters, digits and hyphens",
            "guide.md: action: is no action, an id is lowercase letters, digits and hyphens",
            "guide.md: action:-a is no action, an id is lowercase letters, digits and hyphens",
            "guide.md: action:ask ace is no action, an id is lowercase letters, digits and hyphens"])
        # an id has at most 64 characters, as the page and the apps read it
        long = f"[A](action:{'a' * 64}) [B](action:{'a' * 65})\n"
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": long}))),
                         [f"guide.md: action:{'a' * 65} is no action, an id is lowercase letters, digits and hyphens"])

    def test_an_action_link_may_have_a_title_and_a_guide_may_show_the_syntax_in_code(self):
        titled = '## 1. One\n\n[A](action:ask-ace "Ask Ace") and [B](action:Bad "x")\n'
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": titled}))),
                         ["guide.md: action:Bad is no action, an id is lowercase letters, digits and hyphens"])
        shown = "## 1. One\n\nWrite `[Label](action:Not An Id)` and:\n\n```md\n[Label](action:Not_An_Id)\n```\n\n~~~\n[x](action:Y)\n~~~\n"
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": shown}))), [])

    def test_the_account_links_of_a_guide_name_a_user_of_its_accounts(self):
        accounts = [{"for": "Portal", "username": "maya.chen", "password": "${PW:-x}"}, {"for": "Key", "key": "${K:-k}"}]
        text = ("## 1. One\n\nSign in as [Maya](account:maya.chen) or [Bob](account:bob \"Bob\"), not `[x](account:shown)`.\n\n"
                "```md\n[y](account:also-shown)\n```\n")
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": text}), accounts)),
                         ["guide.md: account:bob is no user of x-gravitee.accounts, so the page cannot show its sign-in"])
        # A playground without accounts has no sign-in to show
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": "[Maya](account:maya.chen)\n"}))),
                         ["guide.md: account:maya.chen is no user of x-gravitee.accounts, so the page cannot show its sign-in"])

    def test_the_steps_of_a_guide_are_numbered_in_order(self):
        # A page per step, at the address of its number: the details link to the third step as /3
        good = "# Lab\n\n## 1. One\n\n## 2. Two\n\n## What to look at\n"
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": good}))), [])
        self.assertEqual(list(check._guide(self.folder(files={"guide.md": "## 1. One\n## 3. Three\n"}))),
                         ["guide.md: number its steps 1, 2, 3 in order, not 1, 3"])

    def test_the_apis_a_seed_imports_are_json_objects(self):
        root = self.folder(files={"seed/apim/apis/one.json": '{"api": {"name": "One"}}',
                                  "seed/apim/apis/two.json": "not json", "seed/apim/apis/three.json": "[]"})
        self.assertEqual(list(check._seed(root)), [
            "seed/apim/apis/three.json must hold a JSON object",
            "seed/apim/apis/two.json is not valid JSON: Expecting value: line 1 column 1 (char 0)"])

    def test_the_apis_of_the_environments_a_seed_adds_are_json_objects_too(self):
        root = self.folder(files={
            "seed/apim/apis/one.json": '{"api": {"name": "One"}}',
            "seed/environments/beam/environment.yaml": "id: beam\nname: Beam\n",
            "seed/environments/beam/apis/sample-requests.json": '{"api": {"name": "Sample requests"}}',
            "seed/environments/beam/apis/broken.json": "{",
            "seed/environments/moderna/environment.yaml": "id: moderna\nname: Moderna\n",
            "seed/environments/moderna/apis/list.json": "[]",
            "seed/environments/moderna/portal/apis/not-an-export.json": "not json"})
        self.assertEqual(list(check._seed(root)), [
            "seed/environments/beam/apis/broken.json is not valid JSON: "
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            "seed/environments/moderna/apis/list.json must hold a JSON object"])

    def test_an_environment_folder_is_named_like_an_id_and_holds_its_environment_file(self):
        good = "id: {}\nname: Fine\n"
        root = self.folder(files={
            "seed/environments/moderna/environment.yaml": good.format("moderna"),
            "seed/environments/Novo_Nordisk/environment.yaml": good.format("Novo_Nordisk"),
            "seed/environments/local/environment.yaml": good.format("local"),
            "seed/environments/default/environment.yaml": good.format("default"),
            "seed/environments/defaults/environment.yaml": good.format("defaults"),
            "seed/environments/beam/apis/one.json": "{}",
            "seed/environments/.hidden/environment.yaml": "",
            "seed/environments/README.md": "files are no environment"})
        name = ("an environment is named by an id, lowercase letters, digits and dashes, and not `local` or `default`")
        self.assertEqual(list(check._environments(root)), [
            f"seed/environments/Novo_Nordisk: {name}",
            "seed/environments/beam/environment.yaml is missing",
            f"seed/environments/default: {name}",
            f"seed/environments/local: {name}"])

    def test_the_id_of_an_environment_is_its_folder_and_it_has_a_name(self):
        root = self.folder(files={
            "seed/environments/beam/environment.yaml": "id: beam-therapeutics\nname: Beam Therapeutics\n",
            "seed/environments/kyverna/environment.yaml": "id: kyverna\ndescription: No name\n",
            "seed/environments/moderna/environment.yaml": "name: Moderna\n",
            "seed/environments/pitt/environment.yaml": "",
            "seed/environments/boston/environment.yaml":
                "id: 'boston'   # the folder\nname: \"Boston Children's Hospital\"  # what the Console lists\n",
            "seed/environments/novo/environment.yaml": "id: novo\nname: Novo's own\n"})
        self.assertEqual(list(check._environments(root)), [
            "seed/environments/beam/environment.yaml: id must be beam, the name of its folder",
            "seed/environments/kyverna/environment.yaml: name is missing",
            "seed/environments/moderna/environment.yaml: id must be moderna, the name of its folder",
            "seed/environments/pitt/environment.yaml: id must be pitt, the name of its folder",
            "seed/environments/pitt/environment.yaml: name is missing"])
        self.assertEqual(list(check._environments(self.folder())), [], "a seed without environments has none to check")

    def test_the_environments_of_a_seed_are_among_what_the_seed_is_checked_on(self):
        root = self.folder(files={"seed/environments/beam/apis/one.json": "{}"})
        self.assertEqual(list(check._seed(root)), ["seed/environments/beam/environment.yaml is missing"])

    def test_a_plain_yaml_file_is_read_by_its_top_level_lines(self):
        text = "# an environment\nid: beam   # its folder\nname: 'Beam: Therapeutics'\nempty:\nnested:\n  id: not-this\n- id: nor-this\n"
        self.assertEqual(check._flat(text), {"id": "beam", "name": "Beam: Therapeutics", "empty": "", "nested": ""})
        self.assertEqual(check._flat("id: x\r\nname: Y\r\n"), {"id": "x", "name": "Y"})

    def test_what_an_add_on_or_a_playground_builds_says_how_it_comes(self):
        # Built here or pulled, as its pull policy says: the Lab UI never forces a build
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        service_ = built["services"]["acme-website"]
        service_.update(built=True, pull_policy=None)
        self.assertEqual(list(check._pull_policy(built["playgrounds"]["welcome"], built)), [
            "acme-website builds its image: say how it comes with pull_policy (build or never, or always to pull it)"])
        service_["pull_policy"] = "build"
        self.assertEqual(list(check._pull_policy(built["playgrounds"]["welcome"], built)), [])

    def test_a_catalog_that_was_audited_leaves_the_pull_policy_to_im01(self):
        said = "acme-website builds its image: say how it comes with pull_policy (build or never, or always to pull it)"
        built = self.machine([member("fixture", "playground", "welcome", ["acme-website"])])
        built["services"]["acme-website"].update(built=True, pull_policy=None)
        self.assertIn(said, self.lines("playground", "welcome", catalog="fixture"))
        self.assertNotIn(said, self.lines("playground", "welcome", catalog="fixture", audited=True))


PORTAL_COMMAND = ["/bin/sh", "-c", "sed -i -e '/X-Frame-Options/d' -e \"s|self|& {pages}|\" x; sh /run.sh"]


class Playgrounds(Machine):
    """The UIs, the terminal and the seed of a playground, on a machine whose platform has a portal and whose playground
    brings an add-on."""

    def playground(self, meta=None, inspector_console=False, command=None, environment=None, **more):
        """What is wrong with the playground `welcome` once its x-gravitee says `meta` too."""
        console = {"io.gravitee.console.inspector.name": "Inspector", "io.gravitee.console.inspector.url": "http://localhost:9002"}
        website = {"io.gravitee.console.website.name": "Website", "io.gravitee.console.website.url": "http://localhost:8000"}
        portal = {"io.gravitee.group": "API Management", "io.gravitee.console.portal.name": "Portal",
                  "io.gravitee.console.portal.url": "http://localhost:8085"}
        inspector = member("fixture", "addon", "agent-lens", ["agent-lens"])
        welcome = member("fixture", "playground", "welcome", ["acme-website", "hello-agent", "hello-seed"],
                         include=[("fixture", "addon", "agent-lens")], says=meta or {})
        welcome[1]["services"]["acme-website"]["labels"].update(website)
        welcome[1]["services"]["hello-seed"]["labels"]["io.gravitee.role"] = "seed"
        if inspector_console:
            inspector[1]["services"]["agent-lens"]["labels"].update(console)

        def platform(core):
            core["services"]["apim-portal-ui"] = service("graviteeio/apim-portal-ui:4.12", "Portal", labels=portal,
                                                         command=command or [], environment=environment or {})
            core["x-gravitee"]["groups"] = ["API Management"]
        self.machine([inspector, welcome], core=platform, guides={"welcome": "## 1. One\n"}, **more)
        self.patch(check, "_guide", return_value=[])
        self.patch(check, "_seed", return_value=[])
        return list(check._playground(self.built["playgrounds"]["welcome"], self.built))

    def test_its_consoles_are_services_it_runs_with_a_console_each_listed_once(self):
        # One list names every UI next to its guide: its own app, an add-on's, the platform's
        self.assertEqual(self.playground(), [])
        self.assertEqual(self.playground(meta={"consoles": []}), [])
        self.assertEqual(self.playground(meta={"consoles": ["acme-website"]}), [])
        for consoles in ("acme-website", ["acme-website", 8002], {"acme-website": 1}):
            with self.subTest(consoles=consoles):
                self.assertEqual(self.playground(meta={"consoles": consoles}), ["x-gravitee.consoles must be a list of services"])
        self.assertEqual(self.playground(meta={"consoles": ["acme-website", "acme-website"]}),
                         ["x-gravitee.consoles: acme-website is listed twice"])
        # A service with no console, one that does not exist, one that this playground does not run
        nothing = "is not a service with a console that this playground runs"
        for name in ("hello-agent", "nothing", "agent-lens"):
            with self.subTest(name=name):
                self.assertEqual(self.playground(meta={"consoles": [name]}), [f"x-gravitee.consoles: {name} {nothing}"])

    def test_its_terminal_is_a_service_it_runs_that_is_not_its_seed(self):
        self.assertEqual(self.playground(meta={"terminal": "acme-website"}), [])
        self.assertEqual(self.playground(), [])   # one is not required
        wrong = "x-gravitee.terminal must name a service that this playground runs"
        for name in ("nothing", "apim-portal-ui", 8000, ["acme-website"]):   # unknown, the core's, not a name
            with self.subTest(name=name):
                self.assertEqual(self.playground(meta={"terminal": name}), [wrong])
        self.assertEqual(self.playground(meta={"terminal": "hello-seed"}),
                         ["x-gravitee.terminal cannot be the seed, which ends once it ran"])

    def test_a_console_of_an_add_on_it_brings_is_one_of_its_consoles(self):
        shown = {"consoles": ["acme-website", "agent-lens"]}
        self.assertEqual(self.playground(meta=shown, inspector_console=True), [])
        # Without a console of its own the add-on's service is not one
        self.assertEqual(self.playground(meta=shown), [
            "x-gravitee.consoles: agent-lens is not a service with a console that this playground runs"])

    def test_a_console_shown_in_a_frame_lets_the_lab_ui_frame_it(self):
        pages = " ".join(frames.PAGES)
        framed = [part.replace("{pages}", pages) for part in PORTAL_COMMAND]
        message = ("x-gravitee.consoles: apim-portal-ui is shown in a frame, so its command must name "
                   f"{' and '.join(frames.PAGES)} and remove X-Frame-Options (x-ui-frames in docker-compose.yml), "
                   "or a setting of it must name them as the pages that may frame it")
        shown = {"consoles": ["acme-website", "apim-portal-ui"]}
        self.assertEqual(self.playground(meta=shown, command=framed), [])
        self.assertEqual(self.playground(meta=shown, command=" ".join(framed)), [])   # Compose may print it as one text
        # No command; one that leaves a page out; one that leaves the X-Frame-Options line, which refuses every page
        for command in (None, ["nginx"], ["sh", "-c", f"s|self|& {frames.PAGES[0]}|; /X-Frame-Options/d"],
                        ["sh", "-c", f"s|self|& {pages}|"]):
            with self.subTest(command=command):
                self.assertEqual(self.playground(meta=shown, command=command), [message])
        # A setting of its own that names the pages is enough: the portal is configured, not patched
        for pages, expected in ((" ".join(frames.PAGES), []), (frames.PAGES[0], [message])):
            with self.subTest(setting=pages):
                self.assertEqual(self.playground(meta=shown, environment={"ALLOWED_FRAME_ANCESTOR_URLS": "http://localhost:8084 " + pages}), expected)
        # Only the consoles listed are asked, and only the platform's: an app answers for its own headers
        self.assertEqual(self.playground(meta={"consoles": []}), [])
        self.assertEqual(self.playground(meta={"consoles": ["acme-website"]}, command=None), [])

    def test_a_playground_that_cannot_start_asks_nothing_of_services_it_does_not_have(self):
        self.playground()
        self.built["playgrounds"]["welcome"].update(blocked={"reasons": ["Runs with full privileges"]}, services=[], seed=None)
        found = list(check._playground(self.built["playgrounds"]["welcome"], self.built, inert=True))
        self.assertEqual(found, [])


class Hosts(Machine):
    """What a service takes of this machine beyond its container, and the reason it gives."""

    def setUp(self):
        super().setUp()
        self.unit_file = unit("fixture", "addon", "k")
        self.folder_ = self.root / "/".join(self.unit_file.split("/")[:-1])
        self.folder_.mkdir(parents=True)
        self.raw = {}

    def found(self, meta, services, own=None, included=(), others=None, file=None):
        """What `_host_access` says of the add-on `k` whose services are written like this."""
        file = file or self.unit_file
        self.raw = {file: {"services": services}, **{path: {"services": theirs} for path, theirs in (others or {}).items()}}
        return list(check._host_access(meta, self.raw, file, list(included), list(services if own is None else own)))

    def bind(self, source, target="/t", **more):
        return {"type": "bind", "source": str(source), "target": target, **more}

    def test_what_a_service_takes_of_this_machine_is_said_in_words(self):
        roots = check._roots(self.unit_file)
        inside, here = self.bind(self.folder_ / "conf.yml"), str(self.folder_)
        self.assertEqual(check._elevations({"image": "x", "ports": ["127.0.0.1:80:80"], "tmpfs": ["/run"],
                                            "volumes": [inside, {"type": "volume", "source": "data", "target": "/d"}]},
                                           roots), [], "a folder of the unit, a named volume and a port are nothing")
        self.assertEqual(check._elevations({"privileged": True, "network_mode": "host", "pid": "host",
                                            "cap_add": ["SYS_ADMIN", "NET_ADMIN"], "devices": ["/dev/kvm"]}, roots),
                         ["runs privileged", "shares the network of this machine", "shares the processes of this machine",
                          "adds the capabilities SYS_ADMIN, NET_ADMIN", "reaches the devices of this machine"])
        found = check._elevations({"volumes": [self.bind("/var/run/docker.sock", "/s"), self.bind("/etc/hosts", "/h"),
                                               self.bind("${HOME}/.kube", "/k"), self.bind(f"{here}-other/x", "/o")]}, roots)
        self.assertEqual(found[0], "mounts the Docker socket")
        self.assertEqual(len(found), 4)
        self.assertIn(f"mounts {os.path.realpath('/etc/hosts')} of this machine", found)
        self.assertIn("mounts ${HOME}/.kube of this machine", found)

    def test_a_service_that_takes_more_of_this_machine_says_why_in_the_file_that_asks_for_it(self):
        path = self.unit_file
        user = unit("fixture", "addon", "user")
        k3s = {"k3s": {"privileged": True}, "web": {}}
        ask = "k3s: runs privileged: say why in x-gravitee.host_access (k3s: <reason>), so that it is read before it runs"
        self.assertEqual(self.found({}, k3s), [ask])
        self.assertEqual(self.found({"host_access": {"k3s": "Runs a kubelet"}}, k3s), [])
        # A declaration stays true: for what takes nothing, and for what is not a service of the file
        self.assertEqual(self.found({"host_access": {"k3s": "Runs a kubelet", "web": "Why"}}, k3s),
                         ["x-gravitee.host_access: web takes nothing of this machine beyond its container: remove it"])
        self.assertEqual(self.found({"host_access": {"k3s": "Runs a kubelet", "ghost": "Why"}}, k3s),
                         ["x-gravitee.host_access: ghost is not a service of this file"])
        # What an included file asks is its own: only what the file adds is said
        self.assertEqual(self.found({}, {**k3s, "own": {"network_mode": "host"}}, ("k3s", "web", "own"), [path], {path: k3s}, user),
                         ["own: shares the network of this machine: say why in x-gravitee.host_access (own: <reason>), "
                          "so that it is read before it runs"])
        for bad in ({"host_access": ["k3s"]}, {"host_access": {"k3s": ""}}, {"host_access": {"k3s": 1}}, {"host_access": {1: "x"}}):
            with self.subTest(bad=bad):
                self.assertEqual(self.found(bad, k3s),
                                 ["x-gravitee.host_access must map a service to the reason it takes more of this machine"])

    def test_a_bind_inside_the_folder_of_the_unit_asks_nothing_and_one_outside_it_asks_for_a_reason(self):
        inside = {"s": {"volumes": [self.bind(self.folder_ / "data", "/d")]}}
        self.assertEqual(self.found({}, inside), [])
        self.assertEqual(self.found({"host_access": {"s": "why"}}, inside),
                         ["x-gravitee.host_access: s takes nothing of this machine beyond its container: remove it"])
        outside = {"s": {"volumes": [self.bind(self.root / "docker-compose.yml", "/d")]}}
        found = self.found({}, outside)
        self.assertEqual(len(found), 1)
        self.assertIn("s: mounts ", found[0])
        self.assertIn("say why in x-gravitee.host_access (s: <reason>)", found[0])
        self.assertEqual(self.found({"host_access": {"s": "why"}}, outside), [])

    def test_a_bind_is_compared_by_its_real_place_and_so_is_the_project(self):
        # The project is reached through a link, and Compose names the files by either spelling: neither is outside
        real = Path(tempfile.mkdtemp(prefix="gravitee-lab-real-")).resolve()
        self.addCleanup(shutil.rmtree, real, True)
        reached = real.parent / f"{real.name}-link"
        reached.symlink_to(real)
        self.addCleanup(reached.unlink)
        (real / "docker-compose.yml").write_text("", encoding="utf-8")
        self.patch(check, "ROOT", reached)
        self.patch(paths, "ROOT", reached)
        folder = real / ".lab/catalogs/fixture/addons/k"
        folder.mkdir(parents=True)
        for spelling in (real, reached):
            with self.subTest(spelling=str(spelling)):
                services = {"s": {"volumes": [self.bind(spelling / ".lab/catalogs/fixture/addons/k/data", "/d")]}}
                self.assertEqual(self.found({}, services), [])
        # A link in the unit's folder that leads out of it is outside, wherever the bind says it is
        (folder / "elsewhere").symlink_to("/etc")
        services = {"s": {"volumes": [self.bind(reached / ".lab/catalogs/fixture/addons/k/elsewhere", "/d")]}}
        found = self.found({}, services)
        self.assertEqual(len(found), 1)
        self.assertIn(f"s: mounts {os.path.realpath('/etc')} of this machine", found[0])

    def test_a_bind_whose_source_is_a_variable_with_no_default_is_outside_and_its_declaration_is_never_unneeded(self):
        here = str(self.folder_)
        for source in (f"{here}/${{KEY_FILE}}", "${KEY_FILE}", f"{here}/${{KEY_FILE:-}}", f"{here}/${{KEY_FILE:-${{OTHER}}}}"):
            with self.subTest(source=source):
                services = {"s": {"volumes": [self.bind(source, "/key")]}}
                self.assertEqual(self.found({"host_access": {"s": "Reads the key"}}, services), [])
                said = self.found({}, services)
                self.assertEqual(len(said), 1)
                self.assertIn("s: mounts ${KEY_FILE", said[0])
                self.assertNotIn(here, said[0])

    def test_a_variable_with_no_default_is_outside_in_either_spelling_of_a_project_reached_through_a_link(self):
        # Compose puts the folder of the file in front of a variable it did not interpolate, in the spelling it was started with
        real = Path(tempfile.mkdtemp(prefix="gravitee-lab-real-")).resolve()
        self.addCleanup(shutil.rmtree, real, True)
        reached = real.parent / f"{real.name}-link"
        reached.symlink_to(real)
        self.addCleanup(reached.unlink)
        (real / ".lab/catalogs/fixture/addons/k").mkdir(parents=True)
        self.patch(check, "ROOT", reached)
        self.patch(paths, "ROOT", reached)
        for spelling in (real, reached):
            with self.subTest(spelling=str(spelling)):
                services = {"s": {"volumes": [self.bind(f"{spelling}/.lab/catalogs/fixture/addons/k/${{KEY_FILE}}", "/key")]}}
                self.assertEqual(self.found({"host_access": {"s": "Reads the key"}}, services), [])
                said = self.found({}, services)
                self.assertEqual(len(said), 1)
                self.assertIn("s: mounts ${KEY_FILE", said[0])
                self.assertNotIn(str(spelling), said[0])
        # With a default that is inside the folder it is inside, in either spelling
        inside = {"s": {"volumes": [self.bind(f"{reached}/.lab/catalogs/fixture/addons/k/${{DATA:-./data}}", "/d")]}}
        self.assertEqual(self.found({}, inside), [])

    def test_a_variable_with_a_default_is_that_default(self):
        here = str(self.folder_)
        inside = {"s": {"volumes": [self.bind(f"{here}/${{DATA_DIR:-./data}}", "/d")]}}
        self.assertEqual(self.found({}, inside), [])
        self.assertEqual(self.found({"host_access": {"s": "why"}}, inside),
                         ["x-gravitee.host_access: s takes nothing of this machine beyond its container: remove it"])
        outside = {"s": {"volumes": [self.bind(f"{here}/${{DATA_DIR:-/etc/hosts}}", "/d")]}}
        self.assertIn(f"mounts {os.path.realpath('/etc/hosts')} of this machine", self.found({}, outside)[0])

    def test_a_source_that_compose_typed_a_volume_is_a_bind_when_it_names_a_place(self):
        # Read without interpolation, `${HOME}/.kube:/k` is a named volume as Compose types it, the text of the source untouched
        services = {"s": {"volumes": [{"type": "volume", "source": "${HOME}/.kube", "target": "/k", "volume": {}},
                                      {"type": "volume", "source": "data", "target": "/d", "volume": {}}]}}
        said = self.found({}, services)
        self.assertEqual(len(said), 1)
        self.assertIn("s: mounts ${HOME}/.kube of this machine", said[0])
        self.assertEqual(self.found({"host_access": {"s": "why"}}, services), [])
        # The short form, as a text
        short = {"s": {"volumes": ["${HOME}/.kube:/k:ro", "data:/d", "./conf:/c"]}}
        self.assertIn("mounts ${HOME}/.kube of this machine", self.found({}, short)[0])

    def test_the_declaration_of_what_binds_a_variable_stays_needed_in_the_files_that_name_the_service_again(self):
        # An add-on that includes the one that declared it asks nothing it did not add
        here = str(self.folder_)
        base = unit("fixture", "addon", "base")
        (self.root / "/".join(base.split("/")[:-1])).mkdir(parents=True)
        theirs = {"s": {"volumes": [self.bind(f"{self.root}/{'/'.join(base.split('/')[:-1])}/${{KEY_FILE}}", "/key")]}}
        mine = {"s": {"volumes": theirs["s"]["volumes"] + [self.bind(f"{here}/data", "/d")]}}
        self.assertEqual(self.found({}, mine, ("s",), [base], {base: theirs}), [])


class Gateways(Machine):
    """The gateway of a platform that an add-on runs."""

    KINDS = {"cloud": "https://cloud.test", "apim": "https://apim.test"}

    def setUp(self):
        super().setUp()
        self.patch(platforms, "KINDS", self.KINDS)

    def hybrid(self, ns="fixture", name="hybrid", kind="cloud", token="HYBRID_CLOUD_TOKEN", service_name=None, waits=()):
        """An add-on that runs the gateway of a kind: the unit as the project reads it."""
        file, raw = member(ns, "addon", name, [f"apim-{name}-gateway", f"{name}-redis"])
        names = list(raw["services"])
        raw["x-gravitee"]["gateway"] = {"platform": kind, "service": service_name or names[0], "token": token}
        raw["services"][names[0]]["ports"] = [port(8090, 8082)]
        if waits:
            raw["services"][names[0]]["depends_on"] = {other: {"condition": "service_healthy"} for other in waits}
        return file, raw

    def build(self, *units, settings=True, **more):
        variables = {file: [variable("HYBRID_CLOUD_TOKEN", None if settings else "demo", "Token of the gateway")]
                     for file, _ in units}
        return self.machine(list(units), variables=variables, **more)

    def gateway(self, uid="hybrid", **said):
        file, raw = self.hybrid()
        raw["x-gravitee"]["gateway"].update(said)
        built = self.build((file, raw))
        return list(check._gateway(built["addons"][uid], built))

    def test_a_gateway_is_a_service_of_the_add_on_that_publishes_a_port_and_takes_its_token_in_a_setting(self):
        self.assertEqual(self.gateway(), [])

    def test_the_kind_of_platform_a_gateway_serves_exists(self):
        self.assertEqual(self.gateway(platform="apim"), [])
        self.assertEqual(self.gateway(platform="nowhere"),
                         ["x-gravitee.gateway.platform: nowhere is not a kind of platform: cloud, apim"])

    def test_the_service_of_a_gateway_is_the_add_on_s_own_and_publishes_a_port(self):
        for name in ("nothing", "apim-gateway"):   # not there, the core's
            with self.subTest(service=name):
                self.assertEqual(self.gateway(service=name), [f"x-gravitee.gateway.service: {name} is not a service of this add-on"])
        self.assertEqual(self.gateway(service="hybrid-redis"), ["x-gravitee.gateway.service: hybrid-redis publishes no port"])

    def test_the_token_of_a_gateway_is_a_setting_the_add_on_requires(self):
        wanted = "x-gravitee.gateway.token: {0} must be a setting of this add-on without a default: ${{{0}}}"
        self.assertEqual(self.gateway(token="HYBRID_OTHER"), [wanted.format("HYBRID_OTHER")])
        built = self.build(self.hybrid(), settings=False)   # the setting has a default
        self.assertEqual(list(check._gateway(built["addons"]["hybrid"], built)), [wanted.format("HYBRID_CLOUD_TOKEN")])

    def test_one_add_on_of_a_catalog_runs_the_gateway_of_a_kind_of_platform(self):
        again = "x-gravitee.gateway: hybrid runs the gateway of cloud already: one add-on does"
        built = self.build(self.hybrid(), self.hybrid(name="other", kind="apim"))   # another kind: both can
        self.assertEqual([list(check._gateway(built["addons"][uid], built)) for uid in ("hybrid", "other")], [[], []])
        built = self.build(self.hybrid(), self.hybrid(name="other", kind="cloud"))   # the same kind: the later one is the problem
        self.assertEqual([list(check._gateway(built["addons"][uid], built)) for uid in ("hybrid", "other")], [[], [again]])

    def test_a_second_provider_in_another_catalog_is_told_which_one_is_used(self):
        # Only a catalog the engine lists (or this machine) may declare a gateway: the key is cleared for any other (N04)
        built = self.build(self.hybrid(), self.hybrid(ns="partner", name="theirs"),
                           catalogs={"partner": {"name": "Partner Bank", "listed": True}})
        self.assertEqual(built["platform"]["gateways"]["cloud"], "hybrid")
        self.assertEqual(list(check._gateway(built["addons"]["hybrid"], built)), [])
        self.assertEqual(list(check._gateway(built["addons"]["@partner/theirs"], built)),
                         ["x-gravitee.gateway: also provided by Fixture, which is used"])
        # The one that is used says nothing of the other, and a catalog that may not declare one has none to say
        built = self.build(self.hybrid(), self.hybrid(ns="partner", name="theirs"))
        self.assertIsNone(built["addons"]["@partner/theirs"]["gateway"])
        self.assertEqual(list(check._gateway(built["addons"]["@partner/theirs"], built)), [])

    def test_a_deploy_brings_the_gateway_alone_so_it_leaves_the_core_as_it_is(self):
        self.assertEqual(self.gateway(), [])
        built = self.build(self.hybrid(waits=("hybrid-redis", "apim-gateway")))   # its own Redis is fine, the core's is not
        self.assertEqual(list(check._gateway(built["addons"]["hybrid"], built)), [
            "x-gravitee.gateway: a deploy brings hybrid, whose apim-hybrid-gateway depends_on apim-gateway, a service of the "
            "core, which a deploy does not start"])

    def test_an_add_on_that_runs_no_gateway_is_not_asked_for_one(self):
        built = self.build(member("fixture", "addon", "kafka", ["kafka"]))
        self.assertEqual(list(check._gateway(built["addons"]["kafka"], built)), [])

    def test_the_add_on_is_checked_with_the_rest(self):
        file, raw = self.hybrid()
        raw["x-gravitee"]["gateway"]["service"] = "nothing"
        self.build((file, raw))
        self.assertEqual(self.lines("addon", "hybrid"), ["x-gravitee.gateway.service: nothing is not a service of this add-on"])


class Listings(Case):
    """The README of each kind, when a catalog keeps one, lists every item once, in the row its x-gravitee block gives."""

    ROWS = {"addon": ["| [AI Agent Lens](agent-lens/) | Live diagram |", "| [Jade](jade/) | Tracing |",
                      "| [Kafka](kafka/) | Kafka broker |"],
            "setup": ["| [Event streams](event-streams.yml) | Kafka |"],
            "playground": ["| [Welcome](welcome/) | ACME Hello | 30 min |"]}

    def setUp(self):
        self.units = {kind: [self.unit(kind, *spec) for spec in specs] for kind, specs in {
            "addon": [("agent-lens", "AI Agent Lens", "Live diagram"), ("jade", "Jade", "Tracing"),
                      ("kafka", "Kafka", "Kafka broker")],
            "setup": [("event-streams", "Event streams", "Kafka")],
            "playground": [("welcome", "Welcome", "ACME Hello", "30 min")]}.items()}
        self.folder(check, paths)

    def unit(self, kind, name, label, description, duration=None):
        return {"id": name, "kind": kind, "name": label, "description": description, "duration": duration}

    def readme(self, kind, rows, head="# Add-ons\n\nOne each.\n\n| Add-on | What it adds |\n|---|---|\n",
               tail="\nTo make your own, see [AGENTS.md](AGENTS.md).\n", units=None):
        self.write(f"catalog/{check.LISTINGS[kind][0]}", head + "".join(f"{row}\n" for row in rows) + tail)
        return list(check._table(kind, self.units[kind] if units is None else units, self.root / "catalog"))

    def test_every_item_has_its_row_and_nothing_else_does(self):
        for kind, rows in self.ROWS.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.readme(kind, rows), [])

    def test_a_row_is_written_from_the_x_gravitee_block(self):
        self.assertEqual([check._row(kind, one) for kind, units in self.units.items() for one in units],
                         [row for rows in self.ROWS.values() for row in rows])
        # A name is the item's id without x-gravitee.name; a description on several lines is one; no duration is none
        one = self.unit("playground", "welcome", "Welcome", "ACME\n  Hello  ")
        self.assertEqual(check._row("playground", one), "| [Welcome](welcome/) | ACME Hello |  |")
        # The link leads to the name of the folder, whatever the id of the item is in this Lab
        self.assertEqual(check._row("addon", {**self.units["addon"][1], "id": "@acme-hub/jade"}), self.ROWS["addon"][1])

    def test_a_missing_row_is_shown_as_it_must_read(self):
        rows = [row for row in self.ROWS["addon"] if "Jade" not in row]
        self.assertEqual(self.readme("addon", rows), [
            ("addons/README.md", "does not list Jade: add the row | [Jade](jade/) | Tracing |")])

    def test_a_row_that_differs_from_its_block_is_shown_as_it_must_read(self):
        rows = [row.replace("Tracing", "OpenTelemetry tracing") for row in self.ROWS["addon"]]
        rows[2] = "| [Kafka](kafka/) | Kafka broker | |"
        self.assertEqual(self.readme("addon", rows), [
            ("addons/README.md", "the row of Jade must read | [Jade](jade/) | Tracing |"),
            ("addons/README.md", "the row of Kafka must read | [Kafka](kafka/) | Kafka broker |")])

    def test_an_item_is_listed_once(self):
        self.assertEqual(self.readme("setup", self.ROWS["setup"] * 2), [
            ("setups/README.md", "lists Event streams 2 times: keep one row, "
                                 "| [Event streams](event-streams.yml) | Kafka |")])

    def test_a_row_of_what_does_not_exist_is_removed(self):
        rows = [*self.ROWS["playground"], "| [Gone](gone/) | Deleted | 5 min |"]
        self.assertEqual(self.readme("playground", rows), [
            ("playgrounds/README.md", "lists gone/, which is no playground of this catalog: remove its row")])

    def test_only_a_row_that_starts_with_a_link_is_a_row(self):
        head = ("# Setups\n\nSee [AGENTS.md](AGENTS.md) and [the guide](../docs/guide.md).\n\n"
                "| Setup | What it turns on |\n|---|---|\n")
        self.assertEqual(self.readme("setup", self.ROWS["setup"], head=head), [])

    def test_a_catalog_that_keeps_no_table_has_no_finding(self):
        # The template, a new folder catalog and the internal catalog pass on day one
        for kind in self.units:
            self.assertEqual(list(check._table(kind, self.units[kind], self.root / "catalog")), [])
        self.assertEqual(list(check._table("setup", [], self.root / "catalog")), [])

    def test_the_old_name_of_the_listings_is_gone(self):
        self.assertFalse(hasattr(check, "CATALOGS"))
        self.assertEqual(sorted(check.LISTINGS), ["addon", "playground", "setup"])


class Literals(Case):
    """No password, secret, key or token is written in a shared file: each is a setting, ${NAME:-default}."""

    LITERAL = "is a literal: write ${NAME:-default} so that it is a setting (no password in a compose file)"
    IN_URI = ("a literal password in its URI: write ${NAME:-default} for the password so that it is a setting "
              "(no password in a compose file)")

    def found(self, config, included=(), **others):
        """What is wrong with a file of this config, `others` being the config of the files it includes (by path)."""
        raw = {"a.yml": config, **others}
        return list(check._literals(raw, "a.yml", list(included)))

    def environment(self, **values):
        return {"services": {"app": {"environment": values}}}

    def test_a_literal_password_in_an_environment(self):
        self.assertEqual(self.found(self.environment(DB_PASSWORD="hunter2")),
                         [f"app environment: DB_PASSWORD {self.LITERAL}"])

    def test_a_literal_in_labels_and_in_any_extension_block(self):
        config = {"services": {"app": {"labels": {"io.example.api-key": "abc"}}},
                  "x-bank-secrets": {"demo-password": "HelloWorld@123"}, "x-nested": {"deep": [{"client_secret": "s"}]}}
        self.assertEqual(self.found(config), [
            f"app labels: io.example.api-key {self.LITERAL}",
            f"x-bank-secrets: demo-password {self.LITERAL}",
            f"x-nested.deep[0]: client_secret {self.LITERAL}"])

    def test_the_names_of_a_secret(self):
        for key in ("PASSWORD", "DB_PASSWD", "KEYSTORE_PASSPHRASE", "CLIENT_SECRET", "LLM_API_KEY", "OPENAI_APIKEY",
                    "ADMIN_TOKEN", "token", "gravitee_ratelimit_redis_password", "Admin_Password", "KAFKA_CLIENT_PASSWORDS"):
            with self.subTest(key=key):
                self.assertEqual(self.found(self.environment(**{key: "literal"})), [f"app environment: {key} {self.LITERAL}"])

    def test_a_name_that_ends_with_key_is_an_api_key(self):
        # Whatever the separator: a key of a service is a secret, named after its service or not
        for key in ("ACME_KEY", "ADMIN_KEY", "encryption_key", "signing-key", "gravitee.license.key", "llm.Key"):
            with self.subTest(key=key):
                self.assertEqual(self.found(self.environment(**{key: "literal"})), [f"app environment: {key} {self.LITERAL}"])
        # What only has key in its name is none: a key alone, a keystore, an id of a key, a public key
        self.assertEqual(self.found(self.environment(KEY="a", KEYS="b", KEY_ID="c", KEYSTORE="d", MONKEY="e",
                                                     PUBLIC_KEY="f", **{"public-key": "g", "ssl.public.key": "h"})), [])
        # A setting passes, as for every secret
        self.assertEqual(self.found(self.environment(ACME_KEY="${ACME_KEY:-demo}")), [])

    def test_a_setting_passes_with_or_without_a_default(self):
        self.assertEqual(self.found(self.environment(
            DB_PASSWORD="${DB_PASSWORD:-demo}", API_KEY="${API_KEY}", SECRET="${A:-${B:-x}}", ADMIN_TOKEN="${T-}",
            OTHER_PASSWORD="prefix-${X:-y}")), [])

    def test_an_empty_value_passes(self):
        self.assertEqual(self.found(self.environment(DB_PASSWORD="", API_KEY="", KAFKA_GRAVITEE_MANAGEMENTAPIORGADMINTOKEN="")),
                         [])

    def test_a_name_that_says_where_what_kind_or_whether_is_not_a_secret(self):
        self.assertEqual(self.found(self.environment(
            AM_TOKEN_URL="http://am-gateway:8092/oauth/token", AM_AUTHORIZE_URL="http://localhost/authorize",
            DB_PASSWORD_FILE="/run/secrets/db", CLIENT_SECRET_PATH="/etc/secret", TLS_SECRET_URI="file:///x",
            KAFKA_TLS_TYPE="PEM", KAFKA_SECRET_TYPE="pem", TOKEN_ENDPOINT="http://x/token", gravitee_secrets_enabled="true",
            gravitee_secrets_kubernetes_kubeConfigFile="/kube", GOOGLE_APPLICATION_CREDENTIALS="/opt/secrets/key.json",
            DELEGATION="on", KAFKA_TLS_CLIENT_AUTH="requested", gravitee_servers_1_ssl_keystore_type="pkcs12",
            NEXTAUTH_URL="http://localhost:3010", TOKEN_URL="http://x", PUBLIC_KEY="not asked")), [])

    def test_a_setting_that_only_mentions_a_secret_is_not_one(self):
        # Where a secret manager is, how it is reached, which one it is, how long a token lasts, what a password must be
        self.assertEqual(self.found(self.environment(
            gravitee_secrets_vault_host="vault", gravitee_secrets_vault_port="8200", gravitee_secrets_vault_auth_method="token",
            gravitee_secrets_vault_namespace="lab", gravitee_secrets_provider="vault", SECRET_PROVIDER="aws",
            PASSWORD_POLICY="strong", PASSWORD_REGEX="[a-z]+", ADMIN_TOKEN_TIMEOUT="30", ADMIN_API_KEY_TIMEOUTMS="3000",
            API_KEY_HOST="keys", KEY_PROVIDER_HOST="h", PASSWORD_METHOD="bcrypt")), [])
        # What ends in anything else is still one
        self.assertEqual(self.found(self.environment(SECRET_HOSTS="a", PASSWORD_PORTS="b")), [
            f"app environment: SECRET_HOSTS {self.LITERAL}", f"app environment: PASSWORD_PORTS {self.LITERAL}"])

    def test_only_text_is_read(self):
        # A flag or a list of names is no password: "secrets" lists the secrets a service uses, by name
        config = {"services": {"app": {"secrets": ["db_password"], "environment": {"A_PASSWORD_ENABLED": True,
                                                                                    "SECRET_COUNT": None}}},
                  "secrets": {"db_password": {"file": "./db_password.txt"}}}
        self.assertEqual(self.found(config), [])
        # A number written without quotes is one, where YAML keeps it a number
        self.assertEqual(self.found({"x-demo": {"password": 1234}}), [f"x-demo: password {self.LITERAL}"])

    def test_a_literal_password_in_a_uri(self):
        self.assertEqual(self.found(self.environment(
            OPENFGA_DATASTORE_URI="postgres://openfga:openfga@postgres:5432/openfga?sslmode=disable",
            REDIS_URL="redis://:secret@redis:6379/0")), [
            f"app environment: OPENFGA_DATASTORE_URI has {self.IN_URI}", f"app environment: REDIS_URL has {self.IN_URI}"])
        self.assertEqual(self.found({"services": {"app": {"command": ["serve", "--db", "mongodb://u:p@mongo/db"]}}}),
                         [f"app command: a URI has {self.IN_URI}"])
        self.assertEqual(self.found({"x-demo": {"links": ["https://admin:admin@example.com/ui"]}}),
                         [f"x-demo.links: a URI has {self.IN_URI}"])

    def test_a_uri_whose_password_is_a_setting_passes(self):
        self.assertEqual(self.found(self.environment(
            OPENFGA_DATASTORE_URI="postgres://openfga:${OPENFGA_POSTGRES_PASSWORD:-openfga}@postgres:5432/openfga",
            A="postgres://${DB_USER:-openfga}:${DB_PASSWORD:-openfga}@postgres/db", B="https://user@example.com/x",
            C="http://localhost:8080/path:abc@x", D="postgres://user:@host/db", E="amqp://host:5672",
            F="redis://:${REDIS_PASSWORD}@redis")), [])
        # A setting elsewhere in the text does not make a literal password one
        self.assertEqual(self.found(self.environment(A="postgres://u:literal@host/${DB:-db}")),
                         [f"app environment: A has {self.IN_URI}"])

    def test_a_secret_in_a_uri_is_said_once(self):
        self.assertEqual(self.found(self.environment(DB_PASSWORD="postgres://u:p@h")),
                         [f"app environment: DB_PASSWORD {self.LITERAL}"])

    def test_the_password_and_the_key_of_an_account(self):
        config = {"x-gravitee": {"accounts": [
            {"for": "Console", "username": "admin", "password": "admin"},
            {"for": "LLM proxy", "key": "acme-key"},
            {"for": "Portal", "username": "john", "password": "${PORTAL_PASSWORD:-doe}"},
            {"for": "Proxy", "key": "${LLM_KEY:-acme-key}"},
            {"for": "Empty", "username": "john", "password": ""},
            {"for": "Token", "token": "abc"}]}}
        self.assertEqual(self.found(config), [
            f"x-gravitee.accounts[0]: password {self.LITERAL}", f"x-gravitee.accounts[1]: key {self.LITERAL}",
            f"x-gravitee.accounts[5]: token {self.LITERAL}"])

    def test_the_rest_of_x_gravitee_is_not_asked(self):
        # Its keys are read by the Lab UI; a license feature may be named after a secret manager
        config = {"x-gravitee": {"name": "Demo", "key": "value", "requires": {"license": {
            "apim-secrets-manager": "Step 4", "apim-secret-provider-vault": "Step 5"}}}}
        self.assertEqual(self.found(config), [])
        # Outside x-gravitee, a block named `accounts` or `key` is like any other
        self.assertEqual(self.found({"x-other": {"accounts": [{"key": "k", "password": "p"}]}}), [
            f"x-other.accounts[0]: password {self.LITERAL}"])

    def test_a_shared_secret_anchor_is_a_setting_everywhere_it_is_used(self):
        # Compose resolves the anchors: each use shows the setting the anchor was given
        demo = "${ACME_BAKERY_DEMO_PASSWORD:-HelloWorld@123}"
        config = {"x-bank-secrets": {"demo-password": demo},
                  "x-gravitee": {"accounts": [{"username": "alice", "password": demo}]},
                  "services": {"seed": {"environment": {"BANK_DEMO_PASSWORD": demo, "APIM_PASSWORD": "${P:-admin}"}}}}
        self.assertEqual(self.found(config), [])

    def test_what_an_included_file_writes_is_its_own(self):
        # Read alone, a file shows the services of the files it includes: their secret is theirs, once
        inner = self.environment(DB_PASSWORD="hunter2")
        outer = {"services": {"app": {"environment": {"DB_PASSWORD": "hunter2", "API_KEY": "abc"}}}}
        self.assertEqual(self.found(outer, included=["b.yml"], **{"b.yml": inner}), [f"app environment: API_KEY {self.LITERAL}"])
        self.assertEqual(self.found(inner, **{"b.yml": inner}), [f"app environment: DB_PASSWORD {self.LITERAL}"])
        # A different secret over the included one is the file's
        outer["services"]["app"]["environment"] = {"DB_PASSWORD": "other"}
        self.assertEqual(self.found(outer, included=["b.yml"], **{"b.yml": inner}),
                         [f"app environment: DB_PASSWORD {self.LITERAL}"])

    def test_a_build_argument_and_a_nested_block_are_named_by_their_path(self):
        config = {"services": {"app": {"build": {"args": {"NPM_TOKEN": "abc"}}}}, "name": "demo"}
        self.assertEqual(self.found(config), [f"app build.args: NPM_TOKEN {self.LITERAL}"])


class Sharing(Machine):
    """An item of this machine about to be shared is checked as what it will be, and `make check` is silent about the rest."""

    def setUp(self):
        super().setUp()
        self.write(".gemini/settings.json", '{"context": {"fileName": ["AGENTS.md"]}}')

    def whoami(self, **more):
        """A machine with an add-on of this machine: the machine and the add-on's file."""
        file, raw = member("local", "addon", "whoami", ["whoami"])
        raw["services"]["whoami"].update(ports=[port(8098)], **more)
        raw["x-gravitee"].update(category="Other")
        self.units = [member("fixture", "addon", "jade", ["jade"]), (file, raw)]
        self.machine(self.units)
        return raw

    def found(self, *files, **how):
        return [(file, message) for file, message in check.problems(**how) if file in files]

    def test_what_belongs_to_this_machine_is_checked_only_when_it_is_about_to_be_shared(self):
        raw = self.whoami()
        del raw["x-gravitee"]["description"]
        self.machine(self.units)
        self.assertEqual(self.found(WHOAMI), [], "make check is about the engine")
        self.assertEqual(self.found(WHOAMI, sharing=WHOAMI), [(WHOAMI, "x-gravitee.description is missing")])

    def test_about_to_be_shared_it_includes_nothing_of_this_machine(self):
        self.whoami()
        mine = member("local", "setup", "mine", include=[("fixture", "addon", "jade"), ("local", "addon", "whoami")])
        del mine[1]["x-gravitee"]["category"]
        self.machine([*self.units, mine])
        self.assertEqual(self.found(MINE, sharing=MINE), [
            (MINE, f"includes {WHOAMI}: {PERSONAL}"), (MINE, "x-gravitee.category is missing")])
        self.assertEqual(self.found(MINE, WHOAMI, sharing=WHOAMI), [])

    def test_only_the_playground_about_to_be_shared_is_loaded_alone(self):
        self.whoami()
        play = member("local", "playground", "sandbox", ["sandbox"])
        self.machine([*self.units, play, member("fixture", "playground", "welcome", ["acme-website"])])
        alone = self.patch(check, "_alone", return_value=[])
        check.problems()
        alone.assert_not_called()
        check.problems(sharing=unit("local", "playground", "sandbox"))
        self.assertEqual([call.args[0]["file"] for call in alone.call_args_list], [unit("local", "playground", "sandbox")])
        alone.reset_mock()
        check.problems(sharing=WHOAMI)
        alone.assert_not_called()

    def test_a_port_published_twice_is_the_problem_of_what_is_of_this_machine(self):
        raw = self.whoami()
        raw["services"]["whoami"]["ports"] = [port(9411)]   # Jade's, which comes first
        self.units[0][1]["services"]["jade"]["ports"] = [port(9411)]
        self.machine(self.units)
        clash = [(WHOAMI, f"publishes port 9411, like {JADE}")]
        self.assertEqual(self.found(JADE, WHOAMI, sharing=WHOAMI), clash)
        self.assertEqual(self.found(JADE, WHOAMI), [], "not the engine's to say")

    def test_a_port_two_items_of_this_machine_publish_is_not_the_one_about_to_be_shared(self):
        # Once it is shared, `make check` says it of the other one, whichever comes first by name
        self.whoami()
        other = member("local", "addon", "other", ["other"])
        other[1]["services"]["other"]["ports"] = [port(8098)]
        self.machine([*self.units, other])
        self.assertEqual(self.found(WHOAMI, other[0], sharing=WHOAMI), [(other[0], f"publishes port 8098, like {WHOAMI}")])
        self.assertEqual(self.found(WHOAMI, other[0], sharing=other[0]), [(WHOAMI, f"publishes port 8098, like {other[0]}")])

    def test_what_belongs_to_this_machine_publishes_where_it_wants_until_it_is_shared(self):
        self.whoami()
        self.units[1][1]["services"]["whoami"]["ports"] = [port(8098, host_ip=None)]
        self.machine(self.units)
        self.assertEqual(self.found(WHOAMI), [])
        self.assertEqual(self.found(WHOAMI, sharing=WHOAMI),
                         [(WHOAMI, "publishes port 8098 on every interface: write 127.0.0.1:8098:8098")])

    def test_no_secret_is_written_in_what_is_about_to_be_shared(self):
        self.whoami(environment={"WHOAMI_API_KEY": "abc"})
        self.assertEqual(self.found(WHOAMI), [])
        self.assertEqual(self.found(WHOAMI, sharing=WHOAMI), [(WHOAMI, f"whoami environment: WHOAMI_API_KEY {LITERAL}")])

    def test_what_keeps_running_restarts_unless_stopped(self):
        self.whoami(restart="always")
        self.assertEqual(self.found(WHOAMI), [])
        self.assertEqual(self.found(WHOAMI, sharing=WHOAMI), [(WHOAMI, f"whoami: {ALWAYS}")])

    def test_a_port_is_published_by_the_file_that_adds_it(self):
        self.whoami()
        self.assertEqual([check.publishers().get(number) for number in ("8082", "8098", "9999")], [CORE, WHOAMI, None])

    def test_a_bind_of_a_plugin_folder_is_an_anchor_of_what_is_about_to_be_shared_as_it_is_of_a_catalog(self):
        raw = self.whoami()
        (self.root / "plugins/apim").mkdir(parents=True)
        raw["services"]["whoami"]["volumes"] = [{"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/plugins"}]
        self.machine(self.units, products=["apim"])
        self.assertEqual(self.found(WHOAMI, sharing=WHOAMI), [])
        # A folder that is not a plugin folder still asks for a reason
        raw["services"]["whoami"]["volumes"][0]["source"] = str(self.root / "docker-compose.yml")
        self.machine(self.units, products=["apim"])
        [(_, asked)] = self.found(WHOAMI, sharing=WHOAMI)
        self.assertIn("whoami: mounts ", asked)

    def test_the_plugin_folders_of_the_contract_are_anchors_before_a_clone_has_made_them(self):
        self.assertEqual(check._anchored([]), [], "no contract.lock, no anchor")
        shutil.copy(REPO / check.LOCK_FILE, self.root / check.LOCK_FILE)
        self.assertEqual(check._anchored([]), ["am", "apim"])
        self.assertEqual(check._anchored(["apim", "mine"]), ["am", "apim", "mine"])

    def test_the_core_is_checked_whoever_asks(self):
        self.whoami()
        self.machine(self.units, core=lambda core: core["services"]["apim-gateway"].update(
            environment={"REDIS_PASSWORD": "redispassword"}, restart="always"))
        said = [message for file, message in check.problems() if file == CORE]
        self.assertIn(f"apim-gateway: {ALWAYS}", said)
        self.assertIn(f"apim-gateway environment: REDIS_PASSWORD {LITERAL}", said)
        self.assertEqual(said, [message for file, message in check.problems(sharing=WHOAMI) if file == CORE])


class Core(unittest.TestCase):
    """What the core says of the contract it keeps with the catalogs."""

    def test_the_seeder_image_is_a_repository_without_a_tag(self):
        for image in ("ghcr.io/owner/lab/seeder", "registry.example.invalid/lab/seeder", "localhost:5000/seeder/x"):
            self.assertEqual(list(check._contract_keys({"x-gravitee": {"seeder": {"image": image}}})), [], image)
        wanted = ("x-gravitee.seeder.image is missing or is no repository: write the seeder's image without a tag, such as "
                  "ghcr.io/owner/repo/seeder, the only image a seed service may have")
        for core in ({}, {"x-gravitee": {}}, {"x-gravitee": {"seeder": None}}, {"x-gravitee": {"seeder": {}}},
                     {"x-gravitee": {"seeder": {"image": "seeder"}}}, {"x-gravitee": {"seeder": {"image": "a/b:latest"}}},
                     {"x-gravitee": {"seeder": {"image": 3}}}):
            with self.subTest(core=core):
                self.assertEqual(list(check._contract_keys(core)), [wanted])

    def test_the_levels_are_whole_numbers_from_1_and_the_floor_is_not_above_the_top(self):
        seeder = {"seeder": {"image": "ghcr.io/o/r/seeder"}}
        self.assertEqual(list(check._contract_keys({"x-gravitee": seeder})), [], "a core without levels reads as level 1")
        self.assertEqual(list(check._contract_keys({"x-gravitee": {**seeder, "contract": 3, "contract_min": 2}})), [])
        for meta, said in (({"contract": 0}, ["x-gravitee.contract must be a whole number from 1"]),
                           ({"contract": "1"}, ["x-gravitee.contract must be a whole number from 1"]),
                           ({"contract_min": 1.5}, ["x-gravitee.contract_min must be a whole number from 1"]),
                           ({"contract": True}, ["x-gravitee.contract must be a whole number from 1"]),
                           ({"contract": 2, "contract_min": 3},
                            ["x-gravitee.contract_min is above contract: the oldest level still read cannot pass the newest"])):
            with self.subTest(meta=meta):
                self.assertEqual(list(check._contract_keys({"x-gravitee": {**seeder, **meta}})), said)




class Contract(Machine):
    """`lab` in catalog.yml is the true minimum: the highest level among the contract items a catalog uses."""

    ROWS = {("unit.key", "x-gravitee.name"): 1, ("unit.key", "x-gravitee.kind"): 1, ("unit.key", "x-gravitee.description"): 1,
            ("unit.key", "x-gravitee.category"): 1, ("unit.key", "x-gravitee.terminal"): 3, ("unit.key", "x-gravitee.requires"): 1,
            ("unit.key", "x-gravitee.requires.images"): 2, ("label", "io.gravitee.name"): 1, ("label", "io.gravitee.description"): 1,
            ("label", "io.gravitee.console.<id>.url"): 2, ("core.service", "apim-gateway"): 1, ("core.network", "internal"): 1,
            ("core.category", "Other"): 1, ("core.variable", "GRAVITEE_LICENSE"): 1, ("anchor", "seeder"): 2,
            ("anchor", "plugins/apim"): 2, ("seed.folder", "apim"): 2, ("seed.folder", "apim/apis"): 1, ("seed.file", "controls.yaml"): 3,
            ("seed.setting", "APIM_MANAGEMENT_URL"): 2, ("guide.block", "run"): 3}

    def setUp(self):
        super().setUp()
        self.lock = self.patch(check, "_lock", return_value=(3, 1, self.ROWS))

    def units(self, *units, **more):
        self.machine(list(units), **more)
        return [(kind, uid, self.built[f"{kind}s"][uid]) for kind in paths.KINDS for uid in self.built[f"{kind}s"]]

    def lines(self, level, *units, **more):
        found = self.units(*units, **more)
        return list(check._lab_lines({"schema": 1, "lab": level}, found, self.given["raw"], self.given))

    def plain(self):
        return member("fixture", "addon", "echo", ["echo"])

    def test_lab_is_the_highest_level_of_what_the_catalog_uses(self):
        # The keys of the x-gravitee block, the labels, the network, the category and the core service: all level 1
        self.assertEqual(self.lines(1, self.plain()), [])
        terminal = member("fixture", "playground", "tour", ["tour-app"], says={"terminal": "tour-app"})
        self.assertEqual(self.lines(1, terminal), ["lab is 1 but must be 3, for x-gravitee.terminal: older Gravitee Labs read it. Fix: run catalog.py upgrade --write, which writes it"])
        self.assertEqual(self.lines(3, terminal), [])

    def test_a_lab_that_is_higher_than_needed_is_wrong_too(self):
        said = self.lines(2, self.plain())
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"^lab is 2 but must be 1, for [^,]+, [^,]+, [^,]+ and \d+ more: older Gravitee Labs")
        self.assertIn("category Other", said[0])
        self.assertEqual(self.lines(0, self.plain())[0][:23], "lab is 0 but must be 1,")

    def test_lab_is_never_above_the_level_this_engine_provides(self):
        self.assertEqual(self.lines(4, self.plain()), ["lab is 4, more than any Gravitee Lab reads: older Gravitee Labs read it. Fix: run catalog.py upgrade --write, which writes it"])
        self.assertEqual(self.lines(99, self.plain()), ["lab is 99, more than any Gravitee Lab reads: older Gravitee Labs read it. Fix: run catalog.py upgrade --write, which writes it"])

    def test_a_catalog_yml_without_the_lines_of_older_labs_is_a_warning_and_nothing_to_verify(self):
        terminal = member("fixture", "playground", "tour", ["tour-app"], says={"terminal": "tour-app"})
        self.assertEqual(self.lines(None, terminal), [check.WARNING_MARK + check.NO_LEGACY])
        found = check.as_problems([("catalog.yml", check.WARNING_MARK + check.NO_LEGACY)], {"addons": {}, "setups": {},
                                                                                            "playgrounds": {}}, "fixture")
        self.assertEqual([(one.severity, one.message) for one in found], [("warning", "catalog.yml has no lines for older Gravitee "
                                                                           "Labs, which then cannot read it. Fix: catalog.py "
                                                                           "upgrade --write")])

    def test_every_kind_of_item_counts_where_a_unit_uses_it(self):
        def needed(*units, **more):
            return check._used(self.units(*units, **more), self.given["raw"], self.given, self.ROWS)
        service = member("fixture", "addon", "svc", ["svc"])
        service[1]["services"]["svc"]["labels"]["io.gravitee.console.web.url"] = "http://localhost:8000"
        found = needed(service)
        self.assertEqual(found[("label", "io.gravitee.console.<id>.url")], 2, "the console labels are listed with their id as <id>")
        self.assertEqual({found[key] for key in found if key[0] == "label" and "console" not in key[1]}, {1})
        self.assertEqual(found[("core.network", "internal")], 1)
        self.assertEqual(found[("core.category", "Other")], 1)
        self.assertNotIn(("anchor", "seeder"), found)
        # What the lock does not list is no part of the contract: a key it has never heard of costs no level
        odd = member("fixture", "addon", "odd", ["odd"], says={"colour": "red"})
        self.assertNotIn(("unit.key", "x-gravitee.colour"), needed(odd))
        # A core service the unit sets keys on or waits for
        wait = member("fixture", "addon", "wait", ["wait"])
        wait[1]["services"]["wait"]["depends_on"] = {"apim-gateway": {"condition": "service_healthy"}}
        self.assertEqual(needed(wait)[("core.service", "apim-gateway")], 1)
        # What it requires is a key of its own
        wants = member("fixture", "addon", "wants", ["wants"], says={"requires": {"images": {"APIM_VERSION": "4.12"}}})
        found = needed(wants)
        self.assertEqual((found[("unit.key", "x-gravitee.requires")], found[("unit.key", "x-gravitee.requires.images")]), (1, 2))

    def test_a_key_of_catalog_yml_counts_once_the_catalog_gives_it_something_and_renamed_counts_once_it_is_there(self):
        rows = {**self.ROWS, ("catalog.key", "x-gravitee-catalog.lab"): 1, ("catalog.key", "x-gravitee-catalog.canary"): 1,
                ("catalog.key", "x-gravitee-catalog.renamed"): 3, ("catalog.key", "x-gravitee-catalog.renamed.items"): 3,
                ("catalog.key", "x-gravitee-catalog.renamed.settings"): 3}
        self.lock.return_value = (3, 1, rows)
        empty = {"items": {}, "settings": {}}
        self.assertEqual(check._catalog_keys({"lab": 1, "canary": [], "renamed": None}, rows),
                         {("catalog.key", "x-gravitee-catalog.lab"): 1}, "an empty list or no renamed uses nothing")
        self.assertEqual(check._catalog_keys({"renamed": empty}, rows), {("catalog.key", "x-gravitee-catalog.renamed"): 3},
                         "an empty renamed is a key an older Lab refuses all the same")
        renamed = {**empty, "items": {"addon:gate": "addon:gate-keeper"}}
        self.assertEqual(check._catalog_keys({"renamed": renamed}, rows), {("catalog.key", "x-gravitee-catalog.renamed"): 3,
                                                                          ("catalog.key", "x-gravitee-catalog.renamed.items"): 3})
        found = self.units(self.plain())
        said = list(check._lab_lines({"schema": 1, "lab": 1, "renamed": renamed}, found, self.given["raw"], self.given))
        self.assertEqual(said, ["lab is 1 but must be 3, for catalog.yml key x-gravitee-catalog.renamed, catalog.yml key "
                                "x-gravitee-catalog.renamed.items: older Gravitee Labs read it. Fix: run catalog.py upgrade "
                                "--write, which writes it"])
        self.assertEqual(list(check._lab_lines({"schema": 1, "lab": 3, "renamed": renamed}, found, self.given["raw"], self.given)), [])
        self.assertEqual(check.needed_lab(found, self.given["raw"], self.given, {"renamed": renamed}), 3,
                         "what catalog.py writes for older Gravitee Labs")
        self.assertEqual(check.needed_lab(found, self.given["raw"], self.given), 1)

    def test_the_seeder_a_plugin_folder_and_the_seed_settings_are_anchors_and_settings_of_the_contract(self):
        seeder = member("fixture", "playground", "tour", ["tour-seed"])
        seed = seeder[1]["services"]["tour-seed"]
        seed.update(build={"context": str(check.ROOT / "seeder")}, volumes=[
            {"type": "bind", "source": str(check.ROOT / "plugins/apim"), "target": "/p"}])
        seed["labels"]["io.gravitee.role"] = "seed"
        seed["environment"] = {"APIM_MANAGEMENT_URL": "http://apim-management-api:8083", "OTHER": "x"}
        (check.ROOT / "plugins/apim").mkdir(parents=True)
        self.write(".lab/catalogs/fixture/playgrounds/tour/seed/apim/apis/one.json", "{}")
        self.write(".lab/catalogs/fixture/playgrounds/tour/seed/controls.yaml", "")
        found = check._used(self.units(seeder, products=["apim"]), self.given["raw"], self.given, self.ROWS)
        for key in (("anchor", "seeder"), ("anchor", "plugins/apim"), ("seed.setting", "APIM_MANAGEMENT_URL"),
                    ("seed.folder", "apim"), ("seed.folder", "apim/apis"), ("seed.file", "controls.yaml")):
            self.assertIn(key, found, key)
        self.assertNotIn(("seed.setting", "OTHER"), found)
        self.assertEqual(max(found.values()), 3)
        self.assertEqual(self.lines(2, seeder, products=["apim"]), [
            "lab is 2 but must be 3, for seed file controls.yaml: older Gravitee Labs read it. Fix: run catalog.py upgrade --write, which writes it"])

    def test_a_run_block_of_the_guide_is_an_item_of_the_contract(self):
        play = member("fixture", "playground", "tour", ["tour-app"])
        found = check._used(self.units(play, guides={"tour": "## 1. One\n\n```run\nls\n```\n"}), self.given["raw"], self.given,
                            self.ROWS)
        self.assertEqual(found[("guide.block", "run")], 3)
        found = check._used(self.units(play, guides={"tour": "## 1. One\n\n```sh\nls\n```\n"}), self.given["raw"], self.given,
                            self.ROWS)
        self.assertNotIn(("guide.block", "run"), found)

    def test_without_the_lock_the_level_cannot_be_checked(self):
        self.lock.return_value = None
        self.assertEqual(self.lines(1, self.plain()), ["contract.lock is not there or cannot be read, so `lab` cannot be verified"])

    def test_the_lock_is_read_as_the_test_of_the_contract_writes_it(self):
        lock = "contract 3\ncontract_min 1\ncore.category \"Gravitee Platform\" since=1\n" \
               "core.service apim-gateway port=8082,8443 health=yes networks=edge,internal since=2\n" \
               "unit.key x-gravitee.terminal kinds=playground since=3\nbroken line\n"
        self.write(check.LOCK_FILE, lock)
        self.assertEqual(REAL_LOCK(), (3, 1, {("core.category", "Gravitee Platform"): 1, ("core.service", "apim-gateway"): 2,
                                              ("unit.key", "x-gravitee.terminal"): 3}))
        self.write(check.LOCK_FILE, "contract 3\n")   # the two levels come first
        self.assertIsNone(REAL_LOCK())
        (self.root / check.LOCK_FILE).unlink()
        self.assertIsNone(REAL_LOCK())


class Descriptor(Machine):
    """What catalog.yml says: the refusals of its reading, a namespace that is not the folder's or is reserved, a key the Lab
    does not read."""

    def setUp(self):
        super().setUp()
        self.write(".lab/catalogs/fixture/catalog.yml", "x-gravitee-catalog:\n  id: fixture\n")

    def read(self, meta=None, error=None, ns="fixture", listed=("fixture",)):
        """What `_read_yml` says of the catalog `ns` when the reading of its file gives this."""
        reading = self.patch(manifest, "read_catalog", **({"side_effect": error} if error else
                                                         {"return_value": {"unknown": [], **(meta or {"id": ns})}}))
        return check._read_yml(ns, list(listed)), reading

    def test_a_catalog_that_reads_has_nothing_to_say(self):
        found, reading = self.read()
        self.assertEqual(found[1], [])
        self.assertEqual(reading.call_args.args[0], ".lab/catalogs/fixture/catalog.yml")

    def test_every_problem_of_the_table_is_told_one_to_a_line(self):
        error = registry.CatalogError("catalog_invalid", "catalog.yml: id is required.", 400,
                                      problems=["id is required", "version must be three numbers with dots, such as 1.0.0"])
        found, _ = self.read(error=error)
        self.assertEqual(found, (None, ["id is required", "version must be three numbers with dots, such as 1.0.0"]))

    def test_the_names_of_the_listed_catalogs_are_not_one_this_catalog_may_take(self):
        self.patch(check.defaults, "read", return_value=[{"id": "fixture", "name": "Fixture catalog"},
                                                         {"id": "other", "name": "The other one"}])
        _, reading = self.read()
        self.assertEqual(reading.call_args.kwargs["borrowed"], ["The other one"], "its own name is its own")

    def test_a_file_that_holds_more_than_its_block_is_refused_and_an_include_is_told(self):
        text = "x-gravitee-catalog:\n  id: fixture\ninclude:\n  - ../other/compose.yml\n"
        self.write(".lab/catalogs/fixture/catalog.yml", text)
        error = registry.CatalogError("catalog_invalid", "catalog.yml: it may hold only the x-gravitee-catalog block.", 400)
        found, _ = self.read(error=error)
        self.assertEqual(found, (None, ["it may hold only the x-gravitee-catalog block.",
                                        "an include, a service or any key beside x-gravitee-catalog is refused: the file is "
                                        "data for the Lab, not a Compose project"]))
        self.write(".lab/catalogs/fixture/catalog.yml", "x-gravitee-catalog:\n  id: fixture\nservices: {}\n")
        self.assertEqual(self.read(error=error)[0], (None, ["it may hold only the x-gravitee-catalog block."]))

    def test_the_id_is_the_name_the_catalog_is_installed_under_and_not_a_reserved_word(self):
        found, _ = self.read({"id": "other"})
        self.assertEqual(found[1], ["id is other, not fixture, the name this catalog is installed or linked under: link it again"])
        found, _ = self.read({"id": "lab"})
        self.assertEqual(found[1], [
            "id is lab, not fixture, the name this catalog is installed or linked under: link it again",
            "lab is a reserved name. Ask the owner of this catalog to choose another."])
        # The engine lists the id of its default catalog: the reserved word is accepted for it, and for it alone
        found, _ = self.read({"id": "gravitee"}, ns="gravitee", listed=("gravitee",))
        self.assertEqual(found[1], [])
        found, _ = self.read({"id": "gravitee"}, ns="gravitee", listed=("fixture",))
        self.assertEqual(found[1], ["gravitee is a reserved name. Ask the owner of this catalog to choose another."])

    def test_a_key_the_lab_does_not_read_is_told(self):
        found, _ = self.read({"id": "fixture", "unknown": ["colour"]})
        self.assertEqual(found[1], ["colour is not a key of x-gravitee-catalog: " + ", ".join(check.manifest.KEYS)])

    def test_a_key_of_requires_or_of_a_catalog_it_requires_is_told_by_its_path(self):
        found, _ = self.read({"id": "fixture", "unknown": ["requires.catalogs.hub.mirror", "requires.gpu"]})
        self.assertEqual(found[1], ["requires.catalogs.hub.mirror is not a key of a catalog it requires: name, url, min",
                                    "requires.gpu is not a key of requires: catalogs"])

    def test_the_dependencies_a_catalog_declares_are_the_ones_its_units_include_from(self):
        meta = {"requires": {"catalogs": {"partner": {}, "stranger": {}}}}
        built = self.machine([member("fixture", "setup", "pair", include=[("partner", "addon", "ledger")]),
                              member("partner", "addon", "ledger", ["ledger"])])
        units = [("setup", "pair", built["setups"]["pair"])]
        self.assertEqual(list(check._dependencies(meta, units, self.given, "fixture")),
                         ["requires.catalogs.stranger is declared and no unit includes from it: remove it"])
        self.assertEqual(list(check._dependencies({"requires": {"catalogs": {"partner": {}}}}, units, self.given, "fixture")), [])
        self.assertEqual(list(check._dependencies({}, units, self.given, "fixture")), [])

    def test_a_canary_is_an_item_of_the_catalog(self):
        built = self.machine([member("fixture", "playground", "tour", ["tour-app"]), member("fixture", "addon", "echo", ["echo"])])
        units = [("playground", "tour", built["playgrounds"]["tour"]), ("addon", "echo", built["addons"]["echo"])]
        self.assertEqual(list(check._canary({"canary": ["playground:tour", "playground:ghost", "addon:tour"]}, units)), [
            "canary: playground:ghost is not an item of this catalog", "canary: addon:tour is not an item of this catalog"])


class Renamed(Machine):
    """What catalog.yml renames, against what the catalog holds."""

    def units(self):
        built = self.machine([member("fixture", "addon", "gate-keeper", ["gate-keeper-proxy"]),
                              member("fixture", "playground", "tide-pools", ["tide-pools-app"])])
        return [("addon", "gate-keeper", built["addons"]["gate-keeper"]),
                ("playground", "tide-pools", built["playgrounds"]["tide-pools"])]

    def lines(self, **renamed):
        return list(check._renamed_lines({"renamed": renamed}, self.units(), self.given["raw"]))

    def test_an_old_item_is_gone_from_the_catalog_and_the_end_of_its_chain_is_there(self):
        self.assertEqual(self.lines(items={"addon:gate": "addon:gate-keeper", "playground:tides": "playground:tide-lab",
                                           "playground:tide-lab": "playground:tide-pools"}), [], "a chain ends at an item")
        self.assertEqual(self.lines(items={"addon:gate": "addon:keeper"}),
                         ["renamed.items: addon:keeper is not an item of this catalog"])
        self.assertEqual(self.lines(items={"addon:gate-keeper": "addon:gate"}), [
            "renamed.items: addon:gate-keeper is an item of this catalog: what is renamed away cannot be there too",
            "renamed.items: addon:gate is not an item of this catalog"],
            "a rename away from an id the catalog still has would move a person's settings out of an item that is there")

    def test_an_old_setting_that_an_item_still_reads_is_refused(self):
        units = self.units()
        reads = {units[0][2]["file"]: ["GATE_LICENSE"]}
        self.assertEqual(list(check._renamed_lines({"renamed": {"settings": {"GATE_LICENSE": "GATE_KEEPER_LICENSE"}}}, units,
                                                   self.given["raw"], reads)),
                         ["renamed.settings: GATE_LICENSE is still read by gate-keeper: the Lab renames it in the .env of every "
                          "item of the catalog, so read GATE_KEEPER_LICENSE there"])

    def test_the_history_says_a_volume_the_renamed_item_no_longer_has_and_a_setting_it_left_behind(self):
        units = self.units()
        self.write(units[0][2]["file"], "services:\n  gate-keeper-proxy:\n    image: x:${GATE_KEEPER_VERSION:-1}\n"
                                        "    environment: {T: \"${GATE_KEEPER_TOKEN:-}\", L: \"${GRAVITEE_LICENSE:-}\"}\n"
                                        "volumes:\n  gate-keeper-data: {}\n")
        before = ("services:\n  gate:\n    image: x:${GATE_VERSION:-1}\n    environment: {T: \"${GATE_TOKEN:-}\", "
                  "L: \"${GRAVITEE_LICENSE:-}\"}\nvolumes:\n  gate-data:\n    driver: local\n")
        then = (self.root / units[0][2]["file"]).read_text()
        commits = {"addons/gate/compose.yml": (before, None, then)}   # the commit that renamed it: gate before, gate-keeper there

        def read(path, also=None):
            return commits.get(path)
        meta = {"renamed": {"items": {"addon:gate": "addon:gate-keeper"}, "settings": {"GATE_TOKEN": "GATE_KEEPER_TOKEN"}}}
        said = list(check._renamed_lines(meta, units, self.given["raw"], {}, read, {"GRAVITEE_LICENSE"}))
        self.assertEqual(said, [
            "renamed.items: addon:gate had the volume gate-data, which addon:gate-keeper does not: keep the name, or the people "
            "who have it lose its data",
            "renamed.settings: addon:gate read GATE_VERSION, which addon:gate-keeper no longer reads: map it to the name "
            "addon:gate-keeper reads, or the value people set is left behind"], "a setting of the core is the Lab's .env's")
        meta["renamed"]["settings"]["GATE_VERSION"] = "GATE_KEEPER_VERSION"
        commits["addons/gate/compose.yml"] = (before, None, then.replace("gate-keeper-data", "gate-data"))
        self.assertEqual(list(check._renamed_lines(meta, units, self.given["raw"], {}, read, {"GRAVITEE_LICENSE"})), [])
        self.write(units[0][2]["file"], "services:\n  gate-keeper-proxy:\n    image: x:1\n")
        self.assertEqual(list(check._renamed_lines(meta, units, self.given["raw"], {}, read, {"GRAVITEE_LICENSE"})), [],
                         "a later release that drops a setting or a volume is not the rename's: only its commit is read")
        self.assertEqual(list(check._renamed_lines(meta, units, self.given["raw"], {}, lambda path, also=None: None)), [],
                         "with no history to read, nothing is said of it")

    def test_a_rename_into_an_item_the_release_before_had_is_refused(self):
        units = self.units()
        commits = {"addons/gate/compose.yml": ("services: {}\n", "services: {}\n", "services: {}\n")}
        said = list(check._renamed_lines({"renamed": {"items": {"addon:gate": "addon:gate-keeper"}}}, units, self.given["raw"], {},
                                         lambda path, also=None: commits.get(path)))
        self.assertEqual(said, ["renamed.items: addon:gate-keeper was an item already when addon:gate was renamed into it: the "
                                "Lab keeps the two apart, so addon:gate is removed, not renamed. Fix: rename it into a name that "
                                "is new"])

    def test_a_setting_of_the_core_or_a_shared_one_is_never_renamed(self):
        said = list(check._renamed_lines({"renamed": {"settings": {"GRAVITEE_LICENSE": "GATE_LICENSE"}}}, self.units(),
                                         self.given["raw"], {}, None, {"GRAVITEE_LICENSE"}))
        self.assertEqual(said, ["renamed.settings: GRAVITEE_LICENSE is a setting of the Lab's own .env (the core's, or shared by "
                                "the catalog's items): the Lab renames only the settings of each item's .env"])

    def test_a_rename_into_a_name_an_earlier_release_gave_another_item_is_a_warning(self):
        units = self.units()
        commits = {"addons/gate-keeper/compose.yml": ("services: {}\n", None, None)}   # an item before, and was removed

        def read(path, also=None):
            return commits.get(path)
        said = list(check._renamed_lines({"renamed": {"items": {"addon:gate": "addon:gate-keeper"}}}, units, self.given["raw"], {},
                                         read))
        self.assertEqual(said, [check.WARNING_MARK + "renamed.items: addon:gate-keeper is a name an earlier release gave another "
                                "item: who updates from that release has both, so addon:gate is removed there, not renamed"])

    def test_settings_are_not_judged_against_the_tree_and_nothing_is_nothing(self):
        self.assertEqual(self.lines(settings={"GATE_LICENSE": "GATE_KEEPER_LICENSE"}), [])
        self.assertEqual(list(check._renamed_lines({}, self.units(), self.given["raw"])), [])


class Availability(Case):
    """A catalog is checked where it is installed or linked, with the ones it includes from."""

    def setUp(self):
        fixtures.project(self, paths, check, project, compose, envfiles)

    def test_a_catalog_that_includes_from_another_needs_it_here(self):
        with self.assertRaises(check.Unavailable) as said:
            check.available("fixture")
        self.assertEqual(str(said.exception), "fixture includes from partner, which is neither installed nor linked here: "
                                              "check them together, after catalog.py link PATH or catalog.py add ADDRESS --yes")
        self.assertEqual(check.missing("fixture"), ["partner"])
        fixtures.install(self, "partner")
        self.assertEqual(check.missing("fixture"), [])
        self.assertIn("partner", check.available("fixture"))

    def test_a_catalog_that_is_not_here_cannot_be_checked(self):
        for ns, said in (("ghost", "ghost is neither installed nor linked here: link its folder first, with catalog.py link PATH"),
                         ("local", "local is not a catalog: it holds the items of this machine")):
            with self.subTest(ns=ns), self.assertRaises(check.Unavailable) as raised:
                check.available(ns)
            self.assertEqual(str(raised.exception), said)

    def test_an_install_that_is_damaged_or_still_downloading_has_nothing_to_check(self):
        # The Lab reads no unit of it (registry.sources): a check of it would read nothing and print All good
        def rows(state, version="1.0.0"):
            return {"fixture": {"kind": "git", "version": version, "state": state, "requires_catalogs": {}}}
        for state, said in (
                ("damaged", "fixture is damaged: repair it from the menu of its row in the Lab UI, or remove it and add it again"),
                ("downloading", "fixture is still downloading: check it once it is installed (catalog.py provision waits for "
                                "the downloads)")):
            with self.subTest(state=state), self.assertRaises(check.Unavailable) as raised:
                check.available("fixture", rows(state))
            self.assertEqual(str(raised.exception), said)
        for state in ("uptodate", "updating", "unreachable", "lab_new"):   # a copy the Lab reads, whatever else is the matter
            self.assertIn("fixture", check.available("fixture", rows(state)), state)
        catalogs = {"fixture": {"kind": "folder", "version": "1.0.0", "state": "linked", "requires_catalogs": {"partner": {}}},
                    "partner": {"kind": "git", "version": "2.0.0", "state": "damaged", "requires_catalogs": {}}}
        self.assertEqual(check.missing("fixture", catalogs), ["partner"], "a damaged install does not count as present")
        with self.assertRaises(check.Unavailable) as raised:
            check.available("fixture", catalogs)
        self.assertEqual(str(raised.exception), "fixture includes from partner, which is damaged: repair it from the menu of "
                                                "its row in the Lab UI, or remove it and add it again")

    def test_a_declared_catalog_that_is_only_downloading_is_not_here(self):
        fixtures.declare(self, "partner")
        self.assertEqual(check.missing("fixture"), ["partner"])
        fixtures.install(self, "partner", link=True)
        self.assertEqual(check.missing("fixture"), [])
        (self.root / ".lab/catalogs/partner").unlink()
        (self.root / ".lab/catalogs/partner").symlink_to(self.root / "gone")
        self.assertEqual(check.missing("fixture"), ["partner"], "a link to nothing is not a catalog")


class Needs(Case):
    """The catalogs a folder includes from are the Lab's own."""

    def test_what_a_folder_declares_must_be_installed_or_linked(self):
        here = {"partner": {"kind": "git", "version": "2.0.0", "state": "uptodate", "requires_catalogs": {}}}
        check.needs_here("hub", ["partner"], here)
        check.needs_here("hub", [], {})
        with self.assertRaises(check.Unavailable) as raised:
            check.needs_here("hub", ["partner", "ghost"], here)
        self.assertEqual(str(raised.exception), "hub includes from ghost, which is neither installed nor linked here: check them "
                                                "together, after catalog.py link PATH or catalog.py add ADDRESS --yes")
        with self.assertRaises(check.Unavailable) as raised:
            check.needs_here("hub", ["a", "b"], {})
        self.assertIn("a, b, which are neither installed nor linked here", str(raised.exception))
        damaged = {"partner": {"kind": "git", "version": "2.0.0", "state": "damaged", "requires_catalogs": {}}}
        with self.assertRaises(check.Unavailable) as raised:
            check.needs_here("hub", ["partner"], damaged)
        self.assertEqual(str(raised.exception), "hub includes from partner, which is damaged: repair it from the menu of its row in "
                                                "the Lab UI, or remove it and add it again")


class Scrubbing(Case):
    """Compose reads what a stranger wrote in the scrubbed environment."""

    def test_the_environment_of_compose_is_the_scrubbed_one_for_the_length_of_the_block_only(self):
        before = compose.environment
        with mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "t", "GITHUB_TOKEN": "g", "SSH_AUTH_SOCK": "/x", "COMPOSE_FILE": "x.yml"}):
            with check.scrubbed():
                env = compose.environment()
                self.assertFalse({"ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "COMPOSE_FILE"} & set(env))
                self.assertEqual((env["COMPOSE_EXPERIMENTAL_GIT_REMOTE"], env["COMPOSE_EXPERIMENTAL_OCI_REMOTE"]), ("false", "false"))
                self.assertNotEqual(env["HOME"], os.environ["HOME"], "an empty folder of the run")
            self.assertIs(compose.environment, before)
            self.assertEqual(compose.environment()["ACTIONS_RUNTIME_TOKEN"], "t")
            with self.assertRaises(RuntimeError), check.scrubbed():
                raise RuntimeError("boom")
            self.assertIs(compose.environment, before)


@COMPOSE
class Stranger(Case):
    """A unit that names a variable of the runner reads it empty, and the policy says the name is not the catalog's."""

    def setUp(self):
        fixtures.project(self, paths, check, project, compose, envfiles)
        shutil.copy(REPO / check.LOCK_FILE, self.root / check.LOCK_FILE)
        self.patch(license, "status_of", lambda variable: None)
        unit = ("x-gravitee: {kind: addon, name: Leak, description: d, category: Other}\n"
                "services:\n  fixture-leak:\n    image: x\n    labels: {io.gravitee.description: leak}\n"
                "    environment:\n      LEAK: ${ACTIONS_RUNTIME_TOKEN}\n      HOME_IS: ${HOME}\n")
        fixtures.install(self, "partner")
        fixtures.install(self, "fixture", files={"addons/leak/compose.yml": unit, "addons/leak/README.md": "leak\n"})
        project.forget()

    def environment(self, scrubbed):
        with mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "a-secret-of-the-runner"}):
            project.forget()
            if scrubbed:
                with check.scrubbed():
                    return check.loaded()
            return check.loaded()

    def test_the_value_of_a_variable_of_the_runner_is_empty_and_home_is_not_the_persons(self):
        file = unit("fixture", "addon", "leak")
        loose = self.environment(False)[1]["configs"][file]["services"]["fixture-leak"]["environment"]
        self.assertEqual(loose["LEAK"], "a-secret-of-the-runner", "the shell's environment, as the Lab reads its own units")
        built, inputs = self.environment(True)
        tight = inputs["configs"][file]["services"]["fixture-leak"]["environment"]
        self.assertEqual(tight["LEAK"], "")
        self.assertNotEqual(tight["HOME_IS"], os.environ["HOME"])
        self.assertTrue(tight["HOME_IS"].startswith(str(compose.scratch())), "a temporary path")

    def test_and_the_policy_says_the_name_is_not_the_catalog_s_own(self):
        built, inputs = self.environment(True)
        found = check.catalog_problems("fixture", built, inputs)
        reasons = [line for where, line in found if where == "addons/leak/compose.yml"]
        self.assertIn("Blocked: Reads settings that are not its own: ACTIONS_RUNTIME_TOKEN, HOME", reasons)


@COMPOSE
class Linking(Case):
    """A folder is linked into a copy of the Lab's project, for the length of a block, and nowhere else."""

    def setUp(self):
        fixtures.project(self, paths, check, project, compose, envfiles)
        shutil.copy(REPO / check.LOCK_FILE, self.root / check.LOCK_FILE)
        self.patch(license, "status_of", lambda variable: None)
        holder = tempfile.mkdtemp(prefix="gravitee-lab-author-")
        self.addCleanup(shutil.rmtree, holder, True)
        self.folder_of = Path(holder).resolve() / "hub"
        shutil.copytree(fixtures.CATALOGS / "partner", self.folder_of)
        project.forget()

    def taken(self):
        return {key: (value if not isinstance(value, Path) else str(value)) for key, value in
                (("config", (self.root / ".lab/catalogs.json").read_bytes() if (self.root / ".lab/catalogs.json").exists() else None),
                 ("lock", (self.root / ".lab/catalogs.lock").read_bytes() if (self.root / ".lab/catalogs.lock").exists() else None),
                 ("tree", sorted(str(one.relative_to(self.root)) for one in self.root.rglob("*"))))}

    def test_the_folder_is_the_catalog_of_its_id_inside_and_nothing_of_it_is_left_outside(self):
        before = self.taken()
        with check.temporary_link(self.folder_of, "partner") as linked:
            self.assertEqual((linked.ns, linked.lab, linked.folder), ("partner", self.root, self.folder_of))
            self.assertNotEqual(linked.root, self.root)
            self.assertEqual(paths.ROOT, linked.root)
            self.assertTrue(os.path.islink(linked.root / ".lab/catalogs/partner"))
            self.assertEqual(os.readlink(linked.root / ".lab/catalogs/partner"), str(self.folder_of))
            self.assertEqual([one["id"] for one in registry.entries()], ["partner"])
            self.assertEqual(registry.inputs()["partner"]["state"], "linked")
            self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
            self.assertTrue(linked.root.is_dir())
            holder = linked.root
        self.assertFalse(os.path.lexists(holder), "the temporary project is gone, links and all")
        self.assertTrue(self.folder_of.is_dir(), "and the folder that was linked into it is never touched")
        self.assertEqual(self.taken(), before)
        self.assertEqual((paths.ROOT, check.ROOT, project.ROOT, compose.ROOT), (self.root,) * 4)

    def test_a_folder_named_from_where_the_command_runs_is_linked_by_its_full_path(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.folder_of.parent)
        with check.temporary_link(Path("hub"), "partner") as linked:
            self.assertEqual(os.readlink(linked.root / ".lab/catalogs/partner"), str(self.folder_of))
            self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])

    def test_a_catalog_that_says_host_access_is_linked_as_allowed_and_one_that_does_not_is_not(self):
        with check.temporary_link(self.folder_of, "partner"):
            self.assertNotIn("host", registry.entry("partner"))
        catalog = self.folder_of / "catalog.yml"
        text = catalog.read_text()
        catalog.write_text(text.replace("  lab: 1\n", "  lab: 1\n  host_access: true\n", 1))
        self.assertIn("host_access: true", catalog.read_text())
        with check.temporary_link(self.folder_of, "partner"):
            self.assertIs(registry.entry("partner")["host"], True)
        catalog.write_text(text.replace("  lab: 1\n", "  lab: 1\n  host_access: false\n", 1))
        with check.temporary_link(self.folder_of, "partner"):
            self.assertNotIn("host", registry.entry("partner"))

    def test_every_module_that_holds_the_project_holds_the_copy_and_gets_the_lab_s_back(self):
        modules = [module for name, module in sys.modules.items() if name.startswith("lab_ui") and getattr(module, "ROOT", None) == self.root]
        self.assertTrue({check, project, compose, paths} <= set(modules))
        with check.temporary_link(self.folder_of, "partner") as linked:
            self.assertEqual({id(module.ROOT) for module in modules}, {id(linked.root)})
        self.assertEqual({module.ROOT for module in modules}, {self.root})
        with self.assertRaises(RuntimeError), check.temporary_link(self.folder_of, "partner") as linked:
            holder = linked.root
            raise RuntimeError("boom")
        self.assertEqual({module.ROOT for module in modules}, {self.root})
        self.assertFalse(os.path.lexists(holder))

    def test_a_module_that_is_first_imported_while_the_copy_is_the_project_does_not_keep_it(self):
        late = types.ModuleType("lab_ui.zz_imported_late")
        self.addCleanup(sys.modules.pop, "lab_ui.zz_imported_late", None)
        with check.temporary_link(self.folder_of, "partner") as linked:
            late.ROOT = paths.ROOT   # what `from .paths import ROOT` does in a module imported now
            sys.modules["lab_ui.zz_imported_late"] = late
            self.assertEqual(late.ROOT, linked.root)
        self.assertEqual(late.ROOT, self.root)

    def test_what_the_project_needs_of_the_lab_is_copied_or_an_empty_folder_and_nothing_is_a_link_into_it(self):
        with check.temporary_link(self.folder_of, "partner") as linked:
            links = [str(Path(top, name).relative_to(linked.root)) for top, folders, names in os.walk(linked.root)
                     for name in (*folders, *names) if os.path.islink(os.path.join(top, name))]
            self.assertEqual(links, [".lab/catalogs/partner"])
            self.assertEqual(sorted(one.name for one in linked.root.iterdir()),
                             [".lab", "catalogs.default.json", "contract.lock", "docker-compose.yml", "plugins", "seeder"])
            self.assertEqual(sorted(one.name for one in (linked.root / "plugins").iterdir()), ["am", "apim"])
            self.assertEqual(list((linked.root / "seeder").iterdir()), [], "the seeder is named by the units, not read")
            self.assertEqual((linked.root / "docker-compose.yml").read_bytes(), (self.root / "docker-compose.yml").read_bytes())
            self.assertFalse((linked.root / ".env").exists())

    def test_the_catalogs_it_needs_are_the_labs_own_with_what_the_lab_holds_of_them_and_no_other(self):
        fixtures.install(self, "partner")
        fixtures.install(self, "lab-new")
        fixtures.local(self, "scratch", "addon")
        fixtures.lock(self, "partner", audience="private")
        project.forget()
        with check.temporary_link(self.folder_of, "fixture", needs=["partner", "ghost"]) as linked:
            self.assertEqual(sorted(one.name for one in (linked.root / ".lab/catalogs").iterdir()), ["fixture", "partner"])
            self.assertEqual(os.path.realpath(linked.root / ".lab/catalogs/partner"), os.path.realpath(self.root / ".lab/catalogs/partner"))
            self.assertEqual([one["id"] for one in registry.entries()], ["partner", "fixture"])
            self.assertEqual(registry.lock_of("partner")["audience"], "private")
            self.assertEqual(registry.inputs()["partner"]["state"], "linked")

    def test_a_catalog_of_the_same_id_is_replaced_inside_by_the_folder(self):
        fixtures.install(self, "partner", kind="git") if fixtures.have_git() else fixtures.install(self, "partner")
        project.forget()
        before = self.taken()
        with check.temporary_link(self.folder_of, "partner") as linked:
            [entry] = registry.entries()
            self.assertEqual((entry["id"], entry["kind"], entry["path"]), ("partner", "folder", str(self.folder_of)))
            self.assertEqual(registry.lock_of("partner")["pin"], None)
            self.assertEqual(os.readlink(linked.root / ".lab/catalogs/partner"), str(self.folder_of))
        self.assertEqual(self.taken(), before)

    def test_the_audience_it_is_given_is_what_the_lock_says_for_the_policy(self):
        with check.temporary_link(self.folder_of, "partner", audiences={"partner": "public"}):
            self.assertEqual(registry.lock_of("partner")["audience"], "public")
            self.assertEqual(registry.inputs()["partner"]["audience"], "public")
        with check.temporary_link(self.folder_of, "partner"):
            self.assertEqual(registry.lock_of("partner")["audience"], "private")

    def test_a_list_of_catalogs_that_cannot_be_read_is_none_and_is_not_written_over(self):
        (self.root / ".lab").mkdir(exist_ok=True)
        (self.root / ".lab/catalogs.json").write_text("{not json", encoding="utf-8")
        with check.temporary_link(self.folder_of, "partner"):
            self.assertEqual([one["id"] for one in registry.entries()], ["partner"])
        self.assertEqual((self.root / ".lab/catalogs.json").read_text(encoding="utf-8"), "{not json")

    def test_a_folder_is_read_through_no_link(self):
        outside = Path(tempfile.mkdtemp(prefix="gravitee-lab-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "catalog.yml").write_text("x-gravitee-catalog:\n  schema: 1\n  id: stolen\n", encoding="utf-8")
        (self.folder_of / "catalog.yml").unlink()
        (self.folder_of / "catalog.yml").symlink_to(outside / "catalog.yml")
        self.assertEqual(check.folder_summary(self.folder_of), {}, "a link in the place of catalog.yml leads anywhere")
        self.assertEqual(check.folder_summary(outside)["id"], "stolen")
        self.assertEqual(check.folder_summary(outside / "nowhere"), {})


class Problems(Case):
    """What the items say becomes problems with a code when the policy has one."""

    def test_the_codes_come_from_the_verdict_of_the_unit_and_the_fix_from_the_table(self):
        project_ = {"addons": {"@hub/a": {"catalog": "hub", "file": ".lab/catalogs/hub/addons/a/compose.yml", "id": "@hub/a",
                                          "verdict": {"codes": ["T03", "T20"]}}},
                    "setups": {}, "playgrounds": {}}
        found = [("addons/a/compose.yml", "Blocked: Runs with full privileges"),
                 ("addons/a/compose.yml", "Blocked: Reads settings that are not its own: X, Y"),
                 ("addons/a/compose.yml", "publishes port 80, like docker-compose.yml"),
                 ("catalog.yml", "canary: playground:ghost is not an item of this catalog")]
        out = check.as_problems(found, project_, "hub")
        self.assertEqual([(one.code, one.severity, one.path, one.line) for one in out],
                         [("T03", "error", "addons/a/compose.yml", 0), ("T20", "error", "addons/a/compose.yml", 0),
                          ("", "error", "addons/a/compose.yml", 0), ("", "error", "catalog.yml", 0)])
        self.assertEqual(out[0].message, "Runs with full privileges. Fix: Remove `privileged`: a demo that needs it is a personal item.")
        self.assertTrue(out[1].message.startswith("Reads settings that are not its own: X, Y. Fix: Prefix the setting with `HUB_`, or "))
        self.assertEqual(out[2].message, "publishes port 80, like docker-compose.yml")

    def test_the_places_of_this_machine_are_taken_out_of_every_line(self):
        project_ = {"addons": {}, "setups": {}, "playgrounds": {}}
        found = [("a.yml", "svc: mounts /Users/me/hub/data of this machine"), ("a.yml", "in /tmp/x/seeder")]
        out = check.as_problems(found, project_, "hub", ("/Users/me/hub", "/tmp/x"))
        self.assertEqual([one.message for one in out], ["svc: mounts data of this machine", "in seeder"])

    def test_a_unit_without_a_verdict_has_no_code_for_its_reason(self):
        project_ = {"addons": {"@hub/a": {"catalog": "hub", "file": ".lab/catalogs/hub/addons/a/compose.yml", "id": "@hub/a"}},
                    "setups": {}, "playgrounds": {}}
        out = check.as_problems([("addons/a/compose.yml", "Blocked: Runs with full privileges")], project_, "hub")
        self.assertEqual([(one.code, one.message) for one in out], [("", "Runs with full privileges")])


@COMPOSE
class Engine(Case):
    """`make check`: the core, the skills and the agents' files. What a catalog holds is that catalog's to check, so a catalog
    never turns it red, and with none installed it prints All good (Z3). The output and the exit code are pinned."""

    ALL_GOOD = "✓ docker-compose.yml\n\nAll good\n"

    def setUp(self):
        self.folder(check, paths, project, compose, envfiles)
        fixtures.core(self)
        fixtures.default(self, "fixture")
        self.write(".gemini/settings.json", '{"context": {"fileName": ["AGENTS.md"]}}')
        self.patch(license, "status_of", lambda variable: None)
        project.forget()

    def run_check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.run()
        return code, out.getvalue()

    def test_with_no_catalog_installed_it_prints_all_good_and_exits_0(self):
        self.assertEqual(self.run_check(), (0, self.ALL_GOOD))

    def test_the_readme_tables_of_the_folders_of_the_old_layout_are_none_of_its_business(self):
        # 27 rows of addons/, setups/ and playgrounds/ at the root of the engine made it red; they are no listing of this layout
        for folder in ("addons", "setups", "playgrounds"):
            self.write(f"{folder}/README.md", f"# {folder}\n\n| Name | What |\n|---|---|\n| [Kafka](kafka/) | Kafka broker |\n")
        self.assertEqual(self.run_check(), (0, self.ALL_GOOD))

    def test_what_the_installed_catalogs_hold_never_turns_it_red(self):
        fixtures.install(self, "fixture")
        fixtures.install(self, "partner")
        fixtures.install(self, "hostile")
        project.forget()
        self.assertEqual(self.run_check(), (0, self.ALL_GOOD))
        fixtures.install(self, "fixture", files={"addons/echo/compose.yml": "x-gravitee: {kind: addon}\nservices: {echo: {image: x, privileged: true}}\n"})
        project.forget()
        self.assertEqual(self.run_check(), (0, self.ALL_GOOD))

    def test_a_problem_of_the_core_is_told_with_the_file_and_the_exit_code_is_1(self):
        text = fixtures.CORE.read_text(encoding="utf-8").replace("image: ghcr.io/gravitee-io-labs/gravitee-lab/seeder", "image: seeder")
        self.write("docker-compose.yml", text)
        project.forget()
        self.assertEqual(self.run_check(), (1, "✗ docker-compose.yml\n    x-gravitee.seeder.image is missing or is no repository: "
                                               "write the seeder's image without a tag, such as ghcr.io/owner/repo/seeder, the "
                                               "only image a seed service may have\n\n1 problem\n"))

    def test_the_wiring_of_the_agents_is_checked_with_the_core(self):
        (self.root / ".gemini/settings.json").unlink()
        self.write("AGENTS.md", "")
        project.forget()
        self.assertEqual(self.run_check(), (1, "✓ docker-compose.yml\n"
                                               "✗ .gemini/settings.json\n"
                                               "    is missing: create it with {\"context\": {\"fileName\": [\"AGENTS.md\"]}}\n"
                                               "✗ AGENTS.md\n"
                                               "    CLAUDE.md is missing: create it with the single line @AGENTS.md\n\n2 problems\n"))

    def test_the_checkout_itself_prints_all_good(self):
        done = subprocess.run([sys.executable, str(REPO / "lab-ui" / "server.py"), "--check"], cwd=REPO, capture_output=True,
                              text=True, timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual((done.returncode, done.stdout), (0, self.ALL_GOOD), done.stderr)

    def test_the_checkout_check_is_what_make_check_runs(self):
        text = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertIn("lab-ui/server.py --check", text)


class Shared(Case):
    """A machine read once for the whole class, the way the Lab reads it: `inputs` (what gather() read) and `built` (the
    project). A class changes what it reads with CATALOGS ({namespace: an install() patch}, beside the default one), LOCKED
    ({namespace: public or private}), FIXTURE (the patch of the default catalog) and `prepare`."""

    CATALOGS, LOCKED, FIXTURE = {}, {}, {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()   # a case of the class's own: the folder it makes and what it patches last as long
        cls.addClassCleanup(holder.doCleanups)
        fixtures.project(holder, paths, check, project, compose, envfiles, **cls.FIXTURE)
        shutil.copy(REPO / check.LOCK_FILE, holder.root / check.LOCK_FILE)
        for ns, how in cls.CATALOGS.items():
            fixtures.install(holder, ns, **how)
        for ns, audience in cls.LOCKED.items():
            fixtures.lock(holder, ns, audience=audience)
        cls.prepare(holder)
        holder.patch(license, "status_of", lambda variable: None)   # the files of the project folder, not the machine's license
        project.forget()
        cls.inputs = project.gather()
        cls.built = project.build(cls.inputs)
        cls.root = holder.root

    @classmethod
    def prepare(cls, case):
        """What else the project folder holds before it is read."""

    def found(self, ns, file=None):
        """The problems of catalog `ns`, those of one file of it when given."""
        return [(where, line) for where, line in check.catalog_problems(ns, self.built, self.inputs) if file in (None, where)]

    def said(self, ns, file):
        return [line for _, line in self.found(ns, file)]


def snapshot(root):
    """Everything under a folder, as it is: path, kind, size, modification time, and where a link leads."""
    found = []
    for top, folders, names in os.walk(root):
        for name in sorted(names) + sorted(folders):
            where = os.path.join(top, name)
            info = os.lstat(where)
            found.append((os.path.relpath(where, root), oct(info.st_mode), info.st_size, info.st_mtime_ns,
                          os.readlink(where) if os.path.islink(where) else None))
    return sorted(found)


def _odd_ledger():
    """The partner's ledger with a key of x-gravitee and a label that no Lab reads yet."""
    text = (fixtures.CATALOGS / "partner" / "addons/ledger/compose.yml").read_text(encoding="utf-8")
    return text.replace("description: Keeps the entries of a partner}", "description: Keeps the entries of a partner, badge: gold}") \
        .replace("      io.gravitee.name: Ledger\n", "      io.gravitee.name: Ledger\n      io.gravitee.badge: gold\n"
                 "      io.gravitee.role: tester\n")


NOT_READ = [("addons/ledger/compose.yml", "label io.gravitee.badge is not read by this Gravitee Lab: remove it, or check with "
                                          "the Gravitee Lab release that reads it (catalog.py upgrade --lab-ref latest)"),
            ("addons/ledger/compose.yml", "label io.gravitee.role=tester is not read by this Gravitee Lab: remove it, or check "
                                          "with the Gravitee Lab release that reads it (catalog.py upgrade --lab-ref latest)"),
            ("addons/ledger/compose.yml", f"x-gravitee.badge is not a key of x-gravitee: {', '.join(sorted(check.META))}")]


class NotReadInAFolder(Shared):
    """What a catalog of the author's own uses that this Lab does not read is a mistake, and each item is named: the unit is
    not blocked for it, since the person runs what they write."""

    CATALOGS = {"partner": {"files": {"addons/ledger/compose.yml": _odd_ledger()}}}

    def test_each_item_this_lab_does_not_read_is_named(self):
        self.assertEqual(sorted(self.found("partner")), NOT_READ)
        self.assertEqual(self.built["addons"]["@partner/ledger"]["blocked"], None)


class NotReadInAPublishedCatalog(Shared):
    """The same catalog published, a git catalog someone added: its unit needs a newer Gravitee Lab and is not run, and the
    check of it says so besides naming each item."""

    CATALOGS = {"partner": {"kind": "git", "files": {"addons/ledger/compose.yml": _odd_ledger()}}}

    def test_the_unit_is_blocked_as_needing_a_newer_gravitee_lab(self):
        unit = self.built["addons"]["@partner/ledger"]
        self.assertEqual((unit["compat"], unit["blocked"]), ("new", {"reasons": [
            "Needs a newer Gravitee Lab: uses label io.gravitee.badge, label io.gravitee.role=tester, x-gravitee.badge"]}))
        self.assertEqual(sorted(self.found("partner")), [
            ("addons/ledger/compose.yml", "Blocked: Needs a newer Gravitee Lab: uses label io.gravitee.badge, label "
                                          "io.gravitee.role=tester, x-gravitee.badge"),
            *NOT_READ])


class Fixtures(Shared):
    """The catalogs of the tests: `fixture` and `partner` say nothing but what their `lint` states; the others are
    refused for the level they ask for."""

    CATALOGS = {"partner": {}, "lab-new": {}, "lab-old": {}}

    def test_the_lint_of_every_unit_is_what_expected_json_says(self):
        wanted, seen = fixtures.expected(), set()
        for ns in ("fixture", "partner"):
            for key, lint in unit_lint(ns, self.built, self.inputs).items():
                self.assertEqual(lint, wanted[key]["lint"], key)
                seen.add(key)
        self.assertEqual(seen, set(wanted), "a unit of the oracle that the check does not see, or one that the oracle lacks")
        self.assertEqual({key for key, lint in wanted.items() if lint["lint"]}, {"addon:port-core"},
                         "the oracle states one finding, and the check says that one and no other")

    def test_the_blocked_units_are_the_ones_the_oracle_blocks_with_its_reasons_as_errors(self):
        wanted = fixtures.expected()
        for ns in ("fixture", "partner"):
            found = self.found(ns)
            for key, one in wanted.items():
                kind, _, uid = key.partition(":")
                unit = self.built[f"{kind}s"].get(uid)
                if not unit or unit["catalog"] != ns:
                    continue
                where = unit["file"][len(f".lab/catalogs/{ns}/"):]
                self.assertEqual([line for file, line in found if file == where and line.startswith("Blocked: ")],
                                 [f"Blocked: {reason}" for reason in one["reasons"]], key)

    def test_nothing_else_is_found_in_the_fixture_and_the_partner(self):
        self.assertEqual(self.found("partner"), [])
        self.assertEqual(sorted(self.found("fixture")), [
            ("addons/future/compose.yml", "Blocked: Needs a newer Gravitee Lab"),
            ("addons/port-core/compose.yml", "publishes port 8082, like docker-compose.yml"),
            ("setups/pair.yml", "Blocked: Depends on private content")])

    def test_a_catalog_is_listed_file_by_file(self):
        self.assertEqual(check.catalog_files("partner"), ["catalog.yml", "addons/ledger/compose.yml"])
        files = check.catalog_files("fixture")
        self.assertEqual(files[:2], ["catalog.yml", "addons/cluster/compose.yml"])
        self.assertIn("setups/pair.yml", files)
        self.assertEqual(files[-2:], ["playgrounds/shop/compose.yml", "playgrounds/tour/compose.yml"])

    def test_a_catalog_written_for_a_lab_that_does_not_exist_yet_is_refused_for_its_level(self):
        found = self.found("lab-new")
        self.assertIn(("catalog.yml", "lab is 99, more than any Gravitee Lab reads: older Gravitee Labs read it. Fix: run "
                                      "catalog.py upgrade --write, which writes it"), found)
        self.assertIn(("addons/newer/compose.yml", "Blocked: Needs a newer Gravitee Lab"), found)

    def test_a_catalog_written_for_a_lab_no_longer_read_is_refused_for_its_level(self):
        found = self.found("lab-old", "catalog.yml")
        self.assertEqual(len(found), 1)
        self.assertRegex(found[0][1], r"^lab is 0 but must be 1, for .+: older Gravitee Labs read it")
        self.assertIn(("addons/older/compose.yml", "Blocked: Written for an older Gravitee Lab"), self.found("lab-old"))

    def test_the_fixture_units_that_are_fine_pass_every_lint_of_the_catalog_profile(self):
        # No unit of the fixtures is in the catalog profile's way but the ones the oracle says
        quiet = {key for key, one in fixtures.expected().items() if not one["lint"] and one["verdict"] == "ok"}
        self.assertTrue(quiet)
        for key in sorted(quiet):
            kind, _, uid = key.partition(":")
            unit = self.built[f"{kind}s"][uid]
            self.assertEqual(self.said(unit["catalog"], unit["file"][len(f".lab/catalogs/{unit['catalog']}/"):]), [], key)

    def test_checking_writes_nothing(self):
        before = snapshot(self.root)
        check.catalog_problems("fixture", self.built, self.inputs)
        check.catalog_files("fixture")
        self.assertEqual(snapshot(self.root), before)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            check.run()
        self.assertEqual(snapshot(self.root), before)

    def test_the_report_is_text_annotations_or_a_list(self):
        found, files = self.found("fixture"), check.catalog_files("fixture")
        text = check.render(found, files)
        self.assertIn("✓ addons/echo/compose.yml\n", text)
        self.assertIn("✗ addons/port-core/compose.yml\n    publishes port 8082, like docker-compose.yml\n", text)
        self.assertTrue(text.endswith("\n3 problems\n"))
        annotations = check.render(found, files, "github")
        self.assertIn("::error file=addons/port-core/compose.yml::publishes port 8082, like docker-compose.yml\n", annotations)
        self.assertEqual(len(annotations.splitlines()), 3)
        listed = json.loads(check.render(found, files, "json"))
        self.assertEqual(listed, [{"file": where, "message": line} for where, line in found])
        self.assertEqual(json.loads(check.render([], files, "json")), [])
        self.assertEqual(check.render([], files, "github"), "")
        self.assertEqual(check.render([], ["catalog.yml"]), "✓ catalog.yml\n\nAll good\n")


class Annotations(unittest.TestCase):

    def test_what_an_author_names_cannot_write_to_the_screen_that_shows_it(self):
        found = [("addons/x\x1b[2Jy/compose.yml", "includes a\x1b]0;title\x07 file\x00")]
        text = check.render(found, [])
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertEqual(text, "✗ addons/x [2Jy/compose.yml\n    includes a ]0;title  file \n\n1 problem\n")
        self.assertNotIn("\x1b", check.render(found, [], "github"))
        self.assertEqual(json.loads(check.render(found, [], "json")), [{"file": found[0][0], "message": found[0][1]}],
                         "JSON says every character it holds, escaped")

    def test_a_name_that_is_no_utf_8_is_a_report_in_every_format_not_a_crash(self):
        found = [("addons/\udcff/compose.yml", "includes a file that is \udcfe")]   # what listdir gives for bytes that are no UTF-8
        for how in ("text", "github", "json"):
            with self.subTest(how=how):
                check.render(found, [], how).encode("utf-8")
        self.assertEqual(len(json.loads(check.render(found, [], "json"))), 1)

    def test_what_the_command_line_prints_keeps_its_lines_and_loses_what_moves_the_cursor(self):
        self.assertEqual(check.printable("one\r\ntwo\x1b[2K\nthree\rfour\x07"), "one\ntwo [2K\nthree four ")
        self.assertEqual(check.printable(""), "")
        self.assertEqual(check.printable(None), "None")
        self.assertEqual(check.printable("\u009b2J \u202e"), " 2J  ", "a C1 control and a direction override are spaces too")

    def test_a_message_that_has_a_line_break_or_a_percent_sign_stays_one_annotation(self):
        text = check.render([("a, b: c.yml", "50% of it\r\nis wrong")], [], "github")
        self.assertEqual(text, "::error file=a%2C b%3A c.yml::50%25 of it%0D%0Ais wrong\n")


class Hostile(Shared):
    """The hostile catalog (one unit for each rule of the trust policy), read through the project: what the policy gives for
    each unit is the oracle's, every reason is an error of the check, and an author is told the fix."""

    CATALOGS = {"partner": {}, "hostile": {}}
    LOCKED = {"hostile": "public"}   # the hostile catalog is public and the partner private (T24)
    ORACLE = json.loads((fixtures.DATA / "expected-hostile.json").read_text(encoding="utf-8"))

    @classmethod
    def units(cls):
        return {uid.rsplit("/", 1)[-1]: unit for kind in paths.KINDS for uid, unit in cls.built[f"{kind}s"].items()
                if unit["catalog"] == "hostile"}

    def where(self, name):
        unit = self.units()[name]
        return unit["file"][len(".lab/catalogs/hostile/"):]

    # The two units of the oracle that Compose cannot read in the project: a unit that does not load is no unit but a problem of
    # its catalog, said under its own file (6.3: "not a unit"), where the golden of the policy was judged from its text
    UNREADABLE = ("t23-include-outside", "t27-unreadable")

    def test_the_policy_gives_the_reasons_of_the_oracle_for_every_unit(self):
        units = self.units()
        self.assertEqual(sorted(units), sorted(set(self.ORACLE) - set(self.UNREADABLE)))
        for name, one in sorted(self.ORACLE.items()):
            if name in self.UNREADABLE:
                continue
            reasons = (units[name]["blocked"] or {}).get("reasons", [])
            self.assertEqual((reasons, "blocked" if reasons else "ok"), (one["reasons"], one["verdict"]), name)

    def test_every_reason_is_an_error_of_the_check(self):
        found = self.found("hostile")
        for name, one in sorted(self.ORACLE.items()):
            if name in self.UNREADABLE:
                continue
            self.assertEqual([line for where, line in found if where == self.where(name) and line.startswith("Blocked: ")],
                             [f"Blocked: {reason}" for reason in one["reasons"]], name)

    def test_a_unit_that_does_not_load_is_a_problem_of_the_catalog_said_under_its_own_file(self):
        for name in self.UNREADABLE:
            self.assertNotIn(name, self.units())
            said = self.said("hostile", f"addons/{name}/compose.yml")
            self.assertEqual(len(said), 1, name)
            self.assertRegex(said[0], r"^Cannot be read: .+")

    def test_a_service_that_takes_the_machine_must_say_why_before_the_policy_has_its_say(self):
        said = self.said("hostile", "addons/t10-docker-socket/compose.yml")
        self.assertIn("hostile-socket: mounts the Docker socket: say why in x-gravitee.host_access (hostile-socket: <reason>), "
                      "so that it is read before it runs", said)
        self.assertIn("hostile-provider: runs a program on this machine: say why in x-gravitee.host_access "
                      "(hostile-provider: <reason>), so that it is read before it runs",
                      self.said("hostile", "addons/t05-provider/compose.yml"))
        # What declares its reason is not asked again; whether this catalog may use the machine is the policy's to say
        self.assertFalse([line for line in self.said("fixture", "addons/cluster/compose.yml") if "host_access" in line])
        self.assertIn("Blocked: Runs with full privileges", self.said("hostile", "addons/h-privileged-declared/compose.yml"))

    def test_a_port_on_every_interface_says_how_to_write_it(self):
        self.assertIn("publishes port 8199 on every interface: write 127.0.0.1:8199:80",
                      self.said("hostile", "addons/t17-all-interfaces/compose.yml"))

    def test_a_name_that_clashes_is_told_with_the_fix_of_the_prefix(self):
        said = self.said("hostile", "addons/t21-name-clash/compose.yml")
        self.assertIn("Blocked: Same name as Echo", said)
        self.assertIn("fixture-echo: name it hostile-fixture-echo, or start it with the name of the unit, t21-name-clash-", said)

    def test_a_volume_that_is_not_its_own_is_the_policys_and_no_naming_matter(self):
        self.assertFalse([line for line in self.said("hostile", "addons/t14-external-volume/compose.yml") if "data-" in line])
        self.assertTrue([line for line in self.said("hostile", "addons/t14-volume-alias/compose.yml") if "data-" in line])

    def test_a_path_that_leaves_the_folder_of_the_unit_says_where_the_anchor_is(self):
        said = self.said("hostile", "addons/t18-build-outside/compose.yml")
        self.assertTrue([line for line in said if line.startswith("hostile-build-outside: build context ../../../../.. is outside the folder of this unit: keep what it reads")], said)
        said = self.said("hostile", "addons/t11-bind-lab-state/compose.yml")
        self.assertTrue([line for line in said if line.startswith("hostile-lab-state: bind ../../../../../.env is outside the folder of this unit")], said)
        # A bind outside the project is the policy's alone, and one that says why is nobody's to fix
        self.assertFalse([line for line in self.said("hostile", "addons/t11-bind-outside/compose.yml") if "outside the folder" in line])
        self.assertFalse([line for line in self.said("hostile", "addons/h-privileged-declared/compose.yml") if "outside the folder" in line])

    def test_a_unit_the_screen_refused_is_told_what_the_screen_reads(self):
        words = ("the screen reads words, not YAML: include:, extends:, env_file: and label_file: are read as the keys they "
                 "spell wherever they are written, in a quoted text or a command too: spell the word another way, or move the "
                 "text out of the unit file")
        include = self.said("hostile", "addons/t19-absolute-include/compose.yml")
        self.assertEqual(include[0], "Blocked: Includes content from outside the catalog")
        self.assertIn("an include is a list under include: of plain file names, one on each line (- ../../addons/other/"
                      "compose.yml), each the file of a unit of this catalog or of one it declares: no absolute path, address, "
                      "~ or variable, no list in braces and no other file", include)
        self.assertIn(words, include)
        env = self.said("hostile", "addons/t19-env-file-dev-zero/compose.yml")
        self.assertEqual(env[0], "Blocked: Reads files outside its folder")
        self.assertIn("env_file is refused, whatever file it names: Compose reads that file into the environment, where the "
                      "Lab cannot see it. Write the settings under environment: instead", env)
        self.assertIn(words, env)
        # A unit the screen does not refuse is told none of it
        self.assertFalse([line for line in self.said("hostile", "addons/t12-bind-writable/compose.yml") if "screen" in line])

    def test_a_setup_that_includes_a_setup_is_told_it_includes_add_ons_only(self):
        said = self.said("hostile", "setups/t23-includes-a-setup.yml")
        self.assertIn("includes setups/p-pair.yml: only add-ons can be included", said)

    def test_a_catalog_that_includes_from_another_must_declare_it(self):
        # hostile declares partner, and its units include the ledger of partner: all is declared
        self.assertFalse([line for line in self.said("hostile", "catalog.yml") if line.startswith("requires.catalogs")])
        self.assertFalse([line for where, line in self.found("hostile") if "not shared into another" in line])


def addon(name, body, says=""):
    """The compose file of an add-on that a test adds to a catalog: its x-gravitee block, then `body`."""
    return f"x-gravitee: {{kind: addon, name: {name.title()}, category: Other, description: Added by a test{says}}}\n{body}"


AUTHORING = {
    # The names it gives: no prefix, a container that is not gio-<service>, a volume that is not data-<name>
    "addons/api/compose.yml": addon("api", """services:
  server:
    image: busybox
    container_name: my-server
    labels: {io.gravitee.description: Serves}
    networks: [internal]
volumes:
  cache: {}
"""),
    "addons/api/README.md": "# Api\n",
    # The depth of its anchors: three folders short for the seeder, one short for a plugin folder
    "addons/depth/compose.yml": addon("depth", """services:
  depth-app:
    image: busybox
    build: ../../seeder
    pull_policy: never
    volumes:
      - ../../../../plugins/apim:/plugins
      - ../../../../../plugins/apim:/right:ro
      - ./data:/data:ro
    labels: {io.gravitee.description: Wrong depth}
    networks: [internal]
"""),
    "addons/depth/README.md": "# Depth\n",
    "addons/depth/data/.keep": "",
    # Words the screen reads
    "addons/words/compose.yml": addon("words", """services:
  words-app:
    image: busybox
    command: ["echo", "env_file: here"]
"""),
    "addons/envy/compose.yml": addon("envy", """services:
  envy-app:
    image: busybox
    env_file: .env
"""),
    # An include of a file that is no unit, which the Lab does not follow, and a unit that includes that unit
    "notes.yml": "services: {}\n",
    "addons/listy/compose.yml": addon("listy", """include:
  - ../../notes.yml
services:
  listy-app:
    image: busybox
"""),
    "addons/reach/compose.yml": addon("reach", """include:
  - ../listy/compose.yml
services:
  reach-app:
    image: busybox
"""),
    # A unit that is fine, to see that none of this is said of it
    "addons/fine/compose.yml": addon("fine", """services:
  fine-app:
    image: busybox
    container_name: gio-fine-app
    labels: {io.gravitee.description: Fine}
    networks: [internal]
volumes:
  data-fine: {}
"""),
    "addons/fine/README.md": "# Fine\n",
    # An x-gravitee that is a list, a text and a number: the project says it, and the check goes on
    "addons/xlist/compose.yml": "x-gravitee: [1, 2]\nservices:\n  xlist-app:\n    image: busybox\n",
    "addons/xtext/compose.yml": "x-gravitee: hello\nservices:\n  xtext-app:\n    image: busybox\n",
    "addons/xnumber/compose.yml": "x-gravitee: 7\nservices:\n  xnumber-app:\n    image: busybox\n",
    # A level that is none, in an add-on and in a setup
    "addons/oldlab/compose.yml": addon("oldlab", "services:\n  oldlab-app:\n    image: busybox\n", ", requires: {lab: x}"),
    "addons/oldlab/README.md": "# Oldlab\n",
    "setups/oldlab.yml": "x-gravitee: {kind: setup, name: Oldlab, category: Other, requires: {lab: 1.5}}\n"
                         "include:\n  - ../addons/fine/compose.yml\n",
    # A playground whose seed is a link and whose .env is a pipe
    "playgrounds/trap/compose.yml": """x-gravitee: {kind: playground, name: Trap, category: Other, description: Has a link, tags: [A], duration: 1 min, first_start: 1 min}
services:
  trap-app:
    image: busybox
    labels: {io.gravitee.description: Trap}
""",
    "playgrounds/trap/README.md": "# Trap\n",
    "playgrounds/trap/guide.md": "## 1. One\n",
}


class Authoring(Shared):
    """What an author is told, on a catalog that is not the engine's (so its names carry its prefix): the names, where the
    anchors are, what the screen reads, what the Lab does not follow."""

    CATALOGS = {"partner": {"files": AUTHORING}}

    @classmethod
    def prepare(cls, case):
        trap = case.root / ".lab/catalogs/partner/playgrounds/trap"
        (trap / "seed").symlink_to("/etc")
        os.mkfifo(trap / ".env")

    def test_a_service_that_a_unit_owns_carries_the_prefix_of_its_catalog_or_the_name_of_its_unit(self):
        said = self.said("partner", "addons/api/compose.yml")
        self.assertIn("server: name it partner-server, or start it with the name of the unit, api-", said)
        self.assertEqual([line for line in self.said("partner", "addons/fine/compose.yml")], [])
        # The unit's own name, and the prefix of the catalog, pass
        self.assertFalse([line for line in self.said("partner", "addons/depth/compose.yml") if "name it" in line])

    def test_a_listed_catalog_owes_no_prefix_it_claims_its_names_first(self):
        unit = self.built["addons"]["@partner/api"]
        raw = self.inputs["raw"]
        self.assertEqual(list(check._naming("@partner/api", unit, raw, "partner", True))[0],
                         "server: container_name is my-server: write gio-server")
        self.assertEqual(len(list(check._naming("@partner/api", unit, raw, "partner", False))), 3)

    def test_a_container_is_gio_and_the_service_a_volume_is_data_and_what_it_keeps(self):
        said = self.said("partner", "addons/api/compose.yml")
        self.assertIn("server: container_name is my-server: write gio-server", said)
        self.assertIn("volume cache: name a volume it declares data-<what it keeps>", said)

    def test_a_path_three_folders_short_says_where_the_anchor_is_and_the_right_one_passes(self):
        said = self.said("partner", "addons/depth/compose.yml")
        self.assertIn("depth-app: build context ../../seeder is outside the folder of this unit: the seeder is "
                      "../../../../../seeder from the folder of a unit", said)
        self.assertIn("depth-app: bind ../../../../plugins/apim is outside the folder of this unit: the plugin folder of apim is "
                      "../../../../../plugins/apim from the folder of a unit", said)
        self.assertEqual(len([line for line in said if "outside the folder" in line]), 2, "the right depth and ./data pass")

    def test_a_build_from_a_repository_is_no_folder_to_find(self):
        pinned = "https://example.invalid/app.git#" + "a" * 40
        unit, folder = {"services": ["svc"]}, self.root / ".lab/catalogs/partner/addons/api"
        config = {"services": {"svc": {"build": {"context": pinned}}}}
        anchors = {"project": os.path.realpath(self.root), "seeder": os.path.realpath(self.root / "seeder"), "plugins": [],
                   "closure": []}
        self.assertEqual(list(check._geometry(unit, config, folder, {}, anchors)), [])
        for context in ("git@example.invalid:team/app.git", "https://example.invalid/app.git#main"):
            config["services"]["svc"]["build"]["context"] = context
            self.assertEqual(list(check._geometry(unit, config, folder, {}, anchors)), [], context)
        config["services"]["svc"]["build"]["context"] = str(self.root / "somewhere")
        self.assertEqual(len(list(check._geometry(unit, config, folder, {}, anchors))), 1, "a folder of the project is not its own")

    def test_a_unit_the_screen_refused_for_a_word_is_told_which_word(self):
        words = [line for line in self.said("partner", "addons/words/compose.yml") if line.startswith("env_file is refused")]
        self.assertEqual(len(words), 1)
        envy = self.said("partner", "addons/envy/compose.yml")
        self.assertIn("Blocked: Reads files outside its folder", envy)
        self.assertTrue([line for line in envy if line.startswith("env_file is refused, whatever file it names")])
        self.assertTrue([line for line in envy if line.startswith("the screen reads words, not YAML")])
        # The words are told once, and a unit the screen accepts is told none
        self.assertEqual(len([line for line in envy if line.startswith("the screen reads words")]), 1)
        self.assertEqual([line for line in self.said("partner", "addons/fine/compose.yml") if "screen" in line], [])

    def test_an_include_of_a_file_that_is_no_unit_is_told(self):
        said = self.said("partner", "addons/listy/compose.yml")
        self.assertIn("Blocked: Includes content from outside the catalog", said)
        self.assertIn("includes notes.yml, which the Lab does not follow: it reads an included file only when it is a unit "
                      "written with lists of plain file names, in a folder whose .env is a plain file", said)

    def test_a_unit_that_includes_one_the_lab_does_not_follow_is_told_which(self):
        said = self.said("partner", "addons/reach/compose.yml")
        self.assertIn("Blocked: Includes content from outside the catalog", said)
        self.assertIn("includes notes.yml, which the Lab does not follow: it reads an included file only when it is a unit "
                      "written with lists of plain file names, in a folder whose .env is a plain file", said)

    def test_an_x_gravitee_that_is_no_mapping_is_told_by_the_project_and_the_check_goes_on(self):
        # A list, a text or a number in its place used to stop the whole check with a traceback, and no report
        for name in ("xlist", "xtext", "xnumber"):
            with self.subTest(name=name):
                said = self.said("partner", f"addons/{name}/compose.yml")
                self.assertIn("x-gravitee must be a mapping", said)
                self.assertIn("x-gravitee.kind must be addon", said)

    def test_a_level_that_is_none_is_told_of_an_add_on_and_of_a_setup_as_of_a_playground(self):
        level = "x-gravitee.requires.lab must be a whole number from 1: older Gravitee Labs read it, and an item does without it"
        self.assertEqual(self.said("partner", "addons/oldlab/compose.yml").count(level), 1)
        self.assertEqual(self.said("partner", "setups/oldlab.yml").count(level), 1)
        self.assertNotIn(level, self.said("partner", "addons/fine/compose.yml"))

    def test_a_seed_that_is_a_link_and_a_pipe_for_an_env_are_told_where(self):
        said = self.said("partner", "playgrounds/trap/compose.yml")
        self.assertIn("playgrounds/trap/seed is a link: the Lab does not follow links, so the unit is refused", said)
        self.assertIn("playgrounds/trap/.env is a link or a pipe, not a plain file: the Lab does not read it, so the unit is "
                      "refused", said)
        self.assertTrue([line for line in said if line.startswith("Blocked: ")])


class FreshClone(Shared):
    """A clone of the engine in which `make init` has not run, as in the CI of a catalog: no plugin folder is there, and the
    contract names the anchors all the same, so that a unit that binds one where it is meant to be is not told where it is."""

    CATALOGS = {"partner": {"files": {
        "addons/hybrid/compose.yml": addon("hybrid", """services:
  hybrid-gateway:
    image: busybox
    labels: {io.gravitee.description: Gateway}
    volumes:
      - ../../../../../plugins/apim:/opt/plugins:ro
"""),
        "addons/hybrid/README.md": "# Hybrid\n"}}}

    @classmethod
    def prepare(cls, case):
        shutil.rmtree(case.root / "plugins")

    def test_a_plugin_folder_that_is_not_made_yet_is_still_the_anchor_the_contract_names(self):
        self.assertFalse((self.root / "plugins").exists())
        said = self.said("partner", "addons/hybrid/compose.yml")
        self.assertEqual([line for line in said if "outside the folder" in line or "host_access" in line], [])

    def test_the_right_depth_is_still_told_for_a_folder_that_is_not_an_anchor(self):
        unit, folder = {"services": ["hybrid-gateway"]}, self.root / ".lab/catalogs/partner/addons/hybrid"
        config = {"services": {"hybrid-gateway": {"volumes": [{"type": "bind", "source": str(self.root / "plugins/other")}]}}}
        anchors = {**check._anchors(check._anchored([])), "closure": []}
        [said] = check._geometry(unit, config, folder, {}, anchors)
        self.assertIn("is outside the folder of this unit", said)
        self.assertEqual(list(check._geometry(
            unit, {"services": {"hybrid-gateway": {"volumes": [{"type": "bind", "source": str(self.root / "plugins/apim")}]}}},
            folder, {}, anchors)), [])


class Dependencies(unittest.TestCase):

    def test_a_catalog_cannot_require_itself(self):
        meta = {"requires": {"catalogs": {"fixture": {}, "partner": {}}}}
        self.assertEqual(list(check._dependencies(meta, [], {}, "fixture")), [
            "requires.catalogs.fixture is this catalog itself: remove it",
            "requires.catalogs.partner is declared and no unit includes from it: remove it"])
        self.assertEqual(list(check._dependencies({"requires": {"catalogs": {"partner": {}}}}, [], {}, "fixture")), [
            "requires.catalogs.partner is declared and no unit includes from it: remove it"])


class Closure(unittest.TestCase):
    """What an author is told when a unit is refused for what its closure reaches."""

    FILE = ".lab/catalogs/c/addons/a/compose.yml"

    def said(self, reach):
        return list(check._unsafe_notes(self.FILE, reach, lambda path: check._shown(path, "c")))

    def test_nothing_unsafe_says_nothing(self):
        self.assertEqual(self.said({"unsafe": []}), [])
        self.assertEqual(self.said(None), [])

    def test_the_unit_s_own_env_and_the_env_of_a_unit_it_includes_are_not_the_same_fix(self):
        self.assertEqual(self.said({"unsafe": [".lab/catalogs/c/addons/a/.env"]}),
                         ["its .env is a link or a pipe: make it a plain file"])
        self.assertEqual(self.said({"unsafe": [".lab/catalogs/c/addons/b/.env"]}),
                         ["includes a unit whose .env (addons/b/.env) is a link or a pipe: make it a plain file"])

    def test_a_closure_of_too_many_files_is_a_bomb_and_a_file_of_its_own_include_list_is_told_how_to_write_it(self):
        self.assertEqual(self.said({"unsafe": [".lab/catalogs/c/addons/z/compose.yml"], "bomb": ".lab/catalogs/c/addons/z/compose.yml"}),
                         [f"its includes reach more than {check.REACH_LIMIT} files in all, which is taken for a bomb: include fewer"])
        said = self.said({"unsafe": [self.FILE]})
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("an include is a list under include: of plain file names, one on each line"))


class Names(unittest.TestCase):
    """What a folder holds that the Lab refuses to read, found without following a link."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()

    def write(self, path, text="x"):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def test_a_folder_of_plain_files_is_read(self):
        self.write("compose.yml")
        self.write("guide.md")
        self.write("seed/apim/apis/one.json")
        self.write("seed/.hidden/ignored.json")
        self.assertEqual(list(check._not_read(self.root, "addons/x")), [])
        self.assertEqual(list(check._not_read(self.root / "nothing", "addons/x")), [])

    def test_a_link_in_place_of_the_folder_or_of_a_file_or_inside_the_seed_is_told(self):
        self.write("compose.yml")
        (self.root / "guide.md").symlink_to(self.root / "compose.yml")
        self.write("seed/one.json")
        (self.root / "seed/two.json").symlink_to("/etc/hosts")
        said = list(check._not_read(self.root, "playgrounds/x"))
        self.assertEqual(said, [
            "playgrounds/x/guide.md is a link or a pipe, not a plain file: the Lab does not read it, so the unit is refused",
            "playgrounds/x/seed/two.json is a link or a pipe: the Lab does not follow it, so the unit is refused"])
        elsewhere = self.root.parent / f"{self.root.name}-link"
        elsewhere.symlink_to(self.root)
        self.addCleanup(elsewhere.unlink)
        self.assertEqual(list(check._not_read(elsewhere, "playgrounds/x")),
                         ["playgrounds/x is a link: the Lab does not follow links, so the unit is refused"])

    def test_a_file_over_1_mb_is_told(self):
        self.write("compose.yml", "x" * (check.BIG_FILE + 1))
        self.write("seed/big.json", "y" * (check.BIG_FILE + 1))
        self.assertEqual(list(check._not_read(self.root, "addons/x")), [
            "addons/x/compose.yml is over 1 MB: the Lab does not read more than that of a file, so the unit is refused",
            "addons/x/seed/big.json is over 1 MB: the Lab does not read more than that of a file, so the unit is refused"])

    def test_a_text_that_is_not_utf_8_or_is_too_big_is_not_read(self):
        (self.root / "a").write_bytes(b"\xff\xfe\x00")
        self.write("b", "x" * (check.BIG_FILE + 1))
        os.mkfifo(self.root / "c")
        for name in "abc":
            self.assertIsNone(check._read(self.root / name), name)
        self.write("d", "fine")
        self.assertEqual(check._read(self.root / "d"), "fine")
        self.assertIsNone(check._read(self.root / "nowhere"))

    def test_a_guide_a_seed_or_a_table_that_is_a_link_is_never_followed_to_be_read(self):
        # What a catalog holds is the author's and, in CI, a stranger's: nothing the check reads leads out of the folder
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "guide.md").write_text("## 2. Two\n", encoding="utf-8")
        (elsewhere / "api.json").write_text("not json", encoding="utf-8")
        (elsewhere / "README.md").write_text("| [Ghost](ghost/) | Gone |\n", encoding="utf-8")
        unit = self.root / "unit"
        (unit / "seed/apim/apis").mkdir(parents=True)
        (unit / "guide.md").symlink_to(elsewhere / "guide.md")
        (unit / "seed/apim/apis/one.json").symlink_to(elsewhere / "api.json")
        (unit / "addons").mkdir()
        (unit / "addons/README.md").symlink_to(elsewhere / "README.md")
        self.assertEqual(list(check._guide(unit)), [])
        self.assertEqual(list(check._seed(unit)), [])
        self.assertEqual(list(check._table("addon", [], unit)), [])
        (unit / "guide.md").unlink()
        (unit / "guide.md").write_text("## 2. Two\n", encoding="utf-8")
        self.assertEqual(list(check._guide(unit)), ["guide.md: number its steps 1, 2, 3 in order, not 2"])
        big = unit / "seed/apim/apis/big.json"
        big.write_text(" " * (check.BIG_FILE + 1), encoding="utf-8")
        self.assertEqual(list(check._seed(unit)), [], "a file over 1 MB is not read: the line about its size is `_not_read`'s")

    def test_the_words_the_screen_reads_are_found_wherever_they_are_spelled(self):
        # Escapes of a double-quoted scalar spell a word too, and a flow include is no block list
        text = 'services:\n  a:\n    "env_\\x66ile": x\n'
        said = list(check._screen_notes(text, ".lab/catalogs/c/addons/a/compose.yml"))
        self.assertTrue(said[0].startswith("env_file is refused"), said)
        both = "include:\n  - /etc/x.yml\nservices:\n  a:\n    extends: {file: other.yml}\n    env_file: [a.env]\n    label_file: b\n"
        said = list(check._screen_notes(both, ".lab/catalogs/c/addons/a/compose.yml"))
        self.assertTrue(said[0].startswith("env_file and label_file are refused, whatever file they name"), said)
        self.assertTrue(said[1].startswith("an include is a list under include:"), said)
        self.assertEqual(said[2], "extends of another file is refused: include the unit that has the service, or write the service out")
        self.assertTrue(said[3].startswith("the screen reads words, not YAML"))
        self.assertEqual(list(check._screen_notes("services:\n  a:\n    image: x\n", "a.yml")), [])
        self.assertEqual(list(check._screen_notes(None, "a.yml")), [])


@COMPOSE
class Declarations(Case):
    """Everything catalog.yml says, on a catalog read with Compose."""

    DESCRIPTOR = """x-gravitee-catalog:
  schema: 1
  id: fixture
  name: Fixture catalog
  description: Synthetic units for the tests of the Lab
  version: 1.0.0
  lab: 1
  license: AGPL-3.0-only
  images: ghcr.io/acme/hub
  colour: red
  canary:
    - playground:ghost
    - playground:tour
  requires:
    catalogs:
      partner: {name: Partner fixture, url: "https://example.invalid/partner.git", min: 1.0.0}
      lab-new: {name: Newer, url: "https://example.invalid/new.git", min: 1.0.0}
"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()
        cls.addClassCleanup(holder.doCleanups)
        fixtures.project(holder, paths, check, project, compose, envfiles, files={"catalog.yml": cls.DESCRIPTOR})
        shutil.copy(REPO / check.LOCK_FILE, holder.root / check.LOCK_FILE)
        fixtures.install(holder, "partner")
        fixtures.install(holder, "lab-new")
        holder.patch(license, "status_of", lambda variable: None)
        project.forget()
        cls.inputs = project.gather()
        cls.built = project.build(cls.inputs)

    def test_what_catalog_yml_declares_is_checked_against_what_the_units_do(self):
        found = check.catalog_problems("fixture", self.built, self.inputs)
        said = [line for where, line in found if where == "catalog.yml"]
        self.assertEqual(sorted(said), [
            "canary: playground:ghost is not an item of this catalog",
            "colour is not a key of x-gravitee-catalog: " + ", ".join(manifest.KEYS),
            "requires.catalogs.lab-new is declared and no unit includes from it: remove it"])

    def test_the_images_a_catalog_builds_are_the_audit_s_to_judge_and_the_items_say_nothing_of_them(self):
        found = check.catalog_problems("fixture", self.built, self.inputs)
        self.assertEqual([line for where, line in found if "CI publishes" in line or "CI never publishes" in line], [])
        self.assertFalse(hasattr(check, "_registry"), "the registry-prefix rule is IM02 and IM04 now")

    def test_the_units_go_to_the_audit_with_the_services_they_define_and_the_contexts_as_their_files_wrote_them(self):
        from lab_ui.catalogs import audit
        units = {(one["kind"], one["id"]): one for one in check.audit_units("fixture", self.built, self.inputs)}
        shop = units[("playground", "shop")]
        self.assertEqual((shop["file"], sorted(shop["config"]["services"])), ("playgrounds/shop/compose.yml",
                                                                              ["fixture-shop-seed", "fixture-shop-web"]))
        build = shop["config"]["services"]["fixture-shop-web"]["build"]
        self.assertEqual(build if isinstance(build, str) else build["context"], "apps/web", "relative to the unit")
        seed = shop["config"]["services"]["fixture-shop-seed"]["build"]
        self.assertEqual(seed if isinstance(seed, str) else seed["context"], "../../../../../seeder")
        # What the audit does with them: the image outside the prefix is IM02, the Lab's seeder is the one anchor
        result = audit.run(self.holder.root / paths.CATALOGS_DIR / "fixture", audit.Options(families=("IM",), units=list(units.values())))
        said = [(one.code, one.path, one.message) for one in result.problems]
        self.assertTrue(any(code == "IM02" and "fixture-shop-web" in message and "ghcr.io/acme/hub" in message
                            for code, _, message in said), said)
        self.assertFalse([one for one in said if "seed" in one[2]], "the seeder is the Lab's, never published")
