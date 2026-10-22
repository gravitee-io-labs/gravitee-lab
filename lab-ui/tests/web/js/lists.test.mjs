// A lab without a platform draws what it drew before platforms existed, and a registered platform changes only what the design
// says: the three lists (web/js/pages/list.js), the banner (web/js/components/sidebar.js) and the Stack (web/js/pages/stack.js), run for real
// over a small DOM. lists.main.txt is the DOM those pages drew for this state before platforms: it was written by running this
// file over those modules with WRITE_MAIN=1 (a tree of main, with list.js's rows written without the whitespace node that the small
// DOM does not take), and a deliberate change of what a lab without a platform sees is written the same way. The state carries what
// a registered platform adds (what a playground deploys to and what it cannot, the place chosen) with no platform registered: none
// of it may show. The Start of a row follows the place chosen on any page, as the page of the playground does (web/js/platform/places.js).
//
// The catalogs add two more snapshots, written the same way: lists.empty.txt is what the lists draw while the catalogs download, fail, are missing
// or hold nothing, with no item to list and with only this machine's (WRITE_EMPTY=1), and lists.catalogs.txt what they draw with another catalog added:
// the Catalog fact of its About, the picker of the catalogs in place of the two choices, its Blocked and Needs a catalog notes (WRITE_CATALOGS=1). With the first
// catalog alone, the pages are lists.main.txt: no chip, no picker, no scope control while all of the items are one catalog's.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { BLOCKED, FIRST, HOSTILE, NEEDS, PERSONAL, catalog, content, gains, origin } from "./fixtures.mjs";

// The clock is the mock's too: what a button says of how its action went lasts a time that only the ticks of the tests make pass, never the
// load of the machine (a snapshot drawn a second late would otherwise be another one)
mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: Date.now() });
// A timer set with no delay (the menu's, in ui.js) is NaN for the mock, which then loses the timers that follow it
const setTimer = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => setTimer(fn, ms ?? 0, ...rest);
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const MAIN = new URL("./lists.main.txt", import.meta.url), EMPTY = new URL("./lists.empty.txt", import.meta.url), CATALOGS = new URL("./lists.catalogs.txt", import.meta.url);
const ENVIRONMENTS = [{ id: "development", name: "Development" }, { id: "test", name: "Test" }];
const CANNOT = { "acme-bakery": "Needs Access Management" };   // what a place says Acme Bakery cannot go there for
// What the server answers, and every call the page made
const server = { calls: [], dialog: null };
const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const JOB = { id: "ab12cd34", for: "playground:acme-hostels", verb: "start", title: "Start Acme Hostels", done: false, ok: null, progress: null, phases: [], started: 0 };
// The groups of the Stack the page remembers open, and the place that was last chosen to deploy to (the default of every playground)
const stored = { expanded: JSON.stringify({ "group:api-management": true, "addon:kafka": true }), deploy: "acme/development" };
const page = installDom({ ids, stored, fetch: async (path, options = {}) => {
  const call = { method: options.method || "GET", path, body: options.body ? JSON.parse(options.body) : undefined };
  if (path !== "/api/state") server.calls.push(call);
  if (path === "/api/state") return reply(store.state);
  if (path.startsWith("/api/platforms/")) return reply({ environments: ENVIRONMENTS });   // the forms read the environments of a platform
  if (path.includes("/check")) return reply({ checks: [], dialog: server.dialog });
  return reply(JOB);
} });
globalThis.EventSource = class { close() {} addEventListener() {} };   // the output of a job, which the drawer follows
page.ids.health.append(page.doc.createElement("span"));   // the spinner that the status dot replaces
// A button that fades back to its face reads how long it takes from the stylesheet: no fade here
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });

// What the registered platforms and the places of the cache say, which a test sets
const registry = { platforms: [], places: [] };
const { store, router } = await import("../../../web/js/core.js");
for (const key of Object.keys(registry)) {
  Object.defineProperty(store, key, { get: () => registry[key], set: (value) => { registry[key] = value; }, configurable: true });
}
let reads = 0;   // how many times the places were asked for
store.refreshPlaces = () => { reads += 1; };
const { renderShell } = await import("../../../web/js/components/sidebar.js");
const { playgroundsPage, setupsPage, addonsPage, selectCatalog } = await import("../../../web/js/pages/list.js");
const { stackPage } = await import("../../../web/js/pages/stack.js");
page.ids["combo-pop"].innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { Combo } = await import("../../../web/js/components/combo.js");
Combo.init();
await import("../../../web/js/dialogs/catalog.js");   // answers the buttons of the content, which the lists draw (Try again, Add the listed catalog)
globalThis.innerHeight = 800;

// --- The lab -----------------------------------------------------------------------------

const RUNNING = { key: "running", label: "Running", running: true };
const STOPPED = { key: "stopped", label: "Stopped", running: false };
const OFF = { key: "off", label: "", running: false };
const NONE = { note: "", note_tone: "", note_title: "", note_target: null, note_file: null };
const ACTION = (verb) => ({ do: verb, label: verb === "stop" ? "Stop" : "Start", enabled: true, reason: null, dialog: null });

function service(label, state, more = {}) {
  return { label, description: "", container: `gio-${label}`, image: `graviteeio/${label}:1`, tag: "1", registry: "graviteeio", built: false,
    role: null, needs_license: false, ports: [], state, uptime: state.running ? "5 min" : "", on: true, running_tag: state.running ? "1" : null,
    behind: false, brings: [], recreates: [], ...NONE, ...more };
}

const item = (kind, id, name, more = {}) => ({ kind, id, name, personal: false, held: null, category: "Demo", description: `${name} for the demo`,
  file: `${kind}s/${id}/compose.yml`, env_file: null, settings: [], on: false, up: false, state: OFF, progress: null, ...NONE, links: [],
  action: ACTION("start"), secondary: null, failed_job: null, login: null, starred: false, ...more });

// A playground as the state says it, with the keys that a platform adds, empty (what it deploys to, what it cannot, what it holds)
const playground = (id, name, more = {}) => item("playground", id, name, { tags: ["demo"], duration: "10 min", first_start: null, source: null,
  chapters: [], guide: null, accounts: [], requires: {}, setups: [], addons: [], includes: [], services: [], apps: [], seed: null, created: [],
  stop: { stops: [], turns_off: [], keeps: [], delete: null }, reseed: null, brings: [], recreates: [], tabs: [], deploys_to: [], cannot: {}, deployed: [], ...more });

// The lab with the core stopped, or running; `away`: an add-on of the lab runs. Acme Hostels deploys to a platform too.
function lab({ away = false, core = "stopped", hotels = {}, ...more } = {}) {
  const gateway = service("gateway", core === "running" ? RUNNING : STOPPED);
  const kafka = service("kafka", away ? RUNNING : STOPPED, { ports: ["9092", "9093"] });
  const group = { id: "api-management", name: "API Management", services: ["gateway"], state: gateway.state.running ? RUNNING : STOPPED, progress: null,
    version: "1", registry: null, registry_label: null, product: null, file: "docker-compose.yml", env_file: ".env", settings: [], brings: [], recreates: [], ...NONE };
  return {
    blind: false, wait: "", docker: { error: null, warning: null }, license: null, products: [], categories: [], job: null, problems: [],
    links: [], services: { gateway, kafka },
    platform: { name: "Gravitee platform", state: group.state, progress: null, groups: [group], file: "docker-compose.yml", env_file: ".env", settings: [],
      start: { needed: true, starts: [], restarts: [], enabled: true, reason: null, dialog: null }, apply: { needed: false }, behind: [],
      restart: { needed: core === "running", enabled: true, reason: null }, update: { needed: core === "running", enabled: true, reason: null, dialog: null },
      reset_turns_off: null },
    playgrounds: [playground("acme-hostels", "Acme Hostels", { created: [{ what: "APIs", count: 3 }], deploys_to: ["cloud"], ...hotels }),
      playground("acme-bakery", "Acme Bakery", { on: true, state: STOPPED })],
    setups: [item("setup", "messaging", "Messaging", { members: [{ id: "kafka", name: "Kafka" }], includes: [] })],
    addons: [item("addon", "kafka", "Kafka", { services: ["kafka"], needs: [], conflicts: {}, restarts: [], brings: [], recreates: [], on: away, up: away,
      state: away ? RUNNING : OFF, action: ACTION(away ? "stop" : "start") })],
    ...more,
  };
}

// A place as the cache of /api/places says it
const place = (more = {}) => ({ id: "acme/development", name: "Development (My Account)", platform: { id: "acme", name: "My Account", kind: "cloud" },
  environment: { id: "development", name: "Development", organization: "o1" }, state: "ready", missing: null, reason: "", token: { state: "valid" },
  gateway: { id: "g1", name: "lab", state: "connected", here: true, kept: true, connection: "CONNECTED" },
  capabilities: { products: { "API Management": "4.12.21" }, plugins: false, detail: "" }, cannot: CANNOT, ...more });
