"""personal: what is created from a few fields, kept on this machine or in a catalog that is a folder, and deleted, in a
temporary project folder that holds the small project of test_layers (the fixture catalog is the default one: its ids are
bare), with docker compose replaced. Sharing is catalogs/contribute.py's, and its tests are test_contribute's. No Docker."""

import json
import os
import re
import shlex
import shutil
import unittest
from unittest import mock

from lab_ui.catalogs import cli as catalog

from lab_ui.library import check, layers, layout, moves, personal
from lab_ui.docker import compose, envfiles, files
from lab_ui import paths
from lab_ui.catalogs.registry import CatalogError

from .. import Case, fixtures, gitfixture
from .test_layers import EXPORTER, lab, own

LOCAL = ".lab/catalogs/local"
FIXTURE = ".lab/catalogs/fixture"
WHOAMI, SANDBOX, MINE = (f"{LOCAL}/addons/whoami/compose.yml", f"{LOCAL}/playgrounds/sandbox/compose.yml",
                         f"{LOCAL}/setups/mine.yml")


class Folder(Case):
    """The project's files, empty, and Compose accepting what it is given to check. The units of the small project are files
    of the default catalog, where they would be."""

    def setUp(self):
        self.folder(paths, compose, envfiles, files)
        fixtures.default(self)   # the fixture catalog is the default one: its ids are bare
        self.patch(paths, "_default", (None, None))
        self.compose = self.patch(compose, "run", return_value=(0, "", ""))
        # What runs, as Docker says it: nothing, unless a test says so
        self.containers = self.patch(compose, "containers", return_value=({}, None))
        self.project = lab()
        self.project["services"]["mongodb"]["ports"] = ["27017"]
        # What `make check` finds published: Kafka adds a port to the gateway
        self.patch(check, "publishers", lambda: {"27017": paths.CORE_FILE, "9092": f"{FIXTURE}/addons/kafka/compose.yml"})
        self.placed()

    def placed(self):
        """Every unit of the project has its file where its id puts it."""
        for kind in paths.KINDS:
            for uid, unit in self.project[kind + "s"].items():
                unit["file"] = paths.unit_file(kind, uid)
                if not (paths.ROOT / unit["file"]).exists():
                    self.write(unit["file"])

    def text(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def own(self):
        """Whoami and Sandbox, of this machine, with their files, and the setup Mine."""
        own(self.project)
        self.project["setups"]["local/mine"]["file"] = MINE
        self.write(WHOAMI, "include:\n  - ../../fixture/addons/kiosk/compose.yml\n")
        self.write(SANDBOX, "include:\n  - ../../setups/mine.yml\n  - ../../addons/whoami/compose.yml\n")
        self.write(MINE, "include:\n  - ../../fixture/addons/kiosk/compose.yml\n")
        self.placed()

    def catalog(self, ns="fixture", **how):
        """The catalog `ns` of the fixtures as a folder of the person's (a copy: the repository is not touched)."""
        return fixtures.install(self, ns, "folder", **how)

    def tree(self):
        """Every file of the project folder, to see that nothing else changed."""
        return sorted(file.relative_to(self.root).as_posix() for file in self.root.rglob("*") if file.is_file())


class Setups(Folder):

    def test_save_setup(self):
        saved = personal.save_setup(self.project, "  My  demo ", " Kafka and traces ", ["kafka-desk", "jade"])
        self.assertEqual(saved, {"file": f"{LOCAL}/setups/my-demo.yml", "changes": [], "id": "local/my-demo"})
        self.assertEqual(self.text(saved["file"]),
                         'x-gravitee:\n  kind: setup\n  name: "My demo"\n  description: "Kafka and traces"\n\n'
                         "include:\n  - ../../fixture/addons/jade/compose.yml\n"
                         "  - ../../fixture/addons/kafka-desk/compose.yml\n")
        self.assertEqual(compose.includes(saved["file"]),
                         [f"{FIXTURE}/addons/jade/compose.yml", f"{FIXTURE}/addons/kafka-desk/compose.yml"])

    def test_save_setup_keeps_characters_as_typed(self):
        # Compose's YAML refuses the escapes JSON gives a character outside the basic plane
        name, description = "Démo \N{ROCKET}", 'Say "hi" \\ \N{ROCKET}'
        personal.save_setup(self.project, name, description, ["kiosk"])
        text = self.text(f"{LOCAL}/setups/d-mo.yml")
        self.assertIn(f'  name: "{name}"\n', text)
        self.assertIn('  description: "Say \\"hi\\" \\\\ \N{ROCKET}"\n', text)

    def test_save_setup_renamed_moves_the_file(self):
        self.own()
        # Asked first, it says the other file it writes again, and writes nothing
        before = self.tree()
        said = {"file": f"{LOCAL}/setups/other-name.yml", "changes": [SANDBOX], "id": "local/other-name"}
        self.assertEqual(personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/mine",
                                             check_only=True), said)
        self.assertEqual(self.tree(), before)
        saved = personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/mine")
        self.assertEqual(saved, said)
        self.assertFalse((self.root / MINE).exists())
        self.assertNotIn("description", self.text(f"{LOCAL}/setups/other-name.yml"))
        # The playground that includes it follows it
        self.assertEqual(compose.includes(SANDBOX), [f"{LOCAL}/setups/other-name.yml", WHOAMI])
        # Saved again under its name, it is itself, not another setup that has the name
        self.project["setups"]["local/other-name"] = self.project["setups"].pop("local/mine")
        saved = personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/other-name")
        self.assertEqual(saved, {"file": f"{LOCAL}/setups/other-name.yml", "changes": [], "id": "local/other-name"})

    def test_a_setup_kept_in_a_folder_catalog_is_written_there(self):
        self.catalog("fixture")
        self.own()
        saved = personal.save_setup(self.project, "Team demo", "", ["kiosk"], category="Other", catalog="fixture")
        self.assertEqual(saved, {"file": f"{FIXTURE}/setups/team-demo.yml", "changes": [], "id": "team-demo"},
                         "the first catalog owns the bare ids")
        self.assertEqual(self.text(saved["file"]), 'x-gravitee:\n  kind: setup\n  name: "Team demo"\n'
                                                   "  category: Other\n\ninclude:\n  - ../addons/kiosk/compose.yml\n")
        with self.assertRaisesRegex(ValueError, "Whoami is of this machine: share it first"):
            personal.save_setup(self.project, "Team", "", ["local/whoami"], category="Other", catalog="fixture")
        # Of this machine, a setup includes what it wants
        personal.save_setup(self.project, "Team", "", ["local/whoami"])
        self.assertIn("  - ../addons/whoami/compose.yml\n", self.text(f"{LOCAL}/setups/team.yml"))

    def test_a_setup_of_another_catalog_is_named_with_its_namespace(self):
        self.catalog("partner")
        self.project["addons"]["@partner/ledger"] = {**self.project["addons"]["kiosk"], "id": "@partner/ledger", "name": "Ledger"}
        saved = personal.save_setup(self.project, "Team demo", "", ["@partner/ledger"], category="Other", catalog="partner")
        self.assertEqual((saved["id"], saved["file"]), ("@partner/team-demo", ".lab/catalogs/partner/setups/team-demo.yml"))
        self.assertEqual(self.text(saved["file"]).splitlines()[-1], "  - ../addons/ledger/compose.yml")

    def test_a_catalog_includes_what_it_holds_or_declares_and_nothing_else(self):
        self.catalog("fixture")
        self.catalog("partner")   # declares nothing
        self.project["addons"]["@partner/ledger"] = {**self.project["addons"]["kiosk"], "id": "@partner/ledger", "name": "Ledger"}
        with self.assertRaisesRegex(ValueError, "Kiosk is in a catalog that Partner fixture does not declare"):
            personal.save_setup(self.project, "Team", "", ["kiosk"], category="Other", catalog="partner")
        # The first catalog declares the partner's
        saved = personal.save_setup(self.project, "Team", "", ["@partner/ledger", "kiosk"], category="Other", catalog="fixture")
        self.assertIn("  - ../../partner/addons/ledger/compose.yml\n", self.text(saved["file"]))

    def test_a_setup_is_shared_from_its_menu_not_saved_as_shared(self):
        self.catalog("fixture")
        self.own()
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "Share it from its menu"):
            personal.save_setup(self.project, "Mine", "", ["kiosk"], sid="local/mine", catalog="fixture")
        self.assertEqual(self.tree(), before)

    def test_a_setup_the_on_file_names_keeps_its_name(self):
        # A person may write it there: renamed, the on file would name a file that is gone
        self.own()
        self.write(paths.ON_FILE, f"include:\n  - {MINE}\n")
        before = self.tree()
        with self.assertRaisesRegex(RuntimeError, "Turn it off first"):
            personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/mine")
        self.assertEqual(self.tree(), before)
        # Saved under its name, it stays where the on file finds it
        self.assertEqual(personal.save_setup(self.project, "Mine", "", ["kafka"], sid="local/mine"),
                         {"file": MINE, "changes": [], "id": "local/mine"})

    def test_save_setup_refusals(self):
        self.own()
        for name, addons, sid, message in (
                ("", ["kiosk"], None, "Give the setup a name"),
                ("x" * 61, ["kiosk"], None, "Give the setup a name of 60 characters at most"),
                ("Local", ["kiosk"], None, "Choose another name than local"),
                ("Demo", [], None, "Choose at least one add-on"),
                ("Demo", ["nope"], None, "No such add-on: nope"),
                ("Demo", ["kiosk", None], None, "The add-ons must be a list of ids"),
                ("Demo", "kiosk", None, "The add-ons must be a list of ids"),
                ("Demo", [5], None, "The add-ons must be a list of ids"),
                ("Demo", ["jade", "agent-lens"], None, f"AI Agent Lens and Jade cannot .*: {EXPORTER}"),
                ("Demo", ["kafka-desk", "redpanda"], None, "Kafka and Redpanda cannot run together"),
                ("Demo", ["kiosk"], "observability", "Only your own setups can be changed here"),
                ("Mine", ["kiosk"], None, "Another setup has this name"),
                ("Observability", ["kiosk"], None, "Another setup has this name"),
                ("Costs $5", ["kiosk"], None, r"Write the name without \$")):
            with self.subTest(name=name, addons=addons), self.assertRaisesRegex(ValueError, message):
                personal.save_setup(self.project, name, "", addons, sid)
        with self.assertRaisesRegex(ValueError, "Choose a category"):
            personal.save_setup(self.project, "Demo", "", ["kiosk"], category="Nope")
        # Compose would read a variable in it
        with self.assertRaisesRegex(ValueError, r"Write the description without \$"):
            personal.save_setup(self.project, "Demo", "Traces in ${HOME}", ["kiosk"])


