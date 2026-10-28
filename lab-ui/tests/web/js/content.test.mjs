// What the pages say while the content of the catalogs is on its way or missing (web/js/components/ui.js: contentStatus, contentLead), and the dot of the
// Catalogs entry of the menu (paintMenuDot), run for real over a small DOM and the state the server answers (fixtures.mjs): one block
// of the `.empty` family in each of the four keys the server gives, a status for a screen reader, with the buttons a click on which the page
// owns; what the foot of the Welcome dialog needs to keep its slots; the names the entry of the menu takes, and the name it has while only its
// icon shows. A server that does not say the content draws nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, bubble, installDom, text } from "./dom-stub.mjs";
import { ADDRESS, FIRST, HOSTILE, HOSTILE_QUOTED, PERSONAL, STATE, STATES, catalog, content, lab } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids, fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
globalThis.innerHeight = 800;
const { store } = await import("../../../web/js/core.js");
const { contentStatus, contentLead, paintMenuDot, paintProgress } = await import("../../../web/js/components/ui.js");

const draw = (markup) => {
  const root = page.doc.createElement("div");
  root.innerHTML = markup;
  page.doc.body.append(root);
  return root;
};
const withContent = (key, more = {}, catalogs = []) => lab({ content: content(key, more), catalogs });
const block = (root) => root.firstElementChild;
const buttons = (root) => root.querySelectorAll("[data-content-act]").map((el) => [el.getAttribute("data-content-act"), el.getAttribute("data-catalog"), text(el)]);

// --- The four keys ------------------------------------------------------------------------------------------------------------

test("the content is one block of the empty family and a status for a screen reader, once, in each of its four keys", () => {
  for (const key of ["downloading", "failed", "none", "ready"]) {
    const root = draw(contentStatus(withContent(key)));
    assert.equal(root.children.length, 1, key);
    assert.deepEqual([block(root).tag, block(root).getAttribute("class"), block(root).getAttribute("role"), block(root).getAttribute("data-content")],
      ["div", "empty", "status", key]);
    assert.equal(root.querySelectorAll('[role="status"]').length, 1, `${key}: said once`);
    assert.equal(root.querySelectorAll("[data-line]").length, 0, `${key}: a block, not a line`);
  }
});

test("while it downloads the block says Downloading with a spinner, a bar that follows the download and a mark that says where from", () => {
  const root = draw(contentStatus(withContent("downloading")));
  assert.equal(text(root.querySelector(".loading .tip-btn")), "Downloading", "its word, whose mark opens the address");
  assert.equal(root.querySelectorAll(".loading .spinner").length, 1);
  const bar = root.querySelector(".progress");
  assert.deepEqual([bar.getAttribute("data-progress"), bar.getAttribute("role"), bar.hidden], ["content", "progressbar", true], "drawn hidden until it is measured");
  // The mark is the address the server gives, never a word of the page's own
  const mark = root.querySelector(".tip-btn");
  assert.equal(mark.getAttribute("aria-label"), `Downloading: From ${ADDRESS}`);
  assert.ok(mark.querySelector("svg use").getAttribute("href").endsWith("#i-info"));
  assert.deepEqual(bubble(root.querySelector(".tip")), [`From ${ADDRESS}`]);
  assert.deepEqual(buttons(root), [], "nothing to press while it comes");
  // The long sentence exists once, as the reason the Welcome button gives; the blocks of the places do not say it
  assert.ok(!text(block(root)).includes("setups"));
  // No address, no mark: the word alone
  const bare = draw(contentStatus(withContent("downloading", { address: "" })));
  assert.equal(bare.querySelectorAll(".tip").length, 0);
  assert.equal(text(block(bare)), "Downloading");
});

