// The rules of the split page of a running playground (web/js/workspace/split.js): the widths of its panels, when they are rows
// and not columns, which tab shows and when its UI is looked at, where a link or a message of an app loads, and the actions of the
// guide (their id, why one cannot be sent, the two messages that carry them), and the terminal under the UIs (its height, its divider,
// when the guide may run a block at its prompt). No DOM.
import assert from "node:assert/strict";
import { test } from "node:test";
import { actionMessage, actionOf, actionReason, bytesOf, chooseTab, commandOf, edge, fits, guideWidth, isLook, isReady, limits, linkTarget, located, looksAt,
  newPage, nudge, opening, originOf, plainClick, termEdge, termHeight, termLimits, termNudge, terminalReason } from "../../../web/js/workspace/split.js";

const BANK = { id: "bakery-web", label: "Acme Bakery", url: "http://localhost:8110" };
const GAMMA = { id: "gamma-ui", label: "Gamma Console", url: "http://localhost:8086" };
const TABS = [BANK, GAMMA];
const PLAIN = { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };

test("the guide is never narrower than 600 px, the UIs never than 480", () => {
  assert.deepEqual(limits(1388), { min: 600, max: 900 });
  assert.deepEqual(limits(1238), { min: 600, max: 750 });
  assert.deepEqual(limits(900), { min: 600, max: 600 });   // narrower than both: the minimum still stands
});

test("both panels fit side by side from the width of their minimums, the divider, the card's border and the margins of the split", () => {
  // `fits` takes the page's container, which is the window less the menu
  assert.equal(fits(1113), false);
  assert.equal(fits(1114), true);
  assert.equal(fits(1100 - 64), false);   // a 1100 px window has the collapsed menu
  assert.equal(fits(1200 - 64), true);    // a 1200 px window with the menu collapsed
  assert.equal(fits(1200 - 228), false);  // and not with the menu open: the page is what is left of the window
  assert.equal(fits(1366 - 228), true);   // a laptop of 1366 px, the menu open
  assert.equal(fits(0), false);
});

test("the guide starts at its minimum, 600 px, whatever the room, and stays within the limits of its container", () => {
  for (const none of [null, "", undefined, "wide", NaN]) assert.equal(guideWidth(none, 1388), 600, String(none));
  for (const room of [1088, 1100, 1250, 1388, 1800, 3000]) assert.equal(guideWidth(null, room), 600, `${room} px of room`);
  assert.equal(guideWidth("820", 1388), 820);   // what the browser kept is text
  assert.equal(guideWidth(820.4, 1388), 820);
  assert.equal(guideWidth(300, 1388), 600);
  assert.equal(guideWidth(5000, 1388), 900);   // the divider still goes up to what leaves the UIs their 480 px
  assert.equal(guideWidth(5000, 1664 - 228 - 24), 924);   // a 1664 px window with the menu open: the UIs keep 480 px
  assert.equal(guideWidth(900, 1200), 712);
  // A container that shrank under the width the person gave takes it back to what leaves the UIs their minimum, and
  // the width given comes back when it grows
  assert.equal(guideWidth(1114, 1500), 1012);
  assert.equal(guideWidth(1114, 1250), 762);
  assert.equal(guideWidth(1114, 1800), 1114);
});

test("an arrow moves the divider by 16 px, with Shift by 64, and it stops at both limits", () => {
  assert.equal(nudge("ArrowRight", false, 800, 1388), 816);
  assert.equal(nudge("ArrowLeft", false, 800, 1388), 784);
  assert.equal(nudge("ArrowRight", true, 800, 1388), 864);
  assert.equal(nudge("ArrowLeft", true, 800, 1388), 736);
  assert.equal(nudge("ArrowLeft", true, 620, 1388), 600);
  assert.equal(nudge("ArrowRight", true, 880, 1388), 900);
  assert.equal(nudge("ArrowLeft", false, 600, 1388), 600);
});

test("Home and End take the divider to its limits; any other key is not its own", () => {
  assert.equal(nudge("Home", false, 900, 1388), 600);
  assert.equal(nudge("End", false, 800, 1388), 900);
  for (const key of ["Tab", "Enter", " ", "ArrowUp", "ArrowDown", "a"]) assert.equal(nudge(key, false, 800, 1388), null, key);
});

