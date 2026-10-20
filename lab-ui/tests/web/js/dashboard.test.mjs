// The Dashboard (web/js/pages/dashboard.js), run for real over a small DOM against the state of the lab, what /api/dashboard and its stream
// say and the routes of the platforms. It answers its questions in the order a person asks them: is it all right (the head, what runs
// behind what is saved and why, and its one action), then two cards side by side, what is in the Gravitee platform and which version
// (each product named once, with a line for its installation on this machine and its consoles, and the Gravitee Cloud environments under
// the product that runs there) and what else is on (the catalog, with the search and the filters of the lists), and last what the lab's
// containers use of the machine (Resources: two live charts and the storage). It acts as the pages that own the actions do (the head as the Containers', an item as its list's, on the same keys), draws
// what the store says live, reads its own numbers when it opens, when the lab moves and by Refresh, listens to the load only while it
// is seen, moves its charts from the clock and not from the samples, reads the Cloud in parallel without holding up the rest, and
// separates nothing with a dot, a bullet or a slash.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, HOSTILE, catalog as catalogRow, content, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// What the server answers, and every request it was asked
const server = { dashboard: null, platforms: [], checks: {}, calls: [], refusal: "", hold: false, failing: "" };
const reply = (body, status = 200) => ({ ok: status < 400, status, statusText: "", headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path, options = {}) => {
    const method = options.method || "GET", body = options.body ? JSON.parse(options.body) : undefined;
    server.calls.push({ method, path, body });
    if (path === "/api/state") return reply(store.state);
    if (path.startsWith("/api/dashboard")) {
      const found = typeof server.dashboard === "function" ? server.dashboard(path) : server.dashboard;
      return found && found.error ? reply(found, 500) : reply(found);
    }
    if (path === "/api/platforms") {
      if (server.hold) return new Promise(() => {});   // not answered yet
      if (server.failing && method === "GET") return reply({ error: server.failing }, 500);
      if (method === "POST") {
        if (server.refusal) return reply({ error: server.refusal }, 400);
        server.platforms = [...server.platforms, PLATFORM];
        return reply(PLATFORM);
      }
      return reply({ platforms: server.platforms });
    }
    if (path.endsWith("/plan")) return reply(server.plan || { dialog: null });
    const check = path.match(/^\/api\/platforms\/([a-z0-9-]+)\/check$/);
    if (check) {
      const found = typeof server.checks[check[1]] === "function" ? server.checks[check[1]]() : server.checks[check[1]];
      if (found instanceof Error) throw found;
      return reply(await found);
    }
    return reply({});
  },
});
// The streams: each one opened is kept, with what it was told to do
const streams = [];
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.closed = false; streams.push(this); }
  close() { this.closed = true; }
  send(sample) { this.onmessage({ data: JSON.stringify(sample) }); }
  addEventListener() {}
};
// A button that fades back to its face reads how long it takes from the stylesheet: no fade here
// The clock the charts are read against, and the animation frames, which a test runs by hand at the time it chooses
let clock = Date.now();
Date.now = () => clock;
const queued = new Map();
let asked = 0;
globalThis.requestAnimationFrame = (fn) => { queued.set(++asked, fn); return asked; };
globalThis.cancelAnimationFrame = (id) => { queued.delete(id); };
const frame = (seconds) => {
  clock = seconds * 1000;
  const due = [...queued.values()];
  queued.clear();
  due.forEach((fn) => fn());
};
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
page.ids.health.append(page.doc.createElement("span"));   // the spinner that the status dot replaces
globalThis.innerHeight = 800;   // a menu opens under its button, or above it when the window ends first
const { store, router } = await import("../../../web/js/core.js");
const { dashboardPage } = await import("../../../web/js/pages/dashboard.js");
const { stackPage } = await import("../../../web/js/pages/stack.js");
const { renderShell } = await import("../../../web/js/components/sidebar.js");
page.ids["combo-pop"].innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { Combo } = await import("../../../web/js/components/combo.js");
Combo.init();

// The dialog of what an action does (ui.js `effectsDialog`): the browser tells it which choice is checked, which the stub does not
const choice = page.ids.choice, picks = page.ids["choice-list"];
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input")[0] : El.prototype.querySelector.call(picks, selector));
const answer = async (confirmed) => {
  choice.returnValue = confirmed ? "ok" : "";
  choice.onclose();
  await flush();
};

// --- The lab -----------------------------------------------------------------------------

const RUNNING = { key: "running", label: "Running", running: true };
const HEALTHY = { key: "healthy", label: "Healthy", running: true };
const STOPPED = { key: "stopped", label: "Stopped", running: false };
const ABSENT = { key: "absent", label: "Not created", running: false };
const OFF = { key: "off", label: "", running: false };
const NONE = { note: "", note_tone: "", note_title: "", note_target: null, note_file: null };
const ACTION = (verb, more = {}) => ({ do: verb, label: verb === "stop" ? "Stop" : "Start", enabled: true, reason: null, dialog: null, ...more });

const service = (name, state, more = {}) => ({ label: name, description: "", container: `gio-${name}`, image: `graviteeio/${name}:1`, tag: "1", registry: "graviteeio",
  built: false, role: null, needs_license: false, ports: [], state, uptime: state.running ? "5 min" : "", on: true, running_tag: state.running ? "4.12.20" : null,
  behind: false, brings: [], recreates: [], ...NONE, ...more });
const group = (id, name, services, state, more = {}) => ({ id, name, services, state, progress: null, version: "4.12.20", registry: null, registry_label: null,
  product: id, file: "docker-compose.yml", env_file: ".env", settings: [], brings: [], recreates: [], containers: { running: state.key === "stopped" ? 0 : services.length, total: services.length },
  ...NONE, ...more });
const item = (kind, id, name, more = {}) => ({ kind, id, name, personal: false, held: null, category: "Demo", description: `${name} for the demo`, file: `${kind}s/${id}/compose.yml`,
  env_file: null, settings: [], on: false, up: false, state: OFF, progress: null, ...NONE, links: [], services: [], starred: false, action: ACTION("start"), secondary: null,
  failed_job: null, login: null, ...more });
// An item that is on and runs, or is on and does not
const running = (kind, id, name, more = {}) => item(kind, id, name, { on: true, up: true, state: RUNNING, action: ACTION("stop"), ...more });
const waiting = (kind, id, name, more = {}) => item(kind, id, name, { on: true, state: STOPPED, ...more });

// In the server's order: the legacy ones last
const link = (label, port, service, section, more = {}) => ({ label, url: `http://localhost:${port}`, port: `${port}`, service, section, legacy: false, up: true, ...more });
const LINKS = [
  link("AM Console", 8081, "am-ui", "Access Management"),
  link("APIM Console", 8084, "apim-ui", "API Management"),
  link("Developer Portal", 8085, "apim-portal", "API Management"),
  link("Jade", 16686, "jade", "Observability"),
  link("Mail server", 5080, "mail", "Mail", { up: false }),
  link("Classic Developer Portal", 8082, "apim-portal", "API Management", { legacy: true }),
];

// The platform running with its three groups, a playground off and three add-ons, one of them on and running
// The add-on that runs a hybrid gateway for Gravitee Cloud here: its service runs the image of API Management's gateway
const hybrid = (up = false) => (up ? running : item)("addon", "hybrid", "Hybrid gateway", { services: ["apim-hybrid-gateway"],
  gateway: { platform: "cloud", service: "apim-hybrid-gateway", token: "HYBRID_CLOUD_TOKEN", domains: ["localhost:8090"] } });
function lab({ core = "running", groups, links = LINKS, platform = {}, extra = {}, gateway: runs = true, addons, ...more } = {}) {
  const on = core === "running" ? HEALTHY : core === "absent" ? ABSENT : STOPPED;
  const services = { "apim-gateway": service("apim-gateway", on), "apim-management-api": service("apim-management-api", on), "apim-ui": service("apim-ui", on),
    "apim-portal": service("apim-portal", on), "am-gateway": service("am-gateway", on), "am-ui": service("am-ui", on), mongodb: service("mongodb", on),
    jade: service("jade", HEALTHY), "apim-hybrid-gateway": service("apim-hybrid-gateway", STOPPED, { image: "graviteeio/apim-gateway:4.12.21" }), ...extra };
  const state = core === "running" ? RUNNING : STOPPED;
  const found = groups || [
    group("api-management", "API Management", ["apim-gateway", "apim-management-api", "apim-ui", "apim-portal"], state, { version: "4.12.20" }),
    group("access-management", "Access Management", ["am-gateway", "am-ui"], state, { version: "4.12.7" }),
    group("databases", "Databases", ["mongodb"], state, { version: null, product: null }),
  ];
  return {
    blind: false, wait: "", docker: { error: null, warning: null }, license: null, products: [], categories: ["Observability", "Mail"], job: null, problems: [],
    links, services,
    platform: { name: "Gravitee platform", state, progress: null, groups: found, containers: { running: core === "running" ? 7 : 0, total: 7 }, file: "docker-compose.yml",
      env_file: ".env", settings: [], start: { needed: core !== "running", starts: [], restarts: [], enabled: true, reason: null, dialog: null },
      apply: { needed: false, restarts: [], starts: [], dialog: null }, behind: [], restart: { needed: core === "running", enabled: true, reason: null },
      update: { needed: core === "running", enabled: true, reason: null, dialog: null }, reset_turns_off: null, ...platform },
    playgrounds: [item("playground", "acme-bakery", "Acme Bakery")],
    setups: [item("setup", "events", "Event Streams", { members: [] }), item("setup", "tracing", "Tracing", { members: [] })],
    addons: [...(addons || [running("addon", "jade", "Jade", { services: ["jade"], links: [LINKS[3]] }), item("addon", "kafka", "Kafka"), item("addon", "mail", "Mail server")]),
      ...(runs ? [hybrid(runs === "up")] : [])],
    ...more,
  };
}
// The state a test sets, as the router would hand it to the page
store.state = lab();

// --- What the server says ----------------------------------------------------------------

const PLATFORM = { id: "cloud-eu", name: "Gravitee Cloud", kind: "cloud", url: "https://eu.cloud.gravitee.io",
  account: { id: "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46", hrid: "my-account", name: "My Account" }, tokenSet: true };
const environment = (name) => ({ id: `env-${name.toLowerCase()}`, name, organizationId: "o1", token: { state: "unset" } });
const gateway = (more) => ({ id: "g1", name: "lab", environmentId: "env-development", type: "HYBRID", status: "DEPLOYED",
  connection: "CONNECTED", version: "4.12.20", domains: ["localhost:8090"], here: false, kept: true, ...more });
const check = (gateways = [], more = {}) => ({ state: "ok", message: "", environments: ["Development", "Test", "Production"].map(environment), gateways, ...more });

const product = (id, name, more = {}) => ({ id, name, deployed: "4.12.20", deployed_state: "running", channel: "stable", stable: "4.12.21",
  prerelease: "4.13.0-alpha.4", latest: "4.12.21", status: "newer", ...more });
// What `docker system df` says, as the server gives it: a size and what is unused for each type, and the total of the four
const DF = (more = {}) => {
  const found = { images: { count: 122, size: 58890000000, reclaimable: 42700000000 }, containers: { count: 20, running: 19, size: 2243000000, reclaimable: 131100 },
    volumes: { count: 73, size: 5944000000, reclaimable: 5479000000 }, cache: { count: 1630, size: 18620000000, reclaimable: 9709000000 }, ...more };
  const total = (field) => ["images", "containers", "volumes", "cache"].reduce((sum, kind) => sum + found[kind][field], 0);
  return { ...found, total: { size: total("size"), reclaimable: total("reclaimable") }, read_at: 1791734641 };
};
// A full answer: two products of the platform (one behind, one that runs a pre-release), Docker and the machine
const dashboard = (more = {}) => ({
  docker: { engine: "29.7.2", compose: "5.4.0", os: "Docker Desktop", cpus: 6, memory: 8320028672 },
  machine: { cpus: 11, memory_total: 38654705664, memory_available: 11066654720, disk_total: 494384795648, disk_free: 96660758528 },
  data: DF(),
  products: [
    product("api-management", "API Management"),
    product("access-management", "Access Management", { deployed: "4.13.0-alpha.7", channel: "pre-release", stable: "4.12.8", prerelease: "4.13.0-alpha.7", latest: "4.13.0-alpha.7", status: "latest" }),
  ],
  gateways: { cloud: { stable: "4.12.21" } },
  pending: false,
  ...more,
});
const section = page.doc.createElement("section");
section.id = "page-dashboard";   // as the page's own section is, which the focus given back is looked for in
page.doc.body.append(section);
dashboardPage.init(section);
const containers = page.doc.createElement("section");
page.doc.body.append(containers);
stackPage.init(containers);
// A browser tells a form's button whether the required fields are filled, which the stub does not
const form = page.doc.querySelector("#platform-url").closest("form");
form.checkValidity = () => form.querySelectorAll("[required]").every((el) => el.value);
page.doc.querySelector("#use-token").closest("form").checkValidity = () => true;
const block = (id) => section.querySelector(`[data-card="${id}"]`);
// What an element says, each of its parts apart: what is drawn in separate cells or on separate lines reads as separate words
// (but what a button keeps for when it works)
const said = (node) => (node.children ? node.children.filter((child) => !child.getAttribute?.("aria-hidden")).map(said).filter(Boolean).join(" ") : node.data.trim()).replace(/\s+/g, " ").trim();
// A cell of Resources by its words: what it says, and not what only a tooltip says or a key repeats
const tileSaid = (tile) => (function walk(node) {
  const kids = node.children;
  return kids ? kids.filter((el) => !el.getAttribute?.("aria-hidden") && el.getAttribute?.("role") !== "tooltip").map(walk).filter(Boolean).join(" ") : node.data.trim();
}(tile)).replace(/\s+/g, " ").trim();
// What a tooltip says, a line each: the tooltip of the element that opens it (a label, a figure, or a part of the bar), which is
// the one of the whole Lab UI
const tipOf = (el) => bubble(el.closest(".tip") || el.querySelector(".tip"));
// A part of the bar by its name, the first line of its tooltip
const named = (part) => tipOf(part)[0];
const stream = () => streams.filter((found) => found.url === "/api/dashboard/stats").at(-1);
const requests = () => server.calls.map((call) => `${call.method} ${call.path}`);
const reads = () => requests().filter((call) => call.startsWith("GET /api/dashboard")).length;
// The rows of a block; those of the Cloud are its cards' heads and rows, in order
const rows = (id) => block(id).children;
// The row of Gravitee Cloud, in the platform's card
const cloudRow = () => block("cloud").children[0];
// A toggle chip of the filters turned on or off, as a person ticks it
const toggle = (selector, on) => {
  const box = section.querySelector(selector);
  if (box.checked === on) return;
  box.checked = on;
  box.dispatchEvent(new Ev("change"));
};
const hrefs = (el) => el.querySelectorAll("a").map((a) => a.getAttribute("href"));
const shown = () => section.querySelectorAll("[data-stack]").filter((button) => !button.hidden).map((button) => button.dataset.stack);

// Resources unfolded or folded, as a person does it: a click on its line
const unfold = (open = true) => {
  if (section.querySelector(".dash-resources").classList.contains("open") !== open) section.querySelector("[data-res-head] .dash-res-sum").click();
};
// The page opened afresh over a lab: nothing asked, no stream; drawn as the router draws it, once it was shown. Resources is folded, as it
// is by default, unless `unfolded`.
async function open({ body = dashboard(), listed = [], checks = {}, state = lab(), unfolded = false } = {}) {
  dashboardPage.hide();
  clock += 200000;   // what the load held is old, and gone
  store.state = state;
  Object.assign(server, { dashboard: body, platforms: listed, checks, calls: [], refusal: "" });
  store.pending.clear();
  dashboardPage.show();
  // The filters are kept from one visit to the next, as the lists keep theirs: a test begins with none
  for (const box of ["[data-running]", "[data-starred]"]) toggle(box, false);
  const search = section.querySelector('[data-filter="q"]');
  search.value = "";
  search.dispatchEvent(new Ev("input"));
  dashboardPage.render(state);
  await flush();
  dashboardPage.render(state);
  unfold(unfolded);
}
// The lab moved: the state the store holds is drawn
async function move(state) {
  store.state = state;
  dashboardPage.render(state);
  await flush();
}

// --- Before anything is read --------------------------------------------------------------
// The page keeps what it read when it is left (it is drawn again as it comes back, and read again meanwhile): these are the first of its
// life, while nothing was read

test("before the numbers come the page draws what the state says at once: no version, which the state does not know to run, a quiet Cloud and Resources empty", async () => {
  server.hold = true;   // the platforms are not answered either
  try {
    await open({ body: new Promise(() => {}) });
    assert.deepEqual(cells().map(tileSaid), ["CPU —", "Memory —", "Storage Loading", "Docker —"]);
    assert.equal(section.querySelectorAll(".tip-text").length, 0, "no tooltip says what is not known yet");
    assert.equal(products().length, 2, "the databases have no version: they are not a product");
    assert.equal(text(part(products()[0], "name")), "API Management");
    assert.equal(ver(products()[0]), "", "the version saved is not what runs: it is not said as if it were");
    assert.equal(products()[0].querySelectorAll(".s-version").length, 0);
    assert.equal(products()[0].querySelectorAll(".dash-wait").length, 1, "a spinner where the version is being read, not a dash and not the saved one");
    assert.ok(catalog().length > 0, "what is on is the state's: it needs no read");
    // The Cloud says it is loading, in its one row, with nothing to click before it is known whether one is attached
    assert.equal(rows("cloud").length, 1);
    assert.equal(said(cloudRow()), "Gravitee Cloud Loading");
    assert.equal(block("cloud").querySelectorAll("button, a").length, 0);
    assert.equal(text(block("engine")), "Docker —", "a section with its label, and a dash while nothing is read");
    // Resources is its line, folded, its figures dashes and its storage loading
    assert.equal(said(block("summary")), "CPU — Memory —");
    assert.ok(!section.querySelector(".dash-resources").classList.contains("open"));
  } finally {
    server.hold = false;
  }
  dashboardPage.hide();
});

