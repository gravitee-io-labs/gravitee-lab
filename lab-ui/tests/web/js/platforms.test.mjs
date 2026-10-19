// The Gravitee Cloud page (web/js/pages/platforms.js) and its dialogs (web/js/dialogs/platform.js), run for real over a small DOM and a stub of
// what the server answers: a card for each account that says what it is and whether it answers, its environments in one table (the
// ones in use first, with their gateway on this machine, whether the Lab may deploy and what it deployed, and the one action to take;
// the others as quiet lines), the menus, the dialogs that set an environment up and the forms, every control drawing, asking and sending
// what the page promises. A gateway that has not connected is read again every 5 seconds for a minute, then less often, and while a gateway
// job runs; no longer once the page is left.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const PLATFORM = { id: "cloud-eu", name: "My Account", kind: "cloud", url: "https://eu.cloud.gravitee.io",
  account: { id: "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46", hrid: "my-account", name: "My Account" }, tokenSet: true };
// The Cloud gives an environment no hrid and no status
const NAMES = ["Development", "Test", "Production"];
const environment = (name, token) => ({ id: `env-${name.toLowerCase()}`, name, organizationId: "o1", token });
const gateway = (more) => ({ id: "g1", name: "lab", environmentId: "env-development", type: "HYBRID", status: "DEPLOYED",
  connection: "UNKNOWN_HYBRID", version: "4.12.20", domains: ["localhost:8090", "apim-hybrid-gateway:8082"], here: false, kept: true, ...more });
// The add-on that runs the gateways of a Cloud here, as the state says it
const ADDON = (more) => ({ kind: "addon", id: "hybrid", name: "Hybrid gateway", state: { key: "running", label: "Running" }, note: "", note_tone: "",
  note_title: "", action: { do: "stop", label: "Stop", enabled: true, reason: null, dialog: null },
  gateway: { platform: "cloud", service: "apim-hybrid-gateway", token: "HYBRID_CLOUD_TOKEN", domains: ["localhost:8090", "apim-hybrid-gateway:8082"] }, ...more });
const STOPPED = { state: { key: "stopped", label: "Stopped" }, action: { do: "start", label: "Start", enabled: true, reason: null, dialog: null } };
const JOB = { id: "ab12cd34", for: "addon:hybrid", verb: "start", title: "Start Hybrid gateway", done: false, ok: null, progress: null, phases: [], started: 0 };

// What the server answers: the records, and the check of each, which a test changes; every call the page made; the refusal it gets
// in answer to a POST when a test sets one; the plan of a gateway's start
const server = { platforms: [PLATFORM], check: null, checks: 0, states: 0, calls: [], refusal: null, refuse: null, plan: { dialog: null }, hold: null, made: "g-made" };
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
    if (path.endsWith("/check")) {
      server.checks += 1;
      if (server.hold) await server.hold;
      return reply(server.check);
    }
    if (method === "POST" && server.refuse?.(path)) return reply({ error: server.refuse(path) }, 400);
    if (method === "POST" && server.refusal) return reply({ error: server.refusal }, 400);
    if (path === "/api/platforms") return method === "POST" ? reply(PLATFORM) : server.failing ? reply({ error: server.failing }, 500) : reply({ platforms: server.platforms });
    if (path.endsWith("/plan")) return reply(server.plan);
    if (method === "POST" && path.endsWith("/gateways")) return reply({ gateway: { id: server.made }, tokenSaved: true });
    if (/\/(start|stop)$/.test(path)) return reply({ ...JOB, id: `job-${server.calls.length}` });   // a job of its own: never one a test set in the state
    return reply({});
  },
});
// A job's output, which the drawer follows: its stream is kept, so that a test can end the job it started (the clock of the drawer, a timer of
// its own, would run on in the ticks of the tests after it)
const streams = [];
globalThis.EventSource = class { constructor() { streams.push(this); } close() {} addEventListener() {} };
const endJobs = () => {
  for (const stream of streams.splice(0)) stream.onerror?.();
  page.doc.body.hidden = false;   // the drawer's output hides its parent, which in this small DOM is the body
};
globalThis.innerHeight = 800;   // a menu opens under its button, or above it when the window ends first
// A button that fades back to its face reads how long it takes from the stylesheet: no fade here
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
// Two selectors that the page asks and the small DOM does not know: a menu opened with the keyboard focuses its first entry
// (:focus-visible, which a click never has), and a button at work takes no click (:is)
const { matches, closest } = El.prototype;
El.prototype.matches = function (selector) { return selector === ":focus-visible" ? false : matches.call(this, selector); };
El.prototype.closest = function (selector) { return closest.call(this, selector.replace(":is(.busy, .said)", ".busy")); };
const { store } = await import("../../../web/js/core.js");
const { platformsPage, accountState } = await import("../../../web/js/pages/platforms.js");
const { logsPage } = await import("../../../web/js/pages/logs.js");
const { License } = await import("../../../web/js/dialogs/license.js");
const { openCreateGateway, openCloudToken } = await import("../../../web/js/dialogs/platform.js");
const RUNNING = { key: "running", label: "Running", running: true };
const state = (more = {}) => ({ blind: false, wait: "", docker: { error: null }, services: { "apim-hybrid-gateway": { state: RUNNING } }, playgrounds: [],
  addons: [ADDON()], setups: [], job: null, links: [], categories: [], platform: { groups: [] }, ...more });
store.state = state();

const section = page.doc.createElement("section");
page.doc.body.append(section);
platformsPage.init(section);

// The page as it opens: the records and their checks are read
async function open(check, platforms = [PLATFORM]) {
  endJobs();
  Object.assign(server, { platforms, check, checks: 0, calls: [], refusal: null, refuse: null, failing: null, hold: null });
  platformsPage.show([]);
  await flush();
}
const ok = (gateways, tokens = {}, names = NAMES) => ({ state: "ok", message: "", gateways,
  environments: names.map((name) => environment(name, tokens[name] || { state: "unset" })) });
const VALID = { state: "valid", expires: "2027-01-31T00:00:00Z" };
const cards = () => section.querySelectorAll(".plat-card");
const card = () => cards()[0];
const rows = () => section.querySelectorAll(".plat-row");
const used = () => rows().filter((r) => !r.classList.contains("plat-quiet"));
const quiet = () => rows().filter((r) => r.classList.contains("plat-quiet"));
const rowOf = (name) => rows().find((r) => text(r.querySelector(".plat-env")) === name);
// The table of the environments, the first of a card, and the list of the hybrid gateways under it
const envTable = () => section.querySelectorAll(".plat-table").filter((t) => !t.classList.contains("plat-gw-table"));
const gwRows = () => section.querySelectorAll(".plat-gw-row");
const gwRow = (name) => gwRows().find((r) => text(r.querySelector(".plat-env")) === name);
// A gateway of the list, as [name, environment, state, version, domains]
const gwSaid = (r) => [text(r.querySelector(".plat-env")), text(r.querySelector(".plat-gw-env .plat-cell")), said(r.querySelector(".plat-gw-state .status")),
  text(r.querySelector(".plat-gw-ver .plat-cell")), text(r.querySelector(".plat-gw-dom .plat-cell"))];
// The words of an element, one for each of its parts that holds some (the page puts no space between its cells), but what a button keeps
// for when it works and the bubble of a tooltip
const said = (el) => el.children.map((child) => (child.tag ? (child.getAttribute("aria-hidden") ? "" : said(child)) : child.data.trim()))
  .filter(Boolean).join(" ");
const gatewayOf = (name) => said(rowOf(name).querySelector(".plat-gateway .status"));
// What the popover of a gateway's state says of it besides its name: its version and the address it answers on here
const detailsOf = (name) => rowOf(name).querySelectorAll(".plat-gateway .status .pop-text, .plat-gateway .status .pop-what").map(text);
const deploysOf = (name) => said(rowOf(name).querySelector(".plat-deploys .status"));

const lines = (el) => bubble(el.querySelector(".tip"));
// A line of a dialog as it reads, without the bubbles of the tooltips in it
const plain = (el) => el.children.filter((child) => !(child.tag && child.classList.contains("tip"))).map((child) => child.textContent).join("");
const primary = (name) => rowOf(name).querySelector("[data-do]");
const primaryOf = (name) => (primary(name) ? said(primary(name)) : "");
// What an action leaves at work: its button keeps its working face a moment (jobs.js), which the clock of the test runs out
const settle = async () => {
  await flush();
  mock.timers.tick(600);
  await flush();
};
const posted = (suffix) => server.calls.filter((c) => c.method === "POST" && c.path.endsWith(suffix));
const toasts = () => page.ids.toasts.querySelectorAll(".toast").map(text);

// A menu: opened from a button of the page, its entries as their labels; one chosen as a click on it does
const entries = () => page.ids.menu.querySelectorAll(".menu-item");
const label = (entry) => text(entry.children.find((c) => c.tag === "span" && !c.classList.contains("menu-hint")));
const close = () => page.doc.dispatchEvent(new Ev("keydown", { key: "Escape" }));
const menu = (button) => {
  close();
  button.click();
  return entries().map(label);
};
const choose = async (name) => {
  const entry = entries().find((e) => label(e) === name);
  page.doc.dispatchEvent(new Ev("click", { target: entry }));
  await settle();
};
const envMore = (name) => rowOf(name).querySelector("[data-env-more]");
const accountMore = () => section.querySelector("[data-account-more]");

// The dialog of what an action does (ui.js `effectsDialog`): the browser tells it which choice is checked, which the stub does not
const choice = page.ids.choice, picks = page.ids["choice-list"];
picks.querySelector = (selector) => (selector === "input:checked" ? picks.querySelectorAll("input")[0] : El.prototype.querySelector.call(picks, selector));
const effects = () => ({ title: text(page.ids["choice-title"]), action: text(page.ids["choice-ok"]),
  steps: picks.querySelectorAll(".step").map((s) => [text(s.querySelector("strong")), s.querySelectorAll(".step-items li").map(said),
    s.querySelector(".step-head .muted") ? text(s.querySelector(".step-head .muted")) : ""]),
  notes: picks.querySelectorAll(".choice-row").map((n) => [text(n.querySelector(".choice-row-label")), n.querySelectorAll(".pill").map(text)]) });
const answer = async (confirmed) => {
  choice.returnValue = confirmed ? "ok" : "";
  choice.onclose();
  await settle();
};

// --- Before there is a card ------------------------------------------------------------

test("while the platforms are read the page draws the rows they will have", async () => {
  let release;
  Object.assign(server, { platforms: [], check: null, failing: null, hold: null });
  const read = globalThis.fetch;
  globalThis.fetch = async (path, options) => (path === "/api/platforms" && !options?.method ? new Promise((resolve) => { release = () => resolve(read(path, options)); }) : read(path, options));
  platformsPage.show([]);
  await flush();
  assert.equal(section.querySelectorAll(".plat-skel").length, 3, "rows that are not there yet");
  assert.equal(section.querySelector(".plat-lonely .plat-table").getAttribute("aria-busy"), "true");
  assert.ok(section.querySelector(".page-head [data-new]").hidden);
  release();
  await flush();
  globalThis.fetch = read;
  assert.equal(section.querySelectorAll(".plat-skel").length, 0);
  platformsPage.hide();
});

test("a list that cannot be read says so and tries again", async () => {
  Object.assign(server, { platforms: [], check: null, failing: "Lab UI offline", hold: null });
  platformsPage.show([]);
  await flush();
  assert.equal(said(section.querySelector(".plat-lonely")), "Accounts cannot be read Lab UI offline Try again");
  assert.equal(cards().length, 0);
  server.failing = null;
  server.platforms = [PLATFORM];
  server.check = ok([]);
  section.querySelector("[data-reload]").click();
  await flush();
  assert.equal(cards().length, 1, "once it can be read the card is there");
  platformsPage.hide();
});

test("with no account connected the page says so and offers the one button, with no sentence and nothing the page title says", async () => {
  await open(null, []);
  const lonely = section.querySelector(".plat-lonely");
  assert.ok(!lonely.hidden);
  assert.equal(said(lonely), "No account connected Connect account");
  assert.equal(cards().length, 0);
  assert.ok(section.querySelector(".page-head [data-new]").hidden, "the head offers nothing while the one button is in the page");
  assert.deepEqual(lonely.querySelectorAll("button").map(text), ["Connect account"]);
  assert.ok(!lonely.querySelector("button").classList.contains("primary"), "a plain button: the page's primary is the banner's");
  assert.ok(lonely.querySelector(".empty"), "the one empty block of every page");
});

// --- The card -------------------------------------------------------------------------

test("a card is titled by its account, says where its console is and how it answers, and has the key of its connection, one console button and a labeled More", async () => {
  await open(ok([]));
  assert.equal(cards().length, 1);
  const head = card().querySelector(".plat-head");
  assert.equal(said(head.querySelector(".plat-id")), "My Account eu.cloud.gravitee.io", "the page says Gravitee Cloud: the card does not");
  assert.equal(head.querySelector(".plat-name").getAttribute("role"), "heading");
  assert.equal(head.querySelector(".plat-host").getAttribute("data-tip"), "https://eu.cloud.gravitee.io");
  assert.equal(text(head.querySelector(".pill")), "Connected");
  const [key, link, more] = head.querySelector(".plat-tools").children.filter((c) => c.tag);
  assert.deepEqual([key.getAttribute("class"), key.getAttribute("data-tip"), key.getAttribute("aria-label"), key.getAttribute("aria-haspopup")],
    ["icon-btn", "Manage connection", "Manage connection, My Account", "dialog"], "the key of every connection's settings, named for its account");
  assert.equal(key.querySelector("use").getAttribute("href"), "#i-key");
  assert.equal(text(link), "Open console");
  assert.equal(link.tag, "a", "the console opens in a tab of its own");
  assert.equal(link.getAttribute("href"), "https://eu.cloud.gravitee.io");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(said(more), "More");
  assert.equal(more.getAttribute("aria-haspopup"), "true");
  assert.deepEqual(head.querySelectorAll("button, a").map((b) => text(b)), ["", "Open console", "More"], "one icon, as the key of a row, the rest labeled");
  assert.ok(!section.querySelector(".page-head [data-new]").hidden);
  assert.equal(text(section.querySelector(".page-head [data-new]")), "Connect account");
});

test("the name the person gave the account is its title", async () => {
  await open(ok([]), [{ ...PLATFORM, name: "Work" }]);
  assert.equal(said(card().querySelector(".plat-id")), "Work eu.cloud.gravitee.io");
});

test("the card is one card for each account, in the order they are kept", async () => {
  await open(ok([]), [PLATFORM, { ...PLATFORM, id: "client-lab", name: "Client Lab" }]);
  assert.deepEqual(cards().map((c) => c.dataset.key), ["cloud-eu", "client-lab"]);
  platformsPage.hide();
});

test("its state is the answer of its last check: Connected, Account Token refused or Unreachable, and Loading before there is one, Checking when asked again", async () => {
  assert.deepEqual(accountState({ state: "ok", message: "" }), ["ok", "Connected"]);
  assert.deepEqual(accountState({ state: "invalid" }), ["err", "Account Token refused"]);
  let hold;
  Object.assign(server, { platforms: [{ ...PLATFORM, id: "new-one" }], check: ok([]), calls: [], checks: 0, hold: new Promise((resolve) => { hold = resolve; }) });
  platformsPage.show([]);
  await flush();
  assert.equal(said(card().querySelector(".plat-state")), "Loading");
  assert.equal(card().querySelectorAll(".plat-skel").length, 3, "the rows it will have");
  hold();
  await flush();
  for (const [found, word, message] of [["invalid", "Account Token refused", "Gravitee Cloud refuses this token"],
    ["unreachable", "Unreachable", "eu.cloud.gravitee.io refuses the connection"], ["ok", "Connected", ""]]) {
    await open(found === "ok" ? ok([]) : { state: found, message, environments: [], gateways: [] });
    assert.equal(text(card().querySelector(".plat-state .pill")), word);
    assert.equal(card().querySelector(".plat-state .pill").getAttribute("title"), null, "why is the notice's, said once");
    const why = card().querySelector(".notice-text");
    assert.equal(why ? said(why) : "", message);
  }
  let release;
  server.hold = new Promise((resolve) => { release = resolve; });
  accountMore().click();
  await choose("Check again");
  assert.equal(said(card().querySelector(".plat-state")), "Checking");
  release();
  await flush();
  assert.equal(text(card().querySelector(".plat-state .pill")), "Connected");
  platformsPage.hide();
});

