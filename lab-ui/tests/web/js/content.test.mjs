// What the pages cannot say for themselves of the content of the catalogs (web/js/components/ui.js: contentStatus): which catalog a Try again is for,
// and that what the server says is text; the words of each key are in the tests of the pages that draw them. And the dot of the Catalogs entry of the
// menu (paintMenuDot): the names the entry takes, and the name it has while only its icon shows.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, installDom, text } from "./dom-stub.mjs";
import { FIRST, HOSTILE, HOSTILE_QUOTED, catalog, content, lab } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const page = installDom({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
globalThis.innerHeight = 800;
const { contentStatus, paintMenuDot } = await import("../../../web/js/components/ui.js");

const draw = (markup) => {
  const root = page.doc.createElement("div");
  root.innerHTML = markup;
  page.doc.body.append(root);
  return root;
};
const withContent = (key, more = {}, catalogs = []) => lab({ content: content(key, more), catalogs });
const block = (root) => root.firstElementChild;
const buttons = (root) => root.querySelectorAll("[data-content-act]").map((el) => [el.getAttribute("data-content-act"), el.getAttribute("data-catalog"), text(el)]);

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
