// The Catalogs page (web/js/pages/catalogs.js) and its dialogs (web/js/dialogs/catalog.js), run for real over a small DOM and a stub of what the
// server answers, with the state the server answers (fixtures.mjs): a row for each kind of catalog and each of its states, the popover of
// each state, what a row opens on, the menu and its reasons, the Add dialog in both screens (the address, then what the catalog adds, or the
// second screen at once from a known address), Update and Go back, Remove, Allow and New catalog, and the actions of the content status.
// Every name that comes from a server is hostile on purpose somewhere: none becomes markup.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, isOff, offWhy, text } from "./dom-stub.mjs";
import { ADDRESS, FIRST, FOLDER, HOSTILE, HOSTILE_QUOTED, NOT_ADDED, PERSONAL, STATES, catalog, content, item, lab, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
// A timer made with no delay (ui.js looks after a menu so, each time a state is drawn) gets from node's mock a time that is not a number, and
// a queue with one in it fires the timers behind it late or never. It is given the 0 that a browser reads.
{
  const set = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay = 0, ...rest) => set(fn, delay, ...rest);
}
const source = (file) => readFileSync(new URL(`../../../web/${file}`, import.meta.url), "utf8");
const html = source("index.html");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// What the server answers: the calls the page made, in order, the answer to each route (a value, or a function of the body), and a refusal
const server = { calls: [], answers: {}, refuse: {}, states: 0 };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path, options = {}) => {
    const method = options.method || "GET", body = options.body ? JSON.parse(options.body) : undefined;
    if (path === "/api/state") {
      server.states += 1;
      return reply(store.state);
    }
    server.calls.push({ method, path, body });
    if (server.refuse[path]) return reply({ error: server.refuse[path] }, 400);
    const answer = server.answers[path];
    return reply(typeof answer === "function" ? await answer(body) : answer ?? {});
  },
});
globalThis.innerHeight = 800;
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
// Selectors the page asks and the small DOM does not know: a menu opened with the keyboard (:focus-visible), a button at work (:is)
const { matches, closest } = El.prototype;
El.prototype.matches = function (selector) { return selector === ":focus-visible" ? false : matches.call(this, selector); };
El.prototype.closest = function (selector) { return closest.call(this, selector.replace(":is(.busy, .said)", ".busy")); };
// The page stops a click on a control that waits where the document hears it: the stub's events have no such call
Ev.prototype.stopImmediatePropagation = function () { this.cancelBubble = true; };
// A radio is checked as its markup says, until a test chooses another
Object.defineProperty(El.prototype, "checked", {
  get() { return this._checked ?? this.hasAttribute("checked"); },
  set(value) { this._checked = !!value; },
  configurable: true,
});

const { store, router, ago } = await import("../../../web/js/core.js");
const { catalogsPage } = await import("../../../web/js/pages/catalogs.js");
const { addCatalog, openAdd, openImport, openNew, tryAgain } = await import("../../../web/js/dialogs/catalog.js");
const { paintActions, paintProgress } = await import("../../../web/js/components/ui.js");

const section = page.doc.createElement("section");
page.doc.body.append(section);
catalogsPage.init(section);
// The page draws whenever the store says the state changed, as the router has the page on screen do
store.subscribe((state) => catalogsPage.render(state));

// --- Helpers ------------------------------------------------------------------------------------------------------------------

const dialogs = () => page.doc.querySelectorAll("dialog");
const dialogOf = (title) => dialogs().find((d) => d.querySelector("h2") && text(d.querySelector("h2")) === title);
const opened = () => dialogs().filter((d) => d.hasAttribute("open"));
const toasts = () => page.doc.querySelectorAll(".toast").map(text);
const clearToasts = () => page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
const menu = () => page.doc.querySelector("#menu");
const entries = () => menu().querySelectorAll(".menu-item").map((el) => [text(el.querySelector("span")), el.disabled ? text(el.querySelector(".menu-hint") || el) : ""]);
const names = (root) => root.querySelectorAll(".menu-item").map((el) => text(el.querySelector("span")));
const calls = (path) => server.calls.filter((c) => c.path === path);
const last = () => server.calls[server.calls.length - 1];

// The state: the rows, around the lab; the page draws them. The rows a test left open are closed first, unless the test is about them staying.
function show(catalogs, more = {}, keep = false) {
  store.state = lab({ catalogs, ...more });
  store.pending.clear();
  if (!keep) {
    catalogsPage.show([]);
    for (const li of items().filter((x) => !x.querySelector(".list-detail").hidden)) li.querySelector(".s-toggle").click();
  }
  catalogsPage.render(store.state);
  paintProgress(section);
}
const items = () => section.querySelectorAll(".list-item");
const row = (name) => items().find((li) => text(li.querySelector(".list-name")) === name);
// A button as it reads at rest: the face of an action button, without the words it keeps for when it works
const face = (b) => text(b.querySelector(".pbtn-face") || b);
const cells = (li) => ({
  toggle: li.querySelector(".s-toggle"), name: text(li.querySelector(".list-name")), sub: li.querySelector(".list-desc") ? text(li.querySelector(".list-desc")) : null,
  state: li.querySelector(".status-word") ? text(li.querySelector(".status-word")) : "", items: text(li.querySelector(".col-cat")),
  buttons: li.querySelectorAll(".list-actions .btn").map(face), more: li.querySelector("[data-more]"),
});
const detail = (li) => li.querySelector(".list-detail");
// The pairs of a panel, as [label, value], and its headings
// The words of an element, except the bubbles of its tooltips, which a screen reader does not read twice either
const words = (el) => (el.tag ? (el.getAttribute("aria-hidden") ? "" : el.children.map(words).filter(Boolean).join(" ")) : el.data.trim());
const pairs = (panel) => panel.querySelectorAll(".panel-fact").map((fact) => [words(fact.children[0]), words(fact.children[1])]);
const panels = (li) => detail(li).querySelectorAll(".panel");

const click = (el) => el.click();
const press = (target, type = "click") => page.doc.dispatchEvent(new Ev(type, { target }));
const type = (el, value) => {
  el.value = value;
  el.dispatchEvent(new Ev("input", { bubbles: true }));
};
const submit = async (el) => {
  el.closest("form").dispatchEvent(new Ev("submit"));
  await flush();
};
const open = (li) => click(li.querySelector(".s-toggle"));

// A row of the table for each key, as the server words it (fixtures.mjs): the popover is its title, its reason, its next step
const GIT = (key, more = {}) => catalog({ state: STATES[key], ...more });

test("the page is a card of the lists' grid with its head: its title, then New catalog and Add catalog with its plus, none primary", () => {
  show([FIRST()]);
  assert.equal(text(section.querySelector("h1")), "Catalogs");
  const heads = section.querySelectorAll(".page-actions .btn");
  assert.deepEqual(heads.map(text), ["New catalog", "Add catalog"]);
  assert.deepEqual(heads.map((b) => b.classList.contains("primary")), [false, false], "no primary on the page");
  assert.equal(heads[0].querySelectorAll("svg").length, 0);
  assert.ok(heads[1].querySelector("svg use").getAttribute("href").endsWith("#i-plus"));
  assert.ok(section.querySelector(".card.list-card"), "the one card of the lists");
  assert.deepEqual(section.querySelector(".list-head").children.map(text), ["", "Catalog", "Items", "State", ""]);
  assert.deepEqual([catalogsPage.id, catalogsPage.nav, catalogsPage.title], ["catalogs", "catalogs", "Catalogs"]);
});

test("Add catalog waits with the reason \"Git is not installed\" when Git is missing; New catalog needs no Git", () => {
  show([FIRST()], { git: { available: false, version: "" } });
  const [made, added] = section.querySelectorAll(".page-actions .btn");
  assert.equal(isOff(made), false);
  assert.equal(isOff(added), true);
  assert.equal(offWhy(added), "Git is not installed");
  show([FIRST()]);
  assert.equal(isOff(added), false, "it comes back with Git");
});

// --- Rows ---------------------------------------------------------------------------------------------------------------------

const ROWS = () => [FIRST(), PERSONAL(), GIT("update", { id: "acme-hub", version: "1.4.0" }), FOLDER(), NOT_ADDED({ id: "partner-bank", listed: false, name: "Partner Bank", description: "Banking demos from a partner", url: "https://example.org/partner/lab-bank.git" })];

test("the rows come in two sections, Local then Remote, each in the order the server gives them, each with its name, its muted line, its state, its items and the one thing to press", () => {
  show(ROWS());
  assert.deepEqual(items().map((li) => text(li.querySelector(".list-name"))), ["Personal", "My catalog", "Gravitee public", "Acme Hub", "Partner Bank"]);
  assert.deepEqual(section.querySelectorAll(".list-rows > .stack-sep").map((band) => text(band)), ["Local", "Remote"]);
  assert.deepEqual([...section.querySelector(".list-rows").children].map((li) => (li.className === "stack-sep" ? text(li) : text(li.querySelector(".list-name")))),
    ["Local", "Personal", "My catalog", "Remote", "Gravitee public", "Acme Hub", "Partner Bank"], "a band over the rows of its section");
  const first = cells(row("Gravitee public"));
  assert.deepEqual([first.sub, first.state, first.items, first.buttons], ["The demos everyone has", "Up to date", "25", []], "the default catalog: no Update while it is current");
  // Personal: a row with no description has no subtitle, no state and no actions, and counts what is local
  const personal = cells(row("Personal"));
  assert.deepEqual([personal.sub, personal.state, personal.items, personal.buttons, personal.more], [null, "", "3", [], null]);
  assert.equal(row("Personal").querySelector(".list-desc"), null);
  // A catalog with an update: Update, then the menu
  const update = cells(row("Acme Hub"));
  assert.deepEqual([update.sub, update.state, update.items, update.buttons], ["Banking demos for partner workshops", "Update available", "25", ["Update"]]);
  assert.ok(update.more, "the More menu follows");
  // A folder: its state is Linked, no description to say
  const folder = cells(row("My catalog"));
  assert.deepEqual([folder.sub, folder.state, folder.buttons], [null, "Linked", []]);
  // One that is only offered: Add, no items, no menu, and nothing to open
  const offered = cells(row("Partner Bank"));
  assert.deepEqual([offered.sub, offered.state, offered.items, offered.buttons, offered.more, offered.toggle], ["Banking demos from a partner", "Not added", "", ["Add"], null, null]);
});

test("the imported playgrounds are one row, Imported, in Local: it counts them all and opens on each, with its menu, and on what they hold", () => {
  const imported = (id, name, counts) => catalog({ id, name, kind: "folder", imported: true, counts, state: STATES.linked,
    address: `~/lab/.lab/imports/${id}`, held: {} });
  show([FIRST(), imported("contoso", "Contoso demo", { playground: 1, setup: 0, addon: 2 }), imported("contoso-rail", "Contoso Rail", { playground: 1, setup: 1, addon: 3 })]);
  assert.deepEqual([...section.querySelector(".list-rows").children].map((li) => (li.className === "stack-sep" ? text(li) : text(li.querySelector(".list-name")))),
    ["Local", "Imported", "Remote", "Gravitee public"]);
  const group = cells(row("Imported"));
  assert.deepEqual([group.state, group.items, group.buttons, group.more], ["", "8", [], null]);
  open(row("Imported"));
  const [imports, holds] = panels(row("Imported"));
  assert.deepEqual(pairs(imports).map(([name]) => name), ["Contoso demo", "Contoso Rail"]);
  imports.querySelector('[data-member="contoso"] [data-member-more]').click();
  assert.deepEqual(names(menu()).filter(Boolean), ["Remove"], "an import has no address to copy");
  assert.deepEqual(pairs(holds), [["Playgrounds", "2"], ["Setups", "1"], ["Add-ons", "5"]]);
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape" }));
});

test("kept settings are listed where they came from, by the names of their items, and go at the person's word only, saying what goes", async () => {
  clearToasts();
  const kept = [{ folder: "contoso-1a2b3c4d", from: "Contoso demo", personal: false, items: [{ name: "Kiosk", kinds: ["Settings", "Secrets"] }], at: "2026-10-20T08:00:00Z" },
    { folder: "local-0a1b2c3d", from: "Personal", personal: true, items: [{ name: "Echo Tool", kinds: ["Settings", "Secrets", "State"] }], at: "2026-10-21T08:00:00Z" }];
  show([FIRST(), PERSONAL()], { kept_settings: kept });
  assert.deepEqual([...section.querySelector(".list-rows").children].map((li) => (li.className === "stack-sep" ? text(li) : text(li.querySelector(".list-name")))),
    ["Local", "Imported", "Personal", "Remote", "Gravitee public"], "Imported is there for kept settings alone");
  open(row("Imported"));
  const [imported] = panels(row("Imported"));
  assert.deepEqual(pairs(imported).map(([name, value]) => [name, value.replace(/ \d+ \w+ ago$/, "")]), [["Kiosk", "From Contoso demo,"]]);
  open(row("Personal"));
  const personal = panels(row("Personal")).find((panel) => text(panel).startsWith("Kept settings"));
  assert.deepEqual(pairs(personal).map(([name]) => name), ["Echo Tool"], "what came from Personal is on Personal's row");
  assert.ok(!text(section).includes(".lab/") && !text(section).includes("echo-tool"), "nothing of the Lab's own naming");
  const button = personal.querySelector('[data-kept="local-0a1b2c3d"] [data-kept-more]');
  assert.ok(button.getAttribute("class").includes("panel-more"), "at the end of the row, as every More");
  server.answers["/api/kept-settings/delete"] = {};
  button.click();
  assert.deepEqual(names(menu()), ["Remove"]);
  press(menu().querySelectorAll(".menu-item")[0]);
  await flush();
  assert.equal(text(page.ids["choice-title"]), "Remove kept settings");
  assert.ok(text(page.ids["choice-list"]).includes("Echo Tool: Settings, Secrets, State"), "what goes, item by item");
  assert.equal(calls("/api/kept-settings/delete").length, 0, "nothing goes before the person says so");
  page.ids.choice.returnValue = "ok";
  page.ids.choice.onclose();
  await settle();
  assert.deepEqual(last().body, { folder: "local-0a1b2c3d" });
  assert.deepEqual(toasts(), ["Removed"]);
  show([FIRST(), PERSONAL()], { kept_settings: kept });   // the rows it opened are closed again
});

test("Personal is drawn only once it holds something, or the Lab keeps settings of it", () => {
  const empty = PERSONAL({ counts: { playground: 0, setup: 0, addon: 0 } });
  show([FIRST(), empty]);
  assert.deepEqual(items().map((li) => text(li.querySelector(".list-name"))), ["Gravitee public"]);
  show([FIRST(), empty], { kept_settings: [{ folder: "local-0a1b2c3d", from: "Personal", personal: true, items: [{ name: "Echo Tool", kinds: ["Settings"] }],
    at: "2026-10-21T08:00:00Z" }] });
  assert.deepEqual(items().map((li) => text(li.querySelector(".list-name"))), ["Personal", "Gravitee public"], "its kept settings can still be removed");
  open(row("Personal"));
  const kept = panels(row("Personal")).find((panel) => text(panel).startsWith("Kept settings"));
  assert.deepEqual(pairs(kept).map(([name]) => name), ["Echo Tool"]);
  show([FIRST(), empty]);   // the row it opened is closed again
});

test("a row is 56 px of the lists' grid: a toggle named for the catalog, its name and line, its items, its state, its actions", () => {
  show([FIRST()]);
  const li = items()[0], top = li.querySelector(".list-row");
  assert.ok(top.classList.contains("list-grid"));
  assert.deepEqual(top.children.filter((c) => c.tag).map((c) => c.getAttribute("class")), ["s-toggle", "list-main", "list-col col-cat", "list-state", "list-actions"]);
  const toggle = top.children[0];
  assert.deepEqual([toggle.tag, toggle.getAttribute("aria-label"), toggle.getAttribute("aria-expanded"), toggle.getAttribute("aria-controls")],
    ["button", "Gravitee public", "false", "detail-catalog-gravitee"]);
  assert.equal(li.querySelector(".list-detail").id, "detail-catalog-gravitee");
  assert.equal(li.querySelector(".list-detail").hidden, true);
});

// --- States ---------------------------------------------------------------------------------------------------------------------

const dot = (li) => li.querySelector(".list-state .dot-status").getAttribute("class").replace("dot-status", "").trim();