test("More on the card offers Rename, Check again and Remove account; the Account Token is the connection's", async () => {
  await open(ok([]));
  assert.deepEqual(menu(accountMore()), ["Rename", "Check again", "Remove account"]);
  assert.ok(entries()[2].classList.contains("danger"), "what removes is in the danger tone");
  close();
});

// The dialog of a card's connection, which its key opens
const connectionOf = () => page.doc.querySelector("#connection-title").closest("dialog");
const openConnection = () => card().querySelector("[data-connection]").click();

test("the key of a card shows its connection: the account, its Account ID to copy, its address and whether its Account Token is set, never the token", async () => {
  await open(ok([]));
  openConnection();
  const dialog = connectionOf();
  assert.ok(dialog.hasAttribute("open"), "a modal dialog");
  assert.equal(text(dialog.querySelector("h2")), "Connection of My Account");
  assert.deepEqual(dialog.querySelectorAll(".pairs > div").map((row) => [text(row.querySelector("dt")), said(row.querySelector("dd"))]), [
    ["Account", "My Account"], ["Account ID", PLATFORM.account.id], ["Address", "https://eu.cloud.gravitee.io"], ["Account Token", "Set"]]);
  const copy = dialog.querySelector("[data-copy]");
  assert.deepEqual([copy.dataset.copy, copy.getAttribute("aria-label")], [PLATFORM.account.id, "Copy the Account ID"], "the button every dialog copies with");
  assert.equal(dialog.querySelector("dd code").textContent, PLATFORM.account.id, "in monospace");
  assert.doesNotMatch(dialog.innerHTML, /token-value|password/, "no field and no value of a token");
  assert.equal(dialog.querySelector(".pairs .dot-status").getAttribute("class"), "dot-status healthy");
  dialog.querySelector("[data-connection-close]").click();
  assert.ok(!dialog.hasAttribute("open"));
  // A token Cloud refuses says so, in red, why in its popover
  await open({ state: "invalid", message: "Gravitee Cloud refuses this token", environments: [], gateways: [] });
  openConnection();
  const token = connectionOf().querySelectorAll(".pairs > div")[3];
  assert.equal(said(token.querySelector("dd")), "Refused");
  assert.equal(token.querySelector(".status").getAttribute("class"), "status err");
  assert.deepEqual(lines(token), ["Refused", "Gravitee Cloud refuses this token"]);
  connectionOf().querySelector("[data-connection-close]").click();
  platformsPage.hide();
});

test("Replace Account Token, from the connection, closes it and opens the form that replaces it", async () => {
  await open(ok([]));
  openConnection();
  const replaceButton = connectionOf().querySelector("[data-connection-replace]");
  assert.deepEqual([replaceButton.getAttribute("class"), text(replaceButton)], ["btn", "Replace Account Token"]);
  replaceButton.click();
  assert.ok(!connectionOf().hasAttribute("open"));
  assert.equal(text(formOf("account-token").querySelector("h2")), "Replace Account Token");
  formOf("account-token").querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("Check again reads the platform again", async () => {
  await open(ok([]));
  const reads = server.checks;
  menu(accountMore());
  await choose("Check again");
  assert.equal(server.checks, reads + 1);
});

// --- The environments -----------------------------------------------------------------

test("the environments in use come first in one table under their label, the others as quiet lines under theirs, each with Set up", async () => {
  await open(ok([gateway()], { Test: VALID }));
  assert.deepEqual(envTable()[0].querySelectorAll(".plat-heads > span").map(said), ["Environment", "Gateway", "Deploys", ""]);
  assert.deepEqual(section.querySelectorAll(".stack-sep").map(text), ["In use", "Not set up", "Hybrid gateways"], "then the gateways, under their band");
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Development", "Test"]);
  assert.deepEqual(quiet().map((r) => text(r.querySelector(".plat-env"))), ["Production"]);
  assert.deepEqual(quiet()[0].querySelectorAll("button").map(text), ["Set up"], "a name and the way in, nothing more");
  assert.equal(quiet()[0].querySelector("[data-do]").getAttribute("data-do"), "set-up");
  // The groups are named for a screen reader, which hears their rows and not the band that labels them
  assert.deepEqual(envTable()[0].children.filter((el) => el.getAttribute).map((el) => el.getAttribute("role") === "rowgroup" ? el.getAttribute("aria-label") : "heads"), ["heads", "In use", "Not set up"]);
  assert.deepEqual(envTable()[0].querySelectorAll(".stack-sep").map((el) => el.getAttribute("aria-hidden")), ["true", "true"]);
  assert.equal(envTable()[0].getAttribute("role"), "table");
  assert.deepEqual(section.querySelectorAll('[role="rowgroup"] > .plat-row').map((r) => r.getAttribute("role")).filter((r) => r !== "row"), [], "every line is a row");
});

test("an account with nothing in use lists its environments as quiet lines and has no empty columns", async () => {
  await open(ok([]));
  assert.equal(section.querySelectorAll(".plat-heads").length, 0);
  assert.deepEqual(section.querySelectorAll(".stack-sep").map(text), ["Not set up", "Hybrid gateways"]);
  assert.deepEqual(quiet().map((r) => text(r.querySelector(".plat-env"))), NAMES);
  assert.equal(used().length, 0);
  assert.equal(envTable()[0].querySelectorAll("[data-do]").map((b) => b.getAttribute("data-do")).join(), "set-up,set-up,set-up");
});

test("with more than three environments not set up the card lists two and says how many more there are; that opens them, and closes them", async () => {
  const names = ["Development", "Test", "Production", "Demo", "QA", "Staging"];
  await open(ok([gateway()], {}, names));
  assert.deepEqual(quiet().map((r) => text(r.querySelector(".plat-env"))), ["Test", "Production"]);
  const toggle = () => section.querySelector(".plat-toggle");
  assert.equal(text(toggle()), "Show 3 more environments");
  assert.equal(toggle().getAttribute("aria-expanded"), "false");
  toggle().click();
  assert.deepEqual(quiet().map((r) => text(r.querySelector(".plat-env"))), ["Test", "Production", "Demo", "QA", "Staging"]);
  assert.equal(text(toggle()), "Show fewer environments");
  assert.equal(toggle().getAttribute("aria-expanded"), "true");
  toggle().click();
  assert.equal(quiet().length, 2);
  await open(ok([gateway()], {}, names.slice(0, 5)));
  assert.equal(text(toggle()), "Show 2 more environments");
  await open(ok([gateway()], {}, names.slice(0, 4)));
  assert.equal(quiet().length, 3, "a line that would hide one only takes the room of the one it hides");
  assert.equal(toggle(), null);
});

test("the way in to an environment that is not set up is one dialog, whatever it lacks", async () => {
  await open(ok([]));
  quiet()[1].querySelector("[data-do]").click();
  await flush();
  assert.equal(text(field("use-gateway").closest("dialog").querySelector("h2")), "Set up Test");
  field("use-gateway").closest("form").querySelector("[data-cancel]").click();
});

// What an environment says of its gateway on this machine, the first that applies. The gateway that runs here is the add-on's: its own warn
// or error note, else its container's state while it does not run; then Cloud's word, a connected gateway that does not run here being connected
// elsewhere. A gateway nobody here keeps and that is not connected is none of the Lab's.
const WORDS = [
  ["no gateway", null, null, ["", "Not created"]],
  ["Cloud: never connected, kept, not here", {}, null, ["stopped", "Stopped here"]],
  ["Cloud: not connected, kept, not here", { connection: "NOT_CONNECTED" }, null, ["stopped", "Stopped here"]],
  ["Cloud: connected, but not here", { connection: "CONNECTED" }, null, ["stopped", "Connected elsewhere"]],
  ["Cloud: connected and not kept", { connection: "CONNECTED", kept: false }, null, ["stopped", "Connected elsewhere"]],
  ["Cloud: pending", { status: "PENDING" }, null, ["starting", "Pending"]],
  ["Cloud: deleting", { status: "DELETING" }, null, ["stopping", "Deleting"]],
  ["Cloud: failed", { status: "FAILED" }, null, ["issue", "Failed"]],
  ["here, running and connected", { here: true, connection: "CONNECTED" }, {}, ["healthy", "Running here"]],
  ["here, running, not connected yet", { here: true, connection: "NOT_CONNECTED" }, {}, ["starting", "Connecting"]],
  ["here, running, never connected", { here: true }, {}, ["starting", "Connecting"]],
  ["here, the add-on is behind what is saved", { here: true, connection: "CONNECTED" }, { note: "Apply needed", note_tone: "warn" }, ["warn", "Apply needed"]],
  ["here, the add-on lacks settings", { here: true, connection: "CONNECTED" }, { note: "Needs settings", note_tone: "warn", action: { do: "settings", label: "Settings", enabled: true } }, ["warn", "Needs settings"]],
  ["here, the add-on has an issue", { here: true, connection: "CONNECTED" },
    { state: { key: "issue", label: "Issue" }, note: "Hybrid gateway exited (1)", note_tone: "err" }, ["issue", "Hybrid gateway exited (1)"]],
  ["here, the add-on lacks a license", { here: true }, { state: { key: "off", label: "" }, note: "Needs a license", note_tone: "warn" }, ["warn", "Needs a license"]],
  ["here, the add-on failed to start", { here: true }, { state: { key: "stopped", label: "Stopped" }, note: "Failed", note_tone: "err" }, ["issue", "Failed"]],
  ["here, the add-on is starting", { here: true, connection: "CONNECTED" }, { state: { key: "starting", label: "Starting" } }, ["starting", "Starting"]],
  ["here, the add-on is stopping", { here: true, connection: "CONNECTED" }, { state: { key: "stopping", label: "Stopping" } }, ["stopping", "Stopping"]],
  ["here, the add-on is on and stopped", { here: true, connection: "CONNECTED" },
    { state: { key: "stopped", label: "Stopped" }, note: "Starts with the platform" }, ["stopped", "Stopped here"]],
  ["here, the add-on is off", { here: true, connection: "CONNECTED" }, { state: { key: "off", label: "" } }, ["stopped", "Stopped here"]],
];

test("the gateway of an environment says the first thing that applies, with the dot of its state", async () => {
  for (const [why, more, addon, [dot, word]] of WORDS) {
    store.state = state({ addons: [ADDON(addon || {})] });
    await open(ok(more ? [gateway(more)] : [], { Development: VALID }));
    const status = rowOf("Development").querySelector(".plat-gateway .status");
    assert.equal(said(status), word, why);
    assert.equal(status.querySelector(".dot-status").className, `dot-status ${dot}`, `${why}: the ${dot || "gray"} dot`);
  }
  store.state = state();
  platformsPage.hide();
});

test("while Docker does not answer, a gateway says only what Cloud says of it", async () => {
  store.state = state({ blind: true, wait: "Docker is not reachable", docker: { error: "no daemon" }, addons: [ADDON({ note: "Failed", note_tone: "err" })] });
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  assert.equal(gatewayOf("Development"), "Connected");
  assert.equal(rowOf("Development").querySelector(".plat-gateway .dot-status").className, "dot-status ");
  store.state = state();
  platformsPage.hide();
});

test("a gateway is one state on one line; its popover says its name, its version and, while it runs here, where it answers, in monospace", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "ci", environmentId: "env-test", version: "4.12.21" })], { Development: VALID, Test: VALID }));
  assert.equal(rowOf("Development").querySelectorAll(".plat-sub").length, 0, "no second line: the row is 48 px, as on the Dashboard");
  assert.deepEqual(detailsOf("Development"), ["4.12.20", "localhost:8090"], "the address a person calls, not the name of the container");
  assert.deepEqual(detailsOf("Test"), ["4.12.21"], "a gateway that does not run here answers nowhere on this machine");
  assert.deepEqual(rowOf("Development").querySelectorAll(".plat-gateway .chip").map(text), ["4.12.20", "localhost:8090"]);
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-gateway .status")), ["lab", "Version 4.12.20", "Address localhost:8090"]);
  assert.equal(rowOf("Development").querySelector(".plat-gateway .status .tip-btn").getAttribute("aria-label"), "Running here: lab. Version 4.12.20. Address localhost:8090");
  assert.deepEqual(lines(rowOf("Test").querySelector(".plat-gateway .status")), ["ci", "Version 4.12.21"]);
  await open(ok([gateway({ connection: "CONNECTED", kept: false, name: "Alice laptop" })], { Development: VALID }));
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-gateway .status")), ["Alice laptop", "Another machine runs it", "Version 4.12.20"], "who runs it, then its version");
  platformsPage.hide();
});

test("the reason of the add-on is the tooltip of a gateway that runs here and cannot", async () => {
  store.state = state({ addons: [ADDON({ note: "Failed", note_tone: "err", note_title: "[seed] ✗ no license", state: { key: "stopped", label: "Stopped" } })] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-gateway .status")), ["lab", "[seed] ✗ no license", "Version 4.12.20"]);
  store.state = state();
  platformsPage.hide();
});

test("a missing license is a warning, in amber, in one style, and the action of the row is to add one", async () => {
  store.state = state({ addons: [ADDON({ state: { key: "off", label: "" }, note: "Needs a license", note_tone: "warn", note_title: "Uses: Enterprise features", note_file: ".env" })] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  const status = rowOf("Development").querySelector(".plat-gateway .status");
  assert.equal(said(status), "Needs a license");
  assert.equal(status.querySelector(".dot-status").className, "dot-status warn", "a blocker looks like one");
  assert.deepEqual(lines(status), ["lab", "Uses: Enterprise features", "Version 4.12.20"], "the shared popover, not a title");
  assert.equal(rowOf("Development").querySelectorAll("[title]").filter((el) => el.getAttribute("title") === "Uses: Enterprise features").length, 0);
  assert.equal(rowOf("Development").querySelectorAll("[data-license]").length, 0, "the word is not a link beside a button for the same thing");
  assert.equal(primaryOf("Development"), "Add license");
  const opened = [];
  const was = License.show;
  License.show = (options) => opened.push(options);
  primary("Development").click();
  License.show = was;
  assert.deepEqual(opened, [{ form: true }], "the form that takes one");
  const options = menu(envMore("Development"));
  assert.ok(!options.includes("Start gateway") || entries().find((e) => label(e) === "Start gateway").disabled, "Start waits for the license");
  assert.equal(text(entries().find((e) => label(e) === "Start gateway").querySelector(".menu-hint")), "Needs a license", "and says why");
  close();
  store.state = state({ addons: [ADDON({ state: { key: "off", label: "" }, note: "License expired", note_tone: "err", note_file: ".env" })] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  assert.equal(primaryOf("Development"), "Replace license");
  // No gateway of the platform can start without it, the one that is not here neither
  await open(ok([gateway({ here: true }), gateway({ id: "g2", name: "second", environmentId: "env-test", connection: "NOT_CONNECTED" })], { Development: VALID, Test: VALID }));
  assert.equal(gatewayOf("Test"), "Stopped here");
  assert.equal(primaryOf("Test"), "Replace license");
  // In the list of the gateways, a gateway the Lab keeps waits for it too, with its reason
  const start = gwRow("second").querySelector('[data-gw-do="start"]');
  assert.ok(isOff(start));
  assert.equal(offWhy(start), "License expired");
  store.state = state();
  platformsPage.hide();
});

test("an environment with several gateways says how many after the state of its own, a quiet button that leads to their list", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "second" })], { Development: VALID }));
  const status = rowOf("Development").querySelector(".plat-gateway .status");
  assert.equal(said(status), "Running here 2 gateways", "no +1 whose names hide in a tooltip");
  const count = status.querySelector(".plat-count");
  assert.deepEqual([count.tag, count.getAttribute("type"), count.getAttribute("aria-label"), count.dataset.gateways], ["button", "button", "2 gateways in Development", "cloud-eu"]);
  assert.equal(status.querySelectorAll("[title]").length, 0);
  const band = section.querySelector("[data-gateways-of]");
  band.focus = () => { page.doc.activeElement = band; };
  band.scrollIntoView = () => { band.scrolled = true; };
  count.click();
  assert.ok(band.scrolled && page.doc.activeElement === band, "the list in view, the keyboard on its band");
  // One gateway, which the state names: no count
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  assert.equal(said(rowOf("Development").querySelector(".plat-gateway .status")), "Running here");
  platformsPage.hide();
});