class Addons(Folder):

    def create(self, name="Whoami", description="Says who answers", category="Other", image="traefik/whoami:v1.11",
               port="8098:80", **more):
        return personal.create_addon(self.project, name, description, category, image, port, **more)

    def test_an_add_on_runs_one_image(self):
        created = self.create()
        self.assertEqual(created, {"file": WHOAMI, "id": "local/whoami"})
        self.assertEqual(self.text(WHOAMI), """x-gravitee:
  kind: addon
  name: "Whoami"
  category: Other
  description: "Says who answers"

services:

  whoami:
    image: traefik/whoami:${WHOAMI_VERSION:-v1.11}
    container_name: gio-whoami
    restart: unless-stopped
    ports:
      - "127.0.0.1:8098:80"     # Web UI
    labels:
      io.gravitee.name: "Whoami"
      io.gravitee.description: "Says who answers"
      io.gravitee.console.whoami.name: "Whoami"
      io.gravitee.console.whoami.url: http://localhost:8098
    networks:
      - internal
""")
        self.assertEqual(self.text(f"{LOCAL}/addons/whoami/README.md"), "# Whoami\n\nSays who answers\n")
        # Compose checked it with the project, as the editor does
        self.assertEqual(self.compose.call_args[0][0][-2:], ["config", "-q"])
        self.assertEqual(self.tree().count(WHOAMI), 1)
        self.assertFalse((self.root / f"{LOCAL}/addons/whoami/.compose.yml.check.yml").exists())

    def test_without_a_port_or_a_tag(self):
        self.create("My  tool: v2", image="ghcr.io/acme/tool", port="")
        text = self.text(f"{LOCAL}/addons/my-tool-v2/compose.yml")
        self.assertIn('  name: "My tool: v2"\n', text)
        self.assertIn("    image: ghcr.io/acme/tool:${MY_TOOL_V2_VERSION:-latest}\n", text)
        self.assertIn('    restart: unless-stopped\n    labels:\n      io.gravitee.name: "My tool: v2"\n'
                      '      io.gravitee.description: "Says who answers"\n    networks:\n', text)
        # The container listens on the port it publishes unless another one is given
        self.create("Other", port=8097)
        self.assertIn('      - "127.0.0.1:8097:8097"     # Web UI\n', self.text(f"{LOCAL}/addons/other/compose.yml"))

    def test_kept_in_a_folder_catalog_it_is_written_there_and_its_id_is_the_catalogs(self):
        self.catalog("fixture")
        self.catalog("partner")
        self.assertEqual(self.create(catalog="partner"),
                         {"file": ".lab/catalogs/partner/addons/whoami/compose.yml", "id": "@partner/whoami"})
        self.assertTrue((self.root / ".lab/catalogs/partner/addons/whoami/README.md").is_file())
        # In the first catalog the id is the bare one
        self.assertEqual(self.create("Other", catalog="fixture")["id"], "other")
        # Checked with the project the way a file of a catalog is: from a copy in the scratch folder
        self.assertFalse(list((self.root / ".lab/catalogs/partner/addons/whoami").glob(".*.yml")))

    def test_personal_is_what_no_catalog_and_the_personal_catalog_both_say(self):
        for number, chosen in enumerate((None, "", "local")):
            with self.subTest(catalog=chosen):
                made = self.create(f"Tool {number}", catalog=chosen)
                self.assertEqual(made["id"], f"local/tool-{number}")

    def test_only_a_folder_catalog_that_can_be_written_takes_new_items(self):
        self.catalog("fixture")
        before = self.tree()
        for chosen in ("nope", "acme-hub", "../x", 3):
            with self.subTest(catalog=chosen), self.assertRaises(CatalogError) as raised:
                self.create(catalog=chosen)
            self.assertEqual((raised.exception.code, raised.exception.status), ("not_editable", 400))
        self.assertEqual(raised.exception.message, "3 cannot take new items from the Lab. Choose Personal or a folder catalog.")
        self.assertEqual(self.tree(), before)

    def test_a_catalog_that_is_a_repository_is_not_editable_by_name(self):
        gitfixture.require()
        fixtures.install(self, "partner", "git")
        before = self.tree()
        with self.assertRaises(CatalogError) as raised:
            self.create(catalog="partner")
        self.assertEqual(raised.exception.code, "not_editable")
        self.assertEqual(raised.exception.message, "Partner fixture cannot take new items from the Lab. Choose Personal or a folder catalog.")
        self.assertEqual(self.tree(), before)

    def test_a_folder_that_cannot_be_written_or_is_not_there_is_not_editable_either(self):
        gone = self.catalog("partner")
        with mock.patch("os.access", return_value=False), self.assertRaises(CatalogError) as raised:
            self.create(catalog="partner")
        self.assertEqual(raised.exception.code, "not_editable")
        # The entry stays and the folder is gone: the link leads nowhere
        shutil.rmtree(gone.path)
        with self.assertRaises(CatalogError) as raised:
            self.create(catalog="partner")
        self.assertEqual(raised.exception.code, "not_editable")

    def test_a_link_inside_the_catalog_that_leads_out_of_it_is_not_followed(self):
        self.catalog("partner")
        outside = self.root.parent / "outside-the-catalog"
        outside.mkdir(exist_ok=True)
        self.addCleanup(outside.rmdir)
        (self.root / ".lab/catalogs/partner/addons").rename(self.root / ".lab/catalogs/partner/addons.real")
        os.symlink(outside, self.root / ".lab/catalogs/partner/addons")
        with self.assertRaisesRegex(ValueError, "The folder of the catalog does not hold it"):
            self.create(catalog="partner")
        self.assertEqual(list(outside.iterdir()), [])

    def test_refusals(self):
        self.catalog("fixture")
        self.write(f"{LOCAL}/addons/mine/compose.yml")
        self.write(f"{LOCAL}/addons/leftover/.env", "TOKEN=1\n")
        # Two image versions the project has: the platform's, and the one of an add-on's database
        self.project["services"]["apim-gateway"]["raw_image"] = "graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}"
        self.project["services"]["openfga"]["raw_image"] = "postgres:${OPENFGA_POSTGRES_VERSION:-17.9}"
        before = self.tree()
        for fields, message in (
                ({"name": " "}, "Give the add-on a name of 60 characters at most"),
                ({"name": "x" * 61}, "Give the add-on a name of 60 characters at most"),
                ({"name": "Local"}, "Choose another name than local"),
                ({"name": "Echo"}, "Another add-on has this name"),
                ({"name": "Kafka"}, "The project has a service named kafka"),
                ({"name": "Mine"}, "Another add-on has this name"),
                ({"name": "Mine", "catalog": "fixture"}, "Another add-on has this name"),
                ({"name": "MongoDB"}, "The project has a service named mongodb"),
                ({"name": "3scale"}, "Start the name with a letter"),
                ({"name": "APIM"}, "The project has an image version named APIM_VERSION: choose another name"),
                ({"name": "OpenFGA Postgres"}, "The project has an image version named OPENFGA_POSTGRES_VERSION"),
                ({"name": "Zz $HOME thing"}, r"Write the name without \$"),
                ({"name": "Leftover"}, "A folder named leftover is already there"),
                ({"description": ""}, "Describe the add-on in one line"),
                ({"description": "Sets $JAVA_OPTS on the gateway"}, r"Write the description without \$"),
                ({"category": "Nope"}, "Choose a category"),
                ({"category": None}, "Choose a category"),
                ({"image": "Traefik/Whoami"}, "Give the image as repository:tag"),
                ({"image": "traefik/whoami:v1 --privileged"}, "Give the image as repository:tag"),
                ({"image": None}, "Give the image as repository:tag"),
                ({"port": "80:"}, "Give the port as 8098, or as 8098:80"),
                ({"port": "70000"}, "Give the port as 8098, or as 8098:80"),
                ({"port": "0"}, "Give the port as 8098, or as 8098:80"),
                ({"port": "27017"}, "Port 27017 is published by mongodb"),
                # On the gateway, by the add-on
                ({"port": "9092:80"}, "Port 9092 is published by Kafka")):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                self.create(**fields)
        self.assertEqual(self.tree(), before)

    def test_a_name_another_catalog_has_is_taken_too(self):
        self.catalog("partner")   # holds the add-on Ledger
        with self.assertRaisesRegex(ValueError, "Another add-on has this name"):
            self.create("Ledger")

    def test_what_compose_refuses_leaves_nothing(self):
        before = self.tree()
        self.compose.return_value = (1, "", 'container name "gio-whoami" is already in use by service kafka\n')
        with self.assertRaisesRegex(ValueError, "Invalid compose file: container name .* already in use"):
            self.create()
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / f"{LOCAL}/addons/whoami").exists())

    def test_what_is_written_goes_through_an_atomic_replace(self):
        written = []
        real = paths.write_atomic
        self.patch(paths, "write_atomic", lambda file, text, mode=0o644: (written.append(file.name), real(file, text, mode))[1])
        self.create()
        self.assertEqual(sorted(written), ["README.md", "compose.yml"])


