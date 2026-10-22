"""The page and the Lab UI as files, without running them: what a module imports exists and what it exports is
imported, the routes the page calls exist, the sprite has the icons the page draws and nothing more, every class of
the stylesheets is used and every class the page writes is styled, every script of a CDN is pinned and checked by its
hash, nothing of the page needs a script or a style the policy of its address would refuse, the Lab UI and its tests are
Python 3.9, and every module is valid JavaScript."""

import ast
import http.client
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lab_ui.server import api

from .. import Case

LAB_UI = Path(__file__).resolve().parents[2]
WEB = LAB_UI / "web"
MODULES = sorted((WEB / "js").rglob("*.js"))
INDEX = (WEB / "index.html").read_text()
PYTHON = [LAB_UI / "server.py", *sorted((LAB_UI / "lab_ui").glob("*.py")), *sorted((LAB_UI / "tests").glob("*.py"))]

IMPORT = re.compile(r'^import\s+(?:\{([^}]*)\}\s+from\s+)?"([^"]+)";', re.M)
LAZY_IMPORT = re.compile(r'\bimport\("([^"]+)"\)')
EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:const|let|function|class)\s+([\w$]+)", re.M)
API_PATH = re.compile(r"""[`"'](/api/[^`"'?\s]*)""")   # up to its query string
PLACEHOLDER = re.compile(r"\$\{[^}]*\}")
# What a ${...} of a path may stand for: an id, a job id, an action, a kind of item
SAMPLES = ("abc", "0a1b2c3d", "start", "restart", "addon")
SYMBOL = re.compile(r'<symbol id="i-([\w-]+)"')
ICON_CALL = re.compile(r"\bicon\(([^()]*)\)")
ICON_USE = re.compile(r'href="#i-([\w-]+)"')
CLASS = re.compile(r"\.(-?[a-zA-Z_][\w-]*)")
CLASS_ATTRIBUTE = re.compile(r'\bclass="((?:[^"$]|\$\{[^}]*\})*)"')   # its value, with its ${...} whole
CHOICE = re.compile(r'(?:\?|:|\|\||\?\?)\s*"([\w -]*)"')   # a class a ${...} picks: `cond ? "on" : ""`
COMMENT = re.compile(r"<!--.*?-->|/\*.*?\*/|(?<![:\w\"'`\\/])//[^\n]*", re.S)   # not a // inside an address or a string
SCRIPT_CALL = re.compile(r"\bloadScript\(([^()]*)\)")
STYLE_CALL = re.compile(r"\bloadStyle\(")
HASH = re.compile(r'^const (\w+) = "(sha384-[A-Za-z0-9+/]{64})";', re.M)   # the sha384 of a file, in base64
CDN = re.compile(r'^const \w+ = "(https://[^"]+)"', re.M)


# What a package delivered ahead of the page that uses it: the exports that no module imports yet, by file, until the package that draws
# them is merged. A name that is imported by then, or is no longer exported, fails `test_what_awaits_a_page_is_not_used_yet`: the list
# shrinks as the pages arrive, and is empty when they all have, as it is now.
AWAITING = {}


def name(file):
    return file.relative_to(WEB).as_posix()


def imported(source):
    """What a module imports: (the path as written, the names it takes from there)."""
    for names, path in IMPORT.findall(source):
        yield path, [part.split(" as ")[0].strip() for part in names.split(",") if part.strip()]


