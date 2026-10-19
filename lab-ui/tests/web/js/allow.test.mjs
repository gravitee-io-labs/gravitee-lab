// The allowance of the items that use this machine (the items the policy holds back), walked from end to end over a server that remembers what it
// was told, with the state it sends in the shapes of the contract: a catalog that is not listed holds a Kubernetes add-on that
// runs privileged. The Add dialog offers the choice once the preview lists the item in `rows.uses_machine`, Keep them blocked first; Allow adds the
// catalog and then gives the allowance; the Catalogs page and the add-ons list follow the state (Blocked with its reason, then free to start,
// then Blocked again); stopping the allowance is held while the item is on, by the page and by the server; allowing is the one consent that
// Enter never gives. A listed catalog has the allowance by being listed, and none of this is drawn for it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, isOff, offWhy, text } from "./dom-stub.mjs";
import { FIRST, catalog, content, gains, item, lab, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
{
  const set = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay = 0, ...rest) => set(fn, delay, ...rest);
}
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// --- The server: a catalog that is not listed, with one item that runs privileged ---------------------------------------------------------------

const REASON = "Runs with full privileges";
const USES = [{ id: "@acme-hub/k3s", name: "Kubernetes", service: "k3s", what: ["runs privileged"], why: "Runs containerd and the kubelet" }];
const world = { added: false, host: "no", on: false, calls: [], refuse: {}, previewed: 0 };
const allowed = () => world.host === "allowed";

// What a preview says of a catalog that is not listed: it is judged as if allowed, so the item is not Blocked, and `rows.uses_machine` lists it
const preview = () => ({ ticket: "9f3a4c1d2e5b6a70", expires: 600, catalog: { id: "acme-hub", name: "Acme Hub", description: "Banking demos", version: "1.4.0", lab: 1, kind: "git" },
  address: "github.com/acme/lab-hub", url: "https://github.com/acme/lab-hub.git", audience: "private", counts: { playground: 0, setup: 0, addon: 1 },
  rows: { builds: { local: 0, remote: [] }, ports: [], core: [], reads: [], uses_machine: USES, needs: [] }, blocked: [], ignored: 0 });
// The row of the catalog once added: its allowance (`host`), the items it holds that use this machine, and, until it is allowed, the ones that cannot run
const row = () => catalog({ id: "acme-hub", host: world.host, counts: { playground: 0, setup: 0, addon: 1 }, uses_machine: USES,
  blocked: allowed() ? [] : [{ kind: "addon", id: "@acme-hub/k3s", name: "Kubernetes", reasons: [REASON] }] });
const RUNNING = { key: "running", label: "Running", running: true };
const OFF = { key: "off", label: "", running: false };
// The add-on of that catalog, as an item of the state: Blocked with its note and its reason until the catalog is allowed
const k3s = () => {
  const blocked = !allowed();
  return item("addon", "@acme-hub/k3s", "Kubernetes", {
    ...gains({ origin: origin(), readonly: true, blocked: blocked ? { reasons: [REASON] } : null, uses_machine: [{ service: "k3s", what: ["runs privileged"], why: "Runs containerd and the kubelet" }] }),
    services: [], needs: [], conflicts: {}, restarts: [], brings: [], recreates: [], links: [], secondary: null, failed_job: null, login: null, on: world.on, up: world.on, state: world.on ? RUNNING : OFF,
    action: blocked ? { do: "start", label: "Start", enabled: false, reason: REASON, dialog: null }
      : { do: world.on ? "stop" : "start", label: world.on ? "Stop" : "Start", enabled: true, reason: null, dialog: null },
    ...(blocked ? { note: "Blocked", note_tone: "err", note_title: REASON, note_causes: [{ kind: "blocked", reasons: [REASON], owner: "Acme Hub", folder: false }] } : {}),
  });
};
const stateNow = () => lab({ catalogs: world.added ? [FIRST(), row()] : [FIRST()], addons: world.added ? [k3s()] : [], content: content("ready") });

