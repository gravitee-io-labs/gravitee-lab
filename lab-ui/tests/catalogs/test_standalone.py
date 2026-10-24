"""catalogs/standalone.py: a playground for Docker Compose alone. `Pieces` are the writing rules, with nothing but Python: the YAML
it writes, the short forms of a service, the guide as plain Markdown, the comments of the settings, the link to a product it leaves
out. `Folder` makes the archive of the fixture's Shop with Compose 5 (a visible skip without it) and reads it as the person who
receives it does: what it holds, what it leaves out, and a Compose file that Compose reads in its own folder, with its own .env."""

import base64
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import standalone
from lab_ui.docker import compose, envfiles, files
from lab_ui.library import check, project

from .. import Case, fixtures


class Pieces(Case):

    def test_a_value_is_plain_when_it_reads_back_as_the_same_text_and_quoted_else(self):
        for value, written in (("mongodb", "mongodb"), ("127.0.0.1:8092:8092", "127.0.0.1:8092:8092"), ("8092", '"8092"'),
                               ("true", '"true"'), ("off", '"off"'), ("", '""'), ("${REDIS:-x}", '"${REDIS:-x}"'),
                               ("a: b", '"a: b"'), ("a #b", '"a #b"'), (" lead", '" lead"'), (True, "true"), (3, "3"), (None, "null"),
                               ("say \"hi\"", '"say \\"hi\\""')):
            with self.subTest(value=value):
                self.assertEqual(standalone._scalar(value), written)

    def test_mappings_and_lists_are_written_in_blocks(self):
        doc = {"services": {"web": {"image": "nginx:1", "ports": ["127.0.0.1:80:80"], "depends_on": {"db": {"condition": "service_healthy"}},
                                    "healthcheck": {"test": ["CMD", "true"]}, "networks": []}},
               "volumes": {"data": {}}}
        self.assertEqual(standalone._yaml(doc), [
            "services:", "  web:", "    image: nginx:1", "    ports:", "      - 127.0.0.1:80:80", "    depends_on:", "      db:",
            "        condition: service_healthy", "    healthcheck:", "      test:", "        - CMD", '        - "true"', "    networks: []",
            "volumes:", "  data: {}"])
        self.assertEqual(standalone._yaml([{"a": "b", "c": "d"}, ["x"]]), ["- a: b", "  c: d", "-", "  - x"])

    def test_a_service_is_written_in_the_forms_a_person_writes(self):
        service = {"command": None, "labels": None, "networks": {"edge": None, "internal": None},
                   "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8193", "protocol": "tcp"},
                             {"target": 53, "protocol": "udp"}],
                   "volumes": [{"type": "bind", "source": "./seed", "target": "/seed", "read_only": True, "bind": {}},
                               {"type": "volume", "source": "data", "target": "/data", "volume": {}},
                               {"type": "tmpfs", "target": "/tmp"}],
                   "depends_on": {"db": {"condition": "service_started", "required": True, "restart": False}},
                   "build": {"context": "./apps/web", "dockerfile": "Dockerfile"}, "image": "web:1", "restart": "no"}
        tidy = standalone._tidy(service)
        self.assertEqual(list(tidy), ["image", "build", "restart", "depends_on", "volumes", "ports", "networks"])
        self.assertEqual(tidy["ports"], ["127.0.0.1:8193:8000", "53/udp"])
        self.assertEqual(tidy["volumes"], ["./seed:/seed:ro", "data:/data", {"type": "tmpfs", "target": "/tmp"}])
        self.assertEqual((tidy["depends_on"], tidy["build"], tidy["networks"]), (["db"], "./apps/web", ["edge", "internal"]))
        kept = standalone._tidy({"depends_on": {"db": {"condition": "service_healthy", "required": True}}})
        self.assertEqual(kept["depends_on"], {"db": {"condition": "service_healthy"}}, "a condition that is not the default stays")

    def test_a_build_from_an_address_is_no_folder_of_this_machine(self):
        for context in ("https://github.com/acme/app.git#0123abc", "git@github.com:acme/app.git", "ssh://git@host/app.git"):
            self.assertTrue(standalone.REMOTE.match(context), context)
        for context in ("/Users/me/lab/apps/web", "./apps/web", "apps/web"):
            self.assertFalse(standalone.REMOTE.match(context), context)

    def test_a_kept_setting_is_the_variable_a_person_sets(self):
        doc = {"env": {"A": "x@@PASS@@y", "B": "@@LICENSE@@"}, "list": ["@@PASS@@"]}
        self.assertEqual(standalone._settled(doc, {"PASS": "demo", "LICENSE": ""}),
                         {"env": {"A": "x${PASS:-demo}y", "B": "${LICENSE}"}, "list": ["${PASS:-demo}"]})

    def test_the_guide_is_markdown_any_viewer_shows(self):
        accounts = {"dana@shop.test": {"username": "dana@shop.test", "password": "${SHOP_PASSWORD:-fixture-dana}"},
                    "keyed": {"username": "keyed", "key": "${SHOP_KEY:-k-1}"}}
        text = ("Click [Try it](action:try-it) as [Dana](account:dana@shop.test), with [the key](account:keyed),"
                " not [Bob](account:bob).\n\n```bash run\ncurl localhost\n```\n\n~~~sh run\nls\n~~~\n")
        self.assertEqual(standalone._guide(text, accounts),
                         "Click **Try it** as **Dana** (user `dana@shop.test`, password `fixture-dana`), with **the key** (user `keyed`, "
                         "key `k-1`), not **Bob**.\n\n```bash\ncurl localhost\n```\n\n~~~sh\nls\n~~~\n")

    def test_a_setting_says_what_it_is_without_what_only_the_lab_has_nor_a_product_left_out(self):
        row = {"comment": "off: the lessons switch them on. Save & apply restarts the app, Re-seed puts them in the new state"}
        self.assertEqual(standalone._comment(row), "off: the lessons switch them on.")
        row = {"comment": "Password of user admin on the internal APIs and on the link to the Alert Engine"}
        self.assertEqual(standalone._comment(row, ["Alert Engine"]), "Password of user admin on the internal APIs")
        self.assertEqual(standalone._comment(row), row["comment"])
        self.assertEqual(standalone._comment({}), "")

    def test_a_product_left_out_is_no_longer_named_by_those_kept(self):
        services = {"gw": {"environment": {"gravitee_alerts_alertengine_enabled": "true", "gravitee_alerts_alertengine_ws_endpoints_0": "x",
                                           "other": "1"}}, "web": {}}
        standalone._without(services, {"alert-engine"})
        self.assertEqual(services["gw"]["environment"], {"other": "1", "gravitee_alerts_alertengine_enabled": "false"})
        services = {"gw": {"environment": {"gravitee_alerts_alertengine_enabled": "true"}}}
        standalone._without(services, set())
        self.assertEqual(services["gw"]["environment"], {"gravitee_alerts_alertengine_enabled": "true"}, "kept, it stays linked")


