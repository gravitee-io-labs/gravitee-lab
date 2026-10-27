// The page of a running playground (web/js/pages/playground.js), run for real over a small DOM: one rule for every playground, state and
// width. Its head names the first console, its guide is beside its UIs, the details are the last tab and list what there is to open, and
// a page of its guide is turned in place of the one on screen, running or not. Once a platform is registered, every playground says
// where it deploys: the picker (a place it cannot go to is listed with its reason), the buttons that follow it, the lines of Before you
// start for the place, the places that hold what its seed created, and the Stop that says what stays or goes in each. The place chosen
// on any page is the default of every playground (web/js/platform/places.js). A playground of another catalog says which after its star and what it
// lacks of the catalogs and of this Lab before it can start; one of this machine has a More with its Share and its Delete; the address of one
// that is about to arrive while the catalogs download is not given up.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { HOSTILE, catalog as catalogRow, content, FIRST, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
// A timer set with no delay (the menu's, in ui.js) is NaN for the mock, which then loses the timers that follow it
const setTimer = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => setTimer(fn, ms ?? 0, ...rest);
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const found = { answers: true, frameable: true, reason: "" };
const JOB = { id: "ab12cd34", for: "playground:hotels", verb: "start", title: "Start Acme Hostels", done: false, ok: null, progress: null, phases: [], started: 0 };
// What the server answers beyond a guide: the places, the checks (for the place asked) and their dialog, and every call the page made
const server = { places: [], down: false, checks: () => [], dialog: () => null, calls: [] };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path, options = {}) => {
    const [route, query = ""] = path.split("?");
    const call = { method: options.method || "GET", path, body: options.body ? JSON.parse(options.body) : undefined };
    if (route !== "/api/state") server.calls.push(call);
    if (route === "/api/state") return reply(store.state);
    if (route === "/api/places") return server.down ? reply({ error: "Broken" }, 500) : reply(server.places);
    if (route.endsWith("/guide")) return reply({ markdown: "# Guide\n\n## 1. One\n\nClick it.\n" });
    if (route.endsWith("/check")) return reply({ checks: server.checks(query), dialog: server.dialog(query) });
    if (route.endsWith("/export")) return reply(call.body?.check ? server.exported : { ...server.exported, data: btoa("PK") });
    if (route.endsWith("/standalone")) return reply(call.body?.check ? server.standalone : { ...server.standalone, data: btoa("PK") });
    if (route.endsWith("/move")) return reply(call.body?.check ? server.movePlan : { moved: [], id: "local/hotels", ...server.moveDone });
    if (route.endsWith("/copy")) return reply(call.body?.check ? server.copyPlan : { copies: [], id: "local/hotels" });
    if (call.method === "POST") return reply(JOB);
    return reply(found);
  },
});
globalThis.EventSource = class { close() {} addEventListener() {} };   // a job's output, which the drawer follows
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });   // a button gives its face back at once
// The two libraries a guide is rendered with are loaded from a CDN by the page: stand-ins here, and a script loads at once
globalThis.marked = { parse: (markdown) => `<p>${markdown.replace(/\n+/g, " ")}</p>` };
globalThis.DOMPurify = { addHook() {}, sanitize: (html) => html };
page.doc.head.append = (script) => { queueMicrotask(() => script.onload()); };
globalThis.scrollBy = () => {};   // the guide of a playground that does not run comes into view under the page's head
const { router, store } = await import("../../../web/js/core.js");
const { playgroundPage, confirmStart, seeded } = await import("../../../web/js/pages/playground.js");

const HEALTHY = { key: "healthy", label: "Healthy", running: true };
const STOPPED = { key: "stopped", label: "Stopped", running: false };
const tab = (id, label, port, state) => ({ id, label, url: `http://localhost:${port}`, service: id, up: state.running, state });
const link = (label, service, port, state, section = "") => ({ label, url: `http://localhost:${port}`, port: String(port), service, section, up: state.running });

function playground(id, { tabs, guide = "abc", on = true, links = [] }) {
  return {
    kind: "playground", id, name: id, on, up: on, state: { key: on ? "running" : "off", label: on ? "Running" : "" }, note: "", note_tone: "", note_title: "",
    description: "", tags: [], chapters: [], guide, accounts: [], requires: {}, services: [], apps: [], addons: [], setups: [], includes: [], created: [],
    seed: null, reseed: null, brings: [], recreates: [], stop: { stops: [], turns_off: [], keeps: [], delete: null }, tabs, links, file: `playgrounds/${id}/compose.yml`,
    action: { do: on ? "stop" : "start", label: on ? "Stop" : "Start", enabled: true, reason: null, dialog: null }, secondary: null, source: null, duration: null,
    first_start: null, env_file: "", settings: [], personal: false, held: null, failed_job: null, login: null,
  };
}

const SERVICES = { "bakery-web": { label: "Acme Bakery", state: HEALTHY }, "gamma-ui": { label: "Gamma Console", state: HEALTHY }, "zz-app": { label: "Zz", state: STOPPED },
  "apim-ui": { label: "API Management", state: STOPPED }, "other-ui": { label: "Other", state: HEALTHY } };
const BANK = tab("bakery-web", "Acme Bakery", 8110, HEALTHY), GAMMA = tab("gamma-ui", "Gamma Console", 8086, HEALTHY);
const PLAYGROUNDS = [
  playground("acme-bakery", { tabs: [BANK, GAMMA], links: [link("Acme Bakery", "bakery-web", 8110, HEALTHY, "Playground"), link("Gamma Console", "gamma-ui", 8086, HEALTHY, "API Management")] }),
  playground("zz-down", { tabs: [tab("zz-app", "Zz", 8199, STOPPED), GAMMA] }),
  playground("no-console", { tabs: [] }),
  playground("no-guide", { tabs: [BANK], guide: null }),
  playground("stopped", { tabs: [BANK], on: false }),
];
store.state = {
  blind: false, wait: "", docker: { error: null }, services: SERVICES, playgrounds: PLAYGROUNDS, job: null, addons: [], setups: [],
  platform: { groups: [{ name: "API Management", services: ["gamma-ui", "apim-ui"] }] }, categories: [],
  links: [link("Acme Bakery", "bakery-web", 8110, HEALTHY, "Playground"), link("Gamma Console", "gamma-ui", 8086, HEALTHY, "API Management"),
    link("API Management console", "apim-ui", 8083, STOPPED, "API Management"), link("Other", "other-ui", 8199, HEALTHY, "Other")],
};

const section = page.el("page-playground", "section");
playgroundPage.init(section);
const draw = async (id) => {
  playgroundPage.show([id]);
  playgroundPage.render(store.state);
  await flush();
};
const head = () => section.querySelectorAll("#pg-actions a, #pg-actions button").map((el) => [el.tag.toLowerCase(), el.textContent.trim(), isOff(el) ? "disabled" : "", el.getAttribute("href") || ""]);
const tabs = () => section.querySelectorAll('[role="tab"]').map((el) => el.dataset.tab);

const setWidth = (width) => {
  page.body.clientWidth = width + 24;
  globalThis.innerWidth = width;
  page.doc.documentElement.clientWidth = width;
};

test("the head of a running playground opens its first console, beside the guide or under it", async () => {
  for (const width of [1500, 900]) {
    setWidth(width);
    await draw("acme-bakery");
    page.resized.run();   // the window changed under the same card
    assert.equal(section.querySelector(".pg-split").classList.contains("rows"), width < 1090, `${width} px`);
    assert.deepEqual(head().filter(([, text]) => text.startsWith("Open")), [["a", "Open Acme Bakery", "", "http://localhost:8110"]], `${width} px`);
    assert.deepEqual(tabs(), ["bakery-web", "gamma-ui", "@details"]);
  }
});

test("the guide of a running playground can be hidden, to give its consoles the whole card, and the browser keeps that choice", async () => {
  await draw("acme-bakery");
  const card = () => section.querySelector(".pg-split"), button = () => section.querySelector("[data-guide]");
  const said = () => [card().classList.contains("guide-hidden"), button().getAttribute("aria-expanded"), button().getAttribute("aria-label")];
  assert.deepEqual(said(), [false, "true", "Hide the guide"]);
  assert.equal(button().getAttribute("aria-controls"), "pg-guide-pane");
  button().click();
  assert.deepEqual(said(), [true, "false", "Show the guide"]);
  assert.equal(page.stored["guide:pane"], "hidden");
  await draw("acme-bakery");   // another visit: as it was left
  assert.deepEqual(said(), [true, "false", "Show the guide"]);
  button().click();
  assert.deepEqual(said(), [false, "true", "Hide the guide"]);
  assert.equal(page.stored["guide:pane"], undefined, "shown is the default: nothing is kept");
});

test("a first console that does not run is a disabled Open that says why, not another button", async () => {
  await draw("zz-down");
  const [open] = head().filter(([, text]) => text.startsWith("Open"));
  assert.deepEqual(open, ["button", "Open Zz", "disabled", ""]);
  const off = section.querySelector('#pg-actions button[aria-disabled="true"]');
  assert.equal(offWhy(off), "Zz is not running");
  assert.equal(off.getAttribute("aria-label"), "Open Zz", "it keeps its name while it waits");
});

test("a playground that lists no console is the same page: the split, its details alone, and no Open", async () => {
  await draw("no-console");
  assert.deepEqual(tabs(), ["@details"]);
  assert.ok(section.querySelector(".pg-split"));
  assert.deepEqual(head().filter(([, text]) => text.startsWith("Open")), []);
  const names = () => section.querySelectorAll("#pg-links .link strong").map((el) => el.textContent);
  assert.equal(section.querySelector("#pg-open").hidden, false);
  assert.deepEqual(names(), ["Gamma Console", "API Management console"], "the platform's consoles, whether they run or not; not the console of an add-on it does not bring");
  const links = store.state.links;
  store.state.links = [];
  await draw("no-console");
  assert.equal(section.querySelector("#pg-open").hidden, true, "nothing to open: no section, whatever the layout");
  store.state.links = links;
});

test("a playground without a guide is the same page: the guide's place says so", async () => {
  await draw("no-guide");
  assert.ok(section.querySelector(".pg-split"));
  assert.match(section.querySelector("#pg-guide").innerHTML, /No guide/);
});

test("before it runs there is no split and no Open: its details and its Start", async () => {
  await draw("stopped");
  assert.equal(section.querySelector(".pg-split"), null);
  assert.deepEqual(head().filter(([, text]) => text.startsWith("Open")), []);
});

test("the details list the consoles of the playground and of the platform alike, each gray while its service does not run", async () => {
  await draw("acme-bakery");
  const rows = () => section.querySelectorAll("#pg-links .link").map((el) => [el.querySelector("strong").textContent, el.classList.contains("off")]);
  assert.deepEqual(rows().sort(), [["Acme Bakery", false], ["API Management console", true], ["Gamma Console", false]].sort());
  assert.equal(section.querySelectorAll("#pg-links .link").find((el) => el.classList.contains("off")).getAttribute("data-tip"), "API Management console is not running");
});

// --- Deploy to: where a playground that declares it deploys -----------------------------------------------------------------------------

page.ids["combo-pop"].innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { Combo } = await import("../../../web/js/components/combo.js");
const { serviceCells } = await import("../../../web/js/components/ui.js");
Combo.init();