test("a read that fails says why in the heading of Resources, and the rest of the page is untouched; platforms that cannot be listed say why in the row of the Cloud, with a way to ask again", async () => {
  server.failing = "Lab UI offline";
  try {
    await open({ body: { error: "Not found" } });
    assert.equal(text(block("engine")), "Docker Not found");
    assert.equal(said(block("summary")), "CPU — Memory — Not found", "the folded line says it too, in the place of Docker's versions");
    assert.equal(products().length, 2);
    assert.equal(ver(products()[0]), "—", "a dash, never the version saved");
    assert.equal(said(cloudRow()), "Gravitee Cloud Accounts cannot be read Check again");
    assert.deepEqual(tipOf(cloudRow().querySelector(".status .tip-btn")), ["Accounts cannot be read", "Lab UI offline"], "why is the popover of its state, not a sentence beside it");
    assert.equal(cloudRow().querySelector(".status").getAttribute("class"), "status err", "in red, as a state");
    assert.equal(block("cloud").querySelectorAll("[data-connect]").length, 0, "no way to connect one while they cannot be listed");
    // Check again reads them again, and says it is checking meanwhile, once however many times it is clicked
    server.failing = "";
    server.hold = true;
    server.calls = [];
    block("cloud").querySelector("[data-reload]").click();
    assert.equal(said(cloudRow()), "Gravitee Cloud Accounts cannot be read Checking");
    assert.equal(requests().filter((call) => call === "GET /api/platforms").length, 1);
    server.hold = false;
    dashboardPage.hide();
    server.calls = [];
    dashboardPage.show();
    await flush();
    assert.equal(said(cloudRow()), "Gravitee Cloud No account connected Connect account");
  } finally {
    server.failing = "";
    server.hold = false;
  }
  await open({ body: { error: "Not found" } });
  assert.equal(said(cloudRow()), "Gravitee Cloud No account connected Connect account");
  // It is not left as it is: the numbers are read again after a while, and what they say fills in
  assert.equal(reads(), 1);
  server.dashboard = dashboard();
  mock.timers.tick(9900);
  await flush();
  assert.equal(reads(), 1, "not yet");
  mock.timers.tick(200);
  await flush();
  assert.equal(reads(), 2);
  assert.equal(text(block("engine")), "Docker 29.7.2 Compose 5.4.0");
  assert.equal(ver(products()[0]), "4.12.20");
  mock.timers.tick(60000);
  await flush();
  assert.equal(reads(), 2, "read: nothing asks again");
  dashboardPage.hide();
});

// --- The head ------------------------------------------------------------------------------

test("its head is the platform's: the title and the one action that fits, never more than one; what is to apply is the banner's, and the platform's state is the sidebar's", async () => {
  await open();
  assert.equal(text(section.querySelector(".page-head h1")), "Dashboard");
  assert.equal(section.querySelectorAll("[data-platform-state], .page-head .state, [data-late]").length, 0, "the state of the platform is said once, by the chip of the sidebar, and no badge sits beside the title");
  assert.deepEqual(shown(), ["stop"], "running and as saved: Stop");
  assert.ok(!section.querySelector('[data-stack="stop"]').classList.contains("primary"), "nothing leads while nothing is to start");
  // The platform stopped: Start, the one primary
  await move(lab({ core: "stopped" }));
  assert.deepEqual(shown(), ["start"]);
  assert.ok(section.querySelector('[data-stack="start"]').classList.contains("primary"));
  // Something runs behind what is saved: the head keeps Stop; the Apply is the banner's, with the reason, on every page but Containers
  const why = ["API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs.", "Alert Engine: its settings changed since it started: INTERNAL_API_PASSWORD."];
  const behind = [{ kind: "group", id: "api-management", name: "API Management", reason: why[0] }, { kind: "group", id: "alert-engine", name: "Alert Engine", reason: why[1] }];
  await move(lab({ platform: { apply: { needed: true, restarts: [], starts: [], dialog: null }, behind } }));
  assert.equal(section.querySelectorAll("[data-late]").length, 0);
  assert.deepEqual(shown(), ["stop"], "Apply is not offered twice: the banner has it");
  // What is left to the Containers page is not here: no Restart, no Update, no menu with Remove containers and Reset
  assert.equal(section.querySelectorAll('[data-stack="restart"], [data-stack="update"], .page-head [data-more]').length, 0);
  dashboardPage.hide();
});

test("a platform with an issue says what it is in the popover of the sidebar's chip, and the Apply button is never there with no explanation", async () => {
  const note = { note: "Gateway exited (1)", note_tone: "err", note_title: "", note_target: null, note_file: null };
  const license = { note: "Needs a license", note_tone: "warn", note_title: "", note_target: null, note_file: ".env" };
  const groups = [group("api-management", "API Management", ["apim-gateway"], { key: "issue", label: "Issue" }, note),
    group("access-management", "Access Management", ["am-gateway"], RUNNING), group("alert-engine", "Alert Engine", ["alert-engine"], OFF, license)];
  const behind = [{ kind: "group", id: "access-management", name: "Access Management", reason: "Access Management: 4.12.7 is saved, 4.13.0-alpha.7 still runs." }];
  await open({ state: lab({ groups, platform: { state: { key: "issue", label: "Issue" }, apply: { needed: true, restarts: [], starts: [], dialog: null }, behind } }) });
  renderShell();
  const chip = page.ids["health-state"], health = page.ids.health;
  assert.deepEqual([text(page.ids["health-text"]), chip.getAttribute("class").trim()], ["Platform issue", "state err"]);
  assert.deepEqual(JSON.parse(health.getAttribute("data-tip-card")).rows[0].lines, [["API Management: Gateway exited (1)"], ["Alert Engine: Needs a license"]], "what is wrong, by product");
  assert.equal(text(page.ids["health-said"]), "Platform issue", "a screen reader hears it change");
  // And what is to apply is the banner's, with its reason: Apply is not offered with no reason
  assert.ok(text(page.ids.banner).startsWith("Apply needed"));
  assert.deepEqual(tipOf(page.ids.banner.querySelector(".tip-btn")), ["Access Management: 4.12.7 is saved, 4.13.0-alpha.7 still runs."]);
  // A state with nothing to explain has no popover: running, stopped, Docker that does not answer (the banner says why)
  await move(lab());
  renderShell();
  assert.equal(health.getAttribute("data-tip-card"), null);
  await move(lab({ blind: true, docker: { error: "Cannot connect to the Docker daemon", warning: null }, wait: "Docker is not reachable" }));
  renderShell();
  assert.equal(text(page.ids["health-text"]), "Docker is not reachable");
  assert.equal(health.getAttribute("data-tip-card"), null, "the banner says the cause once");
  dashboardPage.hide();
});

test("a button that is still saying how its action went stays alone: nothing is offered beside it", async () => {
  await open();
  store.pending.set("platform", "stop");
  await move(lab({ core: "stopped" }));
  assert.deepEqual(shown(), ["stop"], "Stop stays in view until it has said how it went; Start waits");
  store.pending.clear();
  await move(lab({ core: "stopped" }));
  assert.deepEqual(shown(), ["start"]);
  dashboardPage.hide();
});

test("its Start, Apply and Stop are the Containers page's, button for button and on the same keys, so that both show the same progress", async () => {
  const state = lab({ platform: { apply: { needed: true, restarts: [], starts: [], dialog: null } } });
  await open({ state });
  stackPage.render(state);
  const buttons = (root, verbs) => verbs.map((verb) => {
    const button = root.querySelector(`[data-stack="${verb}"]`);
    return ["class", "title", "data-act-for", "data-act-verb", "hidden"].map((name) => button.getAttribute(name) || "");   // paintBusy leaves a title where there was none
  });
  assert.deepEqual(buttons(section, ["start", "apply", "stop"]), buttons(containers, ["start", "apply", "stop"]));
  // The same actions, the same keys: the Dashboard's say that they act on the whole platform
  assert.deepEqual(["start", "stop"].map((verb) => text(section.querySelector(`[data-stack="${verb}"] .pbtn-face`))), ["Start all", "Stop all"]);
  assert.deepEqual(["start", "stop"].map((verb) => text(containers.querySelector(`[data-stack="${verb}"] .pbtn-face`))), ["Start", "Stop"]);
  assert.ok(section.querySelectorAll(".page-actions [data-act-for]").every((button) => button.dataset.actFor === "platform"));
  assert.equal(containers.querySelectorAll(".page-actions button").length, 6, "the Containers page keeps every action");
  assert.equal(containers.querySelector("[data-more]").getAttribute("aria-label"), "More");
  dashboardPage.hide();
});

test("a click on its Stop stops the platform as the Containers page does: one job, on the key of the platform", async () => {
  await open();
  section.querySelector('[data-stack="stop"]').click();
  await flush();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => call.path), ["/api/stack/stop"]);
  assert.equal(store.pending.get("platform"), "stop", "its button says so from the click");
  dashboardPage.hide();
});

test("with the platform stopped the banner leaves its Start to the head, and the Apply it names is on every page but Containers", async () => {
  const banner = () => page.ids.banner.querySelectorAll(".notice").map((line) => said(line));
  const drawn = (state, id) => {
    store.state = state;
    router.current = { page: { id }, params: [] };
    renderShell();
    return banner();
  };
  assert.deepEqual(drawn(lab({ core: "stopped" }), "dashboard"), []);
  assert.deepEqual(drawn(lab({ core: "stopped" }), "playgrounds"), ["Gravitee platform is stopped Start"], "any other page has it");
  const why = "API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs.";
  const late = lab({ platform: { apply: { needed: true, restarts: [], starts: [], dialog: null },
    behind: [{ kind: "group", id: "api-management", name: "API Management", reason: why }] } });
  assert.deepEqual(drawn(late, "stack"), [], "Containers shows it in its table");
  // One notice: its headline, a summary that names no product, whose popover says which and why, and Apply
  for (const id of ["dashboard", "playgrounds", "images", "plugins"]) assert.deepEqual(drawn(late, id), ["Apply needed 1 container differs from what is saved Apply"], id);
  // Its name starts with the words it shows, so that a voice that says them finds it
  assert.deepEqual(page.ids.banner.querySelectorAll(".tip-btn").map((button) => button.getAttribute("aria-label")), [`1 container differs from what is saved: ${why}`]);
});

// --- Products ----------------------------------------------------------------------------------
// The left card: one row for each product, on four columns (the product, its version, its update, its consoles), and under them the
// Gravitee Cloud environments the Lab uses

const cls = (row) => row.getAttribute("class") || "";
const elements = (row) => row.children.filter((child) => child.getAttribute);
const products = () => rows("products").filter((row) => cls(row).includes("dash-product"));
const part = (row, name) => row.querySelector(`.dash-${name}`);
// What a version cell and a status cell say, without the word that names each for a screen reader (`sr-only`)
const ver = (row) => said(part(row, "ver")).replace(/^Version ?/, "");
const upd = (row) => said(part(row, "upd")).replace(/^Status ?/, "");
const keys = () => rows("products").map((row) => row.dataset.key);
const cells = () => section.querySelectorAll(".dash-cell");
const columns = (row) => elements(row).map((cell) => cls(cell).split(" ")[0]);

test("the card is the Gravitee platform: a row for each product, named once, with its version and its update, and nothing else on the row", async () => {
  await open();
  const card = block("products").parent;
  assert.equal(text(card.querySelector(".card-head h2")), "Gravitee Platform");
  assert.deepEqual(card.querySelectorAll(".dash-heads span").map(text), ["Product", "Version", "Status", ""], "Status: an issue, what runs behind or whether a newer version is out");
  assert.deepEqual(keys(), ["product:api-management", "product:access-management"], "one row for each product: no band, no line for the installation");
  assert.deepEqual(products().map(columns), products().map(() => ["dash-name", "dash-ver", "dash-upd", "dash-open"]), "the same four columns for every product");
  assert.deepEqual(products().map((row) => text(part(row, "name"))), ["API Management", "Access Management"]);
  assert.deepEqual(products().map((row) => part(row, "name").querySelector(".dot-status").getAttribute("class")), ["dot-status running", "dot-status running"]);
  assert.equal(products().every((row) => row.querySelectorAll(".dash-note, .list-note, .dash-where").length === 0), true, "nothing says This machine, and no second line");
  assert.deepEqual(products().map((row) => ver(row)), ["4.12.20", "4.13.0-alpha.7"], "a pre-release says it by its version: no pill says it again");
  assert.deepEqual(products().map((row) => upd(row)), ["Update available", "Up to date"]);
  // The update is the state component: a muted check and Up to date when it is the latest of its channel, amber and Update available
  // when there is a newer one, which its popover names beside the one that runs, and where it is chosen
  assert.deepEqual(products().map((row) => [part(row, "upd").querySelector(".dot-status").getAttribute("class"), cls(part(row, "upd").querySelector(".status"))]),
    [["dot-status warn", "status warn"], ["dot-status done", "status muted"]]);
  assert.deepEqual(tipOf(part(products()[0], "upd").querySelector(".tip-btn")), ["Update available", "4.12.20 4.12.21", "Choose it on the Images page."]);
  assert.equal(text(part(products()[0], "upd").querySelector(".pop-captions")), "Running Latest release");
  assert.deepEqual(tipOf(part(products()[1], "upd").querySelector(".tip-btn")), ["Up to date", "4.13.0-alpha.7 is the latest pre-release"]);
  assert.deepEqual(products().map((row) => row.querySelectorAll(".pill").map(text)), [[], []], "no pill on a row that is all right");
  assert.deepEqual(products().map((row) => part(row, "ver").querySelectorAll(".s-version").map(text)), [["4.12.20"], ["4.13.0-alpha.7"]], "a version is in mono");
  assert.doesNotMatch(text(card), /containers? of|\d of \d/, "no count of containers on a row that is all right");
  assert.doesNotMatch(text(card), /Apply needed|Databases/);
  assert.doesNotMatch(products().map(text).join(" "), /This machine/, "a row never says where it runs in words: API Management's control is two icons");
  assert.equal(card.querySelectorAll(".dash-foot").length, 0, "no foot: the page of the versions is a button of the head");
  dashboardPage.hide();
});

test("a product that runs behind what is saved says so in its update column, with which version is saved and which runs in a tooltip, and no check says all is right", async () => {
  const late = (title) => ({ note: "Apply needed", note_tone: "warn", note_title: title, note_target: null, note_file: null, note_apply: true });
  await open({ state: lab({ groups: [
    group("api-management", "API Management", ["apim-gateway", "apim-management-api", "apim-ui", "apim-portal"], RUNNING, late("4.12.20 is saved, 4.13.0-alpha.4 still runs.")),
    group("access-management", "Access Management", ["am-gateway", "am-ui"], RUNNING, late("4.12.7 is saved, 4.13.0-alpha.7 still runs.")),
    group("databases", "Databases", ["mongodb"], RUNNING, { ...late("8.0.29 is saved, 8.3.11 still runs."), version: null, product: null }),
  ] }) });
  const badges = products().map((row) => part(row, "upd").querySelector(".tip-btn"));
  assert.deepEqual(badges.map(tileSaid), ["Apply needed", "Apply needed"], "a badge on each product that is behind, and none for the databases, which are no product here");
  assert.deepEqual(badges.map(tipOf), [["4.12.20 is saved, 4.13.0-alpha.4 still runs."], ["4.12.7 is saved, 4.13.0-alpha.7 still runs."]]);
  assert.deepEqual(badges.map((badge) => badge.getAttribute("class").trim()), ["tip-btn pill warn", "tip-btn pill warn"]);
  assert.ok(badges[0].querySelector("svg use").getAttribute("href").endsWith("#i-info"), "with the information mark");
  // The version is what runs; the badge is in the place of the update, so that no arrow and no green check says anything else
  assert.deepEqual(products().map((row) => ver(row)), ["4.12.20", "4.13.0-alpha.7"]);
  assert.deepEqual(products().map((row) => upd(row)), ["Apply needed", "Apply needed"]);
  assert.equal(block("products").querySelectorAll(".dash-upd .status").length, 0, "no word of a newer version beside it");
  dashboardPage.hide();
});

test("a product that runs behind what is saved and has a newer version out says both: the badge in its Status, the newer version in its popover", async () => {
  const causes = [{ kind: "tag", saved: "4.12.20", running: "4.13.0-alpha.4", older: true, services: [] }];
  const late = { note: "Apply needed", note_tone: "warn", note_title: "4.12.20 is saved, 4.13.0-alpha.4 still runs.", note_target: null, note_file: null, note_apply: true, note_causes: causes };
  await open({ state: lab({ groups: [group("api-management", "API Management", ["apim-gateway"], RUNNING, late)] }),
    body: dashboard({ products: [product("api-management", "API Management", { deployed: "4.13.0-alpha.4", channel: "pre-release", prerelease: "4.13.0-alpha.9", latest: "4.13.0-alpha.9", status: "newer" })] }) });
  const badge = part(products()[0], "upd").querySelector(".tip-btn");
  assert.equal(upd(products()[0]), "Apply needed");
  assert.deepEqual(tipOf(badge).slice(-2), ["Update available 4.13.0-alpha.9", "Apply restarts it with what is saved, older versions included."], "the newer version, under the causes");
  assert.ok(badge.getAttribute("aria-label").includes("Update available 4.13.0-alpha.9"), "and said to a screen reader");
  dashboardPage.hide();
});

test("the head of the card has the platform's containers and the versions as buttons: a count of the containers, the page that lists them, and the page of the versions", async () => {
  await open();
  const [containers_, versions] = block("count").querySelectorAll("a");
  assert.deepEqual([said(containers_), containers_.getAttribute("href"), containers_.getAttribute("class")], ["Containers 7", "#/containers", "btn"]);
  assert.deepEqual([text(versions), versions.getAttribute("href"), versions.getAttribute("class")], ["Change versions", "#/images", "btn"]);
  // Their words stay at every width (a card too narrow puts them under its title), with no icon that only some widths would show
  assert.deepEqual([containers_, versions].map((button) => button.getAttribute("aria-label")), ["Containers, 7", null]);
  assert.equal(block("count").querySelectorAll("svg").length, 0);
  await move(lab({ platform: { containers: { running: 1, total: 1 } } }));
  assert.equal(said(block("count").querySelector("a")), "Containers 1");
  await move(lab({ blind: true, docker: { error: "Cannot connect" }, wait: "Docker is not reachable" }));
  assert.equal(said(block("count").querySelector("a")), "Containers", "nothing is said of what runs while Docker does not answer");
  dashboardPage.hide();
});