test("every state is a dot and a word, with a popover of its title, its reason and its next step, and nothing to say where the word says it all", () => {
  const dots = { uptodate: "done", update: "warn", checking: "", updating: "starting", downloading: "starting", unreachable: "warn", noaccess: "issue",
    needs_git: "issue", wrong_catalog: "issue", rewritten: "issue", lab_new: "warn", lab_old: "warn", damaged: "issue", linked: "done",
    missing_folder: "issue", suggested: "off" };
  for (const [key, state] of Object.entries(STATES)) {
    show([GIT(key, { id: "x-catalog", name: "X catalog", commit: "9f1c2ab", kind: key === "suggested" ? "suggested" : "git" })]);
    const li = row("X catalog"), shown = li.querySelector(".list-state");
    assert.equal(text(shown.querySelector(".status-word")), state.label, key);
    assert.equal(dot(li), dots[key], `${key}: its dot`);
    assert.equal(shown.querySelector(".status").classList.contains("muted"), state.tone === "muted", `${key}: a muted word is for what needs nothing`);
    const lines = [state.label, state.reason, state.next].filter(Boolean);
    if (lines.length > 1) assert.deepEqual(bubble(shown.querySelector(".tip")), lines, `${key}: title, reason, next step`);
    else assert.equal(shown.querySelector(".tip"), null, `${key}: no popover that repeats the word`);
  }
});

test("the table is what the server's words say for the states that name a host or a catalog", () => {
  show([GIT("unreachable", { commit: "9f1c2ab" })]);
  assert.deepEqual(bubble(row("Acme Hub").querySelector(".tip")), ["Unreachable", "Cannot reach github.com.", "Check your connection, then try again."]);
  show([GIT("noaccess")]);
  assert.deepEqual(bubble(row("Acme Hub").querySelector(".tip")), ["No access", "No access to owner/repo.", "Run gh auth login in a terminal, then try again."]);
  show([GIT("lab_old")]);
  assert.deepEqual(bubble(row("Acme Hub").querySelector(".tip")), ["Written for an older Gravitee Lab", "This version is too old for this Lab.", "Update it, or ask the owners of Acme Hub to update it."]);
});

test("a download shows how far it is under its word; a row with no copy that is not downloading offers Try again, and one that downloads offers nothing", () => {
  show([GIT("downloading", { id: "acme-hub", commit: "", progress: 0.42 })]);
  const li = row("Acme Hub"), bar = li.querySelector(".list-state .progress");
  assert.deepEqual([bar.getAttribute("data-progress"), bar.hidden, bar.getAttribute("aria-valuenow")], ["catalog:acme-hub", false, "42"]);
  assert.deepEqual(cells(li).buttons, [], "an attempt runs: a retry would collide with it");
  show([GIT("unreachable", { commit: "", progress: null })]);
  assert.deepEqual(cells(row("Acme Hub")).buttons, ["Try again"]);
  assert.equal(row("Acme Hub").querySelector(".list-state .progress"), null);
  // With a copy installed an unreachable address is a word, not a button
  show([GIT("unreachable", { commit: "9f1c2ab" })]);
  assert.deepEqual(cells(row("Acme Hub")).buttons, []);
});

test("Update is the button of a catalog with something to update; the state that leads to Update anyway has it, and every other state has none", () => {
  const with_ = [];
  for (const key of Object.keys(STATES)) {
    show([GIT(key, { commit: "9f1c2ab", kind: key === "suggested" ? "suggested" : "git" })]);
    if (cells(row("Acme Hub")).buttons.includes("Update")) with_.push(key);
  }
  assert.deepEqual(with_, ["update", "rewritten"]);
  // The state of a catalog written for an older Lab leads to Update when the server says one is there
  show([GIT("lab_old", { commit: "9f1c2ab", attention: "update" })]);
  assert.deepEqual(cells(row("Acme Hub")).buttons, ["Update"]);
});

test("what a plain request is doing is said in the state meanwhile, by the word of the server's own", () => {
  show([GIT("uptodate")]);
  store.pending.set("catalog-op:acme-hub", "check");
  catalogsPage.render(store.state);
  assert.equal(cells(row("Acme Hub")).state, "Checking");
  store.pending.set("catalog-op:acme-hub", "update");
  catalogsPage.render(store.state);
  assert.equal(cells(row("Acme Hub")).state, "Updating");
  store.pending.set("catalog-op:acme-hub", "remove");
  catalogsPage.render(store.state);
  assert.equal(cells(row("Acme Hub")).state, "Removing");
  store.pending.delete("catalog-op:acme-hub");
  catalogsPage.render(store.state);
  assert.equal(cells(row("Acme Hub")).state, "Up to date");
});

test("an entry of the list that is not a catalog is no row: the progress of the fetch of the Add dialog is carried there", () => {
  show([FIRST(), { id: "check", kind: "progress", progress: 0.4 }, { id: "odd" }, null]);
  assert.deepEqual(items().map((li) => text(li.querySelector(".list-name"))), ["Gravitee public"]);
  // ... and the bar of the dialog reads it, by the key the dialog gives it
  openAdd();
  paintProgress(adding());
  const bar = adding().querySelector("[data-progress]");
  assert.deepEqual([bar.getAttribute("data-progress"), bar.hidden, bar.getAttribute("aria-valuenow")], ["catalog:check", false, "40"]);
  shut();
});

test("a request on a row says so in its state from the click, not from the next poll, and holds the row's other controls and its menu meanwhile", async () => {
  const wait = hold();
  server.answers["/api/catalogs/acme-hub/check"] = () => wait.held.then(() => ({ catalog: GIT("uptodate") }));
  show([GIT("update", { has_previous: true, held: { update: "", remove: "", undo: "" } })]);
  assert.deepEqual(menuOf("Acme Hub").map(([, why]) => why), ["", "", "", "", "", ""], "nothing at work: nothing waits");
  choose("Acme Hub", "Check again");
  await flush();
  // Drawn by the notification alone: no poll, no render called here
  assert.equal(cells(row("Acme Hub")).state, "Checking");
  // The button of Update says the check at work, as it says its own: a second request on the row is not started by it
  const update = row("Acme Hub").querySelector("[data-update]");
  assert.deepEqual([update.classList.contains("busy"), text(update.querySelector(".pbtn-text"))], [true, "Checking…"]);
  const sent = server.calls.length;
  click(update);
  await flush();
  assert.equal(server.calls.length, sent);
  assert.deepEqual(menuOf("Acme Hub"), [["Check again", "Checking"], ["Stop checking automatically", "Checking"], ["Open repository", ""], ["Copy address", ""],
    ["Go back to previous version", "Checking"], ["Remove", "Checking"]], "its entries wait and say why; the ones that ask nothing of the Lab do not");
  wait.release();
  await settle();
  assert.notEqual(cells(row("Acme Hub")).state, "Checking");
  assert.equal(isOff(row("Acme Hub").querySelector("[data-update]")), false);
  assert.ok(menuOf("Acme Hub").every(([, why]) => !why));
  // A row with no copy offers Try again, which waits as well
  show([GIT("unreachable", { id: "later", name: "Later", commit: "" })]);
  store.pending.set("catalog-op:later", "check");
  store.notify();
  assert.deepEqual([isOff(row("Later").querySelector("[data-retry]")), offWhy(row("Later").querySelector("[data-retry]"))], [true, "Checking"]);
});

test("the check of a plan reads on the row as Checking wherever it was asked (Go back, Remove, the button of Update), and the button says so too", async () => {
  const wait = hold();
  show([GIT("update", { id: hub(), has_previous: true, held: { update: "", remove: "", undo: "" } })]);
  server.answers[at("/undo")] = () => wait.held.then(() => PLAN());
  choose("Acme Hub", "Go back to previous version");
  await flush();
  assert.equal(cells(row("Acme Hub")).state, "Checking");
  wait.release();
  await settle();
  assert.equal(heading(), "Go back to previous version");
  shut();
  const again = hold();
  server.answers[at("/update")] = () => again.held.then(() => PLAN());
  click(row("Acme Hub").querySelector("[data-update]"));
  await flush();
  assert.deepEqual([cells(row("Acme Hub")).state, text(row("Acme Hub").querySelector("[data-update] .pbtn-text"))], ["Checking", "Checking…"]);
  again.release();
  await settle();
  shut();
  // Remove: Checking while it reads, Removing once it is asked
  const reads = hold(), removes = hold();
  server.answers[at("/delete")] = (body) => (body.check ? reads.held.then(() => REMOVAL()) : removes.held.then(() => ({})));
  choose("Acme Hub", "Remove");
  await flush();
  assert.equal(cells(row("Acme Hub")).state, "Checking");
  reads.release();
  await settle();
  await answer(true);
  assert.equal(cells(row("Acme Hub")).state, "Removing");
  removes.release();
  await settle();
  assert.notEqual(cells(row("Acme Hub")).state, "Removing");
});

// --- Nothing to list ------------------------------------------------------------------------------------------------------------

const status = () => section.querySelector("[data-status]");

test("with no catalog the page says so in the block of the content status, offers the listed one, and keeps the head's two buttons", () => {
  show([], { content: content("none") });
  assert.equal(items().length, 0);
  assert.equal(section.querySelector(".list-head").hidden, true);
  const block = status().querySelector(".empty");
  assert.deepEqual([block.getAttribute("role"), block.getAttribute("data-content"), text(block.firstElementChild)], ["status", "none", "No catalog added"]);
  assert.deepEqual(block.querySelectorAll("button").map((b) => [b.getAttribute("data-content-act"), b.getAttribute("data-catalog"), text(b)]), [["add", "gravitee", "Add Gravitee public"]]);
  assert.deepEqual(section.querySelectorAll(".page-actions .btn").map(text), ["New catalog", "Add catalog"]);
  // The Catalogs page is the place to browse: with nothing to offer it says only that none is added
  show([], { content: content("none", { offer: null }) });
  assert.deepEqual([text(status().querySelector(".empty")), status().querySelectorAll("a, button").length], ["No catalog added", 0]);
  // Rows take its place
  show([FIRST()]);
  assert.equal(status().innerHTML, "");
  assert.equal(section.querySelector(".list-head").hidden, false);
});

test("a state that cannot be read says so with a plain Try again that reads it again", async () => {
  store.state = lab();
  delete store.state.catalogs;
  catalogsPage.render(store.state);
  const block = status().querySelector(".empty");
  assert.equal(text(block.querySelector("span")), "Catalogs cannot be read");
  const again = block.querySelector("button");
  assert.deepEqual([text(again), again.getAttribute("class")], ["Try again", "btn"]);
  const before = server.states;
  click(again);
  await flush();
  assert.equal(server.states, before + 1);
  assert.equal(items().length, 0);
});

// --- A row that opens -----------------------------------------------------------------------------------------------------------

const DAYS = (n) => new Date(Date.now() - n * 86400000).toISOString();
const USES = [{ id: "@acme-hub/k3s", name: "Kubernetes", what: ["runs privileged"], why: "Runs containerd and the kubelet" }];
const FULL = (more = {}) => GIT("update", { id: "acme-hub", ignored: 3, updated: DAYS(4), checked: new Date(Date.now() - 3 * 3600000).toISOString(),
  uses_machine: USES, blocked: [{ kind: "addon", id: "@acme-hub/x", name: "X", reasons: ["Mounts the Docker socket", "Publishes a port to the network"] }], ...more });

test("a row opens from its toggle and from a click anywhere on it but its controls; the detail is two panels", () => {
  show([FULL()]);
  const li = row("Acme Hub");
  assert.equal(detail(li).hidden, true);
  open(li);
  assert.deepEqual([cells(row("Acme Hub")).toggle.getAttribute("aria-expanded"), detail(li).hidden, li.classList.contains("open")], ["true", false, true]);
  assert.equal(panels(li).length, 2, "what it is, what it holds");
  click(li.querySelector(".s-toggle"));
  assert.deepEqual([cells(row("Acme Hub")).toggle.getAttribute("aria-expanded"), detail(li).hidden], ["false", true]);
  // Anywhere on the row opens it, its controls do not
  click(li.querySelector(".list-name"));
  assert.equal(detail(li).hidden, false);
  click(li.querySelector(".list-name"));
  click(li.querySelector("[data-more]"));
  assert.equal(detail(li).hidden, true, "its menu is not a click on the row");
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape" }));
});

test("About says where the catalog is read from, its version and commit, when it changed and was looked at, who can read it and what this machine allows", () => {
  show([FULL()]);
  open(row("Acme Hub"));
  const [about, holds] = panels(row("Acme Hub"));
  assert.equal(text(about.querySelector(".panel-head")), "About");
  assert.equal(text(about.querySelector("p.panel-row")), "Banking demos for partner workshops");
  assert.deepEqual(pairs(about).map(([label]) => label), ["Address", "Version", "Commit", "Updated", "Checked", "Access", "Uses this machine", "Ignored"]);
  const said = Object.fromEntries(pairs(about));
  assert.deepEqual([said.Address, said.Version, said.Commit, said.Updated, said.Checked, said.Access, said["Uses this machine"], said.Ignored],
    ["github.com/acme/lab-hub", "1.4.0", "9f1c2ab", `${ago(DAYS(4))} ago`, `${ago(new Date(Date.now() - 3 * 3600000).toISOString())} ago`, "Private", "Not allowed", "3 files"]);
  // The address is mono and has a button that copies the full address, without a credential
  assert.equal(about.querySelector(".panel-fact code").tag, "code");
  assert.equal(about.querySelector("[data-copy]").getAttribute("data-copy"), "https://github.com/acme/lab-hub.git");
  assert.equal(about.querySelector("[data-copy]").getAttribute("aria-label"), "Copy the address");
  // The marks say what each means
  const marks = about.querySelectorAll(".panel-fact .tip");
  assert.deepEqual(marks.map((tip) => bubble(tip)), [
    ["Measured when it was added. Private means Git asked for a sign-in."],
    ["Kubernetes", "Runs privileged", "Runs containerd and the kubelet"],
    ["Settings, keys, links and agent instructions are never installed from a catalog."]]);
  assert.ok(holds);
});

test("About leaves out what the catalog does not have: no ignored files, no allowance for a listed catalog or one with nothing that uses this machine", () => {
  show([FIRST({ uses_machine: USES, ignored: 0 }), GIT("uptodate", { id: "plain-hub", name: "Plain hub", ignored: 0 })]);
  for (const name of ["Gravitee public", "Plain hub"]) {
    open(row(name));
    assert.deepEqual(pairs(panels(row(name))[0]).map(([label]) => label), ["Address", "Version", "Commit", "Updated", "Checked", "Access"], name);
  }
  assert.equal(said(row("Gravitee public"), "Access"), "Public");
  show([FULL({ host: "allowed" })]);
  open(row("Acme Hub"));
  assert.equal(said(row("Acme Hub"), "Uses this machine"), "Allowed");
});
const said = (li, label) => Object.fromEntries(pairs(panels(li)[0]))[label];

test("About of a folder is its address, with ~ for the home folder, and the files left out", () => {
  show([FOLDER({ ignored: 2 })]);
  open(row("My catalog"));
  assert.deepEqual(pairs(panels(row("My catalog"))[0]), [["Address", "~/lab/my-catalog"], ["Ignored", "2 files"]]);
  assert.equal(panels(row("My catalog"))[0].querySelector("[data-copy]").getAttribute("data-copy"), "~/lab/my-catalog");
});

test("Holds is a button for each kind that has items; Cannot run lists each item that is Blocked with its first reason", () => {
  show([FULL()]);
  open(row("Acme Hub"));
  const holds = panels(row("Acme Hub"))[1];
  assert.deepEqual(holds.querySelectorAll(".panel-head").map(text), ["Holds", "Cannot run"]);
  const pair = pairs(holds);
  assert.deepEqual(pair, [["Playgrounds", "14"], ["Setups", "2"], ["Add-ons", "9"], ["X", "Mounts the Docker socket"]]);
  assert.deepEqual(holds.querySelectorAll("[data-holds]").map((b) => [b.getAttribute("data-holds"), b.getAttribute("aria-label")]),
    [["playground", "Show 14 playgrounds"], ["setup", "Show 2 setups"], ["addon", "Show 9 add-ons"]]);
  // A kind with none is not offered; with nothing blocked there is no such heading
  show([FULL({ counts: { playground: 0, setup: 1, addon: 0 }, blocked: [] })]);
  open(row("Acme Hub"));
  assert.deepEqual(pairs(panels(row("Acme Hub"))[1]), [["Setups", "1"]]);
  assert.deepEqual(panels(row("Acme Hub"))[1].querySelectorAll(".panel-head").map(text), ["Holds"]);
  // Personal opens on what it holds alone
  show([PERSONAL()]);
  open(row("Personal"));
  assert.equal(panels(row("Personal")).length, 1);
  assert.deepEqual(pairs(panels(row("Personal"))[0]), [["Playgrounds", "1"], ["Add-ons", "2"]]);
});

