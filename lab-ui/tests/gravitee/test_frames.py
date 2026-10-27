"""frames: the tabs a playground declares and the only addresses that may be asked for. No Docker."""

import re
import unittest
from pathlib import Path

from lab_ui.gravitee import frames

from .. import Case

PAGE = "http://localhost:9000"
GAMMA = "http://localhost:8086"


def link(label, url, service, legacy=False):
    return {"label": label, "url": url, "port": None, "service": service, "section": "", "legacy": legacy}


class Declared(Case):
    """The tabs a playground declares, and so the only addresses that are asked for."""

    LINKS = [link("Acme Bakery", "http://localhost:8110", "bakery-web"), link("Gamma Console", GAMMA, "gamma-ui"),
             link("Developer Portal", "http://localhost:8085/next/", "portal"),
             link("Classic Developer Portal", "http://localhost:8085", "portal", legacy=True),
             link("AM Console", "http://localhost:8081", "am-ui")]

    def test_each_service_listed_is_a_tab_by_the_name_of_its_console_in_the_order_listed(self):
        found = frames.tabs(self.LINKS, ["bakery-web", "gamma-ui", "am-ui"])
        self.assertEqual(found, [
            {"id": "bakery-web", "label": "Acme Bakery", "url": "http://localhost:8110", "service": "bakery-web"},
            {"id": "gamma-ui", "label": "Gamma Console", "url": GAMMA, "service": "gamma-ui"},
            {"id": "am-ui", "label": "AM Console", "url": "http://localhost:8081", "service": "am-ui"}])

    def test_the_order_is_the_one_the_playground_names(self):
        found = frames.tabs(self.LINKS, ["am-ui", "gamma-ui", "bakery-web"])
        self.assertEqual([tab["id"] for tab in found], ["am-ui", "gamma-ui", "bakery-web"])

    def test_a_service_with_several_consoles_gives_its_first(self):
        # The links come with the current ones first
        links = sorted(self.LINKS, key=lambda each: each["legacy"])
        self.assertEqual([tab["label"] for tab in frames.tabs(links, ["portal"])], ["Developer Portal"])

    def test_what_is_not_declared_is_left_out(self):
        self.assertEqual(frames.tabs(self.LINKS, ["gone", "gamma-ui"]), [
            {"id": "gamma-ui", "label": "Gamma Console", "url": GAMMA, "service": "gamma-ui"}])
        self.assertEqual(frames.tabs(self.LINKS, []), [])

    def test_a_service_is_one_tab_however_often_it_is_listed(self):
        found = frames.tabs(self.LINKS, ["gamma-ui", "bakery-web", "gamma-ui"])
        self.assertEqual([tab["id"] for tab in found], ["gamma-ui", "bakery-web"])

    def test_a_console_that_is_no_web_address_is_no_tab_and_never_raises(self):
        # Nothing checks a file before the state is built from it: one slip must not take every page down
        for url in (8002, None, ["http://localhost:8002"], "", "localhost:8002", "javascript:alert(1)", "data:text/html,x",
                    "file:///etc/passwd", "ftp://localhost/", "http://", "http://[::1", "http://host:99999"):
            with self.subTest(url=url):
                self.assertEqual(frames.tabs([link("Odd", url, "odd"), *self.LINKS], ["odd", "gamma-ui"]),
                                 [{"id": "gamma-ui", "label": "Gamma Console", "url": GAMMA, "service": "gamma-ui"}])

    def project(self):
        services = {"bakery-web": {}, "gamma-ui": {}, "tracing-ui": {}, "am-ui": {}}
        links = [*self.LINKS, link("Tracing", GAMMA + "/tracing", "tracing-ui")]
        return {"links": links, "addons": {"tracing": {"services": ["tracing-ui"]}}, "services": services,
                "playgrounds": {"bank": {"services": ["bakery-web"], "addons": ["tracing"], "consoles": ["bakery-web", "gamma-ui"]},
                                "other": {"services": [], "addons": [], "consoles": ["am-ui"]},
                                "none": {"services": ["bakery-web"], "addons": [], "consoles": []}}}

    def test_the_tabs_of_a_project_playground(self):
        found = frames.declared(self.project(), "bank")
        self.assertEqual([(tab["label"], tab["service"]) for tab in found], [("Acme Bakery", "bakery-web"), ("Gamma Console", "gamma-ui")])

    def test_a_console_another_unit_puts_on_a_service_of_the_core_is_not_a_tab_of_this_one(self):
        # The tab is the console as the playground makes it, with the add-ons it brings: another unit's is not asked for
        p = self.project()
        mine, theirs = {"kind": "addon", "id": "tracing", "name": "Tracing"}, {"kind": "playground", "id": "other", "name": "Other"}
        def gamma(url, **more):
            labels = {"io.gravitee.console.gamma.name": "Gamma Console", "io.gravitee.console.gamma.url": url}
            return dict(link("Gamma Console", url, "gamma-ui"), console="gamma", labels=labels, **more)
        p["links"] = [gamma(GAMMA) if each["service"] == "gamma-ui" else each for each in p["links"]]
        p["links"].append(gamma(GAMMA + "/other", by=theirs))
        self.assertEqual(frames.allowed(p, "bank", GAMMA), GAMMA)
        self.assertIsNone(frames.allowed(p, "bank", GAMMA + "/other"))
        p["links"].append(gamma(GAMMA + "/bank", by=mine))
        self.assertEqual([tab["url"] for tab in frames.declared(p, "bank")], ["http://localhost:8110", GAMMA + "/bank"])
        self.assertIsNone(frames.allowed(p, "bank", GAMMA), "what it replaces is not its tab")

    def test_a_playground_that_lists_no_console_has_no_tab_whatever_it_runs(self):
        self.assertEqual(frames.declared(self.project(), "none"), [])

    def test_a_playground_with_bad_consoles_declares_what_it_can(self):
        p = self.project()
        p["playgrounds"]["bank"].update(consoles="gamma-ui")
        self.assertEqual(frames.declared(p, "bank"), [])
        p["playgrounds"]["bank"].update(consoles=[None, 3, "gamma-ui"])
        self.assertEqual([tab["id"] for tab in frames.declared(p, "bank")], ["gamma-ui"])
        p["playgrounds"]["bank"].pop("consoles")
        self.assertEqual(frames.declared(p, "bank"), [])
        self.assertIsNone(frames.allowed(p, "bank", 8110))

    def test_only_a_tab_of_the_playground_may_be_asked_for(self):
        p = self.project()
        self.assertEqual(frames.allowed(p, "bank", "http://localhost:8110"), "http://localhost:8110")
        self.assertEqual(frames.allowed(p, "bank", "http://localhost:8110/"), "http://localhost:8110")
        self.assertEqual(frames.allowed(p, "bank", GAMMA), GAMMA)
        for url in (None, "", "http://localhost:8081", "http://localhost:8085", GAMMA + "/tracing", "http://localhost:8110/api/state",
                    "http://localhost:81100", "http://localhost:8110.evil.test", "http://127.0.0.1:8110", "https://localhost:8110",
                    "file:///etc/passwd", "http://169.254.169.254/"):
            with self.subTest(url=url):
                self.assertIsNone(frames.allowed(p, "bank", url))
        self.assertIsNone(frames.allowed(p, "other", GAMMA))       # another playground's tab is not this one's
        self.assertIsNone(frames.allowed(p, "none", "http://localhost:8110"))   # what it runs is no tab unless it lists it
        self.assertIsNone(frames.allowed(p, "nobody", GAMMA))
        self.assertEqual(frames.allowed(p, "other", "http://localhost:8081"), "http://localhost:8081")