const ACCOUNT = { id: "my-account", name: "My Account", kind: "cloud", url: "https://eu.cloud.gravitee.io" };
const CLIENT = { id: "client-lab", name: "Client lab", kind: "cloud", url: "https://client.example.io" };
// What a place that lacks something says, in the words of the places: the short one in the picker, the sentence of a disabled button
const SENTENCE = { "No hybrid gateway": (name) => `Set up a gateway in ${name} first`, "No Cloud Token": (name) => `Cloud Token for ${name} is not set`,
  "Cloud Token expired": (name) => `Cloud Token for ${name} has expired`, "Cloud Token refused": (name) => `Gravitee Cloud refuses the Cloud Token of ${name}` };
// A place as GET /api/places says it
const place = (platform, id, name, more = {}) => {
  const found = { id: `${platform.id}/${id}`, name: `${name} (${platform.name})`, platform: { id: platform.id, name: platform.name, kind: platform.kind },
    environment: { id, name, organization: "org-1" }, state: "ready", missing: null, reason: "", token: { state: "valid" },
    gateway: { id: "g1", name: "Gravitee Lab", state: "healthy", here: false, kept: true, connection: "CONNECTED" },
    capabilities: { products: { "API Management": "4.12.21" }, plugins: false, detail: "" }, cannot: {}, ...more };
  return found.missing ? { ...found, state: "missing", reason: SENTENCE[found.missing](found.name) } : found;
};
const DEV = place(ACCOUNT, "env-dev", "Development");
const TEST = place(ACCOUNT, "env-test", "Test", { missing: "No hybrid gateway", gateway: null, capabilities: null });
const OTHER = place(CLIENT, "env-test", "Test", { missing: "No Cloud Token", token: { state: "unset" } });
// A platform that cannot be read has no environment to list, token or gateway: it is one place, by its name
const SLOW = { ...place({ id: "slow-lab", name: "Slow lab", kind: "cloud" }, "env-dev", "Development"), id: "slow-lab", name: "Slow lab", environment: null,
  state: "unusable", reason: "Unreachable", token: null, gateway: null, capabilities: null };
const PLACES = [DEV, TEST, OTHER, SLOW];
// Development before anything is made there: no gateway, no Cloud Token
const BARE = place(ACCOUNT, "env-dev", "Development", { missing: "No hybrid gateway", gateway: null, token: { state: "unset" }, capabilities: null });
const HOTELS_DEV = { place: DEV.id, environment: "Development", created: [{ what: "APIs", count: 3 }, { what: "Applications", count: 1 }] };

// Acme Hostels: it deploys to Cloud too. Its seed recorded 12 APIs on This machine. Why a playground cannot go to a place is in the
// place's `cannot`, which the server judges against what the gateways of its environment run.
const CANNOT = "Needs Access Management";
const refusing = (found) => ({ ...found, cannot: { hotels: CANNOT } });
const HOTELS_LINK = link("Hotels", "hotels-ui", 8210, HEALTHY, "Playground");
const hotels = (more = {}) => ({
  ...playground("hotels", { tabs: [tab("hotels-ui", "Hotels", 8210, HEALTHY)], on: true, links: [HOTELS_LINK] }),
  name: "Acme Hostels", deploys_to: ["cloud"], cannot: {}, deployed: [], reseed: { enabled: true, reason: null }, created: [{ what: "APIs", count: 12 }],
  stop: { stops: [], turns_off: [], keeps: [{ kind: "data", id: "hotels", name: "What Acme Hostels created in Gravitee" }], delete: { enabled: true, reason: null } },
  ...more,
});
const off = { on: false, up: false, state: { key: "off", label: "" }, action: { do: "start", label: "Start", enabled: true, reason: null, dialog: null } };
const plain = store.state;   // the lab as the tests above leave it: no platform

// The page as it opens for Acme Hostels, with the places the server knows; `kept` is the place this browser kept, for every playground,
// `checks` and `dialog` what the server says of the place asked (its query). Settled once the places and the checks are read, unless `now`.
async function visit(more = {}, { platforms = [ACCOUNT, CLIENT], places = PLACES, kept, checks = () => [], dialog = () => null, now = false, id = "hotels", core = undefined } = {}) {
  Object.assign(server, { places, calls: [], checks, dialog });
  store.state = { ...plain, platforms, core, playgrounds: [...PLAYGROUNDS, hotels(more), { ...hotels(more), id: "lodges", name: "ACME Lodges" }], job: null,
    services: { ...SERVICES, "hotels-ui": { label: "Hotels", state: HEALTHY } }, links: [...plain.links, HOTELS_LINK] };
  store.places = [];
  store.placesRead = false;
  if (kept) page.stored.deploy = kept;
  else delete page.stored.deploy;
  playgroundPage.show([id]);
  playgroundPage.render(store.state);
  if (!now) await flush();
}
// Leaves the page, and lets the history of the guide forget that it did
const leave = async () => {
  playgroundPage.hide();
  await flush();
};
const face = (el) => text(el.querySelector(".pbtn-face") || el);
// The head and the Start of Before you start, as a person reads them: what each says, and why it cannot be used
const buttons = () => section.querySelectorAll("#pg-actions .btn, #pg-actions .combo-btn, .pg-start-btn")
  .map((el) => [el.classList.contains("combo-btn") ? text(el) : face(el), isOff(el) ? offWhy(el) : ""]);
const names = () => buttons().map(([name]) => name);
const reads = (suffix) => server.calls.filter((c) => c.path.includes(suffix));
const posts = (suffix) => server.calls.filter((c) => c.method === "POST" && c.path.endsWith(suffix));
// What to open, by section
const groups = () => section.querySelectorAll("#pg-links .link-group").map((g) => [text(g.querySelector(".link-section")),
  g.querySelectorAll(".link").map((a) => [text(a.querySelector("strong")), a.getAttribute("href") || ""])]);

// The picker, open: its options as [name, tail, cannot be chosen]
const picker = () => section.querySelector("#pg-actions .combo-btn");
const options = () => page.doc.querySelector("#combo-list").querySelectorAll(".combo-option");
async function opened() {
  picker().click();
  await flush();
  return options().map((o) => [text(o.querySelector(".name")), text(o.querySelector(".age")), o.classList.contains("disabled")]);
}
async function pick(name) {
  await opened();
  options().find((o) => text(o.querySelector(".name")) === name).click();
  await flush();
}

// The dialog of what an action does, and its answer, as the Gravitee Cloud page tests read them
const choice = page.ids.choice, picks = page.ids["choice-list"];
let second = false;   // the second option of the choice is the one checked
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input")[second ? 1 : 0] : El.prototype.querySelector.call(picks, selector));
const said = (el) => (el.children.some((child) => child.tag) ? el.children.filter((child) => child.tag && !child.getAttribute("aria-hidden")).map(said).filter(Boolean).join(" ") : text(el));
const rowsOf = (el) => el.querySelectorAll(".choice-row").map((n) => [text(n.querySelector(".choice-row-label")), n.querySelectorAll(".pill").map(text)]);
const effects = () => ({ title: text(page.ids["choice-title"]), action: text(page.ids["choice-ok"]),
  steps: picks.querySelectorAll(".step").map((s) => [text(s.querySelector("strong")), s.querySelectorAll(".step-items li").map(said)]), notes: rowsOf(picks) });
// The options of a choice: what each says, why it cannot be taken, and the rows under it
const choices = () => picks.querySelectorAll(".choice").map((o) => [text(o.querySelector("strong")), o.querySelector(".sub") ? text(o.querySelector(".sub")) : "", rowsOf(o)]);
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
const WHERE = [{ kind: "environment", id: DEV.id, name: DEV.name }];
const dialogOf = (more) => ({ starts: [], stops: [], turns_off: [], keeps: [], restarts: [], deploys_to: WHERE, serves: [], stops_serving: [], ...more });
const HYBRID = { kind: "addon", id: "hybrid", name: "Hybrid gateway" };
// The forms the Before you start lines open: a browser tells their button whether the required fields are filled, which the stub does not
const field = (id) => page.doc.querySelector(`#${id}`);
const form = (id) => {
  const el = field(id).closest("form");
  el.checkValidity = () => el.querySelectorAll("[required]").every((e) => e.value);
  return el;
};
for (const id of ["gateway-name", "cloud-token-secret", "replace-cloud-token-secret"]) form(id);

test("with no platform registered the page is today's, whatever the playground declares: no picker, no place, no new request", async () => {
  await visit({}, { platforms: [], places: [] });
  assert.ok(!picker(), "no picker");
  assert.deepEqual(names(), ["Open Hotels", "Re-seed", "Stop"]);
  assert.equal(reads("/api/places").length, 0, "nothing is asked of the platforms");
  assert.deepEqual(reads("/check").map((c) => c.path), ["/api/playgrounds/hotels/check"], "the checks are the lab's");
  assert.ok(!section.querySelector("#pg-deployed"), "no place of its own");
  assert.ok(!/Deploy/.test(text(section)), "no word of a place");
  assert.deepEqual(groups().map(([name]) => name), ["API Management", "Playground"]);
  await leave();
});

test("a place kept for the playground is not known before the places are read: nothing deploys, and the picker says it waits", async () => {
  await visit({ ...off }, { kept: DEV.id, now: true });
  assert.deepEqual(buttons(), [["Loading", "Loading"], ["Start", "Loading"]]);
  await flush();
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]]);
  await leave();
});

// What places.py answers for a platform it has not read yet (the first call after the Lab UI starts), or cannot read: one entry, by the
// platform's own id, with no environment, token or gateway
const UNREAD = (reason) => ({ id: "my-account", name: "My Account", platform: { id: "my-account", name: "My Account", kind: "cloud" }, environment: null,
  state: "unusable", missing: null, reason, token: null, gateway: null, capabilities: null, cannot: {} });

test("a place kept for a platform that is still being read, or cannot be used, is not This machine: nothing deploys and the reason is the button's title", async () => {
  for (const reason of ["Loading", "Invalid token", "Unreachable"]) {
    await visit({ ...off }, { kept: DEV.id, places: [UNREAD(reason)] });
    assert.deepEqual(buttons(), [["Deploy to My Account", ""], ["Start", reason]], reason);
    assert.equal(reads("/check").at(-1).path, "/api/playgrounds/hotels/check?place=my-account", `${reason}: its checks are those of the platform's place`);
    await leave();
  }
  // The same, running: nothing is deployed to This machine because the platform is slow to answer
  await visit({}, { kept: DEV.id, places: [UNREAD("Loading")] });
  assert.deepEqual(buttons().map(([name, why]) => [name.replace(/^Deploy$|^Re-seed$/, "Seed"), why]),
    [["Open Hotels", ""], ["Deploy to My Account", ""], ["Seed", "Loading"], ["Stop", ""]]);
  await leave();
});

test("a platform that was still being read is read again until it has answered: the place kept comes back by itself", async () => {
  await visit({ ...off }, { kept: DEV.id, places: [UNREAD("Loading")] });
  assert.deepEqual(buttons(), [["Deploy to My Account", ""], ["Start", "Loading"]]);
  server.places = PLACES;
  server.calls = [];
  mock.timers.tick(5000);
  await flush();
  assert.equal(reads("/api/places").length, 1, "it asks again by itself");
  playgroundPage.render(store.state);   // the router draws the page when the places change
  await flush();
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]]);
  assert.equal(reads("/check").at(-1).path, "/api/playgrounds/hotels/check?place=my-account%2Fenv-dev", "its checks are read for the place");
  server.calls = [];
  mock.timers.tick(5000);
  await flush();
  assert.equal(reads("/api/places").length, 0, "and stops asking once nothing is being read");
  await leave();
});