test("a button of Holds goes to its list, filtered to the catalog", async () => {
  const { setupsPage } = await import("../../../web/js/pages/list.js");
  const lists = page.doc.createElement("section");
  page.doc.body.append(lists);
  setupsPage.init(lists);
  const more = { members: [], includes: [], links: [], held: { share: "", delete: "" }, action: { do: "start", label: "Start", enabled: true, reason: null, dialog: null }, secondary: null };
  const setups = [item("setup", "plain", "Plain setup", more), item("setup", "@acme-hub/ledger", "Ledger setup", { ...more, origin: origin() })];
  show([FIRST(), FULL()], { setups });
  open(row("Acme Hub"));
  click(panels(row("Acme Hub"))[1].querySelector('[data-holds="setup"]'));
  assert.equal(page.session.entries[page.session.at].hash, "#/setups");
  setupsPage.show([]);
  setupsPage.render(store.state);
  assert.deepEqual(lists.querySelectorAll(".list-item .list-name").map(text), ["Ledger setup"], "what the catalog holds, and nothing else");
});

test("a file that did not load is a line of Cannot run, and the open rows are kept as the state is drawn again", () => {
  show([FULL({ blocked: [], problems: ["addons/y/compose.yml: Cannot be read: bad indent"] })]);
  open(row("Acme Hub"));
  assert.deepEqual(pairs(panels(row("Acme Hub"))[1]).pop(), ["addons/y/compose.yml", "Cannot be read: bad indent"]);
  show([FULL({ blocked: [], name: "Acme Hub" })], {}, true);
  assert.equal(detail(row("Acme Hub")).hidden, false, "it stays open");
});

// --- Its address ----------------------------------------------------------------------------------------------------------------

test("an address that names a catalog opens its row, brings it into view and focuses its toggle; the rows other addresses opened close again", () => {
  show([FIRST(), FULL()]);
  catalogsPage.show(["acme-hub"]);
  catalogsPage.render(store.state);
  mock.timers.tick(20);
  const li = row("Acme Hub");
  assert.equal(detail(li).hidden, false);
  assert.equal(page.doc.activeElement, li.querySelector(".s-toggle"));
  assert.equal(li.scrolled.length, 1);
  // Another address closes it, the list's own address leaves a row opened by hand
  open(row("Gravitee public"));
  catalogsPage.show(["gravitee"]);
  catalogsPage.render(store.state);
  assert.equal(detail(row("Acme Hub")).hidden, true);
  catalogsPage.show([]);
  catalogsPage.render(store.state);
  assert.equal(detail(row("Gravitee public")).hidden, false, "what was opened by hand stays");
  assert.ok(catalogsPage.pattern.test("#/catalogs/acme-hub"));
  assert.ok(catalogsPage.pattern.test("#/catalogs"));
  assert.ok(!catalogsPage.pattern.test("#/catalogs/Acme"));
  assert.ok(!catalogsPage.pattern.test("#/catalogs/a/b"));
});

test("an address whose row is not there yet waits for it", () => {
  show([FIRST()]);
  catalogsPage.show(["acme-hub"]);
  catalogsPage.render(store.state);
  mock.timers.tick(20);
  assert.notEqual(page.doc.activeElement?.getAttribute?.("aria-label"), "Acme Hub");
  show([FIRST(), FULL()]);
  mock.timers.tick(20);
  assert.equal(page.doc.activeElement, row("Acme Hub").querySelector(".s-toggle"));
});

// --- The menu of a row ----------------------------------------------------------------------------------------------------------

const closeMenu = () => page.doc.dispatchEvent(new Ev("keydown", { key: "Escape" }));
const menuOf = (name) => {
  click(cells(row(name)).more);
  const out = entries();
  closeMenu();
  return out;
};
// An entry of the menu of a row, chosen
const choose = (name, label) => {
  click(cells(row(name)).more);
  press(menu().querySelectorAll(".menu-item").find((el) => text(el.querySelector("span")) === label));
};

test("the menu of a catalog that is current: check again, stop checking, open and copy its address, go back, remove, in that order", () => {
  show([FIRST()]);
  assert.deepEqual(menuOf("Gravitee public"), [["Check again", ""], ["Stop checking automatically", ""], ["Open repository", ""], ["Copy address", ""],
    ["Go back to previous version", "No previous version"], ["Remove", ""]]);
  click(cells(row("Gravitee public")).more);
  assert.equal(menu().querySelectorAll(".menu-sep").length, 1, "a separator before Remove");
  assert.ok(menu().querySelectorAll(".menu-item").pop().classList.contains("danger"), "Remove is the danger one");
  assert.equal(menu().querySelector("a.menu-item").getAttribute("href"), `https://${ADDRESS}`);
  assert.equal(menu().querySelector("a.menu-item").getAttribute("target"), "_blank");
  closeMenu();
});

test("the menu offers what the row needs: Repair when damaged, the allowance for a catalog that is not listed with items that use this machine, Check automatically when it is off", () => {
  show([GIT("damaged", { auto: false, uses_machine: USES, has_previous: true, held: { update: "", remove: "", undo: "" } })]);
  assert.deepEqual(menuOf("Acme Hub").map(([label]) => label), ["Check again", "Check automatically", "Allow use of this machine", "Open repository", "Copy address",
    "Go back to previous version", "Repair", "Remove"]);
  // A listed catalog has the allowance by being listed: there is nothing to allow
  show([FIRST({ uses_machine: USES, host: "listed" })]);
  assert.ok(!menuOf("Gravitee public").some(([label]) => /allow/i.test(label)));
  show([GIT("uptodate", { host: "allowed", uses_machine: USES })]);
  assert.ok(menuOf("Acme Hub").some(([label]) => label === "Stop allowing use of this machine"));
});

test("a catalog with no copy offers Try again in the place of Check again, and waits while it downloads", () => {
  show([GIT("unreachable", { commit: "" })]);
  assert.deepEqual(menuOf("Acme Hub").slice(0, 2), [["Try again", ""], ["Stop checking automatically", ""]]);
  show([GIT("downloading", { commit: "", progress: 0.3 })]);
  assert.deepEqual(menuOf("Acme Hub")[0], ["Try again", "Downloading"]);
});

test("a folder has its address to copy and its link to remove; the repository link needs an https address", () => {
  show([FOLDER()]);
  assert.deepEqual(menuOf("My catalog"), [["Copy address", ""], ["Remove", ""]]);
  show([FIRST({ web_url: "http://example.org/x" }), GIT("uptodate", { id: "other-hub", name: "Other hub", web_url: "" })]);
  assert.ok(!menuOf("Gravitee public").some(([label]) => label === "Open repository"), "http is not offered");
  assert.ok(!menuOf("Other hub").some(([label]) => label === "Open repository"));
});

test("Personal and a catalog that is only offered have no menu", () => {
  show([PERSONAL(), NOT_ADDED()]);
  assert.equal(cells(row("Personal")).more, null);
  assert.equal(cells(row("Gravitee public")).more, null);
});

test("an entry that cannot be used says why: what holds the update, the removal or the way back, what the Lab is waiting for", () => {
  show([GIT("uptodate", { has_previous: true, held: { update: "Turn off Acme Bakery first", remove: "Turn off Acme Bakery first", undo: "Wait for Start of Acme Bakery" } })]);
  assert.deepEqual(menuOf("Acme Hub").slice(-2), [["Go back to previous version", "Wait for Start of Acme Bakery"], ["Remove", "Turn off Acme Bakery first"]]);
  // With a way back, nothing holds it
  show([GIT("uptodate", { has_previous: true, held: { update: "", remove: "", undo: "No previous version" } })]);
  assert.deepEqual(menuOf("Acme Hub").slice(-2), [["Go back to previous version", ""], ["Remove", ""]]);
  // The allowance is held while an item that uses this machine is on
  const k3s = (more) => item("addon", "@acme-hub/k3s", "Kubernetes", more);
  show([GIT("uptodate", { host: "allowed", uses_machine: USES })], { addons: [k3s({ on: true, up: true })] });
  assert.deepEqual(menuOf("Acme Hub").find(([label]) => /allowing/.test(label)), ["Stop allowing use of this machine", "Stop Kubernetes first"]);
  show([GIT("uptodate", { host: "allowed", uses_machine: USES })], { addons: [k3s({ on: true, up: false })] });
  assert.equal(menuOf("Acme Hub").find(([label]) => /allowing/.test(label))[1], "Turn off Kubernetes first");
  show([GIT("uptodate", { host: "allowed", uses_machine: USES })], { addons: [k3s({})] });
  assert.equal(menuOf("Acme Hub").find(([label]) => /allowing/.test(label))[1], "");
  // The Lab UI does not answer: nothing is asked of it
  store.offline = true;
  show([FIRST()]);
  assert.deepEqual(menuOf("Gravitee public").filter(([, why]) => why).map(([label, why]) => [label, why]),
    [["Check again", "Lab UI offline"], ["Stop checking automatically", "Lab UI offline"], ["Go back to previous version", "Lab UI offline"], ["Remove", "Lab UI offline"]]);
  store.offline = false;
  // Without Git nothing can be fetched
  show([FIRST(), GIT("unreachable", { id: "later", name: "Later", commit: "" })], { git: { available: false, version: "" } });
  assert.deepEqual(menuOf("Gravitee public")[0], ["Check again", "Git is not installed"]);
  assert.deepEqual(menuOf("Later")[0], ["Try again", "Git is not installed"]);
  const retry = row("Later").querySelector("[data-retry]");
  assert.deepEqual([isOff(retry), offWhy(retry)], [true, "Git is not installed"]);
});

test("Check again asks the address and tells the word of the answer; Stop checking automatically and Check automatically turn the checks off and on", async () => {
  server.calls.length = 0;
  server.answers["/api/catalogs/acme-hub/check"] = { catalog: GIT("update") };
  server.answers["/api/catalogs/acme-hub/auto"] = { catalog: GIT("uptodate", { auto: false }) };
  show([GIT("uptodate")]);
  clearToasts();
  choose("Acme Hub", "Check again");
  await settle();
  assert.deepEqual([last().path, last().body], ["/api/catalogs/acme-hub/check", {}]);
  assert.deepEqual(toasts(), ["Update available"]);
  choose("Acme Hub", "Stop checking automatically");
  await settle();
  assert.deepEqual([last().path, last().body], ["/api/catalogs/acme-hub/auto", { enabled: false }]);
  show([GIT("uptodate", { auto: false })]);
  choose("Acme Hub", "Check automatically");
  await settle();
  assert.deepEqual(last().body, { enabled: true });
});

test("Check again says the word of an answer that is only that, and the reason and next step of a state that is a trouble, as an error", async () => {
  const checked = async (key) => {
    server.answers["/api/catalogs/acme-hub/check"] = { catalog: GIT(key) };
    show([GIT("uptodate")]);
    clearToasts();
    choose("Acme Hub", "Check again");
    await settle();
    return [toasts(), page.doc.querySelector(".toast")?.classList.contains("error") ?? null];
  };
  assert.deepEqual(await checked("uptodate"), [["Up to date"], false]);
  assert.deepEqual(await checked("update"), [["Update available"], false]);
  assert.deepEqual(await checked("downloading"), [["Downloading"], false]);
  assert.deepEqual(await checked("unreachable"), [["Cannot reach github.com. Check your connection, then try again."], true]);
  assert.deepEqual(await checked("noaccess"), [["No access to owner/repo. Run gh auth login in a terminal, then try again."], true]);
  assert.deepEqual(await checked("rewritten"), [["This catalog was rewritten or moved. Update only if you expected it."], true]);
  assert.deepEqual(await checked("lab_old"), [["This version is too old for this Lab. Update it, or ask the owners of Acme Hub to update it."], true]);
});

test("a refusal of a request on a row is said as an error toast in the server's words", async () => {
  server.refuse["/api/catalogs/acme-hub/check"] = "Cannot reach github.com. Check your connection, then try again.";
  show([GIT("uptodate")]);
  clearToasts();
  choose("Acme Hub", "Check again");
  await flush();
  assert.deepEqual(toasts(), ["Cannot reach github.com. Check your connection, then try again."]);
  assert.ok(page.doc.querySelector(".toast").classList.contains("error"));
  delete server.refuse["/api/catalogs/acme-hub/check"];
});

test("Repair rewrites what was damaged and says Repaired; Copy address copies the full address", async () => {
  server.answers["/api/catalogs/acme-hub/repair"] = { catalog: GIT("uptodate") };
  const copied = [];
  globalThis.navigator.clipboard = { writeText: async (value) => { copied.push(value); } };
  show([GIT("damaged")]);
  clearToasts();
  choose("Acme Hub", "Repair");
  await flush();
  assert.deepEqual([last().path, toasts()], ["/api/catalogs/acme-hub/repair", ["Repaired"]]);
  clearToasts();
  choose("Acme Hub", "Copy address");
  await flush();
  assert.deepEqual([copied, toasts()], [["https://github.com/acme/lab-hub.git"], ["Copied"]]);
  show([FOLDER()]);
  click(cells(row("My catalog")).more);
  press(menu().querySelectorAll(".menu-item")[0]);
  await flush();
  assert.equal(copied[1], "~/lab/my-catalog");
});

// --- Add catalog: the address ---------------------------------------------------------------------------------------------------

// What the server answers to the check of an address
const PREVIEW = (more = {}) => ({ ticket: "9f3a4c1d2e5b6a70", expires: 600,
  catalog: { id: "acme-hub", name: "Acme Hub", description: "Banking demos for partner workshops", version: "1.4.0", lab: 1, kind: "git" },
  address: "github.com/acme/lab-hub", url: "https://github.com/acme/lab-hub.git", audience: "private", counts: { playground: 14, setup: 2, addon: 9 },
  rows: { builds: { local: 3, remote: ["github.com/acme/app"] }, ports: [8140, 8141], core: ["API Management gateway"], reads: ["license"],
    uses_machine: [{ id: "kubernetes", name: "Kubernetes", what: ["runs privileged"], why: "Runs containerd and the kubelet" }],
    needs: [{ id: "partner-bank", name: "Partner Bank", url: "https://example.org/partner/lab-bank.git", min: "2.0.0" }] },
  blocked: [{ kind: "addon", id: "@acme-hub/x", name: "X", reasons: ["Mounts the Docker socket"] }], ignored: 3, ...more });
const BARE = (more = {}) => PREVIEW({ counts: { playground: 1, setup: 1, addon: 1 }, rows: {}, blocked: [], audience: "public", ...more });

const adding = () => page.doc.querySelector("#catalog-title").closest("dialog");
const heading = () => text(page.doc.querySelector("#catalog-title"));
const onScreen = () => (adding().querySelector("#catalog-address").hidden ? "effects" : "address");
const problemOf = () => (adding().querySelector(".create-problem").hidden ? "" : text(adding().querySelector(".create-problem")));
const primary = () => adding().querySelector("#catalog-primary button");
const secondary = () => adding().querySelector("#catalog-secondary button");
const addressInput = () => adding().querySelector("#catalog-input");
const rowsOf = () => adding().querySelectorAll("#catalog-rows .choice-row").map((r) => [text(r.querySelector(".choice-row-label")), words(r.querySelector(".choice-items") || r.querySelector(".choice-summary"))]);
const hold = () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  return { held, release };
};
const settle = async () => {
  for (let i = 0; i < 10; i++) await flush();
  mock.timers.tick(700);   // a button stays on its working face 600 ms at least; the toasts last longer
  for (let i = 0; i < 3; i++) await flush();
  paintActions();          // ... and the page draws every button again once the state has changed (main.js)
};
// The dialog of Add closed, whatever a test left of it
const shut = () => { if (adding().hasAttribute("open")) adding().close(); };
const next = async (el) => {
  el.closest("form").dispatchEvent(new Ev("submit"));
  await settle();
};

