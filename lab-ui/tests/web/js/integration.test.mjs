// The seams between the pages that the Catalogs work left to the shell of the page (web/js/main.js, core.js, jobs.js, ui.js, combo.js,
// environment-actions.js, pages/stack.js): the code a refusal of the server carries, a key that only the click that holds it lets go, the
// timer of the menu, the bubble hosted in the dialog that is open, an effects dialog that starts with nothing chosen, the row that says where an
// item comes from in a plan, the picker that counts catalogs from two, the entries of `state.catalogs` that are no row, Add catalog leading to
// the Catalogs page, and Start all in the Cloud held while no add-on can run its gateway. Run for real over a small DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, flush, installDom, isOff, offWhy, text } from "./dom-stub.mjs";
import { FIRST, PERSONAL, catalog, content, lab } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids, fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
globalThis.innerHeight = 800;
// Two selectors the page asks and the small DOM does not know: a button at work (:is)
const { closest } = El.prototype;
El.prototype.closest = function (selector) { return closest.call(this, selector.replace(":is(.busy, .said)", ".busy")); };
// A radio is checked as its markup says, until a test chooses another
Object.defineProperty(El.prototype, "checked", {
  get() { return this._checked ?? this.hasAttribute("checked"); },
  set(value) { this._checked = !!value; },
  configurable: true,
});

const { api, store, router, catalogRows } = await import("../../../web/js/core.js");
const ui = await import("../../../web/js/components/ui.js");
const { busy } = await import("../../../web/js/activity/jobs.js");
const { CategoryFilter } = await import("../../../web/js/components/combo.js");
const { perform } = await import("../../../web/js/platform/environment-actions.js");
const { platformHead, paintHead } = await import("../../../web/js/pages/stack.js");
const { catalogsPage } = await import("../../../web/js/pages/catalogs.js");

const settle = async () => {
  await flush();
  mock.timers.tick(600);
  await flush();
};

// --- The code of a refusal -----------------------------------------------------------------------------------------------------------

test("a refusal of the server carries its code with its line, and nothing when the answer has none or says something else", async () => {
  const refuse = (status, body) => {
    globalThis.fetch = async () => ({ ok: false, status, statusText: "Refused", json: async () => { if (body instanceof Error) throw body; return body; } });
    return api("/api/catalogs/acme-hub/update", { ticket: "t" }).then(() => null, (err) => [err.message, err.code]);
  };
  assert.deepEqual(await refuse(409, { error: "Turn off Acme Bakery first", code: "held" }), ["Turn off Acme Bakery first", "held"]);
  assert.deepEqual(await refuse(410, { error: "This preview expired. Check the address again.", code: "ticket_expired" }),
    ["This preview expired. Check the address again.", "ticket_expired"], "the page branches on the code, not on the words");
  assert.deepEqual(await refuse(400, { error: "A line" }), ["A line", undefined], "no code in the answer, none on the error");
  assert.deepEqual(await refuse(400, { error: "A line", code: { not: "a word" } }), ["A line", undefined], "a code that is no word is none");
  assert.deepEqual(await refuse(500, {}), ["500 Refused", undefined], "an answer with no line says the status");
  assert.deepEqual(await refuse(502, null), ["502 Refused", undefined], "an answer that is JSON null is no answer");
  assert.deepEqual(await refuse(502, new Error("not JSON")), ["502 Refused", undefined]);
  // A request that goes well is untouched
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ catalog: { id: "acme-hub" } }) });
  assert.deepEqual(await api("/api/catalogs", { ticket: "t" }), { catalog: { id: "acme-hub" } });
  // Offline is its own line
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(api("/api/state"), { message: "Lab UI offline" });
});

// --- A key is let go by the click that holds it ------------------------------------------------------------------------------------------

test("a click that ends first does not let go of the key that a later click on it waits for: the last holder frees it", async () => {
  store.pending.clear();
  const key = "catalog-op:acme-hub";
  let first, second;
  busy(key, "update", new Promise((resolve) => { first = resolve; }));
  busy(key, "update", new Promise((resolve) => { second = resolve; }));
  assert.equal(store.pending.get(key), "update");
  first();
  await settle();
  assert.equal(store.pending.get(key), "update", "the second click still waits for its own answer");
  second();
  await settle();
  assert.equal(store.pending.has(key), false, "and frees it when it has it");
});