test("the bar of the block is the progress of the content: it moves with the state, and hides once there is none", () => {
  const root = draw(contentStatus(withContent("downloading")));
  const bar = root.querySelector(".progress");
  store.state = withContent("downloading", { progress: 0.42 });
  paintProgress(root);
  assert.deepEqual([bar.hidden, bar.getAttribute("aria-valuenow"), bar.firstElementChild.styleValues.width], [false, "42", undefined]);
  store.state = withContent("downloading", { progress: null });
  paintProgress(root);
  assert.equal(bar.hidden, true);
  store.state = withContent("ready");
  assert.equal(store.progress("content"), null);
});

test("a download that failed says why in the server's words, and offers Try again for the catalog that has no copy", () => {
  const root = draw(contentStatus(withContent("failed", {}, [FIRST({ commit: "9f1c2ab" }), catalog({ id: "acme-hub", commit: "", version: "" })])));
  assert.equal(text(block(root).firstElementChild), "Cannot reach github.com. Check your connection, then try again.");
  assert.deepEqual(buttons(root), [["retry", "acme-hub", "Try again"]]);
  const button = root.querySelector("button");
  assert.deepEqual([button.getAttribute("type"), button.getAttribute("class")], ["button", "btn"], "a plain button: nothing is primary here");
  // One that downloads now is not the one to try again; with none to choose, the first; with none at all, none
  const downloading = [catalog({ id: "first", commit: "", progress: 0.3 }), catalog({ id: "second", commit: "" })];
  assert.deepEqual(buttons(draw(contentStatus(withContent("failed", {}, downloading)))), [["retry", "second", "Try again"]]);
  assert.deepEqual(buttons(draw(contentStatus(withContent("failed", {}, [FIRST({ commit: "9f1c2ab" })]))))[0].slice(0, 2), ["retry", "gravitee"]);
  assert.deepEqual(buttons(draw(contentStatus(withContent("failed")))), [["retry", "", "Try again"]]);
  // Folders and the rows that are only suggestions are not downloaded: never what Try again is for
  assert.deepEqual(buttons(draw(contentStatus(withContent("failed", {}, [catalog({ id: "folder", kind: "folder", commit: "" }),
    catalog({ id: "later", kind: "suggested", commit: "" }), catalog({ id: "real", commit: "" })]))))[0].slice(0, 2), ["retry", "real"]);
  // A failure without a line keeps its button
  assert.deepEqual(buttons(draw(contentStatus(withContent("failed", { line: "" })))), [["retry", "", "Try again"]]);
});

test("with no catalog the block says so and offers to add the one the server names, or to browse when it names none", () => {
  const root = draw(contentStatus(withContent("none")));
  assert.equal(text(block(root).firstElementChild), "No catalog added");
  assert.deepEqual(buttons(root), [["add", "gravitee", "Add Gravitee public"]]);
  const browse = draw(contentStatus(withContent("none", { offer: null })));
  assert.equal(browse.querySelectorAll("[data-content-act]").length, 0);
  assert.deepEqual([text(browse.querySelector("a")), browse.querySelector("a").getAttribute("href"), browse.querySelector("a").getAttribute("class")],
    ["Browse catalogs", "#/catalogs", "btn"]);
  // The Catalogs page is the place to browse: with nothing to add, it says only that none is
  const there = draw(contentStatus(withContent("none", { offer: null }), { kind: "catalog" }));
  assert.deepEqual([text(block(there)), there.querySelectorAll("a, button").length], ["No catalog added", 0]);
  assert.deepEqual(buttons(draw(contentStatus(withContent("none"), { kind: "catalog" }))), [["add", "gravitee", "Add Gravitee public"]]);
});

test("a list that has nothing of its kind says so only once the content is there; the Dashboard says it for all three", () => {
  const words = (kind) => text(draw(contentStatus(withContent("ready"), { kind })));
  assert.deepEqual(["playground", "setup", "addon"].map(words), ["No playground yet", "No setup yet", "No add-on yet"]);
  assert.equal(words(""), "No playground, setup or add-on yet");
  assert.equal(words(undefined), "No playground, setup or add-on yet");
  assert.equal(words("something else"), "No playground, setup or add-on yet", "a kind that is none of the three says what the Dashboard says");
  for (const kind of ["constructor", "toString", "hasOwnProperty", "__proto__"]) {
    assert.equal(words(kind), "No playground, setup or add-on yet", `${kind}: a name every object has is no kind of list`);
  }
  // With something to show, or on the page of the catalogs, a content that is there says nothing
  assert.equal(contentStatus(withContent("ready"), { kind: "addon", items: 2 }), "");
  assert.equal(contentStatus(withContent("ready"), { kind: "catalog" }), "");
});