const lacks = (missing, reason, token) => place({ state: "missing", missing, reason, token: { state: token }, gateway: null, capabilities: null });
// A platform that cannot be used is one place of its own id, without environment, token or gateway
const unusable = (reason) => place({ id: "acme", name: "My Account", environment: null, state: "unusable", reason, token: null, gateway: null,
  capabilities: null });
const ACCOUNT = { id: "acme", name: "My Account", kind: "cloud", url: "https://eu.cloud.gravitee.io" };

// --- What a page draws, one line for each node ---------------------------------------------

function outline(node, depth = 0) {
  const pad = "  ".repeat(depth);
  if (!node.tag) {
    const said = node.data.replace(/\s+/g, " ").trim();
    return said ? [`${pad}"${said}"`] : [];
  }
  const attributes = [...node.attrs].map(([name, value]) => ` ${name}="${value}"`).join("");
  return [`${pad}<${node.tag}${attributes}>`, ...node.children.flatMap((child) => outline(child, depth + 1))];
}
const drawn = (el) => outline(el).join("\n");

const PAGES = { playgrounds: playgroundsPage, setups: setupsPage, addons: addonsPage, stack: stackPage };
const sections = {};
for (const [name, entry] of Object.entries(PAGES)) {
  sections[name] = page.doc.createElement("section");
  page.doc.body.append(sections[name]);
  entry.init(sections[name]);
}

// Everything the server says is drawn: the pages, then the shell, as the router does after a refresh. `read`: the places were read
function draw(state, platforms = [], places = [], read = true) {
  Object.assign(registry, { platforms, places });
  store.placesRead = platforms.length > 0 && read;
  store.state = { ...state, platforms };
  for (const entry of Object.values(PAGES)) entry.render(store.state);
  router.current = { page: { id: "playgrounds" }, params: [] };
  renderShell();
}
const row = (name, id) => sections[name].querySelector(`.list-item[data-key="${id}"]`);
const banner = () => page.ids.banner;

// The row of a list that opens, to show its detail
function open(name, id) {
  const head = row(name, id).firstElementChild;
  if (head.getAttribute("aria-expanded") !== "true") head.click();
}

// --- No platform ---------------------------------------------------------------------------

// The lab as a person without a platform sees it, in the states that tell the pages apart
const S1 = { calm: lab(), away: lab({ away: true }), running: lab({ core: "running", away: true }) };
// The cache of places, though none is registered: what the page of Acme Hostels was set to is in it
const REMEMBERED = [lacks("No hybrid gateway", "Set up a gateway in Development (My Account) first", "unset")];

// Everything these pages draw for the lab of S1, by name
function everything() {
  const dom = {};
  draw(S1.calm, [], REMEMBERED);
  dom["banner: the core stopped, nothing runs"] = drawn(banner());
  draw(S1.running, [], REMEMBERED);
  dom["banner: the core runs"] = drawn(banner());
  draw(S1.away, [], REMEMBERED);
  dom["banner: the core stopped, an add-on runs"] = drawn(banner());
  open("playgrounds", "acme-hostels");
  open("setups", "messaging");
  open("addons", "kafka");
  draw(S1.away, [], REMEMBERED);
  for (const name of Object.keys(PAGES)) dom[name] = drawn(sections[name]);
  return dom;
}

// What was written: the name of each part, then what it drew
const written = (dom) => Object.entries(dom).map(([name, body]) => `=== ${name} ===\n${body}\n`).join("");
function read(file = MAIN) {
  const parts = readFileSync(file, "utf8").split(/^=== (.*) ===\n/m), main = {};
  for (let i = 1; i < parts.length; i += 2) main[parts[i]] = parts[i + 1].replace(/\n$/, "");
  return main;
}

test("with no platform registered the lists, the banner and the Stack are what they were before platforms", () => {
  const dom = everything();
  if (process.env.WRITE_MAIN) writeFileSync(MAIN, written(dom));
  const main = read();
  assert.deepEqual(Object.keys(dom), Object.keys(main));
  for (const name of Object.keys(main)) assert.equal(dom[name], main[name], name);
});

// --- A platform registered -----------------------------------------------------------------

const hotels = () => row("playgrounds", "acme-hostels");
const noteOf = () => hotels().querySelector(".list-state .note");   // a badge after the state, in its cell, never in the description
const noted = () => (noteOf() ? text(noteOf()) : null);   // words, not an element: a failed comparison prints what it is given
const markOf = () => hotels().querySelectorAll(".list-marks .icon-pill").map((el) => el.getAttribute("data-tip"));
const startOf = () => hotels().querySelector(".list-actions .act");
// What keeps a Start from working is a warning beside it, whose card says it: its title, and the button that fixes it
const blockerOf = () => hotels().querySelector(".list-actions .blocker");
const blockedBy = () => (blockerOf() ? text(blockerOf().parentElement.querySelector(".pop-title")) : null);
const mendOf = () => blockerOf()?.parentElement.querySelector(".tip-card .btn");

// A row as it was, with the mark of a playground that deploys to a platform after its name and its star
function marked(before) {
  const lines = before.split("\n"), at = lines.findIndex((line) => line.includes('class="list-marks"'));
  const pad = " ".repeat(lines[at].search(/\S/) + 2);
  lines.splice(at + 4, 0, `${pad}<span class="pill icon-pill" role="img" tabindex="0" aria-label="Deploys to Gravitee Cloud" data-tip="Deploys to Gravitee Cloud">`, `${pad}  <svg aria-hidden="true">`, `${pad}    <use href="#i-cloud">`);
  return lines.join("\n");
}

test("a registered platform marks the playgrounds that deploy to it, and changes nothing else on the pages", () => {
  const pages = () => ({ hotels: drawn(hotels()), bank: drawn(row("playgrounds", "acme-bakery")), setups: drawn(sections.setups),
    addons: drawn(sections.addons), stack: drawn(sections.stack) });
  draw(S1.calm);
  const before = pages();
  draw(S1.calm, [ACCOUNT], [place()]);
  const after = pages();
  assert.equal(after.hotels, marked(before.hotels));
  assert.deepEqual(markOf(), ["Deploys to Gravitee Cloud"]);
  assert.equal(hotels().querySelector(".list-marks .pill use").getAttribute("href"), "#i-cloud");
  for (const name of ["bank", "setups", "addons", "stack"]) assert.equal(after[name], before[name], name);
  assert.equal(noted(), null, "a place that is ready lacks nothing");
  assert.ok(!isOff(startOf()));
});

test("a playground whose place is not ready cannot start, and says what it lacks in its note", () => {
  const cases = [
    [lacks("No hybrid gateway", "Set up a gateway in Development (My Account) first", "unset"), "Needs a hybrid gateway", "gateway"],
    [lacks("No Cloud Token", "Cloud Token for Development (My Account) is not set", "unset"), "Needs a Cloud Token", "token"],
    [lacks("Cloud Token expired", "Cloud Token for Development (My Account) has expired", "expired"), "Needs a Cloud Token", "token"],
    [lacks("Cloud Token refused", "Gravitee Cloud refuses the Cloud Token of Development (My Account)", "refused"), "Needs a Cloud Token", "token"],
  ];
  for (const [missing, said, fix] of cases) {
    draw(S1.calm, [ACCOUNT], [missing]);
    assert.equal(noted(), null, "the State keeps what the item is");
    assert.equal(blockedBy(), said, missing.missing);
    assert.equal(mendOf().getAttribute("data-fix"), fix, missing.missing);
    assert.match(text(blockerOf().parentElement.querySelector(".pop-text")), new RegExp(missing.reason.replace(/[()]/g, ".")), missing.missing);
    assert.ok(isOff(startOf()), missing.missing);
    assert.equal(offWhy(startOf()), missing.reason, missing.missing);
  }
  // A platform that cannot be used: its note leads to its row
  for (const [reason, tone] of [["Invalid token", "err"], ["Unreachable", "warn"]]) {
    draw(S1.calm, [ACCOUNT], [unusable(reason)]);
    assert.equal(noted(), null);
    assert.equal(blockedBy(), reason);
    assert.equal(mendOf().tag, "a", reason);
    assert.equal(mendOf().getAttribute("href"), "#/cloud/acme");
    assert.equal(blockerOf().classList.contains("err"), tone === "err");
    assert.ok(isOff(startOf()));
    assert.equal(offWhy(startOf()), reason);
  }
  // Still being read: nothing to say yet, and nothing to start
  draw(S1.calm, [ACCOUNT], [unusable("Loading")]);
  assert.equal(noted(), null);
  assert.equal(blockerOf(), null);
  assert.ok(isOff(startOf()));
  assert.equal(offWhy(startOf()), "Loading");
});

test("a place that is not a platform's, is gone with its platform or is not read yet says nothing and holds nothing", () => {
  const free = () => assert.ok(noted() === null && blockerOf() === null && !isOff(startOf()) && !offWhy(startOf()), "no note, and a Start");
  const unready = lacks("No Cloud Token", "Cloud Token for Development (My Account) is not set", "unset");
  for (const remembered of ["machine", "acme/gone", null]) {
    if (remembered === null) delete page.stored.deploy;
    else page.stored.deploy = remembered;
    draw(S1.calm, [ACCOUNT], [unready]);
    free();
  }
  page.stored.deploy = "acme/development";
  draw(S1.calm, [ACCOUNT], []);
  free();
  // A playground that does not deploy to a platform has no place to lack, whatever is kept for it: every place says why
  draw(lab({ hotels: { deploys_to: [] } }), [ACCOUNT], [{ ...unready, cannot: { ...CANNOT, "acme-hostels": "Changes this machine's gateway" } }]);
  assert.deepEqual(markOf(), []);
  free();
});