test("the holder is the last click, whatever the order they end in; a click alone frees its key, and a refusal does too", async () => {
  store.pending.clear();
  const key = "catalog-op:acme-hub";
  let first, second;
  busy(key, "update", new Promise((resolve) => { first = resolve; }));
  busy(key, "update", new Promise((resolve) => { second = resolve; }));
  second();
  await settle();
  assert.equal(store.pending.has(key), false, "the last click ended: the key is free");
  first();
  await settle();
  assert.equal(store.pending.has(key), false, "the click that ended after it frees nothing more");
  // Another verb on the key replaces the first: the first's end leaves it
  let slow;
  busy(key, "check", new Promise((resolve) => { slow = resolve; }));
  busy(key, "update", new Promise(() => {}));
  slow();
  await settle();
  assert.equal(store.pending.get(key), "update");
  store.pending.clear();
  // Alone
  await busy("catalog-op:alone", "check", Promise.resolve());
  await settle();
  assert.equal(store.pending.has("catalog-op:alone"), false);
  await busy("catalog-op:refused", "check", Promise.reject(new Error("no"))).catch(() => {});
  await settle();
  assert.equal(store.pending.has("catalog-op:refused"), false);
});

// --- The timer of the menu ------------------------------------------------------------------------------------------------------------

test("a menu whose button is drawn away closes once the state is drawn, on a timer with the delay a browser reads", () => {
  store.state = lab();
  const delays = [], set = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay, ...rest) => { delays.push(delay); return set(fn, delay, ...rest); };
  try {
    const button = page.doc.createElement("button");
    page.doc.body.append(button);
    ui.openMenu(button, [{ label: "Check again" }]);
    const menu = page.ids.menu;
    assert.equal(menu.hidden, false);
    store.notify();
    assert.equal(delays.at(-1), 0, "a delay, so that a clock that is not the browser's reads a time");
    mock.timers.tick(0);
    assert.equal(menu.hidden, false, "its button is still there");
    button.remove();
    store.notify();
    mock.timers.tick(0);
    assert.equal(menu.hidden, true, "its button is gone: the menu goes");
  } finally {
    globalThis.setTimeout = set;
  }
});

// --- The bubble in the dialog ---------------------------------------------------------------------------------------------------------

test("the pointer over what has no tooltip hides the bubble: the control that had one may have gone from under it", () => {
  const float = () => page.doc.querySelector("#tip-float");
  const stop = page.doc.createElement("button"), start = page.doc.createElement("button");
  stop.setAttribute("data-tip", "Stopping");
  page.doc.body.append(stop, start);
  page.doc.dispatchEvent(new Ev("mouseover", { target: stop }));
  assert.ok(float().classList.contains("shown"));
  stop.hidden = true;   // Stop all is done: Start all takes its place under the pointer
  page.doc.dispatchEvent(new Ev("mouseover", { target: start }));
  assert.ok(!float().classList.contains("shown"));
  stop.remove();
  start.remove();
});

test("the bubble of a control whose reason changes while it shows says the new one", () => {
  const float = () => page.doc.querySelector("#tip-float");
  const next = page.doc.createElement("button");
  page.doc.body.append(next);
  ui.setOff(next, true, "Checking your machine");
  page.doc.dispatchEvent(new Ev("mouseover", { target: next }));
  assert.equal(text(float()), "Checking your machine");
  ui.setOff(next, true, "Fix the lines in red first");
  assert.equal(text(float()), "Fix the lines in red first");
  ui.setOff(next, false);
  assert.ok(!float().classList.contains("shown"), "usable, with no tooltip of its own: none");
  next.remove();
});

