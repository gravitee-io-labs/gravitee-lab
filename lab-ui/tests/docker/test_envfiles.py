"""The .env files: values survive a round trip, comments and order are kept, an empty add-on file goes away."""

import os
import stat
import unittest
from unittest import mock

from lab_ui.docker import envfiles

from .. import Case

CORE = """\
# Enterprise license (base64)
GRAVITEE_LICENSE=abc123

# Images
APIM_VERSION=4.12.20
APIM_GATEWAY_VERSION=${APIM_VERSION}-debian
REDIS_PASSWORD='two words'   # quoted
"""


class EnvFiles(Case):

    def setUp(self):
        self.folder(envfiles, files={".env": CORE})
        (self.root / "addons" / "hybrid").mkdir(parents=True)

    def text(self, path=".env"):
        return (self.root / path).read_text(encoding="utf-8")

    def test_read_unquotes_and_keeps_references(self):
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "abc123", "APIM_VERSION": "4.12.20",
                                                 "APIM_GATEWAY_VERSION": "${APIM_VERSION}-debian",
                                                 "REDIS_PASSWORD": "two words"})

    def test_read_a_missing_file(self):
        self.assertEqual(envfiles.read("addons/hybrid/.env"), {})

    def test_read_drops_a_trailing_comment(self):
        (self.root / ".env").write_text("A=value # why\nB=with#hash\nC=\"quoted # kept\" # dropped\n", encoding="utf-8")
        self.assertEqual(envfiles.read(".env"), {"A": "value", "B": "with#hash", "C": "quoted # kept"})

    def test_write_keeps_comments_and_order(self):
        envfiles.write(".env", {"APIM_VERSION": "4.13.0", "NEW_B": "2", "NEW_A": "1"})
        changed = CORE.replace("APIM_VERSION=4.12.20", "APIM_VERSION=4.13.0")
        self.assertEqual(self.text(), changed + "NEW_A=1\nNEW_B=2\n")

    def test_write_nothing_changes_nothing(self):
        envfiles.write(".env", {})
        self.assertEqual(self.text(), CORE)

    def test_none_removes_a_variable(self):
        envfiles.write(".env", {"APIM_GATEWAY_VERSION": None, "NOT_THERE": None})
        self.assertEqual(self.text(), CORE.replace("APIM_GATEWAY_VERSION=${APIM_VERSION}-debian\n", ""))

    def test_quote_only_when_needed(self):
        self.assertEqual(envfiles.quote("plain-value_1.2"), "plain-value_1.2")
        self.assertEqual(envfiles.quote(""), "")
        self.assertEqual(envfiles.quote("two words"), "'two words'")
        self.assertEqual(envfiles.quote("pa$$word"), "'pa$$word'")
        self.assertEqual(envfiles.quote("it's"), '"it\'s"')
        self.assertEqual(envfiles.quote("back\\slash"), '"back\\\\slash"')

    def test_quoted_values_read_back_unchanged(self):
        values = ["plain", "two words", "hash # inside", "it's", 'say "hi"', "dollar $HOME ${X}", "back\\slash",
                  "trailing\\", "all ' \" $ \\ # of them", "a=b", "", "'quoted'"]
        changes = {f"V{i}": envfiles.quote(value) for i, value in enumerate(values)}
        envfiles.write("addons/hybrid/.env", changes)
        self.assertEqual(envfiles.read("addons/hybrid/.env"), {f"V{i}": value for i, value in enumerate(values)})

    def test_written_for_the_owner_only(self):
        envfiles.write("addons/hybrid/.env", {"HYBRID_CLOUD_TOKEN": "t"})
        mode = stat.S_IMODE((self.root / "addons/hybrid/.env").stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual([f.name for f in (self.root / "addons/hybrid").iterdir()], [".env"])

    def test_secrets_are_the_owner_s_before_they_are_written(self):
        # Through a copy of its own, which no other save shares, made 0600 before anything is written to it
        modes, fdopen = [], os.fdopen

        def opened(handle, *args, **how):
            modes.append(stat.S_IMODE(os.fstat(handle).st_mode))
            return fdopen(handle, *args, **how)
        with mock.patch.object(os, "fdopen", opened):
            envfiles.write(".env", {"GRAVITEE_LICENSE": "secret"})
        self.assertEqual(modes, [0o600])
        self.assertEqual([f.name for f in self.root.iterdir() if f.is_file()], [".env"])

    def test_an_addon_file_left_empty_is_deleted(self):
        envfiles.write("addons/hybrid/.env", {"HYBRID_CLOUD_TOKEN": "t"})
        self.assertEqual(self.text("addons/hybrid/.env"), "HYBRID_CLOUD_TOKEN=t\n")
        envfiles.write("addons/hybrid/.env", {"HYBRID_CLOUD_TOKEN": None})
        self.assertFalse((self.root / "addons/hybrid/.env").exists())
        envfiles.write("addons/hybrid/.env", {"HYBRID_CLOUD_TOKEN": None})   # nothing to delete: no error, no file
        self.assertFalse((self.root / "addons/hybrid/.env").exists())

    def test_the_core_file_stays_when_empty(self):
        (self.root / ".env").write_text("ONLY=1\n", encoding="utf-8")
        envfiles.write(".env", {"ONLY": None})
        self.assertEqual(self.text(), "")


if __name__ == "__main__":
    unittest.main()