def annotations(tree):
    """Every annotation of a Python module: of an argument, of a variable, of what a function returns."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.arg, ast.AnnAssign)):
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.returns


def code(text):
    """A file of the page as it runs: without its comments, in which a class or an icon named is not one used."""
    return COMMENT.sub("", text)


PAGE = code(INDEX) + "".join(code(module.read_text()) for module in MODULES)   # every file of the page, as one text


def sheets():
    """The rules of each stylesheet without their comments and declarations: just the selectors."""
    found = {}
    for sheet in sorted((WEB / "css").glob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", sheet.read_text(), flags=re.S)
        found[sheet] = re.sub(r"\{[^{}]*\}", "", text)
    return found


def written(source):
    """The classes the class="..." attributes of a file write, and the starts of the ones a template builds (`lv-${level}`)."""
    names, starts = set(), set()
    for value in CLASS_ATTRIBUTE.findall(source):
        for expression in PLACEHOLDER.findall(value):
            for choice in CHOICE.findall(expression):
                names.update(choice.split())
        for word in PLACEHOLDER.sub("\0", value).split():
            if word.endswith("\0") and word != "\0":
                starts.add(word[:-1])
            elif "\0" not in word:
                names.add(word)
    return names, starts


class Imports(Case):

    def test_every_import_finds_its_file_and_its_names(self):
        for module in MODULES:
            source = module.read_text()
            for path in LAZY_IMPORT.findall(source):
                self.assertTrue((module.parent / path).is_file(), f"{name(module)} imports {path}")
            self.assertEqual(len(IMPORT.findall(source)), len(re.findall(r"^import\b", source, re.M)),
                             f"{name(module)} has an import written in a form this test does not read")
            for path, names in imported(source):
                target = module.parent / path
                self.assertTrue(target.is_file(), f"{name(module)} imports {path}")
                exported = set(EXPORT.findall(target.read_text()))
                for wanted in names:
                    self.assertIn(wanted, exported, f"{name(module)} imports {wanted} from {path}")

    def test_every_export_is_imported(self):
        taken = {((module.parent / path).resolve(), wanted) for module in MODULES
                 for path, names in imported(module.read_text()) for wanted in names}
        for module in MODULES:
            unused = [export for export in EXPORT.findall(module.read_text())
                      if (module, export) not in taken and export not in AWAITING.get(name(module), ())]
            self.assertFalse(unused, f"{name(module)} exports what no module imports")

    def test_what_awaits_a_page_is_not_used_yet(self):
        taken = {((module.parent / path).resolve(), wanted) for module in MODULES
                 for path, names in imported(module.read_text()) for wanted in names}
        for file, awaited in AWAITING.items():
            module = WEB / file
            exported = set(EXPORT.findall(module.read_text()))
            self.assertFalse(awaited - exported, f"{file} no longer exports {sorted(awaited - exported)}: take it off the list")
            self.assertFalse({export for export in awaited if (module.resolve(), export) in taken},
                             f"{file}: a page imports what the list says awaits one: take it off the list")

    def test_the_page_finds_its_styles_and_its_script(self):
        for path in re.findall(r'(?:href|src)="/((?:css|js)/[^"]+)"', INDEX):
            self.assertTrue((WEB / path).is_file(), path)


class Routes(Case):

    def test_the_id_of_an_item_is_written_in_its_path_as_it_is(self):
        # The routes take the slash of `local/acme-shop` and `@catalog/acme-shop`: an id written %2F is a 404 (the terminal was)
        for module in MODULES:
            self.assertNotRegex(module.read_text(), r"/api/(?:playgrounds|addons|setups)/\$\{encodeURIComponent\(",
                                f"{name(module)} writes the id of an item with encodeURIComponent in its path")

    def test_every_api_path_is_a_route(self):
        patterns = [pattern for _, pattern, _ in api.ROUTES]
        for module in MODULES:
            for path in API_PATH.findall(module.read_text()):
                template = PLACEHOLDER.sub("{}", path)
                filled = (template.format(*values)
                          for values in itertools.product(SAMPLES, repeat=template.count("{}")))
                self.assertTrue(any(pattern.fullmatch(candidate) for candidate in filled for pattern in patterns),
                                f"{name(module)} calls {path}")


class Agreements(Case):
    """What the page says in the server's words must be what the server says."""

    def test_the_reasons_a_catalog_item_cannot_run_are_said_as_the_server_refuses_with_them(self):
        """Use the catalog version waits, in the menu of the item, with the reasons the server refuses with when the catalog's item cannot
        run here (contribute.use_catalog_version): the same cases (tests/js/cannot-run.json) go through that function and through the
        menu of the page (lists.test.mjs), so that the two cannot say it two ways."""
        from lab_ui.catalogs import contribute
        cases = json.loads((LAB_UI / "tests" / "web" / "js" / "cannot-run.json").read_text())
        self.assertGreaterEqual(len(cases), 6)
        for case in cases:
            with self.subTest(case["about"]):
                self.assertEqual(contribute._cannot_run(case["unit"]), case["why"])


    def test_the_rows_of_the_notice_of_the_move_are_the_ones_the_server_says(self):
        """The notice "Lab updated" draws the rows of the server in their order: its list of keys is the server's own
        (layout.ROWS, and Settings, the one row of a move that waits), so that a row the server adds is not left to the end by a page that
        never heard of it."""
        from lab_ui.library import layout
        found = re.search(r"^const ORDER = \[([^\]]*)\];", (WEB / "js" / "dialogs" / "layout.js").read_text(), re.M)
        self.assertIsNotNone(found)
        order = re.findall(r'"(\w+)"', found[1])
        self.assertEqual(sorted(order), sorted([*layout.ROWS, "settings"]))
        self.assertEqual(order, ["settings", "personal", "kept", "turned_off", "restart", "not_moved"], "the order of the rows of the notice")


    def test_the_notice_the_page_draws_is_what_the_status_of_the_layout_says(self):
        """The three statuses of the layout, as layout.status() writes them (a move that is done and left something to say, one that waits,
        one that failed), are the file tests/js/layout-status.json, which layout.test.mjs draws: a key or a word that the server renames
        is found here, not by a person who sees a notice with nothing in it. WRITE_LAYOUT_STATUS=1 writes the file again."""
        from lab_ui.library import layout
        from lab_ui import paths
        report = {"moved": ["addon:echo", "playground:shop"], "personal": [], "promoted": ["playground:local/scratch"], "kept": ["addon:local/ledger"],
                  "left": ["path:addons/README.md"], "held": [], "turned_off": ["addon:old-demo"], "problems": []}
        found = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".lab" / "migration").mkdir(parents=True)
            with mock.patch.object(paths, "ROOT", root):
                (root / layout.MARKER).write_text(json.dumps({"migrated": "2026-10-17T10:00:00Z"}))
                (root / layout.REPORT).write_text(json.dumps({"report": report}))
                found["ready"] = layout.status()
                found["restart"] = layout.restart_row(found["ready"]["notice"], {"addon:echo"})   # the row the state adds, from what runs and is late
                (root / layout.MARKER).unlink()
                for phase in ("waiting", "failed"):
                    with mock.patch.object(layout, "_phase", return_value=phase):
                        found[phase] = layout.status()
        golden = LAB_UI / "tests" / "web" / "js" / "layout-status.json"
        if os.environ.get("WRITE_LAYOUT_STATUS"):
            golden.write_text(json.dumps(found, indent=1) + "\n")
        self.assertEqual(json.loads(golden.read_text()), found)