test("places that cannot be read leave a place kept unknown: nothing deploys to This machine in its stead", async () => {
  server.down = true;
  const errors = console.error;
  console.error = () => {};
  await visit({ ...off }, { kept: DEV.id });
  assert.deepEqual(buttons(), [["Loading", "Loading"], ["Start", "Loading"]]);
  server.down = false;
  console.error = errors;
  mock.timers.tick(60000);
  await flush();
  playgroundPage.render(store.state);
  await flush();
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]], "read again a minute later");
  await leave();
});

// A playground whose files keep it from Gravitee Cloud: every place says why
const BANK_LIKE = { deploys_to: [] };
const REFUSING = PLACES.map(refusing);

test("a playground that cannot go to a platform has the picker too: This machine, the places of that platform listed with the reason, none to choose", async () => {
  await visit({ ...off, ...BANK_LIKE }, { places: REFUSING });
  assert.deepEqual(buttons(), [["Deploy to This machine", ""], ["Start", ""]]);
  assert.deepEqual(await opened(), [["This machine", "", false], ["Test (Client lab)", CANNOT, true], ["Development (My Account)", CANNOT, true],
    ["Test (My Account)", CANNOT, true], ["Slow lab", CANNOT, true]], "the reason comes before what a place lacks, and before a platform that cannot be used");
  options().find((o) => text(o.querySelector(".name")) === "Development (My Account)").click();
  await flush();
  assert.equal(page.stored.deploy, undefined, "nothing was chosen");
  assert.equal(picker().classList.contains("open"), true);
  Combo.close();
  assert.deepEqual(reads("/check").map((c) => c.path), ["/api/playgrounds/hotels/check"], "This machine reads the checks as it always did");
  await leave();
});

test("a place whose environment does not run what the playground needs is listed disabled with its reason, the others open", async () => {
  const LATER = place(CLIENT, "env-dev", "Development", { capabilities: { products: { "API Management": "4.13.2" }, plugins: false, detail: "" } });
  await visit({ ...off }, { places: [{ ...DEV, cannot: { hotels: "Needs API Management 4.13" } }, LATER] });
  assert.deepEqual(await opened(), [["This machine", "", false], ["Development (Client lab)", "", false], ["Development (My Account)", "Needs API Management 4.13", true]]);
  Combo.close();
  await leave();
});

test("the head of a playground that does not run is its picker, on This machine until a place is chosen", async () => {
  await visit({ ...off });
  assert.deepEqual(buttons(), [["Deploy to This machine", ""], ["Start", ""]]);
  assert.equal(offWhy(picker()), "Deploy to This machine");
  assert.equal(section.querySelectorAll("#pg-actions .row-icons [data-file]").length, 1, "its file stays");
  assert.deepEqual(reads("/check").map((c) => c.path), ["/api/playgrounds/hotels/check"], "This machine reads the checks as it always did");
  await leave();
});

test("the picker lists This machine, then the places by platform with what each lacks; a platform that cannot be used cannot be chosen", async () => {
  await visit({ ...off });
  assert.deepEqual(await opened(), [["This machine", "", false], ["Test (Client lab)", "No Cloud Token", false], ["Development (My Account)", "", false],
    ["Test (My Account)", "No hybrid gateway", false], ["Slow lab", "Unreachable", true]]);
  const slow = options().at(-1);
  assert.equal(slow.getAttribute("aria-disabled"), "true");
  slow.click();
  await flush();
  assert.equal(page.stored.deploy, undefined, "nothing was chosen");
  assert.equal(picker().classList.contains("open"), true, "the picker is still open");
  Combo.close();
  await leave();
});

test("choosing a place keeps it in this browser, and its Before you start is read for that place", async () => {
  await visit({ ...off });
  server.calls = [];
  await pick("Development (My Account)");
  assert.equal(page.stored.deploy, DEV.id);
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]]);
  assert.deepEqual(reads("/check").map((c) => c.path), ["/api/playgrounds/hotels/check?place=my-account%2Fenv-dev"]);
  await pick("This machine");
  assert.equal(page.stored.deploy, "machine");
  assert.equal(reads("/check").at(-1).path, "/api/playgrounds/hotels/check", "back on This machine, the checks are the lab's");
  await leave();
});

test("a browser that keeps nothing still holds the choice while the page is open, and falls back to This machine", async () => {
  const keeps = globalThis.localStorage;
  const blocked = () => { throw new Error("blocked"); };
  globalThis.localStorage = { getItem: blocked, setItem: blocked, removeItem: blocked };
  await visit({ ...off });
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""]);
  await pick("Development (My Account)");
  assert.deepEqual(buttons()[0], ["Deploy to Development (My Account)", ""], "the choice holds on this page");
  await pick("This machine");
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""]);
  globalThis.localStorage = keeps;
  await leave();
});

test("a place kept that is no longer one falls back to This machine", async () => {
  await visit({ ...off }, { kept: "gone/env-x" });
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""]);
  await leave();
});

test("the place chosen on any page is the default of every playground: one key, whatever the playground", async () => {
  await visit({ ...off });
  await pick("Development (My Account)");
  assert.deepEqual(Object.keys(page.stored).filter((key) => key.startsWith("deploy")), ["deploy"], "one key, not one for each playground");
  await leave();
  await visit({ ...off }, { kept: page.stored.deploy, id: "lodges" });
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]], "another playground starts on it");
  assert.equal(reads("/check").at(-1).path, "/api/playgrounds/lodges/check?place=my-account%2Fenv-dev");
  await pick("This machine");
  assert.equal(page.stored.deploy, "machine", "a place chosen there is the default here again");
  await leave();
  await visit({ ...off }, { kept: "machine" });
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""]);
  await leave();
});

test("with no place kept, where the core runs is the default: a Cloud core proposes its environment, a place kept wins, and This machine stays for what cannot go there", async () => {
  const core = { place: DEV.id, chosen: true, name: DEV.name };
  await visit({ ...off }, { core });
  assert.deepEqual(buttons(), [["Deploy to Development (My Account)", ""], ["Start", ""]], "the core's environment, which Start deploys to");
  assert.equal(page.stored.deploy, undefined, "nothing is kept for it: the core is the default");
  await leave();
  await visit({ ...off }, { core, kept: "machine" });
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""], "a place chosen in Deploy to is what it was");
  await leave();
  await visit({ ...off, ...BANK_LIKE }, { core, places: REFUSING });
  assert.deepEqual(buttons()[0], ["Deploy to This machine", ""], "a playground that cannot go to Gravitee Cloud is on This machine");
  await leave();
});

test("a playground that cannot go to the platform kept is on This machine, and what is kept stays for the others", async () => {
  await visit({ ...off, ...BANK_LIKE }, { kept: DEV.id, places: REFUSING });
  assert.deepEqual(buttons(), [["Deploy to This machine", ""], ["Start", ""]]);
  assert.deepEqual(reads("/check").map((c) => c.path), ["/api/playgrounds/hotels/check"]);
  assert.equal(page.stored.deploy, DEV.id, "its page did not change the default");
  await leave();
  // before the places are read it is not held up for a place it cannot go to
  await visit({ ...off, ...BANK_LIKE }, { kept: DEV.id, now: true, places: REFUSING });
  assert.deepEqual(buttons(), [["Deploy to This machine", ""], ["Start", ""]]);
  await flush();
  await leave();
});

test("the picker waits for a job, as every button does; it is free while the playground runs", async () => {
  await visit({});
  assert.equal(picker().hasAttribute("data-busy"), true);
  assert.equal(isOff(picker()), false);
  await leave();
});

test("running, the main button reads Re-seed on This machine, Deploy where a place holds nothing and Re-seed where it holds a record", async () => {
  await visit({});
  assert.deepEqual(names(), ["Open Hotels", "Deploy to This machine", "Re-seed", "Stop"]);
  await pick("Development (My Account)");
  assert.deepEqual(names(), ["Open Hotels", "Deploy to Development (My Account)", "Deploy", "Stop"]);
  const seed = section.querySelector('[data-act="deploy"]');
  assert.equal(seed.getAttribute("data-act-verb"), "deploy reseed", "the button keeps its key and its verbs when the record appears");
  assert.ok(offWhy(seed).endsWith("in Development (My Account)"), offWhy(seed));
  store.state.playgrounds = [...PLAYGROUNDS, hotels({ deployed: [HOTELS_DEV] })];
  playgroundPage.render(store.state);
  assert.deepEqual(names(), ["Open Hotels", "Deploy to Development (My Account)", "Re-seed", "Stop"]);
  assert.equal(section.querySelector('[data-act="reseed"]').getAttribute("data-act-verb"), "deploy reseed");
  await pick("This machine");
  assert.equal(section.querySelector('[data-act="reseed"]').getAttribute("data-act-verb"), "reseed", "This machine's Re-seed is today's");
  await leave();
});

test("a remote place waits for itself alone: what the local seed needs does not hold it", async () => {
  await visit({ reseed: { enabled: false, reason: "Management API is not running" } }, { kept: DEV.id });
  assert.deepEqual(buttons(), [["Open Hotels", ""], ["Deploy to Development (My Account)", ""], ["Deploy", ""], ["Stop", ""]]);
  assert.equal(isOff(section.querySelector('[data-act="deploy"]')), false, "what the local seed needs does not disable it, with a title or without");
  await pick("This machine");
  assert.deepEqual(buttons().find(([name]) => name === "Re-seed"), ["Re-seed", "Management API is not running"]);
  await leave();
});

test("a place that lacks something is chosen all the same, and its Start, Deploy and Re-seed say what to do first, in the words the places give", async () => {
  const lacks = [["Test (My Account)", { missing: "No hybrid gateway" }, "Set up a gateway in Test (My Account) first"],
    ["Test (Client lab)", { missing: "No Cloud Token" }, "Cloud Token for Test (Client lab) is not set"],
    ["Test (Client lab)", { missing: "Cloud Token expired" }, "Cloud Token for Test (Client lab) has expired"],
    ["Test (Client lab)", { missing: "Cloud Token refused" }, "Gravitee Cloud refuses the Cloud Token of Test (Client lab)"],
    ["Slow lab", { state: "unusable", reason: "Unreachable" }, "Unreachable"]];
  for (const [name, more, why] of lacks) {
    const places = PLACES.map((p) => (p.name === name ? { ...p, ...more, reason: more.missing ? SENTENCE[more.missing](name) : more.reason } : p));
    const kept = places.find((p) => p.name === name).id;
    await visit({}, { places, kept });
    assert.deepEqual(buttons().find(([label]) => label === "Deploy"), ["Deploy", why], name);
    await leave();
    await visit({ ...off }, { places, kept });
    assert.deepEqual(buttons().find(([label]) => label === "Start"), ["Start", why], name);
    await leave();
  }
  // Whatever it says is the server's: a sentence of its own is shown as it is
  await visit({ ...off }, { places: [{ ...TEST, reason: "Create a hybrid gateway first" }], kept: TEST.id });
  assert.deepEqual(buttons().find(([label]) => label === "Start"), ["Start", "Create a hybrid gateway first"]);
  await leave();
});

