"""install.ps1, which runs on Windows only: read here as a file and, where PowerShell is installed, parsed by it. What Windows
PowerShell 5.1, the one every Windows has, needs of it: ASCII (it reads no other encoding without a byte order mark), LF line
ends as every file of the project has, and no syntax of PowerShell 7 alone. What keeps its look right: a program whose error
output it drops runs inside Silently, as 5.1 shows that output as its own red error otherwise. And what it tells a person: the
pages it links to have the heading it names, and the command it gives is the one the documentation gives."""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "install.ps1"
# The tokens of PowerShell 7 alone, by the names of its parser: Windows PowerShell 5.1 refuses a script that holds one
SEVEN = {"AndAnd", "OrOr", "QuestionQuestion", "QuestionQuestionEquals", "QuestionDot", "QuestionLBracket", "QuestionMark"}
# The command a person runs, as install.ps1 builds it for main
COMMAND = "irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex"


def powershell():
    """Windows PowerShell where it is, the one the script is for; else PowerShell 7, which the runners of the CI have"""
    return shutil.which("powershell") or shutil.which("pwsh")


def slug(heading):
    """The anchor GitHub gives a heading"""
    return re.sub(r"[^a-z0-9 -]", "", heading.strip().lower()).replace(" ", "-")


class InstallPs1(unittest.TestCase):
    def setUp(self):
        self.raw = SCRIPT.read_bytes()
        self.text = self.raw.decode("ascii", errors="replace")

    def test_it_is_ascii_with_lf_line_ends_and_no_byte_order_mark(self):
        wrong = [number for number, line in enumerate(self.raw.split(b"\n"), 1) if any(byte > 127 for byte in line)]
        self.assertEqual(wrong, [], "lines that are not ASCII, which Windows PowerShell 5.1 reads in the code page of Windows")
        self.assertNotIn(b"\r", self.raw)
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))

    # Skipped on a machine without PowerShell, never in the CI, whose runners have it: there, its absence fails
    @unittest.skipUnless(powershell() or os.environ.get("CI"), "PowerShell is not installed")
    def test_powershell_parses_it_and_it_holds_no_syntax_of_powershell_7_alone(self):
        path = str(SCRIPT).replace("'", "''")
        parse = ("$tokens = $null; $errors = $null; "
                 f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$tokens, [ref]$errors); "
                 "@{ errors = @($errors | ForEach-Object { \"$($_.Extent.StartLineNumber): $($_.Message)\" }); "
                 "kinds = @($tokens | ForEach-Object { \"$($_.Kind)\" } | Sort-Object -Unique) } | ConvertTo-Json -Compress")
        self.assertTrue(powershell(), "the CI has no PowerShell: install it in the job")
        run = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", parse],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        found = json.loads(run.stdout)
        self.assertEqual(found["errors"], [])
        self.assertEqual(sorted(SEVEN & set(found["kinds"])), [], "Windows PowerShell 5.1 does not know these")

    def test_a_program_whose_error_output_is_dropped_runs_inside_silently(self):
        dropped = re.compile(r"(\*>|2>)\s*\$null")
        loud = [f"{number}: {line.strip()}" for number, line in enumerate(self.text.splitlines(), 1)
                if dropped.search(line) and not line.lstrip().startswith("#") and "Silently {" not in line]
        self.assertEqual(loud, [])

    def test_the_pages_it_links_to_have_the_headings_it_names(self):
        links = set(re.findall(r"docs/([\w-]+\.md)#([\w-]+)", self.text))
        self.assertTrue(links)
        for page, anchor in sorted(links):
            headings = {slug(line.lstrip("#")) for line in (ROOT / "docs" / page).read_text().splitlines() if line.startswith("#")}
            self.assertIn(anchor, headings, f"docs/{page} has no heading for #{anchor}")

    def test_the_command_it_gives_is_the_one_of_the_documentation(self):
        self.assertIn('$command = "irm $raw/install.ps1 | iex"', self.text)
        self.assertIn('/gravitee-lab/$branch"', self.text)
        for page in ("README.md", "docs/installation.md"):
            self.assertIn(COMMAND, (ROOT / page).read_text(), page)


if __name__ == "__main__":
    unittest.main()