test("the consoles of a product are those of its own services that run: a button each, the older ones in one More menu, and one Open menu of all of them for a card too narrow", async () => {
  await open();
  const full = (row) => row.querySelectorAll(".dash-full .btn");
  assert.deepEqual(full(products()[0]).map(text), ["APIM Console", "Developer Portal", "More"]);
  assert.deepEqual(full(products()[0]).map((a) => a.getAttribute("class")), ["btn", "btn", "btn"], "one kind of control, with no link among the buttons");
  assert.deepEqual(full(products()[0]).slice(0, 2).map((a) => a.getAttribute("href")), ["http://localhost:8084", "http://localhost:8085"]);
  assert.deepEqual(products()[1].querySelectorAll(".dash-full .btn").map(text), ["AM Console"], "a product with one console has it once");
  for (const a of block("products").querySelectorAll("a.btn")) {
    assert.equal(a.getAttribute("target"), "_blank");
    assert.equal(a.getAttribute("rel"), "noopener");
  }
  assert.equal(products()[0].querySelector(".dash-full .btn use").getAttribute("href"), "#i-external");
  // The legacy one (`legacy` of its link) is in the More menu, so that the row keeps its one line
  assert.equal(products()[1].querySelectorAll("[data-consoles]").length, 0, "a product with no older console has no menu");
  const more = products()[0].querySelector('[data-consoles^="older"]');
  assert.deepEqual([text(more), more.getAttribute("aria-label"), more.getAttribute("aria-haspopup"), more.getAttribute("class")], ["More", "More, consoles of API Management", "true", "btn"]);
  more.click();
  const entries = () => page.ids.menu.querySelectorAll(".menu-item").map((entry) => [text(entry), entry.getAttribute("href"), entry.getAttribute("target")]);
  assert.deepEqual(entries(), [["Classic Developer Portal", "http://localhost:8082", "_blank"]]);
  // A card too narrow for the buttons shows one Open menu with them all, the older ones after a line
  const all = products()[0].querySelector('.dash-compact [data-consoles^="all"]');
  assert.deepEqual([text(all), all.getAttribute("aria-label"), all.getAttribute("class")], ["Open", "Open, consoles of API Management", "btn"]);
  assert.deepEqual(all.querySelectorAll("use").map((use) => use.getAttribute("href")), ["#i-chevron"], "Open of a menu has its chevron, as the lists' Open of several consoles: the mark of a link out is a link's");
  all.click();
  assert.deepEqual(entries(), [["APIM Console", "http://localhost:8084", "_blank"], ["Developer Portal", "http://localhost:8085", "_blank"],
    ["Classic Developer Portal", "http://localhost:8082", "_blank"]]);
  assert.equal(page.ids.menu.querySelectorAll(".menu-sep").length, 1);
  assert.doesNotMatch(text(block("products")), /Jade|Mail server/, "a tool is on its own row of the catalog");
  assert.equal(section.querySelectorAll(".dash-consoles").length, 0, "and there is no row of consoles apart");
  // A console that does not run is not offered, and a link of a playground or an add-on is never a product's
  const own = { label: "Acme Bakery docs", url: "http://localhost:8110/docs", port: "8110", service: "bakery-web", section: "Playground", legacy: false, up: true };
  await move(lab({ links: [LINKS[0], { ...LINKS[1], up: false }, LINKS[2], own, LINKS[5]] }));
  assert.deepEqual(full(products()[0]).map(text), ["Developer Portal", "More"]);
  assert.doesNotMatch(text(block("products")), /Acme Bakery docs/);
  // Nothing runs, nothing to open
  await move(lab({ core: "stopped", links: LINKS.map((found) => ({ ...found, up: false })) }));
  assert.equal(products().flatMap((row) => row.querySelectorAll(".btn")).length, 0);
  dashboardPage.hide();
});

test("a console is where its link says, wherever that is: the one of a service of another product goes on that product's row", async () => {
  const gamma = link("Gamma Console", 8086, "apim-ui", "API Management");
  await open({ state: lab({ links: [...LINKS.slice(0, 2), gamma, ...LINKS.slice(2)] }) });
  assert.deepEqual(products()[0].querySelectorAll(".dash-full .btn").map(text), ["APIM Console", "Gamma Console", "Developer Portal", "More"]);
  dashboardPage.hide();
});

test("a pre-release is told its newer version, a release once there is one", async () => {
  await open({ body: dashboard({ products: [product("api-management", "API Management", { deployed: "4.13.0-alpha.2", channel: "pre-release", stable: "4.13.0", prerelease: null, latest: "4.13.0" })] }) });
  assert.equal(ver(products()[0]), "4.13.0-alpha.2");
  assert.equal(upd(products()[0]), "Update available");
  assert.deepEqual(tipOf(part(products()[0], "upd").querySelector(".tip-btn")), ["Update available", "4.13.0-alpha.2 4.13.0", "Choose it on the Images page."]);
  assert.equal(part(products()[0], "upd").querySelector(".dot-status").getAttribute("class"), "dot-status warn");
  assert.equal(products()[0].querySelectorAll(".list-note").length, 0);
  dashboardPage.hide();
});

test("a product that does not run has a ring and the version configured, muted, and one never created is not warned about", async () => {
  await open({ state: lab({ core: "stopped" }), body: dashboard({ products: [product("api-management", "API Management", { deployed: "4.12.1", deployed_state: "configured" })] }) });
  const row = products()[0];
  assert.equal(row.querySelector(".dot-status").getAttribute("class"), "dot-status stopped");
  assert.equal(row.querySelector(".s-version").getAttribute("class"), "s-version muted");
  assert.equal(ver(row), "4.12.1");
  assert.equal(upd(row), "Update available");
  assert.equal(section.querySelectorAll(".list-note.warn").length, 0, "the head says the platform is stopped: no row repeats it");
  await open({ state: lab({ core: "absent" }) });
  assert.ok(products().every((found) => found.querySelector(".dot-status").getAttribute("class") === "dot-status stopped" && !found.querySelector(".list-note")));
  assert.deepEqual(shown(), ["start"]);
  assert.ok(section.querySelector('[data-stack="start"]').classList.contains("primary"), "the head's Start is the one primary");
  dashboardPage.hide();
});

test("a product that has something wrong says it in place of its update: an issue in red, a license it lacks, how many of its containers run in amber; a change not applied is the badge", async () => {
  const note = { note: "Gateway exited (1)", note_tone: "err", note_title: "", note_target: null, note_file: null };
  const license = { note: "Needs a license", note_tone: "warn", note_title: "Not started without one", note_target: null, note_file: ".env" };
  const apply = { note: "Apply needed", note_tone: "warn", note_title: "", note_target: null, note_file: null };
  await open({ state: lab({ groups: [
    group("api-management", "API Management", ["apim-gateway", "apim-ui"], { key: "partly", label: "Partly running" }, { containers: { running: 2, total: 5 }, note: "Management API stopped" }),
    group("access-management", "Access Management", ["am-gateway"], { key: "issue", label: "Issue" }, note),
    group("alert-engine", "Alert Engine", ["alert-engine"], OFF, { ...license, containers: { running: 0, total: 0 } }),
    group("gamma", "Gamma", ["gamma-ui"], RUNNING, apply),
  ], extra: { "alert-engine": service("alert-engine", ABSENT, { needs_license: true, on: false, ...license }), "gamma-ui": service("gamma-ui", HEALTHY) } }) });
  assert.deepEqual(products().map((row) => part(row, "upd").querySelectorAll(".list-note").map(text)), [["2 of 5 running"], ["Gateway exited (1)"], ["Needs a license"], []]);
  assert.deepEqual(products().map((row) => part(row, "upd").querySelector(".list-note")?.getAttribute("class").trim().replace(/\s+/g, " ")),
    ["list-note warn", "list-note err", "list-note warn text-link", undefined]);
  assert.deepEqual(products().map((row) => row.querySelector(".dot-status").getAttribute("class")), ["dot-status partly", "dot-status issue", "dot-status off", "dot-status running"]);
  // The license note opens the form that takes one, from here as everywhere
  const opened = [];
  const { License } = await import("../../../web/js/dialogs/license.js");
  const show = License.show;
  License.show = (options) => opened.push(options);
  products()[2].querySelector("[data-license]").click();
  License.show = show;
  assert.deepEqual(opened, [{ form: true }]);
  dashboardPage.hide();
});

test("the update of a product that a registry is still listing is a spinner, nothing once it could not say, and the newer version when it can", async () => {
  await open({ body: dashboard({ pending: true, products: [product("api-management", "API Management", { stable: null, prerelease: null, latest: null, status: "unknown" })] }) });
  assert.equal(part(products()[0], "upd").querySelectorAll(".dash-wait").length, 1, "a spinner where the update is being asked for, not a dash");
  server.dashboard = dashboard();
  mock.timers.tick(3000);
  await flush();
  assert.equal(reads(), 2);
  assert.equal(section.querySelectorAll(".dash-wait").length, 0);
  assert.equal(upd(products()[0]), "Update available");
  // Known to be unknown: nothing, not a dash
  await open({ body: dashboard({ products: [product("api-management", "API Management", { stable: null, prerelease: null, latest: null, status: "unknown" })] }) });
  assert.equal(upd(products()[0]), "");
  assert.equal(part(products()[0], "upd").querySelectorAll(".status, .dash-wait").length, 0);
  dashboardPage.hide();
});

test("a product row acts on nothing: its name is not a link, and the card has no Start or Stop of its own", async () => {
  await open();
  products()[0].click();
  products()[0].querySelector(".list-name").click();
  await flush();
  assert.equal(server.calls.filter((call) => call.method === "POST").length, 0);
  assert.equal(block("products").querySelectorAll("a[href^='#/containers']").length, 0);
  assert.equal(products()[0].querySelectorAll("a").filter((a) => !cls(a).includes("btn") && !cls(a).includes("text-link")).length, 0, "its name is no link");
  assert.deepEqual(block("products").querySelectorAll("button:not(.tip-btn)").map((button) => button.getAttribute("data-consoles")), ["older:api-management", "all:api-management"],
    "no Start, Stop or Restart of a part: they are the Containers page's, and the buttons are the menus of the consoles");
  dashboardPage.hide();
});

test("with Docker not answering no dot is colored, no count is said, and the actions wait with the reason as their title", async () => {
  const state = lab({ blind: true, docker: { error: "Cannot connect to the Docker daemon", warning: null }, wait: "Docker is not reachable", core: "absent",
    links: LINKS.map((found) => ({ ...found, up: false })) });
  await open({ state, body: dashboard({ docker: { engine: null, compose: "5.4.0", os: null, cpus: null, memory: null, error: "Cannot connect to the Docker daemon" }, data: null }) });
  assert.deepEqual(products().map((row) => row.querySelector(".dot-status").getAttribute("class")), ["dot-status stopped", "dot-status stopped"], "what is on keeps its ring");
  assert.equal(section.querySelectorAll(".list-note").length, 0, "no row says a word of what runs");
  assert.equal(said(cloudRow()), "Gravitee Cloud No account connected Connect account");
  assert.ok(isOff(section.querySelector('[data-stack="start"]')));
  assert.equal(offWhy(section.querySelector('[data-stack="start"]')), "Docker is not reachable");
  assert.equal(products().flatMap((row) => row.querySelectorAll(".btn")).length, 0);
  dashboardPage.hide();
});

// --- Catalog -------------------------------------------------------------------------------

const catalog = () => rows("catalog").filter((row) => row.getAttribute("class").includes("dash-item"));

test("the card is named for what it holds, and what is on comes first, a playground, the setups and the add-ons, each by name, then what is starred and off, each once", async () => {
  await open({ state: lab({
    playgrounds: [running("playground", "acme-bakery", "Acme Bakery", { starred: true }), item("playground", "hotels", "Acme Hostels", { starred: true })],
    setups: [running("setup", "events", "Event Streams"), item("setup", "tracing", "Tracing")],
    addons: [running("addon", "jade", "Jade"), waiting("addon", "kafka", "Kafka"), item("addon", "mail", "Mail server", { starred: true }), item("addon", "kiosk", "Kiosk")],
  }) });
  assert.equal(text(block("catalog").parent.querySelector(".card-head h2")), "Playgrounds, setups and add-ons");
  assert.deepEqual(catalog().map((row) => text(row.querySelector("a"))), ["Acme Bakery", "Event Streams", "Jade", "Kafka", "Acme Hostels", "Mail server"]);
  assert.deepEqual(catalog().map((row) => row.querySelector(".dash-kind use").getAttribute("href")), ["#i-flask", "#i-layers", "#i-addons", "#i-addons", "#i-flask", "#i-addons"]);
  // A name leads to the page of the item: its row in its list, a playground's own page
  assert.deepEqual(catalog().map((row) => row.querySelector("a").getAttribute("href")), ["#/playgrounds/acme-bakery", "#/setups/events", "#/addons/jade", "#/addons/kafka",
    "#/playgrounds/hotels", "#/addons/mail"]);
  // Its state is one component for every state, a dot and its word: Running, Stopped, Off. What does not run is a muted word.
  assert.deepEqual(catalog().map((row) => [text(row.querySelector(".dash-sub-line .status")), row.querySelector(".dash-sub-line .dot-status").getAttribute("class"), cls(row.querySelector(".status")).includes("muted")]),
    [["Running", "dot-status running", false], ["Running", "dot-status running", false], ["Running", "dot-status running", false], ["Stopped", "dot-status stopped", true],
      ["Off", "dot-status off", true], ["Off", "dot-status off", true]]);
  assert.equal(catalog().every((row) => row.querySelectorAll(".pill").length === 0 && row.querySelectorAll(".status").length === 1), true, "no pill for some states and plain text for others");
  assert.deepEqual(catalog().map((row) => row.querySelectorAll("[data-star]").length), [1, 0, 0, 0, 1, 1], "a star says why an item that is off is there");
  dashboardPage.hide();
});

test("a row has no Open: what opens an item is its own page, which its name leads to; its consoles are on its list's row and on Containers", async () => {
  await open({ state: lab({
    playgrounds: [running("playground", "acme-bakery", "Acme Bakery", { links: [link("Acme Bakery docs", 8110, "bakery-web", "Playground")] })],
    addons: [running("addon", "jade", "Jade", { links: [LINKS[3]] })],
  }) });
  assert.equal(block("catalog").querySelectorAll(".dash-go, [data-open]").length, 0, "two buttons at most: its Start or Stop, and its Settings");
  assert.deepEqual(catalog().map((row) => row.querySelector(".name-link").getAttribute("href")), ["#/playgrounds/acme-bakery", "#/addons/jade"]);
  assert.doesNotMatch(text(block("products")), /Acme Bakery docs|Jade/);
  dashboardPage.hide();
});

test("an item's Start or Stop is its list's, on its own key, and one that waits for its settings says so, its key icon opening them", async () => {
  await open({ state: lab({ addons: [running("addon", "jade", "Jade"), item("addon", "kafka", "Kafka", { starred: true }),
    item("addon", "mail", "Mail server", { starred: true, settings: ["MAIL_PASSWORD"], action: ACTION("start", { enabled: false, reason: "Needs settings: MAIL_PASSWORD" }) }),
    item("addon", "kiosk", "Kiosk", { starred: true, action: ACTION("start", { enabled: false, reason: "Wait for Start Jade" }) })] }) });
  const buttons = catalog().map((row) => row.querySelector("button[data-act]"));
  // The lists' own button, its word on it, the same width in every row: never an icon alone for an action that says nothing else, and
  // never another action in its place
  assert.deepEqual(buttons.map((button) => [button.dataset.actFor, text(button.querySelector(".pbtn-face")), button.getAttribute("class").includes("act")]),
    [["addon:jade", "Stop", true], ["addon:kafka", "Start", true], ["addon:mail", "Start", true], ["addon:kiosk", "Start", true]]);
  assert.deepEqual(buttons.map((button) => button.querySelector("use").getAttribute("href")), ["#i-stop", "#i-play", "#i-play", "#i-play"]);
  assert.deepEqual([isOff(buttons[2]), offWhy(buttons[2])], [true, "Needs settings: MAIL_PASSWORD"], "it waits for its settings, and says which");
  const key = catalog()[2].querySelector(".dash-set [data-settings]");
  assert.equal(key.getAttribute("aria-label"), "Settings Mail server", "its settings are the key of its slot, named");
  assert.ok(isOff(buttons[3]), "one that cannot start says why");
  assert.equal(offWhy(buttons[3]), "Wait for Start Jade");
  buttons[0].click();
  await flush();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => `${call.path} ${JSON.stringify(call.body)}`), ["/api/addons/jade/stop {\"accept\":[]}"]);
  assert.equal(store.pending.get("addon:jade"), "stop");
  dashboardPage.hide();
});

test("a row stays while its button has not said how its action went, though what it started or stopped is no longer on", async () => {
  await open({ state: lab({ addons: [running("addon", "jade", "Jade")] }) });
  assert.equal(catalog().length, 1);
  store.pending.set("addon:jade", "stop");
  await move(lab({ addons: [item("addon", "jade", "Jade")] }));
  assert.equal(catalog().length, 1, "its Stop is still at work");
  store.pending.clear();
  await move(lab({ addons: [item("addon", "jade", "Jade")] }));
  assert.equal(catalog().length, 0, "off and not starred: it is gone");
  dashboardPage.hide();
});

test("under its name an item says what its list says of it: why it cannot start, that it started outside the Lab UI, what it needs, what is not applied", async () => {
  const why = "Needs Access Management 4.13 or later and API Management 4.13.0-alpha.3 or later: choose them on the Images page";
  const note = (text, more = {}) => ({ note: text, note_tone: "", note_title: "", note_target: null, note_file: null, ...more });
  await open({ state: lab({
    playgrounds: [item("playground", "bank", "Acme Bakery", { starred: true, ...note(why), action: ACTION("start", { enabled: false, reason: why }) }),
      running("playground", "api", "Acme Hostels API", note("Started by hand"))],
    setups: [],
    addons: [running("addon", "hybrid", "Hybrid gateway", note("Needs settings", { note_target: { kind: "addon", id: "hybrid" } }), { action: ACTION("stop") }),
      running("addon", "kafka", "Kafka", note("Apply needed", { note_tone: "warn", note_title: "Kafka broker: its configuration changed since it started.", note_apply: true })),
      running("addon", "jade", "Jade")],
  }) });
  // What the list says of an item is its badge after the name, as on the lists: never a line under the name. What keeps a Start from working
  // is the warning just before its Start instead, in a slot of its own, as on the lists
  assert.equal(section.querySelectorAll(".dash-note").length, 0);
  const badge = (row) => row.querySelector(".dash-marks .pill");
  assert.deepEqual(catalog().map((row) => [text(row.querySelector("a")), badge(row) ? tileSaid(badge(row)) : ""]),
    [["Acme Hostels API", "Started by hand"], ["Hybrid gateway", "Needs settings"], ["Kafka", "Apply needed"], ["Jade", ""], ["Acme Bakery", ""]]);
  // A running add-on that lacks settings is stopped from its row: its badge leads to the settings, its button is its Stop
  assert.equal(text(catalog()[1].querySelector("button[data-act] .pbtn-face")), "Stop");
  // The reason a Start cannot be used is there to read, as well as on the button that is disabled: a keyboard cannot reach that
  const bank = catalog().find((row) => text(row.querySelector("a")) === "Acme Bakery");
  assert.equal(bank.querySelectorAll(".dash-marks .pill").length, 0, "it is not said twice");
  assert.equal(text(bank.querySelector(".dash-warn .pop-title")), why);
  assert.ok(isOff(bank.querySelector("button[data-act]")));
  assert.equal(offWhy(bank.querySelector("button[data-act]")), why);
  // What is not applied is the badge of the other pages, with its reason
  const kafka = catalog().find((row) => text(row.querySelector("a")) === "Kafka");
  assert.deepEqual(tipOf(kafka.querySelector(".dash-marks .tip-btn")), ["Kafka broker: its configuration changed since it started."]);
  dashboardPage.hide();
});