test("the bubble of a control that cannot be used is hosted in the dialog that is open, so that it shows above it, and goes back to the page when it closes", () => {
  const float = () => page.doc.querySelector("#tip-float");
  const outside = page.doc.createElement("button");
  outside.setAttribute("data-tip", "Outside");
  page.doc.body.append(outside);
  const dlg = page.doc.createElement("dialog");
  const inside = page.doc.createElement("button");
  inside.setAttribute("aria-disabled", "true");
  inside.setAttribute("data-tip", "Choose where to share");
  dlg.append(inside);
  page.doc.body.append(dlg);
  outside.focus();
  assert.ok(float().parentElement === page.body, "no dialog open: the page");
  assert.equal(text(float()), "Outside");
  dlg.showModal();
  inside.focus();
  assert.ok(float().parentElement === dlg, "a dialog is open: in it, in the top layer with it");
  assert.equal(text(float()), "Choose where to share");
  assert.ok(float().classList.contains("shown"));
  assert.equal(page.doc.querySelectorAll("#tip-float").length, 1, "one bubble for the page");
  // The last of several open dialogs hosts it
  const upper = page.doc.createElement("dialog");
  const deeper = page.doc.createElement("button");
  deeper.setAttribute("data-tip", "Deeper");
  upper.append(deeper);
  page.doc.body.append(upper);
  upper.showModal();
  deeper.focus();
  assert.ok(float().parentElement === upper, "the last dialog that is open");
  // A dialog that closes with the bubble in it takes it away: back to the page, hidden
  upper.close();
  page.doc.dispatchEvent(new Ev("close", { target: upper }));
  assert.ok(float().parentElement === page.body);
  assert.ok(!float().classList.contains("shown"));
  // ... but not the bubble of a control the focus went back to as it closed
  dlg.close();
  outside.focus();
  page.doc.dispatchEvent(new Ev("close", { target: dlg }));
  assert.ok(float().parentElement === page.body);
  assert.ok(float().classList.contains("shown"), "the bubble of the control that has the focus now stays");
  page.doc.dispatchEvent(new Ev("focusout", { target: outside }));
  dlg.remove();
  upper.remove();
  outside.remove();
});

test("the bubble is hosted in the dialog of the control that asks for it, not in the last one of the document: a dialog that comes first in it can be the one opened last", () => {
  const float = () => page.doc.querySelector("#tip-float");
  const made = (why) => {
    const dlg = page.doc.createElement("dialog"), button = page.doc.createElement("button");
    button.setAttribute("data-tip", why);
    dlg.append(button);
    page.doc.body.append(dlg);
    return { dlg, button };
  };
  const early = made("Seen over the later one"), late = made("Seen in the later one");
  late.dlg.showModal();
  early.dlg.showModal();   // opened last: on top of the one that comes after it in the document
  early.button.focus();
  assert.ok(float().parentElement === early.dlg, "in the dialog of its control, which is the one on top");
  assert.equal(text(float()), "Seen over the later one");
  assert.ok(float().classList.contains("shown"));
  // A control of the other dialog (it can be reached again once the top one closes) is served in its own
  early.dlg.close();
  page.doc.dispatchEvent(new Ev("close", { target: early.dlg }));
  assert.ok(float().parentElement === page.body, "the dialog that closes takes the bubble away");
  late.button.focus();
  assert.ok(float().parentElement === late.dlg);
  assert.equal(text(float()), "Seen in the later one");
  // A control that is in no dialog, while one is open, still gets the last one that is open
  const outside = page.doc.createElement("button");
  outside.setAttribute("data-tip", "Outside");
  page.doc.body.append(outside);
  outside.focus();
  assert.ok(float().parentElement === late.dlg, "no dialog of its own: the last open one");
  late.dlg.close();
  page.doc.dispatchEvent(new Ev("close", { target: late.dlg }));
  assert.ok(float().parentElement === page.body);
  page.doc.dispatchEvent(new Ev("focusout", { target: outside }));
  for (const node of [early.dlg, late.dlg, outside]) node.remove();
});

// --- The toasts in the dialog on top -----------------------------------------------------------------------------------------------------

