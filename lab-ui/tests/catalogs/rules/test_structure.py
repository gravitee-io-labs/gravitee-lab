"""ST, the shape of a catalog's tree and its files. For each code one failing fixture, in which only that code fires,
and one that passes; the message equals word for word. The structure rules and the Lab's `store.rejections` are one
definition. The calibration at the end runs every rule of this package over the content that the engine holds today, as a catalog."""

import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from lab_ui.catalogs import audit, store
from lab_ui.catalogs.rules import Context, context, structure
from lab_ui.catalogs.tree import FolderTree, ListTree
from tests.data.rules import support
from tests.data.rules.support import codes, run

BIDI = chr(0x202E)
ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 64
MACHO = b"\xcf\xfa\xed\xfe\x07\x00\x00\x01" + b"\0" * 64
PE = b"MZ" + b"\0" * 58 + (0x80).to_bytes(4, "little") + b"\0" * 64 + b"PE\0\0" + b"\0" * 20
ZIP = b"PK\x03\x04" + b"\0" * 30
TAR = b"\0" * 257 + b"ustar\0" + b"\0" * 100
GZIP = b"\x1f\x8b\x08\0" + b"\0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def one(case, code, path, message, *changes, **how):
    """Runs the rules on the good catalog with the changes, and asks for exactly one finding: this code, this path, this
    message."""
    found = run(structure, *changes, **how)
    case.assertEqual([(code, path, message)], [(each.code, each.path, each.message) for each in found], found)
    return found[0]


def quiet(case, *changes, **how):
    case.assertEqual([], run(structure, *changes, **how))