test("a hosted gateway is not listed, so the environment has none of its own", async () => {
  await open(ok([gateway({ id: "h", name: "hosted", type: "SAAS" })], { Development: VALID }));
  assert.equal(gatewayOf("Development"), "Not created");
  assert.equal(primaryOf("Development"), "Create gateway");
  platformsPage.hide();
});

// What an environment says of what the Lab may do in it, on one line: what the Lab deployed there, else that its Cloud Token is about to
// expire, else Can deploy once it has a Cloud Token that is valid and a gateway here, else what it lacks, the gateway or the token. The
// rest is in the popover, the first line of which is the word.
const DEPLOYS = [
  ["a valid token and a gateway", { state: "valid", expires: "2027-01-31T00:00:00Z" }, {}, ["healthy", "Can deploy", ["Can deploy", `Expires ${on("2027-01-31")}`]]],
  ["a valid token, from a claim", { state: "valid", expires: Date.parse("2027-01-31T00:00:00Z") / 1000 }, {}, ["healthy", "Can deploy", ["Can deploy", `Expires ${on("2027-01-31")}`]]],
  ["a valid token without an expiry", { state: "valid" }, {}, ["healthy", "Can deploy", []]],
  ["a valid token that expires in 5 days", { state: "valid", expires: new Date(Date.now() + 5 * 86400000 + 3600000).toISOString() }, {}, ["warn", "Expires in 5 days", null]],
  ["a valid token and no gateway of the Lab's", { state: "valid" }, { kept: false, connection: "CONNECTED" }, ["stopped", "Needs a gateway here", ["Needs a gateway here", "A deploy goes through a gateway of this environment that runs here"]]],
  ["no token", { state: "unset" }, {}, ["", "Needs a Cloud Token", ["Cloud Token", "Lets the Lab create APIs in this environment for a playground. Create one in Gravitee Cloud: Settings, Cloud Tokens, type Automation."]]],
  ["no state of the token", undefined, {}, ["", "Needs a Cloud Token", null]],
  ["a state this page does not know", { state: "revoked" }, {}, ["", "Needs a Cloud Token", null]],
  ["an expired token", { state: "expired", expires: "2026-10-01T00:00:00Z" }, {}, ["issue", "Cloud Token expired", ["Cloud Token expired", `Expired on ${on("2026-10-01")}`]]],
  ["a refused token", { state: "refused" }, {}, ["issue", "Cloud Token refused", []]],
];
function on(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

test("an environment says whether the Lab may deploy to it, in one state with the dot of its state, the rest in its popover", async () => {
  for (const [why, token, more, [dot, word, tip]] of DEPLOYS) {
    await open(ok([gateway({ connection: "CONNECTED", ...more })], { Development: token }));
    const status = rowOf("Development").querySelector(".plat-deploys .status");
    assert.equal(said(status), word, why);
    assert.equal(status.querySelector(".dot-status").className, `dot-status ${dot}`, why);
    if (tip) assert.deepEqual(status.querySelector(".tip") ? lines(status) : [], tip, why);
    assert.equal(rowOf("Development").querySelectorAll(".plat-sub").length, 0, `${why}: no second line`);
  }
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: { state: "valid", expires: new Date(Date.now() + 5 * 86400000 + 3600000).toISOString() } }));
  assert.ok(rowOf("Development").querySelector(".plat-deploys .status").classList.contains("warn"), "a token near its end is in amber");
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-deploys .status")).slice(0, 2), ["Expires in 5 days", "Can deploy"]);
  platformsPage.hide();
});

test("what the playgrounds deployed in an environment is its state, by name for one and by count for several, and listed in its popover", async () => {
  const record = (place, created) => ({ place, platform: "My Account", environment: "Development", created });
  const hotels = { id: "acme-hostels", name: "Acme Hostels API", deployed: [record("cloud-eu/env-development", [{ what: "APIs", count: 3 }]), record("other/env-x", [{ what: "APIs", count: 1 }])] };
  const bank = { id: "acme-bakery", name: "Acme Bakery", deployed: [record("cloud-eu/env-development", [{ what: "APIs", count: 2 }])] };
  const empty = { id: "none", name: "None", deployed: [record("cloud-eu/env-development", [])] };
  store.state = state({ playgrounds: [hotels, empty] });
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  assert.equal(deploysOf("Development"), "Acme Hostels API deployed");
  const status = () => rowOf("Development").querySelector(".plat-deploys .status");
  assert.deepEqual(lines(status()), ["Acme Hostels API deployed", "Can deploy", `Expires ${on("2027-01-31")}`, "Acme Hostels API: 3 APIs"], "reachable by keyboard, with the rest of what is known");
  assert.equal(rowOf("Development").querySelectorAll("[title]").filter((el) => /ACME/.test(el.getAttribute("title"))).length, 0);
  store.state = state({ playgrounds: [hotels, bank] });
  platformsPage.render(store.state);
  assert.equal(deploysOf("Development"), "2 playgrounds deployed");
  assert.deepEqual(lines(status()), ["2 playgrounds deployed", "Can deploy", `Expires ${on("2027-01-31")}`, "Acme Hostels API: 3 APIs", "Acme Bakery: 2 APIs"]);
  store.state = state({ playgrounds: [{ ...hotels, deployed: [record("cloud-eu/env-test", [{ what: "APIs", count: 3 }])] }] });
  platformsPage.render(store.state);
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Development", "Test"], "what was deployed there makes it an environment in use, with no token and no gateway");
  assert.equal(deploysOf("Test"), "Acme Hostels API deployed");
  assert.deepEqual(lines(rowOf("Test").querySelector(".plat-deploys .status")).slice(0, 2), ["Acme Hostels API deployed", "Needs a Cloud Token"], "what it lacks, in its popover");
  assert.equal(primaryOf("Test"), "Create gateway");
  store.state = state();
  platformsPage.hide();
});

// The one action of an environment: the gateway to create or to start, then the Cloud Token to add or replace, then the gateway to stop
const ACTIONS = [
  ["a gateway that runs here, a token, nothing to do but stop it", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], VALID, "Stop gateway", "stop-gateway"],
  ["a gateway that runs here and no token", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], undefined, "Add Cloud Token", "add-token"],
  ["a gateway that runs here and an expired token", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], { state: "expired" }, "Replace Cloud Token", "replace-token"],
  ["a gateway that runs here and a refused token", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], { state: "refused" }, "Replace Cloud Token", "replace-token"],
  ["a gateway that does not run", ADDON(STOPPED), [gateway({ here: true })], VALID, "Start gateway", "start-gateway"],
  ["a gateway that does not run and no token: the gateway is first", ADDON(STOPPED), [gateway({ here: true })], undefined, "Start gateway", "start-gateway"],
  ["a gateway that another one runs here", ADDON(), [gateway({ here: false })], VALID, "Start gateway", "start-gateway"],
  ["a gateway connected elsewhere that the Lab keeps", ADDON(), [gateway({ connection: "CONNECTED" })], VALID, "Start gateway", "start-gateway"],
  ["a gateway connected elsewhere that it does not", ADDON(), [gateway({ connection: "CONNECTED", kept: false })], VALID, "Replace gateway", "create-gateway"],
  ["a gateway that runs here and lacks a license", ADDON({ state: { key: "off", label: "" }, note: "Needs a license", note_tone: "warn", note_file: ".env" }), [gateway({ here: true })], VALID, "Add license", "add-license"],
  ["a gateway that runs here, started outside the Lab UI", ADDON({ action: { do: "start", label: "Turn on", enabled: true, reason: null, dialog: null },
    secondary: { do: "stop", label: "Turn off", enabled: true, reason: null, dialog: null } }), [gateway({ here: true, connection: "CONNECTED" })], VALID, "Stop gateway", "stop-gateway"],
  ["a token and no gateway", ADDON(), [], VALID, "Create gateway", "create-gateway"],
  ["nothing to run gateways", null, [gateway({ here: true })], VALID, "", null],
  ["nothing to run gateways and no token", null, [gateway({ here: true })], undefined, "Add Cloud Token", "add-token"],
];

test("an environment offers one action, the first that applies", async () => {
  for (const [why, addon, gateways, token, word, action] of ACTIONS) {
    store.state = state({ addons: addon ? [addon] : [] });
    await open(ok(gateways, { Development: token }));
    assert.equal(primaryOf("Development"), word, why);
    assert.equal(primary("Development")?.getAttribute("data-do") ?? null, action, why);
  }
  store.state = state();
  platformsPage.hide();
});

test("every environment in use has its action and a labeled More, and the actions of every row are in the same columns", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID, Test: VALID }));
  for (const name of ["Development", "Test"]) {
    const actions = rowOf(name).querySelector(".plat-actions");
    assert.deepEqual(actions.children.filter((c) => c.tag).map(said), [primaryOf(name), "More"], name);
    assert.equal(envMore(name).getAttribute("aria-label"), `More for ${name}`);
    assert.equal(envMore(name).getAttribute("aria-haspopup"), "true");
  }
  assert.ok(rowOf("Development").querySelector("[data-do]").classList.contains("plat-primary"));
  platformsPage.hide();
});

test("Start and Stop are the add-on's own action: they carry its key and show it at work, and wait for Docker", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  const stop = primary("Development");
  assert.equal(stop.getAttribute("data-act-for"), "addon:hybrid");
  assert.equal(stop.getAttribute("data-act-verb"), "stop start");
  assert.ok(stop.hasAttribute("data-busy"), "it waits for the running job");
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  assert.equal(primary("Development").getAttribute("data-act-for"), "addon:hybrid");
  store.state = state();
  platformsPage.hide();
});

test("Stop is disabled with the reason of the add-on while it cannot be used", async () => {
  store.state = state({ addons: [ADDON({ action: { do: "stop", label: "Stop", enabled: false, reason: "Wait for Start Acme Hostels", dialog: null } })] });
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  assert.equal(isOff(primary("Development")), true);
  assert.equal(offWhy(primary("Development")), "Wait for Start Acme Hostels", "a button that cannot be used says why");
  store.state = state();
  platformsPage.hide();
});

// What More offers beside the action: the gateway that runs here can be stopped, its logs read and its address copied, a Cloud Token added or
// replaced, then what removes: the token the Lab keeps, the gateway in Cloud, and both at once. The console is the account's, not the environment's.
const MORE = [
  ["a gateway that runs here and a valid token", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], VALID,
    ["Show logs", "Copy address", "Replace Cloud Token", "Remove Cloud Token", "Remove gateway", "Stop using Development"]],
  ["a gateway that runs here and no token: Stop is in the menu", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], undefined,
    ["Stop gateway", "Show logs", "Copy address", "Remove gateway"]],
  ["a gateway that runs here and a refused token", ADDON(), [gateway({ here: true, connection: "CONNECTED" })], { state: "refused" },
    ["Stop gateway", "Show logs", "Copy address", "Remove Cloud Token", "Remove gateway", "Stop using Development"]],
  ["a gateway that does not run: it answers nowhere", ADDON(STOPPED), [gateway({ here: true })], VALID, ["Show logs", "Replace Cloud Token", "Remove Cloud Token", "Remove gateway", "Stop using Development"]],
  ["a token and no gateway: one thing to remove", ADDON(), [], VALID, ["Replace Cloud Token", "Remove Cloud Token"]],
  ["a token and nothing to run gateways", null, [gateway({ here: true })], VALID, ["Replace Cloud Token", "Remove Cloud Token", "Remove gateway", "Stop using Development"]],
  // The token is the row's action while it is expired: what the gateway needs comes to More, free to use (gatewayWhy has nothing to say with an add-on)
  ["an expired token and no gateway: it is created from here", ADDON(), [], { state: "expired" }, ["Create gateway", "Remove Cloud Token"]],
  ["an expired token and a gateway that another machine runs: it is made again from here", ADDON(), [gateway({ connection: "CONNECTED", kept: false })], { state: "expired" },
    ["Replace gateway", "Remove Cloud Token", "Remove gateway"]],
];

test("More offers the rest of what can be done in an environment, in groups, what removes last", async () => {
  for (const [why, addon, gateways, token, offered] of MORE) {
    store.state = state({ addons: addon ? [addon] : [], services: { "apim-hybrid-gateway": { state: RUNNING } } });
    await open(ok(gateways, { Development: token }));
    assert.deepEqual(menu(envMore("Development")), offered, why);
    assert.deepEqual(entries().filter((entry) => entry.disabled || entry.getAttribute("aria-disabled") === "true").map(label), [], `${why}: nothing waits`);
    close();
  }
  store.state = state();
  platformsPage.hide();
});

test("an environment with nothing to offer beside its action has no More", async () => {
  store.state = state({ addons: [] });
  await open(ok([], {}, NAMES), [PLATFORM]);
  const record = (place, created) => ({ place, platform: "My Account", environment: "Development", created });
  store.state = state({ addons: [], playgrounds: [{ id: "h", name: "Acme Hostels API", deployed: [record("cloud-eu/env-development", [{ what: "APIs", count: 3 }])] }] });
  platformsPage.render(store.state);
  assert.equal(primaryOf("Development"), "Add Cloud Token");
  assert.equal(envMore("Development"), null, "an empty menu is not offered");
  store.state = state();
  platformsPage.hide();
});

test("the entries of More that remove are in the danger tone and say what they do; none opens a console, which is the account's", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  menu(envMore("Development"));
  const tone = (name) => entries().find((e) => label(e) === name).classList.contains("danger");
  assert.deepEqual(["Remove Cloud Token", "Remove gateway", "Stop using Development", "Replace Cloud Token", "Copy address"].map(tone), [true, true, true, false, false]);
  const hint = (name) => text(entries().find((e) => label(e) === name).querySelector(".menu-hint"));
  assert.equal(hint("Remove Cloud Token"), "Deploys stop", "what it does, on one line");
  assert.equal(hint("Remove gateway"), "Deleted in Gravitee Cloud", "what it does, not its name again");
  assert.equal(hint("Stop using Development"), "Removes its token and gateway");
  assert.equal(hint("Copy address"), "localhost:8090");
  assert.equal(entries().filter((e) => e.tag === "a").length, 0, "no link to the same console as the card's button");
  close();
  platformsPage.hide();
});

test("Copy address copies where the gateway answers, and says so", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  const copied = [];
  Object.defineProperty(globalThis.navigator ??= {}, "clipboard", { value: { writeText: async (value) => { copied.push(value); } }, configurable: true });
  menu(envMore("Development"));
  page.ids.toasts.replaceChildren();
  await choose("Copy address");
  assert.deepEqual(copied, ["localhost:8090"]);
  assert.deepEqual(toasts(), ["Copied"]);
  platformsPage.hide();
});

// --- The hybrid gateways ---------------------------------------------------------------