class Playgrounds(Folder):

    def create(self, name="Sandbox", description="A place to try", category="AI", setups=(), addons=(), **more):
        return personal.create_playground(self.project, name, description, category, list(setups), list(addons), **more)

    def test_a_playground_includes_setups_then_add_ons_and_has_a_guide(self):
        self.own()
        (self.root / SANDBOX).unlink()
        (self.root / SANDBOX).parent.rmdir()
        created = self.create(setups=["observability", "local/mine"], addons=["local/whoami", "kafka"])
        self.assertEqual(created, {"file": SANDBOX, "id": "local/sandbox"})
        self.assertEqual(self.text(SANDBOX), """x-gravitee:
  kind: playground
  name: "Sandbox"
  category: AI
  description: "A place to try"

include:
  - ../../setups/mine.yml
  - ../../../fixture/setups/observability.yml
  - ../../../fixture/addons/kafka/compose.yml
  - ../../addons/whoami/compose.yml

services: {}
""")
        self.assertEqual(self.text(f"{LOCAL}/playgrounds/sandbox/guide.md"),
                         "# Sandbox\n\nA place to try\n\n## 1. First step\n")
        self.assertEqual(self.compose.call_args[0][0][-2:], ["config", "-q"])

    def test_kept_in_a_folder_catalog_or_with_nothing_included(self):
        self.catalog("fixture")
        created = self.create(category="Other", setups=["observability"], addons=["kafka"], catalog="fixture")
        self.assertEqual(created, {"file": f"{FIXTURE}/playgrounds/sandbox/compose.yml", "id": "sandbox"})
        self.assertEqual(self.text(created["file"]), 'x-gravitee:\n  kind: playground\n  name: "Sandbox"\n'
                         '  category: Other\n  description: "A place to try"\n\n'
                         "include:\n  - ../../setups/observability.yml\n"
                         "  - ../../addons/kafka/compose.yml\n\nservices: {}\n")
        self.create("Empty")
        self.assertNotIn("include", self.text(f"{LOCAL}/playgrounds/empty/compose.yml"))

    def test_what_a_catalog_keeps_has_a_category_like_an_add_on(self):
        self.catalog("fixture")
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "Choose a category"):
            self.create(category="", catalog="fixture")
        with self.assertRaisesRegex(ValueError, "Choose a category"):
            personal.save_setup(self.project, "Team demo", "", ["kiosk"], catalog="fixture")
        self.assertEqual(self.tree(), before)
        # Of this machine, it may have none
        self.create(category="")
        personal.save_setup(self.project, "Team demo", "", ["kiosk"])

    def test_refusals(self):
        self.catalog("fixture")
        self.own()
        before = self.tree()
        for fields, message in (
                ({"name": ""}, "Give the playground a name of 60 characters at most"),
                ({"name": "Welcome"}, "Another playground has this name"),
                ({"name": "Sandbox"}, "Another playground has this name"),
                ({"name": "Other", "description": " "}, "Describe the playground in one line"),
                ({"name": "Other", "description": "Costs ${ bad"}, r"Write the description without \$"),
                ({"name": "Other", "category": "Nope"}, "Choose a category"),
                ({"name": "Other", "setups": ["nope"]}, "No such setup: nope"),
                ({"name": "Other", "addons": ["nope"]}, "No such add-on: nope"),
                ({"name": "Other", "setups": ["local/mine"], "catalog": "fixture"}, "Mine is of this machine: share it"),
                ({"name": "Other", "addons": ["local/whoami"], "catalog": "fixture"}, "Whoami is of this machine"),
                # Jade comes with the setup, AI Agent Lens is chosen
                ({"name": "Other", "setups": ["observability"], "addons": ["agent-lens"]},
                 f"AI Agent Lens and Jade cannot run together: {EXPORTER}")):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                self.create(**fields)
        self.compose.return_value = (1, "", "services.x must be a mapping\n")
        with self.assertRaisesRegex(ValueError, "Invalid compose file: services.x must be a mapping"):
            self.create("Other")
        self.assertEqual(self.tree(), before)