// --- Start, Deploy and Re-seed -----------------------------------------------------------------------------------------------------

test("Start on a remote place always asks first: what it starts, then where it deploys, the place named; it sends the place back", async () => {
  await visit({ ...off }, { kept: DEV.id, dialog: () => dialogOf({ starts: [HYBRID] }) });
  section.querySelector(".pg-start-btn").click();
  await flush();
  assert.deepEqual(effects(), { title: "Start Acme Hostels", action: "Start",
    steps: [["Starts", ["Hybrid gateway Add-on"]], ["Deploys to", ["Development (My Account) Environment"]]], notes: [] });
  assert.equal(posts("/start").length, 0, "nothing runs before it is confirmed");
  await answer(true);
  assert.deepEqual(posts("/start").map((c) => [c.path, c.body]), [["/api/playgrounds/hotels/start", { place: DEV.id, accept: [] }]]);
  await leave();
});

test("a remote Start that starts nothing still opens the dialog, with the place alone", async () => {
  await visit({ ...off }, { kept: DEV.id });
  section.querySelector(".pg-start-btn").click();
  await flush();
  assert.deepEqual(effects().steps, [["Deploys to", ["Development (My Account) Environment"]]]);
  await answer(false);
  assert.equal(posts("/start").length, 0);
  await leave();
});

test("a remote Start that swaps the gateway says what it serves and stops serving, after the steps", async () => {
  const swap = dialogOf({ restarts: [HYBRID], serves: WHERE, stops_serving: [{ kind: "environment", id: TEST.id, name: TEST.name }] });
  await visit({ ...off }, { kept: DEV.id, dialog: () => swap });
  section.querySelector(".pg-start-btn").click();
  await flush();
  const { steps, notes } = effects();
  assert.deepEqual(steps, [["Restarts", ["Hybrid gateway Add-on"]], ["Deploys to", ["Development (My Account) Environment"]]]);
  assert.deepEqual(notes, [["Serves", ["Development (My Account)"]], ["Stops serving", ["Test (My Account)"]]]);
  assert.equal(text(picks.querySelector(".step .muted")), "Recreated to take the changes");
  await answer(false);
  await leave();
});

test("a remote Start that stops another playground sends back what it showed", async () => {
  await visit({ ...off }, { kept: DEV.id, dialog: () => dialogOf({ stops: [{ kind: "playground", id: "acme-bakery", name: "Acme Bakery" }] }) });
  section.querySelector(".pg-start-btn").click();
  await flush();
  assert.deepEqual(effects().steps[0], ["Stops", ["Acme Bakery Playground"]]);
  await answer(true);
  assert.deepEqual(posts("/start")[0].body, { place: DEV.id, accept: ["playground:acme-bakery"] });
  await leave();
});

test("This machine's Start is as it always was: no place sent, no dialog when nothing else changes", async () => {
  await visit({ ...off });
  section.querySelector(".pg-start-btn").click();
  await settle();
  assert.deepEqual(posts("/start").map((c) => [c.path, c.body]), [["/api/playgrounds/hotels/start", { accept: [] }]]);
  await leave();
});

test("when it asks, This machine's Start names itself in its last step, whatever the playground, once a platform is registered; not before", async () => {
  const dialog = { starts: [{ kind: "platform", id: "platform", name: "Gravitee platform" }], stops: [], turns_off: [], keeps: [], restarts: [] };
  const action = { do: "start", label: "Start", enabled: true, reason: null, dialog };
  const steps = async (more, platforms) => {
    await visit({ ...off, action, ...more }, { ...(platforms ? {} : { platforms: [], places: [] }), dialog: () => dialog });
    section.querySelector(".pg-start-btn").click();
    await flush();
    const found = effects().steps;
    await answer(false);
    await leave();
    return found;
  };
  assert.deepEqual(await steps({}, true), [["Starts", ["Gravitee platform Platform"]], ["Deploys to", ["This machine Platform"]]]);
  assert.deepEqual(await steps(BANK_LIKE, true), [["Starts", ["Gravitee platform Platform"]], ["Deploys to", ["This machine Platform"]]],
    "the picker is there for every playground, so is the place the dialog names");
  assert.deepEqual(await steps({}, false), [["Starts", ["Gravitee platform Platform"]]], "a lab with no platform says nothing of it");
});

test("Deploy and Re-seed on a remote place ask too, and run the seed there; This machine's Re-seed sends no place", async () => {
  await visit({}, { kept: DEV.id });
  section.querySelector('[data-act="deploy"]').click();
  await flush();
  assert.deepEqual(effects(), { title: "Deploy Acme Hostels", action: "Deploy", steps: [["Deploys to", ["Development (My Account) Environment"]]], notes: [] });
  await answer(true);
  assert.deepEqual(posts("/reseed").map((c) => [c.path, c.body]), [["/api/playgrounds/hotels/reseed", { place: DEV.id, accept: [] }]]);
  await leave();

  await visit({ deployed: [HOTELS_DEV] }, { kept: DEV.id });
  section.querySelector('[data-act="reseed"]').click();
  await flush();
  assert.equal(effects().title, "Re-seed Acme Hostels");
  await answer(true);
  assert.deepEqual(posts("/reseed").map((c) => c.body), [{ place: DEV.id, accept: [] }]);
  await leave();

  await visit({});
  section.querySelector('[data-act="reseed"]').click();
  await settle();
  assert.deepEqual(posts("/reseed").map((c) => c.body), [{}], "This machine: as it always was");
  await leave();
});

test("a Deploy at work says so on its button, with the verb of its job", async () => {
  await visit({}, { kept: DEV.id });
  store.state = { ...store.state, job: { ...JOB, verb: "deploy", title: "Deploy Acme Hostels", progress: 0.4 } };
  playgroundPage.render(store.state);
  const seed = section.querySelector('[data-act="deploy"]');
  assert.ok(seed.classList.contains("busy"), "it works");
  assert.match(text(seed.querySelector(".pbtn-side")), /Deploying 40%/);
  await leave();
});

// --- Before you start, for the place -------------------------------------------------------------------------------------------------

const LINES = [{ name: "My Account", state: "ok", detail: "Connected", target: { kind: "platform", id: "my-account" } },
  { name: "Hybrid gateway", state: "warn", detail: "None in Development", fix: "gateway" },
  { name: "Cloud Token", state: "warn", detail: "Not set", fix: "token" },
  { name: "Mail server", state: "warn", detail: "Needs settings", target: { kind: "addon", id: "smtp" }, file: "addons/smtp/.env" }];
const lines = () => section.querySelectorAll(".pg-prep-item").map((li) => [text(li.querySelector(".pg-prep-name")),
  li.classList.contains("warn") ? "needs you" : li.classList.contains("auto") ? "start" : "in place", li.querySelectorAll(".pg-prep-action .icon-btn").map((b) => b.getAttribute("data-tip"))]);

test("Before you start is read for the place, and a line that a form answers offers it: a plus for the gateway, a key for the Cloud Token", async () => {
  await visit({ ...off }, { kept: DEV.id, places: [BARE, TEST, OTHER, SLOW], checks: (query) => (query ? LINES : []) });
  assert.deepEqual(lines(), [["Hybrid gateway", "needs you", ["Create hybrid gateway"]], ["Cloud Token", "needs you", ["Add Cloud Token"]],
    ["Mail server", "needs you", ["Settings"]], ["My Account", "in place", []]]);
  assert.equal(section.querySelector('.pg-prep-name a[href="#/cloud/my-account"]').textContent, "My Account", "the platform leads to its row");
  assert.deepEqual(buttons().at(-1), ["Start", "Set up a gateway in Development (My Account) first"]);
  await leave();
});

test("the plus opens Create hybrid gateway in the environment of the place, and the key Add Cloud Token (Replace where one is set); both read again once done", async () => {
  await visit({ ...off }, { kept: DEV.id, places: [BARE, TEST, OTHER, SLOW], checks: (query) => (query ? LINES : []) });
  const selected = (id) => field(id).querySelectorAll("option").filter((o) => o.hasAttribute("selected")).map((o) => o.getAttribute("value"));

  section.querySelector('[data-fix="gateway"]').click();
  await flush();
  const gateway = form("gateway-name");
  assert.equal(text(gateway.querySelector("h2")), "Create hybrid gateway for Development", "the environment of the place is in the title: it is not asked");
  assert.ok(gateway.parent.hasAttribute("open"));
  assert.deepEqual(selected("gateway-environment"), ["env-dev"], "the environment of the place");
  assert.deepEqual(field("gateway-environment").querySelectorAll("option").map((o) => [o.getAttribute("value"), text(o)]),
    [["env-dev", "Development"], ["env-test", "Test"]], "the environments of its platform, with no request to read them");
  assert.equal(field("gateway-name").value, "Gravitee Lab - Development");
  field("gateway-environment").value = "env-dev";
  server.calls = [];
  gateway.dispatchEvent(new Ev("submit"));
  await settle();
  assert.deepEqual(posts("/gateways").map((c) => [c.path, c.body]), [["/api/platforms/my-account/gateways", { environmentId: "env-dev", name: "Gravitee Lab - Development" }]]);
  assert.ok(reads("/check").length > 0, "the checks are read again");
  assert.ok(reads("/api/places").length > 0, "and so are the places");

  section.querySelector('[data-fix="token"]').click();
  await flush();
  const add = form("cloud-token-secret");
  assert.equal(text(add.querySelector("h2")), "Add Cloud Token for Development");
  assert.deepEqual(selected("cloud-token-environment"), ["env-dev"]);
  add.parent.close();

  // A token that is there and no good: the key replaces it
  store.places = store.places.map((p) => (p.id === DEV.id ? { ...p, token: { state: "expired" }, missing: "Cloud Token expired" } : p));
  playgroundPage.render(store.state);
  assert.equal(section.querySelector('[data-fix="token"]').getAttribute("data-tip"), "Replace Cloud Token");
  section.querySelector('[data-fix="token"]').click();
  await flush();
  const replace = form("replace-cloud-token-secret");
  assert.equal(text(replace.querySelector("h2")), "Replace Cloud Token for Development");
  assert.deepEqual(selected("replace-cloud-token-environment"), ["env-dev"]);
  replace.parent.close();
  await leave();
});

test("for a remote place the notes of the core's start are not drawn", async () => {
  await visit({ ...off, created: [], first_start: "5 min", note: "Apply needed", note_tone: "warn" });
  assert.equal(section.querySelectorAll(".pg-start-note").length, 2, "This machine: the first start and the note");
  await pick("Development (My Account)");
  assert.equal(section.querySelectorAll(".pg-start-note").length, 0);
  await leave();
});

// --- Deployed to --------------------------------------------------------------------------------------------------------------------

const rows = (el) => el.querySelectorAll(".pg-place").map((li) => [text(li.querySelector(".pg-place-name")), text(li.querySelector(".sub")),
  li.querySelector(".icon-btn") ? (isOff(li.querySelector(".icon-btn")) ? offWhy(li.querySelector(".icon-btn")) : "Delete") : ""]);

