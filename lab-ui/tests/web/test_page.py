"""The page's JS tests (tests/web/js, run by node) and the places where the page's files must agree with them."""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from .. import Case

JS = Path(__file__).resolve().parent / "js"
WEB = Path(__file__).resolve().parents[2] / "web"
MODULES = sorted((WEB / "js").rglob("*.js"))


def node_reads_modules():
    """Whether node is there and loads a module from a .js file with no package.json that says so (split.js): from 22.7,
    or 20.19 of the 20 line. An older one cannot run these tests, which are then skipped."""
    if not shutil.which("node"):
        return False
    found = re.match(r"v(\d+)\.(\d+)", subprocess.run(["node", "--version"], capture_output=True, text=True).stdout)
    major, minor = (int(found[1]), int(found[2])) if found else (0, 0)
    return (major == 20 and minor >= 19) or (major == 22 and minor >= 7) or major >= 23


class Split(Case):

    @unittest.skipUnless(node_reads_modules(), "node 20.19 or 22.7 or later is not installed")
    def test_every_js_test_of_the_page(self):
        files = sorted(str(file) for file in JS.glob("*.test.mjs"))
        self.assertTrue(files)
        run = subprocess.run(["node", "--test", *files], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class Agreements(Case):
    """What the rules of split.js say and the files that draw them must say the same."""

    def test_the_stylesheet_draws_the_guide_at_the_width_the_rules_start_it_at(self):
        """Before a script has set the width, the guide has the one it starts at: its minimum."""
        rules = (WEB / "js" / "workspace" / "split.js").read_text()
        minimum = re.search(r"^const GUIDE_MIN = (\d+);", rules, re.M)
        self.assertIsNotNone(minimum)
        self.assertIn(f"var(--guide-w, {minimum[1]}px)", (WEB / "css" / "pages.css").read_text())

    def test_the_page_tells_the_apps_in_its_frames_only_the_actions_of_its_guide(self):
        """The Lab UI knows nothing of the apps it frames: it posts one message, the action of the guide, to the app of the first tab at
        its own origin."""
        workspace = (WEB / "js" / "workspace" / "workspace.js").read_text()
        self.assertEqual(re.findall(r"\bpostMessage\([^;]*;", workspace),
                         ["postMessage(actionMessage(id), origin);"], "one message, the action, to the origin of the tab's UI")
        self.assertNotIn("targetOrigin", workspace)
        self.assertNotIn('"*"', workspace)

    def test_an_action_link_of_the_guide_is_sent_before_any_other_link_is_followed(self):
        """A button of the guide asks the workspace to send its action; it is a button, not a link, so that nothing navigates."""
        playground = (WEB / "js" / "pages" / "playground.js").read_text()
        self.assertIn("Workspace.send(action.dataset.action)", playground)
        # only the buttons made of the guide's links send an action, whatever else in a guide carries an attribute
        self.assertIn('on("#pg-guide .guide-action[data-action]")', playground)
        self.assertIn('const ACTIONS = "button.guide-action[data-action]";', (WEB / "js" / "workspace" / "workspace.js").read_text())
        self.assertLess(playground.index("Workspace.send(action.dataset.action)"), playground.index("Workspace.follow(e, link)"))
        markdown = (WEB / "js" / "components" / "markdown.js").read_text()
        self.assertIn('document.createElement("button")', markdown)
        self.assertIn("forceKeepAttr", markdown)
        self.assertNotIn("ALLOWED_URI_REGEXP", markdown, "the sanitizer's own rules stand: only a valid action id is added")

    def test_skip_to_content_keeps_the_page(self):
        """Skip to content takes the keyboard to the page on screen: its address is no page of the router, which would lead to the
        Dashboard, so its click is the page's own."""
        main = (WEB / "js" / "main.js").read_text()
        self.assertRegex(main, r'querySelector\("\.skip"\)\.addEventListener\("click", \(e\) => \{\s*e\.preventDefault\(\);\s*'
                         r'document\.getElementById\("content"\)\.focus\(\);')

    def test_the_stop_of_a_playgrounds_row_asks_as_its_page_does(self):
        rows = (WEB / "js" / "pages" / "list.js").read_text()
        self.assertIn('if (x.kind === "playground" && action.do === "stop") return stopPlayground(x);', rows)

    def test_no_text_of_the_lab_ui_has_a_middle_dot_or_a_bullet_between_its_words(self):
        """The words of a line are told apart by a comma, parentheses, "and" or a gap of the layout, never by a middle dot or a bullet:
        not in the page (scripts, styles and markup, a CSS ::before included), not in a string of the server (a docstring included),
        not in the README and the guides of docs. Written out, as an escape or as an entity, it is the same glyph. No file is excused,
        comments included."""
        glyphs = "\u00b7\u2022\u2027\u2219\u22c5\u25cf\u25aa\u25e6"
        entities = re.compile(r"&(?:middot|bull);|&#(?:0*183|0*8226|x0*b7|x0*2022);|\\u(?:00b7|2022|2027|2219|22c5)|\\xb7", re.I)
        shown = re.compile(f"[{glyphs}]")
        root = WEB.parent.parent
        found = []
        for file in [*WEB.rglob("*"), root / "README.md", *(root / "docs").rglob("*.md")]:
            if file.suffix in {".js", ".css", ".html", ".svg", ".md"}:
                for number, line in enumerate(file.read_text().splitlines(), 1):
                    if shown.search(line) or entities.search(line):
                        found.append(f"{file.relative_to(root)}:{number}")
        for file in sorted((WEB.parent / "lab_ui").rglob("*.py")):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and (shown.search(node.value) or entities.search(node.value)):
                    found.append(f"{file.relative_to(root)}:{node.lineno}")
        self.assertEqual(found, [], "a middle dot or a bullet between words")

    def test_the_address_names_the_page_of_the_guide_on_screen(self):
        """A page that comes up because it was read last is written into the address, in place: a reload or a link shows the page
        the address says."""
        playground = (WEB / "js" / "pages" / "playground.js").read_text()
        self.assertIn("router.replace(stepHref(item, page))", playground)
        # both when the guide is read (the first time) and when the page comes up again with the guide already drawn
        self.assertEqual(playground.count("nameThePage(item"), 3, "the definition and its two calls")

    def test_every_section_of_the_page_is_a_page_main_registers_and_every_entry_of_the_menu_leads_to_one(self):
        """A section no page is registered for stays blank, and an entry of the menu with none leads to the Dashboard: the pages that main.js
        registers are the sections of index.html, and each entry of the menu is the `nav` of one."""
        index = (WEB / "index.html").read_text()
        main = (WEB / "js" / "main.js").read_text()
        names = re.findall(r"\b\w+Page\b", re.search(r"\[([^\]]*)\]\.forEach\(router\.register\)", main, re.S).group(1))
        ids, navs = set(), set()
        for name in names:
            found = next((re.search(rf"export const {name} = (.*)", module.read_text(), re.S) for module in MODULES
                          if re.search(rf"export const {name} = ", module.read_text())), None)
            self.assertIsNotNone(found, f"{name} is exported by a module")
            body = found.group(1)[:400]
            made = re.match(r'list\("(\w+)"', body)   # the three lists are made by one function, named after their kind
            ids.add(made.group(1) + "s" if made else re.search(r'\bid: "([\w-]+)"', body).group(1))
            navs.add(made.group(1) + "s" if made else re.search(r'\bnav: "([\w-]+)"', body).group(1))
        self.assertEqual(len(names), len(set(names)), "no page is registered twice")
        self.assertEqual(ids, set(re.findall(r'<section class="page[^"]*" id="page-([\w-]+)"', index)))
        self.assertEqual(set(re.findall(r'<a class="nav-item" href="[^"]*" data-nav="([\w-]+)"', index)), navs)
        self.assertIn("catalogsPage", names)
        self.assertIn('import { catalogsPage } from "./pages/catalogs.js";', main)


if __name__ == "__main__":
    unittest.main()