test("Add catalog opens on the address, empty, with its first field focused: its mark says what an address is, written with the host of the first catalog", () => {
  show([FIRST()]);
  server.calls.length = 0;
  click(section.querySelectorAll(".page-actions .btn")[1]);
  assert.equal(adding().hasAttribute("open"), true);
  assert.deepEqual([heading(), onScreen(), addressInput().value, page.doc.activeElement === addressInput()], ["Add catalog", "address", "", true]);
  assert.equal(addressInput().getAttribute("placeholder"), "owner/repo, a Git address or a folder");
  const label = adding().querySelector("#catalog-field .field-label");
  assert.equal(text(label.querySelector("label")), "Address");
  assert.deepEqual(bubble(label.querySelector(".tip")), ["A Git address you can read, such as owner/repo, https://github.com/owner/repo or git@github.com:owner/repo.git, or a folder on this machine. Private catalogs use the Git sign-in of this machine."]);
  // More options hold the one field, the branch
  const more = adding().querySelector("#catalog-more");
  assert.deepEqual([text(more.querySelector("summary")), more.open, text(more.querySelector("label")), more.querySelector("input").getAttribute("placeholder")],
    ["More options", false, "Branch", "Default"]);
  assert.deepEqual(adding().querySelectorAll(".dialog-actions .btn").map(text), ["Cancel", "Continue"]);
  assert.equal(server.calls.length, 0, "nothing is asked of the server by opening it");
  shut();
});

test("the examples of an address are written with the host of the first catalog, never with a host of the page's own; with none, only owner/repo", () => {
  show([FIRST({ address: "git.example.org/team/catalog" })]);
  openAdd();
  assert.ok(bubble(adding().querySelector("#catalog-field .tip"))[0].includes("https://git.example.org/owner/repo or git@git.example.org:owner/repo.git"));
  shut();
  show([]);
  openAdd();
  assert.equal(bubble(adding().querySelector("#catalog-field .tip"))[0], "A Git address you can read, such as owner/repo or a folder on this machine. Private catalogs use the Git sign-in of this machine.");
  shut();
});

test("Continue waits for an address Git can read, and says why while it does not", () => {
  show([FIRST()]);
  openAdd();
  const go = () => adding().querySelector("#catalog-go");
  assert.deepEqual([text(go()), isOff(go()), offWhy(go())], ["Continue", true, "Enter an address"]);
  type(addressInput(), "has a space");
  assert.deepEqual([isOff(go()), offWhy(go())], [true, "This is not an address Git can read"]);
  type(addressInput(), "bad\u0007address");
  assert.equal(offWhy(go()), "This is not an address Git can read");
  type(addressInput(), "   ");
  assert.equal(offWhy(go()), "Enter an address");
  type(addressInput(), "acme/lab-hub");
  assert.equal(isOff(go()), false);
  // Nothing is sent while it waits
  server.calls.length = 0;
  type(addressInput(), "has a space");
  adding().querySelector("form").dispatchEvent(new Ev("submit"));
  assert.equal(server.calls.length, 0);
  shut();
});

test("Continue at work reads Reading, the bar of the fetch is under the field, and Cancel and Escape stay live: closing drops the answer", async () => {
  show([FIRST()]);
  clearToasts();
  openAdd();
  const wait = hold();
  server.answers["/api/catalogs/check"] = () => wait.held.then(() => PREVIEW());
  type(addressInput(), "acme/lab-hub");
  server.calls.length = 0;
  await next(addressInput());
  assert.deepEqual([last().path, last().body], ["/api/catalogs/check", { address: "acme/lab-hub" }]);
  const reading = primary();
  assert.deepEqual([text(reading), isOff(reading), offWhy(reading)], ["Reading", true, "Reading the catalog"]);
  assert.equal(adding().querySelector("[data-progress]").getAttribute("data-progress"), "catalog:check");
  // A second Enter is the same request
  await next(addressInput());
  assert.equal(calls("/api/catalogs/check").length, 1);
  // Nothing traps the person: Cancel is not disabled and Escape is not refused
  const cancel = adding().querySelector("[data-cancel]");
  assert.equal(cancel.disabled, false);
  const escape = new Ev("cancel");
  adding().dispatchEvent(escape);
  assert.equal(escape.defaultPrevented, false);
  click(cancel);
  assert.equal(adding().hasAttribute("open"), false);
  wait.release();
  await settle();
  assert.equal(adding().hasAttribute("open"), false, "the answer that came after is dropped");
  assert.deepEqual(toasts(), []);
});

test("Import to Personal keeps the playground as the person's own, says what it replaces there, and opens its page; what cannot run goes to Imported only", async () => {
  show([FIRST()]);
  openImport();
  const drop = async (file) => {
    adding().querySelector("#catalog-file").dispatchEvent(Object.assign(new Ev("drop"), { dataTransfer: { files: [file] }, preventDefault() {} }));
    await settle();
  };
  server.answers["/api/catalogs/import"] = PREVIEW({ catalog: { id: "contoso", name: "Contoso demo", version: "1.0.0", lab: 1, kind: "folder" },
    imported: true, counts: { playground: 1, setup: 0, addon: 1 }, blocked: [], rows: {}, personal: { replaces: ["Kiosk"], same: [] } });
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  assert.deepEqual(rowsOf().find(([label]) => label === "Replaces"), ["Replaces", "Kiosk The new version gets your settings"]);
  server.answers["/api/catalogs/import/personal"] = { items: [], playground: "local/contoso" };
  const went = [];
  const go = router.go;
  router.go = (to) => went.push(to);
  await submit(primary());
  await settle();
  router.go = go;
  assert.deepEqual([last().path, last().body], ["/api/catalogs/import/personal", { ticket: "9f3a4c1d2e5b6a70" }]);
  assert.deepEqual(went, ["#/playgrounds/local/contoso"]);
  // What cannot run is never Personal's: Imported is the one choice, and Personal says why
  openImport();
  server.answers["/api/catalogs/import"] = PREVIEW({ catalog: { id: "contoso", name: "Contoso demo", version: "1.0.0", lab: 1, kind: "folder" },
    imported: true, counts: { playground: 1, setup: 0, addon: 1 }, blocked: [{ name: "Kiosk", reasons: ["Runs with full privileges"] }], rows: {},
    personal: { replaces: [], same: [] } });
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  const personal = adding().querySelector('[data-dest="personal"]');
  assert.deepEqual([personal.getAttribute("aria-disabled"), personal.getAttribute("data-why")], ["true", "What cannot run stays in Imported"]);
  assert.equal(adding().querySelector('[data-dest="imported"]').getAttribute("aria-checked"), "true");
  adding().closest("dialog").close();
});

test("Import to Personal of what uses this machine is the person's allowance: the button says Allow and import, and the request carries it", async () => {
  show([FIRST()]);
  openImport();
  const drop = async (file) => {
    adding().querySelector("#catalog-file").dispatchEvent(Object.assign(new Ev("drop"), { dataTransfer: { files: [file] }, preventDefault() {} }));
    await settle();
  };
  server.answers["/api/catalogs/import"] = PREVIEW({ catalog: { id: "contoso", name: "Contoso demo", version: "1.0.0", lab: 1, kind: "folder" },
    imported: true, counts: { playground: 1, setup: 0, addon: 0 }, blocked: [],
    rows: { uses_machine: [{ id: "@contoso/kiosk", name: "kiosk", service: "kiosk", what: ["runs privileged"], why: "Runs a service manager" }] },
    personal: { replaces: [], same: [] } });
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  assert.equal(face(primary()), "Allow and import");
  click(adding().querySelector('[data-dest="imported"]'));
  assert.equal(face(primary()), "Import", "Imported asks with its own choice");
  click(adding().querySelector('[data-dest="personal"]'));
  assert.equal(face(primary()), "Allow and import");
  server.answers["/api/catalogs/import/personal"] = { items: [], playground: "local/contoso" };
  const go = router.go;
  router.go = () => {};
  await submit(primary());
  await settle();
  router.go = go;
  assert.deepEqual([last().path, last().body], ["/api/catalogs/import/personal", { ticket: "9f3a4c1d2e5b6a70", allow: true }]);
});

test("Settings kept from a removed import of that name are said, used or not, and what Personal does not take is said once imported", async () => {
  show([FIRST()]);
  clearToasts();
  openImport();
  const drop = async (file) => {
    adding().querySelector("#catalog-file").dispatchEvent(Object.assign(new Ev("drop"), { dataTransfer: { files: [file] }, preventDefault() {} }));
    await settle();
  };
  const preview = (used) => PREVIEW({ catalog: { id: "contoso", name: "Contoso demo", version: "1.0.0", lab: 1, kind: "folder" },
    imported: true, counts: { playground: 1, setup: 0, addon: 0 }, blocked: [], rows: {}, personal: { replaces: [], same: [] },
    kept: { used, where: ".lab/kept-settings/contoso-1a2b3c4d" } });
  server.answers["/api/catalogs/import"] = { ...preview(false), warning: "A change that stopped midway had set kiosk aside: they are in Personal again." };
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  assert.deepEqual(rowsOf().find(([label]) => label === "Kept settings"), ["Kept settings", "From an earlier import Not used by this file"]);
  assert.deepEqual(toasts(), ["A change that stopped midway had set kiosk aside: they are in Personal again."], "what came back is said");
  clearToasts();
  click(adding().querySelector('[data-dest="imported"]'));
  assert.deepEqual(rowsOf().find(([label]) => label === "Kept settings"), ["Kept settings", "From an earlier import Not used by this file"]);
  adding().closest("dialog").close();
  openImport();
  server.answers["/api/catalogs/import"] = preview(true);
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  assert.deepEqual(rowsOf().find(([label]) => label === "Kept settings"), ["Kept settings", "From an earlier import Used"]);
  server.answers["/api/catalogs/import/personal"] = { items: [], playground: "local/contoso",
    kept: { items: ["Kiosk"], where: ".lab/kept-settings/contoso-1a2b3c4d" } };
  const go = router.go;
  router.go = () => {};
  await submit(primary());
  await settle();
  router.go = go;
  assert.deepEqual(toasts(), ["Imported. The kept settings of Kiosk stay apart: see Kept settings on the Catalogs page."]);
});

test("Import takes the file of an exported playground, shows the preview of an Add from that file, and opens the playground once imported", async () => {
  show([FIRST()]);
  clearToasts();
  openImport();
  const screen = () => adding().querySelector("#catalog-file");
  const drop = async (file) => {
    screen().dispatchEvent(Object.assign(new Ev("drop"), { dataTransfer: { files: [file] }, preventDefault() {} }));
    await settle();
  };
  assert.deepEqual([heading(), screen().hidden, primary() ?? null], ["Import playground", false, null], "the file comes first, and nothing to confirm yet");
  server.calls.length = 0;
  await drop(new File(["x"], "notes.txt"));
  assert.deepEqual([problemOf(), server.calls.length], ["Choose the .zip file of an exported playground", 0], "what is no archive is not sent");
  server.answers["/api/catalogs/import"] = PREVIEW({ catalog: { id: "contoso", name: "Contoso demo", version: "1.0.0", lab: 1, kind: "folder" },
    imported: true, address: "~/lab/.lab/imports/contoso", counts: { playground: 1, setup: 0, addon: 1 }, blocked: [], rows: {},
    reused: [{ name: "Ledger", catalog: "Partner" }] });
  await drop(new File([new Uint8Array([80, 75, 5, 6])], "contoso.zip"));
  assert.deepEqual([last().path, last().body], ["/api/catalogs/import", { data: btoa("PK\u0005\u0006") }]);
  assert.deepEqual([heading(), face(primary())], ["Import Contoso demo", "Import"]);
  assert.deepEqual(rowsOf().find(([label]) => label === "From"), ["From", "contoso.zip"], "it comes from the file, not from where the Lab keeps it");
  assert.deepEqual(rowsOf().find(([label]) => label === "Uses yours"), ["Uses yours", "Ledger From Partner"], "what this Lab has already is not a second copy");
  const dest = (value) => adding().querySelector(`[data-dest="${value}"]`);
  assert.deepEqual([dest("personal").getAttribute("aria-checked"), dest("imported").getAttribute("aria-checked")], ["true", "false"], "Personal first");
  dest("imported").click();
  assert.equal(dest("imported").getAttribute("aria-checked"), "true");
  server.answers["/api/catalogs"] = { catalog: { id: "contoso", host: null } };
  const went = [];
  const go = router.go;
  router.go = (to) => went.push(to);
  store.state.playgrounds = [{ kind: "playground", id: "@contoso/contoso", name: "Contoso demo" }];
  await submit(primary());
  await settle();
  router.go = go;
  assert.deepEqual([last().path, last().body], ["/api/catalogs", { ticket: "9f3a4c1d2e5b6a70" }]);
  assert.deepEqual(toasts(), ["Imported"]);
  assert.deepEqual(went, ["#/playgrounds/@contoso/contoso"], "its page opens");
  // A file this Lab has: said on the file's screen, in the server's words
  openImport();
  server.answers["/api/catalogs/import"] = () => { throw Object.assign(new Error("Contoso demo is already in this Lab."), { code: "already_imported" }); };
  server.refuse["/api/catalogs/import"] = "Contoso demo is already in this Lab. To import it again, remove it on the Catalogs page first.";
  await drop(new File([new Uint8Array([80, 75])], "contoso.zip"));
  assert.equal(screen().hidden, false);
  assert.equal(problemOf(), "Contoso demo is already in this Lab. To import it again, remove it on the Catalogs page first.");
  delete server.refuse["/api/catalogs/import"];
  shut();
});

test("a branch typed under More options goes with the address", async () => {
  show([FIRST()]);
  openAdd();
  server.answers["/api/catalogs/check"] = PREVIEW();
  type(addressInput(), "acme/lab-hub");
  adding().querySelector("#catalog-branch").value = " release ";
  await next(addressInput());
  assert.deepEqual(last().body, { address: "acme/lab-hub", branch: "release" });
  shut();
});

test("a refusal is said in the problem line in the server's words, the screen stays, and typing takes it away", async () => {
  show([FIRST()]);
  openAdd();
  server.refuse["/api/catalogs/check"] = "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder.";
  type(addressInput(), "ftp://nope");
  await next(addressInput());
  assert.deepEqual([onScreen(), problemOf(), heading()], ["address", "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder.", "Add catalog"]);
  assert.equal(adding().querySelector("#catalog-go").getAttribute("aria-disabled"), null, "Continue is back: the address can be mended");
  assert.equal(addressInput().value, "ftp://nope");
  type(addressInput(), "acme/lab-hub");
  assert.equal(problemOf(), "");
  delete server.refuse["/api/catalogs/check"];
  shut();
});

// --- Add catalog: what it adds --------------------------------------------------------------------------------------------------

// Screen 2, from the address: typed, checked
async function previewed(preview, host = false) {
  show([FIRST()]);
  server.answers["/api/catalogs/check"] = preview;
  openAdd({});
  type(addressInput(), "acme/lab-hub");
  await next(addressInput());
}

test("the second screen is Add and the name, one row for what the preview says in their order, and the button Add that is neutral", async () => {
  await previewed(PREVIEW());
  assert.deepEqual([heading(), onScreen(), problemOf()], ["Add Acme Hub", "effects", ""]);
  assert.deepEqual(rowsOf(), [
    ["Adds", "14 playgrounds 2 setups 9 add-ons"],
    ["From", "github.com/acme/lab-hub Private"],
    ["Runs", "Containers on this machine"],
    ["Uses this machine", "1 item"],
    ["Builds", "3 images from source Builds from github.com/acme/app"],
    ["Publishes", "Ports 8140 and 8141"],
    ["Changes", "API Management gateway"],
    ["Reads", "Your Gravitee license"],
    ["Needs", "Partner Bank Not added"],
    ["Cannot run", "2 items"],
  ]);
  const button = primary();
  assert.deepEqual([face(button), button.classList.contains("primary"), button.classList.contains("danger"), isOff(button)], ["Add", false, false, false]);
  assert.deepEqual(adding().querySelectorAll(".dialog-actions .btn").map(face), ["Cancel", "Add"], "Cancel and Add");
  assert.equal(secondary(), null);
  shut();
});

test("the marks of the second screen say what Runs means, what Uses this machine takes and why, and what cannot run", async () => {
  await previewed(PREVIEW());
  const tip = (label) => adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === label).querySelector(".tip");
  assert.deepEqual(bubble(tip("Runs")), ["Its containers, builds and seeds run with this machine's Docker access."]);
  assert.deepEqual(bubble(tip("Uses this machine")), ["Kubernetes", "Runs privileged", "Runs containerd and the kubelet"]);
  // Under Keep them blocked the items that use this machine cannot run either: the Blocked one, and Kubernetes
  assert.deepEqual(bubble(tip("Cannot run")), ["X Mounts the Docker socket", "Kubernetes Runs privileged"]);
  shut();
});