test("every hybrid gateway of the account is a row of its list under the environments: the one that runs here first, then the connected ones, then the others", async () => {
  await open(ok([
    gateway({ id: "g3", name: "old", environmentId: "env-production", kept: false, connection: "NOT_CONNECTED", domains: [] }),
    gateway({ id: "g2", name: "elsewhere", environmentId: "env-test", kept: false, connection: "CONNECTED", version: "4.12.21" }),
    gateway({ id: "g1", name: "dev", here: true, connection: "CONNECTED" }),
    gateway({ id: "g4", name: "kept", environmentId: "env-development", connection: "NOT_CONNECTED" }),
    gateway({ id: "h1", name: "hosted", type: "MANAGED" })], { Development: VALID }));
  const band = section.querySelector("[data-gateways-of]");
  assert.deepEqual([band.tag, text(band), band.getAttribute("tabindex"), band.getAttribute("class")], ["h3", "Hybrid gateways", "-1", "stack-sep plat-gw-band"], "a heading on the band of a group");
  const table = section.querySelector(".plat-gw-table");
  assert.deepEqual([table.getAttribute("role"), table.getAttribute("aria-label")], ["table", "Hybrid gateways of My Account"]);
  assert.deepEqual(table.querySelectorAll(".plat-heads > span").map(said), ["Gateway", "Environment", "State", "Version", "Domains", ""]);
  assert.deepEqual(table.querySelectorAll(".plat-heads > span").map((el) => el.getAttribute("role")), Array(6).fill("columnheader"));
  assert.deepEqual(gwRows().map(gwSaid), [
    ["dev", "Development", "Running here", "4.12.20", "localhost:8090, apim-hybrid-gateway:8082"],
    ["elsewhere", "Test", "Connected elsewhere", "4.12.21", "localhost:8090, apim-hybrid-gateway:8082"],
    ["kept", "Development", "Stopped here", "4.12.20", "localhost:8090, apim-hybrid-gateway:8082"],
    ["old", "Production", "Not connected", "4.12.20", "—"],
  ], "a hosted gateway is none of the Lab's; one that runs nowhere is Not connected, gray");
  assert.deepEqual(gwRows().map((r) => r.getAttribute("role")), ["row", "row", "row", "row"]);
  assert.deepEqual(gwRows().map((r) => r.querySelector(".dot-status").getAttribute("class")), ["dot-status healthy", "dot-status stopped", "dot-status stopped", "dot-status off"]);
  assert.deepEqual(gwRows().map((r) => r.querySelector(".plat-gw-state .status").classList.contains("muted")), [false, true, true, true], "quiet while it does not run here");
  assert.equal(gwRow("dev").querySelector(".plat-gw-ver .s-version").getAttribute("class"), "s-version", "mono, as every version");
  assert.equal(gwRow("dev").querySelector(".plat-gw-dom .s-version").getAttribute("data-tip-cut"), "", "cut, and said whole in its tooltip");
  // What to do: Stop for the one that runs here, Start for the one the Lab keeps, nothing but More for the others; each named for its gateway
  const actions = (r) => [r.querySelector("[data-gw-do]")?.getAttribute("aria-label") ?? "", r.querySelectorAll("[data-gw-more]").length];
  assert.deepEqual(gwRows().map(actions), [["Stop gateway, dev", 1], ["", 1], ["Start gateway, kept", 1], ["", 1]]);
  assert.deepEqual(gwRows().map((r) => r.querySelector("[data-gw-do]")?.dataset.actFor ?? ""), ["addon:hybrid", "", "addon:hybrid", ""], "the add-on's own job, wherever it was started");
  assert.deepEqual(gwRows().map((r) => r.querySelectorAll(".plat-actions > *").length), [2, 1, 2, 1], "the slot of the action is kept on a row that has none");
  assert.deepEqual(menu(gwRow("dev").querySelector("[data-gw-more]")), ["Show logs", "Copy address", "Remove gateway"]);
  close();
  assert.deepEqual(menu(gwRow("old").querySelector("[data-gw-more]")), ["Remove gateway"]);
  close();
  platformsPage.hide();
});

test("a gateway of the list is stopped, started and removed with the environment's own code, and its address copied", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "test", environmentId: "env-test" })], { Development: VALID }));
  // Start swaps the one that runs here, after saying so
  assert.equal(gwRow("test").querySelector('[data-gw-do="start"]').getAttribute("data-tip"), "Stops the gateway of Development");
  server.plan = { dialog: null };
  gwRow("test").querySelector('[data-gw-do="start"]').click();
  await settle();
  assert.deepEqual(posted("/start").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g2/start"]);
  // Stop is the add-on's
  gwRow("lab").querySelector('[data-gw-do="stop"]').click();
  await settle();
  assert.deepEqual(posted("/stop").map((c) => c.path), ["/api/addons/hybrid/stop"]);
  // Copy address
  const copied = [];
  Object.defineProperty(globalThis.navigator ??= {}, "clipboard", { value: { writeText: async (value) => { copied.push(value); } }, configurable: true });
  menu(gwRow("lab").querySelector("[data-gw-more]"));
  page.ids.toasts.replaceChildren();
  await choose("Copy address");
  assert.deepEqual([copied, toasts()], [["localhost:8090"], ["Copied"]]);
  // Remove asks first, in the words of the environment's Remove gateway
  menu(gwRow("test").querySelector("[data-gw-more]"));
  await choose("Remove gateway");
  assert.equal(effects().title, "Remove gateway of Test");
  await answer(true);
  assert.deepEqual(posted("/delete").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g2/delete"]);
  platformsPage.hide();
});

test("a gateway Cloud deleted while it runs here is a row of the list, in red, which offers to replace it", async () => {
  await open({ ...ok([]), orphan: { gateway: "gone", environmentId: "env-development" } });
  assert.deepEqual(gwRows().map((r) => gwSaid(r).slice(0, 3)), [["gone", "Development", "Removed in Cloud"]]);
  assert.equal(gwRow("gone").querySelector(".status").getAttribute("class"), "status err");
  assert.equal(gwRow("gone").querySelector("[data-gw-do]").getAttribute("aria-label"), "Replace gateway, gone");
  assert.deepEqual(menu(gwRow("gone").querySelector("[data-gw-more]")), ["Show logs"], "its logs, as it still runs here; nothing to remove: Cloud has it no more");
  close();
  platformsPage.hide();
});

test("an account with no hybrid gateway says so in the one empty block, with the Set up of its first environment", async () => {
  await open(ok([], { Test: VALID }));
  const empty = section.querySelector(".plat-gws .empty");
  assert.equal(said(empty), "No hybrid gateway Set up Development");
  const setUp = empty.querySelector("button");
  assert.deepEqual([setUp.getAttribute("class"), setUp.dataset.do, setUp.dataset.env], ["btn", "set-up", "env-development"], "a plain button, the Set up of the rows");
  assert.equal(section.querySelectorAll(".plat-gw-table").length, 0, "no table of no row");
  // Without an add-on that runs gateways there is nothing to set up for one
  store.state = state({ addons: [] });
  await open(ok([], { Test: VALID }));
  assert.equal(said(section.querySelector(".plat-gws .empty")), "No hybrid gateway");
  store.state = state();
  platformsPage.hide();
});

test("with several accounts each card has the list of its own gateways", async () => {
  const other = { ...PLATFORM, id: "cloud-lab", name: "Client Lab" };
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }), [PLATFORM, other]);
  assert.deepEqual(cards().map((c) => c.querySelector(".plat-gw-table")?.getAttribute("aria-label")), ["Hybrid gateways of My Account", "Hybrid gateways of Client Lab"]);
  assert.deepEqual(cards().map((c) => c.querySelectorAll("[data-gateways-of]").map((band) => band.dataset.gatewaysOf)), [["cloud-eu"], ["cloud-lab"]]);
  platformsPage.hide();
});

test("keyboard focus stays on a gateway's control when the list is drawn again", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "test", environmentId: "env-test" })], { Development: VALID }));
  gwRow("test").querySelector("[data-gw-do]").focus();
  const old = gwRow("test").querySelector("[data-gw-do]");
  store.state = state({ playgrounds: [{ id: "h", name: "Acme Hostels API", deployed: [{ place: "cloud-eu/env-test", platform: "My Account", environment: "Test", created: [{ what: "APIs", count: 3 }] }] }] });
  platformsPage.render(store.state);
  assert.ok(!old.isConnected, "drawn again");
  assert.equal(page.focused(), gwRow("test").querySelector("[data-gw-do]"));
  store.state = state();
  platformsPage.hide();
});

// --- An account that cannot be read ----------------------------------------------------

test("a token the platform refuses is a notice in red under the head: its environments cannot be listed, why, and the one fix", async () => {
  await open({ state: "invalid", message: "Gravitee Cloud refuses this token", environments: [], gateways: [] });
  const problem = card().querySelector(".notice");
  assert.equal(said(problem), "Environments cannot be listed Gravitee Cloud refuses this token Replace Account Token");
  assert.equal(section.querySelectorAll(".plat-table").length, 0, "no table of what is not there");
  assert.ok(problem.classList.contains("err"));
  assert.equal(problem.querySelector(".notice-actions .btn").getAttribute("data-account"), "cloud-eu");
  assert.equal(card().querySelectorAll("[data-do]").length, 0, "nothing is offered in what is not reached");
  assert.equal(card().querySelectorAll("[data-account-more]").length, 1, "the card's menu is still there");
  problem.querySelector("[data-account]").click();
  await flush();
  assert.equal(text(field("account-token").closest("form").querySelector("h2")), "Replace Account Token");
  field("account-token").closest("form").querySelector("[data-cancel]").click();
  assert.equal(problem.querySelectorAll("[data-recheck]").length, 0, "asking again would change nothing: Check again is in the menu");
  platformsPage.hide();
});

test("a platform that does not answer says so, with the reason, and offers to check again only", async () => {
  await open({ state: "unreachable", message: "eu.cloud.gravitee.io refuses the connection", environments: [], gateways: [] });
  const problem = card().querySelector(".notice");
  assert.equal(said(problem), "Environments cannot be listed eu.cloud.gravitee.io refuses the connection Check again");
  assert.ok(problem.classList.contains("warn"));
  assert.equal(problem.querySelectorAll("[data-account]").length, 0);
  const reads = server.checks;
  problem.querySelector("[data-recheck]").click();
  await flush();
  assert.equal(server.checks, reads + 1);
  platformsPage.hide();
});

test("a read that fails is the platform's state, and an account with no environment says so", async () => {
  Object.assign(server, { platforms: [PLATFORM], calls: [] });
  const read = globalThis.fetch;
  globalThis.fetch = async (path, options) => (path.endsWith("/check") ? reply({ error: "Lab UI offline" }, 500) : read(path, options));
  platformsPage.show([]);
  await flush();
  globalThis.fetch = read;
  assert.equal(text(card().querySelector(".plat-state .pill")), "Unreachable");
  assert.equal(said(card().querySelector(".notice")), "Environments cannot be listed Lab UI offline Check again");
  await open(ok([], {}, []));
  assert.equal(said(card().querySelector(".notice.calm")), "No environment in this account Check again");
  platformsPage.hide();
});

// --- Reading again ---------------------------------------------------------------------

test("a gateway that runs here and has not connected is read again every 5 seconds, until it has and until the page is left", async () => {
  await open(ok([gateway({ here: true })], { Development: VALID }));
  const first = server.checks;
  mock.timers.tick(5000);
  await flush();
  assert.equal(server.checks, first + 1, "its add-on runs and Cloud has not seen it");
  server.check = ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID });
  mock.timers.tick(5000);
  await flush();
  assert.equal(gatewayOf("Development"), "Running here");
  const connected = server.checks;
  mock.timers.tick(15000);
  await flush();
  assert.equal(server.checks, connected, "once it is connected it is not read again");
  server.check = ok([gateway({ here: true })], { Development: VALID });
  mock.timers.tick(5000);
  await flush();
  platformsPage.hide();
  const left = server.checks;
  mock.timers.tick(20000);
  await flush();
  assert.equal(server.checks, left, "a page that was left reads nothing");
});

test("a gateway that does not run is not read again: nothing is waited for", async () => {
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true }), gateway({ id: "g2", name: "other", environmentId: "env-test" })], { Development: VALID }));
  const first = server.checks;
  mock.timers.tick(20000);
  await flush();
  assert.equal(server.checks, first);
  store.state = state();
  platformsPage.hide();
});

test("every card is read again while a gateway job runs, and once more after it ended", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  const idle = server.checks;
  mock.timers.tick(10000);
  await flush();
  assert.equal(server.checks, idle, "everything is connected and nothing runs");
  store.state = state({ job: JOB });
  mock.timers.tick(5000);
  await flush();
  mock.timers.tick(5000);
  await flush();
  assert.equal(server.checks, idle + 2, "a read at each tick while the job runs");
  store.state = state({ job: { ...JOB, done: true, ok: true } });
  mock.timers.tick(5000);
  await flush();
  assert.equal(server.checks, idle + 3, "one more once it ended");
  mock.timers.tick(10000);
  await flush();
  assert.equal(server.checks, idle + 3, "then nothing");
  store.state = state({ job: { ...JOB, for: "playground:acme" } });
  mock.timers.tick(5000);
  await flush();
  assert.equal(server.checks, idle + 3, "another job is not a gateway's");
  store.state = state();
  platformsPage.hide();
});

test("an address that names a platform brings its card into view, focused", async () => {
  Object.assign(server, { platforms: [PLATFORM], check: ok([]), checks: 0 });
  platformsPage.show(["cloud-eu"]);
  await flush();
  mock.timers.tick(1);
  await flush();
  assert.equal(card().scrolled.length, 1, "brought into view");
  assert.equal(page.focused(), card());
  platformsPage.hide();
});

// --- Gateways: create, start, stop, remove ---------------------------------------------

async function click(selector, root = section) {
  root.querySelector(selector).click();
  await settle();
}

test("Create gateway opens the form of the environment, titled as the button, and a gateway made that way is read again", async () => {
  await open(ok([], { Test: VALID }));
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  assert.equal(text(create.querySelector("h2")), "Create gateway for Test", "the environment is in the title");
  assert.deepEqual(selected("gateway-environment"), ["env-test"]);
  assert.ok(field("gateway-environment").closest(".field").hidden, "it is not asked: the row said it");
  create.querySelector("[data-cancel]").click();
});

test("Start asks the server what it does and says it first: Restarts, Serves, Stops serving; Start runs the job", async () => {
  await open(ok([gateway({ id: "g2", name: "second", environmentId: "env-test" })], { Test: VALID }));
  server.plan = { dialog: { starts: [], stops: [], turns_off: [], keeps: [], restarts: [{ name: "Hybrid gateway", kind: "addon", id: "hybrid" }],
    serves: [{ kind: "environment", id: "cloud-eu/env-test", name: "Test (Client lab)" }],
    stops_serving: [{ kind: "environment", id: "cloud-eu/env-development", name: "Development (My Account)" }] } };
  await click('[data-do="start-gateway"]');
  assert.deepEqual(server.calls.filter((c) => c.path.endsWith("/plan")).map((c) => [c.method, c.path]), [["GET", "/api/platforms/cloud-eu/gateways/g2/plan"]]);
  assert.deepEqual(effects(), { title: "Start second", action: "Start",
    steps: [["Restarts", ["Hybrid gateway Add-on"], "Recreated to take the changes"]],
    notes: [["Serves", ["Test (Client lab)"]], ["Stops serving", ["Development (My Account)"]]] });
  assert.equal(posted("/start").length, 0, "nothing runs before it is confirmed");
  await answer(true);
  assert.deepEqual(posted("/start").map((c) => [c.path, c.body]), [["/api/platforms/cloud-eu/gateways/g2/start", { accept: [] }]]);
  assert.ok(server.checks > 1, "the platform is read again");
});

test("a Start that is canceled does nothing", async () => {
  await open(ok([gateway({ here: true })], { Development: VALID }));
  store.state = state({ addons: [ADDON(STOPPED)] });
  platformsPage.render(store.state);
  server.plan = { dialog: { restarts: [{ name: "Hybrid gateway", kind: "addon", id: "hybrid" }],
    serves: [{ kind: "environment", id: "a/b", name: "x" }], stops_serving: [{ kind: "environment", id: "c/d", name: "y" }] } };
  await click('[data-do="start-gateway"]');
  await answer(false);
  assert.equal(posted("/start").length, 0);
  store.state = state();
});