class Names(unittest.TestCase):
    """ST01 to ST07, ST13 to ST15: the rules that `store.rejections` defines for the Lab."""

    def test_the_good_catalog_is_quiet_with_the_kind_level_guides_in_it(self):
        quiet(self)
        self.assertIn("addons/README.md", support.GOOD)

    def test_st01_a_link(self):
        one(self, "ST01", "docs/link.md", "docs/link.md is a symbolic link. Fix: commit the file itself; the Lab does not "
            "install links.", {"docs/link.md": ("120000", "README.md")})
        found = run(structure, {"addons/hello/data": ("120000", "../x")})
        self.assertEqual({"ST01"}, codes(found))

    def test_st02_a_submodule(self):
        one(self, "ST02", "vendor/lib", "vendor/lib is a submodule. Fix: copy what you need into the catalog; the Lab installs "
            "no submodule.", {"vendor/lib": ("160000", "")})

    def test_st03_a_name_the_lab_cannot_install(self):
        message = "{} has a name the Lab cannot install. Fix: use letters, digits and . _ + - only."
        for path in ("docs/a b.md", "docs/a\\b.md", "docs/a\x01b.md", "docs/é.md", "docs//a.md", "docs/../a.md", "docs/a:b.md",
                     "docs/a\nb.md"):
            with self.subTest(path=path):
                one(self, "ST03", path, message.format(path), {path: "x"})
        quiet(self, {"docs/a-b_c+d.e.md": "x", "docs/.hidden.md": "x"})

    def test_st04_a_path_too_deep_or_too_long(self):
        message = "{} is too deep or too long. Fix: at most 8 folders and 200 characters."
        deep = "docs/a/b/c/d/e/f/g/h.md"
        one(self, "ST04", deep, message.format(deep), {deep: "x"})
        long = "docs/" + "a" * 196 + ".md"
        one(self, "ST04", long, message.format(long), {long: "x"})
        quiet(self, {"docs/a/b/c/d/e/f/g.md": "x", "docs/" + "a" * 190 + ".md": "x"})

    def test_st05_an_environment_file(self):
        message = ("{p} is an environment file. Fix: git rm --cached {p}, add it to .gitignore, and commit a .env.example with "
                   "defaults. If it held a real secret, revoke the secret first: removing the file does not remove it from the "
                   "history.")
        for path in (".env", "addons/hello/.env", "addons/hello/.env.local", "docs/.env.production"):
            with self.subTest(path=path):
                one(self, "ST05", path, message.format(p=path), {path: "A=1\n"})
        quiet(self, {".env.example": "A=\n", "addons/hello/.env.example": "A=\n"})

    def test_st06_a_key_a_certificate_or_machine_state(self):
        message = "{} looks like a key, a certificate or machine state. Fix: make it with a script at start and ignore it. If it is real, revoke it first."
        for path in ("docs/server.pem", "docs/a.key", "docs/a.p12", "docs/a.pfx", "docs/a.jks", "docs/a.keystore", "docs/a.crt",
                     "docs/a.csr", "docs/a.srl", "addons/hello/secrets/token.txt", "addons/hello/.state/created.json",
                     ".state/x", "secrets/x"):
            with self.subTest(path=path):
                one(self, "ST06", path, message.format(path), {path: "x"})
        quiet(self, {"docs/pem.md": "x", "docs/keys.md": "x", "docs/secrets.md": "x"})

    def test_st07_an_agent_instruction_file_inside_an_item(self):
        message = ("{} is an agent instruction file inside an item. Fix: keep it at the top of the catalog or in the kind "
                   "folder; the Lab never installs it.")
        for path in ("addons/hello/AGENTS.md", "addons/hello/CLAUDE.md", "addons/hello/GEMINI.md", "addons/hello/docs/agents.md",
                     "addons/hello/.claude/settings.json", "addons/hello/.agents/skills.md", "addons/hello/.gemini/settings.json",
                     "addons/hello/.cursorrules", "addons/hello/.windsurfrules", "addons/hello/.github/copilot-instructions.md"):
            with self.subTest(path=path):
                one(self, "ST07", path, message.format(path), {path: "x"})

    def test_the_kind_level_guides_and_the_top_of_the_catalog_pass_st07_st08_and_st15_but_the_same_names_in_a_unit_do_not(self):
        quiet(self, {"playgrounds/README.md": "# P\n", "playgrounds/AGENTS.md": "# P\n", "playgrounds/CLAUDE.md": "@AGENTS.md\n",
                     "setups/README.md": "# S\n", "setups/AGENTS.md": "# S\n", "setups/CLAUDE.md": "@AGENTS.md\n"})
        quiet(self, {".claude/skills/x/SKILL.md": "x", ".agents/skills/x/SKILL.md": "x", ".gemini/settings.json": "{}",
                     ".github/copilot-instructions.md": "x", "GEMINI.md": "x"})
        self.assertEqual({"ST07"}, codes(run(structure, {"addons/hello/AGENTS.md": "x", "addons/hello/README.md": "# H\n"})))

    def test_st13_two_paths_equal_when_case_is_folded(self):
        message = "{} differs from {} only by case. Fix: rename one; a Mac cannot hold both."
        one(self, "ST13", "docs/Guide.md", message.format("docs/Guide.md", "docs/guide.md"), {"docs/Guide.md": "x"})
        one(self, "ST13", "Docs", message.format("Docs", "docs"), {"Docs/x.md": "x", "Docs/y.md": "y"})
        found = run(structure, {"docs/a": "file", "docs/A/b.md": "x"})
        self.assertEqual({"ST13"}, codes(found))

    def test_st14_a_unit_not_named_as_a_unit(self):
        message = "{}: a unit is named with lowercase letters, digits and dashes."
        one(self, "ST14", "addons/Bad_Name", message.format("addons/Bad_Name"), {"addons/Bad_Name/compose.yml": "services: {}\n",
                                                                                 "addons/Bad_Name/README.md": "x"})
        one(self, "ST14", "setups/Bad.yml", message.format("setups/Bad.yml"), {"setups/Bad.yml": "include: []\n"})
        quiet(self, {"addons/a-b2/compose.yml": "services: {}\n", "setups/ab-2.yml": "include: []\n"})

    def test_st15_a_file_or_folder_that_is_no_complete_unit(self):
        message = "{} is not a complete unit. Fix: add compose.yml, or delete the folder."
        one(self, "ST15", "playgrounds/empty", message.format("playgrounds/empty"), {"playgrounds/empty/README.md": "x",
                                                                                      "playgrounds/empty/guide.md": "y"})
        one(self, "ST15", "setups/notes.txt", message.format("setups/notes.txt"), {"setups/notes.txt": "x"})
        one(self, "ST15", "addons/loose.txt", message.format("addons/loose.txt"), {"addons/loose.txt": "x"})
        one(self, "ST15", "setups/deep/a.yml", message.format("setups/deep/a.yml"), {"setups/deep/a.yml": "x"})

    def test_the_structure_rules_and_the_stores_filter_are_one_definition(self):
        listing = ListTree.from_files({
            "catalog.yml": support.CATALOG, "addons/a/compose.yml": "x", "addons/a/AGENTS.md": "x", "addons/a/.env": "x",
            "addons/a/k.pem": "x", "addons/a/sub/l": ("120000", "x"), "addons/a/g": ("160000", ""), "addons/b c/compose.yml": "x",
            "addons/B/compose.yml": "x", "addons/README.md": "x", "playgrounds/p/guide.md": "x", "setups/s.txt": "x",
            "addons/a/deep/1/2/3/4/5/6/7.yml": "x", "addons/a/big.md": b"x" * (1024 * 1024 + 1)})
        theirs = {}
        for entry, code in store.rejections([store.Entry(each.mode, "0" * 40, each.size, each.path) for each in listing.entries()]):
            theirs[entry.path] = code
        ours = {each.path: each.code for each in structure.check(listing, context(listing)) if each.code in structure.FROM_STORE}
        for path, code in theirs.items():
            if code in ("ST14", "ST15"):
                code_paths = [place for place, found in ours.items() if found == code and (path == place or path.startswith(place + "/"))]
                self.assertTrue(code_paths, (path, code))
            elif code in structure.FROM_STORE:
                self.assertEqual(code, ours.get(path), path)
        self.assertEqual({"ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST09", "ST14", "ST15"}, set(ours.values()))
        self.assertNotIn("addons/README.md", ours, "a kind-level guide is nobody's file")

    def test_outside_the_places_the_lab_installs_the_same_rules_hold(self):
        for path, code in (("docs/.env", "ST05"), ("tools/id_rsa.key", "ST06"), ("docs/a b.md", "ST03"), ("docs/l", "ST01")):
            content = ("120000", "x") if code == "ST01" else "x"
            self.assertEqual({code}, codes(run(structure, {path: content})), path)