class Folder(Case):
    """The fixture catalog as the default one; its Shop, which builds an app, seeds API Management and Access Management, asks for a
    plugin, and lists accounts and consoles."""

    def setUp(self):
        fixtures.need_compose()
        if not shutil.which("openssl"):
            self.skipTest("openssl is not installed")
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        certs = paths.ROOT / standalone.CERTS
        certs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).resolve().parents[3] / standalone.CERTS / "generate.sh", certs / "generate.sh")
        (paths.ROOT / "plugins" / "apim" / "fixture-policy-1.2.0.zip").write_bytes(b"PK")
        (paths.ROOT / "plugins" / "apim" / "other-policy-1.0.0.zip").write_bytes(b"PK")
        project.forget()
        self.addCleanup(project.forget)

    def unpacked(self, pid="shop"):
        made = standalone.archive(pid)
        folder = paths.ROOT.parent / "received"
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            names = archive.namelist()
            archive.extractall(folder)
        return made, names, folder / "shop"

    def test_the_plan_says_what_runs_what_is_left_out_and_what_it_needs(self):
        plan = standalone.plan("shop")
        self.assertEqual(plan["file"], "shop-docker-compose.zip")
        self.assertEqual(plan["runs"][-1], "Shop")
        self.assertIn("API Management", plan["runs"])
        self.assertIn("Alert Engine", plan["leaves_out"])
        self.assertEqual((plan["license"], plan["plugins_missing"], plan["private"]), (["fixture-feature"], [], []))
        self.assertIn("FIXTURE_SHOP_CUSTOMER_PASSWORD", plan["settings"])
        self.assertNotIn("FIXTURE_SHOP_PULL_POLICY", plan["settings"], "how its images come is the archive's")
        self.assertNotIn(standalone.KEYSTORE, plan["settings"], "the certificates are made with the default")

    def test_the_folder_holds_the_compose_file_its_settings_the_guide_and_what_the_services_build_or_mount(self):
        _, names, _ = self.unpacked()
        self.assertTrue(all(name.startswith("shop/") for name in names))
        for name in ("shop/docker-compose.yml", "shop/.env.example", "shop/README.md", "shop/shop/apps/web/Dockerfile",
                     "shop/shop/seed/am/domain.yaml", "shop/seeder/Dockerfile", "shop/certs/mtls/server.p12",
                     "shop/plugins/apim/fixture-policy-1.2.0.zip"):
            self.assertIn(name, names)
        self.assertNotIn("shop/plugins/apim/other-policy-1.0.0.zip", names, "a plugin it does not ask for is not carried")
        self.assertFalse([name for name in names if name.endswith(("/.env", "/.state")) or "/.state/" in name])

    def test_the_compose_file_is_the_playground_alone_with_paths_of_its_folder(self):
        _, _, folder = self.unpacked()
        text = (folder / "docker-compose.yml").read_text()
        self.assertTrue(text.startswith("# Shop: "))
        self.assertNotIn(str(paths.ROOT), text)
        for gone in ("container_name", "io.gravitee", standalone.LAB_UI_SCRIPT, "alert-engine:", "  seeder:"):
            self.assertNotIn(gone, text)
        self.assertIn('gravitee_alerts_alertengine_enabled: "false"', text)
        self.assertIn('SHOP_CUSTOMER_KEY: "${FIXTURE_SHOP_CUSTOMER_KEY:-fixture-shop-key}"', text)
        self.assertIn("networks:\n  edge: {}\n  internal: {}\n", text, "no key Compose adds with nothing in it")
        self.assertIn('gravitee_license_key: "${GRAVITEE_LICENSE}"', text)
        self.assertIn(":4.12.0", text, "the version it was tested with")
        self.assertIn("build: ./shop/apps/web", text)
        (folder / ".env").write_text((folder / ".env.example").read_text())
        done = subprocess.run(["docker", "compose", "config", "--quiet"], cwd=folder, capture_output=True, text=True,
                              env=compose.scrubbed_environment())
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_settings_and_the_readme_say_what_the_person_needs(self):
        _, _, folder = self.unpacked()
        env = (folder / ".env.example").read_text()
        self.assertIn("\nGRAVITEE_LICENSE=\n", env)
        self.assertIn("# Password of Dana\nFIXTURE_SHOP_CUSTOMER_PASSWORD=fixture-dana\n", env)
        for frozen in ("_PULL_POLICY=", "APIM_VERSION=", standalone.KEYSTORE):
            self.assertNotIn(frozen, env)
        readme = (folder / "README.md").read_text()
        for said in ("## Before you start", "`fixture-feature`", "## Start it", "docker compose up -d", "## Stop it",
                     "| Shop: Dana (customer) | `dana@shop.example.test` | `fixture-dana` |", "\n**Try it**\n"):
            self.assertIn(said, readme)
        self.assertLess(readme.index("## Start it"), readme.index("## 1. "), "what to do first comes before the first step")
        self.assertNotIn("](action:", readme)

    def test_a_plugin_this_lab_does_not_have_is_said(self):
        (paths.ROOT / "plugins" / "apim" / "fixture-policy-1.2.0.zip").unlink()
        made, _, folder = self.unpacked()
        self.assertEqual(made["plugins_missing"], ["fixture-policy"])
        self.assertIn("**The plugin fixture-policy**", (folder / "README.md").read_text())