test("a Start that does nothing beyond the add-on asks nothing", async () => {
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  for (const plan of [null, { dialog: null }, { starts: [], restarts: [], serves: [], stops_serving: [] }]) {   // null: what the server sends for a plan that asks nothing
    server.plan = plan;
    server.calls = [];
    await click('[data-do="start-gateway"]');
    assert.equal(posted("/start").length, 1, JSON.stringify(plan));
    assert.deepEqual(posted("/start")[0].body, { accept: [] });
  }
  store.state = state();
});

test("a plan that stops or turns off something sends back what it showed", async () => {
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  server.plan = { dialog: { turns_off: [{ name: "Jade", kind: "addon", id: "jade" }] } };
  await click('[data-do="start-gateway"]');
  assert.deepEqual(effects().steps, [["Turns off", ["Jade Add-on"], ""]]);
  await answer(true);
  assert.deepEqual(posted("/start")[0].body, { accept: ["addon:jade"] });
  store.state = state();
});

test("a refused plan is said in a toast", async () => {
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  page.ids.toasts.replaceChildren();
  server.calls = [];
  const fetchWas = globalThis.fetch;
  globalThis.fetch = async (path, options) => (path.endsWith("/plan") ? reply({ error: "Wait for Start Acme Hostels" }, 400) : fetchWas(path, options));
  await click('[data-do="start-gateway"]');
  globalThis.fetch = fetchWas;
  assert.deepEqual(toasts(), ["Wait for Start Acme Hostels"]);
  assert.equal(posted("/start").length, 0);
  store.state = state();
});

test("Stop is the add-on's own, with its own dialog", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  await click('[data-do="stop-gateway"]');
  assert.deepEqual(posted("/stop").map((c) => [c.path, c.body]), [["/api/addons/hybrid/stop", { accept: [] }]]);
  store.state = state({ addons: [ADDON({ action: { do: "stop", label: "Stop", enabled: true, reason: null,
    dialog: { stops: [{ name: "Acme Hostels", kind: "playground", id: "acme-hostels" }], turns_off: [], starts: [], restarts: [], keeps: [] } } })] });
  platformsPage.render(store.state);
  server.calls = [];
  await click('[data-do="stop-gateway"]');
  assert.deepEqual(effects().steps, [["Stops", ["Acme Hostels Playground"], "Turned off too"]]);
  assert.equal(effects().title, "Stop Hybrid gateway");
  await answer(true);
  assert.deepEqual(posted("/stop")[0].body, { accept: ["playground:acme-hostels"] });
  store.state = state();
});

test("Stop from More, when the token is the action, is the same Stop", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })]));
  server.calls = [];
  menu(envMore("Development"));
  await choose("Stop gateway");
  assert.deepEqual(posted("/stop").map((c) => c.path), ["/api/addons/hybrid/stop"]);
});

test("another gateway of an environment is started and removed from its row of the list, not from the environment's More", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "second" })], { Development: VALID }));
  assert.deepEqual(menu(envMore("Development")), ["Show logs", "Copy address", "Replace Cloud Token", "Remove Cloud Token", "Remove gateway", "Stop using Development"]);
  close();
  server.plan = { dialog: null };
  gwRow("second").querySelector('[data-gw-do="start"]').click();
  await settle();
  assert.deepEqual(posted("/start").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g2/start"]);
  menu(gwRow("second").querySelector("[data-gw-more]"));
  await choose("Remove gateway");
  assert.equal(effects().title, "Remove gateway of Development", "named for its environment: its own name could be the Lab's");
  await answer(false);
  platformsPage.hide();
});

test("Remove gateway says it removes it in Gravitee Cloud, and turns off the add-on when it is the one that runs here", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "second", environmentId: "env-test" })], { Development: VALID, Test: VALID }));
  menu(envMore("Development"));
  await choose("Remove gateway");
  assert.deepEqual(effects(), { title: "Remove gateway of Development", action: "Remove", steps: [["Removes", ["lab"], "In Gravitee Cloud"], ["Turns off", ["Hybrid gateway Add-on"], ""]], notes: [] });
  page.ids.toasts.replaceChildren();
  await answer(true);
  assert.deepEqual(posted("/delete").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g1/delete"]);
  assert.deepEqual(toasts(), ["Removed"]);
  menu(envMore("Test"));
  await choose("Remove gateway");
  assert.deepEqual(effects().steps, [["Removes", ["second"], "In Gravitee Cloud"]], "another gateway turns nothing off");
  await answer(false);
  assert.equal(posted("/delete").length, 1, "a canceled one removes nothing");
});

// --- Cloud Tokens ----------------------------------------------------------------------

test("Remove Cloud Token says the Lab forgets it and the platform keeps it", async () => {
  await open(ok([], { Development: { state: "valid" } }));
  menu(envMore("Development"));
  await choose("Remove Cloud Token");
  assert.deepEqual(effects(), { title: "Remove Cloud Token", action: "Remove", steps: [["Removes", ["Cloud Token of Development"], ""]],
    notes: [["Stays there", ["the token in Gravitee Cloud"]]] });
  await answer(true);
  assert.deepEqual(posted("/token/delete").map((c) => [c.path, c.body]), [["/api/platforms/cloud-eu/environments/env-development/token/delete", {}]]);
  assert.deepEqual(toasts().slice(-1), ["Removed"]);
});

test("Remove account says what goes with it, what it turns off and what stays there", async () => {
  store.state = state({ playgrounds: [{ id: "acme-hostels", name: "Acme Hostels", deployed: [
    { place: "cloud-eu/env-development", platform: "My Account", environment: "Development", created: [{ what: "APIs", count: 3 }] },
    { place: "other/env-x", platform: "Other", environment: "X", created: [{ what: "APIs", count: 1 }] }] }] });
  await open(ok([gateway({ here: true }), gateway({ id: "g2", name: "second", environmentId: "env-test" })], { Development: VALID }));
  menu(accountMore());
  await choose("Remove account");
  assert.deepEqual(effects(), { title: "Remove My Account", action: "Remove",
    steps: [["Removes", ["My Account"], "With its tokens"], ["Turns off", ["Hybrid gateway Add-on"], ""]],
    notes: [["Stays there", ["lab", "second", "Acme Hostels: 3 APIs in Development"]]] });
  await answer(true);
  assert.deepEqual(posted("/delete").map((c) => c.path), ["/api/platforms/cloud-eu/delete"]);
  store.state = state();
});

test("Remove account turns nothing off when none of its gateways runs here", async () => {
  await open(ok([gateway()]));
  menu(accountMore());
  await choose("Remove account");
  assert.deepEqual(effects().steps, [["Removes", ["My Account"], "With its tokens"]]);
  assert.deepEqual(effects().notes, [["Stays there", ["lab"]]]);
  await answer(false);
});

// --- The dialogs -----------------------------------------------------------------------

// A browser tells a form's button whether the required fields are filled, and a select its value, which the stub does not
const field = (id) => page.doc.querySelector(`#${id}`);
const formOf = (id) => {
  const form = field(id).closest("form");
  form.checkValidity = () => form.querySelectorAll("[required]").every((el) => el.disabled || el.value || el.id === "use-environment");   // the stub gives a select no value
  return form;
};
for (const id of ["platform-token", "rename-name", "account-token", "gateway-name", "cloud-token-secret", "replace-cloud-token-secret", "use-token"]) formOf(id);
const typed = (form, id, value) => {
  field(id).value = value;
  form.dispatchEvent(new Ev("input"));
};
const labels = (form) => form.querySelectorAll("label").map(text);
const isOpen = (form) => form.parent.hasAttribute("open");
const submit = async (form) => {
  form.dispatchEvent(new Ev("submit"));
  await settle();
};
const selected = (id) => field(id).querySelectorAll("option").filter((o) => o.hasAttribute("selected")).map((o) => o.getAttribute("value"));

const form = formOf("platform-token");
// The wizard: one dialog, its steps in its head, a form for each step of which one shows, one footer under each
const wizard = form.closest("dialog");
const step2 = () => formOf("use-token");
const titleOf = (panel) => text(panel.closest("dialog").querySelector("h2"));
// A step of the head as it is heard: its words, without what is hidden or drawn only (its number, its check), and its state
const steps = () => wizard.querySelectorAll(".wizard-steps li").map((li) => [li.children
  .filter((child) => !(child.tag && (child.hidden || child.getAttribute("aria-hidden")))).map((child) => child.textContent).join("").trim(),
li.getAttribute("aria-current") || (li.classList.contains("done") ? "done" : "")]);
const opened = () => page.doc.querySelectorAll("dialog").filter((d) => d.hasAttribute("open"));
const shots = (panel) => panel.querySelectorAll(".connect-shot img").filter((img) => !img.hidden).map((img) => img.getAttribute("src"));
const lineOf = (panel) => panel.querySelectorAll(".connect-steps .step-body").map(text);
const connectWith = async (check) => {
  section.querySelector("[data-new]").click();
  typed(form, "platform-token", "a-token");
  typed(form, "platform-account", "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46");
  server.platforms = [PLATFORM];
  server.check = check;
  await submit(form);
};