test("Deployed to is drawn once a remote place holds a record: before the playground runs after what runs, running in the details", async () => {
  await visit({ ...off, deployed: [] });
  assert.ok(!section.querySelector(".pg-place"), "only This machine holds a record: nothing to say");
  assert.ok(!/Deployed to/.test(text(section)));
  await leave();
  await visit({ ...off, deployed: [HOTELS_DEV] });
  const main = section.querySelector(".pg-main");
  assert.deepEqual(rows(main), [["This machine", "12 APIs", ""], ["Development (My Account)", "3 APIs, 1 Application", "Acme Hostels is off"]]);
  assert.ok(main.innerHTML.indexOf("What runs") < main.innerHTML.indexOf("Deployed to"), "after what runs");
  await leave();
  await visit({ deployed: [HOTELS_DEV] });
  const side = section.querySelector("#pg-deployed");
  assert.equal(side.hidden, false);
  assert.equal(text(side.querySelector("h3")), "Deployed to");
  assert.deepEqual(rows(side), [["This machine", "12 APIs", ""], ["Development (My Account)", "3 APIs, 1 Application", "Delete"]]);
  assert.equal(side.querySelectorAll(".pg-place .icon-gap").length, 1, "This machine's row keeps the place of the button: its Stop deletes its record");
  await leave();
});

test("the Delete of a remote place says what goes, needs its Cloud Token, and deletes that place alone", async () => {
  const drop = () => section.querySelector("#pg-deployed [data-delete-place]");
  await visit({ deployed: [HOTELS_DEV] });
  assert.equal(drop().getAttribute("data-delete-place"), DEV.id);
  drop().click();
  await flush();
  assert.deepEqual(effects(), { title: "Delete", action: "Delete", steps: [["Deletes", ["3 APIs, 1 Application in Development (My Account)"]]], notes: [] });
  await answer(false);
  assert.equal(posts("/delete").length, 0);
  drop().click();
  await flush();
  await answer(true);
  assert.deepEqual(posts("/delete").map((c) => [c.path, c.body]), [["/api/playgrounds/hotels/delete", { place: DEV.id }]]);
  mock.timers.tick(3000);   // its button says how it went, then gives its face back
  await flush();
  await leave();

  for (const [token, why] of [["unset", "No Cloud Token"], ["expired", "Cloud Token expired"], ["refused", "Cloud Token refused"]]) {
    await visit({ deployed: [HOTELS_DEV] }, { places: PLACES.map((p) => (p.id === DEV.id ? { ...p, token: { state: token } } : p)) });
    assert.deepEqual([isOff(drop()), offWhy(drop())], [true, why], token);
    await leave();
  }
  await visit({ deployed: [HOTELS_DEV] }, { places: [SLOW] });
  assert.equal(offWhy(drop()), "Account not connected");
  await leave();
  // A platform that is registered but cannot be used answers for the places it had: its reason, not that it is gone
  for (const reason of ["Loading", "Invalid token", "Unreachable"]) {
    await visit({ deployed: [HOTELS_DEV] }, { places: [UNREAD(reason)] });
    assert.deepEqual([isOff(drop()), offWhy(drop())], [true, reason], reason);
    await leave();
  }
});

// --- Stop, with a record in each place ----------------------------------------------------------------------------------------------

test("Stop says per place what stays or goes; the deletion waits for the first place that cannot be reached", async () => {
  const stop = async (more, places) => {
    await visit({ deployed: [HOTELS_DEV], ...more }, places && { places });
    section.querySelector('[data-act="stop"]').click();
    await flush();
  };
  await stop({});
  assert.equal(effects().title, "Stop Acme Hostels");
  assert.deepEqual(choices(), [
    ["Keep what it created", "", [["Stays", ["12 APIs in This machine"]], ["Stays", ["3 APIs, 1 Application in Development (My Account)"]]]],
    ["Delete what it created", "", [["Deletes", ["12 APIs in This machine"]], ["Deletes", ["3 APIs, 1 Application in Development (My Account)"]]]]]);
  second = true;
  picks.onchange();
  assert.equal(text(page.ids["choice-ok"]), "Stop and delete");
  await answer(true);
  assert.deepEqual(posts("/stop").map((c) => [c.path, c.body]), [["/api/playgrounds/hotels/stop", { remove: true, accept: [] }]], "no place: it covers every place with a record");
  second = false;
  await leave();

  await stop({}, PLACES.map((p) => (p.id === DEV.id ? { ...p, token: { state: "expired" } } : p)));
  assert.deepEqual(choices()[1].slice(0, 2), ["Delete what it created", "Cloud Token expired"]);
  assert.ok(picks.querySelectorAll("input")[1].disabled);
  await answer(false);
  await leave();

  await stop({ stop: { ...hotels().stop, delete: { enabled: false, reason: "Management API is not running" } } });
  assert.deepEqual(choices()[1].slice(0, 2), ["Delete what it created", "Management API is not running"], "the local place comes first");
  await answer(false);
  await leave();
});

test("a person with only a Cloud place is not held by what the local seed needs", async () => {
  await visit({ created: [], deployed: [HOTELS_DEV], stop: { ...hotels().stop, delete: { enabled: false, reason: "Management API is not running" } } });
  section.querySelector('[data-act="stop"]').click();
  await flush();
  assert.deepEqual(choices(), [
    ["Keep what it created", "", [["Stays", ["3 APIs, 1 Application in Development (My Account)"]]]],
    ["Delete what it created", "", [["Deletes", ["3 APIs, 1 Application in Development (My Account)"]]]]]);
  await answer(false);
  await leave();
});

test("with a record on This machine alone, Stop is today's: no place named", async () => {
  for (const platforms of [[ACCOUNT], []]) {
    await visit({}, { platforms, places: platforms.length ? PLACES : [] });
    section.querySelector('[data-act="stop"]').click();
    await flush();
    assert.deepEqual(choices(), [["Keep what it created", "", []], ["Delete what it created", "", [["Deletes", ["12 APIs"]]]]]);
    await answer(false);
    await leave();
  }
});

// --- Start, which stops another playground ------------------------------------------------------------------------------------------

test("a start that stops a playground asks what to do with what it created, as its Stop does, and says nothing when there is nothing to choose", async () => {
  await visit({});   // Acme Hostels is on, and created 12 APIs
  const other = { kind: "playground", id: "bank", name: "Acme Bakery" };
  const goes = { kind: "playground", id: "hotels", name: "Acme Hostels apps" };
  const keeps = [{ kind: "data", id: "hotels", name: "What Acme Hostels created in Gravitee" }];
  const asked = confirmStart(other, "Start", dialogOf({ stops: [goes], starts: [{ kind: "playground", id: "bank", name: "Acme Bakery apps" }], keeps }));
  await flush();
  assert.equal(effects().title, "Start Acme Bakery");
  assert.deepEqual(effects().steps.map(([label]) => label), ["Stops", "Starts", "Deploys to"], "the steps are the start's own");
  assert.deepEqual(choices(), [["Keep what it created", "", []], ["Delete what it created", "", [["Deletes", ["12 APIs"]]]]]);
  second = true;
  picks.onchange();
  assert.equal(text(page.ids["choice-ok"]), "Start and delete");
  await answer(true);
  assert.deepEqual(await asked, { accept: ["playground:hotels"], remove: true });
  second = false;
  // Kept is the first choice, and the answer says so
  const again = confirmStart(other, "Start", dialogOf({ stops: [goes], keeps }));
  await flush();
  await answer(true);
  assert.deepEqual(await again, { accept: ["playground:hotels"], remove: false });
  const canceled = confirmStart(other, "Start", dialogOf({ stops: [goes], keeps }));
  await flush();
  await answer(false);
  assert.equal(await canceled, null);
  // Nothing created by what goes, or no playground that goes: the dialog of the plan is enough
  assert.equal(await confirmStart(other, "Start", dialogOf({ stops: [] })), undefined);
  assert.equal(await confirmStart(other, "Start", dialogOf({ keeps: [{ kind: "data", id: "unknown", name: "x" }] })), undefined);
  await leave();
});

test("a start on images it was not tested with is the start that switches them, as when it must, with a second button that keeps them", async () => {
  await visit({});
  const other = { kind: "playground", id: "bank", name: "Acme Bakery" };
  const row = { kind: "version", id: "APIM", name: "API Management amp-demo-latest → 4.13.0-alpha.3" };
  const offer = dialogOf({ starts: [{ kind: "playground", id: "bank", name: "Acme Bakery apps" }], versions: [row],
    restarts: [{ kind: "service", id: "apim-gateway", name: "Gateway (API Management)" }] });
  const asked = confirmStart(other, "Start", dialogOf({}), { offer });
  await flush();
  assert.equal(effects().title, "Start Acme Bakery");
  assert.deepEqual(effects().steps.map(([label]) => label), ["Starts", "Changes versions", "Restarts", "Deploys to"], "the steps of the start that switches");
  assert.deepEqual([text(page.ids["choice-ok"]), text(page.ids["choice-alt"]), page.ids["choice-alt"].hidden], ["Switch and start", "Start without switching", false]);
  assert.equal(page.ids["choice-ok"].className.includes("danger"), false, "nothing goes back");
  await answer(true);
  assert.deepEqual(await asked, { accept: ["version:APIM"], remove: false, switch: true });
  // Without switching: the start's own dialog, and nothing of the versions sent back
  const kept = confirmStart(other, "Start", dialogOf({ stops: [{ kind: "addon", id: "kafka", name: "Kafka" }] }), { offer });
  await flush();
  await answer("alt");
  assert.deepEqual(await kept, { accept: ["addon:kafka"], remove: false });
  // A switch that goes back says it in the danger tone, and its button is the dangerous one
  const back = { ...offer, versions: [], goes_back: [{ ...row, name: "API Management master-latest → 4.13.0-alpha.3" }] };
  const warned = confirmStart(other, "Start", dialogOf({}), { offer: back });
  await flush();
  assert.deepEqual(effects().steps.map(([label]) => label), ["Starts", "Goes back", "Restarts", "Deploys to"]);
  assert.equal(page.ids["choice-ok"].className.includes("danger"), true);
  await answer(false);
  assert.equal(await warned, null, "canceled, it starts nothing");
  // Nothing offered: the dialog of the plan is enough, a switch it makes as one of its steps included
  assert.equal(await confirmStart(other, "Start", dialogOf({}), {}), undefined);
  assert.equal(await confirmStart(other, "Start", dialogOf({ versions: [row] })), undefined);
  await leave();
});

test("a choice with many items to delete folds them under one line that counts them, and lists them one by one when it opens", async () => {
  await visit({ created: [{ what: "domains", count: 1 }, { what: "scopes", count: 10 }, { what: "APIs", count: 3 }, { what: "Customer entities", count: 8 },
    { what: "access rules", count: 12 }, { what: "users", count: 3 }] });
  const keeps = [{ kind: "data", id: "hotels", name: "What Acme Hostels created in Gravitee" }];
  const asked = confirmStart({ kind: "playground", id: "bank", name: "Acme Bakery" }, "Start", dialogOf({ stops: [{ kind: "playground", id: "hotels", name: "Acme Hostels apps" }], keeps }));
  await flush();
  const details = picks.querySelectorAll("details.choice-more");
  assert.equal(details.length, 1, "one fold, in the option that deletes");
  assert.equal(text(details[0].querySelector(".choice-summary")), "37 items");
  assert.deepEqual(details[0].querySelectorAll(".choice-detail li").map((li) => [text(li.querySelector("b")), text(li.querySelector("span"))]),
    [["1", "domain"], ["10", "scopes"], ["3", "APIs"], ["8", "Customer entities"], ["12", "access rules"], ["3", "users"]]);
  assert.equal(details[0].hasAttribute("open"), false, "folded until it is opened");
  await answer(false);
  assert.equal(await asked, null);
  await leave();
});

