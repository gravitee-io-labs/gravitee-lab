// The page of a running playground that has UIs to show (web/js/workspace/workspace.js): run for real over a small DOM, with the frames' windows keeping what
// they are sent and the window keeping its history. The page says nothing to the apps in its frames but the actions of its guide, which only the app of the first
// tab is sent, once it has said that it listens; an app may ask for an address to load in a tab; and
// a tab adds no entry to the history of the page, which the UIs add to by themselves; an address loaded in a UI is an entry
// of that UI's own, so that its Back goes back to the page it was on and never out of this page. The same two panels are rows
// when the window has not the room for columns, and nothing else changes.
import assert from "node:assert/strict";
import { mock, test } from "node:test";

import { Ev, flush, installDom, isOff, offWhy } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });   // a tab that shows a notice looks again every few seconds: not in this test
const page = installDom({
  ids: ["confirm-cancel", "choice-cancel", "confirm", "choice", "toasts", "confirm-title", "confirm-text", "confirm-ok", "confirm-input"],
  fetch: async (path) => asked(path),
});
const HEALTHY = { key: "healthy", label: "Healthy", running: true };
const STOPPED = { key: "stopped", label: "Stopped", running: false };
const BANK = { id: "bakery-web", label: "Acme Bakery", url: "http://localhost:8110", up: true, state: HEALTHY };
const GAMMA = { id: "gamma-ui", label: "Gamma Console", url: "http://localhost:8086", up: true, state: HEALTHY };
const DOWN = (tab) => ({ ...tab, up: false, state: STOPPED });
const found = { answers: true, frameable: true, reason: "" };
let verdicts = {};
const looked = [];
const asked = (path) => {
  const url = new URL(path, "http://lab.test").searchParams.get("url");
  looked.push(url);
  return { ok: true, status: 200, json: async () => verdicts[url] || found };
};

const { Workspace } = await import("../../../web/js/workspace/workspace.js");
const { router, store } = await import("../../../web/js/core.js");
store.state = { blind: false, wait: "" };   // Docker answers: a UI that is down says that it does not run

const section = page.el("page-playground", "section");

// The page decides by its container's width, less the split's margins (12 px a side): `width` is what the card has in the split
const setWidth = (width) => { page.body.clientWidth = width + 24; };

function draw(tabs = [BANK, GAMMA], guide = true, id = "acme-bakery", width = 1400) {
  const item = { id, guide, tabs };
  section.innerHTML = Workspace.html(item, "<p>details</p>");
  setWidth(width);
  globalThis.innerWidth = width;
  page.doc.documentElement.clientWidth = width;
  Workspace.attach(section, item, { attention: "" });
  return item;
}
const card = () => section.querySelector(".pg-split");
const tab = (id) => section.querySelector(`[data-tab="${id}"]`);
const frames = () => section.querySelectorAll("iframe");
const frameOf = (url) => frames().find((frame) => frame.src === url);
const sent = (url) => frameOf(url).contentWindow.posted;
const loaded = (url) => frameOf(url).dispatchEvent(new Ev("load", { bubbles: false }));

draw();
await flush();
tab("gamma-ui").click();
await flush();

test("the UIs are made when their tabs are first shown", () => {
  assert.deepEqual(frames().map((frame) => frame.src), ["http://localhost:8110", "http://localhost:8086"]);
});

test("the page tells the apps in its frames nothing: not when a frame loads, not when the guide turns, not when the window goes narrow and wide again", () => {
  loaded("http://localhost:8110");
  loaded("http://localhost:8086");
  section.querySelector("#pg-guide").dataset.page = "2";
  setWidth(900);
  page.resized.run();
  assert.equal(card().classList.contains("rows"), true);
  setWidth(1400);
  page.resized.run();
  assert.equal(card().classList.contains("rows"), false);
  for (const url of ["http://localhost:8110", "http://localhost:8086"]) assert.deepEqual(sent(url), [], url);
});

test("a tab that shows a notice has no frame", async () => {
  verdicts = { "http://localhost:8086": { answers: true, frameable: false, reason: "refuses" } };
  draw([BANK, GAMMA], true, "other");
  await flush();
  tab("gamma-ui").click();
  await flush();
  assert.deepEqual(frames().map((frame) => frame.src), ["http://localhost:8110"], "Gamma shows a notice: no frame");
  verdicts = {};
});