test("the choice of two is there only for items that use this machine: Keep them blocked is the default, and choosing to allow takes them off Cannot run", async () => {
  await previewed(PREVIEW());
  const choice = adding().querySelector("#catalog-choice");
  assert.equal(choice.hidden, false);
  assert.deepEqual(choice.querySelectorAll(".choice").map((c) => [text(c.querySelector("strong")), text(c.querySelector(".sub")), c.querySelector("input").checked]),
    [["Keep them blocked", "They are listed and cannot start", true], ["Allow them to use this machine", "Privileged containers, the Docker socket or folders of this machine", false]]);
  assert.equal(page.doc.activeElement, choice.querySelector("input"), "with a choice, the keyboard is on it");
  assert.equal(rowsOf().pop()[1], "2 items");
  const [keep, allow] = choice.querySelectorAll("input");
  keep.checked = false;
  allow.checked = true;
  allow.dispatchEvent(new Ev("change", { bubbles: true }));
  assert.equal(rowsOf().pop()[1], "1 item", "the Blocked one alone");
  keep.checked = true;
  allow.checked = false;
  keep.dispatchEvent(new Ev("change", { bubbles: true }));
  assert.equal(rowsOf().pop()[1], "2 items");
  shut();
  // No such item: no choice, and nothing that cannot run
  await previewed(BARE());
  assert.equal(adding().querySelector("#catalog-choice").hidden, true);
  assert.equal(adding().querySelector("#catalog-choice").querySelectorAll("input").length, 0);
  assert.equal(page.doc.activeElement, primary(), "with no choice, the keyboard is on Add: Enter confirms it");
  shut();
});

test("an empty preview has only the rows it has: singular counts, where it is from, what it runs", async () => {
  await previewed(BARE());
  assert.deepEqual(rowsOf(), [["Adds", "1 playground 1 setup 1 add-on"], ["From", "github.com/acme/lab-hub Public"], ["Runs", "Containers on this machine"]]);
  shut();
  await previewed(BARE({ catalog: { id: "mine", name: "Mine", kind: "folder", version: "" }, address: "~/lab/mine", audience: "", counts: { playground: 0, setup: 0, addon: 3 } }));
  assert.deepEqual(rowsOf().slice(0, 2), [["Adds", "3 add-ons"], ["From", "~/lab/mine On this machine"]], "a folder is on this machine; a kind with none is not said");
  shut();
});

test("the ports are said as the design words them", async () => {
  const ports = async (list) => {
    await previewed(BARE({ rows: { ports: list } }));
    const said = rowsOf().find(([label]) => label === "Publishes")?.[1];
    shut();
    return said;
  };
  assert.equal(await ports([8140]), "Port 8140");
  assert.equal(await ports([8140, 8141, 8142]), "Ports 8140, 8141 and 8142");
  assert.equal(await ports([1, 2, 3, 4, 5, 6]), "Ports 1, 2, 3, 4, 5 and 6");
  assert.equal(await ports([1, 2, 3, 4, 5, 6, 7, 8, 9]), "9 ports");
  assert.equal(await ports([]), undefined);
});

test("a version older than the newest, the settings found and the catalogs needed are rows of their own", async () => {
  await previewed(BARE({ newer: true, rows: { found: [{ id: "ledger", name: "Ledger" }], needs: [{ id: "p", name: "Partner Bank" }], reads: ["gateway token", "ACME_SETTING"] } }));
  assert.deepEqual(rowsOf().slice(2), [["Version", "1.4.0 Newer ones need a newer Gravitee Lab"], ["Runs", "Containers on this machine"], ["Reads", "Your Gravitee Cloud gateway token ACME_SETTING"],
    ["Needs", "Partner Bank Not added"], ["Settings found", "Ledger From before the move"]]);
  shut();
});

test("the names a catalog gave to its shared settings are shown as they are; the words of the surface start a sentence", async () => {
  await previewed(BARE({ rows: { reads: ["license", "gateway token", "acme_setting", "lowercase name"] } }));
  assert.equal(rowsOf().find(([label]) => label === "Reads")[1], "Your Gravitee license Your Gravitee Cloud gateway token acme_setting lowercase name");
  shut();
});

test("a word that is the name of something in the language is a word like another: no table of the page has it", async () => {
  await previewed(BARE({ rows: { reads: ["constructor", "toString", "__proto__"] } }));
  assert.equal(rowsOf().find(([label]) => label === "Reads")[1], "constructor toString __proto__");
  shut();
  await updating(PLAN({ changes: [{ id: "x", name: "Acme Bakery", fact: "constructor", value: "v" }, { id: "y", name: "Mock bank", fact: "reads", value: "toString" }] }));
  const changes = adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "Changes");
  assert.deepEqual(changes.querySelectorAll(".choice-lines li").map(text), ["Acme Bakery: constructor v", "Mock bank now reads toString"]);
  server.answers[at("/changes")] = { files: [{ path: "a.yml", status: "constructor", diff: null }] };
  click(secondary());
  await settle();
  assert.deepEqual(dialogOf("Changes in Acme Hub").querySelectorAll(".file-item .muted").map(text), ["constructor"]);
  click(dialogOf("Changes in Acme Hub").querySelector("[data-close]"));
  shut();
});

test("an item that is Blocked and also uses this machine counts once under Cannot run, whichever way the two lists spell its id", async () => {
  const k3s = { kind: "addon", id: "@acme-hub/k3s", name: "Kubernetes", reasons: ["Mounts the Docker socket"] };
  await previewed(PREVIEW({ blocked: [k3s] }));
  assert.equal(rowsOf().pop()[1], "1 item", "Kubernetes once, though it is `kubernetes` in one list and `@acme-hub/k3s` in the other");
  const [keep, allow] = adding().querySelectorAll("#catalog-choice input");
  keep.checked = false;
  allow.checked = true;
  allow.dispatchEvent(new Ev("change", { bubbles: true }));
  assert.ok(!rowsOf().some(([label]) => label === "Cannot run"), "allowed, it is no longer one that cannot run");
  shut();
  await updating(PLAN({ uses_machine: [{ id: "kubernetes", name: "Kubernetes", what: ["runs privileged"] }], blocked: [k3s] }));
  assert.equal(rowsOf().pop()[0], "Cannot run");
  assert.equal(adding().querySelectorAll("#catalog-rows .choice-row").pop().querySelectorAll(".step-items li").length, 1);
  shut();
});

test("more than four lines fold under a line that counts them", async () => {
  await previewed(BARE({ rows: { core: ["One", "Two", "Three", "Four", "Five"] } }));
  const folded = adding().querySelectorAll("#catalog-rows details.choice-row");
  assert.equal(folded.length, 1);
  assert.deepEqual([text(folded[0].querySelector(".choice-row-label")), text(folded[0].querySelector(".choice-summary")), folded[0].querySelectorAll(".choice-detail li").length], ["Changes", "5 items", 5]);
  shut();
});

test("the items of a plan are a list, every one of them on its line with its kind, under the hint of the row, however many they are", async () => {
  // Removes says "No longer in the catalog" of its names
  await updating(PLAN({ removes: ["One", "Two", "Three", "Four", "Five"].map((name, i) => ({ kind: i ? "addon" : "playground", name })), breaks: [], changes: [], blocked: [], updates: [], adds: [], whatsnew: [] }));
  assert.equal(adding().querySelector("#catalog-rows details.choice-row"), null, "nothing of a plan is folded away");
  const removes = adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "Removes");
  assert.deepEqual([text(removes.querySelector(".choice-row-label")), text(removes.querySelector(".choice-items > .muted"))], ["Removes", "No longer in the catalog"]);
  assert.deepEqual(removes.querySelectorAll(".step-items li").map((li) => [text(li.querySelector(".step-name")), text(li.querySelector(".step-kind"))]),
    [["One", "Playground"], ["Two", "Add-on"], ["Three", "Add-on"], ["Four", "Add-on"], ["Five", "Add-on"]]);
  shut();
});

test("Add adds the catalog the preview showed: the ticket goes, the dialog closes, the toast says Added, and the page shows the row", async () => {
  clearToasts();
  await previewed(PREVIEW());
  server.answers["/api/catalogs"] = { catalog: GIT("uptodate") };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs", { ticket: "9f3a4c1d2e5b6a70" }]]);
  assert.equal(adding().hasAttribute("open"), false);
  assert.deepEqual(toasts(), ["Added"]);
});

test("choosing to allow adds the allowance once the catalog is added", async () => {
  clearToasts();
  await previewed(PREVIEW());
  const [keep, allow] = adding().querySelectorAll("#catalog-choice input");
  keep.checked = false;
  allow.checked = true;
  server.answers["/api/catalogs"] = { catalog: GIT("uptodate", { host: "no" }) };
  server.answers["/api/catalogs/acme-hub/allow"] = { catalog: GIT("uptodate", { host: "allowed" }) };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs", { ticket: "9f3a4c1d2e5b6a70" }], ["/api/catalogs/acme-hub/allow", { enabled: true }]]);
});

test("a refusal of the commit stays on the second screen in the problem line; a ticket that expired goes back to the address, which it keeps", async () => {
  await previewed(PREVIEW());
  server.refuse["/api/catalogs"] = "Another catalog already uses the name acme-hub (github.com/other/hub). Remove it first, or ask the owner of this one to rename it.";
  await next(primary());
  assert.deepEqual([onScreen(), problemOf(), adding().hasAttribute("open")], ["effects", server.refuse["/api/catalogs"], true]);
  assert.equal(isOff(primary()), false, "it can be tried again, or canceled");
  server.refuse["/api/catalogs"] = "This took too long. Start again.";
  await next(primary());
  assert.deepEqual([onScreen(), problemOf(), heading(), addressInput().value], ["address", "This took too long. Start again.", "Add catalog", "acme/lab-hub"]);
  delete server.refuse["/api/catalogs"];
  shut();
});

const hashOf = () => page.session.entries[page.session.at].hash;

test("closing the dialog while Add is at work leaves the allowance the person chose ungiven and moves nothing, but the catalog is added and that is said", async () => {
  clearToasts();
  await previewed(PREVIEW());
  const [keep, allow] = adding().querySelectorAll("#catalog-choice input");
  keep.checked = false;
  allow.checked = true;
  const wait = hold();
  server.answers["/api/catalogs"] = () => wait.held.then(() => ({ catalog: GIT("uptodate", { host: "no" }) }));
  server.answers["/api/catalogs/acme-hub/allow"] = { catalog: GIT("uptodate", { host: "allowed" }) };
  router.current = { page: catalogsPage, params: [] };
  const before = hashOf();
  server.calls.length = 0;
  await next(primary());
  click(adding().querySelector("[data-cancel]"));
  assert.equal(adding().hasAttribute("open"), false);
  wait.release();
  await settle();
  assert.deepEqual(server.calls.map((c) => c.path), ["/api/catalogs"], "no allowance: the person walked away from the dialog that asked for it");
  assert.deepEqual(toasts(), ["Added"]);
  assert.equal(hashOf(), before, "the page did not move");
  // Not closing it, the same add gives the allowance and reveals the row
  await previewed(PREVIEW());
  const [keep2, allow2] = adding().querySelectorAll("#catalog-choice input");
  keep2.checked = false;
  allow2.checked = true;
  server.answers["/api/catalogs"] = { catalog: GIT("uptodate", { host: "no" }) };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => c.path), ["/api/catalogs", "/api/catalogs/acme-hub/allow"]);
  assert.equal(hashOf(), "#/catalogs/acme-hub");
  router.current = null;
});

test("a dialog the person opens while an add they left is still at work is not closed by it, and keeps what they typed", async () => {
  await previewed(PREVIEW());
  const wait = hold();
  server.answers["/api/catalogs"] = () => wait.held.then(() => ({ catalog: GIT("uptodate") }));
  router.current = { page: catalogsPage, params: [] };
  await next(primary());
  click(adding().querySelector("[data-cancel]"));
  openAdd();
  type(addressInput(), "other/repo");
  const before = hashOf();
  wait.release();
  await settle();
  assert.deepEqual([adding().hasAttribute("open"), addressInput().value, onScreen()], [true, "other/repo", "address"]);
  assert.equal(hashOf(), before);
  router.current = null;
  shut();
});

test("an add that was left does not unlock the button of the add that replaced it", async () => {
  await previewed(PREVIEW());
  const first = hold(), second = hold(), waits = [first, second];
  server.answers["/api/catalogs"] = () => waits.shift().held.then(() => ({ catalog: GIT("uptodate") }));
  await next(primary());
  click(adding().querySelector("[data-cancel]"));
  await previewed(PREVIEW());
  server.calls.length = 0;
  await next(primary());
  first.release();
  await settle();
  await next(primary());
  assert.equal(calls("/api/catalogs").length, 1, "a second Enter is still the same request");
  second.release();
  await settle();
  shut();
});

test("Enter confirms Add, and Cancel closes it", async () => {
  await previewed(PREVIEW());
  const cancel = adding().querySelector("[data-cancel]");
  click(cancel);
  assert.equal(adding().hasAttribute("open"), false);
  assert.equal(primary() && primary().getAttribute("type") !== "button", true, "its button is the form's own: the one Enter presses");
});

// --- Add catalog: from an address that is known ---------------------------------------------------------------------------------

test("a known address opens the second screen at once: Add and the name, bars in the place of the rows, the button Add reading and waiting", async () => {
  show([FIRST()]);
  const wait = hold();
  server.answers["/api/catalogs/check"] = () => wait.held.then(() => PREVIEW());
  server.calls.length = 0;
  openAdd({ address: "https://example.org/partner/lab-bank.git", name: "Partner Bank" });
  assert.deepEqual([adding().hasAttribute("open"), heading(), onScreen()], [true, "Add Partner Bank", "effects"]);
  assert.equal(adding().querySelectorAll("#catalog-rows .plat-skel").length, 3, "bars");
  assert.equal(adding().querySelector("#catalog-rows").getAttribute("aria-busy"), "true");
  assert.deepEqual([text(primary()), isOff(primary()), offWhy(primary())], ["Reading", true, "Reading the catalog"]);
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs/check", { address: "https://example.org/partner/lab-bank.git" }]], "the address goes at once");
  // Cancel is live while it reads
  assert.equal(adding().querySelector("[data-cancel]").disabled, false);
  wait.release();
  await settle();
  assert.deepEqual([heading(), face(primary()), isOff(primary())], ["Add Acme Hub", "Add", false]);
  assert.equal(adding().querySelectorAll("#catalog-rows .plat-skel").length, 0);
  assert.equal(adding().querySelector("#catalog-rows").getAttribute("aria-busy"), null);
  shut();
});

test("a known address with no name is Add alone until the preview names the catalog; the suggestion's host choice preselects allowing", async () => {
  show([FIRST()]);
  const wait = hold();
  server.answers["/api/catalogs/check"] = () => wait.held.then(() => PREVIEW());
  openAdd({ address: "acme/lab-hub", host: true });
  assert.equal(heading(), "Add");
  wait.release();
  await settle();
  assert.deepEqual(adding().querySelectorAll("#catalog-choice input").map((i) => i.checked), [false, true], "Allow them to use this machine is chosen");
  assert.equal(rowsOf().pop()[1], "1 item");
  shut();
});

test("a known address that cannot be read leaves the person on the address, filled in, with the refusal", async () => {
  show([FIRST()]);
  server.refuse["/api/catalogs/check"] = "No access to partner/lab-bank. Run gh auth login in a terminal, then try again.";
  openAdd({ address: "partner/lab-bank", name: "Partner Bank" });
  await settle();
  assert.deepEqual([onScreen(), heading(), addressInput().value, problemOf()], ["address", "Add catalog", "partner/lab-bank",
    "No access to partner/lab-bank. Run gh auth login in a terminal, then try again."]);
  delete server.refuse["/api/catalogs/check"];
  shut();
});

test("closing the dialog while it reads drops the answer; opening it again starts from the address", async () => {
  show([FIRST()]);
  const wait = hold();
  server.answers["/api/catalogs/check"] = () => wait.held.then(() => PREVIEW());
  openAdd({ address: "acme/lab-hub" });
  shut();
  openAdd();
  wait.release();
  await settle();
  assert.deepEqual([onScreen(), heading(), addressInput().value], ["address", "Add catalog", ""]);
  shut();
});