test("what the server notes of a row comes before what its place lacks, and a Stop is never held", () => {
  const unready = lacks("No hybrid gateway", "Set up a gateway in Development (My Account) first", "unset");
  draw(lab({ hotels: { note: "Started outside the Lab UI" } }), [ACCOUNT], [unready]);
  assert.equal(noted(), "Started outside the Lab UI");
  assert.equal(blockedBy(), "Needs a hybrid gateway", "what its place lacks is the warning beside its Start");
  assert.ok(isOff(startOf()), "it cannot start for all that");
  draw(lab({ hotels: { on: true, up: true, state: RUNNING, action: ACTION("stop") } }), [ACCOUNT], [unready]);
  assert.equal(text(startOf()), "Stop");
  assert.ok(!isOff(startOf()), "what runs can be stopped");
});

test("a Start that switches the versions keeps its warning beside it, never in the State, and its card says the Start does it", () => {
  const switching = { note: "Needs newer versions", note_tone: "warn", note_target: { kind: "image", id: null },
    note_causes: [{ kind: "switch", services: ["API Management"], have: "4.12.20", to: "4.13.0-alpha.3", older: false }] };
  draw(lab({ hotels: { ...switching, action: { ...ACTION("start"), warns: true } } }));
  assert.equal(noted(), null, "nothing in the State");
  assert.equal(blockedBy(), "Needs newer versions", "the warning beside the Start");
  assert.ok(!isOff(startOf()), "and the Start starts");
  const card = () => text(blockerOf().parentElement.querySelector(".tip-card"));
  // Each image as it is and as it was tested, in the same columns whether the Start goes there or only offers to
  assert.match(card(), /Current.*Tested.*API Management.*4\.12\.20.*4\.13\.0-alpha\.3/);
  assert.match(card(), /Start goes to the versions it was tested with/);
  draw(lab({ hotels: { ...switching, note: "Not tested with these versions", action: { ...ACTION("start"), warns: true, offer: { versions: [] } } } }));
  assert.equal(blockedBy(), "Not tested with these versions");
  assert.match(card(), /Current.*Tested.*API Management/);
  assert.match(card(), /Start offers the versions it was tested with/);
  // Without a version to go to it cannot start: the same warning, and the Images page is the way
  const needs = { ...switching, note_causes: [{ kind: "needs", name: "API Management", version: "4.13.0-alpha.3" }] };
  draw(lab({ hotels: { ...needs, action: { ...ACTION("start"), enabled: false, reason: "Needs API Management 4.13.0-alpha.3 or later" } } }));
  assert.equal(blockedBy(), "Needs newer versions");
  assert.ok(isOff(startOf()));
  assert.match(card(), /Choose it on the Images page/);
});

test("the note of a place that lacks a gateway or a Cloud Token opens the form that takes it, in that place's environment", async () => {
  for (const form of page.doc.querySelectorAll("dialog form")) form.checkValidity = () => true;   // the small DOM does not validate
  const form = () => {
    const dialog = page.doc.querySelectorAll("dialog").find((el) => el.open);
    return dialog && { title: text(dialog.querySelector("h2")), environment: dialog.querySelector("option[selected]").getAttribute("value") };
  };
  const closeForm = () => page.doc.querySelectorAll("dialog").find((el) => el.open).close();
  const cases = [
    [lacks("No hybrid gateway", "", "unset"), "Create hybrid gateway for Development"],
    [lacks("No Cloud Token", "", "unset"), "Add Cloud Token for Development"],
    [lacks("Cloud Token expired", "", "expired"), "Replace Cloud Token for Development"],
  ];
  for (const [missing, title] of cases) {
    draw(S1.calm, [ACCOUNT], [missing]);
    mendOf().click();
    await flush();
    assert.deepEqual(form(), { title, environment: "development" }, missing.missing);
    closeForm();
  }
  // A note that leads to the row of its platform opens no form, nor the row of the playground under it
  draw(S1.calm, [ACCOUNT], [unusable("Unreachable")]);
  const expanded = hotels().firstElementChild.getAttribute("aria-expanded");
  blockerOf().click();
  await flush();
  assert.equal(form(), undefined);
  assert.equal(hotels().firstElementChild.getAttribute("aria-expanded"), expanded);
});

test("the calm line of the stopped core is dropped once a platform is registered and something else of the lab runs", () => {
  const says = () => banner().querySelectorAll("p").map((line) => text(line.firstElementChild)).join(" | ");
  for (const platforms of [[], [ACCOUNT]]) {
    draw(S1.calm, platforms);
    assert.equal(says(), "Gravitee platform is stopped", "nothing else runs");
    draw(S1.running, platforms);
    assert.equal(says(), "", "the core runs");
  }
  draw(S1.away, []);
  assert.equal(says(), "Gravitee platform is stopped", "without a platform it is where the lab begins");
  draw(S1.away, [ACCOUNT]);
  assert.equal(says(), "", "an add-on runs: the lab is used through the platform");
  draw(lab({ hotels: { up: true } }), [ACCOUNT]);
  assert.equal(says(), "", "so does an app of a playground");
  // Nothing else the banner says goes with it
  const trouble = { ...S1.away, license: { problem: "The license expired" }, problems: [{ file: "docker-compose.yml", message: "does not load", editable: true }] };
  draw(trouble, [ACCOUNT]);
  assert.equal(says(), "The license expired | docker-compose.yml: does not load");
  assert.ok(!banner().classList.contains("calm"));
  // A Start at work keeps its line until it has said how it went
  store.pending.set("platform", "start");
  draw(S1.away, [ACCOUNT]);
  assert.equal(says(), "Gravitee platform is stopped");
  store.pending.delete("platform");
});

test("the list of playgrounds reads the places when it is shown, for the notes of its rows; the other lists do not", () => {
  const before = reads;
  setupsPage.show([]);
  addonsPage.show([]);
  assert.equal(reads, before);
  playgroundsPage.show([]);
  assert.equal(reads, before + 1);
});

// --- The Start of a row: where the place chosen on any page sends it ---------------------------------------------------------------------

const choice = page.ids.choice, picks = page.ids["choice-list"];
let picked = 0;   // the choice of a dialog that offers several
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input")[picked] : El.prototype.querySelector.call(picks, selector));
const said = (el) => (el.children.some((child) => child.tag) ? el.children.filter((child) => child.tag && !child.getAttribute("aria-hidden")).map(said).filter(Boolean).join(" ") : text(el));
const effects = () => ({ title: text(page.ids["choice-title"]), action: text(page.ids["choice-ok"]),
  steps: picks.querySelectorAll(".step").map((step) => [text(step.querySelector("strong")), step.querySelectorAll(".step-items li").map(said)]) });
const settle = async () => {
  await flush();
  mock.timers.tick(600);
  await flush();
};
const answer = async (confirmed) => {   // "alt": the second button
  choice.returnValue = confirmed === "alt" ? "alt" : confirmed ? "ok" : "";
  choice.onclose();
  await settle();
};
const posts = () => server.calls.filter((call) => call.method === "POST").map((call) => [call.path, call.body]);
const startOfRow = (id) => row("playgrounds", id).querySelector(".list-actions .act");
// A Start asked for with the places as they are: its dialog, if it has one, is answered
async function start(id, state, places) {
  server.calls = [];
  draw(state, [ACCOUNT], places);
  startOfRow(id).click();
  await settle();
}

test("the Start of a row deploys to the place chosen on any page: it asks first, naming the place, and sends it back", async () => {
  server.dialog = { starts: [{ kind: "addon", id: "hybrid", name: "Hybrid gateway" }], stops: [], turns_off: [], keeps: [], restarts: [] };
  await start("acme-hostels", S1.calm, [place()]);
  assert.deepEqual(server.calls.map((call) => call.path), ["/api/playgrounds/acme-hostels/check?place=acme%2Fdevelopment"], "its checks are read for the place, now");
  assert.deepEqual(effects(), { title: "Start Acme Hostels", action: "Start",
    steps: [["Starts", ["Hybrid gateway Add-on"]], ["Deploys to", ["Development (My Account) Environment"]]] });
  assert.deepEqual(posts(), [], "nothing runs before it is confirmed");
  await answer(true);
  assert.deepEqual(posts(), [["/api/playgrounds/acme-hostels/start", { place: "acme/development", accept: [] }]]);
  // A remote place asks even when nothing else starts, and a refusal stops there
  server.dialog = null;
  await start("acme-hostels", S1.calm, [place()]);
  assert.deepEqual(effects().steps, [["Deploys to", ["Development (My Account) Environment"]]]);
  await answer(false);
  assert.deepEqual(posts(), []);
});