test("what a screen reader hears: a product's band is a heading, the kind of a row is named, a link to open says what it opens, a star says which", async () => {
  await open({ state: lab({ playgrounds: [running("playground", "acme-bakery", "Acme Bakery", { starred: true })],
    addons: [running("addon", "jade", "Jade", { starred: true, links: [LINKS[3]] })] }) });
  assert.deepEqual(products().map((row) => [row.querySelector(".list-name").getAttribute("role"), row.querySelector(".list-name").getAttribute("aria-level")]), [["heading", "3"], ["heading", "3"]]);
  assert.deepEqual(catalog().map((row) => [row.querySelector(".dash-kind").getAttribute("role"), row.querySelector(".dash-kind").getAttribute("aria-label")]),
    [["img", "Playground"], ["img", "Add-on"]]);
  assert.deepEqual(catalog().map((row) => text(row.querySelector("button[data-act] .pbtn-face"))), ["Stop", "Stop"], "its action says its word");
  assert.deepEqual(catalog().map((row) => [row.querySelector("[data-star]").getAttribute("aria-label"), row.querySelector("[data-star]").getAttribute("aria-pressed")]),
    [["Star Acme Bakery", "true"], ["Star Jade", "true"]], "one name, pressed while starred");
  // A drawing is never read: its button or the word beside it says what it is
  assert.ok(section.querySelectorAll("svg").every((svg) => svg.getAttribute("aria-hidden") === "true"), "every icon is hidden from a screen reader");
  dashboardPage.hide();
});

test("what a screen reader hears of a row of the platform: the state of a product by name, its version named, the Cloud a heading as a product is", async () => {
  await open({ ...listed(gateway({ here: true })), state: lab({ gateway: "up" }) });
  assert.deepEqual(products().map((row) => [part(row, "name").querySelector('[role="img"]').getAttribute("aria-label")]), [["Running"], ["Running"]], "the dot says its state in words");
  assert.ok(part(products()[0], "ver").querySelector(".sr-only"), "the version is named");
  assert.equal(part(products()[0], "ver").querySelector(".sr-only").textContent, "Version ");
  assert.equal(text(cloudRow().querySelector('[role="heading"]')), "Gravitee Cloud");
  await move(lab({ core: "stopped", gateway: true }));
  assert.deepEqual(products().map((row) => part(row, "name").querySelector('[role="img"]').getAttribute("aria-label")), ["Stopped", "Stopped"]);
  await move(lab({ blind: true, docker: { error: "Cannot connect" }, wait: "Docker is not reachable" }));
  assert.equal(part(products()[0], "name").querySelector('[role="img"]').getAttribute("aria-label"), "State not known");
  dashboardPage.hide();
});

test("a state is one component wherever the page says one: a dot and its word, the word muted while it is off, and none of it underlined or a pill", async () => {
  await open({ ...listed(gateway({ here: true })), state: lab({ gateway: "up", playgrounds: [running("playground", "bank", "Acme Bakery"), item("playground", "hotels", "Acme Hostels", { starred: true })] }) });
  const states = catalog().map((row) => row.querySelector(".status"));
  assert.ok(states.length >= 3);
  for (const found of states) {
    assert.deepEqual(found.children.map((child) => cls(child).split(" ")[0]).slice(0, 2), ["dot-status", found.querySelector(".tip") ? "tip" : "status-word"]);
    assert.equal(found.querySelectorAll(".pill").length, 0);
  }
  assert.deepEqual(states.map((found) => [found.querySelector(".dot-status").getAttribute("class").split(" ").at(-1), cls(found).includes("muted")]).filter(([dot]) => ["running", "healthy", "off", "stopped"].includes(dot)).map(([dot, muted]) => [dot, muted]).slice(0, 1),
    [["running", false]], "a state that runs is said in full");
  assert.ok(states.some((found) => cls(found).includes("muted")), "and what is off is muted");
  assert.ok(states.every((found) => cls(found).includes("muted") === ["off", "stopped", ""].includes(found.querySelector(".dot-status").getAttribute("class").split(" ").at(-1))), "muted exactly while it is off");
  assert.equal(block("cloud").querySelectorAll(".tip-name").length, 0, "no word that opens a tooltip carries a mark of its own");
  dashboardPage.hide();
});

test("a star that is turned takes its row with it: the focus goes to the search, where the keyboard can go on", async () => {
  await open({ state: lab({ addons: [item("addon", "kafka", "Kafka", { starred: true })] }) });
  const search = section.querySelector('[data-filter="q"]');
  let focused = 0;
  search.focus = () => { focused += 1; };
  page.doc.activeElement = null;   // the star that had it left the page with its row
  catalog()[0].querySelector("[data-star]").click();
  dashboardPage.render(store.state);   // the store tells the page the star was turned, as it does
  await flush();
  await flush();
  assert.equal(catalog().length, 0, "off and no longer starred: it is gone");
  assert.equal(focused, 1);
  delete search.focus;
  dashboardPage.hide();
});

test("a star is turned from here, and an item that starts or stops shows it in its state, with how far it is", async () => {
  await open({ state: lab({ addons: [item("addon", "kafka", "Kafka", { starred: true }), item("addon", "jade", "Jade", { on: true, up: true, state: { key: "starting", label: "Starting", running: false }, progress: 0.4, action: ACTION("stop") })] }) });
  const [starting, starred] = catalog();
  const status = starting.querySelector(".dash-sub-line .status");
  assert.equal(text(status), "Starting");
  assert.equal(status.querySelector(".dot-status").getAttribute("class"), "dot-status starting");
  assert.equal(starting.querySelectorAll(".pill").length, 0, "the same component as every other state");
  assert.equal(status.querySelector("[data-progress]").getAttribute("data-progress"), "addon:jade");
  starred.querySelector("[data-star]").click();
  await flush();
  assert.deepEqual(server.calls.filter((call) => call.path.endsWith("/star")).map((call) => `${call.path} ${JSON.stringify(call.body)}`), ["/api/addons/kafka/star {\"starred\":false}"]);
  dashboardPage.hide();
});

test("it has the search and the filters of the lists, and no foot of totals: the kind picker says how many there are of each, Running and Starred are their toggle chips", async () => {
  await open();
  const card = block("catalog").parent;
  const search = card.querySelector(".list-tools label.search input");
  assert.deepEqual([search.getAttribute("type"), search.getAttribute("placeholder"), search.getAttribute("aria-label")], ["search", "Search…", "Search the playgrounds, setups and add-ons"]);
  const pickers = card.querySelectorAll(".list-tools .combo-btn");
  assert.deepEqual(pickers.map((picker) => text(picker.querySelector(".value"))), ["All kinds"]);
  assert.deepEqual(pickers.map(text), ["Kind: All kinds"], "named by its visible words, after what it chooses");
  assert.deepEqual(pickers.map((picker) => [text(picker), picker.getAttribute("aria-haspopup")]), [["Kind: All kinds", "dialog"]], "its popup is a dialog, not a list");
  // Options of their own, which combine: loose toggle chips, as on the lists, and no "All" repeated
  assert.equal(card.querySelectorAll(".list-tools .segmented").length, 0);
  assert.deepEqual(card.querySelectorAll(".list-tools .toggle-chip").map((chip) => [text(chip), chip.querySelector("input").getAttribute("type")]),
    [["Running", "checkbox"], ["Starred", "checkbox"]]);
  assert.equal(card.querySelectorAll("[data-shown]").length, 0, "no choice of its own: what the lists have");
  assert.equal(section.querySelectorAll(".dash-foot").length, 0, "no foot of totals");
  assert.equal(block("lists"), null);
  await pick("[data-kind]");
  assert.deepEqual(optionsOf(), [["All kinds", "", true], ["Playgrounds", "1", false], ["Setups", "2", false], ["Add-ons", "4", false]]);
  Combo.close();
  dashboardPage.hide();
});

// What the picker lists, as [name, number, chosen]
const optionsOf = () => page.doc.querySelector("#combo-list").querySelectorAll(".combo-option").map((o) => [text(o.querySelector(".name")), text(o.querySelector(".age")), o.classList.contains("selected")]);
async function pick(selector, name) {
  section.querySelector(selector).click();
  await flush();
  if (name) {
    page.doc.querySelector("#combo-list").querySelectorAll(".combo-option").find((o) => text(o.querySelector(".name")) === name).click();
    await flush();
  }
}
async function typeIn(words) {
  const search = section.querySelector('[data-filter="q"]');
  search.value = words;
  search.dispatchEvent(new Ev("input"));
  await flush();
}
const names = () => catalog().map((row) => text(row.querySelector("a")));

test("a search looks through the whole catalog as the lists' do, by name, description, category, tag and member, whatever runs or is starred", async () => {
  await open({ state: lab({ playgrounds: [item("playground", "acme-bakery", "Acme Bakery", { description: "Open banking for a retail bank", tags: ["Fraud"] })],
    setups: [item("setup", "events", "Event Streams", { category: "Messaging", members: [{ id: "kafka", name: "Kafka" }] })],
    addons: [running("addon", "jade", "Jade", { category: "Observability" }), item("addon", "kafka", "Kafka", { description: "Events with Jade in mind" }), item("addon", "mail", "Mail server")] }) });
  assert.deepEqual(names(), ["Jade"], "by default what runs and what is starred");
  await typeIn("jad");
  assert.deepEqual(names(), ["Jade", "Kafka"], "the name and the description, what runs first");
  await typeIn("  RETAIL ");
  assert.deepEqual(names(), ["Acme Bakery"], "not by case, and what is off is found");
  assert.equal(text(catalog()[0].querySelector(".dash-sub-line .status")), "Off");
  assert.ok(catalog()[0].querySelector("button[data-act]"), "and started from here");
  // What the lists look into besides: the category, the tags and the members of a setup, so that the same word finds the same things
  await typeIn("observability");
  assert.deepEqual(names(), ["Jade"], "the category");
  await typeIn("fraud");
  assert.deepEqual(names(), ["Acme Bakery"], "a tag");
  await typeIn("kafka");
  assert.deepEqual(names(), ["Event Streams", "Kafka"], "a member of a setup");
  await typeIn("");
  assert.deepEqual(names(), ["Jade"], "an empty search is the default again");
  dashboardPage.hide();
});

test("the kind picker keeps one kind, and Running and Starred narrow what is on and starred as they narrow a list", async () => {
  await open({ state: lab({
    playgrounds: [running("playground", "acme-bakery", "Acme Bakery"), item("playground", "hotels", "Acme Hostels", { starred: true })],
    setups: [item("setup", "events", "Event Streams"), waiting("setup", "tracing", "Tracing")],
    addons: [running("addon", "jade", "Jade", { starred: true }), item("addon", "kafka", "Kafka"), item("addon", "mail", "Mail server", { starred: true })],
  }) });
  const checked = () => section.querySelectorAll(".toggle-chip input").filter((box) => box.checked).map((box) => text(box.parent));
  assert.deepEqual(names(), ["Acme Bakery", "Tracing", "Jade", "Acme Hostels", "Mail server"], "what is on, then what is starred");
  assert.deepEqual(checked(), []);
  toggle("[data-running]", true);
  assert.deepEqual(names(), ["Acme Bakery", "Jade"], "what runs: a setup that waits for the platform does not");
  assert.deepEqual(checked(), ["Running"]);
  toggle("[data-starred]", true);
  assert.deepEqual(names(), ["Jade"], "what runs and is starred");
  assert.deepEqual(checked(), ["Running", "Starred"]);
  toggle("[data-running]", false);
  assert.deepEqual(names(), ["Jade", "Acme Hostels", "Mail server"], "what is starred");
  assert.equal(localStorage.getItem("list-starred:dashboard"), "1", "Starred is chosen once, as on the lists");
  await pick("[data-kind]", "Add-ons");
  assert.deepEqual(names(), ["Jade", "Mail server"]);
  assert.equal(text(section.querySelector("[data-kind] .value")), "Add-ons");
  toggle("[data-starred]", false);
  assert.equal(localStorage.getItem("list-starred:dashboard"), "");
  assert.deepEqual(names(), ["Jade", "Mail server"], "what is on and starred of the kind");
  await typeIn("e");
  assert.deepEqual(names(), ["Jade", "Kafka", "Mail server", "Hybrid gateway"], "a search in a kind looks through all of its kind");
  await pick("[data-kind]", "All kinds");
  assert.equal(catalog().length, 8, "the first page of the whole catalog");
  await typeIn("");
  dashboardPage.hide();
});

test("the filters are kept from one visit to the next, as the lists keep theirs", async () => {
  await open({ state: lab({ addons: [running("addon", "jade", "Jade"), waiting("addon", "kafka", "Kafka", { starred: true })] }) });
  await typeIn("ka");
  toggle("[data-starred]", true);
  assert.deepEqual(names(), ["Kafka"]);
  dashboardPage.hide();
  dashboardPage.show();
  await flush();
  assert.equal(section.querySelector('[data-filter="q"]').value, "ka", "what was typed is there");
  assert.equal(section.querySelector("[data-starred]").checked, true);
  assert.deepEqual(names(), ["Kafka"]);
  toggle("[data-starred]", false);
  await typeIn("");
  dashboardPage.hide();
});

test("a search or a filter that finds nothing says so in the words of the lists, and Clear filters gives the default back", async () => {
  await open();
  await typeIn("zzzz");
  assert.equal(catalog().length, 0);
  assert.equal(said(rows("catalog")[0]), "No matches Clear filters");
  assert.equal(rows("catalog").length, 1);
  rows("catalog")[0].querySelector("[data-clear]").click();
  await flush();
  assert.equal(section.querySelector('[data-filter="q"]').value, "");
  assert.deepEqual(names(), ["Jade"]);
  // What is shown alone empties it: nothing running, nothing starred
  await open({ state: lab({ addons: [waiting("addon", "kafka", "Kafka")] }) });
  toggle("[data-running]", true);
  assert.equal(said(rows("catalog")[0]), "Nothing running");
  toggle("[data-running]", false);
  toggle("[data-starred]", true);
  assert.equal(said(rows("catalog")[0]), "Nothing starred");
  await pick("[data-kind]", "Setups");
  assert.equal(said(rows("catalog")[0]), "Nothing starred", "a kind with it, and none of that kind starred");
  // With a star somewhere in the kind, the same filter finds nothing among a search's matches: no matches, and a way out
  await typeIn("zzzz");
  assert.equal(said(rows("catalog")[0]), "Nothing starred", "as a list says it: none of the kind is starred");
  await open({ state: lab({ addons: [item("addon", "kafka", "Kafka", { starred: true })] }) });
  await pick("[data-kind]", "Add-ons");
  toggle("[data-running]", true);
  assert.equal(said(rows("catalog")[0]), "Nothing running");
  await typeIn("zzzz");
  assert.equal(said(rows("catalog")[0]), "Nothing running", "as a list says it");
  await typeIn("");
  await open({ state: lab({ addons: [running("addon", "jade", "Jade"), item("addon", "kafka", "Kafka", { starred: true })] }) });
  toggle("[data-running]", true);
  toggle("[data-starred]", true);
  assert.equal(said(rows("catalog")[0]), "No matches Clear filters", "what runs and is starred: none, though some of each is");
  // Clear filters gives the default back, and the keyboard goes to the search, the button that had it being gone
  const search = section.querySelector('[data-filter="q"]');
  let focused = 0;
  search.focus = () => { focused += 1; };
  rows("catalog")[0].querySelector("[data-clear]").click();
  await flush();
  delete search.focus;
  assert.deepEqual([text(section.querySelector("[data-kind] .value")), section.querySelectorAll(".toggle-chip input").filter((box) => box.checked).length], ["All kinds", 0]);
  assert.equal(localStorage.getItem("list-starred:dashboard"), "");
  assert.equal(focused, 1);
  assert.deepEqual(names(), ["Jade", "Kafka"], "what is on and what is starred");
  dashboardPage.hide();
});

test("the rows are paged as the lists' are: eight a page, the pager at the foot only when there is more", async () => {
  const addons = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"].map((id) => running("addon", id, id.toUpperCase()));
  await open({ state: lab({ playgrounds: [running("playground", "acme-bakery", "Acme Bakery")], setups: [running("setup", "events", "Events")], addons }) });
  const pager = block("catalog").parent.querySelector(".pager");
  assert.equal(catalog().length, 8);
  assert.equal(pager.hidden, false);
  assert.equal(text(pager.querySelector("span")), "1–8 of 12");
  pager.querySelector(".next").click();
  await flush();
  assert.deepEqual(names(), ["G", "H", "I", "J"], "the next page");
  assert.equal(text(pager.querySelector("span")), "9–12 of 12");
  // A search starts from the first page again, and looks through all of the catalog
  await typeIn("demo");
  assert.equal(text(pager.querySelector("span")), "1–8 of 13");
  // Eight is not more than eight
  await open({ state: lab({ addons: ["a", "b", "c", "d", "e", "f", "g", "h"].map((id) => running("addon", id, id.toUpperCase())) }) });
  assert.equal(catalog().length, 8);
  assert.equal(block("catalog").parent.querySelector(".pager").hidden, true);
  dashboardPage.hide();
});

test("nothing on and nothing starred says so, and offers the playgrounds when there are any", async () => {
  const none = { addons: [item("addon", "kafka", "Kafka")], playgrounds: [item("playground", "acme-bakery", "Acme Bakery")] };
  await open({ state: lab(none) });
  assert.equal(rows("catalog").length, 1);
  assert.equal(said(rows("catalog")[0]), "Nothing running or starred Browse playgrounds");
  const browse = rows("catalog")[0].querySelector("a");
  assert.deepEqual([browse.getAttribute("class"), browse.getAttribute("href")], ["btn", "#/playgrounds"]);
  await open({ state: lab({ ...none, playgrounds: [] }) });
  assert.equal(said(rows("catalog")[0]), "Nothing running or starred", "no playground, nowhere to send");
  // Docker does not answer: what is on is still the files'
  await open({ state: lab({ blind: true, docker: { error: "x" }, wait: "Docker is not reachable", addons: [waiting("addon", "kafka", "Kafka")] }) });
  assert.deepEqual(catalog().map((row) => [text(row.querySelector("a")), row.querySelectorAll(".dash-sub-line .status").length]), [["Kafka", 0]], "on, and nothing said of whether it runs");
  // The core file does not load: what is on is not known
  await open({ state: lab({ blind: true, docker: { error: null }, wait: "docker-compose.yml does not load", addons: [waiting("addon", "kafka", "Kafka")] }) });
  assert.equal(said(rows("catalog")[0]), "docker-compose.yml does not load");
  dashboardPage.hide();
});