test("the Catalogs page leaves a download and a failure to its rows", () => {
  assert.equal(contentStatus(withContent("downloading"), { kind: "catalog" }), "");
  assert.equal(contentStatus(withContent("failed"), { kind: "catalog" }), "");
});

test("a server that does not say the content, or says a key this page does not know, draws nothing", () => {
  assert.equal(contentStatus({}), "");
  assert.equal(contentStatus(null), "");
  assert.equal(contentStatus(undefined, { kind: "setup" }), "");
  assert.equal(contentStatus(lab({ content: undefined })), "");
  assert.equal(contentStatus(withContent("somewhere else")), "");
  assert.deepEqual(contentLead({}), { ready: true, why: "", extra: "" });
  assert.deepEqual(contentLead(withContent("somewhere else")), { ready: true, why: "", extra: "" });
});

// --- A list that has items of this machine --------------------------------------------------------------------------------------

test("with items to show the block is one line above them, whatever the key but ready, which draws none", () => {
  for (const key of ["downloading", "failed", "none"]) {
    const alone = draw(contentStatus(withContent(key), { kind: "setup" })), above = draw(contentStatus(withContent(key), { kind: "setup", items: 3 }));
    assert.equal(block(alone).hasAttribute("data-line"), false, key);
    assert.equal(block(above).hasAttribute("data-line"), true, key);
    assert.equal(text(block(above)), text(block(alone)), `${key}: the same words, the same component`);
    assert.equal(above.querySelectorAll('[role="status"]').length, 1);
  }
  assert.equal(contentStatus(withContent("ready"), { kind: "setup", items: 3 }), "", "removed once the content is there");
  // A caller may say it either way
  assert.equal(block(draw(contentStatus(withContent("none"), { items: 3, line: false }))).hasAttribute("data-line"), false);
  assert.equal(block(draw(contentStatus(withContent("none"), { line: true }))).hasAttribute("data-line"), true);
});

// --- What comes from the server is text --------------------------------------------------------------------------------------

test("a line, an address and the name of an offer are text, never markup", () => {
  const failed = draw(contentStatus(withContent("failed", { line: HOSTILE, address: HOSTILE_QUOTED })));
  assert.equal(failed.querySelectorAll("img, script").length, 0);
  assert.equal(text(failed.firstElementChild.firstElementChild), HOSTILE);
  const downloading = draw(contentStatus(withContent("downloading", { address: HOSTILE })));
  assert.equal(downloading.querySelectorAll("img").length, 0);
  assert.equal(downloading.querySelector(".tip-btn").getAttribute("aria-label"), `Downloading: From ${HOSTILE}`);
  const none = draw(contentStatus(withContent("none", { offer: { id: HOSTILE_QUOTED, name: HOSTILE } })));
  assert.equal(none.querySelectorAll("img, script").length, 0);
  assert.deepEqual(buttons(none), [["add", HOSTILE_QUOTED, `Add ${HOSTILE}`]]);
  const retry = draw(contentStatus(withContent("failed", {}, [catalog({ id: HOSTILE_QUOTED, commit: "" })])));
  assert.deepEqual(buttons(retry), [["retry", HOSTILE_QUOTED, "Try again"]]);
  assert.equal(retry.querySelectorAll("script").length, 0);
});

// --- The foot of the Welcome dialog -------------------------------------------------------------------------------------------