test("on This machine the Start of a row is what it always was: no place sent, and no dialog when nothing else changes", async () => {
  for (const [kept, platforms] of [["machine", [ACCOUNT]], [undefined, [ACCOUNT]], ["acme/development", []]]) {
    if (kept === undefined) delete page.stored.deploy;
    else page.stored.deploy = kept;
    server.calls = [];
    draw(S1.calm, platforms, [place()]);
    startOfRow("acme-hostels").click();
    await settle();
    assert.deepEqual(posts(), [["/api/playgrounds/acme-hostels/start", { accept: [] }]], `${kept}, ${platforms.length} platform`);
    assert.ok(!server.calls.some((call) => call.path.includes("/check")), "the state's dialog is enough: nothing is read");
  }
  page.stored.deploy = "acme/development";
});

test("a Start on images it was not tested with is the Start that switches them, as when it must, with a second button that keeps them", async () => {
  const versions = [{ kind: "version", id: "APIM", name: "API Management amp-demo-latest → 4.13.0-alpha.3" }];
  const offer = { starts: [{ kind: "playground", id: "acme-hostels", name: "Acme Hostels apps" }], versions };
  const hotels = { hotels: { action: { ...ACTION("start"), warns: true, offer } } };
  page.stored.deploy = "machine";
  server.dialog = null;
  await start("acme-hostels", lab(hotels), []);
  assert.deepEqual([effects().title, effects().action, text(page.ids["choice-alt"]), page.ids["choice-alt"].hidden],
    ["Start Acme Hostels", "Switch and start", "Start without switching", false]);
  assert.deepEqual(effects().steps, [["Starts", ["Acme Hostels apps Playground"]], ["Changes versions", ["API Management amp-demo-latest → 4.13.0-alpha.3 Version"]]],
    "the steps of the start that switches");
  assert.deepEqual(posts(), [], "nothing runs before it is confirmed");
  await answer("alt");
  assert.deepEqual(posts(), [["/api/playgrounds/acme-hostels/start", { accept: [] }]], "without switching, the start it always was");
  await start("acme-hostels", lab(hotels), []);
  await answer(true);
  assert.deepEqual(posts(), [["/api/playgrounds/acme-hostels/start", { accept: ["version:APIM"], switch: true }]], "the versions it showed");
  // Another dialog has no second button
  await start("acme-hostels", lab({ hotels: { action: { ...ACTION("start"), dialog: { ...offer, versions: [] } } } }), []);
  assert.equal(page.ids["choice-alt"].hidden, true);
  await answer(false);
  page.stored.deploy = "acme/development";
});

test("a Start says where its item comes from when the server says it, on This machine and on a place alike: one row among those that say what stays", async () => {
  const from = { from: [{ name: "Acme Hub" }], hints: { from: "Version 1.4.0" } };
  const starts = [{ kind: "addon", id: "kafka", name: "Kafka" }];
  const notes = () => picks.querySelectorAll(".choice-row").map((row) => [text(row.querySelector(".choice-row-label")), row.querySelectorAll(".pill").map(text),
    row.querySelectorAll(".muted").map(text)]);
  // On a place, the dialog is the one the checks of that place read
  server.dialog = { starts, stops: [], turns_off: [], keeps: [], restarts: [], ...from };
  await start("acme-hostels", S1.calm, [place()]);
  assert.deepEqual(effects().steps, [["Starts", ["Kafka Add-on"]], ["Deploys to", ["Development (My Account) Environment"]]]);
  assert.deepEqual(notes(), [["From", ["Acme Hub"], ["Version 1.4.0"]]]);
  await answer(false);
  // On This machine, the one the state gives the row
  page.stored.deploy = "machine";
  draw(lab({ hotels: { action: { ...ACTION("start"), dialog: { starts, stops: [], turns_off: [], keeps: [], restarts: [], ...from } } } }), [ACCOUNT], [place()]);
  startOfRow("acme-hostels").click();
  await settle();
  assert.deepEqual(notes(), [["From", ["Acme Hub"], ["Version 1.4.0"]]]);
  await answer(false);
  // Said by no one, it is not drawn
  server.dialog = null;
  draw(lab({ hotels: { action: { ...ACTION("start"), dialog: { starts, stops: [], turns_off: [], keeps: [], restarts: [] } } } }), [ACCOUNT], [place()]);
  startOfRow("acme-hostels").click();
  await settle();
  assert.deepEqual(notes(), []);
  await answer(false);
  page.stored.deploy = "acme/development";
});

test("a playground that cannot go to the place chosen starts on This machine, as its page does", async () => {
  await start("acme-bakery", lab({ hotels: {} }), [place()]);
  assert.deepEqual(posts(), [["/api/playgrounds/acme-bakery/start", { accept: [] }]]);
  assert.ok(!server.calls.some((call) => call.path.includes("/check")));
});

test("the Start of a row names This machine in its dialog once a platform is registered, when it asks, and not before", async () => {
  const dialog = { starts: [{ kind: "addon", id: "kafka", name: "Kafka" }], stops: [], turns_off: [], keeps: [], restarts: [] };
  const asking = lab({ hotels: { action: { ...ACTION("start"), dialog } } });
  page.stored.deploy = "machine";
  for (const [platforms, steps] of [[[ACCOUNT], [["Starts", ["Kafka Add-on"]], ["Deploys to", ["This machine Platform"]]]], [[], [["Starts", ["Kafka Add-on"]]]]]) {
    draw(asking, platforms, [place()]);
    startOfRow("acme-hostels").click();
    await settle();
    assert.deepEqual(effects().steps, steps, `${platforms.length} platform`);
    await answer(false);
  }
  page.stored.deploy = "acme/development";
});

test("the Start of a row waits for the places while a place is kept and they are not read: it never starts the wrong one", () => {
  draw(S1.calm, [ACCOUNT], [], false);
  assert.ok(isOff(startOfRow("acme-hostels")));
  assert.equal(offWhy(startOfRow("acme-hostels")), "Loading");
  assert.equal(noted(), null, "nothing to say yet");
  assert.ok(!isOff(startOfRow("acme-bakery")), "a playground that goes nowhere does not wait");
  draw(S1.calm, [ACCOUNT], [place()], false);
  assert.ok(isOff(startOfRow("acme-hostels")), "unread is unread, whatever the cache holds");
  draw(S1.calm, [ACCOUNT], [place()], true);
  assert.ok(!isOff(startOfRow("acme-hostels")));
});

test("the note of an add-on that needs a hybrid gateway leads to the Gravitee Cloud page", () => {
  const needs = (more) => lab({ ...more });
  const withNote = needs({});
  withNote.addons[0] = { ...withNote.addons[0], note: "Needs a hybrid gateway", note_tone: "warn", note_target: { kind: "platform" } };
  draw(withNote, [ACCOUNT], [place()]);
  const note = row("addons", "kafka").querySelector(".list-state .note");
  assert.equal(note.tag, "a");
  assert.equal(note.getAttribute("href"), "#/cloud");
  assert.equal(text(note), "Needs a hybrid gateway");
  withNote.addons[0] = { ...withNote.addons[0], note_target: { kind: "platform", id: "acme" } };
  draw(withNote, [ACCOUNT], [place()]);
  assert.equal(row("addons", "kafka").querySelector(".list-state .note").getAttribute("href"), "#/cloud/acme", "with an id, it is that platform's row");
});

// --- The catalogs ------------------------------------------------------------------------------------------------------------------------

// An item of another catalog, as the state gives it: its origin, read only, its path in the catalog; an item of this machine
const BLANK = { services: [], needs: [], conflicts: {}, restarts: [], brings: [], recreates: [] };
const made = (kind, id, name, more) => (kind === "playground" ? playground(id, name, more)
  : item(kind, id, name, kind === "setup" ? { members: [], includes: [], ...more } : { ...BLANK, ...more }));
const inHub = (kind, id, name, more = {}) => made(kind, `@acme-hub/${id}`, name, { origin: origin(), readonly: true, display_file: `${kind}s/${id}/compose.yml`,
  needs_catalogs: [], compat: "ok", twin: null, uses_machine: [], ...more });
const ofMine = (kind, id, name, more = {}) => made(kind, `local/${id}`, name, { personal: true, held: { share: "", delete: "" }, ...more });
const base = lab();
// The lab with another catalog added and this machine's items: what the lists tell apart
function world({ playgrounds = [], setups = [], addons = [], ...more } = {}) {
  return lab({ playgrounds: [...base.playgrounds, ...playgrounds], setups: [...base.setups, ...setups], addons: [...base.addons, ...addons],
    catalogs: [FIRST(), catalog(), PERSONAL()], content: content("ready"), ...more });
}
// With the first catalog alone, whatever else the state says: every filter of every list is as new
const alone = () => draw(lab({ catalogs: [FIRST()], content: content("ready") }));
// A row open, whether it was or not (open toggles it)
const opening = (name, id) => { if (!row(name, id).classList.contains("open")) open(name, id); };
const USES = [{ service: "k3s", what: ["runs privileged"], why: "Runs containerd and the kubelet" }];
const everyone = () => world({
  playgrounds: [inHub("playground", "ledger-demo", "Ledger demo"), ofMine("playground", "scratch", "Scratch")],
  setups: [inHub("setup", "duo", "Duo", { members: [{ id: "kafka", name: "Kafka" }], includes: [], note: "Needs a catalog", note_tone: "warn",
    note_target: { kind: "catalog", id: null }, note_causes: [NEEDS.missing], action: { ...ACTION("start"), enabled: false, reason: "Needs the catalog Partner Bank" } })],
  addons: [inHub("addon", "mock-bank", "Mock bank", { note: "Blocked", note_tone: "err", note_causes: [BLOCKED],
    action: { ...ACTION("start"), enabled: false, reason: BLOCKED.reasons[0] } }), inHub("addon", "k3s", "Kubernetes", { uses_machine: USES })],
});

