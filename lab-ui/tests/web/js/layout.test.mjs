// The notice "Lab updated" (web/js/dialogs/layout.js), run for real over a small DOM and a stub of what the server answers: nothing drawn when
// the server has nothing to say, the rows in their order and words, the phase that waits (one row, Settings, Not changed) and
// follows the state until the move is done, the phase that failed (the line, Try again), Close for good and Escape for this page load, and
// every name a person or a catalog chose as text. The routes it calls are the server's.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, flush, installDom, isOff, offWhy, text } from "./dom-stub.mjs";
import { HOSTILE, HOSTILE_QUOTED, LAYOUT, lab, noticeRow } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
{
  const set = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay = 0, ...rest) => set(fn, delay, ...rest);
}
const source = (file) => readFileSync(new URL(`../../../web/${file}`, import.meta.url), "utf8");
const ids = [...source("index.html").matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// What the server answers: the calls made, an answer for each route, a refusal for some
const server = { calls: [], answers: {}, refuse: {}, state: null };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path, options = {}) => {
    if (path === "/api/state") return reply(server.state);
    server.calls.push({ method: options.method || "GET", path, body: options.body ? JSON.parse(options.body) : undefined });
    if (server.refuse[path]) return reply({ error: server.refuse[path] }, 500);
    return reply(server.answers[path] ?? {});
  },
});
globalThis.innerHeight = 800;
const { matches } = El.prototype;
El.prototype.matches = function (selector) { return selector === ":focus-visible" ? false : matches.call(this, selector); };

const { store, router } = await import("../../../web/js/core.js");
await import("../../../web/js/components/ui.js");
const { LabUpdated } = await import("../../../web/js/dialogs/layout.js");

const dialog = () => page.doc.querySelectorAll("dialog").find((d) => d.querySelector("#layout-title"));
const isOpen = () => dialog().hasAttribute("open");
const title = () => text(dialog().querySelector("#layout-title"));
const rows = () => dialog().querySelectorAll(".choice-row").map((row) => [text(row.querySelector(".choice-row-label")), row.querySelectorAll(".pill").map(text), row.querySelector(".muted") ? text(row.querySelector(".muted")) : ""]);
const buttons = () => dialog().querySelectorAll(".dialog-actions button");
const labels = () => buttons().map((b) => text(b));
const line = () => (dialog().querySelector(".create-problem").hidden ? "" : text(dialog().querySelector(".create-problem")));
const calls = (path) => server.calls.filter((c) => c.path === path);
const toasts = () => page.doc.querySelectorAll(".toast").map(text);
const press = (button) => button.click();
const submit = async () => {
  dialog().querySelector("form").dispatchEvent(new Ev("submit"));
  await flush();
};

// The state: the lab with the layout the server says; every subscriber hears it, as after a poll
function say(layout, more = {}) {
  server.state = lab({ layout, ...more });
  store.state = server.state;
  store.notify();
}
// A new page load: the dialog, and what it remembers (the notice the person closed last), as left; the next test starts from nothing. The
// notice of a move has the time of the move for its id: each test says its own, and one that is closed here replaces the one before it.
let moves = 0;
const ready = (rows, more = {}) => LAYOUT.ready(rows, { id: `2026-10-17T10:00:0${++moves}Z`, ...more });
function reset() {
  say(ready([noticeRow("kept", ["x"])]));
  dialog().close();
  say(undefined);
  server.calls.length = 0;
  server.answers = {};
  server.refuse = {};
  page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
}

const MOVED = [noticeRow("personal", ["Scratch", "Probe"]), noticeRow("kept", ["Ledger"]), noticeRow("turned_off", ["Old demo"]), noticeRow("not_moved", ["README.md"])];

say(undefined);
LabUpdated.init();

// --- Nothing to say ----------------------------------------------------------------------------------------------------------------------