// --- Open, the Ports, the places the store keeps -----------------------------------------------------------------------------------

test("Details > Open holds a group named after each platform that has a record, with a link per environment, after the platform's own groups", async () => {
  // The records come in the order the seeds ran: the groups follow the platforms of the lab, the links the environments of the places
  const GONE = { place: "my-account/env-gone", environment: "Old", created: [{ what: "APIs", count: 1 }] };
  await visit({ deployed: [GONE, { place: OTHER.id, environment: "Test", created: [{ what: "APIs", count: 2 }] },
    { place: TEST.id, environment: "Test", created: [{ what: "APIs", count: 1 }] }, HOTELS_DEV, { place: SLOW.id, environment: "Development", created: [] }] });
  assert.deepEqual(groups(), [
    ["API Management", [["Gamma Console", "http://localhost:8086"], ["API Management console", ""]]],
    ["My Account", [["Development", "https://eu.cloud.gravitee.io"], ["Test", "https://eu.cloud.gravitee.io"], ["Old", "https://eu.cloud.gravitee.io"]]],
    ["Client lab", [["Test", "https://client.example.io"]]],
    ["Playground", [["Hotels", "http://localhost:8210"]]]]);
  assert.equal(section.querySelector("#pg-links .link[href^=https]").getAttribute("target"), "_blank");
  await leave();
  await visit({ deployed: [] });
  assert.deepEqual(groups().map(([name]) => name), ["API Management", "Playground"], "no record: no group");
  await leave();
});

test("a service's ports say how another container of the lab reaches it, only while a platform is registered", () => {
  const service = { image: "rnwood/smtp4dev:3", tag: "3", running_tag: "3", built: false, ports: ["8025", "2525"], targets: { 8025: "80", 2525: "25" } };
  const ports = (found, platforms) => {
    store.state = { ...plain, platforms, services: { ...plain.services, smtp4dev: found } };
    const el = page.doc.createElement("div");
    el.innerHTML = serviceCells(found);
    return el.querySelector(".s-ports").getAttribute("data-tip");
  };
  assert.equal(ports(service, []), null, "none without a platform");
  assert.equal(ports(service, [ACCOUNT]), "In the lab: smtp4dev:80, smtp4dev:25");
  assert.equal(ports({ ...service, ports: [] }, [ACCOUNT]), null, "a service that publishes no port has no address to give");
  assert.equal(ports({ ...service, targets: undefined }, [ACCOUNT]), null);
  store.state = plain;
});

test("the store keeps the platforms the state lists and the places the server answers: [] until read, none asked without a platform", async () => {
  store.state = { ...plain, platforms: undefined };
  store.places = [];
  server.calls = [];
  assert.deepEqual(store.platforms, []);
  await store.refreshPlaces();
  assert.deepEqual([store.places, reads("/api/places").length], [[], 0]);
  store.state = { ...plain, platforms: [ACCOUNT] };
  assert.deepEqual(store.platforms, [ACCOUNT]);
  server.places = [DEV];
  let heard = 0;
  store.subscribe(() => { heard += 1; });
  await store.refreshPlaces();
  assert.deepEqual(store.places, [DEV]);
  assert.equal(heard, 1);
  await store.refreshPlaces();
  assert.equal(heard, 1, "a list that did not change redraws nothing");
  server.places = { places: [DEV, TEST] };
  await store.refreshPlaces();
  assert.deepEqual(store.places, [DEV, TEST], "a list in an object is read as well");
  store.state = plain;
  await store.refreshPlaces();
  assert.deepEqual(store.places, [], "the platforms are gone, so are their places");
});

test("the places are read again every minute while the page is open, and no longer once it is left", async () => {
  await visit({ ...off });
  server.calls = [];
  mock.timers.tick(60000);
  await flush();
  assert.equal(reads("/api/places").length, 1);
  await leave();
  mock.timers.tick(120000);
  await flush();
  assert.equal(reads("/api/places").length, 1);
  store.state = plain;   // the lab as the history below finds it
  store.places = [];
});

// --- The history: a page of the guide is turned in the place of the one on screen ---------------

const { session } = page;
const click = (extra = {}) => new Ev("click", { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...extra });
const entered = () => session.entries.length;

test("a page of the guide is turned in place, whether the playground runs or not: Next and Previous add no entry, and a Back leaves the page", async () => {
  location.hash = "#/playgrounds";
  router.register({ id: "playgrounds", pattern: /^#\/playgrounds$/, show() {} });
  router.register({ ...playgroundPage, init: undefined });   // initialized above
  for (const id of ["acme-bakery", "stopped"]) {
    location.hash = "#/playgrounds";
    location.hash = `#/playgrounds/${id}`;
    if (id === "acme-bakery") router.start();
    await flush();
    const before = entered();
    const next = () => section.querySelector(".guide-next");
    const e = click();
    next().dispatchEvent(e);
    await flush();
    assert.equal(e.defaultPrevented, true, `${id}: the browser does not follow it as well`);
    assert.equal(location.hash, `#/playgrounds/${id}/1`, id);
    assert.equal(entered(), before, `${id}: no entry was added`);
    section.querySelector(".guide-prev").dispatchEvent(click());
    await flush();
    assert.equal(location.hash, `#/playgrounds/${id}/introduction`);
    assert.equal(entered(), before, `${id}: Previous neither`);
    session.go(-1);
    assert.equal(location.hash, "#/playgrounds", `${id}: Back leaves the page for the one the person came from`);
  }
});

test("a key held or the middle button leaves a link of the guide to the browser, and a link to another page is the browser's own", async () => {
  location.hash = "#/playgrounds/acme-bakery";
  await flush();
  const before = entered(), hash = location.hash;
  for (const extra of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    const e = click(extra);
    section.querySelector(".guide-next").dispatchEvent(e);
    assert.equal(e.defaultPrevented, false, JSON.stringify(extra));
  }
  const other = page.doc.createElement("a");
  other.setAttribute("href", "#/playgrounds/other/2");
  section.querySelector("#pg-view").append(other);
  const e = click();
  other.dispatchEvent(e);
  assert.equal(e.defaultPrevented, false, "another playground's page");
  assert.equal(entered(), before);
  assert.equal(location.hash, hash);
});

test("what a seed created is counted in the singular when there is one", () => {
  const created = [{ count: 1, what: "Customer entities" }, { count: 1, what: "APIs" }, { count: 8, what: "Customer entities" }, { count: 3, what: "APIs" }];
  assert.deepEqual(seeded({ created }), ["1 Customer entity", "1 API", "8 Customer entities", "3 APIs"]);
});

// --- A playground of a catalog ---------------------------------------------------------------------------------------------------------

const HUB = { id: "@acme-hub/hotels", file: ".lab/catalogs/acme-hub/playgrounds/hotels/compose.yml", display_file: "playgrounds/hotels/compose.yml", readonly: true, origin: origin() };
const ROWS = [FIRST(), catalogRow(), catalogRow({ id: "partner-bank", name: "Partner Bank", commit: "" })];
const need = (more) => ({ id: "partner-bank", name: "Partner Bank", url: "https://example.com/partner/lab-bank.git", min: "2.0.0", have: "", reason: "missing", ...more });
const menu = page.ids.menu;
const entries = () => menu.querySelectorAll(".menu-item").map((el) => [text(el.querySelector("span")), el.hasAttribute("disabled") ? text(el.querySelector(".menu-hint")) : ""]);
const prepared = () => section.querySelectorAll(".pg-prep-item").map((li) => ({ name: text(li.querySelector(".pg-prep-name")), kind: ["warn", "auto", "ok"].find((cls) => li.classList.contains(cls)),
  action: text(li.querySelector(".pg-prep-action")), sub: li.querySelector(".sub") ? text(li.querySelector(".sub")) : "",
  buttons: li.querySelectorAll(".pg-prep-action .btn").map((b) => [b.tag, text(b), b.getAttribute("data-need") || b.getAttribute("href"), b.getAttribute("data-catalog") || ""]) }));
globalThis.innerHeight = 800;

test("the address of a playground of a catalog is its page, with the step of its guide after the id, and a personal one's too", () => {
  const found = (hash) => playgroundPage.pattern.exec(hash)?.slice(1);
  assert.deepEqual(found("#/playgrounds/@acme-hub/hotels"), ["@acme-hub/hotels", undefined]);
  assert.deepEqual(found("#/playgrounds/@acme-hub/hotels/3"), ["@acme-hub/hotels", "3"]);
  assert.deepEqual(found("#/playgrounds/@acme-hub/hotels/getting-started"), ["@acme-hub/hotels", "getting-started"]);
  assert.deepEqual(found("#/playgrounds/local/mine/2"), ["local/mine", "2"]);
  assert.deepEqual(found("#/playgrounds/hotels/2"), ["hotels", "2"]);
  for (const hash of ["#/playgrounds/@acme-hub/../hotels", "#/playgrounds/@a/hotels", "#/playgrounds/@acme-hub/hotels/3/4", "#/playgrounds/@acme-hub/", "#/playgrounds"]) {
    assert.equal(found(hash), undefined, hash);
  }
});

test("a playground of another catalog says which in its About, with a link to the catalog and its version; the head keeps the star alone, and the first catalog's says nothing", async () => {
  const catalogFact = () => section.querySelectorAll(".pairs > div").filter((pair) => text(pair.querySelector("dt")) === "Catalog");
  await visit({ ...off }, { platforms: [], places: [] });
  assert.equal(section.querySelectorAll("#pg-star > *").length, 1, "the star alone");
  assert.equal(catalogFact().length, 0);
  await leave();
  await visit({ ...off, ...HUB }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  assert.equal(section.querySelectorAll("#pg-star > *").length, 1, "the star alone, no catalog beside the name");
  assert.equal(section.querySelector("#pg-star").classList.contains("list-marks"), false);
  const [fact] = catalogFact();
  assert.equal(text(fact.querySelector("dd")), "Acme Hub 1.4.0");
  assert.equal(fact.querySelector("a").getAttribute("href"), "#/catalogs/acme-hub", "it leads to the catalog's row");
  await leave();
});

test("its file button reads View and shows no path of this machine; the file of a playground of this machine is Edit with its path", async () => {
  await visit({ ...off, ...HUB }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  const file = section.querySelector("#pg-actions [data-file]");
  assert.deepEqual([file.getAttribute("data-tip"), file.getAttribute("aria-label")], ["View compose.yml", "View compose.yml"]);
  section.querySelector("#pg-actions [data-more]").click();
  assert.deepEqual(entries(), [["Export", ""], ["Export for Docker Compose", ""], ["Copy to Personal", ""]],
    "a playground of a catalog has a More with Export, Export for Docker Compose and Copy to Personal");
  page.doc.dispatchEvent(Object.assign(new Ev("click"), { target: page.doc.body }));
  await leave();
  await visit({ ...off }, { platforms: [], places: [] });
  assert.equal(section.querySelector("#pg-actions [data-file]").getAttribute("data-tip"), "Edit playgrounds/hotels/compose.yml");
  await leave();
});

test("a playground of this machine has a More, last of the page's actions, with Export, Export for Docker Compose, Share and Delete", async () => {
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  const more = section.querySelector("#pg-actions [data-more]");
  assert.deepEqual([more.getAttribute("aria-label"), more.getAttribute("data-tip"), more.getAttribute("aria-haspopup")], ["More for Acme Hostels", "More", "true"]);
  assert.ok(section.querySelector("#pg-actions .row-icons").lastElementChild === more, "at the end, after its files");
  more.click();
  assert.deepEqual(entries(), [["Export", ""], ["Export for Docker Compose", ""], ["Share", ""], ["Delete", ""]]);
  assert.equal(menu.querySelectorAll(".menu-sep").length, 1);
  assert.ok(menu.querySelectorAll(".menu-item")[3].classList.contains("danger"));
  page.doc.dispatchEvent(Object.assign(new Ev("click"), { target: page.doc.body }));
  await leave();
});

test("Share and Delete of the page are held for the reasons the list gives, and the Lab UI not answering holds them too", async () => {
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "Turn off Acme Hostels first", delete: "Stop Acme Hostels first" } }, { platforms: [], places: [], id: "local/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  assert.deepEqual(entries(), [["Export", ""], ["Export for Docker Compose", ""], ["Share", "Turn off Acme Hostels first"], ["Delete", "Stop Acme Hostels first"]]);
  page.doc.dispatchEvent(Object.assign(new Ev("click"), { target: page.doc.body }));
  store.offline = true;
  section.querySelector("#pg-actions [data-more]").click();
  assert.deepEqual(entries().map(([, why]) => why), ["Lab UI offline", "Lab UI offline", "Lab UI offline", "Lab UI offline"]);
  store.offline = false;
  page.doc.dispatchEvent(Object.assign(new Ev("click"), { target: page.doc.body }));
  await leave();
});

test("Delete of the page says what goes and what stays in Gravitee, deletes that playground alone, and the page goes back to the list once it is gone", async () => {
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  server.calls = [];
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[3] }));   // the menu is the document's
  await flush();
  assert.deepEqual(effects().notes, [["Stays in Gravitee", ["12 APIs"]]]);
  assert.equal(effects().title, "Delete Acme Hostels");
  assert.deepEqual(effects().steps.map(([label]) => label), ["Deletes"]);
  await answer(true);
  assert.deepEqual(posts("/delete").map((call) => [call.path, call.body]), [["/api/playgrounds/local/hotels/delete", {}]]);
  await leave();
});