// The control that tells the catalogs apart, as a list draws it: none, or the picker
const scope = (name) => {
  const choices = sections[name].querySelectorAll("[data-scope]"), picker = sections[name].querySelector("[data-catalogs]");
  return { choices: choices.map((b) => [text(b), b.getAttribute("aria-checked")]), picker: picker ? text(picker) : null };
};
const names = (name) => sections[name].querySelectorAll(".list-item .list-name").map(text);
const marks = (name, id) => row(name, id).querySelectorAll(".list-marks .pill").map(text);
const typed = (name, query) => {
  const search = sections[name].querySelector('[data-filter="q"]');
  search.value = query;
  search.dispatchEvent(new Ev("input"));
};
const closeMenu = () => page.doc.dispatchEvent(new Ev("click", { target: page.doc.body }));
// What the menu of a row offers: [entry, why it cannot be used]
const menuOf = (name, id) => {
  const more = row(name, id).querySelector("[data-more]");
  if (!more) return [];   // an item with nothing to offer has no button, but a place for it
  more.click();
  const found = page.ids.menu.querySelectorAll(".menu-item").map((el) => [text(el.querySelector("span")), el.hasAttribute("disabled") ? text(el.querySelector(".menu-hint")) : ""]);
  closeMenu();
  return found;
};

test("with the first catalog alone there is no chip, no picker and no scope control: nothing to tell apart", () => {
  alone();
  for (const name of ["playgrounds", "setups", "addons"]) {
    assert.deepEqual(scope(name), { choices: [], picker: null }, name);
    assert.equal(sections[name].querySelectorAll('[role="radiogroup"]').length, 0, name);
  }
  assert.deepEqual(marks("playgrounds", "acme-hostels"), []);
  assert.equal(sections.playgrounds.querySelectorAll(".list-marks .tip").length, 0);
});

test("with the first catalog's items and this machine's, the same picker tells them apart, and no Shared: nothing is shared with the project", async () => {
  draw(lab({ playgrounds: [...base.playgrounds, ofMine("playground", "scratch", "Scratch")], catalogs: [FIRST(), PERSONAL()], content: content("ready") }));
  assert.deepEqual(scope("playgrounds"), { choices: [], picker: "Catalog: All catalogs" });
  assert.deepEqual(scope("setups"), { choices: [], picker: null }, "a list with one source has no control");
  assert.deepEqual(marks("playgrounds", "local/scratch"), ["Personal"]);
  assert.deepEqual(marks("playgrounds", "acme-hostels"), []);
  assert.deepEqual(await opened("playgrounds"), [["All catalogs", "3", true], ["Gravitee public", "2", false], ["Personal", "1", false]]);
  await choose("Personal");
  assert.deepEqual(names("playgrounds"), ["Scratch"]);
  assert.equal(value("playgrounds"), "Personal");
  await choose("All catalogs");
  assert.deepEqual(names("playgrounds"), ["Acme Hostels", "Acme Bakery", "Scratch"], "all of them, in the server's order");
  alone();
});

test("with another catalog added the picker is drawn from the items, not from the markup of the page", () => {
  draw(everyone());
  assert.deepEqual(scope("playgrounds"), { choices: [], picker: "Catalog: All catalogs" });
  const button = sections.playgrounds.querySelector("[data-catalogs]");
  assert.deepEqual([button.getAttribute("class"), button.getAttribute("aria-haspopup")], ["combo-btn", "dialog"]);
  assert.equal(text(button.querySelector(".sr-only")), "Catalog:");
  assert.ok(button.parent.lastElementChild === button, "among the filters of the list, after them");
  assert.equal(sections.playgrounds.querySelectorAll('[role="radiogroup"]').length, 0);
  // A list whose items are one catalog's has none, whatever else is added
  draw(world());
  assert.deepEqual(scope("playgrounds"), { choices: [], picker: null }, "one source holds the items of this list");
  draw(everyone());
  assert.equal(scope("playgrounds").picker, "Catalog: All catalogs");
  alone();
  assert.equal(sections.playgrounds.querySelectorAll("[data-catalogs]").length, 0, "taken off once the catalogs are one");
});

// The picker, open: its options as [name, count, ticked]
const opened = async (name) => {
  sections[name].querySelector("[data-catalogs]").click();
  await flush();
  return page.doc.querySelector("#combo-list").querySelectorAll(".combo-option").map((o) => [text(o.querySelector(".name")), text(o.querySelector(".age")), o.classList.contains("selected")]);
};
const choose = async (label) => {
  page.doc.querySelector("#combo-list").querySelectorAll(".combo-option").find((o) => text(o.querySelector(".name")) === label).click();
  await flush();
};
const value = (name) => text(sections[name].querySelector("[data-catalogs] .value"));

test("the picker lists All catalogs, then the first catalog, the others as they were added, then Personal, each with its items in this list", async () => {
  const later = origin({ id: "later", name: "Later Hub" });
  draw(world({ catalogs: [FIRST(), catalog({ id: "later", name: "Later Hub" }), catalog(), PERSONAL()],
    playgrounds: [inHub("playground", "ledger-demo", "Ledger demo"), ofMine("playground", "scratch", "Scratch"),
      made("playground", "@later/one", "One", { origin: later, readonly: true }), made("playground", "@later/two", "Two", { origin: later, readonly: true })] }));
  assert.deepEqual(await opened("playgrounds"), [["All catalogs", "6", true], ["Gravitee public", "2", false], ["Later Hub", "2", false], ["Acme Hub", "1", false], ["Personal", "1", false]]);
  await choose("Acme Hub");
  assert.deepEqual(names("playgrounds"), ["Ledger demo"]);
  assert.equal(value("playgrounds"), "Acme Hub", "one choice shows its name");
  await choose("Personal");
  assert.equal(value("playgrounds"), "2 catalogs", "several show their number, not their names");
  assert.deepEqual(names("playgrounds"), ["Ledger demo", "Scratch"]);
  await choose("Gravitee public");
  assert.equal(value("playgrounds"), "3 catalogs");
  assert.deepEqual(names("playgrounds"), ["Acme Hostels", "Acme Bakery", "Ledger demo", "Scratch"]);
  await choose("All catalogs");
  assert.equal(value("playgrounds"), "All catalogs");
  assert.equal(names("playgrounds").length, 6);
  Combo.close();
  alone();
});

test("what was chosen is not kept: drawn again once the catalogs were one, the picker is empty", async () => {
  draw(everyone());
  await opened("playgrounds");
  await choose("Acme Hub");
  assert.deepEqual(names("playgrounds"), ["Ledger demo"]);
  Combo.close();
  alone();
  draw(everyone());
  assert.equal(names("playgrounds").length, 4, "all of them again");
  assert.equal(value("playgrounds"), "All catalogs");
  alone();
});

test("the progress of the fetch of an Add, carried among the catalogs, is no source of a list: no picker for it, and every list is as it is without it", () => {
  const check = { id: "check", kind: "progress", progress: 0.4 };
  const lists = () => ["playgrounds", "setups", "addons"].map((name) => drawn(sections[name]));
  draw(lab({ catalogs: [FIRST()], content: content("ready") }));
  const bare = lists();
  draw(lab({ catalogs: [FIRST(), check], content: content("ready") }));
  assert.deepEqual(lists(), bare, "the first catalog alone, and the fetch of another");
  assert.deepEqual(scope("playgrounds"), { choices: [], picker: null });
  draw(everyone());
  const world = lists();
  draw({ ...everyone(), catalogs: [...everyone().catalogs, check] });
  assert.deepEqual(lists(), world, "with another catalog, the picker and its rows are the same");
  draw({ ...everyone(), catalogs: [check, ...everyone().catalogs] });
  assert.deepEqual(lists(), world, "wherever it stands");
  alone();
});