class Sizes(unittest.TestCase):

    def test_st09_a_file_too_large(self):
        one(self, "ST09", "docs/big.md", "docs/big.md is 1025 KB, the limit is 1024 KB. Fix: shrink it, or link to it.",
            {"docs/big.md": b"x" * (1024 * 1024 + 1)})
        one(self, "ST09", "docs/big.png", "docs/big.png is 2049 KB, the limit is 2048 KB. Fix: shrink it, or link to it.",
            {"docs/big.png": PNG + b"\0" * (2 * 1024 * 1024)})
        one(self, "ST09", "addons/hello/data.json", "addons/hello/data.json is 1025 KB, the limit is 1024 KB. Fix: shrink it, or "
            "link to it.", {"addons/hello/data.json": b" " * (1024 * 1024 + 1)})
        quiet(self, {"docs/ok.md": b"x" * (1024 * 1024), "docs/ok.png": PNG + b"\0" * (1024 * 1024 + 500)})

    def test_st10_a_unit_over_its_limits(self):
        many = {f"addons/hello/data/f{number}.txt": "x" for number in range(1999)}
        one(self, "ST10", "addons/hello", "addons/hello holds 2001 files, the limit is 2000. Fix: split it.", many)
        quiet(self, {f"addons/hello/data/f{number}.txt": "x" for number in range(1998)})
        heavy = {f"addons/hello/data/f{number}.bin": b"\0" * (1024 * 1024) for number in range(26)}
        found = run(structure, heavy)
        self.assertEqual([("ST10", "addons/hello", "addons/hello holds 26 MB, the limit is 25 MB. Fix: split it.")],
                         [(each.code, each.path, each.message) for each in found])

    def test_st10_a_catalog_over_its_limits(self):
        units = {f"addons/u{number}/compose.yml": "services: {}\n" for number in range(501)}
        found = run(structure, units)
        self.assertEqual([("ST10", "", "The catalog holds 502 units, the limit is 500. Fix: split it.")],
                         [(each.code, each.path, each.message) for each in found])

    def test_a_path_of_thousands_of_folders_costs_in_proportion_to_its_length(self):
        """ST04 refuses a path of more than 8 folders; the comparison of paths must not cost the square of the depth meanwhile."""
        for depth, count in ((4000, 50), (20000, 1)):
            listing = ListTree.from_files({"/".join(["d"] * depth) + f"/f{number}.txt": "x" for number in range(count)})
            found, seconds = support.timed(structure.check, listing, context(listing))
            self.assertLess(seconds, 1.0, f"{count} paths of {depth} folders took {seconds:.2f} s")
            self.assertIn("ST04", codes(found))
        self.assertEqual([("a/B", "a/b")], structure.case_twins(["a/b/" + "d/" * 3000 + "x", "a/B/" + "d/" * 3000 + "x"]))

    def test_the_most_the_check_reads_of_a_file_is_a_megabyte_so_a_huge_line_is_fast(self):
        line = ("a" * 1000 + chr(0x00E9)) * 1000
        for name, content in (("docs/line.md", line), ("docs/line.txt", line.replace("a", "\r\n")[:1024 * 1024]),
                              (".gitattributes", "filter " * 150000), ("docs/spaces.txt", " " * (1024 * 1024)),
                              ("docs/bidi.txt", (chr(0x200B) + "x") * 500000)):
            listing = support.tree({name: content})
            _, seconds = support.timed(structure.check, listing, context(listing))
            self.assertLess(seconds, 1.0, f"{name} took {seconds:.2f} s")