test("the toasts are hosted in the dialog that was opened last, not the one that comes last in the document: a confirmation of index.html opened over a dialog made later", () => {
  const host = () => page.doc.querySelector("#toasts").parent;
  const closed = (dlg) => { dlg.close(); page.doc.dispatchEvent(new Ev("close", { target: dlg })); };
  // A dialog the script made, appended after the ones of index.html, and opened first
  const later = page.doc.createElement("dialog");
  page.doc.body.append(later);
  ui.toast("No dialog");
  assert.ok(host() === page.body, "no dialog open: the page");
  later.showModal();
  ui.toast("Over the later dialog");
  assert.ok(host() === later, "one dialog open: in it");
  // The confirmation of index.html comes before it in the document and is opened over it: it is on top in the top layer
  const confirm = page.ids.confirm;
  const asked = ui.confirmDialog({ title: "Discard changes", action: "Discard", danger: true });
  page.body.hidden = false;   // the small DOM puts every id in the body, and the dialog hides the parent of its field (the word to type), which here is the body
  assert.ok(page.doc.body.children.indexOf(confirm) < page.doc.body.children.indexOf(later), "it comes first in the document");
  ui.toast("Over the confirmation");
  assert.ok(host() === confirm, "the toast shows above the dialog that is on top, and can be reached");
  assert.deepEqual(page.doc.querySelectorAll(".toast").map(text).slice(-1), ["Over the confirmation"]);
  // It closes: the dialog under it is the one on top again
  confirm.returnValue = "";
  closed(confirm);
  confirm.onclose();
  assert.ok(host() === later, "back in the dialog that was under it");
  ui.toast("Back over the later dialog");
  assert.ok(host() === later);
  closed(later);
  assert.ok(host() === page.body, "no dialog left: the page");
  return asked.then((answer) => assert.equal(answer, false));
});

test("a toast made just before its dialog closes is not left in it: the toasts leave as close() is called, not when the browser says the dialog closed", () => {
  // The close event comes a task later (seconds, in a pane that is not in front): `toast("Updated")` then `dialog.close()` must show the toast
  const host = () => page.doc.querySelector("#toasts").parent;
  const dlg = page.doc.createElement("dialog");
  page.doc.body.append(dlg);
  dlg.showModal();
  ui.toast("Updated");
  assert.ok(host() === dlg);
  dlg.close();   // no event is dispatched to the document here
  assert.ok(host() === page.body, "back in the page at once, where it shows");
  // The bubble that a control of the dialog showed goes with it, hidden, as the toasts do
  const float = () => page.doc.querySelector("#tip-float");
  const inside = page.doc.createElement("button");
  inside.setAttribute("data-tip", "Why");
  dlg.append(inside);
  dlg.showModal();
  inside.focus();
  assert.ok(float().parentElement === dlg && float().classList.contains("shown"));
  dlg.close();
  assert.ok(float().parentElement === page.body && !float().classList.contains("shown"), "back in the page at once, and hidden");
  page.doc.dispatchEvent(new Ev("focusout", { target: inside }));
  inside.remove();
  // Under another dialog that stays open it goes to that one
  const under = page.doc.createElement("dialog");
  page.doc.body.append(under);
  under.showModal();
  dlg.showModal();
  ui.toast("x");
  assert.ok(host() === dlg);
  dlg.close();
  assert.ok(host() === under, "the dialog that is on top now");
  under.close();
  assert.ok(host() === page.body);
  dlg.remove();
  under.remove();
  page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
});

test("a dialog opened again goes to the top; one that is open already keeps its place; a dialog that was never shown by showModal is found by the document", () => {
  const host = () => page.doc.querySelector("#toasts").parent;
  const closed = (dlg) => { dlg.close(); page.doc.dispatchEvent(new Ev("close", { target: dlg })); };
  const [a, b, c] = [1, 2, 3].map(() => { const dlg = page.doc.createElement("dialog"); page.doc.body.append(dlg); return dlg; });
  a.showModal();
  b.showModal();
  ui.toast("x");
  assert.ok(host() === b);
  // Opened over again, an open one is left where it was
  a.showModal();
  ui.toast("y");
  assert.ok(host() === b, "a call on a dialog that is open does not move it in the top layer");
  closed(b);
  ui.toast("z");
  assert.ok(host() === a);
  b.showModal();
  ui.toast("w");
  assert.ok(host() === b, "closed and opened again: on top");
  // Open by its attribute, as the page never does: the last of the document stands in when none opened by showModal is open
  closed(a);
  closed(b);
  c.open = true;
  ui.toast("v");
  assert.ok(host() === c);
  closed(c);
  for (const dlg of [a, b, c]) dlg.remove();
  page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
});

// --- An effects dialog that starts with nothing chosen ----------------------------------------------------------------------------------