test("Add on a row that is only offered goes through the preview; a listed one is added with no dialog", async () => {
  server.answers["/api/catalogs/check"] = PREVIEW();
  show([NOT_ADDED({ id: "partner-bank", listed: false, name: "Partner Bank", url: "https://example.org/partner/lab-bank.git" })]);
  server.calls.length = 0;
  click(row("Partner Bank").querySelector("[data-add]"));
  await settle();
  assert.deepEqual([adding().hasAttribute("open"), onScreen(), server.calls[0].body], [true, "effects", { address: "https://example.org/partner/lab-bank.git" }]);
  shut();
  clearToasts();
  server.answers["/api/catalogs"] = { catalog: FIRST({ commit: "9f1c2ab" }) };
  show([NOT_ADDED()]);
  server.calls.length = 0;
  click(row("Gravitee public").querySelector("[data-add]"));
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs", { listed: "gravitee" }]]);
  assert.equal(adding().hasAttribute("open"), false, "no dialog");
  assert.deepEqual(toasts(), ["Added"]);
});

test("a listed catalog that downloads is told Added once it is there, and one that fails says nothing more", async () => {
  clearToasts();
  const later = { id: "later-hub", name: "Later hub" };
  server.answers["/api/catalogs"] = { catalog: FIRST({ ...later, commit: "", progress: 0.05, state: STATES.downloading }) };
  show([NOT_ADDED(later)]);
  click(row("Later hub").querySelector("[data-add]"));
  await settle();
  assert.deepEqual(toasts(), [], "it is on its way");
  show([FIRST({ ...later, commit: "", progress: 0.5, state: STATES.downloading })]);
  store.notify();
  assert.deepEqual(toasts(), []);
  show([FIRST({ ...later, commit: "9f1c2ab" })]);
  store.notify();
  assert.deepEqual(toasts(), ["Added"]);
  store.notify();
  assert.deepEqual(toasts(), ["Added"], "once");
});

test("Add is disabled with the reason when Git is missing", () => {
  show([NOT_ADDED({ id: "third-hub", name: "Third hub" })], { git: { available: false, version: "" } });
  const add = row("Third hub").querySelector("[data-add]");
  assert.deepEqual([isOff(add), offWhy(add)], [true, "Git is not installed"]);
});

// --- Try again and Add the listed catalog, wherever the content status draws them ----------------------------------------------------

test("Try again and Add the listed catalog work from every place the content status is drawn: one handler for the document", async () => {
  clearToasts();
  server.answers["/api/catalogs/acme-hub/check"] = { catalog: GIT("downloading", { commit: "", progress: 0.02 }) };
  show([GIT("unreachable", { commit: "" })]);
  const anywhere = page.doc.createElement("div");
  anywhere.innerHTML = '<button type="button" class="btn" data-content-act="retry" data-catalog="acme-hub">Try again</button><button type="button" class="btn" data-content-act="add" data-catalog="gravitee">Add Gravitee public</button>';
  page.doc.body.append(anywhere);
  server.calls.length = 0;
  press(anywhere.querySelector('[data-content-act="retry"]'));
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs/acme-hub/check", {}]]);
  // The download ends: the person asked for it, and is told once
  show([GIT("uptodate")]);
  store.notify();
  assert.deepEqual(toasts(), ["Downloaded"]);
  clearToasts();
  server.answers["/api/catalogs"] = { catalog: FIRST({ commit: "9f1c2ab" }) };
  server.calls.length = 0;
  press(anywhere.querySelector('[data-content-act="add"]'));
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs", { listed: "gravitee" }]]);
  assert.deepEqual(toasts(), ["Added"]);
  // A button that waits does nothing; one with no catalog to try has nothing to ask
  server.calls.length = 0;
  anywhere.querySelector('[data-content-act="retry"]').setAttribute("aria-disabled", "true");
  press(anywhere.querySelector('[data-content-act="retry"]'));
  await tryAgain("");
  assert.equal(server.calls.length, 0);
  anywhere.remove();
});

test("a Try again the server refuses is an error toast in its words, and the row says nothing more", async () => {
  server.refuse["/api/catalogs/acme-hub/check"] = "Another catalog change is running. Try again in a moment.";
  clearToasts();
  show([GIT("unreachable", { commit: "" })]);
  click(row("Acme Hub").querySelector("[data-retry]"));
  await settle();
  assert.deepEqual(toasts(), ["Another catalog change is running. Try again in a moment."]);
  delete server.refuse["/api/catalogs/acme-hub/check"];
});

// --- Update and Go back ---------------------------------------------------------------------------------------------------------

// What the server answers to the check of an update
const PLAN = (more = {}) => ({ ticket: "b1c2d3e4f5a60718", from: "9f1c2ab", to: "c4d5e6f", version: "1.5.0", release: "1.5.0", rewritten: false,
  updates: [{ kind: "playground", id: "@acme-hub/bank", name: "Acme Bakery" }], adds: [{ kind: "addon", id: "@acme-hub/ledger", name: "Ledger" }],
  removes: [{ kind: "addon", id: "@acme-hub/old", name: "Old" }], breaks: [{ kind: "playground", id: "local/demo", name: "Demo", needs: "Old" }],
  changes: [{ id: "@acme-hub/bank", name: "Acme Bakery", fact: "port", value: "8141" }], whatsnew: ["Add the ledger"],
  blocked: [{ kind: "addon", id: "@acme-hub/x", name: "X", reasons: ["Publishes a port to the network"] }], held: null, ...more });
const PLAIN = (more = {}) => PLAN({ breaks: [], changes: [], blocked: [], removes: [], ...more });

// Each test works on a catalog of its own: a button remembers, for a moment, how the last action on its key went
let hubs = 0, current = "acme-hub";
const hub = () => { current = `hub-${++hubs}`; return current; };
const at = (suffix) => `/api/catalogs/${current}${suffix}`;
async function updating(plan, more = {}, route = "update") {
  show([GIT("update", { id: hub(), ...more })]);
  server.answers[at(`/${route}`)] = plan;
  server.calls.length = 0;
  if (route === "update") click(row("Acme Hub").querySelector("[data-update]"));
  else choose("Acme Hub", "Go back to previous version");
  await settle();
  // The check of the plan has answered: its button waited its 600 ms, which the page's clock gives it and this one may not
  store.pending.clear();
  paintActions();
}
const buttons = () => adding().querySelectorAll(".dialog-actions .btn").map(face);

test("Update says what changes before it does: the version, who changes, what comes and goes, what breaks, what is new, what cannot run; Cancel, Show changes, Update", async () => {
  await updating(PLAN());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/update"), { check: true }]]);
  assert.deepEqual([adding().hasAttribute("open"), heading(), onScreen()], [true, "Update Acme Hub", "effects"]);
  assert.deepEqual(rowsOf(), [
    ["Version", "1.5.0"],
    ["Updates", "Acme Bakery Playground"],
    ["Adds", "Ledger Add-on"],
    ["Removes", "No longer in the catalog Old Add-on"],
    ["Breaks", "Demo Needs Old Playground"],
    ["What's new", "Add the ledger"],
    ["Changes", "Acme Bakery now publishes port 8141"],
    ["Cannot run", "X Publishes a port to the network Add-on"],
  ]);
  assert.deepEqual(buttons(), ["Cancel", "Show changes", "Update"]);
  const update = primary();
  assert.deepEqual([update.classList.contains("primary"), update.classList.contains("danger"), isOff(update)], [false, false, false]);
  assert.equal(secondary().classList.contains("primary"), false, "Show changes is a plain button");
  assert.equal(page.doc.activeElement, update, "Enter confirms what is harmless");
  shut();
});

test("Update names an item it renames under its new name with the one it had, or says its name is the same, that the settings follow, and that an item that is on stays on", async () => {
  await updating(PLAIN({ renames: [{ kind: "addon", id: "@acme-hub/trace-viewer", name: "Trace viewer", was: "Viewer", on: true },
    { kind: "playground", id: "@acme-hub/tide-pools", name: "Tide pools", was: "Tides", on: false },
    { kind: "addon", id: "@acme-hub/kiosk-browser", name: "Kiosk", was: "Kiosk", on: false }] }));
  assert.deepEqual(rowsOf().find(([label]) => label === "Renames"),
    ["Renames", "Your settings follow Trace viewer Was Viewer, stays on Add-on Tide pools Was Tides Playground Kiosk Same name Add-on"],
    "a name that stays the same is no news: its folder is");
  assert.deepEqual(buttons(), ["Cancel", "Show changes", "Update"], "a rename holds nothing back");
  shut();
});

test("the version reads from the one installed to the new one when the catalog raised it, and says so when it did not or made no release", async () => {
  const version = () => rowsOf().find(([label]) => label === "Version")[1];
  await updating(PLAN({ version_from: "1.4.0" }));
  assert.equal(version(), "1.4.0 → 1.5.0");
  shut();
  await updating(PLAN({ version_from: "1.5.0" }));
  assert.equal(version(), "1.5.0 Unchanged");
  shut();
  await updating(PLAN({ version_from: "1.5.0", release: null }));
  assert.equal(version(), "1.5.0 Unreleased");
  shut();
  await updating(PLAN({ version_from: "1.4.0", release: null }));
  assert.equal(version(), "1.4.0 → 1.5.0 Unreleased");
  shut();
});

test("an item that is updated says what changed in it, the first three parts of its folder and how many more", async () => {
  await updating(PLAN({ updates: [{ kind: "addon", id: "@acme-hub/kafka", name: "Kafka", what: ["README.md"] },
    { kind: "playground", id: "@acme-hub/bank", name: "Acme Bakery", what: ["README.md", "compose.yml", "guide/", "seed/", "tests/"] }] }));
  const updates = adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "Updates");
  assert.deepEqual(updates.querySelectorAll(".step-items li").map((li) => [text(li.querySelector(".step-name .muted")), text(li.querySelector(".step-kind"))]),
    [["README.md", "Add-on"], ["README.md, compose.yml, guide/ and 2 more", "Playground"]]);
  shut();
});

test("a row is left out when it has nothing: a plan that only adds says only that", async () => {
  await updating(PLAN({ updates: [], removes: [], breaks: [], changes: [], whatsnew: [], blocked: [] }));
  assert.deepEqual(rowsOf(), [["Version", "1.5.0"], ["Adds", "Ledger Add-on"]]);
  shut();
});

test("a plan held says why on the button and sends nothing", async () => {
  await updating(PLAN({ held: "Turn off Acme Bakery first" }));
  assert.deepEqual([isOff(primary()), offWhy(primary()), face(primary())], [true, "Turn off Acme Bakery first", "Update"]);
  server.calls.length = 0;
  await next(primary());
  assert.equal(server.calls.length, 0);
  shut();
  await updating(PLAN({ held: "Wait for Start of Acme Bakery" }));
  assert.equal(offWhy(primary()), "Wait for Start of Acme Bakery");
  shut();
});

test("Update sends the ticket, closes, and says Updated", async () => {
  clearToasts();
  await updating(PLAN());
  server.answers[at("/update")] = { catalog: GIT("uptodate") };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/update"), { ticket: "b1c2d3e4f5a60718" }]]);
  assert.equal(adding().hasAttribute("open"), false);
  assert.deepEqual(toasts(), ["Updated"]);
});

test("a refusal of the update stays in the problem line, in the server's words, with the dialog open", async () => {
  await updating(PLAN());
  server.refuse[at("/update")] = "Turn off Acme Bakery first.";
  await next(primary());
  assert.deepEqual([adding().hasAttribute("open"), problemOf()], [true, "Turn off Acme Bakery first."]);
  delete server.refuse[at("/update")];
  shut();
});

test("a catalog rewritten or moved says so in the error tone, and its button is Update anyway in the danger tone, which Enter never presses", async () => {
  await updating(PLAN({ rewritten: true }));
  assert.deepEqual(rowsOf()[0], ["Rewritten", "This catalog was rewritten or moved"]);
  assert.ok(adding().querySelector("#catalog-rows .choice-row").classList.contains("danger"));
  const button = primary();
  assert.deepEqual([face(button), button.classList.contains("danger")], ["Update anyway", true]);
  assert.equal(page.doc.activeElement, adding().querySelector("[data-cancel]"), "the keyboard starts on Cancel");
  const enter = new Ev("keydown", { key: "Enter", target: adding().querySelector("#catalog-choice") });
  const field = page.doc.createElement("input");
  adding().querySelector("form").append(field);
  field.dispatchEvent(Object.assign(enter, { target: field, bubbles: true }));
  assert.equal(enter.defaultPrevented, true);
  field.remove();
  server.answers[at("/update")] = { catalog: GIT("uptodate") };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls[0].body, { ticket: "b1c2d3e4f5a60718", confirm_history: true });
});

test("a listed catalog whose plan has nothing to decide is updated by the click, with no dialog", async () => {
  clearToasts();
  show([FIRST({ id: hub(), state: STATES.update })]);
  server.answers[at("/update")] = (body) => (body.check ? PLAIN() : { catalog: FIRST() });
  server.calls.length = 0;
  click(row("Gravitee public").querySelector("[data-update]"));
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/update"), { check: true }], [at("/update"), { ticket: "b1c2d3e4f5a60718" }]]);
  assert.equal(adding().hasAttribute("open"), false);
  assert.deepEqual(toasts(), ["Updated"]);
  // ... and the same plan for one that is not listed asks first
  await updating(PLAIN());
  assert.equal(adding().hasAttribute("open"), true);
  shut();
  // ... as does a listed one with something to decide
  for (const more of [{ changes: PLAN().changes }, { blocked: PLAN().blocked }, { breaks: PLAN().breaks }, { held: "Turn off X first" }, { rewritten: true }]) {
    show([FIRST({ id: hub(), state: STATES.update })]);
    server.answers[at("/update")] = PLAIN(more);
    click(row("Gravitee public").querySelector("[data-update]"));
    await settle();
    assert.equal(adding().hasAttribute("open"), true, JSON.stringify(Object.keys(more)));
    shut();
  }
});

test("an update applied at once keeps the row's button at work from the click to its end: it does not rest between the check and the apply, and a second click starts nothing", async () => {
  clearToasts();
  show([FIRST({ id: hub(), state: STATES.update })]);
  const wait = hold();
  server.answers[at("/update")] = (body) => (body.check ? PLAIN() : wait.held.then(() => ({ catalog: FIRST() })));
  server.calls.length = 0;
  const button = () => row("Gravitee public").querySelector("[data-update]");
  const key = `catalog-op:${current}`;
  click(button());
  for (let i = 0; i < 10; i++) await flush();
  assert.deepEqual(server.calls.map((c) => c.body), [{ check: true }, { ticket: "b1c2d3e4f5a60718" }], "the check has answered and the apply is on its way");
  // The 600 ms that a button stays at work after a click, which the check alone would have held it for
  mock.timers.tick(700);
  for (let i = 0; i < 3; i++) await flush();
  assert.equal(store.pending.get(key), "update");
  assert.ok(button().classList.contains("busy"), "the button still says Updating");
  assert.equal(cells(row("Gravitee public")).state, "Updating");
  click(button());
  await flush();
  assert.equal(server.calls.length, 2, "a second click is the same update");
  wait.release();
  await settle();
  assert.deepEqual([toasts(), store.pending.has(key)], [["Updated"], false]);
  assert.equal(server.calls.length, 2);
});

test("closing the dialog while Update is at work leaves the allowance chosen with it ungiven; the update is done and said", async () => {
  clearToasts();
  await updating(PLAN({ uses_machine: USES, blocked: [] }));
  const [keep, allow] = adding().querySelectorAll("#catalog-choice input");
  keep.checked = false;
  allow.checked = true;
  const wait = hold();
  server.answers[at("/update")] = () => wait.held.then(() => ({ catalog: GIT("uptodate") }));
  server.answers[at("/allow")] = { catalog: GIT("uptodate", { host: "allowed" }) };
  server.calls.length = 0;
  await next(primary());
  click(adding().querySelector("[data-cancel]"));
  wait.release();
  await settle();
  assert.deepEqual(server.calls.map((c) => c.path), [at("/update")]);
  assert.deepEqual(toasts(), ["Updated"]);
});