test("with nothing to say the page draws nothing: no layout, no notice, a notice whose rows are empty, a layout that is not what the contract says", () => {
  reset();
  for (const layout of [undefined, null, {}, { phase: "ready", notice: null }, { phase: "ready", notice: "text" }, { phase: "ready", notice: [] },
    ready([]), ready([{ key: "personal", label: "Now personal", items: [], hint: "x" }]),
    // The server's notice exists whenever an item moved, even with nothing for the person to do: `moved` alone is no row
    ready([], { moved: [{ kind: "addon", id: "echo" }] }),
    ready([{ key: "kept", label: "Kept aside", items: [null, 3, "", {}], hint: "" }]), ready("rows")]) {
    say(layout);
    assert.equal(isOpen(), false, JSON.stringify(layout));
  }
  say({ phase: "waiting", notice: { ...LAYOUT.waiting().notice, rows: [] } });
  assert.equal(isOpen(), false, "a move that waits has its row; without it there is nothing to say");
});

// --- The notice of a move that is done ---------------------------------------------------------------------------------------------------

test("a move that left something to say is one dialog: its title, a row for each thing in their words, one Close", () => {
  reset();
  say(ready(MOVED));
  assert.equal(isOpen(), true);
  assert.equal(title(), "Lab updated");
  assert.deepEqual(rows(), [["Now personal", ["Scratch", "Probe"], "Not in a catalog"], ["Kept aside", ["Ledger"], "Your changes, in .lab/migration/kept"],
    ["Turned off", ["Old demo"], "Needs a catalog"], ["Not moved", ["README.md"], "Files stay where they were"]]);
  assert.deepEqual(labels(), ["Close"]);
  assert.equal(line(), "", "no failure line");
  assert.equal(dialog().querySelector("[data-primary]").classList.contains("primary"), true, "Enter presses Close, which is harmless");
  assert.ok(page.doc.activeElement === dialog().querySelector("[data-primary]"), "and the keyboard is on it");
});

test("the row Restart needed, which the server computes from what runs, stands where the design puts it whichever place the server gives it", () => {
  reset();
  say(ready([...MOVED, noticeRow("restart", ["Echo", "Shop"])]));
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Kept aside", "Turned off", "Restart needed", "Not moved"]);
  assert.deepEqual(rows()[3], ["Restart needed", ["Echo", "Shop"], "Their folders moved"]);
  // Alone, it is a notice all the same: that is what the person must do
  reset();
  say(ready([noticeRow("restart", ["Echo"])]));
  assert.deepEqual(rows(), [["Restart needed", ["Echo"], "Their folders moved"]]);
  // A row the page does not know is drawn after the ones it does: nothing the server says is dropped
  reset();
  say(ready([{ key: "later", label: "Later", items: ["Thing"], hint: "" }, ...MOVED.slice(0, 1)]));
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Later"]);
});

test("more than four names fold under a line that counts them, as in every effects dialog", () => {
  reset();
  say(ready([noticeRow("personal", ["a", "b", "c", "d", "e", "f"])]));
  const fold = dialog().querySelector("details.choice-more");
  assert.ok(fold, "one line that counts them");
  assert.match(text(fold.querySelector("summary")), /Now personal\s+6 items\s*Not in a catalog/, "its hint is on the line that counts: it is not lost when the names fold");
  assert.equal(fold.querySelectorAll("li").length, 6);
});

test("Close sends the server the word, closes the dialog, and the state that still carries the notice does not draw it again", async () => {
  reset();
  const notice = ready(MOVED);
  say(notice);
  await submit();
  assert.deepEqual(calls("/api/layout/close").map((c) => [c.method, c.body]), [["POST", {}]]);
  assert.equal(isOpen(), false);
  say(notice);   // the poll that comes before the server has forgotten it
  assert.equal(isOpen(), false);
  say(ready([]));
  assert.equal(isOpen(), false);
  assert.deepEqual(toasts(), [], "closing a notice is not an event");
});