test("the Catalogs page leads to what a catalog holds: the list opens filtered to it, and to this machine's", () => {
  alone();
  selectCatalog("playground", "acme-hub");
  draw(everyone());
  assert.deepEqual(names("playgrounds"), ["Ledger demo"]);
  assert.equal(value("playgrounds"), "Acme Hub");
  draw(everyone());
  assert.deepEqual(names("playgrounds"), ["Ledger demo"], "it stays until the person changes it");
  alone();
  draw(lab({ playgrounds: [...base.playgrounds, ofMine("playground", "scratch", "Scratch")], catalogs: [FIRST(), PERSONAL()] }));
  selectCatalog("playground", "local");
  draw(store.state);
  assert.deepEqual(names("playgrounds"), ["Scratch"], "with the two choices, Personal");
  alone();
  selectCatalog("playground", "nowhere");
  draw(everyone());
  assert.equal(names("playgrounds").length, 4, "a catalog the list does not hold chooses nothing");
  assert.equal(selectCatalog("nothing", "acme-hub"), undefined, "a kind that is no list");
  alone();
});

test("an item of another catalog says its catalog in its About, as a link with the version, and has no mark beside its name; the first catalog's says none", () => {
  const state = everyone();
  draw(state);
  for (const id of ["@acme-hub/ledger-demo", "local/scratch", "acme-hostels"]) opening("playgrounds", id);
  draw(state);
  const fact = (id) => row("playgrounds", id).querySelectorAll(".panel-fact").filter((one) => text(one.firstElementChild) === "Catalog");
  const [found] = fact("@acme-hub/ledger-demo");
  assert.equal(text(found.lastElementChild), "Acme Hub 1.4.0");
  assert.equal(found.querySelector("a").getAttribute("href"), "#/catalogs/acme-hub");
  assert.equal(row("playgrounds", "@acme-hub/ledger-demo").querySelectorAll(".list-marks .tip, .list-marks .pill").length, 0, "nothing beside its name");
  assert.deepEqual(marks("playgrounds", "local/scratch"), ["Personal"]);
  assert.equal(fact("local/scratch").length, 0);
  assert.equal(fact("acme-hostels").length, 0);
  for (const id of ["@acme-hub/ledger-demo", "local/scratch", "acme-hostels"]) if (row("playgrounds", id).classList.contains("open")) open("playgrounds", id);   // as they were, for the pages drawn next
  alone();
});

test("the search finds an item by the name of its catalog", () => {
  draw(everyone());
  typed("playgrounds", "acme hub");
  assert.deepEqual(names("playgrounds"), ["Ledger demo"]);
  typed("playgrounds", "");
  alone();
});

test("a Blocked item has one warning, beside its Start, whose popover lists its reasons and who to ask; its Start waits with the first reason", () => {
  draw(everyone());
  const note = row("addons", "@acme-hub/mock-bank").querySelector(".list-actions .tip");
  assert.ok(note.querySelector(".tip-btn").classList.contains("blocker"));
  assert.ok(note.querySelector(".tip-btn").classList.contains("err"));
  assert.deepEqual(bubble(note), ["Blocked", ...BLOCKED.reasons, "Ask the owner of Acme Hub to change it."]);
  assert.equal(row("addons", "@acme-hub/mock-bank").querySelectorAll(".list-state .pill").length, 0, "nothing of it in the State");
  const start = row("addons", "@acme-hub/mock-bank").querySelector(".list-actions .act");
  assert.deepEqual([isOff(start), offWhy(start)], [true, BLOCKED.reasons[0]]);
  alone();
});

test("an item that needs a catalog says so with a warning beside its Start that leads to the Catalogs page, and to the catalog's row when it is added", () => {
  draw(everyone());
  const mend = () => row("setups", "@acme-hub/duo").querySelector(".list-actions .tip-card .btn");
  const title = () => text(row("setups", "@acme-hub/duo").querySelector(".list-actions .pop-title"));
  assert.equal(row("setups", "@acme-hub/duo").querySelectorAll(".list-state .pill, .list-state .note").length, 0);
  assert.deepEqual([mend().tag, mend().getAttribute("href"), title()], ["a", "#/catalogs", "Needs a catalog"]);
  const state = everyone();
  state.setups = state.setups.map((s) => (s.id === "@acme-hub/duo" ? { ...s, note: "Needs Partner Bank 2.0 or later", note_target: { kind: "catalog", id: "partner-bank" } } : s));
  draw(state);
  assert.deepEqual([mend().getAttribute("href"), title()], ["#/catalogs/partner-bank", "Needs Partner Bank 2.0 or later"]);
  alone();
});

test("the file button of an item of a catalog reads View with the file's name; its menu has no Delete, no Share and no Edit, a setup keeps Duplicate, a playground has Export, and each Copy to Personal", () => {
  draw(everyone());
  const file = row("playgrounds", "@acme-hub/ledger-demo").querySelector(".row-icons [data-file]");
  assert.deepEqual([file.getAttribute("data-tip"), file.getAttribute("aria-label")], ["View compose.yml", "View compose.yml"]);
  assert.equal(row("playgrounds", "local/scratch").querySelector(".row-icons [data-file]").getAttribute("data-tip"), "Edit playgrounds/local/scratch/compose.yml");
  assert.deepEqual(menuOf("setups", "@acme-hub/duo").map(([label]) => label), ["Duplicate", "Copy to Personal"]);
  assert.deepEqual(menuOf("playgrounds", "@acme-hub/ledger-demo").map(([label]) => label), ["Export", "Copy to Personal"]);
  assert.deepEqual(menuOf("playgrounds", "local/scratch").map(([label]) => label), ["Export", "Share", "Delete"]);
  alone();
  draw(world({ playgrounds: [made("playground", "bakery", "Bakery")] }));
  assert.deepEqual(menuOf("playgrounds", "bakery").map(([label]) => label), ["Copy to Personal"], "one of the default catalog comes with every Lab: nothing to export");
  alone();
});

test("the file button says the path the server reads an item by (display_file), and opens the file that is its own", () => {
  const file = ".lab/catalogs/local/playgrounds/scratch/compose.yml";
  draw(world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { file, display_file: "playgrounds/scratch/compose.yml" })] }));
  const button = () => row("playgrounds", "local/scratch").querySelector(".row-icons [data-file]");
  assert.deepEqual([button().getAttribute("data-file"), button().getAttribute("data-tip")], [file, "Edit playgrounds/scratch/compose.yml"]);
  draw(world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { file })] }));
  assert.equal(button().getAttribute("data-tip"), `Edit ${file}`, "a server that does not say another is read by its own");
  alone();
});

test("an item of this machine that a catalog now holds too offers the catalog's, held while either runs", async () => {
  const twin = { id: "@acme-hub/scratch", name: "Acme Hub" };
  const state = (mineMore = {}, hubMore = {}) => world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { twin, ...mineMore }),
    inHub("playground", "scratch", "Scratch", { twin: { id: "local/scratch", name: "Personal" }, ...hubMore })] });
  const entry = () => menuOf("playgrounds", "local/scratch").filter(([label]) => label === "Use the catalog version");
  draw(state());
  assert.deepEqual(entry(), [["Use the catalog version", ""]]);
  assert.deepEqual(menuOf("playgrounds", "@acme-hub/scratch").map(([label]) => label), ["Export"], "only the item of this machine has it");
  draw(state({ on: true, up: true, state: RUNNING }));
  assert.deepEqual(entry(), [["Use the catalog version", "Turn off Scratch first"]]);
  draw(state({}, { on: true, up: true, state: RUNNING }));
  assert.deepEqual(entry(), [["Use the catalog version", "Turn off Scratch first"]]);
  draw(state());
  // Chosen: the dialog says what goes and what stays, and the request goes with the item's key
  row("playgrounds", "local/scratch").querySelector("[data-more]").click();
  server.calls = [];
  page.doc.dispatchEvent(new Ev("click", { target: page.ids.menu.querySelectorAll(".menu-item").find((el) => text(el).includes("catalog version")) }));
  await flush();
  assert.equal(text(page.ids["choice-title"]), "Use the catalog version");
  assert.equal(text(page.ids["choice-ok"]), "Use");
  assert.deepEqual(picks.querySelectorAll(".step").map((step) => [text(step.querySelector("strong")), step.querySelectorAll(".step-items li").map(said)]), [["Deletes", ["Your copy"]]]);
  await answer(true);
  assert.deepEqual(posts(), [["/api/playgrounds/local/scratch/use-catalog", {}]]);
  alone();
});

// The unit's own words about why it cannot run, which the server refuses with (contribute.use_catalog_version): the same cases are run through
// that function by test_static.py, so that the page and the server cannot say it two ways
const CANNOT_RUN = JSON.parse(readFileSync(new URL("./cannot-run.json", import.meta.url), "utf8"));