test("a plan whose ticket expired is dead: its dialog closes and says so, whether the update or Show changes found out", async () => {
  const line = "This took too long. Start again.";
  await updating(PLAN());
  server.refuse[at("/update")] = line;
  clearToasts();
  await next(primary());
  assert.deepEqual([adding().hasAttribute("open"), toasts()], [false, [line]]);
  assert.ok(page.doc.querySelector(".toast").classList.contains("error"));
  delete server.refuse[at("/update")];
  await updating(PLAN());
  server.refuse[at("/changes")] = line;
  clearToasts();
  click(secondary());
  await settle();
  assert.deepEqual([adding().hasAttribute("open"), toasts(), page.doc.querySelector("#catalog-changes-title").closest("dialog").hasAttribute("open")], [false, [line], false]);
  // Any other refusal leaves the dialog open, in its problem line
  await updating(PLAN());
  server.refuse[at("/changes")] = "Another catalog change is running. Try again in a moment.";
  click(secondary());
  await settle();
  assert.deepEqual([adding().hasAttribute("open"), problemOf()], [true, "Another catalog change is running. Try again in a moment."]);
  delete server.refuse[at("/changes")];
  shut();
});

test("changes asked for and a dialog closed meanwhile do not open the changes over nothing", async () => {
  await updating(PLAN());
  const wait = hold();
  server.answers[at("/changes")] = () => wait.held.then(() => ({ files: [{ path: "a.yml", status: "changed", diff: "+x" }] }));
  click(secondary());
  click(adding().querySelector("[data-cancel]"));
  wait.release();
  await settle();
  assert.equal(page.doc.querySelector("#catalog-changes-title").closest("dialog").hasAttribute("open"), false);
});

test("inside a hunk a line is what its first character says: a removed --- or an added ++ is red or green, and only the lines before the first hunk are the file's own", async () => {
  await updating(PLAN());
  server.answers[at("/changes")] = { files: [{ path: "docs.yml", status: "changed",
    diff: "--- a/docs.yml\n+++ b/docs.yml\n@@ -1,4 +1,4 @@\n----\n--- sql comment\n+++counter\n ctx\n@@ -9 +9 @@\n-old\n+new\n\\ No newline at end of file" }] };
  click(secondary());
  await settle();
  const changes = dialogOf("Changes in Acme Hub");
  assert.deepEqual(changes.querySelectorAll("#catalog-diff span").map((s) => [s.getAttribute("class").replace("diff-", ""), s.textContent]), [
    ["meta", "--- a/docs.yml"], ["meta", "+++ b/docs.yml"], ["hunk", "@@ -1,4 +1,4 @@"], ["del", "----"], ["del", "--- sql comment"], ["add", "+++counter"], ["ctx", " ctx"],
    ["hunk", "@@ -9 +9 @@"], ["del", "-old"], ["add", "+new"], ["ctx", "\\ No newline at end of file"]]);
  click(changes.querySelector("[data-close]"));
  shut();
});

test("an update with nothing to apply says so; a check that is refused is an error toast", async () => {
  clearToasts();
  show([GIT("update", { id: hub() })]);
  server.answers[at("/update")] = { upToDate: true };
  click(row("Acme Hub").querySelector("[data-update]"));
  await settle();
  assert.deepEqual([toasts(), adding().hasAttribute("open")], [["Already up to date."], false]);
  clearToasts();
  show([GIT("update", { id: hub() })]);
  server.refuse[at("/update")] = "Cannot reach github.com. Check your connection, then try again.";
  click(row("Acme Hub").querySelector("[data-update]"));
  await settle();
  assert.deepEqual(toasts(), ["Cannot reach github.com. Check your connection, then try again."]);
  delete server.refuse[at("/update")];
});

test("every growth of power is a line of Changes in the Lab's words", async () => {
  const change = (fact, value, name = "Acme Bakery") => ({ id: "x", name, fact, value });
  await updating(PLAN({ changes: [change("port", "8141"), change("build", "github.com/acme/mock", "Mock bank"), change("core", "API Management gateway"),
    change("reads", "license"), change("reads", "gateway token"), change("plugins", "apim"), change("seed", "custom"), change("host", "runs privileged", "Kubernetes"),
    change("host", "mounts the Docker socket", "Console")] }));
  const changes = adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "Changes");
  assert.deepEqual(changes.querySelectorAll(".choice-lines li").map(text), ["Acme Bakery now publishes port 8141", "Mock bank now builds from github.com/acme/mock", "Acme Bakery now changes the API Management gateway",
    "Acme Bakery now reads your Gravitee license", "Acme Bakery now reads your Gravitee Cloud gateway token", "Acme Bakery now installs plugins for apim",
    "Acme Bakery now runs a custom seed", "Kubernetes now runs privileged", "Console now mounts the Docker socket"]);
  shut();
});

test("what is new is every subject of the notes, one to a line", async () => {
  await updating(PLAN({ whatsnew: ["one", "two", "three", "four", "five", "six"] }));
  assert.deepEqual(adding().querySelectorAll("#catalog-rows .choice-row").find((r) => text(r.querySelector(".choice-row-label")) === "What's new").querySelectorAll("li").map(text),
    ["one", "two", "three", "four", "five", "six"]);
  shut();
});

test("Go back opens the same dialog under its own title, with the same rows, and its button is Go back", async () => {
  clearToasts();
  show([GIT("uptodate", { id: hub(), has_previous: true, held: { update: "", remove: "", undo: "" } })]);
  server.answers[at("/undo")] = PLAN();
  server.calls.length = 0;
  choose("Acme Hub", "Go back to previous version");
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/undo"), { check: true }]]);
  assert.deepEqual([heading(), buttons()], ["Go back to previous version", ["Cancel", "Show changes", "Go back"]]);
  assert.equal(rowsOf()[0][0], "Version");
  server.answers[at("/undo")] = { catalog: GIT("update") };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/undo"), { ticket: "b1c2d3e4f5a60718" }]]);
  assert.equal(adding().hasAttribute("open"), false);
});

test("Go back to a history that was rewritten says so without the tone that asks for care: Go back, neutral, Enter confirms, and no confirmation of the history is sent", async () => {
  clearToasts();
  await updating(PLAN({ rewritten: true }), { has_previous: true, held: { update: "", remove: "", undo: "" } }, "undo");
  assert.deepEqual(rowsOf()[0], ["Rewritten", "This catalog was rewritten or moved"], "the line is still said: it is a fact the person may want");
  const line = adding().querySelector("#catalog-rows .choice-row");
  assert.equal(line.classList.contains("danger"), false, "the row is not in the error tone");
  assert.equal(line.querySelector(".pill").classList.contains("err"), false);
  const button = primary();
  assert.deepEqual([face(button), button.classList.contains("danger"), isOff(button)], ["Go back", false, false]);
  assert.equal(page.doc.activeElement, button, "Enter confirms what is the person's own choice");
  // Enter on a field is not taken away
  const enter = new Ev("keydown", { key: "Enter" });
  const field = page.doc.createElement("input");
  adding().querySelector("form").append(field);
  field.dispatchEvent(Object.assign(enter, { target: field, bubbles: true }));
  assert.equal(enter.defaultPrevented, false);
  field.remove();
  server.answers[at("/undo")] = { catalog: GIT("update") };
  server.calls.length = 0;
  await submit(adding().querySelector("form"));
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/undo"), { ticket: "b1c2d3e4f5a60718" }]], "the server takes an undo as confirmed");
  assert.equal(adding().hasAttribute("open"), false);
});

test("an update of a history that was rewritten still asks for care, and sends the confirmation: the difference with going back is the route", async () => {
  await updating(PLAN({ rewritten: true }));
  assert.ok(adding().querySelector("#catalog-rows .choice-row").classList.contains("danger"));
  assert.deepEqual([face(primary()), primary().classList.contains("danger")], ["Update anyway", true]);
  shut();
});

test("Show changes opens the files on the left and a unified diff on the right; a file with no diff reads Changed, not shown", async () => {
  await updating(PLAN());
  server.answers[at("/changes")] = { files: [
    { path: "playgrounds/bank/compose.yml", status: "changed", diff: "--- a/compose.yml\n+++ b/compose.yml\n@@ -1,2 +1,2 @@\n services:\n-  a: 1\n+  a: 2" },
    { path: "addons/ledger/compose.yml", status: "added", diff: "+x: 1" },
    { path: "addons/old/logo.png", status: "removed", diff: null }] };
  server.calls.length = 0;
  click(secondary());
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/changes"), { ticket: "b1c2d3e4f5a60718" }]]);
  const changes = dialogOf("Changes in Acme Hub");
  assert.equal(changes.hasAttribute("open"), true);
  const files = changes.querySelectorAll(".file-item");
  assert.deepEqual(files.map((f) => [text(f.querySelector(".name")), text(f.querySelector(".muted"))]),
    [["playgrounds/bank/compose.yml", "Changed"], ["addons/ledger/compose.yml", "Added"], ["addons/old/logo.png", "Removed"]]);
  assert.deepEqual(files.map((f) => f.classList.contains("active")), [true, false, false]);
  const lineKinds = () => changes.querySelectorAll("#catalog-diff span").map((s) => [s.getAttribute("class").replace("diff-", ""), s.textContent]);
  assert.deepEqual(lineKinds(), [["meta", "--- a/compose.yml"], ["meta", "+++ b/compose.yml"], ["hunk", "@@ -1,2 +1,2 @@"], ["ctx", " services:"], ["del", "-  a: 1"], ["add", "+  a: 2"]]);
  click(changes.querySelectorAll(".file-item")[2]);
  assert.deepEqual(lineKinds(), [["meta", "Changed, not shown"]]);
  click(changes.querySelector("[data-close]"));
  assert.equal(changes.hasAttribute("open"), false);
  assert.equal(adding().hasAttribute("open"), true, "the update is still there to decide");
  shut();
});

test("an update that makes items need this machine has the row and the choice of Add, and allowing is asked once it is updated", async () => {
  await updating(PLAN({ uses_machine: USES, blocked: [] }));
  assert.ok(rowsOf().some(([label, value]) => label === "Uses this machine" && value === "1 item"));
  assert.equal(adding().querySelector("#catalog-choice").hidden, false);
  assert.equal(rowsOf().pop()[0], "Cannot run", "kept blocked: they cannot run");
  const [keep, allow] = adding().querySelectorAll("#catalog-choice input");
  keep.checked = false;
  allow.checked = true;
  allow.dispatchEvent(new Ev("change", { bubbles: true }));
  assert.ok(!rowsOf().some(([label]) => label === "Cannot run"));
  server.answers[at("/update")] = { catalog: GIT("uptodate") };
  server.answers[at("/allow")] = { catalog: GIT("uptodate", { host: "allowed" }) };
  server.calls.length = 0;
  await next(primary());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/update"), { ticket: "b1c2d3e4f5a60718" }], [at("/allow"), { enabled: true }]]);
});

// --- Remove and Allow: the dialog of what an action does ---------------------------------------------------------------------------

// The dialog of ui.js `effectsDialog`: the browser tells it which choice is checked, which the stub does not
const choice = page.ids.choice, picks = page.ids["choice-list"];
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input").find((i) => i.checked) ?? picks.querySelectorAll("input")[0]
  : El.prototype.querySelector.call(picks, selector));
const effects = () => ({ title: text(page.ids["choice-title"]), action: text(page.ids["choice-ok"]), danger: page.ids["choice-ok"].classList.contains("danger"),
  steps: picks.querySelectorAll(".step").map((s) => [text(s.querySelector("strong")), s.querySelectorAll(".step-items li").map(text), s.querySelector(".step-head .muted") ? text(s.querySelector(".step-head .muted")) : ""]),
  notes: picks.querySelectorAll(".choice-row").map((n) => [text(n.querySelector(".choice-row-label")), n.querySelectorAll(".pill").map(text), n.querySelector(".choice-items > .muted") ? text(n.querySelector(".choice-items > .muted")) : ""]),
  options: picks.querySelectorAll(".choice").map((c) => [c.querySelector("strong") ? text(c.querySelector("strong")) : "", c.querySelector(".sub") ? text(c.querySelector(".sub")) : ""]) });
const answer = async (confirmed, pick = 0) => {
  const inputs = picks.querySelectorAll("input");
  inputs.forEach((input, i) => { input.checked = i === pick; });
  choice.returnValue = confirmed ? "ok" : "";
  choice.onclose();
  await settle();
};
const REMOVAL = (more = {}) => ({ held: null, items: 25, folder: false, breaks: [{ kind: "playground", id: "local/demo", name: "Demo" }], stays: { apis: 3 }, ...more });

test("Remove says what goes, what it breaks and what stays in Gravitee, and offers to keep the settings or delete them too; its button is the danger one", async () => {
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = (body) => (body.check ? REMOVAL() : { warning: null });
  server.calls.length = 0;
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/delete"), { check: true }]]);
  const said = effects();
  assert.deepEqual([said.title, said.action, said.danger], ["Remove Acme Hub", "Remove", true]);
  assert.deepEqual(said.steps, [["Removes", ["25 items"], ""]]);
  assert.deepEqual(said.notes, [["Breaks", ["Demo"], "Needs Acme Hub"], ["Stays in Gravitee", ["3 APIs"], ""]]);
  assert.deepEqual(said.options, [["Keep my settings", "Settings and what its playgrounds created, if you add it again"], ["Delete them too", "Settings and records of what its playgrounds created"]]);
  assert.equal(picks.querySelectorAll("input")[0].checked, true, "keeping the settings is the default");
  clearToasts();
  server.calls.length = 0;
  await answer(true);
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/delete"), { keep_settings: true }]]);
  assert.deepEqual(toasts(), ["Removed"]);
});

test("what stays in Gravitee is counted in the singular for one, and API is the acronym it is, whichever shape the server counts in", async () => {
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = REMOVAL({ breaks: [], stays: { apis: 1, applications: 1, policies: 1, plans: 2 } });
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual(effects().notes, [["Stays in Gravitee", ["1 API", "1 application", "1 policy", "2 plans"], ""]]);
  await answer(false);
  server.answers[at("/delete")] = REMOVAL({ breaks: [], stays: [{ count: 3, what: "apis" }, { count: 1, what: "keys" }, "2 plans"] });
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual(effects().notes, [["Stays in Gravitee", ["3 APIs", "1 key", "2 plans"], ""]]);
  await answer(false);
});

test("Delete them too sends the other choice; Cancel sends nothing", async () => {
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = (body) => (body.check ? REMOVAL({ stays: null, breaks: [] }) : {});
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual([effects().notes, effects().steps], [[], [["Removes", ["25 items"], ""]]], "nothing that breaks or stays is not said");
  server.calls.length = 0;
  await answer(false);
  assert.equal(server.calls.length, 0);
  choose("Acme Hub", "Remove");
  await settle();
  server.calls.length = 0;
  await answer(true, 1);
  assert.deepEqual(server.calls.map((c) => c.body), [{ keep_settings: false }]);
});

test("files that Docker made and could not be deleted are an error toast with the line that is the fix, in place of Removed", async () => {
  const line = "Some files were created by Docker and could not be deleted. Remove the folder .lab/catalogs/acme-hub with administrator rights.";
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = (body) => (body.check ? REMOVAL() : { warning: line });
  clearToasts();
  choose("Acme Hub", "Remove");
  await settle();
  await answer(true);
  assert.deepEqual(toasts(), [line]);
  assert.ok(page.doc.querySelector(".toast").classList.contains("error"));
});

test("a removal that is held says so and opens nothing; a folder removes its link and keeps its files", async () => {
  clearToasts();
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = REMOVAL({ held: "Turn off Acme Bakery first" });
  choice.removeAttribute("open");
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual([toasts(), choice.hasAttribute("open")], [["Turn off Acme Bakery first"], false]);
  // A folder: nothing of its own to keep
  show([FOLDER({ id: hub(), name: "My catalog" })]);
  server.answers[at("/delete")] = (body) => (body.check ? REMOVAL({ folder: true, items: 4, breaks: [], stays: null }) : {});
  choose("My catalog", "Remove");
  await settle();
  const said = effects();
  assert.deepEqual(said.steps, [["Removes", ["The link to this folder"], "Your files stay"]]);
  assert.deepEqual(said.options.map(([label]) => label), [""], "one way: no choice");
  server.calls.length = 0;
  await answer(true);
  assert.deepEqual(server.calls.map((c) => c.body), [{ keep_settings: true }]);
  // One item is one item
  show([GIT("uptodate", { id: hub() })]);
  server.answers[at("/delete")] = REMOVAL({ items: 1, breaks: [], stays: null });
  choose("Acme Hub", "Remove");
  await settle();
  assert.deepEqual(effects().steps, [["Removes", ["1 item"], ""]]);
  await answer(false);
});