test("Close at work says so and waits; a second Enter sends nothing more", async () => {
  reset();
  say(ready(MOVED));
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    if (path !== "/api/layout/close") return original(path, options);
    server.calls.push({ path });
    return { ok: true, status: 200, headers: { get: () => "" }, json: async () => { await gate; return {}; } };
  };
  try {
    const first = submit();
    await flush();
    assert.deepEqual(labels(), ["Closing"]);
    assert.equal(isOff(buttons()[0]), true);
    assert.equal(offWhy(buttons()[0]), "Closing");
    await submit();
    assert.equal(calls("/api/layout/close").length, 1, "a second Enter is the same request");
    release();
    await first;
    await flush();
    assert.equal(isOpen(), false);
  } finally {
    globalThis.fetch = original;
  }
});

test("a refusal of Close is an error toast in the server's words, and the dialog stays open with its button back", async () => {
  reset();
  say(ready(MOVED));
  server.refuse["/api/layout/close"] = "Could not write .lab/layout.json";
  await submit();
  assert.deepEqual(toasts(), ["Could not write .lab/layout.json"]);
  assert.equal(isOpen(), true);
  assert.deepEqual(labels(), ["Close"]);
  assert.equal(isOff(buttons()[0]), false);
  assert.ok(page.doc.querySelector("#toasts").parent === dialog(), "the toast is in the dialog, above the page that is inert behind it");
  // It can be closed after all, and again
  delete server.refuse["/api/layout/close"];
  await submit();
  assert.equal(isOpen(), false);
});

test("Escape, or the router leaving the page, closes it for this page load only: the same notice does not open again, and the server is told nothing", async () => {
  reset();
  const notice = ready(MOVED);
  say(notice);
  dialog().close();   // Escape: the dialog's own close
  assert.equal(isOpen(), false);
  say(notice);
  say({ ...notice, notice: { ...notice.notice, rows: [...notice.notice.rows, noticeRow("restart", ["Echo"])] } });
  assert.equal(isOpen(), false, "not even for a row that came meanwhile: a person who closed it has read it");
  assert.equal(server.calls.length, 0);
  // A notice of another move is another notice
  const other = ready(MOVED);
  say(other);
  assert.equal(isOpen(), true);
  dialog().close();   // what the router does as the address changes
  say(other);
  assert.equal(isOpen(), false);
});

test("a dialog that is closed is closed even before its close event is heard: a state that comes first does not open it again", () => {
  // A browser fires `close` a task after `close()`, and a state can be drawn in between (seen: seconds in a pane that is not in front)
  reset();
  const notice = ready(MOVED);
  say(notice);
  assert.equal(isOpen(), true);
  dialog().removeAttribute("open");   // closed, its event not heard yet
  say(notice);
  assert.equal(isOpen(), false, "it was closed by the person: it stays closed");
  dialog().dispatchEvent(new Ev("close"));
  say(notice);
  assert.equal(isOpen(), false, "and the event that comes after says nothing new");
  // The same for a move that waits
  reset();
  say(LAYOUT.waiting());
  dialog().removeAttribute("open");
  say(LAYOUT.waiting());
  assert.equal(isOpen(), false);
});

test("the notice is drawn again as the state changes under it, in place: the dialog is not opened twice, nor its focus moved", () => {
  reset();
  say(ready(MOVED.slice(0, 1)));
  const button = dialog().querySelector("[data-primary]");
  assert.ok(page.doc.activeElement === button);
  say(ready([...MOVED.slice(0, 1), noticeRow("restart", ["Echo"])]));
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Restart needed"], "a row that came while it is open is there");
  assert.ok(dialog().querySelector("[data-primary]") === button, "the button is the same node: the keyboard stays where it was");
  assert.equal(page.doc.querySelectorAll("dialog").filter((d) => d.hasAttribute("open")).length, 1);
  // It goes when the server says there is nothing left to say, and that is not the person closing it
  say(ready([]));
  assert.equal(isOpen(), false);
  say(ready(MOVED.slice(0, 1)));
  assert.equal(isOpen(), true, "something to say again: it opens, it was not dismissed");
});