test("Connect Gravitee Cloud is one large dialog of two steps, opened on the account: where to get both values, then the fields", async () => {
  await open(null, []);
  section.querySelector(".plat-lonely [data-new]").click();
  assert.equal(titleOf(form), "Connect Gravitee Cloud");
  assert.ok(wizard.classList.contains("platform-wizard"), "the large dialog");
  assert.equal(wizard.querySelector(".wizard-steps").getAttribute("aria-label"), "Steps");
  assert.equal(wizard.querySelector(".wizard-steps").hidden, false);
  assert.deepEqual(steps(), [["Account", "step"], ["Environment", ""]], "the first step is the current one");
  assert.ok(isOpen(form) && !form.hidden && step2().hidden, "the account's form shows, the environment's waits in the same dialog");
  assert.equal(wizard.querySelector("[data-close]").getAttribute("aria-label"), "Close", "the cross of the large dialogs");
  // Where the values are: the console, its registration for a person with no account, the two lines the picture numbers
  assert.equal(field("platform-open").getAttribute("href"), "https://eu.cloud.gravitee.io");
  assert.equal(text(field("platform-open")), "Open Gravitee Cloud");
  const register = form.querySelector(".connect-register a");
  assert.equal(text(form.querySelector(".connect-ask")), "Do you already have a Gravitee Cloud account?");
  assert.equal(text(form.querySelector(".connect-register")), "If not, start a 14-day trial");
  assert.equal(register.getAttribute("href"), "https://eu.cloud.gravitee.io/?createUser=true");
  assert.equal(register.getAttribute("target"), "_blank");
  assert.deepEqual(lineOf(form), ["In Settings, Account Tokens, generate an Account Token and copy it", "Copy the Account ID, at the top of the same page"],
    "numbered as the picture numbers them: 1 the token, 2 the Account ID");
  assert.deepEqual(shots(form), ["/img/cloud-account-token.png"]);
  assert.match(form.querySelector(".connect-shot img").getAttribute("alt"), /Account Token.*Account ID/);
  assert.equal(form.querySelectorAll(".segmented").length, 0, "one picture: nothing to choose");
  assert.equal(form.querySelector(".connect-toggle").getAttribute("aria-expanded"), "false", "folded on a phone");
  // The fields
  assert.deepEqual(labels(form), ["Account Token", "Account ID", "URL"]);
  assert.equal(form.querySelectorAll("#platform-name").length, 0, "no name: it is the account's, read from Gravitee Cloud");
  assert.equal(form.querySelectorAll("select").length, 0, "there is one kind: nothing to choose");
  assert.equal(field("platform-account").hasAttribute("placeholder"), false, "the two fields are built alike");
  assert.equal(field("platform-url").closest("details").getAttribute("id"), "platform-more", "the URL is folded");
  assert.equal(text(form.querySelector("#platform-more summary")), "More options");
  assert.equal(field("platform-url").value, "https://eu.cloud.gravitee.io");
  assert.equal(field("platform-token").getAttribute("type"), "password");
  // The secret has its eye: it shows what was pasted, and hides it again
  const eye = field("platform-token").closest(".search").querySelector("[data-reveal-secret]");
  assert.equal(eye.getAttribute("aria-label"), "Show");
  eye.click();
  assert.deepEqual([field("platform-token").getAttribute("type"), eye.getAttribute("aria-label")], ["text", "Hide"]);
  eye.click();
  assert.deepEqual([field("platform-token").getAttribute("type"), eye.getAttribute("aria-label")], ["password", "Show"]);
  assert.equal(form.querySelectorAll("[data-reveal-secret]").length, 1, "one eye, for the one secret of the form");
  assert.equal(field("platform-account").value, "");
  assert.equal(page.focused(), field("platform-token"), "the keyboard starts on the first field");
  // One footer: the refusal at the left of Cancel and Connect
  const foot = form.querySelector(".dialog-foot");
  assert.ok(foot.querySelector(".create-problem") && foot.querySelector("[data-cancel]") && foot.querySelector("[data-act-for]"));
  assert.equal(text(foot.querySelector("[data-cancel]")), "Cancel");
  const go = form.querySelector("[data-act-for]");
  assert.equal(text(go.querySelector(".pbtn-face")), "Connect");
  assert.equal(isOff(go), true, "the token and the Account ID are missing");
  typed(form, "platform-token", "a-token");
  assert.equal(isOff(go), true, "the Account ID is missing");
  typed(form, "platform-account", "my-account");
  assert.equal(isOff(go), true, "an Account ID is a UUID");
  assert.equal(offWhy(go), "The Account ID is a UUID");
  assert.equal(field("platform-account-wrong").hidden, false, "said under its field, in plain words");
  assert.equal(field("platform-account-wrong").closest(".field"), field("platform-account").closest(".field"), "under its own field, at the gap of its label");
  assert.equal(text(field("platform-account-wrong")), "The Account ID is a UUID, as the Account Tokens page shows it");
  assert.equal(field("platform-account").getAttribute("aria-invalid"), "true", "a screen reader hears it is wrong, and why");
  assert.equal(field("platform-account").getAttribute("aria-describedby"), "platform-account-wrong");
  assert.equal(field("platform-account-wrong").getAttribute("role"), "status");
  typed(form, "platform-account", "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46");
  assert.equal(isOff(go), false);
  assert.equal(field("platform-account-wrong").hidden, true);
  assert.equal(field("platform-account").hasAttribute("aria-invalid"), false);
  assert.equal(form.querySelector(".create-problem").getAttribute("role"), "alert", "a refusal is said at once");
  // The console's link follows the address under More options
  typed(form, "platform-url", "http://localhost:9990");
  field("platform-url").dispatchEvent(new Ev("input"));
  assert.equal(field("platform-open").getAttribute("href"), "http://localhost:9990");
  form.querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("it sends what was typed, trimmed, with no name when there is none, and the person sees a refusal in the dialog", async () => {
  await open(ok([]));
  section.querySelector("[data-new]").click();
  typed(form, "platform-token", "  a-token ");
  typed(form, "platform-account", " 4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46 ");
  server.calls = [];
  server.refusal = "The token or the Account ID is not right";
  await submit(form);
  assert.deepEqual(posted("/api/platforms").map((c) => c.body), [{ url: "https://eu.cloud.gravitee.io", token: "a-token",
    account: "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46" }], "no kind and no name is sent, and what was typed is trimmed");
  const problem = form.querySelector(".create-problem");
  assert.equal(problem.hidden, false);
  assert.equal(text(problem), "The token or the Account ID is not right", "the server's words, in the dialog");
  assert.ok(problem.closest(".dialog-foot"), "in the footer, at the left of the buttons");
  assert.equal(field("platform-token").value, "  a-token ", "what was typed stays to be corrected");
  assert.ok(isOpen(form) && !form.hidden, "the dialog stays open on its step");
  assert.deepEqual(steps(), [["Account", "step"], ["Environment", ""]]);
  typed(form, "platform-account", "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f47");
  assert.equal(problem.hidden, true, "typing again clears it");
  server.refusal = "This account is already connected";
  await submit(form);
  assert.equal(text(problem), "This account is already connected");
  server.refusal = null;
  server.calls = [];
  page.ids.toasts.replaceChildren();
  await submit(form);
  assert.equal(posted("/api/platforms")[0].body.name, undefined, "the name is the account's");
  assert.ok(isOpen(form), "once the platform is registered the dialog goes on to its second step");
  assert.equal(field("platform-token").value, "", "and the Account Token, kept by the Lab, is cleared");
  step2().querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("Rename, from the card's menu, asks the name alone, filled, and saves it once it changed", async () => {
  await open(ok([]));
  menu(accountMore());
  await choose("Rename");
  const renaming = field("rename-name").closest("form");
  assert.equal(text(renaming.querySelector("h2")), "Rename My Account");
  assert.deepEqual(labels(renaming), ["Name"]);
  assert.equal(field("rename-name").value, "My Account");
  const save = renaming.querySelector("[data-act-for]");
  assert.equal(text(save.querySelector(".pbtn-face")), "Save");
  assert.equal(isOff(save), true, "nothing changed");
  typed(renaming, "rename-name", " Work ");
  assert.equal(isOff(save), false);
  server.calls = [];
  await submit(renaming);
  assert.deepEqual(posted("/api/platforms/cloud-eu/name").map((c) => c.body), [{ name: "Work" }]);
  assert.ok(!isOpen(renaming));
  platformsPage.hide();
});

test("once connected the same dialog goes on to the environment, in place: the account shows behind it, its step is checked, and nothing opens another dialog", async () => {
  await open(null, []);
  assert.equal(cards().length, 0);
  page.ids.toasts.replaceChildren();
  await connectWith(ok([]));
  assert.equal(cards().length, 1, "the page shows the account behind the dialog");
  assert.equal(quiet().length, 3, "its environments");
  assert.deepEqual(opened(), [wizard], "one dialog, never closed between the steps");
  assert.ok(form.hidden && !step2().hidden, "the second step in the place of the first");
  assert.equal(titleOf(step2()), "Connect Gravitee Cloud");
  assert.deepEqual(steps(), [["Done: Account", "done"], ["Environment", "step"]], "the account is checked, the environment is the current step");
  assert.equal(wizard.querySelector(".wizard-steps li .wizard-num use").getAttribute("href"), "#i-check");
  assert.equal(text(field("connect-said")), "My Account connected. Step 2 of 2, Environment", "a screen reader hears where it is");
  assert.deepEqual(toasts(), [], "the check of its step says it is connected: no toast says it again");
  assert.equal(page.focused(), field("use-environment"), "the keyboard is on the first field of the step");
  assert.equal(field("use-choice").hidden, false, "the environment is chosen here");
  assert.deepEqual(selected("use-environment"), ["env-development"], "Development first");
  assert.equal(field("use-gateway-name").value, "Gravitee Lab - Development");
  assert.equal(text(field("use-env-name")), "Development", "the line of the Cloud Token names it");
  assert.equal(field("use-open").getAttribute("href"), `${PLATFORM.url}/cloud/accounts/my-account/settings/cloud-tokens`, "the Cloud Tokens of this account");
  // Optional: Skip for now and Set up; no Back, the account exists
  assert.deepEqual(step2().querySelectorAll(".dialog-foot button").map((b) => (b.querySelector(".pbtn-face") ? text(b.querySelector(".pbtn-face")) : text(b))),
    ["Skip for now", "Set up"]);
  server.calls = [];
  step2().querySelector("[data-cancel]").click();
  await settle();
  assert.deepEqual(opened(), [], "skipped: the dialog closes");
  assert.equal(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).length, 0, "nothing is sent, and the account stays");
  assert.equal(cards().length, 1);
  platformsPage.hide();
});

test("an account with no environment, or whose environments cannot be read, ends the wizard with a toast instead of an empty step", async () => {
  await open(null, []);
  page.ids.toasts.replaceChildren();
  await connectWith(ok([], {}, []));
  assert.deepEqual(opened(), [], "no empty step");
  assert.deepEqual(toasts(), ["My Account connected, with no environment yet"]);
  assert.equal(cards().length, 1, "the account stays");
  page.ids.toasts.replaceChildren();
  await connectWith({ state: "unreachable", message: "eu.cloud.gravitee.io does not answer in time", environments: [], gateways: [] });
  assert.deepEqual(opened(), []);
  assert.deepEqual(toasts(), ["My Account connected", "eu.cloud.gravitee.io does not answer in time"], "connected, and why it goes no further");
  platformsPage.hide();
});

test("the pictures of the Cloud Token, one at a time, follow the left side: the segmented control, a numbered line, the Cloud Token field", async () => {
  await open(null, []);
  await connectWith(ok([]));
  const panel = step2(), radios = () => panel.querySelectorAll(".segmented [role=radio]");
  assert.equal(panel.querySelector(".segmented").getAttribute("role"), "radiogroup");
  assert.deepEqual(radios().map((r) => [text(r), r.getAttribute("aria-checked")]), [["1 Generate", "true"], ["2 Choose the environment", "false"]]);
  assert.deepEqual(shots(panel), ["/img/cloud-token-generate.png"], "one picture at a time");
  assert.deepEqual(lineOf(panel), ["In Settings, Cloud Tokens, generate a Cloud Token",
    "Choose the type Automation and the environment Development, confirm, then copy the token"], "numbered as the pictures");
  assert.deepEqual(panel.querySelectorAll(".connect-steps .step").map((li) => li.classList.contains("shown")), [true, false], "the line of the picture shown is marked");
  radios()[1].click();
  assert.deepEqual(shots(panel), ["/img/cloud-token-environment.png"]);
  assert.deepEqual(radios().map((r) => r.getAttribute("aria-checked")), ["false", "true"]);
  assert.deepEqual(panel.querySelectorAll(".connect-steps .step").map((li) => li.classList.contains("shown")), [false, true]);
  field("use-token").focus();
  assert.deepEqual(shots(panel), ["/img/cloud-token-generate.png"], "the Cloud Token field shows where it is made");
  panel.querySelectorAll(".connect-steps .step")[1].click();
  assert.deepEqual(shots(panel), ["/img/cloud-token-environment.png"], "a numbered line shows its picture");
  // On a phone the pictures are folded under the fields until asked
  const toggle = panel.querySelector(".connect-toggle");
  assert.equal(text(toggle), "Show where to find it");
  assert.equal(toggle.getAttribute("aria-controls"), "use-shots");
  toggle.click();
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  panel.querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("an Account ID that is connected already is said before anything is sent: whose it is, no way to connect it again, and the way to replace its Account Token", async () => {
  await open(ok([]));
  for (const dialog of opened()) dialog.close();
  section.querySelector("[data-new]").click();
  await flush();   // the accounts registered are read when the form opens
  typed(form, "platform-token", "a-token");
  typed(form, "platform-account", PLATFORM.account.id.toUpperCase());
  const note = field("platform-account-known");
  assert.equal(note.hidden, false);
  assert.equal(text(note), "Already connected as My Account. Replace its Account Token");
  const go = form.querySelector("[data-act-for]");   // (a button left busy by an earlier test is judged by its attribute)
  assert.equal(go.getAttribute("aria-disabled"), "true");
  // Another Account ID is a new account: nothing is said of it
  typed(form, "platform-account", "d7a34c19-52f0-4b86-8e1d-3c9a60b7f215");
  assert.equal(note.hidden, true);
  form.parent.close();
});

test("Escape and Cancel drop the secret that was typed", async () => {
  await open(ok([]));
  section.querySelector("[data-new]").click();
  typed(form, "platform-token", "a-secret");
  form.querySelector("[data-cancel]").click();
  assert.ok(!isOpen(form));
  assert.equal(field("platform-token").value, "", "the dialog holds no secret once closed");
  section.querySelector("[data-new]").click();
  typed(form, "platform-token", "a-secret");
  wizard.querySelector("[data-close]").click();
  assert.ok(!isOpen(form), "the cross closes it as Cancel does");
  assert.equal(field("platform-token").value, "");
  platformsPage.hide();
});

test("Replace Account Token opens one field, checked against the Account ID the platform keeps", async () => {
  await open(ok([]));
  openConnection();
  connectionOf().querySelector("[data-connection-replace]").click();
  const replace = formOf("account-token");
  assert.equal(text(replace.querySelector("h2")), "Replace Account Token");
  assert.deepEqual(labels(replace), ["Account Token"]);
  assert.equal(field("account-token").getAttribute("type"), "password");
  const go = replace.querySelector("[data-act-for]");
  assert.equal(text(go.querySelector(".pbtn-face")), "Replace");
  assert.equal(isOff(go), true);
  typed(replace, "account-token", " a-new-token ");
  assert.equal(isOff(go), false);
  server.calls = [];
  server.refusal = "Gravitee Cloud refuses this token";
  await submit(replace);
  assert.equal(text(replace.querySelector(".create-problem")), "Gravitee Cloud refuses this token");
  assert.ok(isOpen(replace));
  server.refusal = null;
  page.ids.toasts.replaceChildren();
  const reads = server.checks;
  await submit(replace);
  assert.deepEqual(posted("/token").map((c) => [c.path, c.body]), [["/api/platforms/cloud-eu/token", { token: "a-new-token" }],
    ["/api/platforms/cloud-eu/token", { token: "a-new-token" }]]);
  assert.ok(!isOpen(replace));
  assert.equal(field("account-token").value, "");
  assert.deepEqual(toasts(), ["Replaced"]);
  assert.ok(server.checks > reads, "the platform is read again with the new token");
});

test("Create gateway opens the form of the environment: its name in the title, no choice of environment, a name of its own, and Create and start", async () => {
  await open(ok([], { Test: VALID }));
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  assert.equal(text(create.querySelector("h2")), "Create gateway for Test");
  assert.deepEqual(labels(create), ["Environment", "Name"], "the domains come from the add-on");
  assert.ok(field("gateway-environment").closest(".field").hidden, "the row said which environment");
  assert.deepEqual(field("gateway-environment").querySelectorAll("option").map(text), NAMES);
  assert.deepEqual(selected("gateway-environment"), ["env-test"]);
  assert.equal(field("gateway-name").value, "Gravitee Lab - Test", "told from the gateways of the other environments");
  assert.equal(text(create.querySelector("[data-act-for] .pbtn-face")), "Create and start", "a gateway made here runs here");
  assert.ok(field("gateway-replaces").hidden, "nothing else runs here: nothing to say");
  field("gateway-environment").value = "env-test";   // what a browser reads from the selected option
  typed(create, "gateway-name", " Lab 2 ");
  server.calls = [];
  page.ids.toasts.replaceChildren();
  const reads = server.checks, states = server.states;
  await submit(create);
  assert.deepEqual(posted("/gateways").map((c) => [c.path, c.body]), [["/api/platforms/cloud-eu/gateways", { environmentId: "env-test", name: "Lab 2" }]]);
  assert.ok(!isOpen(create));
  assert.ok(server.states > states, "the other pages hear of it: the state is read again");
  assert.deepEqual(toasts(), ["Created"]);
  assert.equal(page.doc.querySelectorAll("dialog").filter((d) => d.hasAttribute("open")).length, 0, "no closing notice");
  assert.ok(server.checks > reads, "the platform is read again");
});

test("a gateway made from the page is started at once, through the plan and the confirmation of a Start", async () => {
  await open(ok([], { Test: VALID }));
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  field("gateway-environment").value = "env-test";
  // Cloud lists it once it is made; the add-on does not run it yet
  server.check = ok([gateway({ id: "g-made", name: "Gravitee Lab - Test", environmentId: "env-test", kept: true })], { Test: VALID });
  store.state = state({ addons: [ADDON(STOPPED)] });
  server.plan = { dialog: { restarts: [{ name: "Hybrid gateway", kind: "addon", id: "hybrid" }] } };
  server.calls = [];
  await submit(create);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/gateways"], "nothing runs before the plan is shown");
  assert.deepEqual(server.calls.filter((c) => c.path.endsWith("/plan")).map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g-made/plan"]);
  assert.equal(effects().title, "Start Gravitee Lab - Test");
  await answer(true);
  assert.deepEqual(posted("/start").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/g-made/start"]);
  store.state = state();
  platformsPage.hide();
});

test("a gateway is not started for a license the add-on lacks: the row says so and offers the license", async () => {
  await open(ok([], { Test: VALID }));
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  field("gateway-environment").value = "env-test";
  server.check = ok([gateway({ id: "g-made", environmentId: "env-test", here: true })], { Test: VALID });
  store.state = state({ addons: [ADDON({ state: { key: "off", label: "" }, note: "Needs a license", note_tone: "warn", note_file: ".env" })] });
  server.calls = [];
  await submit(create);
  assert.equal(server.calls.filter((c) => c.path.endsWith("/plan")).length, 0);
  assert.equal(primaryOf("Test"), "Add license");
  store.state = state();
  platformsPage.hide();
});

test("another environment's gateway that runs here is said before a new one takes its place, in the form and on the Start of the row", async () => {
  // What the last test's button said of its action is over, by the clock of the page; a click it left waiting is let go
  mock.timers.tick(5000);
  await flush();
  const now = Date.now;
  Date.now = () => now() + 5000;
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "second", environmentId: "env-test" })], { Development: VALID, Test: VALID }));
  Date.now = now;
  assert.equal(offWhy(primary("Test")), "Stops the gateway of Development");
  assert.equal(offWhy(primary("Development")), "", "its own Stop needs no warning");
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  await click('[data-do="set-up"][data-env="env-test"]');
  assert.equal(text(field("use-replaces")), "The gateway of Development runs here now: starting this one stops it");
  assert.ok(!field("use-replaces").hidden);
  field("use-gateway-name").closest("form").querySelector("[data-cancel]").click();
  store.state = state({ addons: [ADDON(STOPPED)] });
  await open(ok([gateway({ here: true })], { Development: VALID }));
  await click('[data-do="set-up"][data-env="env-test"]');
  assert.ok(field("use-replaces").hidden, "an add-on that does not run serves nothing now");
  field("use-gateway-name").closest("form").querySelector("[data-cancel]").click();
  store.state = state();
  platformsPage.hide();
});

test("a gateway the platform refuses stays in its form, in the server's words", async () => {
  await open(ok([], { Test: VALID }));
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  field("gateway-environment").value = "env-test";
  server.refusal = "Invalid token";
  await submit(create);
  assert.equal(text(create.querySelector(".create-problem")), "Invalid token");
  assert.ok(isOpen(create));
  create.querySelector("[data-cancel]").click();
});

test("what opens Create hybrid gateway from elsewhere reads the environments when it is not given them, and keeps its own title", async () => {
  await open(ok([]));
  let done = 0;
  await openCreateGateway({ platform: { id: "cloud-eu", name: "Gravitee Cloud" }, environmentId: "env-production", done: () => { done += 1; } });
  const create = formOf("gateway-name");
  assert.ok(isOpen(create));
  assert.equal(text(create.querySelector("h2")), "Create hybrid gateway for Production", "the title of the button that opened it, and the environment");
  assert.ok(field("gateway-environment").closest(".field").hidden);
  assert.equal(field("gateway-name").value, "Gravitee Lab - Production");
  assert.equal(text(create.querySelector("[data-act-for] .pbtn-face")), "Create", "nothing starts it: the playground's Start does");
  assert.deepEqual(selected("gateway-environment"), ["env-production"]);
  assert.deepEqual(field("gateway-environment").querySelectorAll("option").map(text), NAMES, "asked of the platform");
  field("gateway-environment").value = "env-production";
  await submit(create);
  assert.equal(done, 1, "what opened it hears once it is created");
  assert.equal(posted("/gateways")[0].body.environmentId, "env-production");
});

test("Add Cloud Token is for the environment of its row and asks the token, and where to find it; Replace Cloud Token is the same form with its own button", async () => {
  await open(ok([gateway({ id: "g2", name: "second", environmentId: "env-test", here: true, connection: "CONNECTED" })]));
  await click('[data-do="add-token"]');
  const add = formOf("cloud-token-secret");
  assert.equal(text(add.querySelector("h2")), "Add Cloud Token for Test", "the environment is in the title");
  assert.deepEqual(labels(add), ["Environment", "Cloud Token"]);
  assert.ok(field("cloud-token-environment").closest(".field").hidden, "the row said which environment: it is not asked");
  assert.deepEqual(bubble(field("cloud-token-secret").closest(".field").querySelector(".tip")),
    ["Lets the Lab create APIs in this environment for a playground. Create one in Gravitee Cloud: Settings, Cloud Tokens, type Automation."],
    "the Lab cannot create it: the popover of its label says where, under no title that says the label again");
  assert.deepEqual(selected("cloud-token-environment"), ["env-test"]);
  assert.equal(field("cloud-token-secret").getAttribute("type"), "password");
  assert.equal(text(add.querySelector("[data-act-for] .pbtn-face")), "Add");
  assert.equal(isOff(add.querySelector("[data-act-for]")), true);
  field("cloud-token-environment").value = "env-test";
  typed(add, "cloud-token-secret", " a-cloud-token ");
  server.calls = [];
  server.refusal = "This Cloud Token is for Production, not Test";
  await submit(add);
  assert.equal(text(add.querySelector(".create-problem")), "This Cloud Token is for Production, not Test");
  server.refusal = null;
  page.ids.toasts.replaceChildren();
  await submit(add);
  assert.deepEqual(posted("/token")[1], { method: "POST", path: "/api/platforms/cloud-eu/environments/env-test/token", body: { token: "a-cloud-token" } });
  assert.ok(!isOpen(add));
  assert.equal(field("cloud-token-secret").value, "");
  assert.deepEqual(toasts(), ["Added"]);

  await open(ok([gateway({ id: "g2", name: "second", environmentId: "env-test", here: true, connection: "CONNECTED" })], { Test: { state: "expired" } }));
  await click('[data-do="replace-token"]');
  const replace = formOf("replace-cloud-token-secret");
  assert.equal(text(replace.querySelector("h2")), "Replace Cloud Token for Test");
  assert.deepEqual(labels(replace), ["Environment", "Cloud Token"]);
  assert.ok(field("replace-cloud-token-environment").closest(".field").hidden);
  assert.ok(bubble(field("replace-cloud-token-secret").closest(".field").querySelector(".tip"))[0].endsWith("Settings, Cloud Tokens, type Automation."), "the same term, where it is made");
  assert.deepEqual(selected("replace-cloud-token-environment"), ["env-test"]);
  assert.equal(text(replace.querySelector("[data-act-for] .pbtn-face")), "Replace");
  field("replace-cloud-token-environment").value = "env-test";
  typed(replace, "replace-cloud-token-secret", "a-newer-token");
  server.calls = [];
  page.ids.toasts.replaceChildren();
  await submit(replace);
  assert.deepEqual(posted("/token").map((c) => [c.path, c.body]), [["/api/platforms/cloud-eu/environments/env-test/token", { token: "a-newer-token" }]]);
  assert.deepEqual(toasts(), ["Replaced"]);
});

test("what opens Add Cloud Token from elsewhere takes the environment it names, reads them when not given, and hears when it is kept", async () => {
  await open(ok([]));
  let done = 0;
  await openCloudToken({ platform: { id: "cloud-eu", name: "Gravitee Cloud" }, environmentId: "env-production", done: () => { done += 1; } });
  const add = formOf("cloud-token-secret");
  assert.ok(isOpen(add));
  assert.deepEqual(selected("cloud-token-environment"), ["env-production"]);
  assert.deepEqual(field("cloud-token-environment").querySelectorAll("option").map(text), NAMES);
  field("cloud-token-environment").value = "env-production";
  typed(add, "cloud-token-secret", "a-cloud-token");
  await submit(add);
  assert.equal(done, 1);
  await openCloudToken({ platform: PLATFORM, environments: [{ id: "e/1", name: "Odd" }], environmentId: "e/1", replacing: true, done: () => {} });
  const replace = formOf("replace-cloud-token-secret");
  field("replace-cloud-token-environment").value = "e/1";
  typed(replace, "replace-cloud-token-secret", "x");
  server.calls = [];
  await submit(replace);
  assert.equal(posted("/token")[0].path, "/api/platforms/cloud-eu/environments/e%2F1/token", "an environment is one segment of the path");
});

test("a platform that cannot be read says so in a toast and opens no form", async () => {
  Object.assign(server, { check: null });
  const fetchWas = globalThis.fetch;
  globalThis.fetch = async (path, options) => (path.endsWith("/check") ? reply({ error: "Lab UI offline" }, 500) : fetchWas(path, options));
  page.ids.toasts.replaceChildren();
  const form = formOf("cloud-token-secret");
  form.parent.close();
  await openCloudToken({ platform: PLATFORM, environmentId: "x", done: () => {} });
  globalThis.fetch = fetchWas;
  assert.ok(!isOpen(form));
  assert.deepEqual(toasts(), ["Lab UI offline"]);
});

// --- Use an environment ----------------------------------------------------------------

const use = formOf("use-token");
const boxes = () => ({ gateway: field("use-gateway"), deploys: field("use-deploys") });
const tick = (box, on) => {
  boxes()[box].checked = on;
  use.dispatchEvent(new Ev("input", { target: boxes()[box] }));
  boxes()[box].dispatchEvent(new Ev("input"));
};
const goes = () => use.querySelector("[data-act-for]");

test("Set up opens one dialog with the two steps in order, a gateway that runs here and the Cloud Token that allows deploys, both ticked", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  assert.equal(titleOf(use), "Set up Test");
  assert.equal(use.closest("dialog"), wizard, "the dialog of the wizard's second step, alone");
  assert.ok(wizard.querySelector(".wizard-steps").hidden, "no steps");
  assert.ok(form.hidden && !use.hidden);
  assert.ok(field("use-choice").hidden, "the environment is the row's: not asked");
  assert.equal(text(use.querySelector("[data-cancel]")), "Cancel");
  assert.deepEqual(shots(use), ["/img/cloud-token-generate.png"], "with the pictures of where a Cloud Token is made");
  assert.equal(text(field("use-env-name")), "Test");
  assert.deepEqual(labels(use).filter(Boolean), ["Environment", "Run a gateway here", "Name", "Allow deploys", "Cloud Token"]);
  assert.equal(boxes().gateway.checked, true);
  assert.equal(boxes().deploys.checked, true);
  assert.equal(field("use-gateway-name").value, "Gravitee Lab - Test");
  assert.ok(field("use-replaces").hidden, "nothing else runs here");
  assert.equal(field("use-token").getAttribute("type"), "password");
  assert.equal(field("use-token").value, "");
  assert.equal(text(goes().querySelector(".pbtn-face")), "Set up");
  assert.equal(isOff(goes()), true, "the token is not typed yet");
  assert.equal(offWhy(goes()), "A required field is empty");
  assert.equal(use.querySelectorAll(".field-note:not([hidden])").length, 0, "no helper sentence: the token's term says where it comes from");
  assert.equal(use.querySelectorAll(".tip-btn").length, 2, "the two terms are explained where they appear");
  assert.deepEqual(use.querySelectorAll(".tip").map((t) => bubble(t)[0]), ["Hybrid gateway", "Cloud Token"]);
  use.querySelector("[data-cancel]").click();
});

test("each step may be left out: its field closes, and the button needs one step", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  typed(use, "use-token", "a-token");
  assert.equal(isOff(goes()), false);
  tick("deploys", false);
  assert.equal(field("use-token").disabled, true, "a step that is not asked has no field");
  assert.ok(field("use-deploys-fields").hidden, "its fields and its lines are not shown, rather than disabled");
  assert.equal(isOff(goes()), false, "the gateway alone");
  tick("gateway", false);
  assert.equal(isOff(goes()), true);
  assert.equal(offWhy(goes()), "Choose a step");
  tick("deploys", true);
  assert.equal(isOff(goes()), false);
  assert.ok(!field("use-deploys-fields").hidden);
  assert.equal(field("use-gateway-name").disabled, true);
  assert.ok(field("use-gateway-fields").hidden, "the Name shows only while its box is ticked");
  use.querySelector("[data-cancel]").click();
});

