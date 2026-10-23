"""`catalog.py ci release-notes`: the notes of a release and its `images.json`.

The notes are made of two deterministic things: the units added or removed between the previous tag and this one, and for each unit
that changed up to five commit subjects, from the function the Lab's Update dialog calls for "What's new". The catalog is a real git
repository with real tags. These tests prove the layout, that it is that function, that a stranger's subject cannot add a link, an
image, a tag or a mention to the notes, that the previous tag is the right one, and that the bytes are the same for the same inputs."""

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import ci, install, store
from lab_ui.catalogs.ci import main as ci_main
from lab_ui.catalogs.ci import release
from tests.data.rules import support

PREFIX = "ghcr.io/acme/lab-hub"


def catalog_yml(version, name="Acme Hub", lab="1"):
    return (f"x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: {name}\n  description: Demos\n  version: {version}\n"
            f"  lab: {lab}\n  images: {PREFIX}\n")


BASE = {"catalog.yml": catalog_yml("1.0.0"), "README.md": "# Hub\n",
        "playgrounds/bank/compose.yml": "services: {}\n", "playgrounds/bank/README.md": "# Bank\n",
        "addons/mock/compose.yml": "services: {}\n", "addons/old/compose.yml": "services: {}\n", "setups/pair.yml": "include: []\n"}


def record(name="bakery-web", digest="1", tags=("sha-abc1234", "latest"), platforms=("linux/amd64",)):
    return {"digest": "sha256:" + digest * 64, "name": f"{PREFIX}/{name}", "platforms": list(platforms), "tags": list(tags)}


class Fixture(support.WithGit):

    def setUp(self):
        super().setUp()
        self.repo = self.work(files=BASE)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.notes, self.images = self.holder / "notes.md", self.holder / "images.json"
        self.records = self.holder / "records"

    def release(self, tag, change=None, message="Release", annotated=False):
        """Commits `change` ({path: text or None to remove}) and tags the commit."""
        for path, content in (change or {}).items():
            if content is None:
                (self.repo / path).unlink()
            else:
                self.put(self.repo, path, content)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        sha = self.git(self.repo, "rev-parse", "HEAD").strip()
        if annotated:
            self.git(self.repo, "tag", "-a", tag, "-m", f"Release {tag}")
        else:
            self.git(self.repo, "tag", tag)
        return sha

    def write_records(self, records):
        self.records.mkdir(exist_ok=True)
        (self.records / "records.json").write_text(json.dumps(records), encoding="utf-8")

    def run_stage(self, tag, *extra, **seams):
        return release.main([str(self.repo), "--tag", tag, "--records", str(self.records), "--notes", str(self.notes), "--images",
                             str(self.images), *extra], self.out, self.err, **seams)

    def text(self):
        return self.notes.read_text(encoding="utf-8")