class Origin(Case):

    def test_a_web_address_has_an_origin_with_the_port_a_browser_compares(self):
        self.assertEqual(frames.origin("http://LocalHost:8086/x?y"), ("http", "localhost", 8086))
        self.assertEqual(frames.origin("http://localhost"), ("http", "localhost", 80))
        self.assertEqual(frames.origin("https://localhost"), ("https", "localhost", 443))
        self.assertEqual(frames.origin("http://[::1]:9000"), ("http", "::1", 9000))

    def test_what_is_no_web_address_has_none(self):
        for url in (None, 8002, "", "nope", "javascript:alert(1)", "blob:http://localhost:8086/x", "ws://localhost/", "http://", "http://[::1"):
            self.assertIsNone(frames.origin(url), str(url))

    def test_two_addresses_are_one_with_or_without_a_slash_at_the_end(self):
        self.assertTrue(frames.same("http://localhost:8110", "http://localhost:8110/"))
        self.assertFalse(frames.same("http://localhost:8110", "http://localhost:8110/x"))


class Pages(Case):
    """The pages of the Lab UI that may frame the UIs are named in the core file, which no code reads: they have to agree
    with frames.PAGES. The apps of a catalog name them too; a catalog tests its own."""

    ROOT = Path(__file__).resolve().parents[3]

    def test_the_platform_names_the_pages_the_lab_ui_is_served_from(self):
        core = (self.ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        anchor = core[core.index("x-ui-frames: &ui-frames"):core.index("\nservices:")]
        for page in frames.PAGES:
            self.assertIn(page, anchor)
        self.assertIn("/X-Frame-Options/d", anchor)

    def test_the_consoles_the_playgrounds_show_use_that_command(self):
        core = (self.ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for name in ("gamma-ui", "apim-management-ui"):
            block = re.search(rf"^  {name}:\n(.*?)(?=^  \S)", core, re.M | re.S).group(1)
            self.assertIn("command: *ui-frames", block, name)


if __name__ == "__main__":
    unittest.main()