class Deleted(Folder):

    def setUp(self):
        super().setUp()
        self.own()
        self.write(f"{LOCAL}/addons/whoami/.env", "TOKEN=1\n")
        self.write(f"{LOCAL}/playgrounds/sandbox/seed/apim/apis/one.json", "{}")

    def test_a_setup_is_its_file_an_add_on_or_a_playground_its_folder(self):
        before = self.tree()
        self.assertEqual(personal.delete(self.project, "playground", "local/sandbox"), {})
        self.assertFalse((self.root / f"{LOCAL}/playgrounds/sandbox").exists())
        del self.project["playgrounds"]["local/sandbox"]
        personal.delete(self.project, "addon", "local/whoami")
        self.assertFalse((self.root / f"{LOCAL}/addons/whoami").exists())
        personal.delete(self.project, "setup", "local/mine")
        # Nothing else went
        gone = {WHOAMI, f"{LOCAL}/addons/whoami/.env", SANDBOX, f"{LOCAL}/playgrounds/sandbox/seed/apim/apis/one.json", MINE}
        self.assertEqual(self.tree(), sorted(set(before) - gone))

    def test_it_loses_its_star_and_what_started_well_on_it(self):
        self.write(".lab/starred.json", json.dumps({"playground": ["local/sandbox", "welcome"], "addon": ["local/whoami"]}))
        self.write(".lab/tested.json", json.dumps({"local/sandbox": [{"versions": {}}], "welcome": [{"versions": {}}]}))
        personal.delete(self.project, "playground", "local/sandbox")
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"playground": ["welcome"], "addon": ["local/whoami"]})
        self.assertEqual(sorted(json.loads(self.text(".lab/tested.json"))), ["welcome"])

    def test_only_what_belongs_to_this_machine(self):
        before = self.tree()
        for kind, uid, noun in (("addon", "kafka", "add-ons"), ("addon", "local/../kafka", "add-ons"),
                                ("addon", "local/", "add-ons"), ("addon", "local/whoami/../../kafka", "add-ons"),
                                ("setup", "observability", "setups"), ("setup", "local/../observability", "setups"),
                                ("playground", "welcome", "playgrounds"), ("playground", "local", "playgrounds")):
            with self.subTest(uid=uid), self.assertRaisesRegex(ValueError, f"Only your own {noun} can be deleted here"):
                personal.delete(self.project, kind, uid)
        with self.assertRaisesRegex(paths.NotFound, "No such add-on: local/gone"):
            personal.delete(self.project, "addon", "local/gone")
        self.assertEqual(self.tree(), before)

    def test_not_while_it_is_on_or_used(self):
        before = self.tree()
        with self.assertRaisesRegex(RuntimeError, "Used by Sandbox"):
            personal.delete(self.project, "addon", "local/whoami")
        self.project["playgrounds"]["local/sandbox"]["setups"] = ["local/mine"]
        with self.assertRaisesRegex(RuntimeError, "Used by Sandbox"):
            personal.delete(self.project, "setup", "local/mine")
        # The playground that is on, and the add-on it brings; a setup the on file names itself. Turned off first
        # while nothing of it runs, stopped first while it runs
        self.write(paths.ON_FILE, f"include:\n  - {SANDBOX}\n  - {MINE}\n")
        for kind, uid in (("playground", "local/sandbox"), ("addon", "local/whoami"), ("setup", "local/mine")):
            with self.subTest(uid=uid), self.assertRaisesRegex(RuntimeError, "Turn it off first"):
                personal.delete(self.project, kind, uid)
        self.containers.return_value = ({"whoami": {"state": "running"}, "kiosk": {"state": "running"}}, None)
        for kind, uid in (("playground", "local/sandbox"), ("addon", "local/whoami"), ("setup", "local/mine")):
            with self.subTest(uid=uid), self.assertRaisesRegex(RuntimeError, "Stop it first"):
                personal.delete(self.project, kind, uid)
        self.assertEqual(self.tree(), sorted([*before, paths.ON_FILE]))

    def test_docker_is_asked_only_for_what_is_on(self):
        with self.assertRaisesRegex(RuntimeError, "Used by Sandbox"):
            personal.delete(self.project, "addon", "local/whoami")
        self.containers.assert_not_called()

    def test_what_the_menu_says_of_a_share_and_of_a_delete(self):
        on = {"playground": None, "effective": [], "setups": []}
        self.assertEqual(personal.held(self.project, on, "addon", "local/whoami"), "", "a share is a copy: nothing holds it")
        self.assertEqual(personal.held(self.project, on, "addon", "local/whoami", deleting=True), "Used by Sandbox")
        on = {"playground": "local/sandbox", "effective": ["local/whoami"], "setups": []}
        self.assertEqual(personal.held(self.project, on, "addon", "local/whoami"), "", "not even while it is on")
        self.assertEqual(personal.held(self.project, on, "addon", "local/whoami", deleting=True), "Turn it off first")
        self.assertEqual(personal.held(self.project, on, "addon", "local/whoami", deleting=True, running=True), "Stop it first")