class Layout(Fixture):

    def test_the_notes_say_what_is_new_what_changed_by_unit_and_what_went(self):
        self.release("v1.0.0")
        self.put(self.repo, "playgrounds/bank/guide.md", "# Guide\n")
        self.commit(self.repo, "Write the guide of the bank")
        self.put(self.repo, "playgrounds/bank/seed/a.json", "{}\n")
        self.commit(self.repo, "Seed the accounts")
        self.put(self.repo, "addons/new-one/compose.yml", "services: {}\n")
        self.commit(self.repo, "Add the new add-on")
        (self.repo / "addons/old/compose.yml").unlink()
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release 1.1.0 and remove the old add-on")
        self.assertEqual(self.run_stage("v1.1.0"), 0, self.err.getvalue())
        self.assertEqual(self.text(), (
            "## Acme Hub 1.1.0\n\nLab level 1\n\n### New\n\n- addon:new-one\n\n### Changed\n\n- playground:bank\n"
            "  - Seed the accounts\n  - Write the guide of the bank\n\n### Removed\n\n- addon:old\n"))
        self.assertIn("Notes for v1.1.0: 1 new, 1 changed, 1 removed, since v1.0.0; 0 images", self.out.getvalue())

    def test_the_first_release_has_every_unit_new_and_nothing_changed(self):
        self.release("v1.0.0")
        self.assertEqual(self.run_stage("v1.0.0"), 0)
        self.assertEqual(self.text(), ("## Acme Hub 1.0.0\n\nLab level 1\n\n### New\n\n- addon:mock\n- addon:old\n- playground:bank\n"
                                       "- setup:pair\n"))
        self.assertIn("since the start", self.out.getvalue())

    def test_a_release_that_changes_no_unit_says_so(self):
        self.release("v1.0.0")
        self.release("v1.0.1", {"README.md": "# Hub 2\n", "catalog.yml": catalog_yml("1.0.1")}, "Fix the readme")
        self.run_stage("v1.0.1")
        self.assertEqual(self.text(), "## Acme Hub 1.0.1\n\nLab level 1\n\nNo add-on, setup or playground changed.\n")

    def test_a_setup_is_a_unit_and_its_file_is_its_folder(self):
        self.release("v1.0.0")
        self.release("v1.1.0", {"setups/pair.yml": "include: []  # changed\n", "catalog.yml": catalog_yml("1.1.0")}, "Rework the pair")
        self.run_stage("v1.1.0")
        self.assertIn("### Changed\n\n- setup:pair\n  - Rework the pair\n", self.text())
        self.assertNotIn("playground:bank", self.text())

    def test_a_unit_that_changed_only_in_a_merge_has_no_subject_and_is_still_listed(self):
        self.release("v1.0.0")
        self.git(self.repo, "checkout", "-q", "-b", "side")
        self.put(self.repo, "addons/mock/extra.txt", "x\n")
        self.commit(self.repo, "Bump a dependency")
        self.git(self.repo, "checkout", "-q", "main")
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release")
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        self.git(self.repo, "tag", "-f", "v1.1.0")
        self.run_stage("v1.1.0")
        self.assertIn("### Changed\n\n- addon:mock\n", self.text())
        self.assertNotIn("Bump", self.text())
        self.assertNotIn("Merge", self.text())

    def test_the_name_and_the_level_come_from_the_catalog_at_the_tag(self):
        self.release("v1.0.0", {"catalog.yml": catalog_yml("1.0.0", name="Hub *Two*", lab="3")})
        self.run_stage("v1.0.0")
        self.assertTrue(self.text().startswith("## Hub \\*Two\\* 1.0.0\n\nLab level 3\n\n"))

    def test_the_subjects_are_at_most_five_each_clipped_to_a_hundred_and_sixty_characters(self):
        self.release("v1.0.0")
        long = "Teach the bank agent to explain every transfer it declines, in plain words, with the reason " + "x" * 80
        for number in range(8):
            self.put(self.repo, "playgrounds/bank/guide.md", f"# {number}\n")
            self.commit(self.repo, long if number == 7 else f"Change {number} of the guide")
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release")
        self.run_stage("v1.1.0")
        subjects = [line[4:] for line in self.text().splitlines() if line.startswith("  - ")]
        self.assertEqual(len(subjects), 5)
        self.assertTrue(all(len(each.replace("\\", "")) <= 160 for each in subjects), subjects)
        self.assertTrue(subjects[0].endswith("…"))


