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
        for value, written in (("mongodb", "mongodb"), ("127.0.0.1:8092:8092", '"127.0.0.1:8092:8092"'), ("8092", '"8092"'),
                               ("@scope/pkg", '"@scope/pkg"'), ("foo:", '"foo:"'), ("2026-10-24", '"2026-10-24"'), ("0b101", '"0b101"'),
                               (".inf", '".inf"'), (".NaN", '".NaN"'), ("3s", '"3s"'), ("graviteeio/apim:4.13", "graviteeio/apim:4.13"),
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
            "services:", "  web:", "    image: nginx:1", "    ports:", '      - "127.0.0.1:80:80"', "    depends_on:", "      db:",
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

    def test_a_volume_is_short_only_when_the_short_form_says_all_of_it(self):
        self.assertEqual(standalone._short_volume({"type": "volume", "target": "/app/node_modules", "volume": {}}), "/app/node_modules")
        self.assertEqual(standalone._short_volume({"type": "bind", "source": "./a", "target": "/a", "bind": {"create_host_path": True}}),
                         "./a:/a")
        for kept in ({"type": "volume", "source": "data", "target": "/d", "volume": {"nocopy": True}},
                     {"type": "bind", "source": "./a", "target": "/a", "bind": {"propagation": "rshared"}},
                     {"type": "volume", "source": "data", "target": "/d", "consistency": "cached"}):
            with self.subTest(volume=kept):
                self.assertEqual(standalone._short_volume(dict(kept)), kept)

    def test_a_fenced_block_of_the_guide_is_left_as_written(self):
        text = ("Intro\n\n```md\n## Not a step\n[x](action:x) [y](account:keyed)\n```\n\n## 1. First\n\nClick [Go](action:go)\n\n"
                "```shell-session run\n$ ls\n```\n")
        guide = standalone._guide(text, {"keyed": {"username": "keyed", "key": "k"}})
        self.assertIn("```md\n## Not a step\n[x](action:x) [y](account:keyed)\n```", guide, "a block shows the syntax as it is")
        self.assertIn("Click **Go**", guide)
        self.assertIn("```shell-session\n$ ls\n```", guide)
        self.assertEqual(guide[standalone._first_step(guide):].splitlines()[0], "## 1. First")

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
        self.assertIn("FIXTURE_SHOP_CUSTOMER_KEY", plan["settings"])
        self.assertNotIn("FIXTURE_SHOP_CUSTOMER_PASSWORD", plan["settings"], "a setting that no service reads is no setting of it")
        self.assertNotIn("FIXTURE_SHOP_PULL_POLICY", plan["settings"], "how its images come is the archive's")
        self.assertNotIn(standalone.KEYSTORE, plan["settings"], "the certificates are made with the default")

    def test_a_copy_of_a_private_item_is_said_and_the_file_is_made_once_the_person_saw_it(self):
        from lab_ui.catalogs import moving
        from lab_ui.catalogs.registry import CatalogError
        fixtures.install(self, "partner", kind="git", audience="private")
        fixtures.local(self, "sandbox")
        fixtures.local(self, "scratch")
        project.forget()
        moving.copy("addon", "@partner/ledger")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace("include:\n", "include:\n  - ../ledger/compose.yml\n", 1),
                           encoding="utf-8")
        project.forget()
        from lab_ui.catalogs import contribute
        self.assertEqual(standalone.plan("local/sandbox")["private"], [{"key": contribute._roots("partner")[0], "name": "Partner fixture",
                                                                       "items": ["Ledger"], "item_ids": ["local/ledger"]}])
        with self.assertRaises(CatalogError) as caught:
            standalone.archive("local/sandbox")
        self.assertEqual((caught.exception.code, caught.exception.message), ("export_private", "It now carries private content of "
                                                                             "Partner fixture. Fix: export it again to see what leaves with it."))
        plan = standalone.plan("local/sandbox")
        self.assertTrue(standalone.archive("local/sandbox", private=[f"{o['key']}|{i}" for o in plan["private"] for i in o["item_ids"]])["data"])

    def test_a_copy_of_a_share_of_a_copy_of_a_private_item_is_said_too(self):
        from lab_ui.catalogs import moving
        fixtures.install(self, "partner", kind="git", audience="private")
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1)   # a folder of the person's, which a share wrote to
        shared = paths.ROOT / paths.CATALOGS_DIR / "lab-new" / "addons" / "ledger" / "compose.yml"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text('x-gravitee: {copy_of: "@partner/ledger", kind: addon, name: Ledger, category: Other, description: x}\n'
                          "services: {}\n", encoding="utf-8")
        fixtures.local(self, "sandbox")
        fixtures.local(self, "scratch")
        project.forget()
        moving.copy("addon", "@partner/ledger")
        ledger = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "ledger" / "compose.yml"
        ledger.write_text(ledger.read_text(encoding="utf-8").replace('"@partner/ledger"', '"@lab-new/ledger"'), encoding="utf-8")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace("include:\n", "include:\n  - ../ledger/compose.yml\n", 1),
                           encoding="utf-8")
        project.forget()
        from lab_ui.catalogs import contribute
        self.assertEqual(standalone.plan("local/sandbox")["private"], [{"key": contribute._roots("partner")[0], "name": "Partner fixture",
                                                                       "items": ["Ledger"], "item_ids": ["local/ledger"]}])

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
        self.assertIn("# API key of the shop's customers\nFIXTURE_SHOP_CUSTOMER_KEY=fixture-shop-key\n", env)
        for frozen in ("_PULL_POLICY=", "APIM_VERSION=", standalone.KEYSTORE):
            self.assertNotIn(frozen, env)
        readme = (folder / "README.md").read_text()
        for said in ("## Before you start", "`fixture-feature`", "## Start it", "docker compose up -d", "## Stop it",
                     "| Shop: Dana (customer) | `dana@shop.example.test` | `fixture-dana` |", "\n**Try it**\n"):
            self.assertIn(said, readme)
        self.assertLess(readme.index("## Start it"), readme.index("## 1. "), "what to do first comes before the first step")
        self.assertNotIn("](action:", readme)

    def test_nothing_set_on_this_machine_reaches_the_archive(self):
        (paths.ROOT / ".env").write_text("MTLS_KEYSTORE_PASSWORD=LEAKKEYSTORE\nAPIM_REGISTRY=leak.registry.example\nAM_VERSION=9.9.9-leak\n"
                                         "INTERNAL_API_PASSWORD=leak-internal\n", encoding="utf-8")
        shop = Path(fixtures.unit_file("fixture", "playground", "shop")).parent
        (paths.ROOT / shop).chmod((paths.ROOT / shop).stat().st_mode | 0o200)
        (paths.ROOT / shop / ".env").write_text("FIXTURE_SHOP_PULL_POLICY=always\nFIXTURE_SHOP_CUSTOMER_KEY=leak-key\n", encoding="utf-8")
        project.forget()
        _, _, folder = self.unpacked()
        text = (folder / "docker-compose.yml").read_text() + (folder / ".env.example").read_text() + (folder / "README.md").read_text()
        for leak in ("LEAK", "leak"):
            self.assertNotIn(leak, text)
        self.assertIn("gravitee_servers_1_ssl_keystore_password: gravitee", text, "the password the certificates are made with")
        self.assertIn("pull_policy: build", text)
        self.assertIn('"${INTERNAL_API_PASSWORD:-adminadmin}"', text)

    def test_what_containers_write_and_what_never_travels_stay_here(self):
        shop = paths.ROOT / Path(fixtures.unit_file("fixture", "playground", "shop")).parent
        for each in [shop, *shop.rglob("*")]:   # installed read only, as a catalog is
            each.chmod(each.stat().st_mode | 0o200)
        compose_file = shop / "compose.yml"
        compose_file.write_text(compose_file.read_text().replace(
            "      - ./.state:/state   # What the seed created, to remove it later",
            "      - ./.state:/state   # What the seed created, to remove it later\n      - ./.state/certs:/certs\n      - ./secrets:/output\n"
            "      - ./config/app.yml:/etc/app.yml:ro"), encoding="utf-8")
        for rel, body in ((".state/certs/server.key", "KEY"), ("secrets/kubeconfig.yaml", "KUBE"), ("config/app.yml", "port: 1\n"),
                          ("apps/web/.env.production", "SECRET"), ("apps/web/.git/config", "x"), ("apps/web/.env.example", "A=1\n")):
            (shop / rel).parent.mkdir(parents=True, exist_ok=True)
            (shop / rel).write_text(body, encoding="utf-8")
        outside = paths.ROOT.parent / "outside.txt"
        outside.write_text("OUTSIDE", encoding="utf-8")
        (shop / "apps" / "web" / "link.txt").symlink_to(outside)
        project.forget()
        _, names, folder = self.unpacked()
        for gone in ("shop/shop/.state/certs/server.key", "shop/shop/secrets/kubeconfig.yaml", "shop/shop/apps/web/.env.production",
                     "shop/shop/apps/web/.git/config", "shop/shop/apps/web/link.txt"):
            self.assertNotIn(gone, names)
        self.assertIn("shop/shop/config/app.yml", names, "a file mounted read only is what the service reads: it travels")
        self.assertIn("shop/shop/apps/web/.env.example", names)
        # The folders a container writes to hold nothing in the archive: Compose makes them at the first start, as on a new Lab
        self.assertEqual([name for name in names if "/.state/" in name or "/secrets/" in name], [])

    def test_a_dockerignore_that_lets_files_back_in_is_left_to_the_build(self):
        web = paths.ROOT / Path(fixtures.unit_file("fixture", "playground", "shop")).parent / "apps" / "web"
        web.chmod(web.stat().st_mode | 0o200)
        (web / ".dockerignore").write_text("*\n!Dockerfile\n!index.html\n", encoding="utf-8")
        project.forget()
        _, names, _ = self.unpacked()
        self.assertIn("shop/shop/apps/web/Dockerfile", names)
        self.assertIn("shop/shop/apps/web/index.html", names)

    def test_a_plugin_is_carried_by_its_exact_name_at_its_highest_version(self):
        apim = paths.ROOT / "plugins" / "apim"
        for name in ("fixture-policy-1.10.0.zip", "fixture-policy-extra-9.0.0.zip"):
            (apim / name).write_bytes(b"PK")
        _, names, _ = self.unpacked()
        carried = sorted(name.rsplit("/", 1)[-1] for name in names if name.startswith("shop/plugins/apim/") and name.endswith(".zip"))
        self.assertEqual(carried, ["fixture-policy-1.10.0.zip"])

    def test_a_plugin_this_lab_does_not_have_is_said(self):
        (paths.ROOT / "plugins" / "apim" / "fixture-policy-1.2.0.zip").unlink()
        made, _, folder = self.unpacked()
        self.assertEqual(made["plugins_missing"], ["fixture-policy"])
        self.assertIn("**The plugin fixture-policy**", (folder / "README.md").read_text())