// --- A move that waits ---------------------------------------------------------------------------------------------------------------

test("a move that waits for the catalog is the dialog's single row, Settings, Not changed, with Close and nothing to ask of the server", async () => {
  reset();
  say(LAYOUT.waiting());
  assert.equal(isOpen(), true);
  assert.equal(title(), "Lab updated");
  assert.deepEqual(rows(), [["Settings", ["Not changed"], ""]]);
  assert.deepEqual(labels(), ["Close"]);
  await submit();
  assert.equal(isOpen(), false);
  assert.equal(server.calls.length, 0, "nothing is closed for good that is not moved");
});

test("a move that waits becomes the notice of what it moved in the same dialog, and goes without a word when it moved nothing the person needs to know", () => {
  reset();
  say(LAYOUT.waiting());
  const dlg = dialog();
  say(ready(MOVED));
  assert.equal(isOpen(), true);
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Kept aside", "Turned off", "Not moved"]);
  assert.ok(dialog() === dlg);
  reset();
  say(LAYOUT.waiting());
  say(ready([]));
  assert.equal(isOpen(), false, "done, with nothing to say");
});

test("a person who closed the waiting dialog is shown the notice of the move when it is done", () => {
  reset();
  say(LAYOUT.waiting());
  dialog().close();
  say(LAYOUT.waiting());
  assert.equal(isOpen(), false);
  say(ready(MOVED));
  assert.equal(isOpen(), true);
});

// --- A move that failed --------------------------------------------------------------------------------------------------------------

test("a move that failed says so in one line and offers Try again, which asks the server to make the move again; Close beside it asks nothing", async () => {
  reset();
  say(LAYOUT.failed());
  assert.equal(isOpen(), true);
  assert.equal(line(), "Could not move your settings. Nothing was deleted.");
  assert.deepEqual(rows(), []);
  assert.deepEqual(labels(), ["Close", "Try again"]);
  assert.ok(buttons()[1].classList.contains("primary"), "Try again is the one Enter presses");
  assert.ok(page.doc.activeElement === buttons()[1]);
  press(buttons()[0]);
  assert.equal(isOpen(), false);
  assert.equal(server.calls.length, 0);
  // Opened again by a state of the next kind
  say(LAYOUT.failed());
  assert.equal(isOpen(), false, "the same failure does not open it again");
});

test("Try again sends the retry, reads the state again, says Trying again meanwhile, and the notice follows the state to the move that is done", async () => {
  reset();
  say(LAYOUT.failed());
  server.answers["/api/layout/retry"] = LAYOUT.waiting();
  server.state = lab({ layout: ready(MOVED) });
  press(buttons()[1]);
  await submit();
  assert.deepEqual(calls("/api/layout/retry").map((c) => [c.method, c.body]), [["POST", {}]]);
  assert.equal(store.state.layout.phase, "ready", "the state was read again");
  assert.equal(isOpen(), true);
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Kept aside", "Turned off", "Not moved"]);
  assert.equal(line(), "", "the failure line is gone with the failure");
});

test("Try again at work reads Trying again and cannot be sent twice; a refusal is an error toast and the button comes back", async () => {
  reset();
  say(LAYOUT.failed());
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    if (path !== "/api/layout/retry") return original(path, options);
    server.calls.push({ path });
    await gate;
    return { ok: false, status: 500, statusText: "x", headers: { get: () => "" }, json: async () => ({ error: "The Lab could not look at the layout" }) };
  };
  try {
    const sent = submit();
    await flush();
    assert.deepEqual(labels(), ["Close", "Trying again"]);
    assert.equal(isOff(buttons()[1]), true);
    await submit();
    assert.equal(calls("/api/layout/retry").length, 1);
    release();
    await sent;
    await flush();
    assert.deepEqual(toasts(), ["The Lab could not look at the layout"]);
    assert.deepEqual(labels(), ["Close", "Try again"]);
    assert.equal(isOff(buttons()[1]), false);
  } finally {
    globalThis.fetch = original;
  }
});