test("the lead of the Welcome foot stays in its place and says why it waits, in the words of the block, with the button beside it", () => {
  assert.deepEqual(contentLead(withContent("ready")), { ready: true, why: "", extra: "" });
  assert.deepEqual(contentLead(withContent("downloading")), { ready: false, why: "Downloading playgrounds, setups and add-ons", extra: "" });
  const failed = contentLead(withContent("failed", {}, [catalog({ id: "acme-hub", commit: "" })]));
  assert.deepEqual([failed.ready, failed.why], [false, "Cannot reach github.com. Check your connection, then try again."]);
  assert.deepEqual(buttons(draw(failed.extra)), [["retry", "acme-hub", "Try again"]]);
  const none = contentLead(withContent("none"));
  assert.deepEqual([none.ready, none.why], [false, "No catalog added"]);
  assert.deepEqual(buttons(draw(none.extra)), [["add", "gravitee", "Add Gravitee public"]]);
  assert.equal(draw(contentLead(withContent("none", { offer: null })).extra).querySelector("a").getAttribute("href"), "#/catalogs");
  // The reason is text for an attribute (the page escapes it where it writes markup); the button is markup, made safe
  const hostile = contentLead(withContent("failed", { line: HOSTILE }));
  assert.equal(hostile.why, HOSTILE);
  assert.equal(draw(hostile.extra).querySelectorAll("img").length, 0);
});

// --- The dot of the menu ----------------------------------------------------------------------------------------------------

const entry = html.match(/<a class="nav-item" href="#\/catalogs"[^>]*>.*?<\/a>/s)[0];
const menu = page.doc.createElement("nav");
menu.innerHTML = `<a class="nav-item" href="#/addons" data-nav="addons" aria-label="Add-ons" data-tip="Add-ons" data-tip-collapsed><span>Add-ons</span></a>${entry}`;
page.doc.body.append(menu);
const catalogs = menu.querySelector('[data-nav="catalogs"]'), addons = menu.querySelector('[data-nav="addons"]'), dot = menu.querySelector(".nav-dot");
const named = () => [catalogs.getAttribute("aria-label"), catalogs.dataset.tip, catalogs.dataset.tipCollapsed, dot.hidden];

test("the entry of the menu is as the page wrote it, with its dot hidden, until a catalog has something to press", () => {
  paintMenuDot(lab());
  assert.deepEqual(named(), ["Catalogs", "Where items come from", "Catalogs", true]);
  paintMenuDot(lab({ catalogs: [FIRST(), catalog({ attention: null })] }));
  assert.deepEqual(named(), ["Catalogs", "Where items come from", "Catalogs", true]);
  paintMenuDot(null);
  paintMenuDot(undefined);
  paintMenuDot({ catalogs: "not a list" });
  paintMenuDot({});
  assert.deepEqual(named(), ["Catalogs", "Where items come from", "Catalogs", true]);
});

test("the dot lights for the first reason that applies, and the entry says it to a screen reader and in its tooltip", () => {
  const reasons = { update: "Catalogs, update available", repair: "Catalogs, needs repair", lab: "Catalogs, update the Lab", download: "Catalogs, not downloaded" };
  for (const [attention, said] of Object.entries(reasons)) {
    paintMenuDot(lab({ catalogs: [FIRST(), catalog({ attention })] }));
    assert.deepEqual(named(), [said, said, said, false], attention);
  }
  // Several rows: the order of the reasons decides, not the order of the rows
  const rows = (...attentions) => lab({ catalogs: attentions.map((attention, i) => catalog({ id: `c${i}`, attention })) });
  paintMenuDot(rows("download", "lab", "repair"));
  assert.equal(named()[0], "Catalogs, needs repair");
  paintMenuDot(rows("download", "update"));
  assert.equal(named()[0], "Catalogs, update available");
  paintMenuDot(rows("download", "lab"));
  assert.equal(named()[0], "Catalogs, update the Lab");
  // A word the page does not know lights nothing
  paintMenuDot(rows("somewhere else"));
  assert.deepEqual(named(), ["Catalogs", "Where items come from", "Catalogs", true]);
  // It goes out when the reason does, and comes back to what the page wrote
  paintMenuDot(rows("update"));
  paintMenuDot(rows(null));
  assert.deepEqual(named(), ["Catalogs", "Where items come from", "Catalogs", true]);
  assert.equal(addons.getAttribute("aria-label"), "Add-ons", "no other entry changes");
  assert.equal(addons.dataset.tip, "Add-ons");
});