test("the divider knows which end of its travel it is at", () => {
  assert.equal(edge(600, 1388), "min");
  assert.equal(edge(900, 1388), "max");
  assert.equal(edge(750, 1388), "");
  assert.equal(edge(600, 1000), "min");   // no room to move: the minimum is also the maximum, and the minimum says so
});

const tab = (id) => ({ id });

test("the tab shown is the one kept for the playground, else the first console, whether it runs or not", () => {
  const all = [tab("bakery-web"), tab("gamma-ui"), tab("details")];
  assert.equal(chooseTab(all, "gamma-ui"), "gamma-ui");
  assert.equal(chooseTab(all, "details"), "details");
  assert.equal(chooseTab(all, "gone"), "bakery-web");
  assert.equal(chooseTab(all, null), "bakery-web");
  assert.equal(chooseTab(all, undefined), "bakery-web");
  // A playground that lists no console has the details alone
  assert.equal(chooseTab([tab("details")], "gone"), "details");
});

test("a UI is looked at once its tab shows, and again while it shows a notice", () => {
  assert.equal(looksAt({ opened: false, status: "" }), true);
  assert.equal(looksAt({ opened: true, status: "refused" }), true);
  assert.equal(looksAt({ opened: true, status: "down" }), true);
  assert.equal(looksAt({ opened: true, status: "framed" }), false);   // its frame is kept, not made again
});

test("a plain click on a link under the address of a UI loads it in that tab", () => {
  const hit = linkTarget(TABS, "http://localhost:8086/environments/default/aim/agents", PLAIN);
  assert.equal(hit.tab, GAMMA);
  assert.equal(hit.url, "http://localhost:8086/environments/default/aim/agents");
  assert.equal(linkTarget(TABS, "http://localhost:8110", PLAIN).tab, BANK);
  assert.equal(linkTarget(TABS, "http://localhost:8110", PLAIN).url, "http://localhost:8110/");
});

test("a link that is not under one of the UIs is left to the browser", () => {
  for (const href of ["http://localhost:8084", "http://127.0.0.1:8086/", "https://localhost:8086/", "http://localhost:80860/",
    "http://localhost:8086.evil.test/", "https://www.gravitee.io", "mailto:a@b.test", "javascript:void(0)", "#/playgrounds/acme-bakery/2", "", "nope"]) {
    assert.equal(linkTarget(TABS, href, PLAIN), null, href);
  }
});

test("a click with a key held, or of the middle button, is the browser's own", () => {
  const href = "http://localhost:8086/";
  assert.equal(plainClick(PLAIN), true);
  for (const key of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) {
    assert.equal(linkTarget(TABS, href, { ...PLAIN, [key]: true }), null, key);
    assert.equal(plainClick({ ...PLAIN, [key]: true }), false, key);
  }
  for (const button of [1, 2]) {
    assert.equal(linkTarget(TABS, href, { ...PLAIN, button }), null, `button ${button}`);
    assert.equal(plainClick({ ...PLAIN, button }), false, `button ${button}`);
  }
});

test("an app of a tab may ask for an address under the UI of any tab", () => {
  const ask = (origin, data) => opening(TABS, origin, data);
  const open = { type: "gravitee-lab:open", url: "http://localhost:8086/environments/default/aim/agents" };
  assert.equal(ask("http://localhost:8110", open).tab, GAMMA);
  assert.equal(ask("http://localhost:8110", open).url, open.url);
  assert.equal(ask("http://localhost:8086", open).tab, GAMMA);   // Gamma to itself
  assert.equal(ask("http://localhost:8086", { ...open, url: "http://localhost:8110/#lessons" }).tab, BANK);
});

test("a message from another origin, or for another address, or of another kind, is ignored", () => {
  const open = { type: "gravitee-lab:open", url: "http://localhost:8086/" };
  const ask = (origin, data) => opening(TABS, origin, data);
  for (const origin of ["http://localhost:9000", "http://localhost:8084", "http://127.0.0.1:8110", "https://localhost:8110", "null", "", undefined]) {
    assert.equal(ask(origin, open), null, String(origin));
  }
  for (const url of ["http://localhost:8084/", "https://www.gravitee.io", "javascript:alert(1)", "", "nope", null, 42, undefined]) {
    assert.equal(ask("http://localhost:8110", { ...open, url }), null, String(url));
  }
  for (const data of [null, undefined, "gravitee-lab:open", 42, {}, { url: open.url }, { ...open, type: "gravitee-lab:close" }, { ...open, type: "Gravitee-Lab:Open" }]) {
    assert.equal(ask("http://localhost:8110", data), null, JSON.stringify(data));
  }
});