const choice = page.ids.choice, picks = page.ids["choice-list"], ok = page.ids["choice-ok"];
const radios = () => picks.querySelectorAll("input");
picks.querySelector = (selector) => (selector === "input:checked" ? radios().find((radio) => radio.checked) : El.prototype.querySelector.call(picks, selector));
const pick = (index) => {
  radios().forEach((radio, i) => { radio.checked = i === index; });
  picks.onchange();
};
const enter = () => {
  const key = new Ev("keydown", { key: "Enter" });
  picks.onkeydown(key);
  return key.defaultPrevented;
};
const answer = async (confirmed) => {
  choice.returnValue = confirmed ? "ok" : "";
  choice.onclose();
  await flush();
};
const OPTIONS = [{ value: "gravitee", label: "Gravitee public", sub: "Public" }, { value: "acme-hub", label: "Acme Hub", sub: "Private" }];
const share = (options = OPTIONS, more = {}) => ui.effectsDialog({ title: "Share Acme Bakery", options, action: () => "Share", unchosen: "Choose where to share", ...more });

test("a choice that must be made starts with nothing chosen: its button waits and says why, Enter does nothing, and an option that is chosen frees it", async () => {
  const asked = share();
  assert.deepEqual(radios().map((radio) => radio.checked), [false, false], "none is chosen, and none is the first by default");
  assert.equal(isOff(ok), true);
  assert.equal(offWhy(ok), "Choose where to share");
  assert.equal(text(ok), "Share");
  assert.equal(enter(), true, "Enter on a choice does nothing while there is none");
  pick(1);
  assert.equal(isOff(ok), false);
  assert.equal(enter(), false, "chosen, Enter confirms");
  await answer(true);
  assert.equal(await asked, "acme-hub");
  assert.equal(isOff(ok), false, "the button is the other dialogs' too");
});

test("Cancel while nothing is chosen answers null, and so does a forged confirmation: nothing is shared by a key", async () => {
  let asked = share();
  await answer(false);
  assert.equal(await asked, null);
  assert.equal(isOff(ok), false);
  asked = share();
  await answer(true);   // the button cannot be pressed; a confirmation that comes all the same chooses nothing
  assert.equal(await asked, null);
  assert.equal(isOff(ok), false);
});

test("an option that cannot be taken waits with its reason, and a choice among options none of which can be taken says the first one's", async () => {
  const blocked = (value, reason) => ({ value, label: value, disabled: true, reason });
  // A single destination that cannot take the item is no choice: it is shown, and the button says why
  let asked = share([blocked("only", "You cannot push to Acme Hub. Ask for access.")]);
  assert.deepEqual(radios().map((radio) => radio.checked), [true]);
  assert.equal(offWhy(ok), "You cannot push to Acme Hub. Ask for access.");
  await answer(false);
  await asked;
  // Several, none of which can: nothing to choose
  asked = share([blocked("a", "You cannot push to A."), blocked("b", "B does not take contributions from the Lab.")]);
  assert.deepEqual(radios().map((radio) => radio.checked), [false, false]);
  assert.equal(offWhy(ok), "You cannot push to A.");
  await answer(false);
  await asked;
  // Several, one of which can: the others are not chosen, the button waits for the one that can
  asked = share([blocked("a", "You cannot push to A."), { value: "b", label: "B" }]);
  assert.equal(offWhy(ok), "Choose where to share");
  pick(1);
  assert.equal(isOff(ok), false);
  await answer(true);
  assert.equal(await asked, "b");
  // A single destination that can be taken is chosen, and the button is free
  asked = share([{ value: "only", label: "Only" }]);
  assert.deepEqual(radios().map((radio) => radio.checked), [true]);
  assert.equal(isOff(ok), false);
  await answer(true);
  assert.equal(await asked, "only");
});

