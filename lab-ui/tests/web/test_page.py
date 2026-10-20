"""The page's own rules (web/js/workspace/split.js: the widths of the split, when it falls back, which tab shows, where a link or a
message of an app loads, the actions of a guide) and the page of a running playground over a small DOM (web/js/workspace/workspace.js: its
history, its tabs, its frames, the actions it sends), the tags the Images page offers (web/js/pages/images.js) and the guide's
markdown (web/js/components/markdown.js), tested by node in tests/js, and the places where the page's files must agree with them."""

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
    def test_the_rules_of_the_split_page(self):
        files = sorted(str(file) for file in JS.glob("*.test.mjs"))
        self.assertTrue(files)
        run = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class Agreements(Case):
    """What the rules of split.js say and the files that draw them must say the same."""

    def test_the_stylesheet_draws_the_guide_at_the_width_the_rules_start_it_at(self):
        """Before a script has set the width, the guide has the one it starts at: its minimum."""
        rules = (WEB / "js" / "workspace" / "split.js").read_text()
        minimum = re.search(r"^const GUIDE_MIN = (\d+);", rules, re.M)
        self.assertIsNotNone(minimum)
        self.assertIn(f"var(--guide-w, {minimum[1]}px)", (WEB / "css" / "pages.css").read_text())

    def test_the_page_tells_the_apps_in_its_frames_only_the_actions_of_its_guide_and_hears_three_messages_of_theirs(self):
        """The Lab UI knows nothing of the apps it frames: it never says which page of the guide is read, it posts one message, the action
        of the guide, to the app of the first tab at its own origin, and it hears three, an address to load, that the app listens and the
        page a UI is on."""
        for module in MODULES:
            self.assertNotIn("gravitee-lab:guide", module.read_text(), module.name)
        workspace = (WEB / "js" / "workspace" / "workspace.js").read_text()
        self.assertEqual(re.findall(r"\bpostMessage\([^;]*;", workspace),
                         ["postMessage(actionMessage(id), origin);"], "one message, the action, to the origin of the tab's UI")
        self.assertNotIn("targetOrigin", workspace)
        self.assertNotIn('"*"', workspace)
        rules = (WEB / "js" / "workspace" / "split.js").read_text()
        self.assertEqual(set(re.findall(r'gravitee-lab:[a-z]+', rules)), {"gravitee-lab:open", "gravitee-lab:action", "gravitee-lab:location", "gravitee-lab:ready"})
        self.assertEqual(re.findall(r'const (ACTION|LOCATION|READY|OPEN) = "(gravitee-lab:[a-z]+)"', rules),
                         [("OPEN", "gravitee-lab:open"), ("ACTION", "gravitee-lab:action"), ("LOCATION", "gravitee-lab:location"),
                          ("READY", "gravitee-lab:ready")])

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

    def test_a_page_of_the_guide_is_turned_in_the_place_of_the_one_on_screen_in_the_history(self):
        """The frames add entries to the page's history; a Back inside one must never undo a turn of the guide. So the guide's links
        and its chooser replace, one way for a playground that runs and one that does not, and nothing of the workspace pushes."""
        workspace = (WEB / "js" / "workspace" / "workspace.js").read_text()
        playground = (WEB / "js" / "pages" / "playground.js").read_text()
        for push in ("pushState", "router.", "location.hash", "location.assign", "history.go(", "history.back(", "history.forward("):
            self.assertNotIn(push, workspace, push)
        self.assertEqual(len(re.findall(r"router\.replace\(href\);", playground)), 1, "one function turns a page")
        for push in ("router.go(", "location.hash", "pushState"):
            self.assertNotIn(push, playground, push)
        # the chooser and the links of the guide use it, whatever the playground shows
        self.assertIn("choose: (value) => turn(stepHref(item, { slug: value })),", playground)
        self.assertIn('return turn(page.getAttribute("href"));', playground)
        # an address loaded in a frame is an entry of that UI's own, so that its Back (Gamma's "Back to logs") has a page of the UI to
        # go back to and never leaves this page; only a UI that came back, whose page is gone, is replaced
        self.assertIn("frame.contentWindow.location.href = url;", workspace)
        self.assertIn("frame.contentWindow.location.replace(url);", workspace)
        self.assertIn("load(panel, panel.url, { replace: true });", workspace)
        # a UI starts at its own start address: what was asked for before it was there is loaded once it has
        self.assertRegex(workspace, r'src: panel\.tab\.url, allow')
        self.assertIn("if (panel.url !== panel.tab.url) setTimeout(() => load(panel, panel.url), 0);", workspace)
        # every address the page loads goes through `load`, which drops what the page it replaces had said
        self.assertEqual(len(re.findall(r"location\.(?:href|replace)|\.src = ", workspace)), 3)
        self.assertEqual(len(re.findall(r"\bload\(", workspace)) - 1, 5, "the definition and five calls: open, home, the start's second page, a UI that came back, a Reload "
                         "of a page of another origin")
        # a Reload is the page's own when the lab can reach it: it is no address, so it is not one of those
        self.assertIn("panel.frame.contentWindow.location.reload();", workspace)

    def test_the_menu_is_the_same_on_every_page(self):
        """A running playground next to its UIs asks nothing of the menu: it is collapsed by the person or by a narrow window, as on any
        page, and takes its room; the page has what is left."""
        styles = "".join(sheet.read_text() for sheet in (WEB / "css").glob("*.css"))
        # the toggle is hidden in a narrow window only, where the menu is collapsed for good
        self.assertRegex(styles, r"@media \(max-width: 1100px\) \{\s*#sidebar-toggle \{ display: none; \}")
        self.assertEqual(len(re.findall(r"#sidebar-toggle \{ display: none; \}", styles)), 1)

    def test_skip_to_content_keeps_the_page(self):
        """Skip to content takes the keyboard to the page on screen: its address is no page of the router, which would lead to the
        Dashboard, so its click is the page's own."""
        main = (WEB / "js" / "main.js").read_text()
        self.assertRegex(main, r'querySelector\("\.skip"\)\.addEventListener\("click", \(e\) => \{\s*e\.preventDefault\(\);\s*'
                         r'document\.getElementById\("content"\)\.focus\(\);')

    def test_a_tooltip_is_one_line_and_a_popover_says_the_rest(self):
        """A tooltip is a label or one short line, never wrapped: anything longer is a popover (ui.js decides by LINE), which
        scrolls inside when the window is too short for it. A tooltip that wraps would be taller than 28 px."""
        components = (WEB / "css" / "components.css").read_text()
        tooltip = re.search(r"^\.tip-text \{([^}]*)\}", components, re.M).group(1)
        self.assertIn("white-space: nowrap;", tooltip)
        self.assertIn("padding: 6px 10px;", tooltip)   # 6 + 16 + 6: 28 px, one line
        popover = re.search(r"^\.tip-card \{([^}]*)\}", components, re.M).group(1)
        self.assertIn("max-height: calc(100dvh - 16px);", popover)
        self.assertNotIn("overflow", popover, "the box never clips: its arrow and the bridge to its button are outside it")
        body = re.search(r"^\.pop-body \{([^}]*)\}", components, re.M).group(1)
        self.assertIn("max-height: inherit;", body)
        self.assertIn("overflow: hidden auto;", body, "its body scrolls inside")
        self.assertNotIn("overflow", tooltip, "the line of a tooltip is cut inside it, not its box")
        ui = (WEB / "js" / "components" / "ui.js").read_text()
        self.assertIn("const LINE = 40;", ui)
        self.assertIn('if (e.target.closest?.(".tip-card")) return;', ui, "a scroll inside a popover is reading it: it stays open")

    def test_the_full_width_of_the_split_is_the_playground_pages_alone(self):
        """The split's missing cap belongs to the page that shows the split, while it is on screen: the page of the playground
        keeps its card in the DOM when another page is shown, and the others keep the width every page has. Its gutter is every
        page's, so that nothing moves sideways from a list to an item."""
        base = (WEB / "css" / "base.css").read_text()
        rules = re.findall(r"^main:has\(([^{]*)\) \{([^}]*)\}", base, re.M)
        self.assertEqual([selector for selector, _ in rules], ["#page-playground.fill:not([hidden])"])
        self.assertEqual(rules[0][1].strip(), "max-width: none;")
        self.assertNotIn("main:has(.pg-split", base)
        # the page decides between columns and rows by its container, whose margins do not depend on the layout
        workspace = (WEB / "js" / "workspace" / "workspace.js").read_text()
        self.assertIn("page.parentElement.clientWidth", workspace)
        self.assertNotRegex(workspace, r"page\.clientWidth")
        self.assertIn("const MARGINS = 24;", (WEB / "js" / "workspace" / "split.js").read_text())

    def test_a_narrow_window_lays_the_same_panels_out_as_rows(self):
        """Without the room for columns the guide and the UIs are one over the other: nothing is hidden, unloaded or disabled but the
        divider, so the page has no state of its own for a narrow window."""
        styles = (WEB / "css" / "pages.css").read_text()
        self.assertEqual(re.findall(r"^\.pg-split\.rows[^{]*\{", styles, re.M), [".pg-split.rows {", ".pg-split.rows .pg-divider {", ".pg-split.rows .pg-uis {"])
        self.assertIn("display: none;", re.search(r"^\.pg-split\.rows \.pg-divider \{([^}]*)\}", styles, re.M).group(1))
        # the running page never scrolls, whichever the layout: each panel does
        self.assertIn('root.classList.add("fill");', (WEB / "js" / "workspace" / "workspace.js").read_text())

    def test_a_playgrounds_row_opens_its_page_in_every_state_and_starts_and_stops_it_as_every_row(self):
        """A playground's row has Open in every state, and the Start or Stop every row has: its Start starts it there,
        its Stop asks as its page's does. A Start that only opened the page would say what it does not do."""
        rows = (WEB / "js" / "pages" / "list.js").read_text()
        self.assertEqual(rows.count('">Open</a>'), 1)
        self.assertNotIn("Open Lab", rows)
        self.assertNotIn("Open Lab", (WEB / "js" / "pages" / "stack.js").read_text())
        self.assertIn('<span class="warn-slot">${stuck ? stuck.html : ""}</span>${itemButton(x)}', rows)   # its own slot, just before
        self.assertIn('if (x.kind === "playground" && action.do === "stop") return stopPlayground(x);', rows)
        self.assertNotIn("router.go(`#/playgrounds", rows, "no button of a row says Start and navigates")

    def test_the_reason_start_is_off_wraps_under_its_button_in_full(self):
        """A note is one clipped line in a row (an ellipsis, no wrap). Under the Start button it is the reason itself: it wraps,
        whole, inside the card, so that it neither is cut nor makes the page scroll sideways."""
        styles = (WEB / "css" / "pages.css").read_text()
        rule = re.search(r"^\.pg-start-note \.list-note \{([^}]*)\}", styles, re.M).group(1)
        for declaration in ("display: block;", "overflow: visible;", "white-space: normal;"):
            self.assertIn(declaration, rule)
        self.assertIn("noteHtml(item)", (WEB / "js" / "pages" / "playground.js").read_text())

    def test_the_head_of_a_playground_opens_its_first_console_with_the_component_every_row_uses(self):
        """One component for a console that opens or says why it cannot: the rows' Open, the stack's and the head's Open <name>."""
        playground = (WEB / "js" / "pages" / "playground.js").read_text()
        self.assertIn("openButton(item.tabs.slice(0, 1), null, { named: true })", playground)
        self.assertNotIn("is not running", playground, "what a console that is down says is `unopened`, said once")

    def test_a_tab_keeps_its_name_whole_and_the_bar_wraps_when_they_do_not_fit(self):
        """No tab is cut, scrolled out of reach or given a tooltip for being cut: a row of tabs that is too long goes on a second row."""
        styles = (WEB / "css" / "pages.css").read_text()
        segmented = re.search(r"^\.segmented \{([^}]*)\}", (WEB / "css" / "components.css").read_text(), re.M).group(1)
        self.assertIn("flex-wrap: wrap;", segmented, "every segmented control wraps its choices, the tabs among them")
        bar = re.search(r"^\.pg-bar \.segmented \{([^}]*)\}", styles, re.M).group(1)
        self.assertNotIn("overflow", bar)
        self.assertNotIn("text-overflow", "".join(re.findall(r"^\.pg-bar[^{]*\{[^}]*\}", styles, re.M)))

    def test_the_guide_takes_the_whole_width_of_its_panel(self):
        """No measure on its text: paragraphs, lists, quotes, headings, tables and code run to the padding; only a picture or a diagram is
        capped, being no text."""
        styles = (WEB / "css" / "pages.css").read_text()
        self.assertNotIn("--measure", styles)
        capped = [rule.split(" {")[0] for rule in re.findall(r"^\.guide[^{]*\{[^}]*\}", styles, re.M) if "max-width" in rule]
        self.assertEqual(capped, [".guide :is(figure, p:has(> img))", ".guide-nav .combo-btn", ".guide img"])
        self.assertNotIn("max-width: var(--", "".join(re.findall(r"^\.guide-foot[^{]*\{[^}]*\}", styles, re.M)))
        # lines run to the edge of the panel: the browser does not move a word down to spare a short last line, and the editor's preview
        # of the same page has no column of its own
        self.assertNotIn("text-wrap: pretty", styles)
        self.assertNotRegex("".join(re.findall(r"^\.editor-preview[^{]*\{[^}]*\}", styles, re.M)), r"max-width")

    def test_what_to_open_is_one_component_on_the_stack_and_in_the_details_of_a_playground(self):
        """Both group the consoles of the components under their product, with the same rows (link-group, link-section)."""
        for page in ("stack.js", "playground.js"):
            self.assertIn("paintLinks(", (WEB / "js" / "pages" / page).read_text(), page)
            self.assertNotIn("link-group", (WEB / "js" / "pages" / page).read_text(), page)
        self.assertEqual((WEB / "js" / "components" / "ui.js").read_text().count('class="link-group"'), 1)
        # one order for both: no option makes one page differ from the other
        for page in ("stack.js", "playground.js"):
            self.assertNotIn("own", "".join(re.findall(r"paintLinks\([^;]*;", (WEB / "js" / "pages" / page).read_text())), page)
        self.assertIn("export function paintLinks(el, state, links, platforms = []) {", (WEB / "js" / "components" / "ui.js").read_text())

    def test_a_diagram_draws_its_edge_labels_in_the_pages_colors_text_included(self):
        """Mermaid gives the paragraph inside an edge label a background of its own (a pink one in its base theme), which hides the
        label's text in the dark theme: the rule that sets the page's surface covers it, not only the span and the box around it."""
        styles = (WEB / "css" / "pages.css").read_text()
        rule = re.search(r"^\.diagram :is\(\[class~=\"edgeLabel\"\][^{]*\{([^}]*)\}", styles, re.M)
        self.assertIsNotNone(rule)
        self.assertIn('[class~="edgeLabel"] p', rule[0])
        self.assertIn("background: var(--surface) !important;", rule[1])

    def test_a_place_is_written_by_its_environment_then_its_platform_in_brackets_and_never_with_a_dot(self):
        """"Development (My Account)": the middle dot is not a separator of the Lab UI, so what names a place, and the tests of it, have
        none; the page builds the name of a place its platform no longer answers for in one function."""
        for file in ["js/platform/places.js", "js/pages/playground.js", "js/pages/platforms.js", "js/dialogs/platform.js", "js/platform/environments.js"]:
            self.assertNotIn(" \u00b7 ", (WEB / file).read_text(), file)
        for file in ["playground.test.mjs", "lists.test.mjs", "platforms.test.mjs", "environments.test.mjs"]:
            self.assertNotIn(" \u00b7 ", (JS / file).read_text(), file)
        self.assertEqual(len(re.findall(r"\$\{environment\} \(\$\{platform\}\)", (WEB / "js" / "platform" / "places.js").read_text())), 1)

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
        for file in sorted((WEB.parent / "lab_ui").glob("*.py")):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and (shown.search(node.value) or entities.search(node.value)):
                    found.append(f"{file.relative_to(root)}:{node.lineno}")
        self.assertEqual(found, [], "a middle dot or a bullet between words")

    def test_the_place_chosen_is_the_default_of_every_playground_and_is_kept_and_read_in_one_module(self):
        """One key in the browser, 'deploy', whichever playground the place was chosen on; places.js reads and writes it, and decides which
        place a playground is on (chosenPlace), so that another rule for the default is a change of that function alone."""
        places = (WEB / "js" / "platform" / "places.js").read_text()
        self.assertIn('const KEY = "deploy";', places)
        self.assertEqual(len(re.findall(r"export function chosenPlace\(item\) \{", places)), 1)
        for module in MODULES:
            if module.name != "places.js":
                source = module.read_text()
                self.assertNotRegex(source, r"(?:recall|remember)\(\s*[`\"']deploy", module.name)
                self.assertNotIn("deploy:${", source, module.name)

    def test_the_address_names_the_page_of_the_guide_on_screen(self):
        """A page that comes up because it was read last is written into the address, in place: a reload or a link shows the page
        the address says."""
        playground = (WEB / "js" / "pages" / "playground.js").read_text()
        self.assertIn("router.replace(stepHref(item, page))", playground)
        # both when the guide is read (the first time) and when the page comes up again with the guide already drawn
        self.assertEqual(playground.count("nameThePage(item"), 3, "the definition and its two calls")

    def test_the_welcome_dialog_ends_with_the_license_and_the_links_to_the_source_and_its_guide_built_from_one_constant(self):
        """The address of the repository is written once, as the constant at the top of welcome.js, and nowhere else on the page: the
        links at the end of the dialog, to the source and to the troubleshooting guide, take it from there."""
        welcome = (WEB / "js" / "dialogs" / "welcome.js").read_text()
        self.assertEqual(re.findall(r'^const (\w+) = "https://github\.com/[\w.-]+/[\w.-]+";', welcome, re.M), ["REPOSITORY"])
        self.assertLess(welcome.index("const REPOSITORY"), welcome.index("const SEEN"), "at the top, before the module's own names")
        self.assertEqual(welcome.count("github.com"), 1)
        self.assertEqual(welcome.count("${REPOSITORY}"), 2, "the source, and the guide's address made of it")
        self.assertIn("const TROUBLESHOOTING = `${REPOSITORY}/blob/main/docs/troubleshooting.md`;", welcome)
        self.assertIn('Gravitee Lab, AGPL-3.0, <a class="text-link" href="${REPOSITORY}" target="_blank" rel="noopener">Source</a>,\n'
                      '      <a class="text-link" href="${TROUBLESHOOTING}" target="_blank" rel="noopener">Troubleshooting</a></p>`;', welcome)
        for module in MODULES:
            if module.name != "welcome.js":
                self.assertNotIn("github.com", module.read_text(), module.name)
        self.assertNotIn("github.com", (WEB / "index.html").read_text())

    def test_the_dashboard_opens_the_lab_and_what_was_the_stack_is_named_containers_where_a_person_reads_it(self):
        """The Dashboard is the first item of the menu, alone above its sections, and the page that opens with no address; the Stack is
        the Containers in the menu, in the title of its page and in the dialog that says how the Lab works, and its old address leads
        to them."""
        index = (WEB / "index.html").read_text()
        nav = re.search(r'<nav class="nav"[^>]*>(.*?)</nav>', index, re.S).group(1)
        self.assertEqual(re.findall(r'<a class="nav-item" href="(#/[a-z]+)"', nav)[0], "#/dashboard", "the first item of the menu")
        self.assertEqual(re.findall(r'<span class="nav-label">([A-Za-z]+)</span>', nav), ["Library", "Monitor", "Configure"],
                         "the Dashboard has no section of its own")
        self.assertLess(nav.index('href="#/dashboard"'), nav.index('<span class="nav-label">Library</span>'))
        self.assertIn('<a class="nav-item" href="#/dashboard" data-nav="dashboard" aria-label="Dashboard" data-tip="Dashboard" data-tip-collapsed><svg aria-hidden="true" focusable="false"><use href="#i-gauge"/></svg><span>Dashboard</span></a>', index)
        monitor = re.search(r'<span class="nav-label">Monitor</span>(.*?)<span class="nav-label">', index, re.S).group(1)
        self.assertEqual(re.findall(r'href="(#/[a-z]+)"', monitor), ["#/containers", "#/logs"])
        self.assertEqual(re.findall(r'<span>([A-Za-z]+)</span>', monitor), ["Containers", "Logs"])
        self.assertIn('id="health" href="#/containers"', index)
        for file in [WEB / "index.html", *MODULES]:
            source = re.sub(r"<!--.*?-->|/\*.*?\*/|\s//[^\n]*", "", file.read_text(), flags=re.S)
            self.assertNotRegex(source, r"""["'`>]Stack\b""", f"{file.name} names the Containers the Stack")
            if file.name != "core.js":   # where its old address leads to them
                self.assertNotIn("#/stack", source, file.name)
        core = (WEB / "js" / "core.js").read_text()
        self.assertIn('const MOVED = { "#/stack": "#/containers", "#/platforms": "#/cloud" };', core)
        self.assertIn('if (!page) return router.replace("#/dashboard");', core)

    def test_the_library_holds_the_lists_and_the_catalogs_they_come_from_and_the_last_one_has_a_dot_for_what_needs_pressing(self):
        """The group of the menu is the Library: Playgrounds, Setups, Add-ons, then Catalogs, which is always there (it is how a person finds
        where items come from). Its entry has its own icon, the name the menu gives it and the tooltip that says what it is for, its name
        while only icons show, and a dot that is hidden until a catalog has something to press. Its page has a section of its own. The
        word Catalog stands for the object alone: no group of the menu bears it."""
        index = (WEB / "index.html").read_text()
        library = re.search(r'<span class="nav-label">Library</span>(.*?)<span class="nav-label">', index, re.S).group(1)
        self.assertEqual(re.findall(r'href="(#/[a-z]+)"', library), ["#/playgrounds", "#/setups", "#/addons", "#/catalogs"])
        self.assertEqual(re.findall(r'<span>([A-Za-z-]+)</span>', library), ["Playgrounds", "Setups", "Add-ons", "Catalogs"])
        self.assertIn('<a class="nav-item" href="#/catalogs" data-nav="catalogs" aria-label="Catalogs" data-tip="Where items come from" '
                      'data-tip-collapsed="Catalogs"><svg aria-hidden="true" focusable="false"><use href="#i-catalog"/></svg>'
                      '<span>Catalogs</span><i class="nav-dot" aria-hidden="true" hidden></i></a>', library)
        self.assertEqual(index.count('class="nav-dot"'), 1, "the Catalogs entry alone has a dot")
        self.assertEqual(len(re.findall(r'<symbol id="i-catalog" ', index)), 1)
        self.assertIn('<section class="page" id="page-catalogs" hidden></section>', index)
        self.assertNotIn('nav-label">Catalog<', index)

    def test_the_dot_of_the_menu_is_on_the_corner_of_the_icon_while_only_icons_show_and_the_collapsed_menu_does_not_hide_it(self):
        """The collapsed menu hides every span of an entry, so the dot is not one: it keeps showing, as an element of its own, on the corner of
        the icon (a rule of an id, which no class outweighs, is what hides the spans)."""
        styles = (WEB / "css" / "components.css").read_text()
        dot = re.search(r"^\.nav-dot \{([^}]*)\}", styles, re.M).group(1)
        for declaration in ("width: 8px;", "height: 8px;", "background: var(--warn);"):
            self.assertIn(declaration, dot)
        collapsed = re.search(r"^body\.nav-collapsed \.nav-dot \{([^}]*)\}", styles, re.M).group(1)
        self.assertIn("position: absolute;", collapsed)
        self.assertNotIn("display", collapsed)
        self.assertRegex((WEB / "css" / "base.css").read_text(), r"body\.nav-collapsed :is\([^)]*\.nav-item span")
        self.assertNotRegex(re.search(r'<a class="nav-item" href="#/catalogs".*?</a>', (WEB / "index.html").read_text()).group(0), r'<span class="nav-dot"')
        self.assertRegex(styles, r"(?m)^\.nav-item:has\(> \.nav-dot\) \{ position: relative; \}")
        self.assertIn("[hidden] { display: none !important; }", (WEB / "css" / "base.css").read_text(), "hidden stays hidden")

    def test_the_cloud_section_is_named_gravitee_cloud_and_its_old_address_leads_there(self):
        """The page of the Gravitee Cloud accounts is Gravitee Cloud in the menu, its title and its address; the old
        #/platforms and every address under it lead there. The local stack keeps its word: the platform."""
        index = (WEB / "index.html").read_text()
        configure = re.search(r'<span class="nav-label">Configure</span>(.*?)</nav>', index, re.S).group(1)
        self.assertIn('<a class="nav-item" href="#/cloud" data-nav="cloud" aria-label="Gravitee Cloud" data-tip="Gravitee Cloud" data-tip-collapsed><svg aria-hidden="true" focusable="false"><use href="#i-cloud"/></svg><span>Gravitee Cloud</span></a>', configure)
        self.assertIn('<section class="page" id="page-cloud" hidden></section>', index)
        page = (WEB / "js" / "pages" / "platforms.js").read_text()
        self.assertIn('pattern: /^#\\/cloud(?:\\/([a-z0-9-]+))?$/, nav: "cloud", title: "Gravitee Cloud"', page)
        self.assertIn("<h1>Gravitee Cloud</h1>", page)
        for file in [WEB / "index.html", *MODULES]:
            source = re.sub(r"<!--.*?-->|/\*.*?\*/|\s//[^\n]*", "", file.read_text(), flags=re.S)
            self.assertNotRegex(source, r"""["'`>]Platforms\b""", f"{file.name} names the Gravitee Cloud page Platforms")
            if file.name != "core.js":
                self.assertNotIn("#/platforms", source, file.name)
        # What the page was called is named nowhere in it: not in its menu, its pages, its styles or its address
        for file in [WEB / "index.html", *MODULES, *(WEB / "css").glob("*.css")]:
            self.assertNotRegex(file.read_text(), r"(?i)overview", file.name)
        self.assertIn('<section class="page" id="page-dashboard" hidden></section>', index)

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

    def test_a_choice_that_cannot_be_taken_looks_as_a_disabled_button_does_and_the_layers_hide_their_muted_word_with_their_code(self):
        """A control that cannot be used keeps the pointer and says why (aria-disabled): the Cloud segment of the core choice and its card of
        the welcome dialog look as the disabled button does, which they are not drawn by. The welcome dialog's layers say where an item lives in
        code or in a muted word, and a phone hides either."""
        components = (WEB / "css" / "components.css").read_text()
        button = re.search(r'^\.btn:is\(:disabled, \[aria-disabled="true"\]:not\(\.busy, \.said\)\) \{([^}]*)\}', components, re.M).group(1)
        rule = re.search(r'^\.segmented button\[aria-disabled="true"\], \.core-card\[aria-disabled="true"\] \{([^}]*)\}', components, re.M).group(1)
        for declaration in re.findall(r"(?:opacity|cursor): [\w.]+;", button):
            self.assertIn(declaration, rule)
        self.assertIn(".segmented button:hover:not([aria-disabled=\"true\"])", components, "a choice that waits does not answer a hover")
        pages = (WEB / "css" / "pages.css").read_text()
        self.assertIn(".core-card:hover:not([aria-disabled=\"true\"])", pages)
        self.assertRegex(pages, r"(?m)^\.layer > :is\(code, \.muted\) \{[^}]*white-space: nowrap;")
        self.assertIn(".layer > :is(code, .muted) { display: none; }", pages.rsplit("@media (max-width: 640px) {", 1)[1])

    def test_on_a_phone_no_dialog_scrolls_sideways_for_a_line_that_does_not_fit(self):
        """The example of the welcome dialog keeps its lines and scrolls inside its own box; the rows of an effects dialog put their label over
        what they say, so that a branch or a destination fits its card, and a pill that is still too long is cut, its words whole in its tooltip."""
        pages = (WEB / "css" / "pages.css").read_text()
        self.assertRegex(pages, r"(?m)^\.welcome-code \{[^}]*white-space: pre; overflow-x: auto;")
        components = (WEB / "css" / "components.css").read_text()
        self.assertRegex(components, r"@media \(max-width: 480px\) \{ \.choice-row \{ grid-template-columns: minmax\(0, 1fr\);")
        self.assertIn('<span class="pill-text" data-tip="${esc(named(i).name)}" data-tip-cut>', (WEB / "js" / "components" / "ui.js").read_text())


if __name__ == "__main__":
    unittest.main()