test("Export for Docker Compose says what the folder runs, leaves out and needs, then saves it; what comes from a private catalog is said, and the button says it too", async () => {
  const saved = [];
  const make = page.doc.createElement.bind(page.doc);
  page.doc.createElement = (tag) => {
    const el = make(tag);
    if (tag === "a") el.click = () => saved.push(el.download);
    return el;
  };
  server.standalone = { name: "Acme Hostels", file: "hotels-docker-compose.zip", runs: ["API Management", "Databases", "Acme Hostels"],
    leaves_out: ["Alert Engine"], license: ["apim-feature"], host: ["Ollama with a model"], plugins_missing: ["acme-policy"], private: [],
    registries: [] };
  await visit({ ...off, ...HUB }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  server.calls = [];
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[1] }));
  await flush();
  assert.equal(effects().title, "Export Acme Hostels for Docker Compose");
  assert.deepEqual(effects().steps, [["Runs", ["API Management", "Databases", "Acme Hostels"]]]);
  assert.deepEqual(effects().notes, [["Leaves out", ["Alert Engine", "The Lab UI", "Its settings", "The license"]],
    ["Needs", ["A license with apim-feature", "Ollama with a model", "The plugin acme-policy"]]]);
  assert.deepEqual(server.calls.map((call) => [call.path, call.body]), [["/api/playgrounds/@acme-hub/hotels/standalone", { check: true }]],
    "what it holds is asked first");
  assert.equal(text(page.ids["choice-ok"]), "Export");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.at(-1).body, {}, "then the file");
  assert.deepEqual(saved, ["hotels-docker-compose.zip"]);
  await leave();
  const plain = server.standalone;
  server.standalone = { ...plain, license: ["a-feature", "b-feature", "c-feature"], private: [{ key: "1a2b3c", name: "Internal", items: ["Ledger"], item_ids: ["@internal/ledger"] }] };
  await visit({ ...off, ...HUB }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[1] }));
  await flush();
  assert.equal(effects().notes[1][1][0], "A license with 3 features", "more features than fit a line are counted");
  assert.deepEqual(effects().notes.at(-1), ["Private", ["Internal"]]);
  assert.equal(text(page.ids["choice-ok"]), "Export with private content");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.at(-1).body, { private: ["1a2b3c|@internal/ledger"] }, "the file says what the person saw leave, item by item and origin by origin");
  await leave();
  server.standalone = plain;
  page.doc.createElement = make;
});

test("Export says what the file carries, what it needs and what it leaves out, then saves it; what another Lab would block keeps it from being made", async () => {
  const saved = [];
  const make = page.doc.createElement.bind(page.doc);
  page.doc.createElement = (tag) => {
    const el = make(tag);
    if (tag === "a") el.click = () => saved.push(el.download);
    return el;
  };
  server.exported = { name: "Acme Hostels", file: "hotels.zip", carries: [{ kind: "playground", id: "local/hotels", name: "Acme Hostels" },
    { kind: "addon", id: "local/kiosk", name: "Kiosk" }], needs: [{ id: "acme", name: "Acme public" }], problems: [], blocked: [],
  uses_machine: [{ name: "Kiosk", what: ["runs privileged"] }] };
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  server.calls = [];
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[0] }));
  await flush();
  assert.equal(effects().title, "Export Acme Hostels");
  assert.deepEqual(effects().steps, [["Carries", ["Acme Hostels Playground", "Kiosk Add-on"]], ["Needs", ["Acme public Catalog"]]]);
  assert.deepEqual(effects().notes, [["Asks to use the machine", ["Kiosk: runs privileged"]], ["Leaves out", ["Its settings", "The license"]]]);
  assert.deepEqual(server.calls.map((call) => [call.path, call.body]), [["/api/playgrounds/local/hotels/export", { check: true }]], "what it carries is asked first");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.at(-1).body, {}, "then the file");
  assert.deepEqual(saved, ["hotels.zip"]);
  await leave();
  // What comes from a private catalog leaves with the file: it is said, and so is the button
  const plain = server.exported;
  server.exported = { ...plain, private: [{ key: "1a2b3c", name: "Internal", items: ["Ledger"], item_ids: ["@internal/ledger"] }],
    registries: [{ registry: "graviteeio.azurecr.io", images: ["graviteeio.azurecr.io/apim-gateway:4.13.0"] }] };
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[0] }));
  await flush();
  assert.deepEqual(effects().notes[0], ["Private", ["Internal"]], "the catalog, once: what it carries is listed above");
  assert.deepEqual(effects().notes[1], ["Needs access", ["graviteeio.azurecr.io"]], "a registry that asks for a login");
  assert.equal(text(page.ids["choice-ok"]), "Export with private content");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.at(-1).body, { private: ["1a2b3c|@internal/ledger"] }, "the file says what the person saw leave, item by item and origin by origin");
  await leave();
  server.exported = plain;
  // Many origins fold under a line that counts them, one each: a name that starts with a number is no count
  server.exported = { ...plain, private: ["12 Tools", "Internal", "Partner", "Ops", "Legal"].map((name, n) => ({ key: `k${n}`, name, items: ["Ledger"],
    item_ids: ["@internal/ledger"] })) };
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[0] }));
  await flush();
  assert.equal(text(picks.querySelector(".choice-more .choice-summary")), "5 items");
  await answer(false);
  await leave();
  server.exported = plain;
  // What another Lab would block is said, and the file is not made
  server.exported = { ...server.exported, uses_machine: [], blocked: [{ name: "Kiosk", reasons: ["Changes Gateway beyond adding settings"] }] };
  await visit({ ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" } }, { platforms: [], places: [], id: "local/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[0] }));
  await flush();
  assert.deepEqual(effects().notes.at(-1), ["Blocked on another Lab", ["Kiosk: Changes Gateway beyond adding settings"]]);
  assert.equal(page.ids["choice-ok"].getAttribute("aria-disabled"), "true");
  await answer(false);
  page.doc.createElement = make;
  await leave();
});

test("an imported playground moves to Personal from its menu: with what it brings or alone, each saying what moves, stays and is replaced", async () => {
  server.movePlan = {
    together: { moves: ["Acme Hostels", "Kiosk"], stays: [{ name: "Ledger", why: "Used by Notes" }], replaces: [], held: "", uses_machine: ["Kiosk"] },
    alone: { moves: ["Acme Hostels"], stays: [], replaces: ["Acme Hostels"], held: "" }, used_by: "" };
  const imported = { ...off, id: "@hostels/hotels", origin: { id: "imported", name: "Imported" }, held: { share: "", delete: "" } };
  await visit(imported, { platforms: [], places: [], id: "@hostels/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  const entries = menu.querySelectorAll(".menu-item");
  assert.ok(entries.some((el) => text(el) === "Move to Personal"));
  server.calls = [];
  page.doc.dispatchEvent(new Ev("click", { target: entries.find((el) => text(el) === "Move to Personal") }));
  await flush();
  assert.equal(effects().title, "Move Acme Hostels to Personal");
  assert.deepEqual(choices(), [
    ["With its setups and add-ons", "What nothing else uses", [["Moves", ["Acme Hostels", "Kiosk"]], ["Uses this machine", ["Kiosk"]], ["Stays", ["Ledger: Used by Notes"]]]],
    ["Only the playground", "Its setups and add-ons stay in Imported", [["Moves", ["Acme Hostels"]], ["Replaces", ["Acme Hostels"]]]]]);
  assert.equal(effects().action, "Allow and move", "what takes more of this machine is allowed by the person, here");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.map((call) => [call.path, call.body]), [["/api/playgrounds/@hostels/hotels/move", { check: true }],
    ["/api/playgrounds/@hostels/hotels/move", { together: true, allow: true }]]);
  await leave();
  // What the import could not delete is said once the move is done
  page.doc.querySelectorAll(".toast").forEach((t) => t.remove());
  server.moveDone = { warning: "Some files were created by Docker and could not be deleted. Remove the folder .lab/imports/hostels with administrator rights." };
  await visit(imported, { platforms: [], places: [], id: "@hostels/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item").find((el) => text(el) === "Move to Personal") }));
  await flush();
  await answer(true);
  await flush();
  assert.deepEqual(page.doc.querySelectorAll(".toast").map(text), ["Moved. Some files were created by Docker and could not be deleted. Remove "
    + "the folder .lab/imports/hostels with administrator rights."]);
  server.moveDone = undefined;
  await leave();
});