test("Use the catalog version waits, saying why in the server's words, while the catalog's item is blocked, needs a catalog or needs another level of the Lab", () => {
  const twin = { id: "@acme-hub/scratch", name: "Acme Hub" };
  const entry = () => menuOf("playgrounds", "local/scratch").filter(([label]) => label === "Use the catalog version");
  for (const { about, unit, why } of CANNOT_RUN) {
    draw(world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { twin }),
      inHub("playground", "scratch", "Scratch", { twin: { id: "local/scratch", name: "Personal" }, ...unit })] }));
    assert.deepEqual(entry(), [["Use the catalog version", why]], about);
  }
  // It is the permanent reason the server says first, before the item that runs: turning it off would not mend it
  draw(world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { twin, on: true, up: true, state: RUNNING }),
    inHub("playground", "scratch", "Scratch", { twin: { id: "local/scratch", name: "Personal" }, blocked: { reasons: ["Mounts the Docker socket"] }, needs_catalogs: [], compat: "ok" })] }));
  assert.deepEqual(entry(), [["Use the catalog version", "Mounts the Docker socket"]]);
  // A reason that is markup is text
  draw(world({ playgrounds: [ofMine("playground", "scratch", "Scratch", { twin }),
    inHub("playground", "scratch", "Scratch", { twin: { id: "local/scratch", name: "Personal" }, blocked: { reasons: [HOSTILE] }, needs_catalogs: [{ id: "x", name: HOSTILE }] })] }));
  assert.deepEqual(entry(), [["Use the catalog version", `${HOSTILE}; Needs ${HOSTILE}`]]);
  assert.equal(page.ids.menu.querySelectorAll("img").length, 0);
  alone();
});

test("what an item takes of this machine is one pair of its opened row, in its author's words", () => {
  const state = everyone();
  draw(state);
  opening("addons", "@acme-hub/k3s");
  opening("addons", "@acme-hub/mock-bank");
  draw(state);
  const facts = (id) => row("addons", id).querySelectorAll(".panel-fact").map((fact) => [text(fact.firstElementChild), text(fact.lastElementChild)]);
  assert.deepEqual(facts("@acme-hub/k3s"), [["Catalog", "Acme Hub 1.4.0"], ["Uses this machine", "Runs containerd and the kubelet"]]);
  assert.deepEqual(facts("@acme-hub/mock-bank"), [["Catalog", "Acme Hub 1.4.0"]], "most items use nothing of it");
  alone();
});

test("a list's address takes the id of any catalog's item, and a playground's own address is its page", () => {
  const hit = (target, hash) => target.pattern.exec(hash)?.slice(1);
  assert.deepEqual(hit(addonsPage, "#/addons"), [undefined]);
  assert.deepEqual(hit(addonsPage, "#/addons/kafka"), ["kafka"]);
  assert.deepEqual(hit(setupsPage, "#/setups/local/mine"), ["local/mine"]);
  assert.deepEqual(hit(addonsPage, "#/addons/@acme-hub/mock-bank"), ["@acme-hub/mock-bank"]);
  assert.equal(hit(addonsPage, "#/addons/@acme-hub/"), undefined);
  assert.equal(hit(addonsPage, "#/addons/@acme-hub/../x"), undefined);
  assert.equal(hit(playgroundsPage, "#/playgrounds/@acme-hub/bank"), undefined, "a page of its own, not a row");
  assert.deepEqual(hit(playgroundsPage, "#/playgrounds"), []);
});

test("an address that names an item of another catalog finds it through a filter that hides it, opens it and focuses its toggle", async () => {
  Combo.close();
  page.body.hidden = false;   // the drawer of the jobs the tests above started was hidden by a node the small DOM took for it
  draw(everyone());
  await settle();   // what the tests before left to do is done
  typed("addons", "zzz");
  assert.deepEqual(names("addons"), []);
  addonsPage.show(["@acme-hub/k3s"]);
  draw(everyone());
  assert.ok(names("addons").includes("Kubernetes"), "its row shows");
  assert.equal(sections.addons.querySelector('[data-filter="q"]').value, "", "the filters were cleared for it");
  assert.equal(row("addons", "@acme-hub/k3s").classList.contains("open"), true);
  await settle();
  assert.ok(page.focused() === row("addons", "@acme-hub/k3s").querySelector(".s-toggle"));
  assert.equal(row("addons", "@acme-hub/k3s").scrolled.length, 1);
  // The list's own address gives the list back as it was before, its search too
  addonsPage.show([]);
  assert.equal(sections.addons.querySelector('[data-filter="q"]').value, "zzz");
  typed("addons", "");
  alone();
});

test("Back from an address that opened a row gives back the catalogs the list was narrowed to", async () => {
  Combo.close();
  page.body.hidden = false;
  draw(everyone());
  await settle();
  const all = names("addons");
  await opened("addons");
  await choose("Gravitee public");
  Combo.close();
  const narrowed = names("addons");
  assert.ok(narrowed.length > 0 && narrowed.length < all.length, "the picker narrowed the list");
  addonsPage.show(["@acme-hub/k3s"]);
  draw(everyone());
  assert.equal(value("addons"), "All catalogs", "the row an address names shows, whatever the filters hid");
  assert.ok(names("addons").includes("Kubernetes"));
  addonsPage.show([]);
  assert.equal(value("addons"), "Gravitee public", "the list's own address gives the catalogs back");
  draw(store.state);
  assert.deepEqual(names("addons"), narrowed);
  // What is changed by hand while the row is open is kept instead
  addonsPage.show(["@acme-hub/k3s"]);
  draw(everyone());
  await opened("addons");
  await choose("Acme Hub");
  Combo.close();
  addonsPage.show([]);
  assert.equal(value("addons"), "Acme Hub");
  alone();
});

// --- The content of the catalogs: in place of the rows, and above them --------------------------------------------------------------------------

const noItem = (key, more = {}, catalogs = []) => lab({ playgrounds: [], setups: [], addons: [], catalogs, content: content(key, more) });
const status = (name) => sections[name].querySelectorAll('[role="status"]');
const tools = (name) => sections[name].querySelector(".list-tools").hidden;
const line = (el) => text(el);
const buttons = (el) => el.querySelectorAll("[data-content-act]").map((b) => [b.dataset.contentAct, b.dataset.catalog, line(b)]);

test("with no item at all the list says why in place of its rows: Downloading with the bar and where from, the failure with Try again, no catalog with what to add", () => {
  draw(noItem("downloading", { progress: 0.4 }, [FIRST({ commit: "", progress: 0.4 })]));
  for (const name of ["playgrounds", "setups", "addons"]) {
    assert.equal(status(name).length, 1, name);
    assert.deepEqual([status(name)[0].getAttribute("data-content"), status(name)[0].hasAttribute("data-line"), tools(name)], ["downloading", false, true], name);
    assert.equal(sections[name].querySelectorAll(".list-item").length, 0);
    assert.notEqual(sections[name].querySelector(".list-card > .empty[hidden]"), null, `${name}: the words for filters that find nothing are not shown`);
  }
  assert.equal(line(status("playgrounds")[0].querySelector(".loading .tip-btn")), "Downloading");
  assert.deepEqual(bubble(status("playgrounds")[0].querySelector(".tip")), ["From github.com/example/catalog"]);
  draw(noItem("failed", {}, [catalog({ id: "gravitee", commit: "" })]));
  assert.equal(line(status("addons")[0].firstElementChild), "Cannot reach github.com. Check your connection, then try again.");
  assert.deepEqual(buttons(status("addons")[0]), [["retry", "gravitee", "Try again"]]);
  draw(noItem("none"));
  assert.equal(line(status("setups")[0].firstElementChild), "No catalog added");
  assert.deepEqual(buttons(status("setups")[0]), [["add", "gravitee", "Add Gravitee public"]]);
  draw(noItem("none", { offer: null }));
  assert.deepEqual(status("playgrounds")[0].querySelectorAll("a").map((a) => [line(a), a.getAttribute("href")]), [["Browse catalogs", "#/catalogs"]]);
  alone();
});

test("once the catalogs are there and hold none of its kind, each list says so in its own words, and the head keeps New", () => {
  draw(noItem("ready", {}, [FIRST()]));
  const all = ["playgrounds", "setups", "addons"];
  assert.deepEqual(all.map((name) => line(status(name)[0])), ["No playground yet", "No setup yet", "No add-on yet"]);
  assert.deepEqual(all.map(tools), [true, true, true]);
  assert.deepEqual(all.map((name) => line(sections[name].querySelector("[data-new]"))), ["New playground", "New setup", "New add-on"]);
  // A server that does not say the content: the lists say what they say when it is all there
  draw(lab({ playgrounds: [], setups: [], addons: [] }));
  assert.deepEqual(all.map((name) => line(status(name)[0])), ["No playground yet", "No setup yet", "No add-on yet"]);
  alone();
  assert.deepEqual(all.map((name) => [status(name).length, tools(name)]), [[0, false], [0, false], [0, false]], "gone once there are items");
});