// --- What the server really says ---------------------------------------------------------------------------------------------------------

// The three statuses as layout.status() writes them, and the row that restart_row makes for what runs (layout-status.json: test_static.py
// holds it equal to what the server says, so that a key or a word renamed there is found there)
const REAL = JSON.parse(readFileSync(new URL("./layout-status.json", import.meta.url), "utf8"));

test("the statuses the server writes are drawn: its title, its rows with their labels and hints, its line, and the row Restart needed it adds from what runs", () => {
  reset();
  const { ready } = REAL;
  say({ ...ready, notice: { ...ready.notice, id: "2026-10-17T11:00:00Z" } });
  assert.equal(title(), ready.notice.title);
  assert.deepEqual(rows(), ready.notice.rows.map((r) => [r.label, r.items, r.hint]));
  assert.deepEqual(labels(), [ready.notice.button]);
  // The state adds the row about what runs, from restart_row: it stands between the rows of the report
  reset();
  say({ ...ready, notice: { ...ready.notice, id: "2026-10-17T11:00:01Z", rows: [...ready.notice.rows, REAL.restart] } });
  assert.deepEqual(rows().map((r) => r[0]), ["Now personal", "Kept aside", "Turned off", "Restart needed", "Not moved"]);
  assert.deepEqual(rows()[3], [REAL.restart.label, REAL.restart.items, REAL.restart.hint]);
  reset();
  say(REAL.waiting);
  assert.deepEqual(rows(), REAL.waiting.notice.rows.map((r) => [r.label, r.items, r.hint]));
  assert.deepEqual(labels(), [REAL.waiting.notice.button]);
  reset();
  say(REAL.failed);
  assert.equal(line(), REAL.failed.notice.text);
  assert.deepEqual(labels(), ["Close", REAL.failed.notice.button]);
});

// --- What a server says is text ------------------------------------------------------------------------------------------------------------

test("a name, a label, a hint or a line that is markup is shown as the text it is", () => {
  reset();
  say(ready([{ key: "personal", label: HOSTILE, items: [HOSTILE, HOSTILE_QUOTED], hint: HOSTILE }], { title: HOSTILE }));
  const html = dialog().innerHTML;
  assert.equal(dialog().querySelectorAll("img").length, 0, "no element came out of a name");
  assert.equal(dialog().querySelectorAll("script").length, 0);
  assert.ok(!/<img|<script/.test(html), html);
  assert.equal(title(), HOSTILE);
  assert.deepEqual(rows(), [[HOSTILE, [HOSTILE, HOSTILE_QUOTED], HOSTILE]]);
  reset();
  say({ phase: "failed", notice: { ...LAYOUT.failed().notice, text: HOSTILE } });
  assert.equal(line(), HOSTILE);
  assert.equal(dialog().querySelectorAll("img").length, 0);
});

// --- The module as a file ---------------------------------------------------------------------------------------------------------------

test("the module names no title attribute, no host of the web, and calls only the routes of the layout", () => {
  const file = source("js/dialogs/layout.js");
  assert.ok(!/\btitle=/.test(file));
  assert.ok(!/github\.com/.test(file));
  assert.deepEqual([...file.matchAll(/"(\/api\/[^"]+)"/g)].map((m) => m[1]).sort(), ["/api/layout/close", "/api/layout/retry"]);
  // The shell starts it once the first state is read, after the welcome that it may open over
  const main = source("js/main.js");
  assert.ok(main.includes('import { LabUpdated } from "./dialogs/layout.js";'));
  assert.ok(main.indexOf("await store.refresh();") < main.indexOf("Welcome.init();") && main.indexOf("Welcome.init();") < main.indexOf("LabUpdated.init();"), "started once the state is read, after the welcome");
});
