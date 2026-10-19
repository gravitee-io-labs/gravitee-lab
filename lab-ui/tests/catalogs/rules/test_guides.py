"""GD, guides and READMEs. For each code one failing fixture in which only that code fires, and one that passes; the
message equals. A guide is Markdown that the Lab renders in its own origin: what is in a fence or in inline code is
shown and never read, what the renderer would take as HTML is read as HTML. A line of a megabyte finishes in under a second."""

import base64
import unittest

from lab_ui.catalogs.rules import context, guides
from tests.data.rules import support
from tests.data.rules.support import codes, run

DATA = support.HERE / "guides"
GUIDE = "docs/guide.md"
PAGES = {"docs/other.md": "# Other\n", "docs/img/logo.png": b"\x89PNG\r\n\x1a\n", "docs/my file.md": "x\n"}


def guide(text, **more):
    return {GUIDE: text, **PAGES, **more}


def found(text, **more):
    return run(guides, guide(text, **more))


def one(case, code, line, message, text, **more):
    out = found(text, **more)
    case.assertEqual([(code, GUIDE, line, message)], [(each.code, each.path, each.line, each.message) for each in out], out)
    return out[0]


class Links(unittest.TestCase):

    def test_the_clean_guide_has_every_form_and_passes(self):
        text = (DATA / "clean.md").read_text()
        extra = {"README.md": "# Hub\n", "addons/hello/compose.yml": "services: {}\n", "docs/img/logo.png": b"\x89PNG"}
        self.assertEqual([], found(text, **extra))

    def test_gd01_a_relative_link_that_does_not_resolve(self):
        message = "docs/guide.md:3: the link {} does not resolve inside the catalog. Fix: link to a file that is there."
        for target in ("missing.md", "./missing.md", "../missing.md", "sub/missing.md#top", "missing.md?x=1", "../../escape.md",
                       "../../../way/out.md", "/not-there.md", "other.md/more", "Other.md"):
            with self.subTest(target=target):
                got = found(f"first\n\n[x]({target})\n")
                self.assertEqual([("GD01", 3, message.format(target))], [(each.code, each.line, each.message) for each in got], target)

    def test_gd01_the_other_places_a_link_can_be(self):
        message = "docs/guide.md:{}: the link {} does not resolve inside the catalog. Fix: link to a file that is there."
        cases = [("[id]: missing.md\n", 1, "missing.md"), ('<a href="missing.html">x</a>\n', 1, "missing.html"),
                 ("![alt](img/missing.png)\n", 1, "img/missing.png"), ("<img src='img/none.png'>\n", 1, "img/none.png"),
                 ('<a href=missing.html>x</a>\n', 1, "missing.html"), ("[x](<missing here.md>)\n", 1, "missing here.md")]
        for text, line, target in cases:
            with self.subTest(text=text):
                one(self, "GD01", line, message.format(line, target), text)

    def test_gd01_what_resolves(self):
        text = "\n".join([
            "[a](other.md)", "[b](./other.md#section)", "[c](../README.md)", "[d](img/logo.png)", "[e](../addons/)", "[f](../addons)",
            "[g](#top)", "[h](other.md?view=1)", "[i](other.md#a?b)", "[j](my%20file.md)", "[k](/README.md)", "[l](https://example.test/x)",
            "[m](mailto:a@example.test)", "[n](action:start)", "[o](tel:123)", "[p](${DOCS}/x)", "[q]({{ base }}/x)", "[r](//cdn.example/x)",
            "[s](other.md \"title\")", "[t](<other.md>)", "[u](other.md 'title')", "<https://example.test/auto>", "[v]()", "[w](?q=1)",
            "[x](./)", "[y](.)", "[z](../docs/other.md)"])
        self.assertEqual([], found(text, **{"README.md": "# Hub\n", "addons/x/compose.yml": "x"}))

    def test_a_link_in_a_fence_or_in_inline_code_is_shown_and_never_read(self):
        text = "```\n[x](missing.md)\n<script>alert(1)</script>\n```\n\n~~~md\n[y](nowhere.md)\n~~~\n\n`[z](none.md)` and ``[w](none.md)``\n"
        self.assertEqual([], found(text))

    def test_gd02_a_link_that_runs_code(self):
        message = "docs/guide.md:3: a {}: link. Fix: use https or a relative path."
        for target, scheme in (("javascript:alert(1)", "javascript"), ("JaVaScRiPt:alert(1)", "javascript"), ("vbscript:msgbox(1)", "vbscript"),
                               ("data:text/plain,hello", "data"), ("jav&#97;script:alert(1)", "javascript"),
                               ("java&Tab;script:alert(1)", "javascript"), ("&#x6A;avascript:alert(1)", "javascript")):
            with self.subTest(target=target):
                one(self, "GD02", 3, message.format(scheme), f"x\n\n[x]({target})\n")
        one(self, "GD02", 3, message.format("javascript"), 'x\n\n<a href="javascript:void(0)">x</a>\n')
        one(self, "GD02", 3, message.format("javascript"), "x\n\n<javascript:alert(1)>\n")
        one(self, "GD02", 3, message.format("vbscript"), "x\n\n[x]: vbscript:msgbox\n")

    def test_gd02_a_scheme_hidden_by_a_character_a_browser_drops_is_still_the_scheme(self):
        self.assertEqual("javascript", guides.scheme_of("java\tscript:alert(1)"))
        self.assertEqual("javascript", guides.scheme_of(" \x01javascript:x"))
        self.assertEqual("", guides.scheme_of("javascript"))
        self.assertEqual("", guides.scheme_of("a/b:c"))
        self.assertEqual("https", guides.scheme_of("HTTPS://x"))

    def test_gd02_the_data_of_an_image_is_gd04s(self):
        self.assertEqual({"GD04"}, codes(found("![x](data:image/png;base64,AAAA)\n")))

    def test_what_is_relative(self):
        for target in ("a.md", "./a.md", "../a.md", "a/b#c", "?q", "#f", "/a"):
            self.assertTrue(guides.is_relative(target), target)
        for target in ("https://x", "//host/x", "\\\\host\\x", "javascript:x", "", "mailto:a@b.c", "/\\host"):
            self.assertFalse(guides.is_relative(target), target)

    def test_resolves(self):
        files, folders = {"docs/a.md", "README.md", "my file.md"}, {"docs"}
        self.assertTrue(guides.resolves("docs", "a.md", files, folders))
        self.assertTrue(guides.resolves("docs", "../README.md#x", files, folders))
        self.assertTrue(guides.resolves("docs", "../my%20file.md", files, folders))
        self.assertTrue(guides.resolves("", "docs/", files, folders))
        self.assertFalse(guides.resolves("", "docs/a.md/", files, folders), "a file is not a folder")
        self.assertFalse(guides.resolves("docs", "../../README.md", files, folders))
        self.assertFalse(guides.resolves("", "../README.md", files, folders))
        self.assertIsNone(guides.resolves("docs", "#top", files, folders))
        self.assertIsNone(guides.resolves("docs", "?q=1", files, folders))