const reply = (body, status = 200) => ({ ok: status < 400, status, statusText: "", headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path, options = {}) => {
    if (path === "/api/state") return reply(stateNow());
    const body = options.body ? JSON.parse(options.body) : undefined;
    world.calls.push([path, body]);
    if (world.refuse[path]) return reply({ error: world.refuse[path].error, code: world.refuse[path].code }, 409);
    if (path === "/api/catalogs/check") return reply(preview());
    if (path === "/api/catalogs") {
      world.added = true;
      return reply({ catalog: row() });
    }
    if (path === "/api/catalogs/acme-hub/allow") {
      // The server holds the allowance while the item is on (host_not_allowed), and writes it otherwise
      if (!body.enabled && world.on) return reply({ error: "Turn off Kubernetes first.", code: "host_not_allowed" }, 409);
      world.host = body.enabled ? "allowed" : "no";
      return reply({ catalog: row() });
    }
    return reply({});
  },
});
globalThis.innerHeight = 800;
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
const { matches, closest } = El.prototype;
El.prototype.matches = function (selector) { return selector === ":focus-visible" ? false : matches.call(this, selector); };
El.prototype.closest = function (selector) { return closest.call(this, selector.replace(":is(.busy, .said)", ".busy")); };
Ev.prototype.stopImmediatePropagation = function () { this.cancelBubble = true; };
Object.defineProperty(El.prototype, "checked", {
  get() { return this._checked ?? this.hasAttribute("checked"); },
  set(value) { this._checked = !!value; },
  configurable: true,
});

const { store, router } = await import("../../../web/js/core.js");
const { catalogsPage } = await import("../../../web/js/pages/catalogs.js");
const { addonsPage } = await import("../../../web/js/pages/list.js");
const { openAdd } = await import("../../../web/js/dialogs/catalog.js");
const { paintActions } = await import("../../../web/js/components/ui.js");
page.ids["combo-pop"].innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { Combo } = await import("../../../web/js/components/combo.js");
Combo.init();

const pages = { catalogs: catalogsPage, addons: addonsPage };
const sections = {};
for (const [name, entry] of Object.entries(pages)) {
  sections[name] = page.doc.createElement("section");
  page.doc.body.append(sections[name]);
  entry.init(sections[name]);
}
store.subscribe((state) => { for (const entry of Object.values(pages)) entry.render(state); });
router.current = { page: { id: "catalogs" }, params: [] };

// --- Helpers ------------------------------------------------------------------------------------------------------------------------------

const settle = async () => {
  for (let i = 0; i < 10; i++) await flush();
  mock.timers.tick(700);
  for (let i = 0; i < 3; i++) await flush();
  paintActions();
};
const refresh = async () => {
  await store.refresh();
  await settle();
};
const calls = () => world.calls.map(([path, body]) => [path, body]);
const toasts = () => page.doc.querySelectorAll(".toast").map(text);
const clearToasts = () => page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
const adding = () => page.doc.querySelector("#catalog-title").closest("dialog");
// The words of an element, except the bubbles of its tooltips, which a screen reader does not read twice either
const words = (el) => (el.tag ? (el.getAttribute("aria-hidden") ? "" : el.children.map(words).filter(Boolean).join(" ")) : el.data.trim());
const rowsOf = () => adding().querySelectorAll("#catalog-rows .choice-row").map((r) => [text(r.querySelector(".choice-row-label")), words(r.querySelector(".choice-items") || r.querySelector(".choice-summary"))]);
const primary = () => adding().querySelector("#catalog-primary button");
const next = async (el) => {
  el.closest("form").dispatchEvent(new Ev("submit"));
  await settle();
};
const catalogRow = () => sections.catalogs.querySelectorAll(".list-item").find((li) => text(li.querySelector(".list-name")) === "Acme Hub");
const menuOf = (li) => {
  li.querySelector("[data-more]").click();
  const entries = page.ids.menu.querySelectorAll(".menu-item");
  return entries.map((el) => [text(el.querySelector("span")), el.hasAttribute("disabled") ? text(el.querySelector(".menu-hint")) : ""]);
};
const choose = (li, label) => {
  li.querySelector("[data-more]").click();
  const entry = page.ids.menu.querySelectorAll(".menu-item").find((el) => text(el.querySelector("span")) === label);
  assert.ok(entry, label);
  page.doc.dispatchEvent(new Ev("click", { target: entry }));
};
const closeMenu = () => page.doc.dispatchEvent(new Ev("click", { target: page.doc.body }));
const kubernetes = () => sections.addons.querySelector('.list-item[data-key="@acme-hub/k3s"]');
// The pairs of the opened row of a catalog, as [label, value]
const about = () => catalogRow().querySelectorAll(".panel-fact").map((fact) => [words(fact.children[0]), words(fact.children[1])]);