class SameFunction(Fixture):

    def test_the_subjects_are_the_ones_of_the_function_the_update_dialog_calls(self):
        self.assertIs(install.bare, store, "the update plan's whatsnew is store.log, from the module the dialog's plan calls")
        self.release("v1.0.0")
        for number in range(3):
            self.put(self.repo, "playgrounds/bank/guide.md", f"# {number}\n")
            self.commit(self.repo, f"Subject {number}")
        sha = self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release 1.1.0")
        first = self.git(self.repo, "rev-parse", "v1.0.0").strip()
        calls = []

        def spy(*args):
            calls.append(args)
            return store.log(*args)
        self.run_stage("v1.1.0", log=spy)
        git_dir = os.path.join(os.path.realpath(self.repo), ".git")
        self.assertEqual(calls, [(git_dir, first, sha, ["playgrounds/bank"], 5)])
        shown = store.log(git_dir, first, sha, ["playgrounds/bank"], 5)
        self.assertEqual(shown, ["Subject 2", "Subject 1", "Subject 0"])
        self.assertIn("  - " + "\n  - ".join(shown), self.text())
        # and it is the call the dialog's plan makes, with the same arguments
        source = Path(install.__file__).read_text(encoding="utf-8")
        self.assertIn("bare.log(store_path, held[\"pin\"], target, folders)", source)