test("a page without the entry draws nothing and breaks nothing", () => {
  const bare = page.doc.createElement("nav");
  bare.innerHTML = '<a class="nav-item" href="#/addons" data-nav="addons"><span>Add-ons</span></a>';
  catalogs.remove();
  paintMenuDot(lab({ catalogs: [catalog({ attention: "update" })] }));
  menu.append(catalogs);
  paintMenuDot(lab());
  assert.equal(dot.hidden, true);
});

test("a menu that shows its words says what the entry is for; one that shows icons only says its name, and the reason when the dot is lit", () => {
  const float = () => page.doc.querySelector("#tip-float");
  const hover = () => page.doc.dispatchEvent(new Ev("mouseover", { target: catalogs }));
  paintMenuDot(lab());
  hover();
  assert.equal(text(float()), "Where items come from");
  // The other entries of the menu say nothing while their words show: their tip is their name
  page.doc.dispatchEvent(new Ev("mouseout", { target: catalogs, relatedTarget: page.doc.body }));
  assert.ok(!float().classList.contains("shown"));
  page.doc.dispatchEvent(new Ev("mouseover", { target: addons }));
  assert.ok(!float().classList.contains("shown"), "a name that is written is not said twice");
  page.doc.dispatchEvent(new Ev("mouseout", { target: addons, relatedTarget: page.doc.body }));
  page.body.classList.add("nav-collapsed");
  try {
    hover();
    assert.equal(text(float()), "Catalogs");
    page.doc.dispatchEvent(new Ev("mouseout", { target: catalogs, relatedTarget: page.doc.body }));
    paintMenuDot(lab({ catalogs: [catalog({ attention: "repair" })] }));
    hover();
    assert.equal(text(float()), "Catalogs, needs repair");
    page.doc.dispatchEvent(new Ev("mouseover", { target: addons }));
    assert.equal(text(float()), "Add-ons", "the name of an entry whose tip is its name alone");
  } finally {
    page.body.classList.remove("nav-collapsed");
  }
  paintMenuDot(lab({ catalogs: [catalog({ attention: "repair" })] }));
  page.doc.dispatchEvent(new Ev("mouseout", { target: addons, relatedTarget: page.doc.body }));
  hover();
  assert.equal(text(float()), "Catalogs, needs repair", "lit: the reason, with the words showing too");
});

// --- The fixtures ---------------------------------------------------------------------------------------------------------------

test("the fixtures hold a state for every key of the table, so that a page is tried against each of them", () => {
  const keys = ["machine", "uptodate", "update", "checking", "updating", "downloading", "unreachable", "noaccess", "needs_git", "needs_docker", "wrong_catalog",
    "rewritten", "lab_new", "lab_old", "damaged", "linked", "missing_folder", "suggested"];
  assert.deepEqual([PERSONAL().state.key, ...Object.keys(STATES)].sort(), [...keys].sort());
  for (const [key, state] of Object.entries(STATES)) {
    assert.equal(state.key, key);
    assert.ok(state.label && ["muted", "warn", "error", "working"].includes(state.tone), `${key}: a word and a tone`);
    assert.deepEqual(Object.keys(state), Object.keys(STATE("x", "X")), `${key}: the shape of one`);
  }
  // The states that are not a plain one tell what happened and what to do, as the table of the Catalogs page words them
  for (const key of ["unreachable", "noaccess", "needs_git", "needs_docker", "wrong_catalog", "rewritten", "lab_new", "lab_old", "damaged", "missing_folder"]) {
    assert.ok(STATES[key].reason && STATES[key].next, `${key}: a reason and a next step`);
  }
});