test("with items of this machine and the catalogs not there, the status is one line above the rows, which stay, and the tools stay; it is gone when they are", () => {
  const mineOnly = (key, more) => lab({ playgrounds: [ofMine("playground", "scratch", "Scratch")], setups: [], addons: [], catalogs: [PERSONAL(), catalog({ id: "gravitee", commit: "" })], content: content(key, more) });
  draw(mineOnly("downloading", { progress: 0.2 }));
  const block = status("playgrounds")[0];
  assert.deepEqual([block.hasAttribute("data-line"), tools("playgrounds")], [true, false]);
  assert.deepEqual(names("playgrounds"), ["Scratch"], "the rows are never hidden");
  const card = sections.playgrounds.querySelector(".list-card").children.filter((child) => child.tag).map((child) => child.getAttribute("class"));
  assert.ok(card.indexOf("list-head list-grid") < card.indexOf("empty") && card.indexOf("empty") < card.indexOf("list-rows"), "between the head of the columns and the rows");
  assert.equal(status("setups")[0].hasAttribute("data-line"), false, "the lists that have none say it as a block");
  draw(mineOnly("failed"));
  assert.equal(line(status("playgrounds")[0].firstElementChild), "Cannot reach github.com. Check your connection, then try again.");
  assert.deepEqual(names("playgrounds"), ["Scratch"]);
  draw(mineOnly("none"));
  assert.deepEqual(buttons(status("playgrounds")[0]), [["add", "gravitee", "Add Gravitee public"]]);
  draw(lab({ playgrounds: [ofMine("playground", "scratch", "Scratch")], catalogs: [FIRST(), PERSONAL()], content: content("ready") }));
  assert.equal(status("playgrounds").length, 0);
  alone();
});

test("the status is written once and stays while it does not change: the bar is moved, not made again at every refresh", () => {
  draw(noItem("downloading", { progress: 0.1 }, [FIRST({ commit: "", progress: 0.1 })]));
  const block = status("playgrounds")[0];
  draw(noItem("downloading", { progress: 0.6 }, [FIRST({ commit: "", progress: 0.6 })]));
  assert.ok(status("playgrounds")[0] === block);
  draw(noItem("failed", {}, [catalog({ id: "gravitee", commit: "" })]));
  assert.ok(status("playgrounds")[0] !== block);
  assert.equal(status("playgrounds").length, 1, "one status, never two");
  alone();
});

test("Try again and Add the catalog that is listed answer from the lists", async () => {
  draw(noItem("failed", {}, [catalog({ id: "gravitee", commit: "" })]));
  server.calls = [];
  page.doc.dispatchEvent(new Ev("click", { target: status("playgrounds")[0].querySelector("[data-content-act]") }));
  await flush();
  assert.deepEqual(posts(), [["/api/catalogs/gravitee/check", {}]]);
  draw(noItem("none"));
  server.calls = [];
  page.doc.dispatchEvent(new Ev("click", { target: status("setups")[0].querySelector("[data-content-act]") }));
  await flush();
  assert.deepEqual(posts(), [["/api/catalogs", { listed: "gravitee" }]]);
  alone();
});

test("a row's address that names an item about to arrive is waited for while the catalogs download; it opens when it arrives, and is given up when they are done and it is not there", async () => {
  Combo.close();
  page.body.hidden = false;
  alone();
  await settle();
  addonsPage.show(["@acme-hub/k3s"]);
  draw(lab({ catalogs: [FIRST()], content: content("downloading") }));
  assert.equal(row("addons", "@acme-hub/k3s"), null);
  draw(lab({ catalogs: [FIRST()], content: content("downloading") }));
  assert.ok(names("addons").length > 0, "what is there is shown meanwhile");
  draw(everyone());
  assert.equal(row("addons", "@acme-hub/k3s").classList.contains("open"), true, "it opens when it arrives");
  await settle();
  assert.ok(page.focused() === row("addons", "@acme-hub/k3s").querySelector(".s-toggle"), "and takes the focus");
  // Given up: the catalogs are done and it is not there; when it comes later nothing opens it
  addonsPage.show([]);
  addonsPage.show(["@acme-hub/late"]);
  draw(lab({ catalogs: [FIRST()], content: content("ready") }));
  draw(world({ addons: [inHub("addon", "late", "Late")] }));
  assert.equal(row("addons", "@acme-hub/late").classList.contains("open"), false);
  addonsPage.show([]);
  alone();
});

test("a row's address that names an item of this machine is not waited for while the catalogs download: those are listed at once, so it is a dead address", async () => {
  Combo.close();
  page.body.hidden = false;
  alone();
  await settle();
  addonsPage.show(["local/late"]);
  draw(lab({ catalogs: [FIRST()], content: content("downloading") }));
  draw(world({ addons: [ofMine("addon", "late", "Late")], content: content("downloading") }));
  assert.equal(row("addons", "local/late").classList.contains("open"), false, "nothing was kept for it: it does not open when it is there");
  addonsPage.show([]);
  alone();
});

test("a catalog's name, an author's words and a failure line are text, never markup", () => {
  const state = world({ playgrounds: [inHub("playground", "x", "X", { origin: origin({ name: HOSTILE }), uses_machine: [{ service: "a", what: [], why: HOSTILE }] })],
    catalogs: [FIRST(), catalog({ name: HOSTILE }), PERSONAL()] });
  draw(state);
  opening("playgrounds", "@acme-hub/x");
  draw(state);
  assert.equal(sections.playgrounds.querySelectorAll("img").length, 0);
  draw(noItem("failed", { line: HOSTILE, address: HOSTILE }, [catalog({ commit: "" })]));
  assert.equal(sections.playgrounds.querySelectorAll("img").length, 0);
  alone();
});

// --- The snapshots ----------------------------------------------------------------------------------------------------------------------------

function empties() {
  const dom = {}, all = ["playgrounds", "setups", "addons"];
  const mine = (key, more) => lab({ playgrounds: [ofMine("playground", "scratch", "Scratch")], setups: [], addons: [], catalogs: [PERSONAL(), catalog({ id: "gravitee", commit: "" })], content: content(key, more) });
  draw(noItem("downloading", { progress: 0.42 }, [FIRST({ commit: "", progress: 0.42 })]));
  for (const name of all) dom[`${name}: downloading`] = drawn(sections[name]);
  draw(noItem("failed", {}, [catalog({ id: "gravitee", commit: "" })]));
  dom["playgrounds: failed"] = drawn(sections.playgrounds);
  draw(noItem("none"));
  dom["playgrounds: no catalog"] = drawn(sections.playgrounds);
  draw(noItem("none", { offer: null }));
  dom["playgrounds: no catalog, none to offer"] = drawn(sections.playgrounds);
  draw(noItem("ready", {}, [FIRST()]));
  for (const name of all) dom[`${name}: nothing of its kind`] = drawn(sections[name]);
  draw(mine("downloading", { progress: 0.42 }));
  dom["playgrounds: downloading, an item of this machine"] = drawn(sections.playgrounds);
  draw(mine("failed"));
  dom["playgrounds: failed, an item of this machine"] = drawn(sections.playgrounds);
  alone();
  return dom;
}

function withCatalogs() {
  const dom = {}, state = everyone();
  draw(state);
  opening("addons", "@acme-hub/k3s");
  opening("playgrounds", "@acme-hub/ledger-demo");
  draw(state);
  for (const name of ["playgrounds", "setups", "addons"]) dom[`${name}: two catalogs and this machine`] = drawn(sections[name]);
  selectCatalog("playground", "acme-hub");
  draw(state);
  dom["playgrounds: filtered to the other catalog"] = drawn(sections.playgrounds);
  draw(lab({ playgrounds: [...base.playgrounds, ofMine("playground", "scratch", "Scratch")], catalogs: [FIRST(), PERSONAL()], content: content("ready") }));
  dom["playgrounds: the first catalog and this machine"] = drawn(sections.playgrounds);
  alone();
  return dom;
}

test("the clock of the page is the mock's: real time that passes, as it does on a loaded machine, changes nothing of what a button says of its action", () => {
  const at = Date.now();
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
  assert.equal(Date.now(), at, "only the ticks of the tests make time pass");
  mock.timers.tick(1600);
  assert.equal(Date.now(), at + 1600);
});

const snapshot = (name, file, variable, dom) => test(`${name}: what the lists draw is written in ${file.pathname.split("/").pop()}, and a deliberate change is written again with ${variable}=1`, () => {
  mock.timers.tick(10000);   // the lists at rest: no button still says how its action went, which the tests above leave in flight
  if (process.env[variable]) writeFileSync(file, written(dom()));
  const now = dom(), then = read(file);
  assert.deepEqual(Object.keys(now), Object.keys(then));
  for (const key of Object.keys(then)) {
    assert.equal(now[key], then[key], key);
  }
});
snapshot("with the content of the catalogs on its way, missing or empty", EMPTY, "WRITE_EMPTY", empties);
snapshot("with another catalog added", CATALOGS, "WRITE_CATALOGS", withCatalogs);

// Last: the button of a failed action says so for a moment, which the tests above do not wait for
test("what the checks of the place cannot read is said in a toast, and nothing starts", async () => {
  mock.timers.tick(10000);   // the toasts of the tests above are gone
  const toasts = () => page.doc.querySelector("#toasts").children.map(text);
  const fetched = globalThis.fetch;
  globalThis.fetch = async (path, options) => (path.includes("/check")
    ? { ok: false, status: 400, headers: { get: () => "" }, json: async () => ({ error: "Cloud Token for Development (My Account) has expired" }) } : fetched(path, options));
  await start("acme-hostels", S1.calm, [place()]);
  globalThis.fetch = fetched;
  assert.deepEqual(toasts(), ["Cloud Token for Development (My Account) has expired"]);
  assert.deepEqual(posts(), []);
});