test("it keeps the Cloud Token first and then makes the gateway, each with what the form already takes, and says it is set up", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  typed(use, "use-token", " a-cloud-token ");
  typed(use, "use-gateway-name", " Lab 2 ");
  server.calls = [];
  page.ids.toasts.replaceChildren();
  const reads = server.checks, states = server.states;
  await submit(use);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => [c.path, c.body]),
    [["/api/platforms/cloud-eu/environments/env-test/token", { token: "a-cloud-token" }],
      ["/api/platforms/cloud-eu/gateways", { environmentId: "env-test", name: "Lab 2" }]]);
  assert.ok(!isOpen(use));
  assert.equal(field("use-token").value, "", "the token is cleared");
  assert.deepEqual(toasts(), ["Test is set up"]);
  assert.ok(server.states > states, "the other pages hear of it");
  assert.ok(server.checks > reads, "the platform is read again");
});

test("the gateway alone sends no token, and the token alone makes no gateway", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  tick("deploys", false);
  server.calls = [];
  await submit(use);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/gateways"]);
  await click('[data-do="set-up"][data-env="env-test"]');
  tick("gateway", false);
  typed(use, "use-token", "a-cloud-token");
  server.calls = [];
  await submit(use);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/environments/env-test/token"]);
});

test("a token that is refused stops it before anything is made, in the server's words", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  typed(use, "use-token", "a-wrong-token");
  server.refuse = (path) => (path.endsWith("/token") ? "This Cloud Token is for Production, not Test" : "");
  server.calls = [];
  await submit(use);
  assert.equal(text(use.querySelector(".create-problem")), "This Cloud Token is for Production, not Test");
  assert.ok(isOpen(use));
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/environments/env-test/token"], "no gateway was made");
  assert.equal(field("use-token").value, "a-wrong-token", "what was typed stays to be corrected");
  use.querySelector("[data-cancel]").click();
});

test("a gateway that is refused after the token was kept leaves that step done, so that sending again does the gateway only", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  typed(use, "use-token", "a-cloud-token");
  server.refuse = (path) => (path.endsWith("/gateways") ? "This token is not allowed to do that: it needs an account owner" : "");
  server.calls = [];
  await submit(use);
  assert.equal(text(use.querySelector(".create-problem")), "This token is not allowed to do that: it needs an account owner");
  assert.ok(isOpen(use));
  assert.equal(field("use-deploys-done").hidden, false, "the step that is done says so");
  assert.equal(boxes().deploys.disabled, true);
  assert.equal(boxes().deploys.checked, false);
  assert.equal(isOff(goes()), false, "the gateway is left to do");
  server.refuse = null;
  server.calls = [];
  await submit(use);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/gateways"], "the token is not sent again");
  assert.ok(!isOpen(use));
});

test("without an add-on that runs gateways the dialog offers the token only", async () => {
  store.state = state({ addons: [] });
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  assert.ok(field("use-gateway-step").hidden);
  assert.equal(boxes().gateway.checked, false);
  assert.equal(field("use-gateway-name").disabled, true);
  typed(use, "use-token", "a-cloud-token");
  assert.equal(isOff(goes()), false);
  server.calls = [];
  await submit(use);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path), ["/api/platforms/cloud-eu/environments/env-test/token"]);
  store.state = state();
});

test("in the wizard, a part done for an environment keeps it: what is left is for the same one", async () => {
  await open(null, []);
  await connectWith(ok([]));
  typed(step2(), "use-token", "a-cloud-token");
  server.refuse = (path) => (path.endsWith("/gateways") ? "This token is not allowed to do that" : "");
  await submit(step2());
  assert.ok(isOpen(step2()));
  assert.equal(field("use-deploys-done").hidden, false);
  assert.equal(field("use-environment").disabled, true, "the token is kept for Development: the environment is no longer chosen");
  server.refuse = null;
  step2().querySelector("[data-cancel]").click();
  await settle();
  await connectWith(ok([]));
  assert.equal(field("use-environment").disabled, false, "a new wizard chooses again");
  step2().querySelector("[data-cancel]").click();
  await settle();
  platformsPage.hide();
});

// After the tests that read the button of Set up at rest: it says a while how it went (its clock is not the tests')
test("another environment chosen in the wizard is another gateway name and the one the Cloud Token is for; Set up sets it up and starts the gateway made", async () => {
  await open(null, []);
  await connectWith(ok([]));
  const choose = field("use-environment");
  choose.value = "env-test";
  choose.dispatchEvent(new Ev("change"));
  assert.equal(field("use-gateway-name").value, "Gravitee Lab - Test");
  assert.equal(text(field("use-env-name")), "Test");
  typed(step2(), "use-token", "a-cloud-token");
  server.calls = [];
  server.check = ok([gateway({ id: server.made, environmentId: "env-test", kept: true })], { Test: VALID });
  page.ids.toasts.replaceChildren();
  await submit(step2());
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !/\/(check|plan|start)$/.test(c.path)).map((c) => [c.path, c.body]),
    [["/api/platforms/cloud-eu/environments/env-test/token", { token: "a-cloud-token" }],
      ["/api/platforms/cloud-eu/gateways", { environmentId: "env-test", name: "Gravitee Lab - Test" }]], "the token first, then the gateway");
  assert.deepEqual(opened().filter((d) => d === wizard), [], "done: the dialog closes");
  assert.deepEqual(toasts(), ["Test is set up"]);
  assert.ok(server.calls.some((c) => c.path.endsWith("/check")), "the platform is read again");
  assert.ok(server.calls.some((c) => c.path === `/api/platforms/cloud-eu/gateways/${server.made}/plan`), "the gateway made here is started, through the plan of a Start");
  await settle();
  mock.timers.tick(2000);   // its button has said how it went
  await settle();
  endJobs();
  platformsPage.hide();
});

// --- What can go wrong, and what a person does with the keyboard --------------------------

test("Cancel closes the Set up dialog and drops what was typed, though information icons come before it", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  assert.ok(use.querySelector("button").classList.contains("tip-btn"), "the first button of the form is an information icon, not Cancel");
  typed(use, "use-token", "a-secret");
  server.calls = [];
  use.querySelector("[data-cancel]").click();
  assert.ok(!isOpen(use), "a click on Cancel closes it");
  assert.equal(field("use-token").value, "");
  assert.equal(server.calls.filter((c) => c.method === "POST").length, 0, "nothing was sent");
  assert.equal(use.querySelectorAll("button").filter((b) => text(b) === "Cancel").length, 1);
  platformsPage.hide();
});

test("a step that was done stays done when the dialog is closed after a refusal: the page reads the platform and says it", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  typed(use, "use-token", "a-cloud-token");
  server.refuse = (path) => (path.endsWith("/gateways") ? "This token is not allowed to do that" : "");
  await submit(use);
  assert.ok(isOpen(use), "the refusal is in the dialog");
  assert.equal(quiet().length, 3, "the page has not been told yet");
  server.refuse = null;
  server.check = ok([], { Test: VALID });
  server.calls = [];
  page.ids.toasts.replaceChildren();
  const states = server.states;
  use.querySelector("[data-cancel]").click();
  await settle();
  assert.ok(!isOpen(use));
  assert.ok(server.calls.some((c) => c.path.endsWith("/check")), "the platform is read again");
  assert.ok(server.states > states, "and the other pages hear of it");
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Test"], "the token that was kept makes it an environment in use");
  assert.deepEqual(toasts().filter((t) => /set up/.test(t)), [], "it is not said set up: the gateway was not made");
  platformsPage.hide();
});