test("a dialog without that parameter is what it was: the first option chosen, its button free, whatever the one before it did", async () => {
  const waiting = share();
  assert.equal(isOff(ok), true);
  await answer(false);
  await waiting;
  const asked = ui.effectsDialog({ title: "Remove", options: OPTIONS, action: (value) => `Do ${value}` });
  assert.deepEqual(radios().map((radio) => radio.checked), [true, false]);
  assert.equal(isOff(ok), false);
  assert.equal(text(ok), "Do gravitee");
  assert.equal(enter(), false);
  pick(1);
  assert.equal(text(ok), "Do acme-hub");
  await answer(true);
  assert.equal(await asked, "acme-hub");
  // Enter on a dangerous choice does nothing, as it did
  const danger = ui.effectsDialog({ title: "Remove", options: OPTIONS, action: () => "Remove", danger: () => true });
  assert.equal(enter(), true);
  await answer(false);
  await danger;
  // ... and a confirmation with no value is true
  const plain = ui.confirmEffects("Apply", [{ label: "Restarts", items: ["Gateway"] }]);
  await answer(true);
  assert.equal(await plain, true);
});

// --- Where an item comes from, in a plan -----------------------------------------------------------------------------------------------

test("a plan says where its item comes from when the server says it: one row among those that say what it leaves, the catalog's name, its version as the hint", async () => {
  const plan = { starts: [{ kind: "addon", id: "kafka", name: "Kafka" }], from: [{ name: "Acme Hub" }], hints: { from: "Version 1.4.0" },
    keeps: [{ kind: "addon", id: "redis", name: "Redis" }], deploys_to: [{ kind: "environment", id: "machine", name: "This machine" }] };
  const rows = ui.planRows(plan);
  assert.deepEqual(rows.map((row) => row.label), ["Stops", "Turns off", "Starts", "Changes versions", "Goes back", "Restarts", "Loses its license", "Deploys to", "From", "Keeps", "Serves", "Stops serving", "Not started"]);
  assert.deepEqual(ui.accepted({ stops: [{ kind: "addon", id: "a" }], versions: [{ kind: "version", id: "APIM" }] }), ["addon:a", "version:APIM"],
    "a version it changes is sent back with what it stops: the server changes nothing the page did not show");
  const from = rows.find((row) => row.label === "From");
  assert.deepEqual([from.items.map((i) => i.name), from.hint, from.note], [["Acme Hub"], "Version 1.4.0", true], "a row that says what the plan leaves as it is");
  assert.deepEqual(ui.planRows({ starts: plan.starts }).find((row) => row.label === "From").items, [], "no catalog said: no row");
  assert.deepEqual(ui.planRows({ from: [{ name: "Acme Hub" }] }).find((row) => row.label === "From").hint, "", "no hint said: none");
  // As the dialog draws it: a pill and a muted hint, among the rows that say what stays
  const asked = ui.confirmPlan("Start Kafka", "Start", plan);
  const notes = picks.querySelectorAll(".choice-row").map((row) => [text(row.querySelector(".choice-row-label")), row.querySelectorAll(".pill").map(text), row.querySelectorAll(".muted").map(text)]);
  assert.deepEqual(notes, [["From", ["Acme Hub"], ["Version 1.4.0"]], ["Keeps", ["Redis"], []]]);
  await answer(true);
  assert.deepEqual(await asked, []);
  // With no dialog there is nothing to say first
  assert.deepEqual(await ui.confirmPlan("Start Kafka", "Start", null), []);
});

// --- The picker counts catalogs from two ---------------------------------------------------------------------------------------------------

test("the filter names as many values as its label lets it, two as it always did, and counts the rest", () => {
  const button = page.doc.createElement("button");
  button.innerHTML = '<span class="value"></span>';
  const items = [{ category: "A" }, { category: "B" }, { category: "C" }];
  const said = (filter, values) => { filter.chosen = values; return text(button.querySelector(".value")); };
  const plain = CategoryFilter(button, () => items, () => {});
  assert.deepEqual([[], ["A"], ["A", "B"], ["A", "B", "C"]].map((values) => said(plain, values)), ["All categories", "A", "A, B", "3 categories"]);
  const few = CategoryFilter(button, () => items, () => {}, undefined, { label: { none: "All catalogs", all: "All catalogs", many: (n) => `${n} catalogs`, few: 1 } });
  assert.deepEqual([[], ["A"], ["A", "B"], ["A", "B", "C"]].map((values) => said(few, values)), ["All catalogs", "A", "2 catalogs", "3 catalogs"]);
  // A label that does not say keeps two
  const old = CategoryFilter(button, () => items, () => {}, undefined, { label: { none: "All", all: "All", many: (n) => `${n} kinds` } });
  assert.deepEqual([["A", "B"], ["A", "B", "C"]].map((values) => said(old, values)), ["A, B", "3 kinds"]);
});