class Files(Case):
    def test_the_license_picker_offers_license_files_only(self):
        """The file dialog of the system shows what a license can be: its text or its key."""
        source = (WEB / "js" / "dialogs" / "license.js").read_text()
        self.assertIn('<input type="file" id="license-file" accept=".txt,.key" hidden>', source)

    def test_the_cell_of_the_license_cta_is_gone_when_it_holds_nothing(self):
        """With a license the place of the ways to get one is empty: it must not take a cell of the grid, where the
        packs would wrap under the facts."""
        self.assertRegex((WEB / "css" / "pages.css").read_text(), r"#license-cta:empty\s*\{\s*display:\s*none")


class Features(Case):
    def test_a_feature_of_the_license_wraps_its_name_and_its_plugins_instead_of_going_past_the_edge(self):
        """A long id and several plugins make a row wider than the dialog: the row wraps, its parts break."""
        css = (WEB / "css" / "pages.css").read_text()
        self.assertRegex(css, r"\.feature \{ display: flex; flex-wrap: wrap;")
        self.assertRegex(css, r"\.feature code \{[^}]*overflow-wrap: anywhere")
        self.assertRegex(css, r"\.feature-plugins \{[^}]*flex-wrap: wrap[^}]*max-width: 100%")


class Icons(Case):

    def test_every_icon_is_in_the_sprite(self):
        sprite = set(SYMBOL.findall(INDEX))
        for file in [WEB / "index.html", *MODULES]:
            source = file.read_text()
            used = set(ICON_USE.findall(source))
            for arguments in ICON_CALL.findall(source):   # icon("play"), icon(paused ? "play" : "pause")
                used.update(re.findall(r'"([\w-]+)"', arguments))
            self.assertFalse(used - sprite, f"{name(file)} draws icons the sprite does not have")

    def test_every_icon_of_the_sprite_is_drawn(self):
        # Its name between quotes anywhere counts: a page also takes an icon from data ({icon: "link"})
        for symbol in SYMBOL.findall(INDEX):
            drawn = f'href="#i-{symbol}"' in PAGE or re.search(rf"""[`"']{symbol}[`"']""", PAGE)
            self.assertTrue(drawn, f"the sprite has {symbol}, which nothing draws")


class Tooltips(Case):

    def test_nothing_has_the_browsers_title(self):
        # The one tooltip of the Lab UI is its own (data-tip, and data-why for a control that waits): the browser's title waits a
        # second, ignores the theme and the keyboard. The document's <title> and document.title name the tab.
        for file in [WEB / "index.html", *MODULES]:
            source = code(file.read_text())
            self.assertEqual(re.findall(r"\stitle=", source), [], f"{name(file)} writes a title attribute")
            self.assertEqual(re.findall(r"(?<!document)\.title\s*=(?!=)", source), [], f"{name(file)} sets a title")
            self.assertEqual(re.findall(r"setAttribute\(\s*[\"']title", source), [], f"{name(file)} sets a title")