test("it is drawn again row by row: what did not change keeps its element, with its hover and its focus", async () => {
  await open({ state: lab({ addons: [running("addon", "jade", "Jade"), waiting("addon", "kafka", "Kafka", { starred: true })] }) });
  const [jade, kafka] = catalog(), product_ = rows("products")[0];
  assert.equal(text(kafka.querySelector(".dash-sub-line .status")), "Stopped");
  await move(lab({ addons: [running("addon", "jade", "Jade"), item("addon", "kafka", "Kafka", { starred: true })] }));
  assert.ok(catalog()[0] === jade && rows("products")[0] === product_, "the rows that said the same are the same elements");
  assert.ok(catalog()[1] === kafka, "the element of a row that changed is kept, its content written");
  assert.equal(text(kafka.querySelector(".dash-sub-line .status")), "Off");
  dashboardPage.hide();
});

// --- Gravitee Cloud ---------------------------------------------------------------------------
// One row of the platform's card, on the grid of the products: its name, the accounts connected (muted), how many hybrid gateways they
// have, which leads to the Gravitee Cloud page and lists them on hover and focus. Every state of the Cloud is that row, its action at its
// end, so that the card is always the same.

const withToken = (name, state = "valid") => ({ ...environment(name), token: { state } });
const listed = (...gateways) => ({ listed: [PLATFORM], checks: { "cloud-eu": check(gateways) } });
// The count of the row, and the card its bubble shows on hover and focus
const count = () => cloudRow().querySelector(".dash-count");
const card = () => JSON.parse(count().getAttribute("data-tip-card"));
// The gateways the card lists, as [where, name, state]
const listedIn = () => card().rows.flatMap((row) => row.lines.map(([who, state]) => [row.subject, who.who, state]));

test("with no platform registered the Cloud is one row of the platform's card that says none is connected, and a button to connect one; only the platforms are asked for", async () => {
  await open();
  assert.deepEqual(requests().filter((call) => call.includes("platforms")), ["GET /api/platforms"], "no check without a platform");
  assert.ok(block("cloud").parent === block("products").parent, "a row of the platform's card, under its products");
  assert.equal(section.querySelectorAll(".dash-pair > .dash-card").length, 2, "no card of its own");
  assert.equal(said(cloudRow()), "Gravitee Cloud No account connected Connect account");
  assert.deepEqual(columns(cloudRow()), columns(products()[0]), "the cells of a product, so that the card is one table");
  assert.equal(cloudRow().querySelector('[role="heading"]').getAttribute("aria-level"), "3", "a heading, as a product's name is");
  assert.equal(part(cloudRow(), "upd").querySelector(".muted").textContent, "No account connected", "said quietly");
  const connect = cloudRow().querySelector("button");
  assert.equal(connect.getAttribute("class"), "btn", "a plain button: attaching an account is rare, nothing on the page shouts for it");
  assert.equal(text(connect), "Connect account", "as the Gravitee Cloud page names it");
  assert.equal(connect.querySelector("use").getAttribute("href"), "#i-plus");
  assert.deepEqual(keys(), ["product:api-management", "product:access-management"], "the products are what they were");
  // The one primary is the head's Start while the platform is stopped
  await move(lab({ core: "stopped" }));
  assert.equal(cloudRow().querySelector("button").getAttribute("class").trim(), "btn");
  assert.equal(section.querySelectorAll(".primary").length, 1, "one primary on the page");
  // A hybrid gateway that runs here is said, so that the row never says Gravitee Cloud is not connected while one is syncing from it
  await open({ state: lab({ gateway: "up" }) });
  assert.equal(said(cloudRow()), "Gravitee Cloud A hybrid gateway runs here, with no account connected Connect account");
  await open({ state: lab({ gateway: "up", blind: true, docker: { error: "x" } }) });
  assert.equal(said(part(cloudRow(), "upd")), "No account connected", "nothing is said of what runs while Docker does not answer");
  dashboardPage.hide();
});

test("Connect account opens the wizard of the Gravitee Cloud page; the row shows the account once it is registered, behind its second step", async () => {
  await open();
  cloudRow().querySelector("[data-connect]").click();
  const wizard = form.closest("dialog"), step2 = page.doc.querySelector("#use-token").closest("form");
  assert.equal(text(wizard.querySelector("h2")), "Connect Gravitee Cloud");
  assert.ok(!form.hidden && step2.hidden, "on its first step");
  page.doc.querySelector("#platform-token").value = "a-token";
  page.doc.querySelector("#platform-account").value = PLATFORM.account.id;
  form.dispatchEvent(new Ev("input"));
  server.checks = { "cloud-eu": check([]) };
  form.dispatchEvent(new Ev("submit"));
  await flush();
  mock.timers.tick(600);
  await flush();
  assert.ok(form.parent.hasAttribute("open") && form.hidden && !step2.hidden, "registered: the same dialog goes on to the environment");
  assert.equal(server.calls.filter((call) => call.method === "POST" && call.path === "/api/platforms").length, 1);
  assert.equal(said(cloudRow()), "Gravitee Cloud No gateway", "the row behind it reads the platforms again: none has a gateway");
  assert.equal(block("cloud").querySelectorAll("[data-connect]").length, 0);
  step2.querySelector("[data-cancel]").click();
  assert.ok(!wizard.hasAttribute("open"), "Skip for now closes it, and the account stays");
  assert.equal(said(cloudRow()), "Gravitee Cloud No gateway");
  dashboardPage.hide();
});

// --- Where API Management runs -------------------------------------------------------------------
// One setting of this machine (state.core), whose group of the platform the server names (`group`): a segmented control of two icons on
// that product's row, after its name. With Gravitee Cloud chosen, the row is its environment, its services of this machine are a muted
// row under it, the other products are as they are, and Start all and Stop all follow it.

const GROUP = { id: "api-management", name: "API Management" };
const MACHINE_CHOSEN = { place: "machine", chosen: true, group: GROUP };
const CLOUD_CORE = { place: "cloud-eu/env-development", chosen: true, name: "Development (Gravitee Cloud)", platform: { id: "cloud-eu", name: "Gravitee Cloud" },
  environment: { id: "env-development", name: "Development" }, group: GROUP,
  start: { needed: true, enabled: true, reason: null }, stop: { needed: false, enabled: true, reason: null } };
const coreButtons = () => block("products").querySelectorAll("[data-core]");

test("API Management's row says where it runs, after its name: This machine or Gravitee Cloud, one choice of two icons; Gravitee Cloud with no account opens Connect Gravitee Cloud", async () => {
  await open({ state: { ...lab(), core: MACHINE_CHOSEN } });
  assert.equal(section.querySelectorAll(".card-head [data-core]").length, 0, "nothing in the head of the card");
  const [apim, am] = products();
  const group = part(apim, "name").querySelector('[role="radiogroup"]');
  assert.equal(group.getAttribute("aria-label"), "Where API Management runs");
  assert.ok(group.classList.contains("segmented"), "the segmented control of a choice of one among several");
  assert.equal(part(am, "name").querySelectorAll("[data-core]").length, 0, "only the product that can run in the Cloud has it");
  assert.equal(text(part(apim, "name")), "API Management", "its icons have no words: their names are for a screen reader and the tooltip");
  assert.deepEqual(coreButtons().map((b) => [b.getAttribute("aria-label"), b.getAttribute("role"), b.getAttribute("aria-checked")]),
    [["This machine", "radio", "true"], ["Gravitee Cloud", "radio", "false"]]);
  assert.deepEqual(coreButtons().map((b) => JSON.parse(b.getAttribute("data-tip-card")).title), ["This machine", "Gravitee Cloud"], "each says what it changes");
  assert.match(JSON.parse(coreButtons()[1].getAttribute("data-tip-card")).text, /Start all starts its hybrid gateway and the rest of the platform here, not the local API Management/);
  assert.equal(ver(apim), "4.12.20", "on this machine, its version");
  coreButtons()[1].click();
  await flush();
  const wizard = form.closest("dialog");
  assert.ok(wizard.hasAttribute("open"), "no account: the wizard that connects one");
  assert.equal(text(wizard.querySelector("h2")), "Connect Gravitee Cloud");
  assert.equal(server.calls.filter((call) => call.path === "/api/core").length, 0, "nothing is kept before an environment is linked");
  wizard.querySelector("[data-cancel]").click();
  assert.deepEqual(coreButtons().map((b) => b.getAttribute("aria-checked")), ["true", "false"], "never a silent change: it still runs on this machine");
  dashboardPage.hide();
  store.state = lab();
});

test("with API Management in the Cloud, its row is its environment and its gateway here, its services of this machine are not used, the other products are as they are, and Start all follows it", async () => {
  const state = { ...lab({ core: "stopped" }), core: CLOUD_CORE };
  await open({ state, listed: [PLATFORM], checks: { "cloud-eu": check([gateway({ here: true })]) } });
  await flush();
  assert.deepEqual(coreButtons().map((b) => b.getAttribute("aria-checked")), ["false", "true"]);
  assert.deepEqual(keys(), ["product:api-management", "local:api-management", "product:access-management"],
    "its row, the row of its services of this machine under it, then the other products");
  const [cloud, local, other] = products();
  assert.equal(said(part(cloud, "ver")), "Gravitee Cloud Development", "where its version would be: the environment it runs in");
  assert.equal(upd(cloud).replace(/^Gateway ?/, ""), "Stopped here", "the state of its hybrid gateway here, in the words of the Gravitee Cloud page");
  assert.ok(cls(local).includes("dash-idle"), "its services of this machine are muted");
  assert.equal(said(part(local, "name")), "This machine , API Management");
  assert.equal(upd(local), "Not used");
  assert.ok(!cls(other).includes("dash-idle"), "a product that stays on this machine is as it was");
  assert.equal(ver(other), "4.13.0-alpha.7");
  assert.deepEqual(shown(), ["start"], "nothing of what it starts runs: Start all");
  const start = section.querySelector('[data-lean] [data-stack="start"]');
  assert.ok(start.classList.contains("primary"));
  server.plan = { needed: true, dialog: { starts: [{ kind: "addon", id: "hybrid", name: "Hybrid gateway" }, { kind: "group", id: "access-management", name: "Access Management" }],
    serves: [{ kind: "environment", id: CLOUD_CORE.place, name: CLOUD_CORE.name }], not_started: [{ kind: "group", id: "api-management", name: "API Management" }],
    hints: { not_started: "API Management runs in Gravitee Cloud" } } };
  start.click();
  await flush();
  assert.ok(requests().includes("GET /api/core/plan"), "its plan is asked of the platform at the click");
  assert.equal(said(picks), "Starts Hybrid gateway Add-on Access Management Platform Serves Development (Gravitee Cloud) Not started API Management API Management runs in Gravitee Cloud",
    "what it starts, the environment it serves, what it does not start and why, in the server's words");
  await answer(true);
  delete server.plan;
  const sent = server.calls.find((call) => call.path === "/api/stack/start");
  assert.deepEqual(sent.body, { core: true, accept: [] }, "the Start all that follows where API Management runs");
  dashboardPage.hide();
  store.state = lab();
});

test("API Management in the Cloud that cannot start says why on Start all, and This machine is kept at once", async () => {
  const state = { ...lab({ core: "stopped" }), core: { ...CLOUD_CORE, start: { needed: true, enabled: false, reason: "Set up a gateway in Development (Gravitee Cloud) first" } } };
  await open({ state, listed: [PLATFORM], checks: { "cloud-eu": check([]) } });
  await flush();
  const start = section.querySelector('[data-lean] [data-stack="start"]');
  assert.ok(isOff(start), "it cannot be used");
  assert.equal(offWhy(start), "Set up a gateway in Development (Gravitee Cloud) first");
  assert.equal(said(part(products()[0], "open")), "Set up", "the row offers what its environment lacks: here its Set up");
  coreButtons()[0].click();
  await flush();
  assert.deepEqual(server.calls.find((call) => call.path === "/api/core").body, { place: "machine" });
  dashboardPage.hide();
  store.state = lab();
});

test("the row says how many hybrid gateways the account has, which leads to the Gravitee Cloud page and lists them, with their state, on hover and focus", async () => {
  const named = { ...PLATFORM, name: "Acme Cloud" };
  await open({ state: lab({ gateway: "up" }), listed: [named], checks: { "cloud-eu": check([
    gateway({ id: "g2", name: "prod", environmentId: "env-production", connection: "NOT_CONNECTED" }),
    gateway({ id: "g3", name: "other", environmentId: "env-development", kept: false }),
    gateway({ id: "g1", name: "dev", environmentId: "env-development", here: true }),
    gateway({ id: "h1", name: "hosted", environmentId: "env-test", type: "MANAGED" })]) } });
  assert.equal(said(cloudRow()), "Gravitee Cloud Acme Cloud 3 gateways", "the account muted, then the count: a hosted gateway is none of the Lab's");
  assert.deepEqual(columns(cloudRow()), ["dash-name", "dash-ver", "dash-upd", "dash-open"], "on the columns of the products");
  assert.equal(cloudRow().querySelector(".dash-account").getAttribute("class"), "dash-account");
  assert.deepEqual([count().tag, count().getAttribute("href"), count().getAttribute("class")], ["a", "#/cloud", "name-link dash-count"], "a name that leads to the Gravitee Cloud page");
  assert.equal(block("cloud").querySelectorAll("button, .btn").length, 0, "nothing else to click: the actions are the Gravitee Cloud page's");
  assert.equal(block("cloud").querySelectorAll(".s-version, .dash-update").length, 0, "the Cloud updates itself: no version, no update");
  // Its popover, the one bubble of the page: the gateways under their environment, the one that runs here first, each with its state
  assert.equal(card().title, "Hybrid gateways");
  assert.deepEqual(listedIn(), [["Development", "dev", "Running here"], ["Development", "other", "Connected elsewhere"], ["Production", "prod", "Stopped here"]]);
  assert.match(count().getAttribute("data-tip"), /^Hybrid gateways\. Development: dev Running here\. other Connected elsewhere\. Production: prod Stopped here$/, "what a screen reader hears of it");
  // One is singular; none is said quietly, and has nothing to list
  await open({ ...listed(gateway({ here: true })), state: lab({ gateway: "up" }) });
  assert.equal(said(cloudRow()), "Gravitee Cloud 1 gateway", "an account named as its kind is not named twice");
  await open({ ...listed() });
  assert.equal(said(cloudRow()), "Gravitee Cloud No gateway");
  assert.equal(count().getAttribute("data-tip"), null);
  dashboardPage.hide();
});

test("the Dashboard and the Gravitee Cloud page say the same words of a gateway: the row's popover lists the page's list", async () => {
  const state = lab({ gateway: "up" });
  const checks = { "cloud-eu": check([gateway({ id: "g1", environmentId: "env-development", here: true, connection: "NOT_CONNECTED" }),
    gateway({ id: "g2", name: "prod", environmentId: "env-production", kept: false, connection: "NOT_CONNECTED" }), gateway({ id: "g3", name: "test", environmentId: "env-test", status: "PENDING" })],
  { environments: [withToken("Development"), withToken("Production", "valid"), withToken("Test", "expired")] }) };
  await open({ state, listed: [PLATFORM], checks });
  const mine = listedIn().map(([where, name, word]) => [name, where, word]);
  const { platformsPage } = await import("../../../web/js/pages/platforms.js");
  const other = page.doc.createElement("section");
  page.doc.body.append(other);
  platformsPage.init(other);
  platformsPage.show([]);
  await flush();
  const theirs = other.querySelectorAll(".plat-gw-row").map((row) => [text(row.querySelector(".plat-env")), text(row.querySelector(".plat-gw-env .plat-gw-text")),
    text(row.querySelector(".plat-gw-state .status-word"))]);
  platformsPage.hide();
  assert.deepEqual(mine, theirs, "the same gateways in the same order and the same words");
  assert.deepEqual(mine.map((row) => row[2]), ["Connecting", "Not connected", "Pending"], "the one that runs here first, then the others in the order of the environments");
  dashboardPage.hide();
});

test("an account that cannot be read says its state as the Gravitee Cloud page does, and why, in the row: a token that is refused is replaced, an account that does not answer is asked again", async () => {
  await open({ listed: [PLATFORM], checks: { "cloud-eu": { state: "invalid", message: "The token was refused", environments: [], gateways: [] } } });
  assert.equal(rows("cloud").length, 1);
  assert.equal(said(cloudRow()), "Gravitee Cloud Account Token refused Replace Account Token", "said once: the server's sentence is the popover of the state");
  assert.deepEqual(tipOf(cloudRow().querySelector(".status .tip-btn")), ["Account Token refused", "The token was refused"]);
  assert.equal(cloudRow().querySelectorAll(".pill").length, 0, "the one state component, not a pill");
  assert.equal(cloudRow().querySelector(".status").getAttribute("class"), "status err", "in red");
  assert.equal(cloudRow().querySelector(".dot-status").getAttribute("class"), "dot-status issue");
  assert.equal(cloudRow().querySelectorAll("[data-recheck]").length, 0, "asking again would not change a token that is refused");
  assert.equal(cloudRow().querySelector("[data-account]").getAttribute("class"), "btn", "it opens the form of the Gravitee Cloud page that replaces it");
  // An account that does not answer: Check again asks again, and the row says it is checking meanwhile
  await open({ listed: [PLATFORM], checks: { "cloud-eu": { state: "unreachable", message: "The Cloud does not answer", environments: [], gateways: [] } } });
  assert.equal(said(cloudRow()), "Gravitee Cloud Unreachable Check again");
  assert.equal(cloudRow().querySelector(".status").getAttribute("class"), "status warn");
  server.checks = { "cloud-eu": check([gateway()], { environments: [environment("Development")] }) };
  server.calls = [];
  cloudRow().querySelector("[data-recheck]").click();
  assert.equal(said(cloudRow()), "Gravitee Cloud Unreachable Checking");
  await flush();
  assert.deepEqual(requests(), ["POST /api/platforms/cloud-eu/check"]);
  assert.equal(said(cloudRow()), "Gravitee Cloud 1 gateway");
  // A check that fails altogether is unreachable; with several accounts, the popover names the one
  await open({ listed: [PLATFORM], checks: { "cloud-eu": new Error("Lab UI offline") } });
  assert.equal(said(cloudRow()), "Gravitee Cloud Unreachable Check again");
  const other = { ...PLATFORM, id: "cloud-lab", name: "Client Lab" };
  await open({ listed: [PLATFORM, other], checks: { "cloud-eu": check([gateway()]), "cloud-lab": { state: "unreachable", message: "No answer", environments: [], gateways: [] } } });
  assert.equal(said(cloudRow()), "Gravitee Cloud Client Lab Unreachable Check again");
  assert.deepEqual(tipOf(cloudRow().querySelector(".status .tip-btn")), ["Unreachable", "Client Lab: No answer"]);
  assert.equal(cloudRow().querySelector("[data-recheck]").dataset.recheck, "cloud-lab");
  dashboardPage.hide();
});