// --- Entries of the catalogs that are no row ----------------------------------------------------------------------------------------------

const CHECKING = { id: "check", kind: "progress", progress: 0.4 };

test("the progress of the fetch of an Add rides among the catalogs and is no row: the helper that lists them leaves it out", () => {
  const rows = [FIRST(), catalog(), PERSONAL(), { id: "gravitee", kind: "suggested", listed: true }, CHECKING, { id: "odd" }, null, { id: "x", kind: "toString" }];
  assert.deepEqual(catalogRows(lab({ catalogs: rows })).map((row) => row.kind), ["git", "git", "machine", "suggested"]);
  assert.deepEqual(catalogRows(lab({ catalogs: "no" })), [], "a state that does not say");
  assert.deepEqual(catalogRows(null), []);
  // The bar of the Add dialog and the clock of the page do read it
  store.state = lab({ catalogs: [FIRST(), CHECKING] });
  assert.ok(store.unit("catalog:check") === CHECKING);
  assert.equal(store.progress("catalog:check"), 0.4);
  assert.equal(store.measuring(), true, "the page looks again every second while the fetch runs");
  store.state = lab({ catalogs: [FIRST(), { ...CHECKING, progress: null }] });
  assert.equal(store.measuring(), false);
});

test("what the content says to retry, and the dot of the menu, never take the progress of a fetch for a catalog", () => {
  const failed = (catalogs) => lab({ content: content("failed"), catalogs });
  const retry = (state) => ui.contentStatus(state).match(/data-catalog="([^"]*)"/)?.[1];
  const added = [FIRST({ commit: "" }), catalog({ commit: "9f1c2ab" })];
  assert.equal(retry(failed(added)), "gravitee");
  assert.equal(ui.contentStatus(failed([...added, CHECKING])), ui.contentStatus(failed(added)), "the same block with it as without");
  assert.equal(ui.contentStatus(failed([CHECKING, ...added])), ui.contentStatus(failed(added)), "wherever it stands");
  assert.equal(retry(failed([CHECKING])), "", "alone, it is no catalog to try again");
  assert.equal(ui.contentLead(failed([CHECKING, ...added])).extra, ui.contentLead(failed(added)).extra);
  const entry = page.doc.createElement("a");
  entry.setAttribute("class", "nav-item");
  entry.setAttribute("data-nav", "catalogs");
  entry.setAttribute("aria-label", "Catalogs");
  entry.innerHTML = '<span>Catalogs</span><i class="nav-dot" hidden></i>';
  page.doc.body.append(entry);
  ui.paintMenuDot(lab({ catalogs: [FIRST(), { ...CHECKING, attention: "update" }] }));
  assert.equal(entry.querySelector(".nav-dot").hidden, true, "no row asks for attention");
  ui.paintMenuDot(lab({ catalogs: [FIRST(), { ...CHECKING, attention: "update" }, catalog({ attention: "repair" })] }));
  assert.equal(entry.getAttribute("aria-label"), "Catalogs, needs repair", "the first row that does, not the entry that is none");
  entry.remove();
});

// --- Add catalog leads to the Catalogs page ------------------------------------------------------------------------------------------------

test("the action of an environment that no add-on can serve leads to the Catalogs page and asks nothing of the platform", () => {
  location.hash = "#/dashboard";
  const calls = [];
  globalThis.fetch = async (path) => { calls.push(path); return { ok: true, status: 200, json: async () => ({}) }; };
  const environment = { id: "env-development", name: "Development" };
  const found = { state: "ok", environments: [environment], gateways: [] };
  perform({ id: "cloud-eu", kind: "cloud", name: "Gravitee Cloud" }, () => found, environment, "add-catalog", () => calls.push("reread"));
  assert.equal(location.hash, "#/catalogs");
  assert.deepEqual(calls, []);
});