class Waiting(Folder):
    """While the old layout is being moved nothing is created, renamed or deleted: what it would say or check is read
    from files that are not where the on file says, and an item the old on file turns on is not seen as on."""

    def refuses(self, call):
        before, files = self.tree(), {path: (self.root / path).stat().st_mtime_ns for path in self.tree()}
        with self.assertRaises(RuntimeError) as caught:
            call()
        self.assertEqual(str(caught.exception), layers.WAITING)
        self.assertEqual(self.tree(), before)
        self.assertEqual({path: (self.root / path).stat().st_mtime_ns for path in self.tree()}, files)

    def test_nothing_is_created_renamed_or_deleted_while_the_layout_moves(self):
        self.own()
        self.patch(layout, "waiting", return_value=True)
        self.refuses(lambda: personal.create_addon(self.project, "New one", "An add-on", "Other", "busybox:1.36"))
        self.refuses(lambda: personal.save_setup(self.project, "New", "", ["kiosk"]))
        self.refuses(lambda: personal.save_setup(self.project, "New", "", ["kiosk"], check_only=True))
        self.refuses(lambda: personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/mine"))
        self.refuses(lambda: personal.create_playground(self.project, "New one", "A playground"))
        self.refuses(lambda: personal.delete(self.project, "addon", "local/whoami"))
        self.refuses(lambda: personal.delete(self.project, "setup", "local/mine"))

    def test_the_same_calls_work_once_it_has_moved(self):
        self.own()
        waiting = self.patch(layout, "waiting", return_value=True)
        with self.assertRaises(RuntimeError):
            personal.create_addon(self.project, "New one", "An add-on", "Other", "busybox:1.36")
        waiting.return_value = False
        self.assertEqual(personal.create_addon(self.project, "New one", "An add-on", "Other", "busybox:1.36")["id"],
                         "local/new-one")

    def test_a_machine_that_has_a_legacy_line_waits_for_real(self):
        self.own()
        self.write(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n")   # S1: the on file names the old layout
        self.assertTrue(layout.waiting())
        self.refuses(lambda: personal.create_playground(self.project, "New one", "A playground"))
        self.refuses(lambda: personal.delete(self.project, "setup", "local/mine"))
        self.write(".lab/layout.json", '{"layout": 2, "from": 1}\n')   # the move ended
        self.assertFalse(layout.waiting())
        self.assertEqual(personal.delete(self.project, "setup", "local/mine"), {})


class Sharing(Folder):
    """Sharing is a copy into a catalog, which catalogs/contribute.py does: this module answers the check without a destination
    and hands the rest over."""

    def setUp(self):
        super().setUp()
        self.own()

    def test_without_a_destination_a_check_says_where_the_item_is_in_its_own_catalog(self):
        before = self.tree()
        self.assertEqual(personal.share(self.project, "addon", "local/whoami", check_only=True),
                         {"file": "addons/whoami/compose.yml", "problems": [], "changes": []})
        self.assertEqual(personal.share(self.project, "setup", "local/mine", check_only=True)["file"], "setups/mine.yml")
        self.assertEqual(self.tree(), before)

    def test_without_a_destination_there_is_nothing_to_share(self):
        before = self.tree()
        for to in ("", None):
            with self.subTest(to=to), self.assertRaisesRegex(ValueError, "Choose where to share"):
                personal.share(self.project, "addon", "local/whoami", to=to)
        self.assertEqual(self.tree(), before)

    def test_only_what_belongs_to_this_machine(self):
        before = self.tree()
        for kind, uid in (("addon", "kafka"), ("addon", "local/../kafka"), ("setup", "observability")):
            for to in ("", "acme-hub"):
                with self.subTest(uid=uid, to=to), self.assertRaisesRegex(ValueError, "Only your own .* can be shared here"):
                    personal.share(self.project, kind, uid, True, to)
        with self.assertRaises(paths.NotFound):
            personal.share(self.project, "playground", "local/gone", True)
        self.assertEqual(self.tree(), before)

    def test_it_is_not_held_while_it_is_on(self):
        self.write(paths.ON_FILE, f"include:\n  - {WHOAMI}\n  - {MINE}\n")
        for kind, uid in (("addon", "local/whoami"), ("setup", "local/mine")):
            self.assertEqual(personal.share(self.project, kind, uid, True)["problems"], [])

    def test_with_a_destination_it_is_the_business_of_contribute(self):
        shared = mock.Mock(return_value={"branch": "lab/whoami-20261016"})
        with mock.patch("lab_ui.catalogs.contribute.share", shared):
            self.assertEqual(personal.share(self.project, "addon", "local/whoami", False, "acme-hub"),
                             {"branch": "lab/whoami-20261016"})
            personal.share(self.project, "setup", "local/mine", check_only=True, to="acme-hub")
        self.assertEqual(shared.call_args_list, [mock.call(self.project, "addon", "local/whoami", "acme-hub", False),
                                                 mock.call(self.project, "setup", "local/mine", "acme-hub", True)])


class Following(Folder):
    """The items of this machine that include an item follow it to the place it has now."""

    def setUp(self):
        super().setUp()
        self.own()

    def test_an_include_of_what_moved_names_its_new_place(self):
        self.write(MINE, "include:\n  - ../addons/whoami/compose.yml   # mine\n  - ../../fixture/addons/kiosk/compose.yml\n")
        other = f"{FIXTURE}/addons/whoami"
        personal.follow(f"{LOCAL}/addons/whoami", other)
        self.assertEqual(self.text(MINE), "include:\n  - ../../fixture/addons/whoami/compose.yml   # mine\n"
                                          "  - ../../fixture/addons/kiosk/compose.yml\n")
        self.assertEqual(self.text(SANDBOX), "include:\n  - ../../setups/mine.yml\n  - ../../../fixture/addons/whoami/compose.yml\n")
        self.assertEqual(compose.includes(SANDBOX), [MINE, f"{FIXTURE}/addons/whoami/compose.yml"])

    def test_what_names_nothing_that_moved_is_not_written_again(self):
        before = {path: (self.root / path).stat().st_mtime_ns for path in (WHOAMI, MINE)}
        personal.follow(f"{LOCAL}/addons/other", f"{FIXTURE}/addons/other")
        self.assertEqual({path: (self.root / path).stat().st_mtime_ns for path in (WHOAMI, MINE)}, before)

    def test_a_move_is_journaled_and_repeating_it_changes_nothing(self):
        personal.follow(f"{LOCAL}/addons/whoami", f"{FIXTURE}/addons/whoami")
        journal = [entry for entry in moves.read_journal() if entry["op"] == "repoint"]
        self.assertEqual([entry["path"] for entry in journal], [SANDBOX])
        personal.follow(f"{LOCAL}/addons/whoami", f"{FIXTURE}/addons/whoami")
        self.assertEqual(len([entry for entry in moves.read_journal() if entry["op"] == "repoint"]), 1)

    def test_a_setup_moves_as_a_file_and_the_playground_that_includes_it_follows(self):
        personal.follow(MINE, f"{FIXTURE}/setups/mine.yml")
        self.assertIn("  - ../../../fixture/setups/mine.yml\n", self.text(SANDBOX))

    def test_a_file_that_is_a_link_or_is_not_text_is_left(self):
        (self.root / f"{LOCAL}/playgrounds/sandbox/compose.yml").unlink()
        os.symlink(self.root / MINE, self.root / SANDBOX)
        self.write(f"{LOCAL}/addons/binary/compose.yml")
        (self.root / f"{LOCAL}/addons/binary/compose.yml").write_bytes(b"\xff\xfe include")
        personal.follow(f"{LOCAL}/addons/whoami", f"{FIXTURE}/addons/whoami")
        self.assertEqual((self.root / f"{LOCAL}/addons/binary/compose.yml").read_bytes(), b"\xff\xfe include")
        self.assertTrue((self.root / SANDBOX).is_symlink())


class Skills(Folder):

    def test_the_skills_found_on_disk(self):
        self.assertEqual(personal.skills(), {})
        self.write(".agents/skills/new-addon/SKILL.md")
        self.write(".agents/skills/share-item/SKILL.md")
        self.write(".agents/skills/other/SKILL.md")
        self.assertEqual(personal.skills(), {"addon": ".agents/skills/new-addon/SKILL.md",
                                             "share": ".agents/skills/share-item/SKILL.md"})

    def test_a_catalog_has_a_skill_like_the_three_kinds(self):
        self.write(".agents/skills/new-catalog/SKILL.md")
        self.assertEqual(personal.skills(), {"catalog": ".agents/skills/new-catalog/SKILL.md"})
        self.assertEqual(sorted(personal.SKILLS), ["addon", "catalog", "playground", "setup", "share"])


class TheSkills(Case):
    """The skills an AI agent follows, written for the layout of the catalogs: they name its paths, its commands and its
    documents, and satisfy the check of the project that reads them."""

    NAMES = ("new-addon", "new-setup", "new-playground", "new-catalog", "share-item", "prepare-pull-request")

    def skill(self, name):
        return (paths.LAB_UI_DIR.parent / paths.SKILL_FILE.format(name)).read_text(encoding="utf-8")

    def test_the_project_has_them_all_and_the_check_of_its_skills_finds_nothing(self):
        self.assertEqual(list(check._skills()), [])
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertTrue((paths.LAB_UI_DIR.parent / paths.SKILL_FILE.format(name)).is_file())
                self.assertTrue((paths.LAB_UI_DIR.parent / check.STUB_FILE.format(name)).is_file())
        self.assertEqual(sorted(personal.SKILLS.values()), sorted(set(self.NAMES) - {"prepare-pull-request"}))

    def test_a_personal_item_is_where_the_layout_of_the_catalogs_puts_it_and_the_old_layout_is_named_nowhere(self):
        for name in self.NAMES:
            text = self.skill(name)
            with self.subTest(name=name):
                self.assertNotRegex(text, r"(?<!catalogs/)(addons|setups|playgrounds)/local", "the personal folders left the project")
                self.assertNotRegex(text, r"(addons|setups|playgrounds)/AGENTS\.md", "the guides of the kinds moved to docs/units.md")
                self.assertNotRegex(text, r"moving it out of local|shared with the project", "a share is a copy")
        for name in ("new-addon", "new-setup", "new-playground", "share-item"):
            self.assertIn(".lab/catalogs/local/", self.skill(name), name)

    def test_the_commands_they_give_are_commands_of_the_tool_with_arguments_it_takes(self):
        tool = catalog.parser()
        seen = 0
        for name in self.NAMES:
            for found in re.finditer(r"python3 -I lab-ui/catalog\.py ([^`\n]+)", self.skill(name)):
                with self.subTest(name=name, command=found.group(1)):
                    words = shlex.split(re.sub(r"<[^>]+>", "x", found.group(1)))
                    with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
                        tool.parse_args(words)
                    seen += 1
        self.assertGreater(seen, 8)

    def test_they_read_documents_that_are_not_gone_with_the_folders_of_the_kinds(self):
        for name in self.NAMES:
            for found in re.finditer(r"`(docs/[a-z-]+\.md)`", self.skill(name)):
                with self.subTest(name=name, document=found.group(1)):
                    self.assertIn(found.group(1), ("docs/units.md", "docs/playgrounds.md", "docs/catalogs.md", "docs/writing-a-catalog.md"))


if __name__ == "__main__":
    unittest.main()