test("only a web address is ever loaded in a tab: blob, data, file and websocket addresses hold another page's origin or none", () => {
  // `blob:http://localhost:8086/x` has the origin of the page that made it, so it would pass for a page of Gamma
  const others = ["blob:http://localhost:8086/3f9c", "data:text/html,<p>x</p>", "file:///etc/passwd", "ws://localhost:8086/socket",
    "filesystem:http://localhost:8086/temporary/x", "ftp://localhost:8086/"];
  for (const url of others) {
    assert.equal(opening(TABS, "http://localhost:8110", { type: "gravitee-lab:open", url }), null, url);
    assert.equal(linkTarget(TABS, url, PLAIN), null, url);
  }
  // Nor from them
  for (const origin of ["blob:http://localhost:8110", "ws://localhost:8110", "file://"]) {
    assert.equal(opening(TABS, origin, { type: "gravitee-lab:open", url: "http://localhost:8086/" }), null, origin);
  }
});

// --- The actions of a guide ----------------------------------------------------------------------------------------

test("an action link is `action:` and an id of lowercase letters, digits and hyphens, which starts with a letter or a digit", () => {
  for (const id of ["ask-ace-accounts", "leak-an-iban", "move-600-through-ace", "a", "0", "1-see-everything", "a--b", "a-"]) {
    assert.equal(actionOf(`action:${id}`), id, id);
  }
  for (const href of ["action:", "action:-a", "action:Ask-Ace", "action:ask ace", "action:ask_ace", "action:ask.ace", "action:ask/ace", "action:ask:ace",
    "action:é", "action:ask\n", "action:\nask", "Action:ask", "ACTION:ask", "action", "actions:ask", " action:ask", "xaction:ask",
    "http://localhost:8110/action:ask", "javascript:alert(1)", "#action:ask", "", null, undefined, 42, {}]) {
    assert.equal(actionOf(href), null, String(href));
  }
});

test("an id is 64 characters at most", () => {
  assert.equal(actionOf(`action:${"a".repeat(64)}`), "a".repeat(64));
  assert.equal(actionOf(`action:${"a".repeat(65)}`), null);
});

test("an address makes a new page unless it has a fragment, which may only move within the page it is on", () => {
  for (const url of ["http://localhost:8110", "http://localhost:8110/", "http://localhost:8110/accounts?x=1"]) assert.equal(newPage(url), true, url);
  for (const url of ["http://localhost:8110/#/teams", "http://localhost:8110/accounts#top", "http://localhost:8110/#"]) assert.equal(newPage(url), false, url);
});

test("what the page posts for an action is its type and its id and nothing else", () => {
  assert.deepEqual(actionMessage("ask-ace-accounts"), { type: "gravitee-lab:action", id: "ask-ace-accounts" });
  assert.deepEqual(Object.keys(actionMessage("a")), ["type", "id"]);
});

test("a UI says which page it is on with {type: gravitee-lab:location, url}, from the origin of its tab and for an address under it", () => {
  const where = (url) => ({ type: "gravitee-lab:location", url });
  const deep = "http://localhost:8086/environments/default/aim/agents?x=1#y";
  assert.equal(located(GAMMA, "http://localhost:8086", where(deep)), deep);
  assert.equal(located(GAMMA, "http://localhost:8086", where("http://localhost:8086")), "http://localhost:8086/");
  assert.equal(located(GAMMA, "http://localhost:8086", { ...where(deep), extra: 1 }), deep, "only its type and its address count");
  // Not from another origin than the tab's, and not for an address of another one: a page cannot send a tab somewhere else
  for (const origin of ["http://localhost:8110", "http://127.0.0.1:8086", "https://localhost:8086", "null", "", undefined, null]) {
    assert.equal(located(GAMMA, origin, where(deep)), null, String(origin));
  }
  for (const url of ["http://localhost:8110/", "https://example.com/", "javascript:alert(1)", "/relative", "", 42, null, undefined]) {
    assert.equal(located(GAMMA, "http://localhost:8086", where(url)), null, String(url));
  }
  for (const data of [null, undefined, "x", {}, { type: "gravitee-lab:ready", url: deep }, { url: deep }]) {
    assert.equal(located(GAMMA, "http://localhost:8086", data), null, JSON.stringify(data));
  }
  assert.equal(located(null, "http://localhost:8086", where(deep)), null);
});