test("an app asks for an address to load in a tab: only a frame of a tab, from its origin, for an address under a tab's UI; a new entry of that UI's history", async () => {
  draw();
  await flush();
  for (const id of ["bakery-web", "gamma-ui"]) { tab(id).click(); await flush(); }
  const bank = frameOf("http://localhost:8110"), gamma = frameOf("http://localhost:8086");
  loaded("http://localhost:8110");
  loaded("http://localhost:8086");
  const ask = (source, origin, url) => page.fire("window", "message", { source, origin, data: { type: "gravitee-lab:open", url } });
  ask(bank.contentWindow, "http://localhost:8110", "http://localhost:8086/environments/default/aim");
  assert.deepEqual(gamma.contentWindow.assigned, ["http://localhost:8086/environments/default/aim"], "a new entry of the UI's history: its Back has somewhere to go");
  ask({}, "http://localhost:8110", "http://localhost:8086/other");                       // not a frame of the page
  ask(bank.contentWindow, "http://localhost:9999", "http://localhost:8086/other");        // not the origin of a tab's UI
  ask(bank.contentWindow, "http://localhost:8110", "https://example.com/");               // not under a tab's UI
  assert.deepEqual(gamma.contentWindow.assigned, ["http://localhost:8086/environments/default/aim"]);
  assert.deepEqual(bank.contentWindow.assigned, []);
  for (const frame of [bank, gamma]) {
    assert.deepEqual(frame.contentWindow.posted, [], "nothing is posted to either");
    assert.deepEqual(frame.contentWindow.replaced, [], "and nothing replaces the page it holds");
  }
});

test("an address asked for before its UI is there is loaded after the UI's start: the first page of a UI is never the page that was asked for", async () => {
  draw([BANK, GAMMA], true, "late");   // no tab was ever chosen here: the first UI shows, the other is not made until it is asked for
  await flush();
  assert.equal(frameOf("http://localhost:8086"), undefined, "Gamma has no frame yet");
  loaded("http://localhost:8110");
  const deep = "http://localhost:8086/environments/default/observe/logs/abc?apiId=x";
  page.fire("window", "message", { source: frameOf("http://localhost:8110").contentWindow, origin: "http://localhost:8110", data: { type: "gravitee-lab:open", url: deep } });
  await flush();
  const gamma = frameOf("http://localhost:8086");
  assert.ok(gamma, "its frame is made, at the UI's start address and not at the one asked for");
  assert.deepEqual(gamma.contentWindow.assigned, [], "nothing is sent before it has loaded");
  loaded("http://localhost:8086");
  assert.deepEqual(gamma.contentWindow.assigned, [], "and not in the load itself: a browser makes an address loaded before the load is over take the place of the page");
  mock.timers.tick(0);
  assert.deepEqual(gamma.contentWindow.assigned, [deep], "then the address asked for, as an entry after the start");
  loaded("http://localhost:8086");
  mock.timers.tick(0);
  assert.deepEqual(gamma.contentWindow.assigned, [deep], "once");
  assert.equal(tab("gamma-ui").getAttribute("aria-selected"), "true", "and its tab is shown");
  draw();   // the usual card again, for what follows
});

// --- The history: the UIs add entries to it, the page's own turns do not -------------------

const { session } = page;
const shown = [];   // the pages of the playground's guide the router showed, in order
const pageAt = () => location.hash;
const entered = () => session.entries.length;