test("#/catalogs and #/catalogs/<ns> are the Catalogs page of the router, which shows its section and gives it the namespace", () => {
  store.state = lab({ catalogs: [FIRST(), catalog()] });
  router.register(catalogsPage);
  router.start();
  location.hash = "#/catalogs";
  assert.ok(router.current.page === catalogsPage);
  assert.deepEqual(router.current.params, [undefined]);
  assert.equal(page.ids["page-catalogs"].hidden, false);
  location.hash = "#/catalogs/acme-hub";
  assert.ok(router.current.page === catalogsPage);
  assert.deepEqual(router.current.params, ["acme-hub"]);
  assert.equal(page.doc.title, "Catalogs - Gravitee Lab");
  catalogsPage.hide();
});

// --- Start all in the Cloud ----------------------------------------------------------------------------------------------------------------

const CLOUD = { place: "cloud-eu/env-development", chosen: true, name: "Development (Gravitee Cloud)", group: { id: "api-management", name: "API Management" },
  start: { needed: true, enabled: true, reason: null }, stop: { needed: false, enabled: true, reason: null } };
const idle = { needed: false, enabled: true, reason: null };
const cloud = (more = {}, gateways = { cloud: "hybrid" }, core = CLOUD) => {
  const state = lab({ ...more });
  return { ...state, core, platform: { ...state.platform, state: { key: "stopped", label: "Stopped" }, gateways, start: { ...idle, needed: true }, apply: idle, restart: idle, update: idle } };
};
// The Start all of the Dashboard's head for a state: whether it is shown, and whether it can be used and what it says then
const head = (state) => {
  store.state = state;
  const root = page.doc.createElement("div");
  root.innerHTML = platformHead("Dashboard", { lean: true });
  page.doc.body.append(root);
  paintHead(root, state);
  const start = root.querySelector('[data-stack="start"]');
  const seen = { shown: !start.hidden, off: isOff(start), why: isOff(start) ? offWhy(start) : "" };
  root.remove();
  return seen;
};

test("with API Management in the Cloud, Start all waits while no add-on can run its gateway, and says why in the words of the lists", () => {
  assert.deepEqual(head(cloud()), { shown: true, off: false, why: "" }, "an add-on runs it: Start all, as it was");
  const none = { cloud: null };
  assert.deepEqual(head(cloud({}, none)), { shown: true, off: true, why: "Needs a gateway add-on" });
  assert.equal(head(cloud({ content: content("downloading") }, none)).why, "Downloading playgrounds, setups and add-ons");
  assert.equal(head(cloud({ content: content("failed") }, none)).why, content("failed").line);
  assert.equal(head(cloud({ content: content("none") }, none)).why, "No catalog added");
  // A provider that exists wins over what the catalogs are doing
  assert.deepEqual(head(cloud({ content: content("downloading") })), { shown: true, off: false, why: "" });
  // The reason of the core is still said when an add-on can run it
  const stuck = { ...CLOUD, start: { needed: true, enabled: false, reason: "Set up a gateway in Development first" } };
  assert.deepEqual(head(cloud({}, { cloud: "hybrid" }, stuck)), { shown: true, off: true, why: "Set up a gateway in Development first" });
  // ... and the catalogs' reason comes first, since it is the cause
  assert.equal(head(cloud({}, none, stuck)).why, "Needs a gateway add-on");
  // This machine's Start all is never held by it, and a state that does not say what runs a gateway holds nothing
  assert.deepEqual(head(cloud({}, none, { ...CLOUD, place: "machine" })), { shown: true, off: false, why: "" });
  const old = cloud();
  delete old.platform.gateways;
  assert.deepEqual(head(old), { shown: true, off: false, why: "" });
  // Held, then free once an add-on can: the same button
  store.state = lab();
});

// --- The sections of the page and the pages of the router -----------------------------------------------------------------------------

test("every section of the page has a menu entry, and the Catalogs page is among the pages that the router has", () => {
  const sections = [...html.matchAll(/<section class="page[^"]*" id="page-([\w-]+)"/g)].map((found) => found[1]);
  assert.ok(sections.includes("catalogs"));
  const entries = [...html.matchAll(/data-nav="([\w-]+)"/g)].map((found) => found[1]);
  assert.ok(entries.includes("catalogs"), "the entry of the menu, last in its group");
  assert.equal(catalogsPage.id, "catalogs");
  assert.equal(catalogsPage.nav, "catalogs");
});