test("an app says that it listens with {type: gravitee-lab:ready}, from the origin of the UI of its tab", () => {
  const ready = { type: "gravitee-lab:ready" };
  assert.equal(isReady(BANK, "http://localhost:8110", ready), true);
  assert.equal(isReady({ ...BANK, url: "http://localhost:8110/some/page?x=1#y" }, "http://localhost:8110", ready), true);
  assert.equal(isReady(BANK, "http://localhost:8110", { ...ready, extra: 1 }), true);   // only its type counts
  for (const origin of ["http://localhost:8086", "http://127.0.0.1:8110", "https://localhost:8110", "http://localhost:81100", "null", "", undefined, null]) {
    assert.equal(isReady(BANK, origin, ready), false, String(origin));
  }
  for (const data of [null, undefined, "gravitee-lab:ready", 42, {}, { type: "gravitee-lab:open" }, { type: "Gravitee-Lab:Ready" }, { type: "gravitee-lab:action", id: "a" }]) {
    assert.equal(isReady(BANK, "http://localhost:8110", data), false, JSON.stringify(data));
  }
  assert.equal(isReady(null, "http://localhost:8110", ready), false, "no tab, nobody to say it");
  assert.equal(isReady({ url: "blob:http://localhost:8110/x" }, "http://localhost:8110", ready), false, "a tab with no web address has no origin");
});

test("the origin an action is posted to is the one of the UI of the tab, never any", () => {
  assert.equal(originOf("http://localhost:8110/some/page"), "http://localhost:8110");
  assert.equal(originOf("https://localhost:8443"), "https://localhost:8443");
  for (const url of ["", "nope", "about:blank", "javascript:void(0)", "blob:http://localhost:8110/x", "data:text/html,x", null, undefined]) {
    assert.equal(originOf(url), null, String(url));
  }
});

const UP = { ...BANK, up: true };
const AT = { live: true, name: "Acme Bakery", tab: UP, status: "framed" };

test("an action can be sent while the playground runs and its first UI is there, shown or not yet looked at", () => {
  assert.equal(actionReason(AT), "");
  assert.equal(actionReason({ ...AT, status: "" }), "", "not looked at yet: its tab is shown first, and looks");
});

test("an action that cannot be sent says why: the playground, its UI", () => {
  assert.equal(actionReason({ ...AT, live: false }), "Acme Bakery is not running");
  assert.equal(actionReason({ ...AT, tab: null }), "Acme Bakery shows no UI beside its guide");
  assert.equal(actionReason({ ...AT, tab: { ...UP, up: false } }), "Acme Bakery is not running");
  assert.equal(actionReason({ ...AT, tab: { ...UP, up: false } }, () => "Docker is not reachable"), "Docker is not reachable", "the page says what a UI that is down is");
  assert.equal(actionReason({ ...AT, status: "refused" }), "Acme Bakery cannot be shown here");
  assert.equal(actionReason({ ...AT, status: "down" }), "Acme Bakery does not answer");
});

test("the reason that comes first is the one given: not running, then no UI, then the UI itself", () => {
  const all = { live: false, name: "Acme Bakery", tab: null, status: "refused" };
  assert.equal(actionReason(all), "Acme Bakery is not running");
  assert.equal(actionReason({ ...all, live: true }), "Acme Bakery shows no UI beside its guide");
  assert.equal(actionReason({ ...all, live: true, tab: { ...UP, up: false } }), "Acme Bakery is not running");
  assert.equal(actionReason({ ...all, live: true, tab: UP }), "Acme Bakery cannot be shown here");
});