test("a playground of a catalog is copied to Personal from its menu: alone, or with what uses it, which then uses the copy", async () => {
  server.copyPlan = {
    alone: { copies: ["Acme Hostels"], switches: [], uses_machine: [], held: "" },
    with_users: { copies: ["Acme Hostels", "Tour"], switches: ["Notes"], uses_machine: [], held: "" } };
  await visit({ ...off, id: "@acme-hub/hotels", origin: { id: "acme-hub", name: "Acme Hub" } }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  section.querySelector("#pg-actions [data-more]").click();
  server.calls = [];
  page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item").find((el) => text(el) === "Copy to Personal") }));
  await flush();
  assert.equal(effects().title, "Copy Acme Hostels to Personal");
  assert.deepEqual(choices(), [
    ["Only Acme Hostels", "What uses it keeps the original", [["Copies", ["Acme Hostels"]]]],
    ["Acme Hostels and what uses it", "Copied too, so that it uses your copy", [["Copies", ["Acme Hostels", "Tour"]], ["Uses the copy", ["Notes"]]]]]);
  assert.equal(effects().action, "Copy");
  await answer(true);
  await flush();
  assert.deepEqual(server.calls.at(-1).body, { users: false }, "the first choice: the copy alone");
  await leave();
});

test("Delete names the folder by the path the item is read by (display_file), the file it opens being its own", async () => {
  const mine = { ...off, personal: true, id: "local/hotels", held: { share: "", delete: "" }, file: ".lab/catalogs/local/playgrounds/hotels/compose.yml" };
  for (const [display_file, said] of [["playgrounds/hotels/compose.yml", "playgrounds/hotels/"], ["", ".lab/catalogs/local/playgrounds/hotels/"], [undefined, ".lab/catalogs/local/playgrounds/hotels/"]]) {
    await visit({ ...mine, display_file }, { platforms: [], places: [], id: "local/hotels" });
    section.querySelector("#pg-actions [data-more]").click();
    page.doc.dispatchEvent(new Ev("click", { target: menu.querySelectorAll(".menu-item")[3] }));
    await flush();
    assert.deepEqual(effects().steps, [["Deletes", [said]]], String(display_file));
    await answer(false);
    await leave();
  }
});

test("what a playground lacks of the catalogs is the first of Before you start: each reason in its own words, with the button that mends it", async () => {
  store.state = { ...plain, catalogs: ROWS, content: content("ready") };
  const lacks = [need(), need({ reason: "downloading" }), need({ reason: "failed" }), need({ id: "ledger-hub", name: "Ledger Hub", reason: "gone", unit: "Ledger" }), need({ reason: "old", have: "1.0.0" })];
  await visit({ ...off, needs_catalogs: lacks, ...HUB }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  store.state = { ...store.state, catalogs: ROWS };
  playgroundPage.render(store.state);
  const found = prepared().filter((line) => line.kind === "warn");
  assert.deepEqual(found.map((line) => line.name), ["Needs the catalog Partner Bank", "Downloading Partner Bank", "Needs Partner Bank", "Needs Ledger, which is no longer in Ledger Hub", "Needs Partner Bank 2.0 or later"]);
  assert.deepEqual(found.map((line) => line.buttons), [
    [["button", "Add Partner Bank", "add", "partner-bank"]], [], [["button", "Try again", "retry", "partner-bank"]], [], [["a", "Update Partner Bank", "#/catalogs/partner-bank", ""]]]);
  // Each button does what the Catalogs page does for the same catalog: one that is not listed reads the address it was given, in the Add dialog
  // (its second screen); one that failed is downloaded again
  server.calls = [];
  section.querySelector('[data-need="add"]').click();
  await flush();
  assert.deepEqual(posts("/check").filter((c) => c.path === "/api/catalogs/check").map((c) => c.body.address), ["https://example.com/partner/lab-bank.git"]);
  assert.ok(page.doc.querySelectorAll("dialog").some((d) => d.hasAttribute("open") && text(d.querySelector("h2")) === "Add catalog"));
  page.doc.querySelectorAll("dialog").forEach((d) => d.close());
  server.calls = [];
  section.querySelector('[data-need="retry"]').click();
  await flush();
  assert.deepEqual(posts("/check").map((c) => [c.path, c.body]), [["/api/catalogs/partner-bank/check", {}]]);
  assert.equal(found[0].kind, "warn");
  await leave();
});

test("a catalog the Lab lists is added at once, with no dialog; the line of one that failed says why", async () => {
  const listed = catalogRow({ id: "partner-bank", name: "Partner Bank", kind: "suggested", listed: true, commit: "" });
  const failed = catalogRow({ id: "partner-bank", name: "Partner Bank", commit: "", listed: false, state: { key: "unreachable", label: "Unreachable", tone: "warn",
    reason: "Cannot reach github.com.", next: "Check your connection, then try again.", whatsnew: [] } });
  await visit({ ...off, ...HUB, needs_catalogs: [need()] }, { platforms: [], places: [], id: "@acme-hub/hotels" });
  store.state = { ...store.state, catalogs: [FIRST(), listed] };
  playgroundPage.render(store.state);
  server.calls = [];
  section.querySelector('[data-need="add"]').click();
  await flush();
  assert.deepEqual(posts("").filter((c) => c.path === "/api/catalogs").map((c) => c.body), [{ listed: "partner-bank" }]);
  assert.ok(!page.doc.querySelectorAll("dialog").some((d) => d.hasAttribute("open") && text(d.querySelector("h2")) === "Add catalog"), "no dialog for a catalog the Lab lists");
  store.state = { ...store.state, catalogs: [FIRST(), failed], playgrounds: store.state.playgrounds.map((p) => (p.id === "@acme-hub/hotels" ? { ...p, needs_catalogs: [need({ reason: "failed" })] } : p)) };
  playgroundPage.render(store.state);
  const line = prepared().find((one) => one.name === "Needs Partner Bank");
  assert.equal(line.sub, "Cannot reach github.com. Check your connection, then try again.");
  await leave();
});

test("a Lab that reads a level other than the playground's says so, and what to do", async () => {
  for (const [compat, name, sub] of [["new", "Needs a newer Gravitee Lab", "Run make update, then restart the Lab."], ["old", "Written for an older Gravitee Lab", "Ask its owners to update it."]]) {
    await visit({ ...off, compat }, { platforms: [], places: [] });
    assert.deepEqual(prepared().filter((line) => line.kind === "warn").map(({ name: said, sub: why }) => [said, why]), [[name, sub]], compat);
    await leave();
  }
  await visit({ ...off, compat: "ok" }, { platforms: [], places: [] });
  assert.deepEqual(prepared().filter((line) => line.kind === "warn"), []);
  await leave();
});

test("what it uses of this machine is one line among what Start does, in its author's words, once each and cut", async () => {
  const use = (service, why) => ({ service, what: ["runs privileged"], why });
  await visit({ ...off, uses_machine: [use("k3s", "Runs containerd and the kubelet"), use("k3s-agent", "Runs containerd and the kubelet"), use("socket", "Reads the Docker socket")] }, { platforms: [], places: [] });
  const line = prepared().find((one) => one.name === "Uses this machine");
  assert.deepEqual([line.kind, line.action], ["auto", "Runs containerd and the kubelet, Reads the Docker socket"]);
  await leave();
  const long = "x".repeat(300);
  await visit({ ...off, uses_machine: [use("k3s", long)] }, { platforms: [], places: [] });
  const cut = prepared().find((one) => one.name === "Uses this machine").action;
  assert.ok(cut.length <= 120 && cut.endsWith("\u2026"), "the author's text is cut");
  await leave();
  await visit({ ...off, uses_machine: [use("k3s", HOSTILE)] }, { platforms: [], places: [] });
  assert.equal(section.querySelectorAll("img").length, 0, "text, never markup");
  await leave();
  await visit({ ...off }, { platforms: [], places: [] });
  assert.ok(!prepared().some((one) => one.name === "Uses this machine"), "most playgrounds use nothing of it");
  await leave();
});

test("a playground's address that names one about to arrive is not given up while the catalogs download; it opens when it arrives, and is given up once they are done and it is not there", async () => {
  const bounced = [], real = router.replace;
  router.replace = (hash) => bounced.push(hash);
  try {
    const late = { ...hotels(), ...off, ...HUB };
    store.state = { ...plain, catalogs: ROWS, content: content("downloading", { address: "github.com/acme/lab-hub" }), playgrounds: [] };
    playgroundPage.show(["@acme-hub/hotels"]);
    playgroundPage.render(store.state);
    assert.deepEqual(bounced, [], "the address stays");
    assert.equal(text(section.querySelector("#pg-view")), "Loading");
    assert.equal(text(section.querySelector("#pg-title")), "");
    assert.equal(section.querySelector("#pg-actions").children.length, 0, "no action of a page that is not there");
    // The state the download ends with has it: the page is drawn
    store.state = { ...plain, catalogs: ROWS, content: content("ready"), playgrounds: [late] };
    playgroundPage.render(store.state);
    assert.deepEqual(bounced, []);
    assert.equal(text(section.querySelector("#pg-title")), "Acme Hostels");
    // Another address while it downloads shows no page of the one before
    playgroundPage.hide();
    store.state = { ...plain, catalogs: ROWS, content: content("downloading"), playgrounds: [] };
    playgroundPage.show(["@acme-hub/hotels"]);
    playgroundPage.render(store.state);
    assert.equal(text(section.querySelector("#pg-view")), "Loading", "the page that was shown is gone");
    // The download ended and it is not there: the list is where the person goes
    for (const done of ["ready", "failed", "none"]) {
      bounced.length = 0;
      store.state = { ...plain, catalogs: ROWS, content: content(done), playgrounds: [] };
      playgroundPage.render(store.state);
      assert.deepEqual(bounced, ["#/playgrounds"], done);
    }
    // A server that does not say the content gives up at once, as it always did
    bounced.length = 0;
    store.state = { ...plain, playgrounds: [] };
    playgroundPage.render(store.state);
    assert.deepEqual(bounced, ["#/playgrounds"]);
  } finally {
    router.replace = real;
  }
  await leave();
});

test("a playground of this machine that is not listed is not waited for while the catalogs download: it is listed at once, so it is gone (just deleted from its More)", async () => {
  const bounced = [], real = router.replace;
  router.replace = (hash) => bounced.push(hash);
  try {
    store.state = { ...plain, catalogs: ROWS, content: content("downloading", { address: "github.com/acme/lab-hub" }), playgrounds: [] };
    playgroundPage.show(["local/gone"]);
    playgroundPage.render(store.state);
    assert.deepEqual(bounced, ["#/playgrounds"], "the list is where the person goes, as for any address that leads nowhere");
    bounced.length = 0;
    playgroundPage.hide();
    playgroundPage.show(["@acme-hub/hotels"]);
    playgroundPage.render(store.state);
    assert.deepEqual(bounced, [], "one of a catalog is still waited for");
  } finally {
    router.replace = real;
  }
  await leave();
});

test("a catalog's name and the author's words are text, never markup, in the head and the lines", async () => {
  await visit({ ...off, ...HUB, origin: origin({ name: HOSTILE }), needs_catalogs: [need({ name: HOSTILE })], uses_machine: [{ service: "a", what: [], why: HOSTILE }] },
    { platforms: [], places: [], id: "@acme-hub/hotels" });
  assert.equal(section.querySelectorAll("img").length, 0);
  const named = section.querySelectorAll(".pairs a").find((link) => link.getAttribute("href").startsWith("#/catalogs/"));
  assert.equal(text(named), HOSTILE, "as text, whole");
  assert.ok(prepared().some((line) => line.name === `Needs the catalog ${HOSTILE}`));
  assert.ok(prepared().some((line) => line.action === HOSTILE));
  await leave();
});