// The dialog of ui.js `effectsDialog`: the browser tells it which choice is checked, which the stub does not
const choice = page.ids.choice, picks = page.ids["choice-list"], ok = page.ids["choice-ok"];
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input").find((i) => i.checked) ?? picks.querySelectorAll("input")[0]
  : El.prototype.querySelector.call(picks, selector));
const effects = () => ({ title: text(page.ids["choice-title"]), action: text(ok), danger: ok.classList.contains("danger"),
  steps: picks.querySelectorAll(".step").map((s) => [text(s.querySelector("strong")), s.querySelectorAll(".step-items li").map(text), s.querySelector(".step-head .muted") ? text(s.querySelector(".step-head .muted")) : ""]),
  notes: picks.querySelectorAll(".choice-row").map((n) => [text(n.querySelector(".choice-row-label")), n.querySelectorAll(".pill").map(text), n.querySelector(".choice-items > .muted") ? text(n.querySelector(".choice-items > .muted")) : ""]) });
const answer = async (confirmed) => {
  choice.returnValue = confirmed ? "ok" : "";
  choice.onclose();
  await settle();
};
const enter = () => {
  const key = new Ev("keydown", { key: "Enter" });
  picks.onkeydown(key);
  return key.defaultPrevented;
};

// --- The walk ----------------------------------------------------------------------------------------------------------------------------

test("the Add dialog lists the item that uses this machine, offers to keep it blocked or to allow it, and Allow adds the catalog and then gives the allowance", async () => {
  world.calls.length = 0;
  store.state = stateNow();
  await refresh();
  clearToasts();
  openAdd({ address: "acme/lab-hub" });
  await settle();
  assert.equal(adding().hasAttribute("open"), true);
  assert.equal(text(page.doc.querySelector("#catalog-title")), "Add Acme Hub");
  // The preview lists it; the rows say what it takes, and, while it is kept blocked, that it cannot run
  assert.deepEqual(rowsOf().filter(([label]) => ["Uses this machine", "Cannot run"].includes(label)), [["Uses this machine", "1 item"], ["Cannot run", "1 item"]]);
  const mark = adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "Uses this machine").querySelector(".tip");
  assert.deepEqual(bubble(mark), ["Kubernetes", "Runs privileged", "Runs containerd and the kubelet"], "what it takes and the author's reason, per item");
  const choices = adding().querySelector("#catalog-choice");
  assert.equal(choices.hidden, false);
  const [keep, allow] = choices.querySelectorAll("input");
  assert.deepEqual([keep.getAttribute("value"), keep.checked, allow.getAttribute("value"), allow.checked], ["keep", true, "allow", false], "Keep them blocked is the default");
  assert.deepEqual(choices.querySelectorAll(".choice-text").map((c) => [text(c.querySelector("strong")), text(c.querySelector(".sub"))]),
    [["Keep them blocked", "They are listed and cannot start"], ["Allow them to use this machine", "Privileged containers, the Docker socket or folders of this machine"]]);
  // Allowing them takes them off the list of what cannot run, and the button stays the neutral one that Enter confirms
  keep.checked = false;
  allow.checked = true;
  allow.dispatchEvent(new Ev("change", { bubbles: true }));
  assert.ok(!rowsOf().some(([label]) => label === "Cannot run"));
  assert.equal(primary().classList.contains("danger"), false);
  world.calls.length = 0;
  await next(primary());
  assert.deepEqual(calls(), [["/api/catalogs", { ticket: "9f3a4c1d2e5b6a70" }], ["/api/catalogs/acme-hub/allow", { enabled: true }]]);
  assert.equal(adding().hasAttribute("open"), false);
  assert.deepEqual(toasts(), ["Added"]);
  assert.equal(world.host, "allowed");
});