location.hash = "#/playgrounds";   // the list the person came from
location.hash = "#/playgrounds/acme-bakery";
router.register({ id: "list", pattern: /^#\/playgrounds$/, show: () => shown.push("list") });
router.register({ id: "playground", pattern: /^#\/playgrounds\/((?:local\/)?[a-z0-9-]+)(?:\/([a-z0-9-]+))?$/, show: ([, step]) => shown.push(step || "") });
router.start();

const link = (href) => {
  const anchor = page.doc.createElement("a");
  anchor.setAttribute("href", href);
  anchor.href = href.startsWith("#") ? `http://localhost:9000/${href}` : href;   // as the browser resolves it
  return anchor;
};
const click = (extra = {}) => new Ev("click", { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...extra });

test("a tab, the divider and a window that goes narrow and wide again add no entry", async () => {
  draw();
  await flush();
  const before = entered(), hash = pageAt();
  for (const id of ["bakery-web", "gamma-ui", "@details", "gamma-ui"]) { tab(id).click(); await flush(); }
  setWidth(900);
  page.resized.run();
  setWidth(1400);
  page.resized.run();
  assert.equal(entered(), before);
  assert.equal(pageAt(), hash);
});

test("a link of the guide under a UI's address loads there as an entry of that UI, and so does Back to start: the page adds none of its own", async () => {
  draw();
  await flush();
  for (const id of ["bakery-web", "gamma-ui"]) { tab(id).click(); await flush(); }
  loaded("http://localhost:8110");
  loaded("http://localhost:8086");
  const before = entered(), gamma = frameOf("http://localhost:8086");
  const followed = click();
  assert.equal(Workspace.follow(followed, link("http://localhost:8086/environments/default/aim/agents")), true);
  assert.deepEqual(gamma.contentWindow.assigned, ["http://localhost:8086/environments/default/aim/agents"]);
  section.querySelector("[data-home]").click();
  assert.equal(gamma.contentWindow.assigned.at(-1), GAMMA.url, "back at its start");
  assert.deepEqual(gamma.contentWindow.replaced, [], "no page of a UI is replaced by the page: its Back goes back through them");
  assert.equal(entered(), before, "the page's own history is as it was");
});

test("Reload loads the page of a UI again: the page it is on when the lab can reach it, else the address it loaded last, and the app is not taken to listen until it says so", async () => {
  drawActions([BANK, GAMMA], { id: "reload-page" });
  await flush();
  tab("bakery-web").click();
  await flush();
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  const bank = frameOf("http://localhost:8110");
  section.querySelector("[data-reload]").click();
  assert.equal(bank.contentWindow.reloaded, 1, "the page it is on");
  assert.deepEqual([bank.contentWindow.assigned, bank.contentWindow.replaced], [[], []]);
  Workspace.send("ask-ace-accounts");
  assert.equal(sent("http://localhost:8110").length, 0, "not posted to the page that goes: it waits");
  listens("http://localhost:8110");
  assert.equal(sent("http://localhost:8110").length, 1, "and goes when the new page says that it listens");
  // A page of another origin cannot be reloaded from here: the address it loaded last, in the place of the page, as no entry of its history
  bank.contentWindow.sealed = true;
  section.querySelector("[data-reload]").click();
  assert.equal(bank.contentWindow.reloaded, 1);
  assert.deepEqual(bank.contentWindow.replaced, ["http://localhost:8110"]);
  assert.deepEqual(bank.contentWindow.assigned, []);
});

test("a UI that says which page it is on is reloaded there, and Open in a new tab opens it: a page of another origin cannot be read, only told", async () => {
  draw();
  await flush();
  tab("gamma-ui").click();
  await flush();
  loaded("http://localhost:8086");
  const gamma = frameOf("http://localhost:8086"), open = () => section.querySelector("[data-open]").href;
  const deep = "http://localhost:8086/environments/default/aim/agents?page=2";
  assert.equal(open(), GAMMA.url, "it says nothing yet: where the lab sent it");
  page.fire("window", "message", { source: gamma.contentWindow, origin: "http://localhost:8086", data: { type: "gravitee-lab:location", url: deep } });
  assert.equal(open(), deep, "Open in a new tab opens the page it is on");
  gamma.contentWindow.sealed = true;
  section.querySelector("[data-reload]").click();
  assert.deepEqual(gamma.contentWindow.replaced, [deep], "Reload loads that page, in the place of the one it holds");
  assert.deepEqual(gamma.contentWindow.assigned, [], "no entry of its history is added");
  assert.equal(open(), GAMMA.url, "the page that comes says where it is: until it does, the start");
  // Said by another frame or another origin, it is nobody's word
  page.fire("window", "message", { source: frameOf("http://localhost:8086").contentWindow, origin: "http://localhost:8110", data: { type: "gravitee-lab:location", url: deep } });
  page.fire("window", "message", { source: {}, origin: "http://localhost:8086", data: { type: "gravitee-lab:location", url: deep } });
  assert.equal(open(), GAMMA.url);
  // Back to start forgets it too
  page.fire("window", "message", { source: gamma.contentWindow, origin: "http://localhost:8086", data: { type: "gravitee-lab:location", url: deep } });
  section.querySelector("[data-home]").click();
  assert.equal(open(), GAMMA.url);
});

test("Reload is offered with the UI and nothing else: a notice has nothing to reload", async () => {
  draw();
  await flush();
  tab("bakery-web").click();
  await flush();
  loaded("http://localhost:8110");
  assert.equal(section.querySelector("[data-reload]").hidden, false);
  tab("@details").click();
  await flush();
  assert.equal(section.querySelector("[data-reload]").hidden, true, "the details have no page to reload");
  tab("bakery-web").click();
  await flush();
  assert.equal(section.querySelector("[data-reload]").getAttribute("data-tip"), "Reload");
  assert.equal(section.querySelector("[data-reload]").getAttribute("aria-label"), "Reload");
});

test("a key held or the middle button leaves a link of the guide to the browser, which opens a new tab: this page is not moved", async () => {
  draw();
  await flush();
  const before = entered(), hash = pageAt();
  for (const extra of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    const e = click(extra);
    assert.equal(Workspace.follow(e, link("http://localhost:8086/environments/default/aim")), false, JSON.stringify(extra));
    assert.equal(e.defaultPrevented, false);
  }
  assert.equal(entered(), before);
  assert.equal(pageAt(), hash);
});

test("a link that is under no UI's address is the browser's own, and so is any link where no card is shown", async () => {
  draw();
  await flush();
  for (const href of ["#/playgrounds/other/2", "#/playgrounds/acme-bakery/2", "https://example.com/", "http://localhost:9999/"]) {
    const e = click();
    assert.equal(Workspace.follow(e, link(href)), false, href);
    assert.equal(e.defaultPrevented, false);
  }
  Workspace.leave(section);   // the details before a start: the page is a page like any other
  const e = click();
  assert.equal(Workspace.follow(e, link("http://localhost:8086/environments/default/aim")), false);
  assert.equal(e.defaultPrevented, false);
  draw();   // the card again, for what follows
});

// --- One window, one layout ------------------------------------------------------------------

test("a window has one layout: the container's width decides, the same two panels are columns or rows, and the page is the same page", async () => {
  const rows = () => card().classList.contains("rows");
  const SPLIT = 1090;   // the least the card has for both panels side by side
  for (const width of [1400, SPLIT, SPLIT - 1, SPLIT + 1, 1100, SPLIT]) {
    draw([BANK, GAMMA], true, `window-${width}`, width);
    await flush();
    page.resized.run();
    assert.equal(rows(), width < SPLIT, `${width} px: ${rows() ? "rows" : "columns"}`);
    assert.equal(section.classList.contains("fill"), true, "the page does not scroll, whichever the layout");
    assert.deepEqual(frames().map((frame) => frame.src), ["http://localhost:8110"], `${width} px: its UI is loaded, rows or columns`);
    assert.equal(tab("bakery-web").getAttribute("aria-selected"), "true");
  }
  draw();   // the usual card again, for what follows
});

test("a window that goes narrow and wide again changes nothing but the place of the panels: the tab, the frames and what they were sent stay", async () => {
  draw([BANK, GAMMA], true, "resize");
  await flush();
  tab("gamma-ui").click();
  await flush();
  loaded("http://localhost:8110");
  loaded("http://localhost:8086");
  const made = frames().map((frame) => frame.src);
  for (const width of [900, 1400, 700, 1400]) {
    setWidth(width);
    page.resized.run();
    assert.equal(card().classList.contains("rows"), width < 1090);
    assert.equal(tab("gamma-ui").getAttribute("aria-selected"), "true");
    assert.deepEqual(frames().map((frame) => frame.src), made);
    assert.equal(section.querySelector("#pg-panel-bakery-web").hidden, true);
    assert.equal(section.querySelector("#pg-panel-gamma-ui").hidden, false);
    // a tab chosen in rows shows its panel and hides the others, as in columns
    for (const [shownId, hiddenId] of [["bakery-web", "gamma-ui"], ["gamma-ui", "bakery-web"]]) {
      tab(shownId).click();
      assert.equal(section.querySelector(`#pg-panel-${shownId}`).hidden, false, `${width} px: ${shownId}`);
      assert.equal(section.querySelector(`#pg-panel-${hiddenId}`).hidden, true, `${width} px: ${hiddenId}`);
      assert.equal(section.querySelector('[id="pg-panel-@details"]').hidden, true);
    }
  }
  for (const url of made) assert.deepEqual(sent(url), []);
  draw();
});

test("a console is a tab of its own, whatever its service is called: even a service named like the details tab", async () => {
  draw([{ ...BANK, id: "details" }, GAMMA], true, "details-named");
  await flush();
  assert.deepEqual(section.querySelectorAll('[role="tab"]').map((button) => button.dataset.tab), ["details", "gamma-ui", "@details"]);
  assert.equal(section.querySelectorAll('[role="tabpanel"]').length, 3);
  assert.equal(new Set(section.querySelectorAll('[role="tabpanel"]').map((panel) => panel.id)).size, 3, "three panels, three ids");
  tab("@details").click();
  await flush();
  assert.equal(section.querySelector('[id="pg-panel-details"]').hidden, true);
  assert.equal(section.querySelector('[id="pg-panel-@details"]').hidden, false);
  draw();
});

// --- The actions of the guide ---------------------------------------------------------------

const ACTIONS = ["ask-ace-accounts", "leak-an-iban"];
const guideOf = () => section.querySelector("#pg-guide");
const buttons = () => section.querySelectorAll("[data-action]");
const listens = (url, origin = new URL(url).origin) => page.fire("window", "message",
  { source: frameOf(url).contentWindow, origin, data: { type: "gravitee-lab:ready" } });
const got = (url) => sent(url).map(({ message, origin }) => `${message.id}@${origin}`);

// The guide as the markdown renderer writes it: its action links are buttons, disabled until the page says they can be used
function drawActions(tabs = [BANK, GAMMA], { id = "actions", live = true, width = 1400 } = {}) {
  const item = draw(tabs, true, id, width);
  guideOf().innerHTML = `<p>Click ${ACTIONS.map((action) => `<button type="button" class="btn guide-action" data-action="${action}" disabled title="Not available here">${action}</button>`).join(" then ")}</p>`;
  Workspace.actions(guideOf(), "Acme Bakery", live);
  return item;
}
const state = () => buttons().map((button) => [isOff(button), isOff(button) ? offWhy(button) : ""]);
const SHOWN = (id) => tab(id).getAttribute("aria-selected") === "true";

test("the action links can be used while the first UI can show, and say why they cannot otherwise", async () => {
  drawActions();
  await flush();
  assert.deepEqual(state(), [[false, ""], [false, ""]], "enabled: nothing to say");
  setWidth(900);
  page.resized.run();
  assert.equal(card().classList.contains("rows"), true);
  assert.deepEqual(state(), [[false, ""], [false, ""]], "the window is no reason: the UI is under the guide, and it is as good");
  setWidth(1400);
  page.resized.run();
  Workspace.actions(guideOf(), "Acme Bakery", false);
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery is not running", "Acme Bakery is not running"]);
  Workspace.actions(guideOf(), "Acme Bakery", true);
  assert.equal(state()[0][0], false);
});

test("the action links wait for what is known of the first UI: down, refused or not answering", async () => {
  drawActions([DOWN(BANK), GAMMA], { id: "bank-down" });
  await flush();
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery is not running", "Acme Bakery is not running"], "the state says it is down");
  verdicts = { "http://localhost:8110": { answers: true, frameable: false, reason: "refuses" } };
  localStorage.setItem("tab:bank-refuses", "@details");   // its UI is not looked at until its tab is shown
  drawActions([BANK, GAMMA], { id: "bank-refuses" });
  await flush();
  assert.equal(state()[0][0], false, "not looked at yet: enabled");
  tab("bakery-web").click();
  await flush();
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery cannot be shown here", "Acme Bakery cannot be shown here"], "once its tab was shown and it refused");
  verdicts = { "http://localhost:8110": { answers: false, frameable: false, reason: "" } };
  drawActions([BANK, GAMMA], { id: "bank-silent" });
  await flush();
  tab("bakery-web").click();
  await flush();
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery does not answer", "Acme Bakery does not answer"]);
  assert.equal(Workspace.send("leak-an-iban"), false, "a disabled action is not sent");
  verdicts = {};
});

test("the action of another UI than the first is no business of the page: only the first tab is looked at", async () => {
  verdicts = { "http://localhost:8086": { answers: true, frameable: false, reason: "refuses" } };
  drawActions([BANK, GAMMA], { id: "gamma-refuses" });
  await flush();
  tab("gamma-ui").click();
  await flush();
  assert.deepEqual(state(), [[false, ""], [false, ""]], "Gamma refusing frames does not stop the actions of the guide, which go to the app");
  verdicts = {};
});

test("a click on an action shows the first tab, makes its UI and sends once the app has said that it listens", async () => {
  localStorage.setItem("tab:queue", "@details");   // the person was on the details: no UI is made yet
  drawActions([BANK, GAMMA], { id: "queue" });
  await flush();
  tab("@details").click();
  await flush();
  assert.equal(frames().length, 0, "no UI was shown yet: none is made");
  assert.equal(Workspace.send("ask-ace-accounts"), true);
  assert.equal(SHOWN("bakery-web"), true, "its tab is shown");
  await flush();
  assert.deepEqual(frames().map((frame) => frame.src), ["http://localhost:8110"], "its UI is made");
  loaded("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110"), [], "nothing is sent to an app that has not said that it listens");
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110"), ["ask-ace-accounts@http://localhost:8110"], "sent at once when it does, to the origin of its UI and no other");
  assert.deepEqual(sent("http://localhost:8110")[0].message, { type: "gravitee-lab:action", id: "ask-ace-accounts" });
  assert.equal(Workspace.send("leak-an-iban"), true);
  assert.deepEqual(got("http://localhost:8110"), ["ask-ace-accounts@http://localhost:8110", "leak-an-iban@http://localhost:8110"], "then each click is sent as it comes");
  assert.equal(SHOWN("bakery-web"), true);
});

test("an action sent from the Details or from another UI brings the first tab back, each time", async () => {
  drawActions([BANK, GAMMA], { id: "tabs" });
  await flush();
  tab("gamma-ui").click();
  await flush();
  assert.equal(SHOWN("gamma-ui"), true);
  Workspace.send("ask-ace-accounts");
  await flush();
  assert.equal(SHOWN("bakery-web"), true);
  listens("http://localhost:8110");
  tab("@details").click();
  Workspace.send("leak-an-iban");
  assert.equal(SHOWN("bakery-web"), true);
  assert.deepEqual(got("http://localhost:8110"), ["ask-ace-accounts@http://localhost:8110", "leak-an-iban@http://localhost:8110"]);
});

test("the actions asked while the app loads are sent in their order once it listens, and only once: one that waits is not asked twice", async () => {
  drawActions([BANK, GAMMA], { id: "order" });
  await flush();
  Workspace.send("leak-an-iban");
  Workspace.send("ask-ace-accounts");
  Workspace.send("leak-an-iban");   // a second click on what waits: it is asked once, where it was first
  await flush();
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110").map((line) => line.split("@")[0]), ["leak-an-iban", "ask-ace-accounts"]);
  listens("http://localhost:8110");   // it says it again (its page loaded again): what was sent is not sent again
  assert.equal(sent("http://localhost:8110").length, 2);
  Workspace.send("leak-an-iban");     // once it listens each click is sent, however many
  Workspace.send("leak-an-iban");
  assert.equal(sent("http://localhost:8110").length, 4);
});

test("an app that never listens does not make the queue grow: the same action waits once", async () => {
  drawActions([BANK, GAMMA], { id: "silent-app" });
  await flush();
  for (let i = 0; i < 50; i++) Workspace.send(i % 2 ? "leak-an-iban" : "ask-ace-accounts");
  await flush();
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110").map((line) => line.split("@")[0]), ["ask-ace-accounts", "leak-an-iban"], "a late word sends each once");
});

test("an app is believed that it listens only if it is the frame of the first tab, from the origin of its UI, with that message", async () => {
  drawActions([BANK, GAMMA], { id: "believed" });
  await flush();
  tab("gamma-ui").click();
  await flush();
  Workspace.send("ask-ace-accounts");
  await flush();
  const ready = { type: "gravitee-lab:ready" };
  const say = (source, origin, data = ready) => page.fire("window", "message", { source, origin, data });
  say(frameOf("http://localhost:8086").contentWindow, "http://localhost:8086");   // the second tab's app
  say(frameOf("http://localhost:8110").contentWindow, "http://localhost:8086");   // the right frame, the wrong origin
  say(frameOf("http://localhost:8110").contentWindow, "http://localhost:9999");
  say(frameOf("http://localhost:8110").contentWindow, "null");
  say({}, "http://localhost:8110");                                               // not a frame of the page
  say(window, "http://localhost:8110");
  say(null, "http://localhost:8110");
  for (const data of [null, "gravitee-lab:ready", {}, { type: "gravitee-lab:open", url: "http://localhost:8086/" }, { type: "ready" }, { type: "Gravitee-Lab:Ready" }]) {
    say(frameOf("http://localhost:8110").contentWindow, "http://localhost:8110", data);
  }
  assert.deepEqual(got("http://localhost:8110"), [], "none of them made it listen");
  assert.deepEqual(got("http://localhost:8086"), [], "and the other app is sent nothing, ever");
  say(frameOf("http://localhost:8110").contentWindow, "http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110"), ["ask-ace-accounts@http://localhost:8110"]);
  assert.deepEqual(got("http://localhost:8086"), []);
});

test("an id that is none is never sent; an action is sent in rows as in columns", async () => {
  drawActions([BANK, GAMMA], { id: "refused" });
  await flush();
  listens("http://localhost:8110");
  for (const id of ["", "Ask-Ace", "ask ace", "../x", "ask-ace\n", undefined, null, 42]) assert.equal(Workspace.send(id), false, String(id));
  assert.deepEqual(got("http://localhost:8110"), []);
  for (const width of [900, 1400]) {
    setWidth(width);
    page.resized.run();
    assert.equal(Workspace.send("ask-ace-accounts"), true, `${width} px`);
  }
  assert.deepEqual(got("http://localhost:8110"), ["ask-ace-accounts@http://localhost:8110", "ask-ace-accounts@http://localhost:8110"]);
});

test("a UI that went away and came back is another page: it says again that it listens, and what waited for it is dropped", async () => {
  const item = drawActions([BANK, GAMMA], { id: "back" });
  await flush();
  tab("bakery-web").click();
  await flush();
  listens("http://localhost:8110");
  Workspace.send("ask-ace-accounts");
  assert.equal(got("http://localhost:8110").length, 1);
  // its service goes down: the page shows a notice, and the actions say why they cannot be sent
  const refresh = (up) => Workspace.attach(section, { ...item, tabs: [up ? BANK : DOWN(BANK), GAMMA] }, { attention: "" });
  refresh(false);
  await flush();
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery is not running", "Acme Bakery is not running"]);
  refresh(true);
  await flush();
  assert.deepEqual(state(), [[false, ""], [false, ""]], "back");
  assert.equal(Workspace.send("leak-an-iban"), true);
  assert.equal(got("http://localhost:8110").length, 1, "the page it was loaded again: it must say that it listens first");
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110").map((line) => line.split("@")[0]), ["ask-ace-accounts", "leak-an-iban"]);
});

test("what waited for an app that went away is dropped, not sent to the page that comes back until it says that it listens", async () => {
  const item = drawActions([BANK, GAMMA], { id: "dropped" });
  await flush();
  tab("bakery-web").click();
  await flush();
  loaded("http://localhost:8110");
  assert.equal(Workspace.send("ask-ace-accounts"), true, "it waits: the app has not said that it listens");
  const refresh = (up) => Workspace.attach(section, { ...item, tabs: [up ? BANK : DOWN(BANK), GAMMA] }, { attention: "" });
  refresh(false);
  await flush();
  refresh(true);
  await flush();
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110"), [], "the page that comes back is another: what waited for the one before is not its business");
  Workspace.send("leak-an-iban");
  assert.deepEqual(got("http://localhost:8110").map((line) => line.split("@")[0]), ["leak-an-iban"]);
});

test("an address the page loads in a UI makes a new page, which says again that it listens: an action asked meanwhile waits for it", async () => {
  drawActions([BANK, GAMMA], { id: "reload" });
  await flush();
  tab("bakery-web").click();
  await flush();
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  Workspace.send("ask-ace-accounts");
  assert.equal(sent("http://localhost:8110").length, 1);
  const bank = frameOf("http://localhost:8110");
  // Back to start loads the start address: a new page, whose listener is not there yet
  section.querySelector("[data-home]").click();
  assert.equal(bank.contentWindow.assigned.at(-1), "http://localhost:8110");
  Workspace.send("leak-an-iban");
  assert.equal(sent("http://localhost:8110").length, 1, "not posted to the page that is going: it waits");
  listens("http://localhost:8110");
  assert.deepEqual(got("http://localhost:8110").map((line) => line.split("@")[0]), ["ask-ace-accounts", "leak-an-iban"], "and goes when the new page says that it listens");
  // a link of the guide under the app's address: the same
  assert.equal(Workspace.follow(click(), link("http://localhost:8110/accounts")), true);
  Workspace.send("ask-ace-accounts");
  assert.equal(sent("http://localhost:8110").length, 2, "waiting for the page it loads");
  listens("http://localhost:8110");
  assert.equal(sent("http://localhost:8110").length, 3);
});

test("an address with a fragment may be the page it is on, which does not say it again: what was said stands", async () => {
  drawActions([BANK, GAMMA], { id: "fragment" });
  await flush();
  tab("bakery-web").click();
  await flush();
  loaded("http://localhost:8110");
  listens("http://localhost:8110");
  assert.equal(Workspace.follow(click(), link("http://localhost:8110/#/teams")), true);
  assert.equal(frameOf("http://localhost:8110").contentWindow.assigned.at(-1), "http://localhost:8110/#/teams");
  Workspace.send("ask-ace-accounts");
  assert.equal(sent("http://localhost:8110").length, 1, "sent at once: nothing says it again");
});

test("only the buttons the guide's links became send an action: no other element with the attribute does, nor is it enabled", async () => {
  drawActions([BANK, GAMMA], { id: "buttons" });
  await flush();
  guideOf().innerHTML += '<p><span data-action="leak-an-iban" title="written">a sentence</span> <a data-action="leak-an-iban" title="written">a link</a></p>';
  Workspace.actions(guideOf(), "Acme Bakery", true);
  const others = section.querySelectorAll('span[data-action], a[data-action]');
  assert.equal(others.length, 2);
  assert.deepEqual(others.map((el) => el.title), ["written", "written"], "left as they were written");
  assert.deepEqual(section.querySelectorAll("button.guide-action").map((el) => [isOff(el), isOff(el) ? offWhy(el) : ""]), [[false, ""], [false, ""]], "the real buttons are enabled");
});

test("a playground that does not run, or shows no UI, sends nothing", async () => {
  drawActions([BANK, GAMMA], { id: "off", live: false });
  await flush();
  assert.equal(Workspace.send("ask-ace-accounts"), false);
  assert.deepEqual(state().map(([disabled]) => disabled), [true, true]);
  Workspace.leave(section);   // no card: the guide before a start, or of a playground with no UI
  Workspace.actions(guideOf() || section.querySelector(".guide"), "Acme Bakery", true);
  assert.equal(Workspace.send("ask-ace-accounts"), false);
  draw();   // the usual card again, for what follows
});

test("the page sends the apps in its frames nothing but the actions of the guide, with the origin of their UI", async () => {
  drawActions([BANK, GAMMA], { id: "only" });
  await flush();
  for (const id of ["bakery-web", "gamma-ui", "@details"]) { tab(id).click(); await flush(); }
  loaded("http://localhost:8110");
  loaded("http://localhost:8086");
  setWidth(900);
  page.resized.run();
  setWidth(1400);
  page.resized.run();
  Workspace.follow(click(), link("http://localhost:8086/environments/default/aim/agents"));
  section.querySelector("[data-home]").click();
  assert.deepEqual([...sent("http://localhost:8110"), ...sent("http://localhost:8086")], [], "nothing without an action");
  listens("http://localhost:8110");
  Workspace.send("ask-ace-accounts");
  for (const url of ["http://localhost:8110", "http://localhost:8086"]) {
    for (const { message, origin } of sent(url)) {
      assert.deepEqual(Object.keys(message), ["type", "id"], url);
      assert.equal(message.type, "gravitee-lab:action");
      assert.equal(origin, url, "never *");
    }
  }
  assert.equal(sent("http://localhost:8110").length, 1);
  assert.equal(sent("http://localhost:8086").length, 0);
  draw();
});

// --- Which tab shows, and what every playground has --------------------------------------------

const shownTab = () => section.querySelectorAll('[role="tab"]').find((button) => button.getAttribute("aria-selected") === "true")?.dataset.tab;
const redraw = (item, next) => Workspace.attach(section, { ...item, tabs: next }, { attention: "" });

test("the first console shows first, running or not: its notice says what its service is, and its UI loads where it is once it runs", async () => {
  const item = draw([DOWN(BANK), GAMMA], true, "first-down");
  await flush();
  assert.equal(shownTab(), "bakery-web", "not the details, nor the first UI that runs");
  assert.equal(frames().length, 0);
  assert.match(section.querySelector(".pg-notice").innerHTML, /Acme Bakery.*Stopped/s);
  redraw(item, [BANK, GAMMA]);
  await flush();
  assert.equal(shownTab(), "bakery-web", "nothing moves the person when a UI comes up");
  assert.deepEqual(frames().map((frame) => frame.src), ["http://localhost:8110"], "it loads where it is");
});

test("the tab the person chose is the one that shows, as the page is drawn again and when it comes back", async () => {
  const item = draw([BANK, GAMMA], true, "chosen");
  await flush();
  assert.equal(shownTab(), "bakery-web");
  tab("gamma-ui").click();
  await flush();
  redraw(item, [DOWN(BANK), GAMMA]);
  assert.equal(shownTab(), "gamma-ui");
  draw([BANK, GAMMA], true, "chosen");   // a new card for the same playground: the choice was kept
  assert.equal(shownTab(), "gamma-ui");
  draw([BANK], true, "chosen");          // the UI it chose is not listed any more: the first console shows
  assert.equal(shownTab(), "bakery-web");
});

test("a tab is never cut short, so it has no tooltip of its own: its name is in full", async () => {
  draw([BANK, { ...GAMMA, label: "A console whose name is a little long" }], true, "names");
  await flush();
  assert.deepEqual(section.querySelectorAll('[role="tab"]').map((button) => button.textContent.trim()), ["Acme Bakery", "A console whose name is a little long", "Details"]);
  assert.deepEqual(section.querySelectorAll('[role="tab"]').map((button) => button.title), ["", "", ""]);
});

test("a playground that lists no console is the same page: its guide beside its details, and its actions say it shows no UI", async () => {
  drawActions([], { id: "no-console" });
  await flush();
  assert.deepEqual(section.querySelectorAll('[role="tab"]').map((button) => button.dataset.tab), ["@details"]);
  assert.equal(shownTab(), "@details");
  assert.ok(section.querySelector(".pg-divider"), "the divider is there");
  assert.deepEqual(state().map(([, title]) => title), ["Acme Bakery shows no UI beside its guide", "Acme Bakery shows no UI beside its guide"]);
  assert.equal(Workspace.send("ask-ace-accounts"), false);
});

test("a playground without a guide is the same page too: the guide's place is there, whatever it holds", async () => {
  draw([BANK, GAMMA], false, "no-guide");
  await flush();
  assert.ok(section.querySelector("#pg-guide"), "the guide's place");
  assert.ok(section.querySelector(".pg-divider"), "the divider");
  assert.equal(card().classList.contains("solo"), false);
  setWidth(900);
  page.resized.run();
  assert.equal(card().classList.contains("rows"), true, "as narrow as any: no guide is no reason for another layout");
  assert.ok(section.querySelector("#pg-guide") && section.querySelector(".pg-bar"), "the same two panels");
  setWidth(1400);
  page.resized.run();
  draw();
});