test("the platforms are read in parallel, none holds up the rest, and the row says it is loading until each is read", async () => {
  const slowOne = { ...PLATFORM, id: "cloud-slow", name: "Slow Cloud" }, other = { ...PLATFORM, id: "cloud-lab", name: "Client Lab" };
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  await open({ listed: [slowOne, other], checks: { "cloud-slow": () => slow, "cloud-lab": check([gateway()]) } });
  assert.deepEqual(requests().filter((call) => call.includes("platforms")), ["GET /api/platforms", "POST /api/platforms/cloud-slow/check", "POST /api/platforms/cloud-lab/check"]);
  assert.equal(ver(products()[0]), "4.12.20", "the rest is drawn while the Cloud is read");
  assert.equal(said(cloudRow()), "Gravitee Cloud Slow Cloud, Client Lab Loading", "the accounts are known, their gateways not all");
  release(check([gateway({ id: "g9", environmentId: "env-test" })]));
  await flush();
  assert.equal(said(cloudRow()), "Gravitee Cloud Slow Cloud, Client Lab 2 gateways");
  assert.deepEqual(listedIn().map(([where]) => where), ["Test (Slow Cloud)", "Development (Client Lab)"], "with several accounts an environment is named with its account");
  dashboardPage.hide();
});

test("what the Gravitee Cloud page read or changed is what the Dashboard says when it is seen again: one record of the checks for both", async () => {
  await open({ state: lab({ gateway: "up" }), listed: [PLATFORM], checks: { "cloud-eu": check([gateway({ here: true })]) } });
  assert.equal(said(cloudRow()), "Gravitee Cloud 1 gateway");
  // The Cloud page creates a gateway and reads the account again: its answer is the store's
  dashboardPage.hide();
  store.cloud.checks.set("cloud-eu", check([gateway({ here: true }), gateway({ id: "g2", environmentId: "env-test" })]));
  store.cloud.at.set("cloud-eu", Date.now());
  server.calls = [];
  dashboardPage.show();
  await flush();
  assert.equal(said(cloudRow()), "Gravitee Cloud 2 gateways", "the Dashboard says what the page read, at once");
  assert.equal(requests().filter((call) => call.endsWith("/check")).length, 0, "and asks nothing: it was read a moment ago");
  dashboardPage.hide();
});

test("the Cloud of a tab that is seen again is read again only when its last answer is old, and the platforms are always listed", async () => {
  await open({ ...listed(gateway({ here: true })) });
  const asks = () => requests().filter((call) => call.endsWith("/check")).length;
  assert.equal(asks(), 1);
  page.hide();
  page.fire("document", "visibilitychange");
  clock += 10000;
  page.show();
  page.fire("document", "visibilitychange");
  await flush();
  assert.equal(asks(), 1, "10 seconds old: left alone");
  assert.equal(requests().filter((call) => call === "GET /api/platforms").length, 2);
  page.hide();
  page.fire("document", "visibilitychange");
  clock += 40000;
  page.show();
  page.fire("document", "visibilitychange");
  await flush();
  assert.equal(asks(), 2, "40 seconds old: read again");
  dashboardPage.hide();
});

test("a trip through the menu leaves a Cloud read less than 30 seconds ago alone, and a later visit reads it again", async () => {
  await open({ ...listed(gateway({ here: true })) });
  const asks = () => requests().filter((call) => call.endsWith("/check")).length;
  assert.equal(asks(), 1);
  dashboardPage.hide();
  clock += 10000;
  dashboardPage.show();
  await flush();
  assert.equal(asks(), 1, "10 seconds old: left alone");
  assert.equal(requests().filter((call) => call === "GET /api/platforms").length, 2, "the accounts are listed at every visit");
  dashboardPage.hide();
  clock += 40000;
  dashboardPage.show();
  await flush();
  assert.equal(asks(), 2, "40 seconds old: read again");
  dashboardPage.hide();
});

test("a gateway that runs here and has not connected is read again, each time twice as late, until it has, and no longer once the page is left", async () => {
  mock.timers.reset();   // what the tests before it left waiting is no concern of its clock
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let answered = check([gateway({ here: true, connection: "NOT_CONNECTED" })]);
  const up = lab({ gateway: "up" });
  await open({ state: up, listed: [PLATFORM], checks: { "cloud-eu": () => answered } });
  const checks = () => requests().filter((call) => call.endsWith("/check")).length;
  assert.equal(checks(), 1);
  mock.timers.tick(4900);
  await flush();
  assert.equal(checks(), 1);
  mock.timers.tick(200);
  await flush();
  assert.equal(checks(), 2, "after 5 seconds");
  await flush();   // its answer is in, and the next look is planned
  mock.timers.tick(9900);
  await flush();
  assert.equal(checks(), 2);
  answered = check([gateway({ here: true })]);
  mock.timers.tick(200);
  await flush();
  assert.equal(checks(), 3, "10 seconds later");
  assert.deepEqual(listedIn(), [["Development", "lab", "Running here"]]);
  mock.timers.tick(600000);
  await flush();
  assert.equal(checks(), 3, "connected: nothing asks again");
  // One that is not connected and runs elsewhere is not waited for
  answered = check([gateway({ connection: "NOT_CONNECTED" })]);
  await open({ state: up, listed: [PLATFORM], checks: { "cloud-eu": () => answered } });
  mock.timers.tick(600000);
  await flush();
  assert.equal(checks(), 1);
  // One that never connects (a token that is not right) is not asked for for ever: five more looks, 5, 10, 20, 40 and 80 seconds
  // apart, then it is left as it is
  answered = check([gateway({ here: true, connection: "NOT_CONNECTED" })]);
  await open({ state: up, listed: [PLATFORM], checks: { "cloud-eu": () => answered } });
  for (const wait of [5, 10, 20, 40, 80]) {
    mock.timers.tick(wait * 1000 - 100);
    await flush();
    const before = checks();
    mock.timers.tick(200);
    await flush();
    assert.equal(checks(), before + 1, `after ${wait} seconds more`);
  }
  assert.equal(checks(), 6);
  mock.timers.tick(3600000);
  await flush();
  assert.equal(checks(), 6, "left as it is: Connecting");
  assert.deepEqual(listedIn(), [["Development", "lab", "Connecting"]]);
  // Leaving the page stops the wait
  await open({ state: up, listed: [PLATFORM], checks: { "cloud-eu": () => answered } });
  dashboardPage.hide();
  mock.timers.tick(600000);
  await flush();
  assert.equal(checks(), 1);
  // Seen again within the half minute a check stays new (a trip through the menu): nothing is asked at once, and the gateway that has
  // not connected is looked at again all the same, as on the first visit
  await open({ state: up, listed: [PLATFORM], checks: { "cloud-eu": () => answered } });
  const first = checks();
  dashboardPage.hide();
  dashboardPage.show();
  await flush();
  assert.equal(checks(), first, "a check of a moment ago is not asked again");
  mock.timers.tick(5100);
  await flush();
  assert.equal(checks(), first + 1, "the wait is armed again");
  dashboardPage.hide();
});

// --- Resources -------------------------------------------------------------------------------

// What a person would hover to read the numbers: every word the page draws for a pointer or a reader besides its text
const unseen = () => [...section.querySelectorAll("[title], [data-tip], [aria-label], [role=tooltip]")].map((el) => `${el.getAttribute("title") || ""} ${el.getAttribute("data-tip") || ""} ${el.getAttribute("aria-label") || ""} ${el.getAttribute("role") === "tooltip" ? text(el) : ""}`).join(" ");

test("Resources is one line by default: its name and its figures in words, no chart, and a toggle that unfolds it, kept in this browser", async () => {
  globalThis.localStorage.removeItem("dash-resources");
  await open();
  const card = section.querySelector(".dash-resources"), toggle_ = card.querySelector("[data-res-toggle]");
  assert.ok(!card.classList.contains("open"), "folded by default");
  assert.equal(said(card.querySelector("h2")), "Resources", "a heading, its toggle in it");
  assert.deepEqual([toggle_.tag, toggle_.getAttribute("aria-expanded"), toggle_.getAttribute("aria-controls"), text(toggle_)], ["button", "false", "dash-res-body", "Resources"]);
  assert.ok(card.querySelector("#dash-res-body"), "what it controls");
  assert.equal(said(block("summary")), "CPU — Memory — Docker 29.7.2 Compose 5.4.0", "the load and the memory in words, then Docker's versions: what Docker stores is the strip's");
  assert.equal(block("summary").querySelectorAll(".dash-chart, .dash-bar, button, a").length, 0, "no chart, no bar, nothing to click but the line");
  assert.equal(block("summary").querySelector(".dash-res-docker").getAttribute("class"), "dash-res-docker muted");
  // The figures of the line are the strip's: one source, the samples of the stream
  stream().send({ cpu_percent: 1, memory_used: 5 * 2 ** 30, containers: 3, at: 5 });
  assert.deepEqual(section.querySelectorAll('[data-value="cpu"]').map(text), ["1.0%", "1.0%"]);
  assert.equal(said(block("summary")), "CPU 1.0% Memory 5.0 GB Docker 29.7.2 Compose 5.4.0");
  // A click anywhere on the line unfolds it, and again folds it; the choice is kept for the next visit
  block("summary").click();
  assert.ok(card.classList.contains("open"));
  assert.equal(toggle_.getAttribute("aria-expanded"), "true");
  assert.equal(globalThis.localStorage.getItem("dash-resources"), "1");
  dashboardPage.hide();
  dashboardPage.show();
  assert.ok(card.classList.contains("open"), "kept");
  toggle_.click();
  assert.ok(!card.classList.contains("open"));
  assert.equal(globalThis.localStorage.getItem("dash-resources"), "");
  dashboardPage.hide();
});

test("Resources unfolded is one row: the load, the memory, what Docker stores, and the versions of Docker, each a section", async () => {
  await open({ unfolded: true });
  const card = section.querySelector(".dash-resources");
  assert.equal(card.querySelector(".card-head"), null, "no heading band: its line");
  assert.deepEqual(elements(card.querySelector(".dash-res-cells")).map((child) => cls(child).split(" ")[0]), ["dash-cell", "dash-cell", "dash-cell", "dash-cell"], "all on one row, in this order: a section for each");
  assert.equal(text(block("engine")), "Docker 29.7.2 Compose 5.4.0");
  assert.ok(block("engine").classList.contains("dash-engine"));
  assert.equal(cells().length, 4, "the free space of the disk is no section of its own; Docker's versions are the last one");
  assert.deepEqual(cells().map(tileSaid), ["CPU — of 6 cores", "Memory — of 7.7 GB", "Storage 85.7 GB in Docker 57.9 GB unused", "Docker 29.7.2 Compose 5.4.0"], "no caption under a figure, no legend, no label of a tooltip");
  assert.deepEqual(cells().slice(0, 2).map((cell) => cell.querySelectorAll(".dash-chart").length), [1, 1], "each load beside a small live chart");
  assert.equal(offWhy(cells()[2].querySelector("[data-refresh]")).startsWith("Count again, counted"), true, "named by what it does, and how old the count is");
  assert.equal(cells()[2].querySelector("[data-refresh]").getAttribute("aria-label"), "Count storage again");
  assert.equal(cells().map((cell) => cell.querySelectorAll("a").length).join(), "0,0,0,0", "nothing here leads elsewhere");
  assert.equal(section.querySelectorAll(".dash-legend, .dash-key, .dash-disk, .dash-tile").length, 0, "no key under the bar and no line of the disk: the parts tell");
  assert.doesNotMatch(unseen() + said(section), /reclaim|\bMachine\b|GB free/, "the word Docker uses, and the old name of the card, are in no word of it");
  dashboardPage.hide();
});

test("CPU and memory say in their figure what it is a share of, and their label opens the containers that weigh most", async () => {
  await open();
  assert.deepEqual([block("cpu"), block("memory")].map((cell) => text(cell.querySelector(".dash-of"))), ["of 6 cores", "of 7.7 GB"], "the cores and the memory Docker has");
  assert.deepEqual([block("cpu"), block("memory")].map((cell) => cell.querySelector(".dash-of").getAttribute("class")), ["dash-of muted", "dash-of muted"]);
  stream().send({ cpu_percent: 4.1, memory_used: 4.9 * 2 ** 30, containers: 12, at: 5 });
  assert.deepEqual(cells().slice(0, 2).map(tileSaid), ["CPU 4.1% of 6 cores", "Memory 4.9 GB of 7.7 GB"]);
  // A Docker that does not say how many cores or how much memory it has: the machine's own
  await open({ body: dashboard({ docker: { engine: "29.7.2", compose: "5.4.0", os: null, cpus: null, memory: null } }) });
  assert.deepEqual(cells().slice(0, 2).map(tileSaid), ["CPU — of 11 cores", "Memory — of 36 GB"]);
  dashboardPage.hide();
});

// The popover of a label: what it says, one line for each container, and the memory's what is left of Docker's
const weigh = (id) => tipOf(block(id).querySelector(".dash-weigh .tip-btn"));
const sample = (more) => ({ cpu_percent: 4.1, memory_used: 4.9 * 2 ** 30, containers: 3, at: 5, ...more });

test("the label of the load names the containers that weigh most, the heaviest first, and what is left of Docker's memory", async () => {
  await open();
  assert.equal(weigh("memory")[0], "No load", "nothing is claimed before a sample");
  stream().send(sample({ top: [{ name: "gio-redis", cpu: 0.1, memory: 100 * 2 ** 20 }, { name: "gio-elasticsearch", cpu: 2, memory: 1.3 * 2 ** 30 }, { name: "gio-gateway", cpu: 9, memory: 800 * 2 ** 20 }] }));
  assert.deepEqual(weigh("memory"), ["Heaviest containers", "gio-elasticsearch 1.3 GB", "gio-gateway 800 MB", "gio-redis 100 MB", "2.8 GB left in Docker"]);
  assert.deepEqual(weigh("cpu"), ["Heaviest containers", "gio-gateway 9.0%", "gio-elasticsearch 2.0%", "gio-redis 0.1%"], "the CPU's by what each uses of the cores");
  assert.equal(block("memory").querySelector(".dash-weigh .tip-btn").tag, "button", "a stop of the keyboard");
  stream().send(sample({ top: [] }));
  assert.equal(weigh("cpu")[0], "Nothing runs");
  dashboardPage.hide();
});

test("the memory warns when little is left of Docker's, and says so in red when there is none", async () => {
  await open({ unfolded: true });
  const level = () => [...section.querySelectorAll('[data-value="memory"]')].map((el) => el.getAttribute("data-level"));
  stream().send(sample({ memory_used: 5 * 2 ** 30 }));   // 5 of 7.7 GB
  assert.deepEqual(level(), [null, null]);
  stream().send(sample({ memory_used: 6.8 * 2 ** 30 }));
  assert.deepEqual(level(), ["warn", "warn"], "the strip's and the line's");
  stream().send(sample({ memory_used: 7.5 * 2 ** 30 }));
  assert.deepEqual(level(), ["err", "err"]);
  stream().send(sample({ memory_used: 5 * 2 ** 30 }));
  assert.deepEqual(level(), [null, null], "it cools down");
  dashboardPage.hide();
});

test("storage says what Docker stores, its images, containers, volumes and build cache, and how much of it no container uses, as the sum of the types", async () => {
  await open();
  const stored = cells()[2];
  assert.equal(text(stored.querySelector(".dash-value")), "85.7 GB", "58.89 + 2.243 + 5.944 + 18.62 GB, what `docker system df` totals");
  // One markup for every figure: its value, then what it is of, muted, on its baseline
  assert.deepEqual(cells().map((cell) => cell.querySelector(".dash-figure").children.filter((child) => child.getAttribute).map((child) => cls(child))),
    [["dash-value", "dash-of muted"], ["dash-value", "dash-of muted"], ["dash-value", "dash-of muted"], ["dash-value", "dash-of muted"]], "Docker's versions too");
  assert.equal(text(stored.querySelector(".dash-of")), "in Docker");
  const unused = stored.querySelector(".dash-unused .tip-btn");
  assert.equal(tileSaid(unused), "57.9 GB unused", "42.7 GB + 131 kB + 5.479 GB + 9.709 GB");
  // Its popover: what unused is, how much of each type, and the one thing to know before removing any
  assert.deepEqual(tipOf(unused), ["57.9 GB unused", "What no container uses", "Images 58.9 GB, 42.7 GB unused", "Containers 2.2 GB, 131 kB unused",
    "Volumes 5.9 GB, 5.5 GB unused", "Build cache 18.6 GB, 9.7 GB unused", "Unused volumes can hold data: removing one loses it."], "each type's size and its unused share: a part too thin to point at is said here");
  assert.deepEqual(stored.querySelectorAll(".dash-unused .pop-who").map(text), ["Images", "Containers", "Volumes", "Build cache"], "each type in its column, the sizes lined up");
  assert.equal(unused.tag, "button", "a stop of the keyboard");
  assert.match(unused.getAttribute("aria-label"), /^57\.9 GB unused\. What no container uses/);
  assert.ok(unused.querySelector("svg use").getAttribute("href").endsWith("#i-info"), "with the information mark");
  dashboardPage.hide();
});

test("a type that takes nothing is not in the popover of what is unused, and nothing unused says so", async () => {
  const none = { count: 0, size: 0, reclaimable: 0 };
  await open({ body: dashboard({ data: DF({ containers: { ...none, running: 0 }, volumes: none, cache: none, images: { count: 4, size: 1200000000, reclaimable: 0 } }) }) });
  assert.equal(tileSaid(cells()[2]), "Storage 1.2 GB in Docker Nothing unused");
  assert.deepEqual(cells()[2].querySelectorAll(".dash-unused .pop-who").map(text), ["Images"]);
  assert.deepEqual(tipOf(cells()[2].querySelector(".dash-unused .tip-btn")), ["Nothing unused", "What no container uses", "Images 1.2 GB, all in use"]);
  dashboardPage.hide();
});