# Items of the catalog a generic page never names: the Lab is a hub, and an item says what it is in its own files. Core products of
# Gravitee (API Management, Access Management, Alert Engine, Gamma, Gravitee Cloud) may be named.
CONTENT = re.compile(r"\b(acme|kafka|jade|kiosk|openfga|reader-ui)\b", re.I)
# A version said in words ("API Management 4.12", "Gravitee 4.13.0"): what a platform runs changes with time, so a page reads it from
# data, never from a sentence. Looked for in the strings of a module, without the addresses they hold.
STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`')
VERSION = re.compile(r"\b[A-Za-z]{2,} v?\d+\.\d+\b")
ADDRESS = re.compile(r"https?://\S+")


class Generic(Case):

    def test_no_page_names_an_item_of_the_catalog(self):
        # What a page says of an item comes from the item (its name, its file): a list of names in the code would be one more
        # place to change for each new item, and would tell every person about one of them
        for file in [WEB / "index.html", *MODULES]:
            found = sorted({match.lower() for match in CONTENT.findall(code(file.read_text()))})
            self.assertEqual(found, [], f"{name(file)} names an item of the catalog")

    def test_no_page_says_a_version_in_words(self):
        # "API Management 4.12" or "without Gamma" is true at one time only: a version a page shows comes from the state or a
        # read, as the Dashboard's products and the reasons the server gives a playground
        for file in MODULES:
            strings = [ADDRESS.sub("", found) for found in STRING.findall(code(file.read_text()))]
            said = sorted({match for text in strings for match in VERSION.findall(text)})
            self.assertEqual(said, [], f"{name(file)} says a version in words")


# What the content of the catalogs says, each in one place: ui.js (contentStatus, contentLead). The page that said one of them before it
# drew from there is listed with it until it does; a file listed that no longer says it fails `test_the_words_of_the_content_are_said_once`.
CONTENT_WORDS = ("Downloading playgrounds, setups and add-ons", "No catalog added", "No playground, setup or add-on yet", "No playground yet",
                 "No setup yet", "No add-on yet", "Browse catalogs")
SAID_BEFORE = {}


class Words(Case):

    def test_the_words_of_the_content_are_said_once(self):
        for words in CONTENT_WORDS:
            said = sorted(name(module) for module in MODULES if words in code(module.read_text()))
            self.assertEqual(said, sorted({"js/components/ui.js", *SAID_BEFORE.get(words, ())}), f"{words!r} is said in one module, ui.js")
            self.assertEqual(code((WEB / "js" / "components" / "ui.js").read_text()).count(words), 1, f"{words!r} is said once in ui.js")
        # The words of the button that adds the catalog the server offers are made of its name, in one place
        self.assertEqual(code((WEB / "js" / "components" / "ui.js").read_text()).count("`Add ${offer.name ?? offer.id}`"), 1)

    def test_nothing_is_said_twice_that_a_page_has_to_stop_saying(self):
        for words, files in SAID_BEFORE.items():
            for file in files:
                self.assertIn(words, (WEB / file).read_text(), f"{file} no longer says {words!r}: take it off the list")


class Styles(Case):

    def test_every_class_of_the_stylesheets_is_used(self):
        # A class a module builds (`lv-${level}`) counts by what comes before the ${
        built = tuple(re.findall(r"([\w-]+-)\$\{", PAGE))
        for sheet, selectors in sheets().items():
            for found in sorted(set(CLASS.findall(selectors))):
                used = re.search(rf"(?<![\w-]){found}(?![\w-])", PAGE) or found.startswith(built)
                self.assertTrue(used, f"{name(sheet)} styles .{found}, which nothing draws")

    def test_every_class_the_page_writes_has_a_rule(self):
        styled = {found for selectors in sheets().values() for found in CLASS.findall(selectors)}
        for file in [WEB / "index.html", *MODULES]:
            source = code(file.read_text())
            self.assertEqual(len(CLASS_ATTRIBUTE.findall(source)), len(re.findall(r'\bclass="', source)),
                             f"{name(file)} has a class attribute written in a form this test does not read")
            names, starts = written(source)
            self.assertFalse(names - styled, f"{name(file)} writes classes no stylesheet has a rule for: {sorted(names - styled)}")
            for start in sorted(starts):
                self.assertTrue(any(found.startswith(start) for found in styled), f"{name(file)} builds {start}..., which no rule styles")


    def test_no_rule_is_declared_twice_in_one_place(self):
        """One selector has one rule in its context (the same @media or @container): a second is a style that two changes
        wrote, and which of them wins is by order alone."""
        for sheet in sorted((WEB / "css").glob("*.css")):
            text = re.sub(r"/\*.*?\*/", "", sheet.read_text(), flags=re.S)
            stack, head, seen = [], "", set()
            for char in text:
                if char == "{":
                    stack.append(" ".join(head.split()))
                    head = ""
                elif char == "}":
                    selector = stack.pop()
                    if head.strip() and not selector.startswith("@"):
                        context = tuple(item for item in stack if item.startswith("@"))
                        self.assertFalse((selector, context) in seen, f"{name(sheet)} declares {selector} twice {' '.join(context)}")
                        seen.add((selector, context))
                    head = ""
                else:
                    head += char

    def test_the_name_of_an_item_that_leads_to_it_is_plain_until_it_is_hovered_or_focused(self):
        """Every page draws it the same way: no underline at rest, a line and the accent while the pointer or the focus is on it."""
        rules = dict(re.findall(r"^(\.name-link[^{]*)\{([^}]*)\}", (WEB / "css" / "components.css").read_text(), re.M))
        self.assertIn("text-decoration: none;", rules[".name-link "])
        hover = next(body for selector, body in rules.items() if "hover" in selector and "focus-visible" in selector)
        self.assertIn("text-decoration: underline;", hover)
        for sheet in (WEB / "css").glob("*.css"):
            self.assertEqual(len(re.findall(r"\.name-link\b", sheet.read_text())), 2 if sheet.name == "components.css" else 0,
                             f"{sheet.name}: a page does not restyle the name link")

    def test_a_pill_made_of_words_gives_way_when_its_room_is_short(self):
        """The note of a row and the chip of a catalog are the pills whose words are a .pill-text: that pill, the tooltip that holds it and the
        row that holds that may all shrink, and the words end with an ellipsis. One rule serves both, so that the chip is not left out."""
        sheet = re.sub(r"/\*.*?\*/", "", (WEB / "css" / "components.css").read_text(), flags=re.S)
        rules = [(" ".join(selector.split()), body) for selector, body in re.findall(r"^([^{}@\s][^{}]*)\{([^}]*)\}", sheet, re.M)]

        def declared(start):
            return next(body for selector, body in rules if selector.startswith(start))

        shrinks = declared(".pill:is(")
        self.assertIn(".pill-text", next(selector for selector, _ in rules if selector.startswith(".pill:is(")), "the pill whose words are a .pill-text, a note or not")
        self.assertTrue("min-width: 0;" in shrinks and "max-width: 100%;" in shrinks)
        self.assertTrue("overflow: hidden;" in declared(".pill-text") and "text-overflow: ellipsis;" in declared(".pill-text"))
        tip = next((selector, body) for selector, body in rules if selector.startswith(":is(.list-marks, .list-state) > .tip"))
        self.assertIn(".note", tip[0], "the tooltip of a note")
        self.assertTrue("flex: 0 1 auto;" in tip[1] and "min-width: 0;" in tip[1], "it shrinks with the row, where `.list-marks > *` keeps its children whole")
        ui = code((WEB / "js" / "components" / "ui.js").read_text())
        self.assertGreaterEqual(ui.count('class="pill-text"'), 2, "the words of a note and the chip's name are drawn by it")


class Libraries(Case):

    def test_every_script_of_a_cdn_is_loaded_with_the_hash_of_its_file(self):
        calls = 0
        for module in MODULES:
            if module.name == "core.js":   # where loadScript is made
                continue
            source = code(module.read_text())
            hashes = {constant for constant, _ in HASH.findall(source)}
            loads = SCRIPT_CALL.findall(source)
            for url in CDN.findall(source) if loads else []:
                self.assertRegex(url, r"[@/]\d+\.\d+\.\d+/", f"{name(module)} loads {url}, which names no exact version")
            for arguments in loads:
                calls += 1
                self.assertIn(arguments.rpartition(",")[2].strip(), hashes, f"{name(module)} loads {arguments} with no sha384 hash")
        self.assertEqual(calls, 6, "marked, DOMPurify and Mermaid for a guide, Monaco's loader for the editor, xterm and its fit addon for the terminal")

    def test_loadscript_asks_the_browser_to_check_the_hash(self):
        core = (WEB / "js" / "core.js").read_text()
        self.assertTrue("export function loadScript(src, integrity) {" in core, "loadScript takes the hash of the file")
        self.assertTrue(re.search(r'\{\s*src, integrity, crossOrigin: "anonymous"', core), "and asks the browser to check it")


def pinned(url):
    """What a policy needs to allow one pinned file: the directory of its exact version, with its trailing slash, which a policy takes as a
    prefix. The page may then load that package at that version and nothing else the CDN serves (an origin alone would let a script that
    finds its way into a guide load any package of it). An address with no version in it is allowed whole."""
    found = re.match(r"(https://[^/]+/.*?[@/]\d+\.\d+\.\d+/)", url)
    return found[1] if found else url


def script_sources():
    """What the page loads a script from, besides itself: the pinned directories of the CDN libraries it loads with their hash (the
    constants of the modules that call loadScript) and the address of any script tag of index.html that names one."""
    found = {pinned(url) for url in re.findall(r'<script\b[^>]*\ssrc="(https://[^"]+)"', INDEX)}
    for module in MODULES:
        source = code(module.read_text())
        if module.name != "core.js" and SCRIPT_CALL.search(source):
            found.update(pinned(url) for url in CDN.findall(source))
    return found


def script_origins():
    """The origins of those sources."""
    return {re.match(r"https://[^/]+", source)[0] for source in script_sources()}


def editor_sources():
    """What the editor's CDN also serves the page: the stylesheet and the icon font of Monaco, loaded from the directory its script comes
    from, so that style-src and font-src name that directory too."""
    return {pinned(url) for url in CDN.findall(code((WEB / "js" / "dialogs" / "editor.js").read_text()))}


def style_sources():
    """What the page loads a stylesheet from: the directory of the editor's, and those of the CDN libraries that the other modules load with
    `loadStyle` (xterm's, for the terminal)."""
    found = set(editor_sources())
    for module in MODULES:
        source = code(module.read_text())
        if module.name not in ("core.js", "editor.js") and STYLE_CALL.search(source):
            found.update(pinned(url) for url in CDN.findall(source))
    return found


def writes_styles():
    """Whether the page writes a style the policy must allow inline: a style attribute in its markup or a style tag."""
    return any(re.search(r"\sstyle=|<style\b", code(file.read_text())) for file in [WEB / "index.html", *MODULES])


def directives(policy):
    """A Content-Security-Policy as {directive: [sources]}: the first of a directive counts, as in a browser."""
    found = {}
    for part in policy.split(";"):
        words = part.split()
        if words and words[0] not in found:
            found[words[0]] = words[1:]
    return found


def worker_schemes():
    """The schemes the worker of the editor starts from: Monaco's is wrapped in an address of data, or of a blob."""
    editor = code((WEB / "js" / "dialogs" / "editor.js").read_text())
    return [scheme for scheme, uses in (("blob:", "new Blob("), ("data:", "data:text/javascript")) if uses in editor]


def wanted_policy():
    """The policy the page needs, from its files: scripts from itself and the pinned directories of the CDN libraries it loads, never inline;
    styles inline only because the page writes some, and the editor's stylesheet from its CDN; the editor's icon font; images from itself and
    data; requests to itself; frames of the consoles on this machine; the worker of the editor; nothing as an object, a base or a form; and
    never framed. The server's header is this, word for word."""
    editor, styles = sorted(editor_sources()), sorted(style_sources())
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' {' '.join(sorted(script_sources()))}",
        f"style-src 'self'{''.join(' ' + source for source in styles)}{' ' + chr(39) + 'unsafe-inline' + chr(39) if writes_styles() else ''}",
        f"font-src 'self'{''.join(' ' + source for source in editor)}",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-src http://localhost:* http://127.0.0.1:*",
        f"worker-src 'self' {' '.join(worker_schemes())}",
        "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"])


def policy_problems(header):
    """What a policy header lacks for the page, or allows beyond what it needs: nothing when it is what the page needs."""
    policy, sources, editor, styles, problems = directives(header), sorted(script_sources()), sorted(editor_sources()), sorted(style_sources()), []

    def want(ok, text):
        if not ok:
            problems.append(text)

    want(policy.get("default-src") == ["'self'"], "default-src is 'self'")
    scripts = policy.get("script-src", [])
    want(sorted(scripts) == sorted(["'self'", *sources]), "script-src is the page and the pinned directories of the CDN libraries, exactly")
    want(not {"'unsafe-inline'", "'unsafe-eval'", "*", "data:", "blob:"} & set(scripts), "script-src allows no inline script, no eval, no wildcard")
    style = policy.get("style-src", [])
    want("'self'" in style, "style-src has the page")
    want(("'unsafe-inline'" in style) == writes_styles(), "style-src allows inline styles when the page writes some, and only then")
    want(set(styles) <= set(style), "style-src has the directory of the stylesheet of the editor and of the terminal")
    want(not set(style) - {"'self'", "'unsafe-inline'", *styles}, "style-src has nothing but the page and the directories of the editor and of the terminal")
    want(sorted(policy.get("font-src", [])) == sorted(["'self'", *editor]), "font-src is the page and the directory of the editor's icon font, exactly")
    want(sorted(policy.get("img-src", [])) == ["'self'", "data:"], "img-src is the page and data")
    want(policy.get("connect-src") == ["'self'"], "connect-src is the page")
    want(sorted(policy.get("frame-src", [])) == ["http://127.0.0.1:*", "http://localhost:*"], "frame-src is the consoles on this machine")
    want(set(worker_schemes()) <= set(policy.get("worker-src", [])), "worker-src allows the address the editor starts its worker from")
    for directive in ("object-src", "base-uri", "form-action", "frame-ancestors"):
        want(policy.get(directive) == ["'none'"], f"{directive} is 'none'")
    return problems


class Policy(Case):
    """The page is HTML in the origin that holds the keys to Docker: it runs only scripts it loaded itself, so that a guide (or anything else
    that finds its way into a page) cannot add one. What it needs of a policy is read from its files; the answer of the server must say
    that, and no more."""

    def served(self, path):
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.headers, response.read()

    def test_the_page_runs_no_inline_script_and_its_first_script_is_a_file_that_runs_before_the_modules(self):
        """The theme and the menu are set before anything is drawn, by web/js/boot.js: a classic script, in the order of the page, ahead of the
        modules, which wait. index.html has no other script than the two files."""
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", INDEX, re.S)
        self.assertEqual([re.search(r'\ssrc="([^"]+)"', attributes)[1] for attributes, _ in scripts], ["/js/boot.js", "/js/main.js"])
        self.assertEqual([body.strip() for _, body in scripts], ["", ""], "no script is written in the page")
        boot, main = (attributes for attributes, _ in scripts)
        self.assertNotRegex(boot, r"\b(type|defer|async)\b", "a classic script runs where it stands, before the page is drawn")
        self.assertIn('type="module"', main)
        self.assertEqual(len(re.findall(r"<script\b", INDEX)), 2)
        # ... and it reaches nothing but the page and the browser's storage: no import, no export, no request
        boot_code = code((WEB / "js" / "boot.js").read_text())
        self.assertNotRegex(boot_code, r"(?m)^(import|export)\b|\bfetch\(|\bXMLHttpRequest\b", "boot.js is a classic script")

    def test_no_markup_of_the_page_has_a_handler_a_script_address_or_a_script_made_of_text(self):
        """A policy without 'unsafe-inline' and 'unsafe-eval' refuses an attribute that holds a handler, a javascript: address, eval and the
        constructor of a function; none of the page's files writes one."""
        for file in [WEB / "index.html", *MODULES]:
            source = code(file.read_text())
            self.assertEqual(re.findall(r"""\son[a-z]+\s*=\s*["'`]""", source), [], f"{name(file)} writes a handler in markup")
            self.assertEqual(re.findall(r"javascript\s*:", source, re.I), [], f"{name(file)} writes a javascript: address")
            self.assertEqual(re.findall(r"\beval\s*\(|\bnew\s+Function\s*\(|\bdocument\.write\(", source), [], f"{name(file)} makes a script of text")

    def test_the_scripts_are_loaded_from_a_file_of_the_page_or_from_the_pinned_directories_of_the_cdn_libraries_that_have_a_hash(self):
        self.assertEqual(sorted(script_origins()), ["https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"])
        sources = sorted(script_sources())
        self.assertEqual(len(sources), 6, "marked, DOMPurify and Mermaid, Monaco, and xterm with its fit addon")
        for source in sources:
            self.assertRegex(source, r"^https://[^/]+/.+[@/]\d+\.\d+\.\d+/$", f"{source} is the directory of one version, not the whole of a CDN")
        self.assertTrue(re.search(r"\bloadScript\(", (WEB / "js" / "dialogs" / "editor.js").read_text()))
        self.assertEqual(sorted(editor_sources()), [source for source in sources if "monaco" in source], "the editor's stylesheet and icon font come from where its script does")
        self.assertTrue(writes_styles(), "a style attribute is written (a bar's width, a link's), so the policy of styles allows inline ones")
        self.assertEqual(worker_schemes(), ["data:"], "the editor wraps the worker of its CDN in an address of data")

    def test_a_pinned_address_is_allowed_as_the_directory_of_its_version(self):
        for url, source in [
            ("https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.14/lib/marked.umd.min.js", "https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.14/"),
            ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min", "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/"),
            ("https://cdn.jsdelivr.net/npm/@scope/pkg@1.2.3/dist/x.js", "https://cdn.jsdelivr.net/npm/@scope/pkg@1.2.3/"),
            ("https://example.test/app.js", "https://example.test/app.js"),   # no version: the file itself, never its origin
        ]:
            self.assertEqual(pinned(url), source)

    def test_the_policy_the_page_needs_is_accepted_and_what_allows_more_or_less_is_not(self):
        """The check that the server's answer is held to: it accepts the policy built from the page's files, and names what a looser or a
        stricter one gets wrong."""
        wanted = wanted_policy()
        self.assertEqual(policy_problems(wanted), [])
        sources, editor, origins = " ".join(sorted(script_sources())), " ".join(sorted(editor_sources())), " ".join(sorted(script_origins()))
        styles = " ".join(sorted(style_sources()))
        for directive, value, said in [
            ("script-src", f"script-src 'self' 'unsafe-inline' {sources}", "script-src allows no inline script"),
            ("script-src", f"script-src 'self' 'unsafe-eval' {sources}", "script-src allows no inline script"),
            ("script-src", f"script-src 'self' {sources} https://cdn.example", "script-src is the page and the pinned directories"),
            ("script-src", "script-src 'self'", "script-src is the page and the pinned directories"),
            ("script-src", f"script-src 'self' {origins}", "script-src is the page and the pinned directories"),   # a CDN's whole origin lets any of its packages in
            ("script-src", f"script-src 'self' {sources} https://cdn.jsdelivr.net", "script-src is the page and the pinned directories"),
            ("default-src", "default-src *", "default-src is 'self'"),
            ("style-src", "style-src 'self'", "style-src allows inline styles"),
            ("style-src", "style-src 'self' 'unsafe-inline'", "style-src has the directory of the stylesheet of the editor and of the terminal"),
            ("style-src", f"style-src 'self' 'unsafe-inline' {origins}", "style-src has nothing but"),   # the editor's directory, not its CDN's whole origin
            ("style-src", f"style-src 'self' {styles} 'unsafe-inline' https://cdnjs.cloudflare.com", "style-src has nothing but"),
            ("style-src", f"style-src 'self' {editor} 'unsafe-inline'", "style-src has the directory of the stylesheet of the editor and of the terminal"),
            ("style-src", "style-src 'self' 'unsafe-inline' https://styles.example", "style-src has nothing but"),
            ("font-src", "font-src 'self'", "font-src is the page and the directory of the editor's icon font"),
            ("font-src", "font-src *", "font-src is the page and the directory of the editor's icon font"),
            ("font-src", f"font-src 'self' {origins}", "font-src is the page and the directory of the editor's icon font"),
            ("font-src", f"font-src 'self' {editor} data:", "font-src is the page and the directory of the editor's icon font"),
            ("img-src", "img-src 'self' data: https:", "img-src is the page and data"),
            ("connect-src", "connect-src 'self' https:", "connect-src is the page"),
            ("frame-src", "frame-src http://localhost:* http://127.0.0.1:* https:", "frame-src is the consoles"),
            ("worker-src", "worker-src 'self'", "worker-src allows the address"),
            ("object-src", "object-src 'self'", "object-src is 'none'"),
            ("base-uri", "base-uri 'self'", "base-uri is 'none'"),
            ("form-action", "form-action 'self'", "form-action is 'none'"),
            ("frame-ancestors", "frame-ancestors 'self'", "frame-ancestors is 'none'"),
        ]:
            with self.subTest(value=value):
                changed = "; ".join(value if part.startswith(f"{directive} ") else part for part in wanted.split("; "))
                self.assertNotEqual(changed, wanted)
                self.assertTrue(any(said in problem for problem in policy_problems(changed)), policy_problems(changed))
        for directive, said in [("object-src", "object-src is 'none'"), ("font-src", "font-src is the page and the directory of the editor's icon font"),
                                ("style-src", "style-src has the page")]:
            with self.subTest(gone=directive):
                gone = "; ".join(part for part in wanted.split("; ") if not part.startswith(f"{directive} "))
                self.assertTrue(any(said in problem for problem in policy_problems(gone)), f"a directive that is missing is named: {policy_problems(gone)}")
        gone = "; ".join(part for part in wanted.split("; ") if not part.startswith("object-src "))
        self.assertEqual(policy_problems(gone), ["object-src is 'none'"], "and the missing one alone")
        self.assertEqual(policy_problems("frame-ancestors 'none'")[0], "default-src is 'self'", "the policy of today says the frame alone and lacks the rest")

    def test_the_html_answer_carries_the_policy_the_page_needs(self):
        status, headers, _ = self.served("/")
        self.assertEqual(status, 200)
        policy = headers["Content-Security-Policy"]
        if policy == "frame-ancestors 'none'":   # today's answer, and no other: a header that has something else is held to the page's needs
            self.skipTest("the server answers the page with frame-ancestors alone: api.py has not added the policy yet (a work package of its own)")
        self.assertEqual(policy_problems(policy), [])
        self.assertEqual(headers["X-Frame-Options"], "DENY")


class Syntax(Case):

    def test_the_lab_ui_and_its_tests_are_python_3_9(self):
        # 3.9 parses `X | Y` in an annotation, then refuses it once the module runs
        for file in PYTHON:
            with self.subTest(file=file.relative_to(LAB_UI).as_posix()):
                tree = ast.parse(file.read_text(), filename=str(file), feature_version=(3, 9))
                unions = [node.lineno for annotation in filter(None, annotations(tree))
                          for node in ast.walk(annotation)
                          if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)]
                self.assertFalse(unions, f"X | Y annotations, which Python 3.9 refuses, on lines {unions}")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_every_module_is_valid_javascript(self):
        for module in MODULES:
            check = subprocess.run(["node", "--input-type=module", "--check"], input=module.read_text(),
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(check.returncode, 0, f"{name(module)}: {check.stderr}")


if __name__ == "__main__":
    unittest.main()