test("a dialog closed with nothing done reads nothing", async () => {
  await open(ok([]));
  await click('[data-do="set-up"][data-env="env-test"]');
  server.calls = [];
  const states = server.states;
  use.querySelector("[data-cancel]").click();
  await settle();
  assert.equal(server.calls.length, 0);
  assert.equal(server.states, states);
  platformsPage.hide();
});

test("a read that fails by itself keeps what the card knew and says the account does not answer; asked again, it says so too", async () => {
  platformsPage.hide();
  await open(ok([gateway({ here: true })], { Development: VALID }));
  assert.equal(gatewayOf("Development"), "Connecting");
  server.check = { state: "unreachable", message: "eu.cloud.gravitee.io does not answer in time", environments: [], gateways: [] };
  mock.timers.tick(5000);
  await flush();
  assert.equal(text(card().querySelector(".plat-state .pill")), "Unreachable");
  assert.equal(said(card().querySelector(".notice")), "Showing what was read before eu.cloud.gravitee.io does not answer in time Check again");
  assert.equal(gatewayOf("Development"), "Connecting", "the gateway that runs here does not vanish with one failed read");
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Development"]);
  assert.deepEqual(quiet().map((r) => text(r.querySelector(".plat-env"))), ["Test", "Production"]);
  server.check = ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID });
  mock.timers.tick(5000);
  await flush();
  assert.equal(card().querySelectorAll(".notice").length, 0, "once it answers, the notice goes");
  assert.equal(gatewayOf("Development"), "Running here");
  assert.equal(text(card().querySelector(".plat-state .pill")), "Connected");
  platformsPage.hide();
});

test("an account that did not answer when first read has no table to keep: it says so, and a refusal is not kept either", async () => {
  const fresh = [{ ...PLATFORM, id: "never-read", name: "Never read" }];   // nothing known of it yet
  await open({ state: "unreachable", message: "refuses the connection", environments: [], gateways: [] }, fresh);
  assert.equal(card().querySelectorAll(".plat-table").length, 0);
  server.check = ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID });
  card().querySelector("[data-recheck]").click();
  await flush();
  assert.equal(envTable().length, 1);
  server.check = { state: "invalid", message: "Gravitee Cloud refuses this token", environments: [], gateways: [] };
  menu(accountMore());
  await choose("Check again");
  assert.equal(card().querySelectorAll(".plat-table").length, 0, "a refusal is the answer, not a moment of trouble");
  assert.equal(text(card().querySelector(".plat-state .pill")), "Account Token refused");
  platformsPage.hide();
});

test("an account that does not answer is read again every 30 seconds, not every 5", async () => {
  platformsPage.hide();
  await open({ state: "unreachable", message: "refuses the connection", environments: [], gateways: [] });
  const first = server.checks;
  for (let i = 0; i < 5; i += 1) {
    mock.timers.tick(5000);
    await flush();
  }
  assert.equal(server.checks, first, "25 seconds: not yet");
  mock.timers.tick(5000);
  await flush();
  assert.equal(server.checks, first + 1, "30 seconds");
  server.check = ok([]);
  for (let i = 0; i < 6; i += 1) {
    mock.timers.tick(5000);
    await flush();
  }
  assert.equal(server.checks, first + 2);
  assert.equal(text(card().querySelector(".plat-state .pill")), "Connected", "and it recovers by itself");
  for (let i = 0; i < 12; i += 1) {
    mock.timers.tick(5000);
    await flush();
  }
  assert.equal(server.checks, first + 2, "then nothing");
  platformsPage.hide();
});

test("a gateway that does not connect is read every 5 seconds for a minute, every 10 for another, then every 30, and said not to be connected", async () => {
  platformsPage.hide();
  await open(ok([gateway({ here: true })], { Development: VALID }));
  const reads = async (ticks) => {
    const before = server.checks;
    for (let i = 0; i < ticks; i += 1) {
      mock.timers.tick(5000);
      await flush();
    }
    return server.checks - before;
  };
  assert.equal(await reads(12), 12, "the first minute");
  assert.equal(gatewayOf("Development"), "Connecting");
  assert.equal(await reads(11), 5, "the second one, every other time");
  assert.equal(gatewayOf("Development"), "Connecting", "not yet, 2 minutes are not over");
  assert.equal(await reads(1), 1);
  assert.equal(gatewayOf("Development"), "Not connected", "after two minutes it says it, with a dot of its own");
  assert.equal(rowOf("Development").querySelector(".plat-gateway .dot-status").className, "dot-status warn");
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-gateway .status")),
    ["lab", "Gravitee Cloud has not seen it for 2 minutes. Its logs say why", "Version 4.12.20", "Address localhost:8090"]);
  assert.equal(await reads(12), 2, "then every 30 seconds");
  assert.ok(menu(envMore("Development")).includes("Show logs"), "the logs are one click away");
  close();
  server.check = ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID });
  await reads(6);
  assert.equal(gatewayOf("Development"), "Running here", "it connects at last");
  assert.equal(await reads(6), 0, "and is not read again");
  platformsPage.hide();
});

test("keyboard focus stays on the control a redraw replaces: the toggle, a row's button, its More", async () => {
  const names = ["Development", "Test", "Production", "Demo", "QA", "Staging"];
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }, names));
  const toggle = section.querySelector(".plat-toggle");
  toggle.focus();
  toggle.click();
  const now = page.focused();
  assert.ok(now && now.isConnected, "focus is on an element of the page, not lost to the body");
  assert.equal(now.getAttribute("data-show"), "cloud-eu");
  assert.equal(text(now), "Show fewer environments");
  // A change of state redraws the rows: the one a person is on is found again
  envMore("Development").focus();
  const old = envMore("Development");
  store.state = state({ playgrounds: [{ id: "h", name: "Acme Hostels API", deployed: [{ place: "cloud-eu/env-development", platform: "My Account", environment: "Development", created: [{ what: "APIs", count: 3 }] }] }] });
  platformsPage.render(store.state);
  assert.ok(!old.isConnected, "the row was drawn again");
  assert.equal(page.focused(), envMore("Development"));
  primary("Development").focus();
  store.state = state();
  platformsPage.render(store.state);
  assert.equal(page.focused().getAttribute("data-do"), "stop-gateway");
  assert.equal(page.focused().getAttribute("data-env"), "env-development");
  section.querySelector('[data-do="set-up"][data-env="env-test"]').focus();
  server.check = ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID, Test: VALID }, names);
  card().querySelector(".plat-tools [data-account-more]").focus();
  menu(accountMore());
  await choose("Check again");
  assert.ok(page.focused() === null || page.focused().isConnected);
  store.state = state();
  platformsPage.hide();
});

test("a gateway nobody here keeps, in an environment the Lab does nothing else with, is listed, and can be removed", async () => {
  const left = gateway({ id: "x1", name: "Leftover", environmentId: "env-test", kept: false, connection: "UNKNOWN_HYBRID", status: "FAILED" });
  const old = gateway({ id: "x2", name: "Old test", environmentId: "env-test", kept: false, connection: "NOT_CONNECTED" });
  await open(ok([left, old]));
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Test"], "it is in use, so that what is in it is seen");
  assert.equal(gatewayOf("Test"), "Not created 2 gateways");
  assert.equal(primaryOf("Test"), "Create gateway");
  assert.deepEqual(menu(envMore("Test")), ["Add Cloud Token"], "its gateways are removed from the list");
  close();
  assert.deepEqual(gwRows().map((r) => gwSaid(r).slice(0, 3)), [["Leftover", "Test", "Failed"], ["Old test", "Test", "Not connected"]]);
  menu(gwRow("Old test").querySelector("[data-gw-more]"));
  await choose("Remove gateway");
  assert.equal(effects().title, "Remove gateway of Test");
  assert.deepEqual(effects().steps, [["Removes", ["Old test"], "In Gravitee Cloud"]]);
  await answer(true);
  assert.deepEqual(posted("/delete").map((c) => c.path), ["/api/platforms/cloud-eu/gateways/x2/delete"]);
  platformsPage.hide();
});

test("a gateway that Cloud deleted while it runs here is said, in red, and offers to replace it", async () => {
  await open({ ...ok([]), orphan: { gateway: "gone", environmentId: "env-development" } });
  assert.deepEqual(used().map((r) => text(r.querySelector(".plat-env"))), ["Development"], "it is not offered as an environment to set up");
  const status = rowOf("Development").querySelector(".plat-gateway .status");
  assert.equal(said(status), "Removed in Cloud");
  assert.deepEqual(detailsOf("Development"), ["It still runs here, with a token Cloud no longer accepts"], "what is left of it, in its popover");
  assert.equal(status.querySelector(".dot-status").className, "dot-status issue");
  assert.deepEqual(lines(status), ["Removed in Gravitee Cloud", "It still runs here, with a token Cloud no longer accepts"]);
  assert.equal(primaryOf("Development"), "Replace gateway");
  assert.deepEqual(menu(envMore("Development")), ["Stop gateway", "Show logs", "Add Cloud Token"], "it is stopped, its logs read; there is nothing to remove in Cloud");
  close();
  await click('[data-do="create-gateway"]');
  assert.equal(text(formOf("gateway-name").querySelector("h2")), "Replace gateway for Development");
  formOf("gateway-name").querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("a gateway that another machine runs says so with its name, offers to replace it, and says who loses it when it is removed", async () => {
  await open(ok([gateway({ connection: "CONNECTED", kept: false, name: "Alice laptop" })], { Development: VALID }));
  assert.equal(gatewayOf("Development"), "Connected elsewhere");
  assert.deepEqual(lines(rowOf("Development").querySelector(".plat-gateway .status")), ["Alice laptop", "Another machine runs it", "Version 4.12.20"]);
  assert.equal(primaryOf("Development"), "Replace gateway");
  menu(envMore("Development"));
  await choose("Remove gateway");
  assert.equal(effects().title, "Remove gateway of Development");
  assert.deepEqual(effects().steps, [["Removes", ["Alice laptop"], "In Gravitee Cloud, connected from another machine"]], "it is not the Lab's: the confirmation says it");
  await answer(false);
  await click('[data-do="create-gateway"]');
  const create = formOf("gateway-name");
  assert.equal(text(create.querySelector("h2")), "Replace gateway for Development");
  assert.equal(field("gateway-name").value, "Gravitee Lab - Development", "not the name of the one it replaces");
  assert.equal(text(create.querySelector("[data-act-for] .pbtn-face")), "Create and start");
  create.querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("Stop using an environment removes its token and its gateways with one confirmation that says what goes and what stays", async () => {
  const hotels = { id: "acme-hostels", name: "Acme Hostels", deployed: [{ place: "cloud-eu/env-development", platform: "My Account", environment: "Development", created: [{ what: "APIs", count: 3 }] }] };
  store.state = state({ playgrounds: [hotels] });
  await open(ok([gateway({ here: true, connection: "CONNECTED" }), gateway({ id: "g2", name: "second" })], { Development: VALID }));
  menu(envMore("Development"));
  await choose("Stop using Development");
  assert.deepEqual(effects(), { title: "Stop using Development", action: "Stop using",
    steps: [["Removes", ["second", "lab"], "In Gravitee Cloud"], ["Removes", ["Cloud Token of Development"], "From the Lab"], ["Turns off", ["Hybrid gateway Add-on"], ""]],
    notes: [["Stays there", ["the token in Gravitee Cloud", "Acme Hostels: 3 APIs"]]] }, "the one that runs here goes last: its removal turns the add-on off");
  server.calls = [];
  page.ids.toasts.replaceChildren();
  await answer(true);
  assert.deepEqual(server.calls.filter((c) => c.method === "POST" && !c.path.endsWith("/check")).map((c) => c.path),
    ["/api/platforms/cloud-eu/environments/env-development/token/delete", "/api/platforms/cloud-eu/gateways/g2/delete", "/api/platforms/cloud-eu/gateways/g1/delete"]);
  assert.deepEqual(toasts(), ["Removed"]);
  store.state = state();
  menu(envMore("Development"));
  await choose("Stop using Development");
  await answer(false);
  assert.equal(posted("/delete").length, 3, "a canceled one removes nothing more");
  platformsPage.hide();
});

test("Stop using says a failure and stops there", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  server.refuse = (path) => (path.endsWith("/gateways/g1/delete") ? "Gravitee Cloud refuses this token" : "");
  menu(envMore("Development"));
  await choose("Stop using Development");
  page.ids.toasts.replaceChildren();
  await answer(true);
  assert.deepEqual(toasts(), ["Gravitee Cloud refuses this token"]);
  platformsPage.hide();
});

test("a Cloud Token near its end says so in amber, in the place of Can deploy", async () => {
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: { state: "valid", expires: new Date(Date.now() + 3 * 86400000 + 3600000).toISOString() } }));
  assert.equal(deploysOf("Development"), "Expires in 3 days");
  assert.equal(lines(rowOf("Development").querySelector(".plat-deploys .status"))[1], "Can deploy");
  assert.equal(rowOf("Development").querySelector(".plat-deploys .dot-status").className, "dot-status warn");
  platformsPage.hide();
});

test("a card's console button names its account, and the menu's entries carry no hint that says nothing", async () => {
  await open(ok([]));
  assert.equal(card().querySelector(".plat-tools a").getAttribute("aria-label"), "Open console, My Account");
  menu(accountMore());
  assert.equal(entries().filter((entry) => entry.querySelector(".menu-hint")).length, 0);
  close();
  await open(ok([], {}, ["Development"]), [PLATFORM, { ...PLATFORM, id: "client-lab", name: "Client Lab" }]);
  assert.deepEqual(cards().map((c) => c.querySelector(".plat-tools a").getAttribute("aria-label")), ["Open console, My Account", "Open console, Client Lab"], "told apart");
  platformsPage.hide();
});

test("the Connect dialog says, in the popover of each label, what each credential is and where it is, and why both are asked", async () => {
  await open(ok([]));
  section.querySelector("[data-new]").click();
  const tipOf = (id) => field(id).closest(".field").querySelector(".tip");
  // The label says the term: the popover is what it means, under no title that says it again
  assert.deepEqual(bubble(tipOf("platform-token")),
    ["Lets the Lab read this account, its environments and its gateways, and create gateways. It deploys nothing: that is the Cloud Token of each environment. In Gravitee Cloud: Settings, Account Tokens."]);
  assert.deepEqual(bubble(tipOf("platform-account")),
    ["The ID Gravitee Cloud shows next to its Account Tokens: Settings, Account Tokens. An Account Token cannot look up its own account, so the Lab asks for its ID too."]);
  assert.equal(tipOf("platform-token").querySelector(".tip-btn").getAttribute("type"), "button");
  assert.equal(form.querySelector("[data-cancel]").closest(".dialog-actions") !== null, true);
  form.querySelector("[data-cancel]").click();
  platformsPage.hide();
});

test("Replace Account Token says where to create it, in the popover of its label", async () => {
  await open(ok([]));
  openConnection();
  connectionOf().querySelector("[data-connection-replace]").click();
  assert.equal(bubble(field("account-token").closest(".field").querySelector(".tip"))[0].endsWith("In Gravitee Cloud: Settings, Account Tokens."), true,
    "the popover of its label says where to create it");
  formOf("account-token").querySelector("[data-cancel]").click();
  platformsPage.hide();
});

// Last: the logs page, once drawn, keeps timers of its own that the clock of the other tests would run
test("Logs lead to the logs of the gateway's service", async () => {
  // The stub's selectors do not know :checked, which the logs page asks of its levels
  const all = page.doc.querySelectorAll;
  page.doc.querySelectorAll = (selector) => all(selector.replace(":checked", ""));
  const logs = page.doc.createElement("section");
  page.doc.body.append(logs);
  logsPage.init(logs);
  await open(ok([gateway({ here: true, connection: "CONNECTED" })], { Development: VALID }));
  menu(envMore("Development"));
  await choose("Show logs");
  page.doc.querySelectorAll = all;
  assert.equal(page.session.entries[page.session.at].hash, "#/logs");
});