class Contents(unittest.TestCase):

    def test_st11_an_invisible_character(self):
        message = "docs/note.md:2 holds an invisible character (U+202E). Fix: remove it; a reader cannot see it."
        one(self, "ST11", "docs/note.md", message, {"docs/note.md": f"fine\nthe {BIDI} text\n"})
        for char in (0x200B, 0x200C, 0x200D, 0x2060, 0x2066, 0x2069, 0x061C, 0xE0041):
            with self.subTest(char=hex(char)):
                found = run(structure, {"addons/hello/compose.yml": f"services:\n  a: {chr(char)}b\n"})
                self.assertEqual({"ST11"}, codes(found))
                self.assertEqual(f"(U+{char:04X})", re.search(r"\(U\+[0-9A-F]+\)", found[0].message).group())
                self.assertEqual(2, found[0].line)
        quiet(self, {"docs/ok.md": chr(0xFEFF) + "a byte order mark at the start is no trick\n",
                     "docs/soft.md": "a soft" + chr(0x00AD) + "hyphen, an é, an emoji \U0001F44D and a variation" + chr(0xFE0F) + "\n"})
        self.assertEqual({"ST11"}, codes(run(structure, {"docs/bom.md": "text" + chr(0xFEFF) + "inside\n"})))

    def test_the_class_of_invisible_characters_is_made_of_format_characters(self):
        import unicodedata
        every = [chr(point) for point in range(0x110000) if structure.INVISIBLE.fullmatch(chr(point))]
        self.assertGreater(len(every), 150)
        self.assertEqual([], [hex(ord(char)) for char in every if unicodedata.category(char) not in ("Cf", "Cn")])
        for char in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x200B, 0x200E, 0x200F, 0x2060):
            self.assertTrue(structure.INVISIBLE.fullmatch(chr(char)), hex(char))
        self.assertIsNone(structure.INVISIBLE.fullmatch(chr(0x00AD)))

    def test_st11_behind_a_nul_byte(self):
        """GitHub shows no diff for a file with a NUL in it; a build, a script or a guide reads it all the same."""
        trojan = f"the {BIDI} text\n".encode()
        message = "docs/a.txt:{} holds an invisible character (U+202E). Fix: remove it; a reader cannot see it."
        for label, data, line in (("first", b"\0" + trojan, 1), ("NULs first", b"\0" * 20 + b"\n" + trojan, 2),
                                  ("at 100", b"x\n" * 50 + b"\0\n" + trojan, 52), ("past 8 KB", b"x\n" * 4500 + b"\0\n" + trojan, 4502),
                                  ("inside the character", b"the \xe2\0\x80\xae text\n", 1),
                                  ("UTF-16 with a mark", b"\xff\xfe" + f"a\n{BIDI}\n".encode("utf-16-le"), 2),
                                  ("UTF-16 big endian", b"\xfe\xff" + f"a\n{BIDI}\n".encode("utf-16-be"), 2)):
            with self.subTest(label):
                one(self, "ST11", "docs/a.txt", message.format(line), {"docs/a.txt": data})
        self.assertEqual({"ST11"}, codes(run(structure, {"docs/a.txt": b"\0" + f"x {chr(0x200B)}\n".encode()})))
        quiet(self, {"docs/a.txt": b"\xff\xfe" + "plain words\n".encode("utf-16-le")})

    def test_a_file_that_is_mostly_not_utf8_is_spared_st11_and_a_file_with_a_nul_is_spared_st12(self):
        """Random bytes spell every invisible character by chance, so an image or a font would be a false alarm; CRLF in a file with
        a NUL is not a line ending."""
        import random
        noise = random.Random(11).getrandbits(8 * 700000).to_bytes(700000, "big")
        self.assertIn(b"\0", noise[:8192])
        for name, data in (("docs/noise.bin", noise), ("docs/picture.png", PNG[:8] + noise), ("docs/font.woff2", b"wOF2" + noise),
                           ("docs/all.dat", bytes(range(256)) * 3000), ("docs/crlf.bin", b"\0a\r\nb\r\n")):
            with self.subTest(name=name):
                quiet(self, {name: data})
        self.assertTrue(structure.garbled("\ufffd" * 3 + "a" * 9) and not structure.garbled("\ufffd" + "a" * 9) and not structure.garbled(""))

    def test_st12_crlf_line_endings(self):
        one(self, "ST12", "docs/x.txt", "docs/x.txt uses CRLF line endings. Fix: use LF; .gitattributes does for new files.",
            {"docs/x.txt": b"a\r\nb\r\n"})
        self.assertEqual("warning", run(structure, {"docs/x.txt": b"a\r\nb\r\n"})[0].severity)
        quiet(self, {"docs/win.md": b"a\r\nb\r\n", "docs/run.bat": b"@echo off\r\n", "docs/lf.txt": b"a\nb\n", "docs/cr.txt": b"a\rb\r"})

    def test_st16_a_program_or_an_archive(self):
        message = "docs/tool.bin is a program, not source. Fix: add the source and build it in a Dockerfile."
        for name, content in (("elf", ELF), ("mach-o", MACHO), ("pe", PE), ("zip", ZIP), ("tar", TAR), ("gzip", GZIP),
                              ("bzip2", b"BZh91AY&SY" + b"\0" * 20), ("xz", b"\xfd7zXZ\0" + b"\0" * 20),
                              ("7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 20), ("zstd", b"\x28\xb5\x2f\xfd" + b"\0" * 20),
                              ("fat mach-o or class", b"\xca\xfe\xba\xbe" + b"\0" * 20)):
            with self.subTest(kind=name):
                one(self, "ST16", "docs/tool.bin", message, {"docs/tool.bin": content})
        quiet(self, {"docs/a.png": PNG, "docs/a.jpg": b"\xff\xd8\xff\xe0" + b"\0" * 20, "docs/a.pdf": b"%PDF-1.7\n\0\0",
                     "docs/a.woff2": b"wOF2" + b"\0" * 20, "docs/a.ico": b"\0\0\x01\0" + b"\0" * 20, "docs/mz.txt": b"MZ is a word\n",
                     "docs/a.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>"})

    def test_st19_git_lfs(self):
        message = ".gitattributes uses Git LFS. Fix: remove it; the Lab installs no LFS file."
        one(self, "ST19", ".gitattributes", message, {".gitattributes": b"\0\n*.png filter=lfs diff=lfs merge=lfs -text\n"})
        one(self, "ST19", ".gitattributes", message, {".gitattributes": b"x\n" * 5000 + b"\0\n*.png filter=lfs\n"})
        one(self, "ST19", ".gitattributes", message, {".gitattributes": "*.png filter=lfs diff=lfs merge=lfs -text\n"})
        one(self, "ST19", "docs/.gitattributes", message, {"docs/.gitattributes": "# lfs\n*.bin  FILTER = lfs\n"})
        quiet(self, {".gitattributes": "* text=auto eol=lf\n*.png binary\n"})

    def test_st20_code_in_a_seed(self):
        message = ('{} is code. A seed brings data; code makes this item "Brings its own seed code", and it cannot use a Cloud '
                   'Token.')
        unit = {"playgrounds/shop/compose.yml": "services: {}\n"}
        for path in ("playgrounds/shop/seed/load.py", "playgrounds/shop/seed/x.sh", "playgrounds/shop/seed/a/x.js",
                     "playgrounds/shop/seed/x.rb", "playgrounds/shop/seed/run"):
            content = ("100755", "#!/bin/sh\n") if path.endswith("run") else "print(1)\n"
            with self.subTest(path=path):
                found = run(structure, unit, {path: content})
                self.assertEqual([("ST20", "warning", path, message.format(path))],
                                 [(each.code, each.severity, each.path, each.message) for each in found])
        quiet(self, unit, {"playgrounds/shop/seed/apim/apis/shop.yaml": "a: 1\n", "playgrounds/shop/apps/x/run.py": "print(1)\n"})

    def test_st21_a_secret_scanners_configuration(self):
        message = ("{} configures a secret scanner. Fix: remove it; the scan that judges this change uses the Lab's own "
                   "configuration, and a maintainer decides what it may skip.")
        for path in (".gitleaks.toml", ".gitleaksignore", ".trufflehogignore", ".secretlintrc.json", ".secretlintrc", ".detect-secrets.cfg",
                     "addons/hello/.gitleaks.toml", "docs/.gitleaksignore"):
            with self.subTest(path=path):
                one(self, "ST21", path, message.format(path), {path: "x"})
        quiet(self, {".editorconfig": "root = true\n"})

    def test_st08_the_files_of_an_open_source_project_may_sit_at_the_top(self):
        for name in ("NOTICE", "CODE_OF_CONDUCT.md", "CHANGELOG.md"):
            with self.subTest(name=name):
                quiet(self, {name: "x\n"})
        self.assertEqual({"ST08"}, codes(run(structure, {"NOTES": "x\n"})))

    def test_st22_a_file_that_runs_on_the_machine_of_whoever_opens_the_folder(self):
        message = "{} would run on a reader's machine. Fix: remove it; a unit is Compose files, data and the sources of its apps."
        for path in (".devcontainer/devcontainer.json", ".devcontainer.json", ".vscode/tasks.json", ".envrc", ".husky/pre-commit",
                     ".githooks/pre-push", ".pre-commit-config.yaml", "addons/hello/Makefile", "addons/hello/sub/makefile",
                     "addons/hello/GNUmakefile", "docs/.devcontainer/x.json"):
            with self.subTest(path=path):
                one(self, "ST22", path, message.format(path), {path: "x"})
        quiet(self, {"Makefile": "include .catalog/standard.mk\n"})
        self.assertEqual({"ST08"}, codes(run(structure, {".vscode/settings.json": "{}"})), "only tasks.json runs")

    def test_st22_a_folder_or_a_file_is_the_same_whatever_the_case_of_its_name(self):
        """A Mac and a Windows machine read `.VSCODE/tasks.json` as `.vscode/tasks.json`: it runs the same."""
        message = "{} would run on a reader's machine. Fix: remove it; a unit is Compose files, data and the sources of its apps."
        for path in (".Devcontainer/devcontainer.json", ".DEVCONTAINER/x", ".VSCODE/tasks.json", ".Vscode/Tasks.json", ".HUSKY/pre-commit",
                     ".GitHooks/pre-push", "Addons/hello/Makefile", "ADDONS/hello/MAKEFILE", "playgrounds/Shop/x/GNUMakefile", ".EnvRC",
                     ".PRE-COMMIT-CONFIG.YAML", ".DevContainer.json", "docs/.Husky/x"):
            with self.subTest(path=path):
                found = run(structure, {path: "x"})   # a name in another case may be a case twin or a unit not named as one too
                self.assertIn(("ST22", path, message.format(path)), [(each.code, each.path, each.message) for each in found], found)
        self.assertEqual({"ST08"}, codes(run(structure, {".VSCODE/settings.json": "{}"})), "only tasks.json runs")


class Standard(unittest.TestCase):

    def test_st08_a_path_in_no_place_the_lab_reads(self):
        message = ("{} is not in a place the Lab reads. Fix: if it should be installed, move it under addons/, setups/ or "
                   "playgrounds/; if it is for maintainers, ignore this.")
        for path in ("tools/run.sh", "src/main.py", "notes.txt", "scripts/x", ".vscode/settings.json", "data/x.csv"):
            with self.subTest(path=path):
                found = run(structure, {path: "x"})
                self.assertEqual([("ST08", "warning", path, message.format(path))],
                                 [(each.code, each.severity, each.path, each.message) for each in found])
        quiet(self, {".editorconfig": "x", ".gitattributes": "x", "tests/test_a.py": "x", "docs/a/b.md": "x", ".catalog/x.mk": "x",
                     "addons/hello/anything/at/all.txt": "x", ".claude/x.md": "x"})

    def test_a_path_that_is_already_an_error_is_not_also_a_warning(self):
        self.assertEqual({"ST06"}, codes(run(structure, {"secrets/key.txt": "x"})))
        self.assertEqual({"ST05"}, codes(run(structure, {"data/.env": "x"})))

    def test_st17_a_required_file_is_missing(self):
        message = "{} is missing. Fix: catalog.py upgrade --adopt adds what is missing."
        for path in ("catalog.yml", "README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "REVIEWING.md",
                     ".github/standard.json", ".github/CODEOWNERS"):
            with self.subTest(path=path):
                found = run(structure, drop=[path])
                self.assertEqual([("ST17", "error", path, message.format(path))],
                                 [(each.code, each.severity, each.path, each.message) for each in found])
        found = run(structure, drop=[".github/PULL_REQUEST_TEMPLATE.md"])
        self.assertEqual([("ST17", "warning")], [(each.code, each.severity) for each in found])

    def test_a_folder_catalog_needs_the_catalog_and_its_readme_only(self):
        listing = ListTree.from_files(support.FOLDER)
        self.assertEqual([], structure.check(listing, context(listing)))
        self.assertFalse(context(listing).git)
        smaller = {path: content for path, content in support.FOLDER.items() if path != "README.md"}
        listing = ListTree.from_files(smaller)
        self.assertEqual(["README.md"], [each.path for each in structure.check(listing, context(listing)) if each.code == "ST17"])

    def test_a_part_of_a_catalog_is_not_asked_for_the_files_of_a_whole_one(self):
        listing = ListTree.from_files({"playgrounds/shop/compose.yml": "services: {}\n", "playgrounds/shop/README.md": "# Shop\n"})
        self.assertEqual({"ST17"}, codes(structure.check(listing, context(listing))))
        self.assertEqual([], structure.check(listing, context(listing, partial=True)))
        self.assertEqual({"ST05"}, codes(structure.check(ListTree.from_files({"playgrounds/shop/compose.yml": "x", "playgrounds/shop/.env": "x"}),
                                                          Context(partial=True, git=False))))

    def test_a_repository_without_its_standard_file_is_still_a_repository(self):
        found = run(structure, drop=[".github/standard.json"])
        self.assertEqual([("ST17", ".github/standard.json")], [(each.code, each.path) for each in found])

    def test_st18_the_license_matches_catalog_yml(self):
        message = "LICENSE does not match license: {0} in catalog.yml. Fix: put the text of {0} in LICENSE."
        cases = {"MIT": (support.APACHE, support.AGPL, "Some words"), "Apache-2.0": (support.MIT, support.AGPL),
                 "AGPL-3.0-only": (support.MIT, support.APACHE)}
        for spdx, wrong in cases.items():
            for text in wrong:
                with self.subTest(spdx=spdx):
                    changes = {"catalog.yml": support.CATALOG.replace("license: MIT", f"license: {spdx}"), "LICENSE": text}
                    one(self, "ST18", "LICENSE", message.format(spdx), changes)
        for spdx, text in (("MIT", support.MIT), ("Apache-2.0", support.APACHE), ("AGPL-3.0-only", support.AGPL),
                           ("apache-2.0", support.APACHE), ("Proprietary", "Any words at all"), ("BSD-3-Clause", "Words")):
            with self.subTest(ok=spdx):
                quiet(self, {"catalog.yml": support.CATALOG.replace("license: MIT", f"license: {spdx}"), "LICENSE": text})
        quiet(self, {"catalog.yml": support.CATALOG.replace("  license: MIT\n", ""), "LICENSE": "anything"})

    def test_the_text_of_a_license_with_blanks_folded_is_still_that_license(self):
        text = "GNU   AFFERO GENERAL\nPUBLIC LICENSE\n  Version 3,\n19 November 2007"
        quiet(self, {"catalog.yml": support.CATALOG.replace("MIT", "AGPL-3.0-only"), "LICENSE": text})


class Limits(unittest.TestCase):
    """What a rule reads of a file is what ST09 lets the file be: no file is left unread without a finding."""

    def test_the_limits_of_the_rules_are_the_ones_of_the_lab(self):
        from lab_ui.catalogs import rules
        self.assertEqual((store.TEXT_FILE, store.OTHER_FILE, store.TEXT_SUFFIXES), (rules.TEXT_LIMIT, rules.OTHER_LIMIT, rules.TEXT_SUFFIXES))
        for name in ("a.yml", "A.YAML", "x/y.md", "z.JSON"):
            self.assertEqual(store.TEXT_FILE, rules.read_limit(name), name)
        for name in ("Dockerfile", "a.txt", "run.sh", "a.markdown", "x.svg", "a.md.txt"):
            self.assertEqual(store.OTHER_FILE, rules.read_limit(name), name)

    def test_st11_in_a_file_between_the_two_limits(self):
        pad = b"x\n" * (700 * 1024)   # 1.4 MB
        found = run(structure, {"docs/big.txt": pad + f"the {BIDI} text\n".encode()})
        self.assertEqual([("ST11", 700 * 1024 + 1)], [(each.code, each.line) for each in found])
        self.assertEqual(["ST09"], [each.code for each in run(structure, {"docs/big.json": pad + b"x"})])

    def test_a_file_over_its_limit_is_st09_and_nothing_else_reads_it(self):
        found = run(structure, {"docs/huge.txt": b"x\n" * (1100 * 1024) + f"the {BIDI} text\n".encode()})
        self.assertEqual(["ST09"], [each.code for each in found])


class Helpers(unittest.TestCase):

    def test_case_twins_are_found_at_the_folder_where_they_part(self):
        self.assertEqual([("a/B", "a/b")], structure.case_twins(["a/b/1", "a/B/2", "a/B/3"]))
        self.assertEqual([("A.md", "a.md")], structure.case_twins(["a.md", "A.md"]))
        self.assertEqual([], structure.case_twins(["a.md", "b.md", "a/b"]))

    def test_a_program_by_its_first_bytes_and_never_by_its_name(self):
        for content in (ELF, MACHO, PE, ZIP, TAR, GZIP):
            self.assertTrue(structure.program(content))
        for content in (b"", b"MZ", b"MZ text file\n", PNG, b"plain text"):
            self.assertFalse(structure.program(content))

    def test_what_runs_and_what_scans(self):
        self.assertTrue(structure.runs_on_reader(".husky/pre-commit"))
        self.assertFalse(structure.runs_on_reader("Makefile"))
        self.assertTrue(structure.scanner_file("a/b/.gitleaks.toml"))
        self.assertFalse(structure.scanner_file("a/gitleaks.md"))

    def test_the_messages_of_the_table_are_section_6(self):
        self.assertEqual(
            {"ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST08", "ST09", "ST10", "ST11", "ST12", "ST13", "ST14", "ST15",
             "ST16", "ST17", "ST18", "ST19", "ST20", "ST21", "ST22"}, set(structure.RULES))
        self.assertEqual(["warning"], sorted({structure.RULES[code].severity for code in ("ST08", "ST12", "ST20")}))
        self.assertEqual("{unit} holds {found}, the limit is {limit}. Fix: split it.", structure.RULES["ST10"].message)

    def test_a_tree_in_a_hostile_order_gives_the_same_findings(self):
        files = support.files({"docs/k.pem": "x", "docs/Guide.md": "x", "tools/x.sh": "x"})
        first = structure.check(ListTree.from_files(files), context(ListTree.from_files(files)))
        reverse = dict(reversed(list(files.items())))
        second = structure.check(ListTree.from_files(reverse), context(ListTree.from_files(reverse)))
        key = lambda each: (each.code, each.path, each.message)  # noqa: E731
        self.assertEqual(sorted(first, key=key)[:1][0].code, sorted(second, key=key)[:1][0].code)
        self.assertEqual({"ST06", "ST08", "ST13"}, codes(first))
        self.assertEqual({"ST06", "ST08", "ST13"}, codes(second))


# ---------------------------------------------------------------------------------------------------------------------
# Calibration on the content the engine holds today
# ---------------------------------------------------------------------------------------------------------------------

class Content(unittest.TestCase):
    """Every ST, SE, GD, IM and DF rule over `addons/`, `setups/` and `playgrounds/` of this checkout, as a catalog: zero errors.
    The one exception is the link of a guide to a file of the engine's checkout that is not in the three folders (`../docs/...`,
    `../AGENTS.md`): inside a catalog it leads nowhere, which is what GD01 says; the default catalog's extraction replaces them."""

    OUTSIDE = ("docs/", "seeder/")
    OUTSIDE_FILES = ("AGENTS.md", "CONTRIBUTING.md", "README.md", "SECURITY.md", "CLAUDE.md")

    @classmethod
    def tracked(cls):
        folders = ("addons", "setups", "playgrounds")
        try:
            done = subprocess.run(["git", "ls-files", "-z", "--", *folders], cwd=support.ROOT, capture_output=True, timeout=60)
            names = [name.decode() for name in done.stdout.split(b"\0") if name] if done.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            names = []
        if names:
            return [name for name in names if (support.ROOT / name).is_file() and not (support.ROOT / name).is_symlink()]
        found = []   # no git: the folders as a clone has them, without what .gitignore keeps out
        for folder in folders:
            for base, dirs, files in os.walk(support.ROOT / folder):
                dirs[:] = [each for each in dirs if each not in ("local", ".state", "secrets", "__pycache__")]
                found += [str((Path(base) / each).relative_to(support.ROOT)) for each in files if not each.startswith(".env")]
        return found

    def setUp(self):
        self.names = self.tracked()
        if not self.names:
            self.skipTest("this checkout holds no addons, setups or playgrounds")
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.catalog = Path(holder.name).resolve() / "catalog"
        for name in self.names:
            target = self.catalog / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(support.ROOT / name, target)
        seeders = audit.lab_seeder_images()
        self.prefix = seeders[0].rsplit("/", 1)[0] if seeders else ""
        meta = "x-gravitee-catalog:\n  schema: 1\n  id: content\n  name: Content\n  description: The content\n  version: 1.0.0\n  lab: 1\n"
        (self.catalog / "catalog.yml").write_text(meta + (f"  images: {self.prefix}\n" if self.prefix else ""))
        (self.catalog / "README.md").write_text("# Content\n")

    def run_all(self, **how):
        families = ("ST", "SE", "GD", "DF", "IM")
        return audit.run(self.catalog, audit.Options(families=families, seeder_paths=("seeder",), **how))

    def test_the_content_is_in_the_shape_of_a_catalog_and_has_the_five_kinds_of_unit(self):
        listing = FolderTree(self.catalog)
        self.assertEqual("walk", (listing.entries(), listing.source)[1])
        self.assertGreater(len([each for each in listing.entries()]), 100)
        self.assertTrue(any(name.startswith("playgrounds/") for name in self.names))
        self.assertTrue(any(name.startswith("addons/") for name in self.names))
        self.assertTrue(any(name.startswith("setups/") for name in self.names))

    def test_no_rule_of_this_package_finds_an_error_in_the_content(self):
        result = self.run_all()
        self.assertEqual([], result.skipped)
        errors = [each for each in result.problems if each.severity == "error" and each.code != "GD01"]
        self.assertEqual([], errors, "\n".join(f"{each.code} {each.path}:{each.line} {each.message}" for each in errors))
        broken = [each for each in result.problems if each.code == "GD01"]
        for each in broken:
            target = re.search(r"the link (.*) does not resolve", each.message).group(1)
            leads = posixpath.normpath(posixpath.join(posixpath.dirname(each.path), unquote(re.split(r"[?#]", target)[0])))
            self.assertTrue(leads.startswith(self.OUTSIDE) or leads in self.OUTSIDE_FILES,
                            f"{each.path}:{each.line} {target} leads to {leads}")

    def test_the_only_warnings_are_the_dockerfile_ones_the_standard_expects(self):
        result = self.run_all()
        warnings = {each.code for each in result.problems if each.severity == "warning"}
        self.assertLessEqual(warnings, {"DF22", "DF23", "DF24", "ST20", "SE05"})

    def test_the_seed_services_pass_all_four_image_rules_with_the_seeder_as_the_anchor(self):
        result = self.run_all()
        self.assertEqual([], [each for each in result.problems if each.code.startswith("IM")])
        seeds = [name for name in self.names if name.endswith("/compose.yml") and "seed" in (support.ROOT / name).read_text()]
        self.assertTrue(seeds, "the engine holds playgrounds with a seed")
        without = audit.run(self.catalog, audit.Options(families=("IM",), seeder_images=(), seeder_paths=("seeder",)))
        self.assertIn("IM02", {each.code for each in without.problems}, "without the anchor every seed fails its own standard")

    def test_the_content_listing_is_the_one_the_lab_would_install(self):
        listing = FolderTree(self.catalog)
        entries = [store.Entry(each.mode, "0" * 40, each.size, each.path) for each in listing.entries()]
        self.assertEqual([], [(each.path, code) for each, code in store.rejections(entries)])
        kept, ignored = store.selected(entries)
        self.assertEqual(0, ignored)
        from lab_ui.catalogs.rules import unit_of
        self.assertEqual({each.path for each in listing.entries() if each.path == "catalog.yml" or unit_of(each.path)},
                         {each.path for each in kept}, "catalog.yml and the files of the units are installed, the guides of the kinds are not")


if __name__ == "__main__":
    unittest.main()