test("volumes that no container uses say they can hold data, and what is unused is the sum of what Docker says of each type", async () => {
  const unusedVolumes = { count: 73, size: 40000000000, reclaimable: 38000000000 };
  await open({ body: dashboard({ data: DF({ volumes: unusedVolumes }) }) });
  assert.equal(tileSaid(cells()[2].querySelector(".dash-unused")), "90.4 GB unused", "42.7 GB + 131 kB + 38 GB + 9.709 GB");
  assert.match(tipOf(cells()[2].querySelector(".dash-unused .tip-btn")).join(" "), /Volumes 40\.0 GB, 38\.0 GB unused.*volumes can hold data: removing one loses it/);
  dashboardPage.hide();
});

test("what Docker stores is a line of words with no bar, and does not depend on the disk: Docker's data may be on another one", async () => {
  await open({ body: dashboard({ machine: { ...dashboard().machine, disk_free: 2100000000 } }) });
  assert.equal(section.querySelectorAll(".dash-bar, .dash-part, .dash-disk").length, 0);
  assert.doesNotMatch(said(section) + unseen(), /disk|free/i);
  assert.equal(text(cells()[2].querySelector(".dash-value")), "85.7 GB");
  dashboardPage.hide();
});

test("Docker's own numbers are read when the page opens, and again by Refresh only", async () => {
  await open();
  assert.deepEqual(requests().filter((call) => call.includes("dashboard")), ["GET /api/dashboard"]);
  section.querySelector("[data-refresh]").click();
  await flush();
  assert.deepEqual(requests().filter((call) => call.includes("dashboard")), ["GET /api/dashboard", "GET /api/dashboard?refresh=1"]);
  stream().send({ cpu_percent: 3, memory_used: 1, containers: 0, at: 5 });
  assert.equal(requests().filter((call) => call.includes("dashboard")).length, 2, "the load comes by its stream: it asks for nothing");
  assert.equal(store.pending.get("dashboard"), "refresh", "the button of Refresh says it works");
  dashboardPage.hide();
});

test("the versions a registry is still listing are asked for again until they are known", async () => {
  await open({ body: dashboard({ pending: true, products: [product("api-management", "API Management", { stable: null, prerelease: null, latest: null, status: "unknown" })] }) });
  assert.equal(reads(), 1);
  server.dashboard = dashboard({ pending: true });
  mock.timers.tick(3000);
  await flush();
  assert.equal(reads(), 2);
  assert.equal(ver(products()[0]) + " " + upd(products()[0]), "4.12.20 Update available", "what came fills in");
  server.dashboard = dashboard();
  mock.timers.tick(3000);
  await flush();
  assert.equal(reads(), 3);
  assert.equal(section.querySelectorAll(".dash-wait").length, 0, "known: the registry that could not say is nothing");
  mock.timers.tick(30000);
  await flush();
  assert.equal(reads(), 3, "known, nothing asks again");
  dashboardPage.hide();
});

test("the versions are read again when the lab moves, once at a time, and not when only a state that is not theirs changes", async () => {
  await open();
  assert.equal(reads(), 1);
  dashboardPage.render(store.state);
  await flush();
  assert.equal(reads(), 1, "nothing moved");
  // What is on, or the count of containers, is the state's: it is drawn, nothing is read for it
  await move(lab({ platform: { containers: { running: 3, total: 7 } }, addons: [running("addon", "jade", "Jade"), running("addon", "kafka", "Kafka")] }));
  assert.equal(reads(), 1);
  // The platform stops, then starts: one read at a time, what moves meanwhile is read once
  let release;
  server.dashboard = () => new Promise((resolve) => { release = resolve; });
  await move(lab({ core: "stopped" }));
  assert.equal(reads(), 2);
  store.state = lab({ groups: [group("api-management", "API Management", ["apim-gateway", "apim-management-api"], { key: "starting", label: "Starting" })] });
  dashboardPage.render(store.state);
  assert.equal(reads(), 2, "one at a time");
  server.dashboard = dashboard({ products: [product("api-management", "API Management", { deployed: "4.12.21", status: "latest" })] });   // what the next read finds
  release(dashboard());
  await flush();
  await flush();
  assert.equal(reads(), 3, "what moved meanwhile is read once");
  // A container that runs another version, or a lab that is no longer known
  const other = lab();
  other.services["apim-gateway"].running_tag = "4.12.21";
  await move(other);
  assert.equal(reads(), 4);
  await move(lab({ blind: true }));
  assert.equal(reads(), 5);
  dashboardPage.hide();
});

test("the load of the containers is one stream, listened to while the page is on screen", async () => {
  await open();
  const opened = streams.length;
  assert.equal(stream().url, "/api/dashboard/stats");
  assert.ok(!stream().closed);
  // Leaving the page closes it; showing it again opens one
  dashboardPage.hide();
  assert.ok(stream().closed);
  dashboardPage.show();
  await flush();
  assert.equal(streams.length, opened + 1);
  assert.ok(!stream().closed);
  // A hidden tab listens to nothing, and a tab seen again does
  page.hide();
  page.fire("document", "visibilitychange");
  assert.ok(stream().closed);
  const hidden = streams.length;
  page.show();
  page.fire("document", "visibilitychange");
  await flush();
  assert.equal(streams.length, hidden + 1);
  assert.ok(!stream().closed);
  // The platforms are read again as the tab is seen again
  assert.ok(requests().filter((call) => call === "GET /api/platforms").length >= 2);
  // A tab that is hidden or seen while the page is not on screen opens nothing
  dashboardPage.hide();
  page.fire("document", "visibilitychange");
  assert.equal(streams.length, hidden + 1);
});

test("the load is listened to again once Docker answers again, with the page left open", async () => {
  const down = lab({ blind: true, docker: { error: "Cannot connect to the Docker daemon", warning: null }, wait: "Docker is not reachable", core: "stopped" });
  await open({ state: down, body: dashboard({ docker: { engine: null, compose: "5.4.0", os: null, cpus: null, memory: null, error: "Cannot connect to the Docker daemon" }, data: null }) });
  const before = streams.length;
  assert.ok(stream() === undefined || stream().closed, "nothing to listen to while Docker does not answer");
  // Docker answers again: the state says so, and the numbers are read again
  server.dashboard = dashboard();
  await move(lab());
  await flush();
  assert.equal(streams.length, before + 1, "a stream is opened again");
  assert.ok(!stream().closed);
  // Still on its way, not a second one
  await move(lab());
  assert.equal(streams.length, before + 1);
  dashboardPage.hide();
});

test("a stream that the browser gave up on is opened again after a while, and no frame is asked for on a dead one", async () => {
  await charts();
  const first = stream();
  // The server answered with an error: the browser closes the stream for good, and no animation runs on it
  first.readyState = 2;
  frame(1001);
  assert.equal(queued.size, 0, "no frame for a load that is not coming");
  first.onerror();
  assert.ok(first.closed);
  const before = streams.length;
  mock.timers.tick(2900);
  await flush();
  assert.equal(streams.length, before, "not yet");
  mock.timers.tick(200);
  await flush();
  assert.equal(streams.length, before + 1, "opened again");
  assert.ok(!stream().closed);
  assert.equal(queued.size, 1, "and the animation runs again");
  // A cut that the browser tries again by itself (it is connecting) is left to it
  const second = stream();
  second.readyState = 0;
  second.onerror();
  assert.ok(!second.closed);
  mock.timers.tick(10000);
  await flush();
  assert.equal(streams.length, before + 1);
  // Leaving the page stops the wait for a new one
  second.readyState = 2;
  second.onerror();
  dashboardPage.hide();
  mock.timers.tick(10000);
  await flush();
  assert.equal(streams.length, before + 1, "nothing is opened for a page nobody is on");
});

test("a stream that went quiet stops saying the load it last said: the figures are dashes until it speaks again", async () => {
  await charts();
  send(1001, 4.2, 2 ** 30);
  assert.deepEqual([value("cpu"), value("memory")], ["4.2%", "1.0 GB"]);
  clock = 1004 * 1000;
  mock.timers.tick(2000);
  assert.equal(value("cpu"), "4.2%", "a moment without a sample is not yet quiet");
  clock = 1012 * 1000;
  mock.timers.tick(2000);
  assert.deepEqual([value("cpu"), value("memory")], ["—", "—"], "what it said 11 seconds ago is no longer the load");
  send(1013, 6, 2 ** 30);
  assert.equal(value("cpu"), "6.0%");
  dashboardPage.hide();
});

test("a stream that the browser gave up on is opened again after longer each time, and from the start once it speaks", async () => {
  await charts();
  const asked = () => streams.length;
  const fail = () => { stream().readyState = 2; stream().onerror(); };
  const before = asked();
  fail();
  mock.timers.tick(3100);
  await flush();
  assert.equal(asked(), before + 1, "after 3 seconds");
  fail();
  mock.timers.tick(3100);
  await flush();
  assert.equal(asked(), before + 1, "6 seconds the second time");
  mock.timers.tick(3000);
  await flush();
  assert.equal(asked(), before + 2);
  fail();
  mock.timers.tick(11900);
  await flush();
  assert.equal(asked(), before + 2, "12 seconds the third time");
  mock.timers.tick(200);
  await flush();
  assert.equal(asked(), before + 3);
  send(1001);
  fail();
  mock.timers.tick(3100);
  await flush();
  assert.equal(asked(), before + 4, "it spoke: 3 seconds again");
  dashboardPage.hide();
});

test("what Docker holds, while the server is still reading it, says it is loading and is asked for again soon", async () => {
  await open({ body: dashboard({ data: null, pending: true }) });
  assert.equal(tileSaid(cells()[2]), "Storage Loading", "Docker's data is the slowest: it says it is loading");
  assert.deepEqual(cells().slice(0, 2).map(tileSaid), ["CPU — of 6 cores", "Memory — of 7.7 GB"], "the rest is there");
  assert.equal(ver(products()[0]) + " " + upd(products()[0]), "4.12.20 Update available", "and so are the versions");
  assert.ok(section.querySelector("[data-refresh]"), "Refresh is there");
  assert.equal(section.querySelector(".dash-unused"), null, "and nothing is said of what is unused");
  server.dashboard = dashboard();
  mock.timers.tick(900);
  await flush();
  assert.equal(reads(), 1, "not yet");
  mock.timers.tick(200);
  await flush();
  assert.equal(reads(), 2, "a second after");
  assert.equal(tileSaid(cells()[2]).startsWith("Storage 85.7 GB in Docker 57.9 GB unused"), true);
  mock.timers.tick(30000);
  await flush();
  assert.equal(reads(), 2, "known, nothing asks again");
  // Without a read to wait for, a Docker that gave nothing is a dash
  await open({ body: dashboard({ data: null }) });
  assert.equal(tileSaid(cells()[2]), "Storage —");
  dashboardPage.hide();
});

test("Docker not answering is said once, by the banner: the figures that need it are dashes, the products show as configured, the rest still shows, and nothing listens to a load there is none of", async () => {
  const state = lab({ blind: true, docker: { error: "Cannot connect to the Docker daemon", warning: null }, wait: "Docker is not reachable", core: "stopped", links: LINKS.map((found) => ({ ...found, up: false })) });
  await open({ state, body: dashboard({ docker: { engine: null, compose: "5.4.0", os: null, cpus: null, memory: null, error: "Cannot connect to the Docker daemon" }, data: null,
    products: [product("api-management", "API Management", { deployed: "4.12.1", deployed_state: "configured" })] }), listed: [{ ...PLATFORM, id: "cloud-down" }], checks: { "cloud-down": check([]) } });
  assert.deepEqual(cells().map(tileSaid), ["CPU —", "Memory —", "Storage —", "Docker — Compose 5.4.0"], "dashes, and no sentence that the banner has said");
  assert.equal(said(section).match(/Docker is not reachable/g), null, "the page does not say it: the banner has the cause, the sidebar's chip the state, and the rest is dashes");
  assert.doesNotMatch(said(section), /Cannot connect/);
  assert.equal(section.querySelectorAll(".dash-unused, .dash-bar, .dash-legend").length, 0);
  assert.ok(cells()[2].querySelector("[data-refresh]"), "Refresh is still there to ask again");
  assert.equal(section.querySelectorAll(".dash-chart").length, 0, "no chart for a load that is not there");
  assert.equal(text(block("engine")), "Docker — Compose 5.4.0");
  // Nothing is said of what runs: no count, no colored dot
  assert.equal(said(block("count").querySelector("a")), "Containers");
  assert.equal(products()[0].querySelector(".dot-status").getAttribute("class"), "dot-status stopped");
  assert.equal(ver(products()[0]) + " " + upd(products()[0]), "4.12.1 Update available");
  // The catalog and the Cloud are what they were
  assert.equal(said(cloudRow()), "Gravitee Cloud No gateway");
  assert.ok(stream() === undefined || stream().closed, "no stream is opened for a load that is not there");
  dashboardPage.hide();
});

// --- The charts -----------------------------------------------------------------------------
// A chart is two paths in an SVG that stays where it is. The page moves them from an animation frame, by the clock: a sample
// changes the figure beside the chart and nothing else.

const line = (id, name = "line") => section.querySelector(`[data-chart="${id}"] .chart-${name}`).getAttribute("d");
// The points of a path as [x, y]
const points = (d) => [...d.matchAll(/(-?\d+\.\d),(-?\d+\.\d)/g)].map((found) => [Number(found[1]), Number(found[2])]);
const value = (id) => text(section.querySelector(`[data-value="${id}"]`));
const send = (at, cpu = 10, memory = 2 ** 30) => {
  clock = at * 1000;
  stream().send({ cpu_percent: cpu, memory_used: memory, containers: 3, at: 5 });   // the server's own clock is not the chart's
};
// A page open at `at`, its charts shown
async function charts(at = 1000, options = {}) {
  queued.clear();
  await open({ unfolded: true, ...options });
  clock = at * 1000;
}

test("a chart is built once: a sample changes the figure beside it and the chart's elements stay, and only a frame moves its line", async () => {
  await charts();
  const svg = section.querySelector('[data-chart="cpu"]'), path = svg.querySelector(".chart-line");
  send(1001, 4.2, 2 ** 30);
  assert.equal(value("cpu"), "4.2%");
  assert.equal(value("memory"), "1.0 GB");
  send(1002, 12);
  assert.equal(value("cpu"), "12%");
  assert.ok(section.querySelector('[data-chart="cpu"]') === svg && svg.querySelector(".chart-line") === path, "the same elements: nothing is built again");
  const before = line("cpu");
  frame(1004);
  assert.notEqual(line("cpu"), before, "a frame moves it");
  assert.equal(queued.size, 1, "and asks for the next");
  // A draw of the page (the lab moved) leaves the chart and its figure as they are
  await move(lab());
  assert.ok(section.querySelector('[data-chart="cpu"]') === svg);
  assert.equal(value("cpu"), "12%");
  dashboardPage.hide();
});

test("the line ends at the right edge a second and a half behind the clock, read between the two samples around it, and slides left as the clock goes on", async () => {
  await charts();
  send(1000, 10);
  send(1001, 30);
  send(1002, 50);
  frame(1003);   // the edge is at 1001.5: half way between 30 and 50
  let [first, ...rest] = points(line("cpu"));
  const end = rest.at(-1);
  assert.equal(end[0], 240, "the right edge");
  // The top of the CPU is its highest point, here the one at the edge: 40, so the edge is at the top
  assert.equal(end[1], 5);
  assert.deepEqual(points(line("cpu", "dot")), [[240, 5]], "the dot is at the edge");
  // As the clock goes on the same line moves left and a new end comes: no step, the same samples at other places
  const at = (seconds) => { frame(seconds); return points(line("cpu")); };
  const x1 = at(1003.2)[0][0], x2 = at(1003.4)[0][0], x3 = at(1003.6)[0][0];
  assert.ok(x1 < first[0] && x2 < x1 && x3 < x2, "it goes left at each frame");
  assert.ok(Math.abs((x1 - x2) - (x2 - x3)) < 0.2 && x1 - x2 < 6, "a little and the same at each: a continuous slide, not a step a second");
  dashboardPage.hide();
});

test("the line is drawn as a smooth curve through the midpoints, with no fill under it, and a clip of its own for each chart", async () => {
  await charts();
  for (let at = 1000; at < 1006; at++) send(at, at % 2 ? 40 : 5);
  frame(1007.5);
  const d = line("cpu");
  assert.match(d, /^M[\d.,-]+(Q[\d.,-]+ [\d.,-]+)+L[\d.,-]+$/, "quadratic curves through the midpoints, then a line to the edge");
  for (const [control, mid] of [...d.matchAll(/Q([\d.]+),([\d.]+) ([\d.]+),([\d.]+)/g)].map((m) => [[+m[1], +m[2]], [+m[3], +m[4]]])) {
    assert.ok(mid[1] >= 4.9 && mid[1] <= 40, "never beyond the points it goes through");
    assert.ok(control[1] >= 4.9 && control[1] <= 40);
  }
  assert.equal(section.querySelectorAll(".chart-area, linearGradient").length, 0, "no area: a flat line under a box of shade reads as a smear");
  assert.deepEqual(section.querySelectorAll("clipPath").map((el) => el.getAttribute("id")), ["dash-clip-cpu", "dash-clip-memory"], "one clip for each chart");
  dashboardPage.hide();
});

test("a chart always shows the last minute: its scale never changes, the line grows in from the right and what is older leaves at the left", async () => {
  await charts();
  send(1000);
  send(1001);
  frame(1002.5);   // the edge is at 1001: a second of load in the 60 that make the width
  assert.equal(points(line("cpu"))[0][0], 236);
  for (let at = 1002; at <= 1030; at++) send(at);
  frame(1031.5);   // 30 seconds of it: half the width, at the same scale
  assert.equal(points(line("cpu"))[0][0], 120);
  for (let at = 1031; at <= 1070; at++) send(at);
  frame(1071.5);   // 70 seconds sent, one minute shown: the first is gone to the left
  assert.ok(points(line("cpu"))[0][0] < 0, "what is older is at the left of the edge, where the chart cuts it");
  assert.equal(points(line("cpu")).at(-1)[0], 240);
  assert.match(section.querySelector('[data-chart="cpu"] clipPath rect').getAttribute("x"), /^0$/);
  dashboardPage.hide();
});

test("a sample that is late holds the last value for a few seconds, then the line stops where nothing was heard and its dot is gone", async () => {
  await charts();
  send(1000, 20);
  send(1001, 20);
  send(1002, 20);
  frame(1005);   // the edge is at 1003.5, a second and a half past the last sample: held
  assert.equal(points(line("cpu")).at(-1)[0], 240);
  assert.equal(points(line("cpu", "dot")).length, 1);
  frame(1010);   // 6 seconds late: nothing was listening
  assert.ok(points(line("cpu")).at(-1)[0] < 240, "the line ends before the edge");
  assert.equal(line("cpu", "dot"), "", "and no dot");
  dashboardPage.hide();
});