test("Allow use of this machine names the items and what they take; Stop allowing says they stay listed and cannot start", async () => {
  show([GIT("uptodate", { id: hub(), uses_machine: USES })]);
  server.answers[at("/allow")] = { catalog: GIT("uptodate", { host: "allowed" }) };
  choose("Acme Hub", "Allow use of this machine");
  await settle();
  let said = effects();
  // The one consent that gives the Docker socket and privileged containers: in the danger tone, which Enter never presses
  assert.deepEqual([said.title, said.action, said.danger, said.steps], ["Allow use of this machine", "Allow", true, [["Uses this machine", ["Kubernetes: runs privileged"], ""]]]);
  server.calls.length = 0;
  await answer(true);
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [[at("/allow"), { enabled: true }]]);
  show([GIT("uptodate", { id: hub(), uses_machine: USES, host: "allowed" })]);
  server.answers[at("/allow")] = { catalog: GIT("uptodate", { host: "no" }) };
  choose("Acme Hub", "Stop allowing use of this machine");
  await settle();
  said = effects();
  assert.deepEqual([said.title, said.action, said.danger, said.steps], ["Stop allowing use of this machine", "Stop allowing", false, [["Blocks", ["Kubernetes"], "They stay listed and cannot start"]]]);
  server.calls.length = 0;
  await answer(true);
  assert.deepEqual(server.calls.map((c) => c.body), [{ enabled: false }]);
  server.calls.length = 0;
  choose("Acme Hub", "Stop allowing use of this machine");
  await settle();
  await answer(false);
  assert.equal(server.calls.length, 0);
});

// --- New catalog ----------------------------------------------------------------------------------------------------------------

const creating = () => page.doc.querySelector("#catalog-new-name").closest("form");
const named = () => page.doc.querySelector("#catalog-new-name");
const creator = () => creating().querySelector("[data-act-for='new:catalog']");
creating().checkValidity = () => named().value.trim() !== "";

test("New catalog is a form with one field, Name; Create waits for it and says why", () => {
  show([FIRST()]);
  click(section.querySelectorAll(".page-actions .btn")[0]);
  const dialog = creating().closest("dialog");
  assert.deepEqual([dialog.hasAttribute("open"), text(dialog.querySelector("h2"))], [true, "New catalog"]);
  assert.deepEqual(creating().querySelectorAll("#catalog-new-form label").map(text), ["Name"]);
  assert.equal(named().getAttribute("maxlength"), "60");
  assert.equal(creating().querySelector("#catalog-new-how").hidden, true, "no skill: no AI agent to choose");
  assert.deepEqual(creating().querySelectorAll(".dialog-actions .btn").map(face), ["Cancel", "Create"]);
  assert.deepEqual([creator().classList.contains("primary"), isOff(creator()), offWhy(creator())], [true, true, "Enter a name"]);
  type(named(), "My catalog");
  assert.equal(isOff(creator()), false);
  dialog.close();
});

test("Create sends the name, closes, and says Created; a refusal stays in the problem line in the server's words", async () => {
  clearToasts();
  show([FIRST()]);
  click(section.querySelectorAll(".page-actions .btn")[0]);
  type(named(), "My catalog");
  server.refuse["/api/catalogs/new"] = "There is already a catalog folder named my-catalog. Choose another name.";
  await submit(named());
  assert.deepEqual([creating().closest("dialog").hasAttribute("open"), text(creating().querySelector(".create-problem"))],
    [true, "There is already a catalog folder named my-catalog. Choose another name."]);
  delete server.refuse["/api/catalogs/new"];
  server.answers["/api/catalogs/new"] = { catalog: FOLDER() };
  server.calls.length = 0;
  await submit(named());
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs/new", { name: "My catalog" }]]);
  assert.deepEqual([creating().closest("dialog").hasAttribute("open"), toasts()], [false, ["Created"]]);
});

test("with a skill for it, New catalog is also the prompt that has an AI agent make the catalog: copyable lines, nothing to create", () => {
  show([FIRST()], { skills: { catalog: ".agents/skills/new-catalog/SKILL.md" }, root: "/work/lab" });
  click(section.querySelectorAll(".page-actions .btn")[0]);
  const dialog = creating().closest("dialog"), how = dialog.querySelectorAll("#catalog-new-how [data-how]");
  assert.deepEqual(how.map((b) => [text(b), b.getAttribute("aria-checked")]), [["Form", "true"], ["AI agent", "false"]]);
  click(how[1]);
  assert.equal(creator().hidden, true);
  assert.equal(text(dialog.querySelector("[data-cancel]")), "Close");
  assert.equal(dialog.querySelector("#catalog-new-form").hidden, true);
  const lines = dialog.querySelectorAll("#catalog-new-agent > div").map((l) => [text(l.querySelector("dt")), text(l.querySelector("dd")), l.querySelector("[data-copy]")?.getAttribute("data-copy") ?? null]);
  const prompt = "Use the new-catalog skill (.agents/skills/new-catalog/SKILL.md) to create a catalog.";
  assert.deepEqual(lines, [["Prompt", prompt, prompt], ["Folder", "/work/lab", "/work/lab"]]);
  // The copy buttons are the shared one: their label says what they copy
  assert.deepEqual(dialog.querySelectorAll("#catalog-new-agent [data-copy]").map((b) => [b.getAttribute("class"), b.getAttribute("aria-label")]),
    [["icon-btn copy", "Copy prompt"], ["icon-btn copy", "Copy folder"]]);
  // And the shortcuts that hand the prompt to an agent, in the foot of the dialog
  const go = dialog.querySelector("#catalog-new-go");
  assert.equal(go.hidden, false);
  assert.deepEqual(go.children.map(text), ["Copy prompt", "Open in Claude app", "Open in Claude Code", "Open in Codex", "Open in Cursor"]);
  assert.equal(go.querySelector("[data-copy]").getAttribute("data-copy"), prompt);
  const said = encodeURIComponent(prompt);
  assert.deepEqual(go.querySelectorAll("a").map((link) => link.getAttribute("href")), [
    `claude://code/new?q=${said}&folder=%2Fwork%2Flab`, `claude-cli://open?q=${said}&cwd=%2Fwork%2Flab`, `codex://new?prompt=${said}&path=%2Fwork%2Flab`,
    `cursor://anysphere.cursor-deeplink/prompt?text=${encodeURIComponent(`${prompt} Work in /work/lab.`)}`]);
  assert.equal(go.querySelectorAll(".primary").length, 0, "no agent is the default one");
  assert.deepEqual(go.querySelectorAll("a").map((link) => link.querySelector("use").getAttribute("href")), ["#i-claude", "#i-terminal", "#i-openai", "#i-cursor"]);
  // What the catalog should hold goes into the prompt, and into what the agents are given
  const task = dialog.querySelector("#catalog-task");
  assert.equal(dialog.querySelector("#catalog-new-task").hidden, false);
  type(task, "Demos of our payment APIs");
  const longer = `${prompt} What it should hold: Demos of our payment APIs`;
  assert.equal(text(dialog.querySelector("#catalog-new-agent .snippet")), longer);
  assert.ok(go.querySelector("a").getAttribute("href").startsWith(`claude://code/new?q=${encodeURIComponent(longer)}`));
  type(task, "");
  click(how[0]);
  assert.equal(dialog.querySelector("#catalog-new-task").hidden, true);
  assert.deepEqual([creator().hidden, text(dialog.querySelector("[data-cancel]")), dialog.querySelector("#catalog-new-form").hidden, go.hidden], [false, "Cancel", false, true]);
  dialog.close();
});

test("a catalog just created is revealed on the Catalogs page unless the person has a dialog open, which is never closed under them", async () => {
  server.answers["/api/catalogs/new"] = { catalog: FOLDER() };
  show([FIRST()]);
  router.current = { page: catalogsPage, params: [] };
  const created = async () => {
    click(section.querySelectorAll(".page-actions .btn")[0]);
    type(named(), "My catalog");
    await submit(named());
    await settle();
  };
  openAdd();
  const before = hashOf();
  await created();
  assert.equal(hashOf(), before, "the dialog of Add is open: the address does not change under it");
  assert.equal(adding().hasAttribute("open"), true);
  shut();
  await created();
  assert.equal(hashOf(), "#/catalogs/my-catalog");
  router.current = null;
});

// --- Add from a line of an item that needs a catalog ------------------------------------------------------------------------------

test("a catalog an item needs is added from its line: a listed one at once, any other through the preview on its second screen", async () => {
  server.answers["/api/catalogs/check"] = PREVIEW();
  show([NOT_ADDED({ id: "gravitee" })]);
  server.answers["/api/catalogs"] = { catalog: FIRST({ commit: "9f1c2ab" }) };
  server.calls.length = 0;
  addCatalog({ id: "gravitee", name: "Gravitee public", url: `https://${ADDRESS}.git` });
  await settle();
  assert.deepEqual(server.calls.map((c) => [c.path, c.body]), [["/api/catalogs", { listed: "gravitee" }]]);
  assert.equal(adding().hasAttribute("open"), false);
  show([FIRST()]);
  server.calls.length = 0;
  addCatalog({ id: "partner-bank", name: "Partner Bank", url: "https://example.org/partner/lab-bank.git" });
  await settle();
  assert.deepEqual([adding().hasAttribute("open"), heading(), onScreen(), server.calls[0].body], [true, "Add Acme Hub", "effects", { address: "https://example.org/partner/lab-bank.git" }]);
  shut();
});

test("on a phone the facts of an open row take the lists' label column: the wide one of the desktop is not left to squeeze the values", () => {
  const rules = source("css/pages.css").split("/* --- Catalogs")[1].split("/* --- Editor")[0];
  assert.match(rules, /#page-catalogs \{ --label: 124px; \}/);
  assert.match(rules, /@container \(max-width: 599px\) \{ #page-catalogs \{ --label: 80px; \} \}/);
  assert.match(source("css/base.css"), /--label: 80px;/, "the lists' own");
});

// --- What a server says is text -------------------------------------------------------------------------------------------------

test("a name, a description, an address, a reason or a subject that is markup is shown as the text it is: in the rows, the detail, the menu and every dialog", async () => {
  const bad = (key) => `${HOSTILE} ${key}`;
  const dirty = () => page.doc.querySelectorAll("img[src=x], script");
  show([GIT("noaccess", { id: "evil-hub", name: HOSTILE, description: HOSTILE_QUOTED, address: HOSTILE, url: "https://example.org/x.git", version: HOSTILE,
    state: { ...STATES.noaccess, label: HOSTILE, reason: HOSTILE, next: HOSTILE_QUOTED }, ignored: 1,
    uses_machine: [{ id: "x", name: HOSTILE, what: [HOSTILE], why: HOSTILE_QUOTED }],
    blocked: [{ kind: "addon", id: "x", name: HOSTILE, reasons: [HOSTILE_QUOTED] }], problems: [`${HOSTILE}: ${HOSTILE_QUOTED}`] })]);
  open(items()[0]);
  assert.equal(dirty().length, 0);
  assert.equal(text(items()[0].querySelector(".list-name")), HOSTILE);
  assert.equal(items()[0].querySelector(".list-name").getAttribute("data-tip"), HOSTILE, "an attribute keeps it whole and inert");
  assert.deepEqual(bubble(items()[0].querySelector(".list-state .tip")), [HOSTILE, HOSTILE, HOSTILE_QUOTED]);
  // The menu and the dialogs it opens
  click(cells(items()[0]).more);
  assert.equal(dirty().length, 0);
  closeMenu();
  server.answers[at("/x")] = {};
  show([GIT("update", { id: hub(), name: HOSTILE })]);
  server.answers[at("/update")] = PLAN({ version: HOSTILE, updates: [{ name: HOSTILE }], adds: [{ name: HOSTILE_QUOTED }], removes: [{ name: HOSTILE }],
    breaks: [{ name: HOSTILE, needs: HOSTILE_QUOTED }], changes: [{ name: HOSTILE, fact: "build", value: HOSTILE_QUOTED }], whatsnew: [HOSTILE, HOSTILE_QUOTED],
    blocked: [{ name: HOSTILE, reasons: [HOSTILE_QUOTED] }], uses_machine: [{ id: "y", name: HOSTILE, what: [HOSTILE], why: HOSTILE_QUOTED }], held: HOSTILE });
  click(row(HOSTILE).querySelector("[data-update]"));
  await settle();
  assert.equal(dirty().length, 0);
  assert.equal(heading(), `Update ${HOSTILE}`);
  assert.equal(offWhy(primary()), HOSTILE);
  shut();
  show([NOT_ADDED()]);
  server.answers["/api/catalogs/check"] = PREVIEW({ catalog: { id: "evil", name: HOSTILE, kind: "git" }, address: HOSTILE, counts: {},
    rows: { builds: { local: 0, remote: [HOSTILE] }, ports: [], core: [HOSTILE], reads: [HOSTILE_QUOTED], needs: [{ name: HOSTILE }], found: [{ name: HOSTILE_QUOTED }],
      uses_machine: [{ id: "z", name: HOSTILE, what: [HOSTILE], why: HOSTILE_QUOTED }] },
    blocked: [{ id: "q", name: HOSTILE, reasons: [HOSTILE_QUOTED] }] });
  openAdd({ address: "x/y" });
  await settle();
  assert.equal(dirty().length, 0);
  assert.equal(heading(), `Add ${HOSTILE}`);
  assert.ok(rowsOf().some(([label, value]) => label === "Builds" && value.includes(`Builds from ${HOSTILE}`)));
  shut();
  server.refuse["/api/catalogs/check"] = HOSTILE;
  openAdd();
  type(addressInput(), "a/b");
  await next(addressInput());
  assert.equal(problemOf(), HOSTILE);
  assert.equal(dirty().length, 0);
  delete server.refuse["/api/catalogs/check"];
  shut();
  // The detail of the removal
  show([GIT("uptodate", { id: hub(), name: HOSTILE })]);
  server.answers[at("/delete")] = REMOVAL({ breaks: [{ name: HOSTILE }], stays: { [HOSTILE]: 2 } });
  choose(HOSTILE, "Remove");
  await settle();
  assert.equal(dirty().length, 0);
  assert.equal(effects().title, `Remove ${HOSTILE}`);
  await answer(false);
  void bad;
});

test("the examples of an address stay text when the first catalog's host is markup", () => {
  show([FIRST({ address: `${HOSTILE}/x/y` })]);
  openAdd();
  assert.equal(page.doc.querySelectorAll("img[src=x]").length, 0);
  shut();
});

// --- The modules as files ---------------------------------------------------------------------------------------------------------

const MODULES = ["js/pages/catalogs.js", "js/dialogs/catalog.js"];
const code = (file) => source(file).replace(/\/\*[\s\S]*?\*\/|(?<![:\w"'`\\/])\/\/[^\n]*/g, "");

test("the modules write no title attribute, no host of the web, no item or maker of a catalog, and only classes some stylesheet has a rule for", () => {
  const styles = source("css/base.css") + source("css/components.css") + source("css/pages.css");
  for (const file of MODULES) {
    assert.doesNotMatch(code(file), /\stitle=/, `${file}: the browser's tooltip`);
    assert.doesNotMatch(source(file), /github\.com/, `${file}: the address of a host is data`);
    assert.doesNotMatch(code(file), /\b(acme|kafka|jade|kiosk|openfga|reader-ui|gravitee-demos)\b/i, `${file}: names no item or catalog`);
    const classes = new Set([...code(file).matchAll(/\bclass="([^"$]*)"/g)].flatMap((found) => found[1].split(/\s+/)).filter(Boolean));
    for (const name of classes) assert.match(styles, new RegExp(`\\.${name.replace(/[-]/g, "\\-")}\\b`), `${file}: .${name} has no rule`);
    // No new size of control: the page writes no height or width of its own
    assert.doesNotMatch(code(file), /style="|\.style\./, `${file}: no style of its own`);
  }
});

test("the stylesheet gives the page nothing but what it needs: rules for classes that exist, and the classes of the diff of Show changes", () => {
  const rules = source("css/pages.css").split("/* --- Catalogs")[1].split("/* --- Editor")[0];
  assert.deepEqual([...new Set([...rules.matchAll(/\.([a-z][\w-]*)/g)].map((found) => found[1]))].sort(), ["choice-items", "diff", "diff-add", "diff-del", "diff-hunk", "diff-meta", "panel-fact", "step-items"]);
});