test("once allowed the item is free to start and the Catalogs page says Allowed; the menu can stop the allowance", async () => {
  await refresh();
  const li = catalogRow();
  assert.ok(li, "the new row");
  li.querySelector(".s-toggle").click();
  const pairs = about();
  assert.deepEqual(pairs.find(([label]) => label === "Uses this machine"), ["Uses this machine", "Allowed"]);
  assert.equal(li.querySelectorAll(".panel").length, 2, "Holds, and nothing that cannot run");
  assert.ok(!li.querySelector(".list-detail").innerHTML.includes("Runs with full privileges"));
  const labels = menuOf(li);
  closeMenu();
  assert.deepEqual(labels.find(([label]) => /allowing use/.test(label)), ["Stop allowing use of this machine", ""]);
  assert.ok(!labels.some(([label]) => label === "Allow use of this machine"));
  // The item itself: no note, its Start is free, and its opened row says what it takes in its author's words
  const k = kubernetes();
  assert.equal(k.querySelector(".list-state .note"), null);
  const start = k.querySelector(".list-actions .btn");
  assert.equal(isOff(start), false);
});

test("while the item is on the allowance cannot be stopped: the menu says which to turn off, and a server that holds it says the same in an error", async () => {
  world.on = true;
  await refresh();
  const li = catalogRow();
  assert.deepEqual(menuOf(li).find(([label]) => /allowing use/.test(label)), ["Stop allowing use of this machine", "Stop Kubernetes first"]);
  closeMenu();
  // Choosing the held entry does nothing
  world.calls.length = 0;
  li.querySelector("[data-more]").click();
  const held = page.ids.menu.querySelectorAll(".menu-item").find((el) => text(el.querySelector("span")) === "Stop allowing use of this machine");
  page.doc.dispatchEvent(new Ev("click", { target: held }));
  await settle();
  assert.deepEqual(calls(), []);
  assert.equal(page.ids.choice.hasAttribute("open"), false, "a held entry opens no dialog");
  closeMenu();
  // A page that did not know the item is on (a poll late) asks, and the server holds it: its words are the toast, and the state is read again
  world.on = false;
  await refresh();
  world.on = true;
  clearToasts();
  choose(catalogRow(), "Stop allowing use of this machine");
  await settle();
  world.calls.length = 0;
  await answer(true);
  assert.deepEqual(calls(), [["/api/catalogs/acme-hub/allow", { enabled: false }]]);
  assert.deepEqual(toasts(), ["Turn off Kubernetes first."]);
  assert.equal(world.host, "allowed", "nothing was taken away");
  world.on = false;
  await refresh();
});

test("Stop allowing says what it blocks and is harmless; the item is Blocked again, with its reason, and cannot start", async () => {
  clearToasts();
  choose(catalogRow(), "Stop allowing use of this machine");
  await settle();
  const said = effects();
  assert.deepEqual([said.title, said.action, said.danger], ["Stop allowing use of this machine", "Stop allowing", false]);
  assert.deepEqual(said.steps, [["Blocks", ["Kubernetes"], "They stay listed and cannot start"]]);
  world.calls.length = 0;
  await answer(true);
  assert.deepEqual(calls(), [["/api/catalogs/acme-hub/allow", { enabled: false }]]);
  assert.equal(world.host, "no");
  const k = kubernetes();
  assert.equal(k.querySelector(".list-state .note"), null, "the State does not say it: the warning beside the Start does");
  assert.equal(text(k.querySelector(".list-actions .pop-title")), "Blocked");
  const start = k.querySelector(".list-actions .btn.act");
  assert.equal(isOff(start), true, "its Start waits");
  assert.equal(offWhy(start), REASON, "and says the first reason");
  // The page of the catalogs lists what cannot run
  const li = catalogRow();
  const pairs = about();
  assert.deepEqual(pairs.find(([label]) => label === "Uses this machine"), ["Uses this machine", "Not allowed"]);
  assert.ok(li.querySelector(".list-detail").innerHTML.includes(REASON));
});