test("the line breaks where nothing listened: samples more than 5 seconds apart are not joined", async () => {
  await charts();
  send(1000);
  send(1001);
  send(1002);
  send(1010);   // the page was away
  send(1011);
  send(1012);
  frame(1014);
  const d = line("cpu");
  assert.equal((d.match(/M/g) || []).length, 2, "two lines");
  // A minute on, what is older is gone
  for (let at = 1200; at < 1204; at++) send(at);
  frame(1206);
  assert.equal((line("cpu").match(/M/g) || []).length, 1);
  dashboardPage.hide();
});

test("the top edge follows a high point up at once and eases down when it leaves the window: no jump of the whole line", async () => {
  await charts();
  for (let at = 1000; at < 1010; at++) send(at, at === 1004 ? 80 : 5);
  frame(1011.5);
  const quiet = (seconds) => { frame(seconds); return points(line("cpu")).at(-1)[1]; };
  const high = points(line("cpu")).reduce((top, point) => Math.min(top, point[1]), 99);
  assert.equal(high, 5, "the spike is at the top edge");
  // The spike leaves the window: a minute and more of it later, the quiet line would now be drawn against 10 percent
  for (let at = 1010; at < 1126; at++) send(at, 5);
  const before = quiet(1127.5);
  const after = [1127.6, 1127.7, 1127.8].map(quiet);
  assert.ok(before < 48 && after.every((y) => y < 48));
  // Each frame the line comes down a little, never the whole way at once
  const steps = [before, ...after].map((y, i, all) => (i ? y - all[i - 1] : 0)).slice(1);
  assert.ok(steps.every((step) => Math.abs(step) < 6), `no jump: ${steps}`);
  dashboardPage.hide();
});

// What a chart says of its scale: the top of it and zero beside it, and in its tooltip what it covers
const axis = (id) => [text(section.querySelector(`[data-axis="${id}"]`)), text(section.querySelector(`[data-plot="${id}"] .dash-axis`).children[1])];
const plot = (id) => section.querySelector(`[data-plot="${id}"]`).getAttribute("data-tip");

test("a chart has the numbers of its scale beside it, so that a line is read against them: zero and what its top is", async () => {
  await charts();
  assert.deepEqual([axis("cpu"), axis("memory")], [["10%", "0"], ["7.7 GB", "0"]], "a quiet CPU is drawn against 10 percent, the memory against what Docker has");
  assert.equal(section.querySelector('[data-chart="cpu"] .chart-guide').getAttribute("d").match(/M/g).length, 2, "a guide at the top and one at zero");
  assert.deepEqual([plot("cpu"), plot("memory")], ["Last 60 seconds, 0 to 10%", "Last 60 seconds, 0 to 7.7 GB"], "and what the line covers");
  // A high point raises the top of the CPU's scale, at once, and its tooltip says it
  for (let at = 1000; at < 1004; at++) send(at, at === 1002 ? 42.4 : 5);
  frame(1004.5);
  assert.deepEqual(axis("cpu"), ["42%", "0"]);
  assert.equal(plot("cpu"), "Last 60 seconds, 0 to 42%");
  assert.equal(axis("memory")[0], "7.7 GB", "the memory's does not move");
  dashboardPage.hide();
});

test("the memory is drawn against what Docker has, from the first frame", async () => {
  await charts();
  send(1000, 10, 4160014336);
  send(1001, 10, 4160014336);
  send(1002, 10, 4160014336);
  frame(1003.5);
  const end = points(line("memory")).at(-1);
  // Half of what Docker has: half way between the top margin and the bottom one
  assert.equal(end[1], 24.5);
  assert.equal(value("memory"), "3.9 GB");
  dashboardPage.hide();
});

test("the animation runs while the page is on screen, the tab seen and the load streaming, and not otherwise", async () => {
  await charts();
  assert.equal(queued.size, 1, "a frame is asked for as the stream opens");
  frame(1001);
  assert.equal(queued.size, 1, "and again after each");
  // A hidden tab, then a seen one
  page.hide();
  page.fire("document", "visibilitychange");
  assert.equal(queued.size, 0, "nothing runs in a tab nobody sees");
  page.show();
  page.fire("document", "visibilitychange");
  await flush();
  assert.equal(queued.size, 1);
  // The page left: no frame, and the one that was asked for does nothing when it comes
  const [late] = [...queued.values()];
  dashboardPage.hide();
  assert.equal(queued.size, 0);
  late();
  assert.equal(queued.size, 0);
  // Docker does not answer: no load, no animation
  await charts(2000, { state: lab({ blind: true, docker: { error: "x" }, wait: "Docker is not reachable" }), body: dashboard({ docker: { engine: null, compose: null, os: null, cpus: null, memory: null, error: "x" }, data: null }) });
  assert.equal(queued.size, 0);
  dashboardPage.hide();
});

test("folded, Resources draws no chart and asks for no frame, while its figures follow the samples; unfolded, its charts are drawn at once and move", async () => {
  await charts(1000, { unfolded: false });
  const before = line("cpu");
  assert.equal(queued.size, 0, "no frame for what is not seen");
  send(1001, 7);
  send(1003, 9);
  assert.equal(value("cpu"), "9.0%", "the line says the load");
  assert.equal(queued.size, 0, "and asks for no frame");
  assert.equal(line("cpu"), before, "the chart is not drawn");
  unfold(true);
  assert.notEqual(line("cpu"), before, "drawn as it unfolds");
  assert.equal(queued.size, 1, "and moving");
  unfold(false);
  assert.equal(queued.size, 0, "folded again: the frame asked for is given back");
  dashboardPage.hide();
});

test("with reduced motion no animation runs: the chart is drawn when a sample comes, to the sample", async () => {
  const query = "(prefers-reduced-motion: reduce)";
  page.setMedia(query, true);
  try {
    await charts();
    assert.equal(queued.size, 0, "no animation loop");
    send(1000, 10);
    send(1001, 30);
    send(1002, 50);
    assert.equal(queued.size, 0);
    const end = points(line("cpu")).at(-1);
    assert.equal(end[0], 240, "the newest sample is at the right edge");
    assert.equal(end[1], 5, "with no lag: it is the sample itself");
    send(1003, 20);
    assert.notEqual(points(line("cpu")).at(-1)[1], end[1], "drawn again by the next");
  } finally {
    page.setMedia(query, false);
    dashboardPage.hide();
  }
});

// --- Words and drawing --------------------------------------------------------------------------

test("no text of the page separates with a dot, a bullet or a slash, in any state, and the old name of the page is nowhere", async () => {
  const everything = [
    { body: dashboard() },
    { state: lab({ platform: { containers: { running: 3, total: 7 } }, addons: [running("addon", "jade", "Jade", { starred: true }), item("addon", "mail", "Mail server", { starred: true })] }),
      listed: [PLATFORM], checks: { "cloud-eu": check([gateway({ here: true }), gateway({ id: "g2", environmentId: "env-production", connection: "NOT_CONNECTED" })]) } },
    { state: lab({ blind: true, docker: { error: "Cannot connect to the Docker daemon" }, wait: "Docker is not reachable", core: "absent" }),
      body: dashboard({ docker: { engine: null, compose: null, os: null, cpus: null, memory: null, error: "Cannot connect to the Docker daemon" }, data: null }),
      listed: [PLATFORM], checks: { "cloud-eu": { state: "unreachable", message: "Unreachable", environments: [], gateways: [] } } },
  ];
  for (const state of everything) {
    await open(state);
    stream()?.send({ cpu_percent: 3, memory_used: 1000, containers: 0, at: 5 });
    const words = said(section) + [...section.querySelectorAll("[title], [data-tip], [aria-label]")].map((el) => `${el.getAttribute("title")} ${el.getAttribute("data-tip")} ${el.getAttribute("aria-label")}`).join(" ");
    assert.doesNotMatch(words, /[·•∙‧⋅]| \/ | \| /, words);
    assert.doesNotMatch(words, /overview/i);
  }
  const source = readFileSync(new URL("../../../web/js/pages/dashboard.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /[·•∙‧⋅]|overview/i, "not in its code either");
  dashboardPage.hide();
});

test("every block of the page is drawn only when its own text changed", async () => {
  await open();
  const all = () => section.querySelectorAll("[data-card]");
  const kept = all().map((el) => el.children.slice());
  server.dashboard = dashboard();
  section.querySelector("[data-refresh]").click();
  await flush();
  await move(lab());
  assert.deepEqual(all().map((el, i) => el.children.every((child, at) => child === kept[i][at])), Array(all().length).fill(true), "the same text leaves the same elements");
  dashboardPage.hide();
});

test("it is the page the Lab UI opens on, and the menu leads to it first", () => {
  assert.equal(dashboardPage.pattern.test("#/dashboard"), true);
  assert.equal(dashboardPage.nav, "dashboard");
  assert.equal(dashboardPage.title, "Dashboard");
});

test("the head of the card of the playgrounds, setups and add-ons has a button that counts the catalogs and leads to their page", async () => {
  await open({ state: lab({ catalogs: [FIRST(), catalogRow(), { id: "local", kind: "machine", name: "Personal" }], content: content("ready") }) });
  const button = block("catalogs").querySelector("a");
  assert.deepEqual([said(button), button.getAttribute("href"), button.getAttribute("class"), button.getAttribute("aria-label")],
    ["Catalogs 2", "#/catalogs", "btn", "Catalogs, 2"], "the catalogs that were added, not this machine's own");
  await move(lab({ catalogs: [], content: content("none") }));
  assert.deepEqual([said(block("catalogs").querySelector("a")), block("catalogs").querySelector("a").getAttribute("aria-label")], ["Catalogs", null], "none: the page is where one is added");
  dashboardPage.hide();
});

// --- The catalogs: where an item comes from, and what they say while they are not there -----------------------------------------------------

const HUB_ITEM = { origin: origin(), readonly: true };

test("an item of another catalog draws no mark for its catalog in its row, nor does the first catalog's or this machine's: only Personal is said", async () => {
  await open({ state: lab({
    playgrounds: [running("playground", "@acme-hub/bank", "Acme Bakery", HUB_ITEM), running("playground", "local/mine", "Mine", { personal: true })],
    setups: [running("setup", "events", "Event Streams")],
    addons: [], catalogs: [FIRST(), catalogRow()], content: content("ready"),
  }) });
  const marks = (row) => row.querySelectorAll(".dash-marks .pill").map(text);
  assert.deepEqual(catalog().map((row) => [text(row.querySelector("a")), marks(row)]), [["Acme Bakery", []], ["Mine", ["Personal"]], ["Event Streams", []]]);
  dashboardPage.hide();
});

test("the search finds an item by the catalog it comes from, as the lists do", async () => {
  await open({ state: lab({ playgrounds: [item("playground", "@acme-hub/bank", "Acme Bakery", HUB_ITEM), item("playground", "hotels", "Acme Hostels")], setups: [], addons: [] }) });
  const search = section.querySelector('[data-filter="q"]');
  search.value = "hub";
  search.dispatchEvent(new Ev("input"));
  assert.deepEqual(catalog().map((row) => text(row.querySelector("a"))), ["Acme Bakery"]);
  search.value = "";
  search.dispatchEvent(new Ev("input"));
  dashboardPage.hide();
});

test("while the catalogs download the card says Downloading, with the bar and where from, in place of the search and the rows", async () => {
  await open({ state: lab({ playgrounds: [], setups: [], addons: [], gateway: false, catalogs: [FIRST({ commit: "", progress: 0.4 })], content: content("downloading", { progress: 0.4 }) }) });
  const card = block("catalog").parent;
  assert.equal(card.querySelector(".list-tools").hidden, true, "nothing to search");
  assert.equal(rows("catalog").length, 1);
  const status = block("catalog").querySelector('[role="status"]');
  assert.deepEqual([status.getAttribute("class"), status.getAttribute("data-content"), status.hasAttribute("data-line")], ["empty", "downloading", false]);
  assert.equal(text(status.querySelector(".loading .tip-btn")), "Downloading");
  assert.equal(status.querySelector(".progress").getAttribute("data-progress"), "content");
  assert.deepEqual(bubble(status.querySelector(".tip")), ["From github.com/example/catalog"]);
  assert.equal(block("catalog").querySelectorAll("[data-star], .dash-item").length, 0);
  dashboardPage.hide();
});

test("when the download failed the card says why and offers Try again; with no catalog, which one it adds; once it is there and holds nothing, that it holds nothing", async () => {
  const empty = { playgrounds: [], setups: [], addons: [], gateway: false };
  await open({ state: lab({ ...empty, catalogs: [catalogRow({ id: "gravitee", commit: "" })], content: content("failed") }) });
  const status = () => block("catalog").querySelector('[role="status"]');
  assert.equal(text(status().firstElementChild), "Cannot reach github.com. Check your connection, then try again.");
  assert.deepEqual(status().querySelectorAll("[data-content-act]").map((b) => [b.dataset.contentAct, b.dataset.catalog, text(b)]), [["retry", "gravitee", "Try again"]]);
  await move(lab({ ...empty, catalogs: [], content: content("none") }));
  assert.equal(text(status().firstElementChild), "No catalog added");
  assert.deepEqual(status().querySelectorAll("[data-content-act]").map((b) => [b.dataset.contentAct, b.dataset.catalog, text(b)]), [["add", "gravitee", "Add Gravitee public"]]);
  await move(lab({ ...empty, catalogs: [], content: content("none", { offer: null }) }));
  assert.deepEqual(status().querySelectorAll("a").map((a) => [text(a), a.getAttribute("href")]), [["Browse catalogs", "#/catalogs"]]);
  await move(lab({ ...empty, catalogs: [FIRST()], content: content("ready") }));
  assert.equal(text(status()), "No playground, setup or add-on yet");
  assert.equal(block("catalog").parent.querySelector(".list-tools").hidden, true, "nothing to search, and nothing on or starred to list");
  assert.ok(!/Nothing running or starred|Browse playgrounds/.test(text(block("catalog"))));
  dashboardPage.hide();
});

test("with items of this machine and the catalogs not there, the status is one line above the rows, which are not hidden; once they are there it is gone", async () => {
  const mine = { playgrounds: [running("playground", "local/mine", "Mine", { personal: true })], setups: [], addons: [], gateway: false, catalogs: [FIRST({ commit: "", progress: 0.2 })] };
  await open({ state: lab({ ...mine, content: content("downloading") }) });
  assert.equal(block("catalog").parent.querySelector(".list-tools").hidden, false, "there is something to search");
  const [first, ...others] = rows("catalog");
  assert.ok(first.querySelector('[role="status"]').hasAttribute("data-line"), "a line, not a block");
  assert.deepEqual(others.map((row) => text(row.querySelector("a"))), ["Mine"]);
  await move(lab({ ...mine, content: content("failed") }));
  assert.equal(text(rows("catalog")[0].querySelector('[role="status"]')), "Cannot reach github.com. Check your connection, then try again.Try again");
  await move(lab({ ...mine, content: content("ready"), catalogs: [FIRST()] }));
  assert.equal(block("catalog").querySelectorAll('[role="status"]').length, 0);
  assert.deepEqual(catalog().map((row) => text(row.querySelector("a"))), ["Mine"]);
  dashboardPage.hide();
});

test("with the catalogs there, or a server that does not say, the card is what it was", async () => {
  await open({ state: lab({ catalogs: [FIRST()], content: content("ready") }) });
  const before = rows("catalog").map((row) => row.getAttribute("class"));
  await open({ state: lab() });
  assert.deepEqual(rows("catalog").map((row) => row.getAttribute("class")), before);
  assert.equal(block("catalog").querySelectorAll('[role="status"]').length, 0);
  assert.equal(block("catalog").parent.querySelector(".list-tools").hidden, false);
  dashboardPage.hide();
});

test("the name of a catalog is text, never markup, on the card", async () => {
  await open({ state: lab({ playgrounds: [running("playground", "@acme-hub/bank", "Acme Bakery", { origin: origin({ name: HOSTILE }) })], setups: [], addons: [], catalogs: [FIRST(), catalogRow({ name: HOSTILE })] }) });
  assert.equal(block("catalog").querySelectorAll("img").length, 0);
  dashboardPage.hide();
});

test("when the last item goes (the default catalog removed), the pager of the card goes with it, and the card says why there is none", async () => {
  const addons = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"].map((id) => running("addon", id, id.toUpperCase()));
  await open({ state: lab({ playgrounds: [running("playground", "acme-bakery", "Acme Bakery")], setups: [running("setup", "events", "Events")], addons, catalogs: [FIRST()], content: content("ready") }) });
  const pager = block("catalog").parent.querySelector(".pager");
  assert.deepEqual([pager.hidden, text(pager.querySelector("span"))], [false, "1–8 of 12"]);
  await move(lab({ playgrounds: [], setups: [], addons: [], gateway: false, catalogs: [], content: content("none") }));
  assert.equal(pager.hidden, true, "the buttons of pages that are gone are gone too");
  assert.equal(text(block("catalog").querySelector('[role="status"]').firstElementChild), "No catalog added");
  await move(lab({ playgrounds: [], setups: [], addons: [], gateway: false, catalogs: [FIRST()], content: content("ready") }));
  assert.equal(pager.hidden, true);
  dashboardPage.hide();
  store.state = lab();
});

test("with no add-on to run its gateway, the row of API Management in the Cloud offers Add catalog, and that leads to the Catalogs page", async () => {
  const environments = [{ ...environment("Development"), token: { state: "valid", expires: "2027-01-31T00:00:00Z" } }];
  const state = { ...lab({ core: "stopped", gateway: false, platform: { gateways: { cloud: null } }, catalogs: [FIRST()], content: content("ready") }), core: CLOUD_CORE };
  await open({ state, listed: [PLATFORM], checks: { "cloud-eu": check([], { environments }) } });
  await flush();
  const act = part(products()[0], "open").querySelector("[data-core-do]");
  assert.deepEqual([text(act), act.getAttribute("data-core-do")], ["Add catalog", "add-catalog"]);
  assert.equal(said(part(products()[0], "upd")).replace(/^Gateway ?/, ""), "No gateway add-on");
  location.hash = "#/dashboard";
  server.calls = [];
  act.click();
  await flush();
  assert.equal(location.hash, "#/catalogs", "it leads to the catalogs, where an add-on is found");
  assert.deepEqual(server.calls.filter((call) => call.method === "POST"), [], "and asks nothing of a platform");
  dashboardPage.hide();
  store.state = lab();
});