class Html(unittest.TestCase):

    def test_gd03_raw_html_that_runs(self):
        message = "docs/guide.md:3 holds {}. A guide is text and diagrams; the Lab runs nothing a guide brings. Fix: show it as code (a fenced block) or remove it."
        cases = {"a <script> tag": "<script>alert(1)</script>", "a <script> tag ": '<SCRIPT src="https://x.example/a.js"></SCRIPT>',
                 "an <iframe> tag": '<iframe src="https://x.example/"></iframe>', "an <object> tag": '<object data="https://x.example/a.swf"></object>',
                 "an <embed> tag": '<embed src="https://x.example/a.swf">', "a <form> tag": '<form action="https://x.example/"><input></form>',
                 "an onclick= attribute": '<div onclick="go()">x</div>', "an onerror= attribute": '<img src="https://x.example/a.png" onerror=alert(1)>',
                 "a srcdoc= attribute": '<div srcdoc="<b>x</b>">y</div>', "a data:text/html URL": "see data:text/html,<b>x</b> for it"}
        for what, text in cases.items():
            with self.subTest(what=what):
                one(self, "GD03", 3, message.format(what.strip()), f"x\n\n{text}\n")

    def test_gd03_in_a_tag_that_spans_lines(self):
        out = found('x\n\n<img\n  src="https://x.example/a.png"\n  onload="go()">\n')
        self.assertEqual([("GD03", 5)], [(each.code, each.line) for each in out])

    def test_gd03_a_greater_than_in_a_quoted_attribute_hides_nothing(self):
        out = found('x\n\n<a title="a > b" onmouseover="go()" href="https://x.example/">x</a>\n')
        self.assertEqual(["GD03"], [each.code for each in out])
        out = found('x\n\n<img alt="<" onerror="go()" src="https://x.example/a.png">\n')
        self.assertEqual(["GD03"], [each.code for each in out])

    def test_gd03_a_fence_inside_an_html_block_hides_nothing(self):
        text = "<div>\n```\n<script>alert(1)</script>\n```\n</div>\n"
        self.assertEqual(["GD03"], [each.code for each in found(text)])
        text = "<div>\n\n```\n<script>alert(1)</script>\n```\n"
        self.assertEqual([], found(text), "a blank line ends the block: now it is a fence")

    def test_gd03_code_inside_an_html_block_is_html(self):
        self.assertEqual(["GD03"], [each.code for each in found("<p>`<script>alert(1)</script>`</p>\n")])
        self.assertEqual([], found("text `<script>alert(1)</script>` text\n"))

    def test_gd03_passes_when_it_is_shown_as_code(self):
        for text in ("```html\n<form action=x><input onclick=y></form>\n```\n", "~~~\n<script>x</script>\n~~~\n", "`<form>`\n",
                     "Use ``<iframe src=x>`` for it\n", "    indented\n\n```\n<object data=x>\n```\n", "```\n<script>\nnever closed\n"):
            with self.subTest(text=text):
                self.assertEqual([], found(text))

    def test_gd03_a_fence_is_closed_by_the_same_character_and_enough_of_it(self):
        self.assertEqual([], found("````\n```\n<script>x</script>\n```\n````\n"))
        self.assertEqual(["GD03"], [each.code for each in found("```\ncode\n```\n<script>x</script>\n")])
        self.assertEqual(["GD03"], [each.code for each in found("~~~\ncode\n~~~\n<script>x</script>\n")])
        self.assertEqual([], found("```\n~~~\n<script>x</script>\n"), "a tilde does not close a backtick fence")
        self.assertEqual(["GD03"], [each.code for each in found("``` `x`\n<script>x</script>\n")], "a backtick in the info makes it no fence")

    def test_gd03_the_blanks_around_an_equal_sign_are_not_counted(self):
        """HTML allows any number of them; a handler or a link spelled with eleven is the same handler or link."""
        for gap in (" " * 11, " " * 300, "\t\t", " \n ", "\n"):
            with self.subTest(gap=gap):
                self.assertEqual(["GD03"], [each.code for each in found(f"x\n\n<img src=https://x.example/a.png onerror{gap}={gap}alert(1)>\n")])
                self.assertEqual(["GD03"], [each.code for each in found(f"x\n\n<div srcdoc{gap}={gap}'<b>x</b>'>y</div>\n")])
                self.assertEqual(["GD02"], [each.code for each in found(f"x\n\n<a href{gap}={gap}'javascript:alert(1)'>y</a>\n")])
                self.assertEqual(["GD04"], [each.code for each in found(f"x\n\n<img src{gap}={gap}\"http://x.example/a.png\">\n")])

    def test_gd03_a_handler_is_one_however_far_from_the_start_of_its_tag(self):
        for far in (3000, 4001, 70000, 600000):
            with self.subTest(far=far):
                self.assertEqual(["GD03"], [each.code for each in found('x\n\n<img alt="' + "a b " * (far // 4) + '" onerror=alert(1)>\n')])
                self.assertEqual(["GD03"], [each.code for each in found("x\n\n<img " + "x=1 " * (far // 4) + " onerror=alert(1)>\n")])
        self.assertEqual([], found("<img alt=\"x\n\n" + "a " * 10 + 'onerror=alert(1)'), "a blank line ends the paragraph, and the tag with it")

    def test_gd03_a_raw_html_block_of_the_first_kinds_runs_to_its_closing_line(self):
        """CommonMark ends `<pre>`, `<script>`, `<style>`, `<textarea>` and a comment at their closing line, not at a blank line:
        a fence inside one is raw HTML, and what is in it is read as HTML."""
        inside = "<img src=https://x.example/a.png onerror=alert(1)>"
        for opener, closer in (("<pre>", "</pre>"), ("<PRE class=a>", "</pre>"), ("<script>", "</script>"), ("<style>", "</STYLE>"),
                               ("<textarea>", "</textarea>"), ("<!--", "-->")):
            with self.subTest(opener=opener):
                out = found(f"{opener}\n\n```\n{inside}\n```\n\n{closer}\n\nafter\n")
                self.assertIn(("GD03", 4), [(each.code, each.line) for each in out])
                self.assertEqual({"GD03"}, codes(out))
                out = found(f"{opener}\n\n~~~\n{inside}\n~~~\n")
                self.assertIn(("GD03", 4), [(each.code, each.line) for each in out], "never closed: it runs to the end")
        self.assertEqual(["GD03"], [each.code for each in found("<?php\n\n```\n<script>x</script>\n```\n?>\n")])
        self.assertEqual(["GD03"], [each.code for each in found("<![CDATA[\n\n```\n<script>x</script>\n```\n]]>\n")])

    def test_gd03_after_the_closing_line_a_fence_is_a_fence_again(self):
        inside = "<script>alert(1)</script>"
        for block in ("<pre>x</pre>", "<pre>\nx\n</pre>", "<!-- a note -->", "<style>\na{}\n</style>", "<textarea>t</textarea>", "<?php x ?>"):
            with self.subTest(block=block):
                self.assertEqual([], found(f"{block}\n```\n{inside}\n```\n"))
                self.assertEqual([], found(f"{block}\n\n```\n{inside}\n```\n"))
        self.assertEqual(["GD03"], [each.code for each in found(f"<pre>x</pre> {inside}\n")])
        self.assertEqual(["GD03"], [each.code for each in found(f"<pre>\n```\n{inside}\n```\n</pre>\n")], "a fence inside <pre> is raw HTML")

    def test_gd03_prose_that_looks_like_an_attribute_is_prose(self):
        self.assertEqual([], found("Turn on=1 and the online=true setting; onward=up is fine. A comparison a < b is too.\n"))

    def test_gd04_an_image_that_is_neither_relative_nor_https(self):
        message = "docs/guide.md:3: the image {} is not relative or https."
        for src in ("http://example.test/a.png", "//cdn.example.test/a.png", "ftp://example.test/a.png", "data:image/png;base64,AAAA",
                    "file:///etc/passwd", "HTTP://EXAMPLE.TEST/a.png"):
            with self.subTest(src=src):
                one(self, "GD04", 3, message.format(src), f"x\n\n![alt]({src})\n")
        one(self, "GD04", 3, message.format("http://example.test/a.png"), 'x\n\n<img src="http://example.test/a.png">\n')
        self.assertEqual([], found("![a](https://example.test/a.png) ![b](img/logo.png) ![c](../docs/img/logo.png)\n"))

    def test_gd04_a_long_source_is_cut_in_the_message(self):
        out = found("x\n\n![a](http://example.test/" + "a" * 300 + ".png)\n")
        self.assertEqual(["GD04"], [each.code for each in out])
        self.assertLess(len(out[0].message), 200)
        self.assertIn("…", out[0].message)


class Svg(unittest.TestCase):

    def test_gd05_an_svg_with_active_content(self):
        message = "{} is an SVG with active content ({}). Fix: export it as a plain image, or remove the element."
        cases = {
            "<script>": "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "<foreignObject>": "<svg><foreignObject><div xmlns='http://www.w3.org/1999/xhtml'>x</div></foreignObject></svg>",
            "<iframe>": "<svg><iframe src='https://x.example/'/></svg>", "<embed>": "<svg><embed src='x'/></svg>",
            "<object>": "<svg><object data='x'/></svg>",
            "<use> with an external href": "<svg><use href='https://x.example/a.svg#id'/></svg>",
            "<use> with an external href ": "<svg xmlns:xlink='http://www.w3.org/1999/xlink'><use xlink:href='other.svg#id'/></svg>",
            "an event attribute": "<svg><rect onclick='go()' width='1' height='1'/></svg>",
            "javascript: URL": "<svg><a href='javascript:alert(1)'><rect/></a></svg>",
        }
        for what, content in cases.items():
            with self.subTest(what=what):
                path = "docs/img/logo.svg"
                out = run(guides, {path: content})
                self.assertEqual([("GD05", path, 0, message.format(path, what.strip()))],
                                 [(each.code, each.path, each.line, each.message) for each in out])

    def test_gd05_an_svg_is_read_up_to_what_st09_lets_it_be(self):
        pad = "<!-- padding -->\n" * (80 * 1024)   # 1.4 MB
        self.assertGreater(len(pad), 1024 * 1024)
        for tail in ("<script>x</script>", "<a href='javascript:x'/>"):
            with self.subTest(tail=tail):
                self.assertEqual(["GD05"], [each.code for each in run(guides, {"docs/big.svg": f"<svg>{pad}{tail}</svg>"})])
        self.assertEqual([], run(guides, {"docs/huge.svg": f"<svg>{pad * 2}<script>x</script></svg>"}), "past 2 MB it is ST09's")

    def test_gd05_the_fixtures(self):
        self.assertEqual([], run(guides, {"docs/img/plain.svg": (DATA / "plain.svg").read_text()}))
        self.assertEqual(["GD05"], [each.code for each in run(guides, {"docs/img/active.svg": (DATA / "active.svg").read_text()})])

    def test_gd05_a_script_spelled_in_odd_ways_is_one(self):
        for content in ("<svg><SCRIPT>x</SCRIPT></svg>", "<svg><Script\n>x</script></svg>", "<svg><a href=' java\tscript:x'><a/></svg>",
                        "<svg><rect ONLOAD = 'x'/></svg>", "<svg><a href='jav&#97;script:x'/></svg>"):
            with self.subTest(content=content):
                self.assertEqual(["GD05"], [each.code for each in run(guides, {"docs/a.svg": content})])

    def test_gd05_a_data_source_that_is_an_svg_is_read_too(self):
        active = base64.b64encode(b"<svg><script>alert(1)</script></svg>").decode()
        plain = base64.b64encode(b"<svg><rect/></svg>").decode()
        self.assertEqual({"GD04", "GD05"}, codes(found(f"![x](data:image/svg+xml;base64,{active})\n")))
        self.assertEqual({"GD04"}, codes(found(f"![x](data:image/svg+xml;base64,{plain})\n")))
        self.assertEqual({"GD04", "GD05"}, codes(found("![x](data:image/svg+xml,%3Csvg%3E%3Cscript%3Ex%3C/script%3E%3C/svg%3E)\n")))
        self.assertEqual({"GD04"}, codes(found("![x](data:image/svg+xml;base64,@@@@)\n")))


class Helpers(unittest.TestCase):

    def test_mask_hides_code_and_keeps_the_lines(self):
        text = "a\n```\ncode\n```\nb `c` d ``e`f`` g `h\ni`\n"
        masked = guides.mask(text)
        self.assertEqual(len(text.split("\n")), len(masked.split("\n")))
        self.assertEqual(len(text), len(masked))
        self.assertEqual("a", masked.split("\n")[0])
        self.assertNotIn("code", masked)
        self.assertNotIn("`c`", masked)
        self.assertIn("`h", masked, "a span that never closes on its line is just a backtick")

    def test_the_rules_of_the_table_are_section_6(self):
        self.assertEqual({"GD01", "GD02", "GD03", "GD04", "GD05"}, set(guides.RULES))
        self.assertEqual("{path}:{line}: the image {src} is not relative or https.", guides.RULES["GD04"].message)

    def test_a_guide_with_a_nul_byte_is_read(self):
        """GitHub shows no diff for a file with a NUL in it; the Lab's renderer reads it all the same."""
        script = b"<script>alert(1)</script>\n[x](javascript:alert(1))\n"
        for label, data in {"first": b"\0\0" + script, "after a line": b"\0\n" + script, "past 8 KB": b"x\n" * 4500 + b"\0\n" + script,
                            "inside the tag": b"<scr\0ipt>alert(1)</script>\n[x](javascript:alert(1))\n",
                            "UTF-16": script.decode().encode("utf-16-le")}.items():
            with self.subTest(label):
                self.assertEqual(["GD02", "GD03"], sorted(codes(run(guides, {"docs/b.md": data}))))
        self.assertEqual(["GD05"], [each.code for each in run(guides, {"docs/a.svg": b"\0<svg><script>x</script></svg>"})])
        self.assertEqual([], run(guides, {"docs/b.md": b"\0\0plain words\n\0"}))

    def test_every_finding_is_said_once_and_a_flood_is_cut(self):
        out = found("x\n\n" + "[a](missing.md) " * 400 + "\n")
        self.assertEqual(1, len(out), "one line, one finding of a kind")
        out = found("x\n\n" + "".join(f"[a](missing{number}.md)\n" for number in range(400)))
        self.assertEqual(guides.PER_FILE, len(out))


class Hostile(unittest.TestCase):
    """A guide of a megabyte on one line, made of the parts the scanner looks for."""

    PIECES = ["](", "](<", "](x ", "[a](", "![a](", "](" + "a" * 100, "<a ", "<a href=", "<img src=", "<", ">", "<script", "<form ", "on", "onclick=",
              " onclick=", "`", "``", "` ", "```", "~~~", "\n```\n", "\n~~~\n", "[x]:", "\n[x]: ", "<https://", "<a:", "data:", "data:text/html",
              "javascript:", "&#", "&amp;", "href=\"", "src='", "srcdoc=", "(", ")", "\\", "\\(", "<!--", "-->", "<div>\n", "a", " ", "\t",
              "![a](" + "(" * 50, "[" * 100, "](" * 3 + "x)", "<pre>\n\n", "</pre>\n", "<script>\n\n", "\n\n", "<!--\n", "<?", "<![CDATA[",
              "<a onerror" + " " * 40 + "=", "onerror" + " " * 5000, "src" + " " * 3000 + "=" + " " * 3000, "<img ", "<p " + "x " * 500]

    def test_every_form_the_scanner_looks_for_finishes_in_under_a_second(self):
        for piece in self.PIECES:
            text = piece * (1024 * 1024 // len(piece))
            listing = support.tree({GUIDE: text, **PAGES})
            ctx = context(listing)
            best = min(support.timed(guides.check, listing, ctx)[1] for _ in range(2))
            self.assertLess(best, 1.0, f"{piece[:30]!r}: {best:.2f} s")

    def test_an_svg_of_a_megabyte_finishes_too(self):
        for piece in ("<use ", "<a ", "on", " onx=", "javascript:", "<", "<svg ", "href=", " "):
            text = piece * (1024 * 1024 // len(piece))
            best = min(support.timed(guides.svg_active, text)[1] for _ in range(2))
            self.assertLess(best, 1.0, f"{piece!r}: {best:.2f} s")

    def test_many_handlers_a_long_way_from_their_tags_finish_too(self):
        for text in ("<a " + "onclick=1 " * 100000, "<a>\n\n" + "x onclick=1\n" * 60000, ("<b x=\"" + "y" * 200 + "\" ") * 5000 + "onload=1 " * 50000):
            best = min(support.timed(guides.check, support.tree({GUIDE: text, **PAGES}), context(support.tree()))[1] for _ in range(2))
            self.assertLess(best, 1.0, f"{text[:20]!r}: {best:.2f} s")

    def test_a_path_of_thousands_of_folders_costs_in_proportion_to_its_length(self):
        listing = support.ListTree.from_files({"/".join(["d"] * 4000) + f"/f{number}.md": "[x](missing.md)\n" for number in range(50)})
        _, seconds = support.timed(guides.check, listing, context(support.tree()))
        self.assertLess(seconds, 1.0)

    def test_a_text_that_holds_many_short_lines_is_read_in_proportion(self):
        text = "".join(f"[a](missing{number}.md) and `code` and <b>bold</b>\n" for number in range(15000))
        _, seconds = support.timed(guides.check, support.tree({GUIDE: text}), context(support.tree()))
        self.assertLess(seconds, 5.0)


class Content(unittest.TestCase):
    """The guides, READMEs and SVG files that this checkout holds."""

    def test_no_guide_of_the_engine_runs_anything(self):
        import subprocess
        try:
            done = subprocess.run(["git", "ls-files", "-z"], cwd=support.ROOT, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is None or done.returncode != 0:
            self.skipTest("this checkout is not a git work tree")
        held, entries = {}, []
        for name in (each.decode() for each in done.stdout.split(b"\0") if each):
            path = support.ROOT / name
            if "/tests/data/" in name:   # the fixtures of the rules hold the files they refuse
                continue
            if path.is_file() and not path.is_symlink():
                entries.append(("100644", name, path.stat().st_size))
                if path.suffix.lower() in (".md", ".svg") and path.stat().st_size <= 1024 * 1024:
                    held[name] = path.read_bytes()
        self.assertGreater(len(held), 20)
        listing = support.ListTree(entries, lambda name, limit: held.get(name))
        out = guides.check(listing, context(listing))
        self.assertEqual([], [(each.code, each.path, each.line) for each in out if each.code != "GD01"])
        known = [(each.path, each.line) for each in out if each.code == "GD01"]
        self.assertLessEqual(len(known), 5, f"links of the engine's own docs that lead nowhere: {known}")


if __name__ == "__main__":
    unittest.main()