test("Allow use of this machine names the item and what it takes, in the danger tone: Enter never gives it, and only the button does", async () => {
  choose(catalogRow(), "Allow use of this machine");
  await settle();
  const said = effects();
  assert.deepEqual([said.title, said.action, said.danger], ["Allow use of this machine", "Allow", true]);
  assert.deepEqual(said.steps, [["Uses this machine", ["Kubernetes: runs privileged"], ""]]);
  assert.equal(enter(), true, "a key that presses Enter is refused");
  world.calls.length = 0;
  await answer(false);
  assert.deepEqual(calls(), [], "canceled: nothing is allowed");
  assert.equal(world.host, "no");
  choose(catalogRow(), "Allow use of this machine");
  await settle();
  await answer(true);
  assert.deepEqual(calls(), [["/api/catalogs/acme-hub/allow", { enabled: true }]]);
  assert.equal(world.host, "allowed");
  const start = kubernetes().querySelector(".list-actions .btn");
  assert.equal(isOff(start), false, "free again");
});

test("an update that makes items use this machine is held the same way: the choice is in the dialog of the update, and an allowance given there is given after it", async () => {
  // The plan of an update the catalog's new version brings: Kubernetes now uses this machine and, while the catalog is not allowed, cannot run
  world.host = "no";
  await refresh();
  const plan = { ticket: "b1c2d3e4f5a60718", from: "9f1c2ab", to: "c4d5e6f", version: "1.5.0", rewritten: false, updates: [], adds: [], removes: [], breaks: [],
    changes: [{ id: "@acme-hub/k3s", name: "Kubernetes", fact: "host", value: "runs privileged" }], whatsnew: [], blocked: [], held: null, uses_machine: USES };
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    if (path === "/api/catalogs/acme-hub/update") { world.calls.push([path, body]); return reply(body.check ? plan : { catalog: row() }); }
    return original(path, options);
  };
  try {
    world.calls.length = 0;
    const li = catalogRow();
    // The row is up to date in this fixture: the update is asked for from its own button, which a catalog with a newer version has
    store.state = lab({ catalogs: [FIRST(), { ...row(), state: { key: "update", label: "Update available", tone: "warn", reason: "Version 1.5.0", next: "", whatsnew: [] } }], addons: [k3s()] });
    for (const entry of Object.values(pages)) entry.render(store.state);
    const button = catalogRow().querySelector("[data-update]");
    assert.ok(button && li);
    button.click();
    await settle();
    assert.deepEqual(calls(), [["/api/catalogs/acme-hub/update", { check: true }]]);
    const dialog = adding();
    assert.equal(text(page.doc.querySelector("#catalog-title")), "Update Acme Hub");
    assert.deepEqual(rowsOf().map(([label]) => label), ["Version", "Uses this machine", "Changes", "Cannot run"], "kept blocked: the item cannot run");
    assert.equal(rowsOf().find(([label]) => label === "Changes")[1], "Kubernetes now runs privileged");
    const [keep, allow] = dialog.querySelectorAll("#catalog-choice input");
    assert.equal(keep.checked, true);
    keep.checked = false;
    allow.checked = true;
    allow.dispatchEvent(new Ev("change", { bubbles: true }));
    assert.ok(!rowsOf().some(([label]) => label === "Cannot run"));
    world.calls.length = 0;
    await next(primary());
    assert.deepEqual(calls(), [["/api/catalogs/acme-hub/update", { ticket: "b1c2d3e4f5a60718" }], ["/api/catalogs/acme-hub/allow", { enabled: true }]]);
  } finally {
    globalThis.fetch = original;
  }
});

test("a listed catalog has the allowance by being listed: no choice, no menu entry and no pair, whatever its items use", async () => {
  world.added = false;
  store.state = lab({ catalogs: [FIRST({ uses_machine: USES })], content: content("ready") });
  for (const entry of Object.values(pages)) entry.render(store.state);
  const li = sections.catalogs.querySelectorAll(".list-item").find((x) => text(x.querySelector(".list-name")) === "Gravitee public");
  li.querySelector(".s-toggle").click();
  assert.ok(!li.querySelectorAll(".panel-fact").some((fact) => text(fact.children[0]) === "Uses this machine"));
  const labels = menuOf(li).map(([label]) => label);
  closeMenu();
  assert.ok(!labels.some((label) => /use of this machine/.test(label)), labels.join());
});