// --- The terminal ------------------------------------------------------------------------

test("the terminal is never shorter than 120 px and leaves the UIs 160, in a panel whatever its height", () => {
  assert.deepEqual(termLimits(600), { min: 120, max: 440 });
  assert.deepEqual(termLimits(250), { min: 120, max: 120 }, "a panel with no room for both keeps the terminal its minimum");
  assert.deepEqual(termLimits(0), { min: 120, max: 120 });
});

test("its height is the one asked for, or kept, within the limits, and 240 px without one", () => {
  assert.equal(termHeight(null, 600), 240);
  assert.equal(termHeight("", 600), 240);
  assert.equal(termHeight("not a number", 600), 240, "what the browser kept that is no number");
  assert.equal(termHeight(300, 600), 300);
  assert.equal(termHeight("300", 600), 300, "kept as text");
  assert.equal(termHeight(299.6, 600), 300);
  assert.equal(termHeight(10, 600), 120);
  assert.equal(termHeight(9000, 600), 440);
  assert.equal(termHeight(300, 400), 240, "a panel that shrank takes the terminal with it");
});

test("its divider's cursor shows the end of its travel it is at", () => {
  assert.equal(termEdge(120, 600), "min");
  assert.equal(termEdge(440, 600), "max");
  assert.equal(termEdge(300, 600), "");
  assert.equal(termEdge(120, 250), "min", "no travel at all: the minimum wins");
});

test("Up makes the terminal taller and Down shorter, by a step or with Shift by a bigger one, and Home and End go to its limits", () => {
  assert.equal(termNudge("ArrowUp", false, 300, 600), 316);
  assert.equal(termNudge("ArrowDown", false, 300, 600), 284);
  assert.equal(termNudge("ArrowUp", true, 300, 600), 364);
  assert.equal(termNudge("ArrowDown", true, 300, 600), 236);
  assert.equal(termNudge("Home", false, 300, 600), 440, "Home is the tallest");
  assert.equal(termNudge("End", false, 300, 600), 120, "End is the shortest");
  assert.equal(termNudge("ArrowUp", true, 420, 600), 440, "stopped at its limit");
  assert.equal(termNudge("ArrowDown", true, 150, 600), 120);
  for (const key of ["ArrowLeft", "ArrowRight", "Enter", "a", "Tab"]) assert.equal(termNudge(key, false, 300, 600), null, key);
});

test("a block of the guide runs only at a shell that is there: the playground runs, and so does the container of its terminal", () => {
  const name = "Acme Freight", terminal = { label: "kubectl", up: true };
  assert.equal(terminalReason({ live: true, name, terminal }), "");
  assert.equal(terminalReason({ live: true, name, terminal: null }), "Acme Freight has no terminal");
  assert.equal(terminalReason({ live: false, name, terminal }), "Acme Freight is not running");
  assert.equal(terminalReason({ live: true, name, terminal: { ...terminal, up: false } }), "kubectl is not running");
  assert.equal(terminalReason({ live: false, name, terminal: null }), "Acme Freight has no terminal", "no terminal comes first: nothing to wait for");
});

test("what a block types is its lines and one Enter at the end, whatever it ended with", () => {
  assert.equal(commandOf("k get pods"), "k get pods\n");
  assert.equal(commandOf("k get pods\n"), "k get pods\n");
  assert.equal(commandOf("k get pods\n\n  \n"), "k get pods\n");
  assert.equal(commandOf("curl -s x \\\n  -H y"), "curl -s x \\\n  -H y\n", "a continued line stays continued");
});

test("the output of the shell arrives as base64 and is read as bytes, the ones of a color sequence too", () => {
  assert.deepEqual([...bytesOf("G1szMm1vaxtbMG0NCg==")], [27, 91, 51, 50, 109, 111, 107, 27, 91, 48, 109, 13, 10]);
  assert.deepEqual([...bytesOf("")], []);
  assert.deepEqual([...bytesOf("w6k=")], [195, 169], "the two bytes of é, which the terminal decodes");
});

test("an action looks when its id starts with open-, and does otherwise", () => {
  assert.equal(isLook("open-kafka-plans"), true);
  assert.equal(isLook("send-order"), false);
  assert.equal(isLook("reopen-it"), false);
});