class Hostile(Fixture):
    """A pull request title is a stranger's text that ends in the notes."""

    def notes_of(self, *subjects):
        self.release("v1.0.0")
        for number, subject in enumerate(subjects):
            self.put(self.repo, "playgrounds/bank/guide.md", f"# {number}\n")
            self.commit(self.repo, subject)
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release 1.1.0")
        self.assertEqual(self.run_stage("v1.1.0"), 0, self.err.getvalue())
        return [line[4:] for line in self.text().splitlines() if line.startswith("  - ")]

    def test_a_link_an_image_a_tag_and_a_mention_cannot_be_added(self):
        found = self.notes_of("[click here](http://evil.example.test/steal)", "![pixel](http://evil.example.test/p.png)",
                              "<script>alert(1)</script> and <img src=x onerror=y>", "Thanks @everyone and @octocat",
                              "Closes #12 and acme/other#7 and https://evil.example.test/x and www.evil.example.test",
                              "A `code` span, *emphasis*, _under_ and ~~strike~~ | pipe")
        text = " ".join(found)
        outside = re.sub(r"`[^`]*`", "", text)   # (what is in a code span is shown as it is, and links nothing)
        self.assertNotIn("](", outside)
        self.assertNotIn("<", re.sub(r"\\<", "", outside))   # (a tag is written \\< and shows as text)
        for each in found:
            for word in each.split():
                if "@" in word or "://" in word or "www." in word or word.startswith("#") or "#" in word and "/" in word:
                    self.assertTrue(word.startswith("`") or word.startswith("(`") or "`" in word, (word, each))
        self.assertIn("`@everyone`", text)
        self.assertIn("`@octocat`", text)
        self.assertIn("`#12`", text)
        self.assertIn("`acme/other#7`", text)

    def test_what_a_subject_holds_that_is_not_text_never_reaches_the_notes(self):
        found = self.notes_of("Fix\x1b[31m the colors\U0000202e of the bank\U0000200b guide", "Two\r\nlines\x07 here ::error::forged")
        text = self.text()
        for bad in ("\x1b", "\U0000202e", "\U0000200b", "\x00", "\x07", "\r"):
            self.assertNotIn(bad, text)
        self.assertEqual(text.count("\n::"), 0)
        self.assertTrue(found)

    def test_a_very_long_subject_is_cut_and_fast(self):
        self.release("v1.0.0")
        self.put(self.repo, "playgrounds/bank/guide.md", "x\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "A" * 100_000)
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release 1.1.0")
        _, spent = support.timed(self.run_stage, "v1.1.0")
        self.assertLess(spent, 5.0)
        self.assertLessEqual(max(len(line) for line in self.text().splitlines()), len("  - ") + release.SHOWN)

    def test_the_plain_form_of_markdown_characters(self):
        self.assertEqual(release.plain("a *b* [c] <d> e_f #g h"), r"a \*b\* \[c\] \<d\> e\_f \#g h")
        self.assertEqual(release.plain("see https://x.example.test/a?b=1 now"), "see `https://x.example.test/a?b=1` now")
        self.assertEqual(release.plain("a`b"), "a\\`b")
        self.assertEqual(release.plain("mail me@x.example.test"), "mail `me@x.example.test`")
        self.assertEqual(release.plain("tick`@x"), "`tick'@x`")


class Tags(Fixture):

    def test_the_previous_tag_is_the_greatest_release_below_this_one_that_leads_to_it(self):
        self.release("v1.0.0")
        self.release("v1.2.0", {"README.md": "# 2\n"})
        self.release("v1.10.0", {"README.md": "# 3\n"})
        self.git(self.repo, "tag", "v1.1")            # not three numbers
        self.git(self.repo, "tag", "vfoo")
        self.git(self.repo, "tag", "v1.5.0-rc1")
        self.git(self.repo, "tag", "release-9.9.9")
        sha = self.release("v1.11.0", {"README.md": "# 4\n"})
        root = str(self.repo)
        self.assertEqual(release.tags_before(root, sha, "v1.11.0"), "v1.10.0")
        self.assertEqual(release.tags_before(root, sha, "v1.10.0"), "v1.2.0")
        self.assertEqual(release.tags_before(root, sha, "v1.2.0"), "v1.0.0")
        self.assertIsNone(release.tags_before(root, sha, "v1.0.0"))

    def test_a_tag_on_another_line_of_history_is_not_a_previous_release(self):
        self.release("v1.0.0")
        self.git(self.repo, "checkout", "-q", "-b", "other")
        self.release("v1.5.0", {"README.md": "# other\n"})
        self.git(self.repo, "checkout", "-q", "main")
        sha = self.release("v2.0.0", {"README.md": "# main\n"})
        self.assertEqual(release.tags_before(str(self.repo), sha, "v2.0.0"), "v1.0.0")

    def test_a_tag_above_this_one_is_not_a_previous_one(self):
        self.release("v1.0.0")
        sha = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.release("v3.0.0", {"README.md": "# 3\n"})
        self.assertIsNone(release.tags_before(str(self.repo), sha, "v1.0.0"))

    def test_an_annotated_tag_is_a_tag(self):
        self.release("v1.0.0", annotated=True)
        self.release("v1.1.0", {"playgrounds/bank/guide.md": "# g\n", "catalog.yml": catalog_yml("1.1.0")}, "Write the guide",
                     annotated=True)
        self.assertEqual(self.run_stage("v1.1.0"), 0, self.err.getvalue())
        self.assertIn("### Changed\n\n- playground:bank\n  - Write the guide\n", self.text())

    def test_a_tag_this_clone_does_not_have_is_not_a_verdict(self):
        self.release("v1.0.0")
        self.assertEqual(self.run_stage("v9.9.9"), 2)
        self.assertIn("the tag v9.9.9 is not in this clone", self.err.getvalue())
        self.assertFalse(self.notes.exists())

    def test_what_is_not_a_version_tag_is_a_usage_error(self):
        for tag in ("1.0.0", "v1.0", "v1.0.0-rc1", "v1.0.0; rm", "main", "--tag", ""):
            with self.subTest(tag=tag):
                self.err = io.StringIO()
                self.assertEqual(self.run_stage(tag), 2)

    def test_the_notes_are_the_same_bytes_for_the_same_inputs(self):
        self.release("v1.0.0")
        self.put(self.repo, "playgrounds/bank/guide.md", "# g\n")
        self.commit(self.repo, "Write the guide")
        self.release("v1.1.0", {"catalog.yml": catalog_yml("1.1.0")}, "Release 1.1.0")
        self.write_records([record("b"), record("a")])
        self.run_stage("v1.1.0")
        first = (self.notes.read_bytes(), self.images.read_bytes())
        self.notes.unlink()
        self.images.unlink()
        self.run_stage("v1.1.0")
        self.assertEqual((self.notes.read_bytes(), self.images.read_bytes()), first)

    def test_a_folder_that_is_not_a_repository_is_not_a_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            code = release.main([folder, "--tag", "v1.0.0", "--records", folder, "--notes", folder + "/n.md", "--images",
                                 folder + "/i.json"], self.out, self.err)
        self.assertEqual(code, 2)


class Images(Fixture):

    def setUp(self):
        super().setUp()
        self.release("v1.0.0")

    def test_images_json_carries_name_digest_tags_and_platforms_by_name(self):
        self.write_records([record("zeta", "2", ("1.0.0", "sha-abc1234"), ("linux/amd64", "linux/arm64")), record("alpha", "1")])
        self.run_stage("v1.0.0")
        text = self.images.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text), {"images": [
            {"digest": "sha256:" + "1" * 64, "name": f"{PREFIX}/alpha", "platforms": ["linux/amd64"], "tags": ["sha-abc1234", "latest"]},
            {"digest": "sha256:" + "2" * 64, "name": f"{PREFIX}/zeta", "platforms": ["linux/amd64", "linux/arm64"],
             "tags": ["1.0.0", "sha-abc1234"]}], "schema": 1, "version": "1.0.0"})
        self.assertEqual(text, json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=True) + "\n")

    def test_no_records_is_an_empty_list_and_a_folder_that_is_not_there_too(self):
        self.run_stage("v1.0.0")
        self.assertEqual(json.loads(self.images.read_text(encoding="utf-8"))["images"], [])
        self.records.mkdir()
        self.run_stage("v1.0.0")
        self.assertEqual(json.loads(self.images.read_text(encoding="utf-8"))["images"], [])

    def test_the_records_may_be_given_as_the_file(self):
        self.write_records([record()])
        code = release.main([str(self.repo), "--tag", "v1.0.0", "--records", str(self.records / "records.json"), "--notes",
                             str(self.notes), "--images", str(self.images)], self.out, self.err)
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(self.images.read_text(encoding="utf-8"))["images"]), 1)

    def test_records_that_are_not_what_publish_writes_are_not_put_in_a_release(self):
        for bad in ("not json", "{}", "[1]", json.dumps([{"name": "x"}]),
                    json.dumps([{**record(), "digest": "sha256:short"}]), json.dumps([{**record(), "name": "UPPER/Name"}]),
                    json.dumps([{**record(), "tags": "latest"}]), json.dumps([{**record(), "platforms": [1]}])):
            with self.subTest(bad=bad[:30]):
                self.records.mkdir(exist_ok=True)
                (self.records / "records.json").write_text(bad, encoding="utf-8")
                self.err = io.StringIO()
                self.assertEqual(self.run_stage("v1.0.0"), 2)
                self.assertIn("the images", self.err.getvalue())

    def test_a_record_carries_nothing_but_its_four_fields(self):
        self.write_records([{**record(), "token": "ghs_" + "x" * 36, "created": "2026-10-17"}])
        self.run_stage("v1.0.0")
        text = self.images.read_text(encoding="utf-8")
        self.assertNotIn("token", text)
        self.assertNotIn("created", text)

    def test_the_summary_of_a_ci_run_holds_the_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = os.path.join(folder, "summary.md")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
                self.run_stage("v1.0.0", "--ci")
            with open(summary, encoding="utf-8") as handle:
                text = handle.read()
        self.assertTrue(text.startswith("### Release notes\n\n## Acme Hub 1.0.0\n"))

    def test_the_notes_replace_a_link_at_their_name_and_do_not_follow_it(self):
        elsewhere = self.holder / "elsewhere.txt"
        elsewhere.write_text("keep", encoding="utf-8")
        os.symlink(elsewhere, self.notes)
        self.run_stage("v1.0.0")
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.notes.is_symlink())

    def test_the_command_line_reaches_the_stage(self):
        self.assertEqual(ci.STAGES["release-notes"], ("release", "main"))
        out = io.StringIO()
        code = ci_main(["release-notes", str(self.repo), "--tag", "v1.0.0", "--records", str(self.records), "--notes", str(self.notes),
                        "--images", str(self.images), "--format", "json"], out, io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["new"], ["addon:mock", "addon:old", "playground:bank", "setup:pair"])
        self.assertEqual(ci_main(["release-notes"], io.StringIO(), io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
