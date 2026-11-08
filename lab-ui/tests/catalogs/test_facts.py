"""What an update changes in an item that a person decides on (`catalogs/facts.py`)."""

import unittest

from lab_ui.catalogs import facts

BANK = {"requires": {"images": {"APIM_VERSION": "4.13.1"}, "license": {"apim-mcp-tool-server": "Lesson 6"},
                     "host": [{"name": "Ollama with qwen3:1.7b"}]},
        "tested": [{"APIM_REGISTRY": "graviteeio", "APIM_VERSION": "4.13.2", "AM_VERSION": "4.13.2"}],
        "settings": [{"key": "BANK_PASSWORD", "required": False}, {"key": "BANK_OLD", "required": False}]}


def changed(**parts):
    return {**BANK, **parts}


class Facts(unittest.TestCase):

    def test_the_same_item_says_nothing(self):
        self.assertEqual(facts.facts(BANK, dict(BANK)), [])

    def test_a_version_it_needs_says_the_new_one_and_the_one_before(self):
        self.assertEqual(facts.facts(BANK, changed(requires={**BANK["requires"], "images": {"APIM_VERSION": "4.13.0"}})),
                         ["Needs APIM_VERSION 4.13.0, was 4.13.1"])
        self.assertEqual(facts.facts(BANK, changed(requires={**BANK["requires"], "images": {"APIM_VERSION": "4.13.1", "AM_VERSION": "4.13.0"}})),
                         ["Needs AM_VERSION 4.13.0"])
        self.assertEqual(facts.facts(BANK, changed(requires={**BANK["requires"], "images": {}})), ["No longer needs a given APIM_VERSION"])

    def test_a_feature_of_the_license_and_a_need_of_the_machine_that_come_or_go(self):
        after = changed(requires={**BANK["requires"], "license": ["apim-a2a-proxy"], "host": [{"name": "Ollama with qwen3:1.7b"}, {"name": "A GPU"}]})
        self.assertEqual(facts.facts(BANK, after), ["Needs the license feature apim-a2a-proxy",
                                                    "No longer needs the license feature apim-mcp-tool-server", "Needs A GPU"])

    def test_a_set_it_was_tested_on_says_its_versions_and_not_its_registries(self):
        after = changed(tested=[{"APIM_REGISTRY": "graviteeio", "APIM_VERSION": "4.13.3", "AM_VERSION": "4.13.3"}])
        self.assertEqual(facts.facts(BANK, after), ["Tested on AM_VERSION 4.13.3, APIM_VERSION 4.13.3"])
        self.assertEqual(facts.facts(after, BANK), ["Tested on AM_VERSION 4.13.2, APIM_VERSION 4.13.2"], "a set it had before is no news")

    def test_a_setting_that_comes_says_whether_it_is_required_and_one_that_goes_is_said(self):
        after = changed(settings=[{"key": "BANK_PASSWORD"}, {"key": "BANK_TOKEN", "required": True}, {"key": "BANK_NOTE", "required": False}])
        self.assertEqual(facts.facts(BANK, after), ["New setting BANK_NOTE", "New setting BANK_TOKEN, required", "Setting BANK_OLD removed"])

    def test_what_it_needs_comes_first_and_past_the_limit_the_rest_is_counted(self):
        after = changed(requires={**BANK["requires"], "images": {"APIM_VERSION": "4.13.0"}},
                        settings=[{"key": f"BANK_{n}"} for n in range(12)])
        said = facts.facts(BANK, after)
        self.assertEqual(len(said), facts.LIMIT)
        self.assertEqual((said[0], said[-1]), ("Needs APIM_VERSION 4.13.0, was 4.13.1", "And 8 more"))

    def test_an_item_that_says_nothing_of_a_part_reads_as_none(self):
        self.assertEqual(facts.facts({}, {"requires": None, "tested": None, "settings": None}), [])


if __name__ == "__main__":
    unittest.main()
