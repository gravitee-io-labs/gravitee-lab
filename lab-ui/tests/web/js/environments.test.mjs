// What the Lab says of an environment of a platform (web/js/platform/environments.js): the rules the Gravitee Cloud page draws and the Dashboard can say
// in the same words, read from the platform's last check and from the state. The contract of environmentStatus: whether the Lab uses the
// environment, the state of its gateway on this machine, whether it can be deployed to, what was deployed there, and the one action. The
// add-on that runs a gateway is chosen by the server (`state.platform.gateways`); with none, the cell says that there is no add-on (the Cloud
// controls with it), or, while the catalogs are not there, why, in the words of the lists.
import assert from "node:assert/strict";
import { test } from "node:test";

import { bubble, installDom, text } from "./dom-stub.mjs";
import { FIRST, catalog, content } from "./fixtures.mjs";

const dom = installDom();
const { store } = await import("../../../web/js/core.js");
const { environmentStatus, gatewayList, addonOf, gatewayGap, gatewayCell, runs, stopOf } = await import("../../../web/js/platform/environments.js");
const { gatewayWhy, runsControl, chooseCore } = await import("../../../web/js/platform/core-choice.js");

const PLATFORM = { id: "cloud-eu", name: "My Account", kind: "cloud" };
const ENV = { id: "env-dev", name: "Development", token: { state: "unset" } };
const gateway = (more) => ({ id: "g1", name: "lab", environmentId: "env-dev", type: "HYBRID", status: "DEPLOYED", connection: "CONNECTED",
  version: "4.12.20", domains: ["localhost:8090", "apim-hyssop-gateway:8082"], here: false, kept: true, ...more });
const ADDON = (more) => ({ id: "hyssop", name: "Hybrid gateway", state: { key: "running" }, note: "", note_tone: "", note_title: "",
  action: { do: "stop", enabled: true, reason: null }, gateway: { platform: "cloud", service: "apim-hyssop-gateway" }, ...more });
const STOPPED = { state: { key: "stopped" }, action: { do: "start", enabled: true, reason: null } };
const dayFrom = (days) => new Date(Date.now() + days * 86400000 + 3600000).toISOString();   // and an hour, which the test takes to run
const lab = (state = {}) => { store.state = { blind: false, addons: [ADDON()], playgrounds: [], platform: { gateways: { cloud: "hyssop" } }, ...state }; };
const status = (gateways = [], token, more = {}, check = {}) => environmentStatus(PLATFORM, { gateways, ...check }, { ...ENV, token: token || ENV.token, ...more });
const VALID = { state: "valid", expires: "2099-01-31T00:00:00Z" };
const brief = (found) => [found.gateway.key, found.gateway.word, found.deploy.key, found.deploy.word, found.action?.do ?? null];

test("while it runs here, the address a person calls, never the name of a container", () => {
  lab();
  const connecting = status([gateway({ here: true, connection: "NOT_CONNECTED" })], VALID).gateway;
  assert.deepEqual([connecting.key, connecting.address], ["connecting", "localhost:8090"], "apim-hyssop-gateway is the name of the container: only the other containers know it");
});

test("a missing license is its own state, in amber, that the action adds: Add license, or Replace license when it expired or lacks a feature", () => {
  const note = (word) => ADDON({ state: { key: "off" }, note: word, note_tone: word === "License expired" ? "err" : "warn", note_title: "Uses: Enterprise features", note_file: ".env" });
  for (const word of ["License expired", "Not in the license"]) {
    lab({ addons: [note(word)] });
    assert.equal(status([gateway({ here: true })], VALID).action.label, "Replace license", word);
  }
});

test("a gateway whose add-on lacks settings says so in amber, never with the green of what runs, and its action opens those settings", () => {
  // The server says what it lacks as causes (its note's), and keeps its Stop: the state is the note's, its popover the card of its own badge
  lab({ addons: [ADDON({ note: "Needs settings", note_tone: "warn", note_title: "Not set: HYSSOP_CLOUD_TOKEN", note_causes: [{ kind: "unset", settings: ["HYSSOP_CLOUD_TOKEN"] }],
    action: { do: "stop", label: "Stop", enabled: true }, secondary: null })] });
  const found = status([gateway({ here: true })], VALID);
  assert.deepEqual([found.gateway.key, found.gateway.dot, found.gateway.word], ["settings", "warn", "Needs settings"]);
  assert.deepEqual(found.gateway.tip, { title: "Needs settings", rows: [{ lines: [[{ code: "HYSSOP_CLOUD_TOKEN" }]] }], foot: "Set it in its settings." },
    "the card the add-on's own badge opens: the settings as chips, where they are set");
  assert.deepEqual(found.action, { do: "settings", label: "Settings", disabled: false, reason: "" });
});

test("the gateway of an environment is the one that runs here, else one the Lab keeps, else one that is connected; the others are counted", () => {
  lab();
  const mine = gateway({ id: "a", name: "mine", here: true }), kept = gateway({ id: "b", name: "kept" }), seen = gateway({ id: "c", name: "seen", kept: false });
  const idle = gateway({ id: "d", name: "idle", kept: false, connection: "NOT_CONNECTED" }), other = gateway({ id: "e", environmentId: "env-test" });
  const hosted = gateway({ id: "f", type: "SAAS" });
  assert.equal(status([seen, kept, mine, other, hosted]).gateway.name, "mine");
  assert.equal(status([seen, kept]).gateway.name, "kept");
  assert.equal(status([seen, idle]).gateway.name, "seen");
  assert.equal(status([other, hosted]).gateway.key, "none");
  assert.equal(status([other, hosted]).used, false);
});

test("an account not read yet lists no gateway", () => {
  assert.deepEqual(gatewayList(PLATFORM, undefined), []);
});

test("a gateway Cloud deleted while it runs here is the environment's, in red, and what to do is to replace it", () => {
  lab();
  const orphan = { gateway: "gone", environmentId: "env-dev" };
  const found = status([], undefined, {}, { orphan });
  assert.equal(found.used, true);
  assert.deepEqual([found.gateway.key, found.gateway.dot, found.gateway.word, found.gateway.id, found.gateway.here], ["orphan", "issue", "Removed in Cloud", "gone", true]);
  assert.deepEqual(found.gateway.tip, ["Removed in Gravitee Cloud", "It still runs here, with a token Cloud no longer accepts"]);
  assert.equal(found.action.do, "create-gateway");
  lab({ addons: [ADDON(STOPPED)] });
  assert.equal(status([], undefined, {}, { orphan }).gateway.tip[1], "Its token is still in the settings of the Hybrid gateway");
  assert.equal(status([], undefined, { id: "env-test", name: "Test" }, { orphan }).used, false, "it is the environment of the token's, not another");
  lab();
  assert.equal(status([gateway({ here: true })], VALID, {}, { orphan }).gateway.key, "running", "a gateway that is listed is not lost");
});

test("Start or Stop depends on what the add-on does, not on the verb of its action: it can run without being on", () => {
  assert.deepEqual([runs(ADDON()), runs(ADDON(STOPPED)), runs(ADDON({ state: { key: "starting" } })), runs(ADDON({ state: { key: "stopping" } })), runs(undefined)], [true, false, true, true, false]);
  const outside = ADDON({ action: { do: "start", label: "Turn on", enabled: true }, secondary: { do: "stop", label: "Turn off", enabled: false, reason: "Wait" } });
  assert.equal(stopOf(outside).label, "Turn off");
  assert.equal(stopOf(ADDON()).do, "stop");
  assert.equal(stopOf(ADDON(STOPPED)), null);
  lab({ addons: [outside] });
  assert.deepEqual(status([gateway({ here: true })], VALID).action, { do: "stop-gateway", label: "Stop gateway", disabled: true, reason: "Wait" });
});

test("a token that is set says so while a gateway is missing, and says it expires when it is near", () => {
  lab();
  const missing = status([], VALID).deploy;
  assert.deepEqual([missing.key, missing.word, missing.note, missing.dot], ["gateway", "Needs a gateway", "Cloud Token set", "stopped"]);
  assert.equal(status([gateway({ here: true })], VALID).deploy.note, "", "far from its end, a token says nothing");
  const rows = [[40, "", false], [14, "", false], [13, "Expires in 13 days", true], [5, "Expires in 5 days", true], [1, "Expires tomorrow", true], [0, "Expires today", true]];
  for (const [days, note, soon] of rows) {
    const { deploy } = status([gateway({ here: true })], { state: "valid", expires: dayFrom(days) });
    assert.deepEqual([deploy.note, deploy.soon, deploy.dot], [note, soon, soon ? "warn" : "healthy"], `${days} days`);
  }
  const claim = status([gateway({ here: true })], { state: "valid", expires: Math.floor(Date.now() / 1000) + 3 * 86400 + 3600 }).deploy;
  assert.equal(claim.note, "Expires in 3 days", "the claim is seconds since 1970, and a part of a day is not counted");
  assert.equal(status([], { state: "valid", expires: dayFrom(3) }).deploy.note, "Cloud Token set, expires in 3 days", "what is missing comes first, the expiry after it");
  assert.equal(status([], { state: "valid", expires: dayFrom(3) }).deploy.soon, true);
  assert.equal(status([gateway({ here: true })], { state: "expired", expires: dayFrom(-3) }).deploy.note, "", "an expired one says it in its word");
});

test("what the playgrounds deployed there makes it in use, by name for one and by count for several", () => {
  const record = (place, created) => ({ place, created });
  lab({ playgrounds: [{ id: "h", name: "Acme Hostels API", deployed: [record("cloud-eu/env-dev", [{ what: "APIs", count: 3 }]), record("cloud-eu/env-test", [{ what: "APIs", count: 1 }])] }] });
  const one = status();
  assert.equal(one.used, true);
  assert.deepEqual(one.deployed, [{ id: "h", name: "Acme Hostels API", created: [{ what: "APIs", count: 3 }] }]);
  assert.equal(one.summary, "Acme Hostels API deployed");
  lab({ playgrounds: [{ id: "h", name: "Acme Hostels API", deployed: [record("cloud-eu/env-dev", [{ what: "APIs", count: 3 }])] },
    { id: "b", name: "Acme Bakery", deployed: [record("cloud-eu/env-dev", [{ what: "APIs", count: 1 }])] },
    { id: "e", name: "Empty", deployed: [record("cloud-eu/env-dev", [])] }] });
  assert.equal(status().summary, "2 playgrounds deployed");
  assert.equal(status().deployed.length, 2, "a record that holds nothing is nothing");
  lab({ playgrounds: [{ id: "h", name: "Other", deployed: [record("other/env-dev", [{ what: "APIs", count: 3 }])] }] });
  assert.equal(status().used, false, "a place of another platform is another place");
});

// --- The add-on that runs a gateway is the server's choice ------------------------------------------------------------------------------

// A lab whose server says what runs the gateways of each kind of platform (`platform.gateways`), and what its catalogs are doing
const served = (gateways, more = {}) => { store.state = { blind: false, addons: [ADDON(), ADDON({ id: "other", name: "Other gateway" })], playgrounds: [],
  platform: { gateways }, content: content("ready"), catalogs: [FIRST()], core: { place: "machine", chosen: false, group: { id: "api-management", name: "API Management" } }, ...more }; };

test("the add-on that runs the gateways of a platform is the one the server names, whatever else declares a gateway", () => {
  served({ cloud: "other" });
  assert.equal(addonOf(PLATFORM).id, "other", "not the first that declares one, nor the one alphabetically first");
  served({ cloud: null });
  assert.equal(addonOf(PLATFORM), undefined, "none can: one that is blocked or needs a catalog is no candidate");
  served({ cloud: "gone" });
  assert.equal(addonOf(PLATFORM), undefined, "an id the state does not list is none");
  served({});
  assert.equal(addonOf(PLATFORM), undefined, "a kind the server does not list has none");
  store.state = null;
  assert.equal(addonOf(PLATFORM), undefined);
});

test("with the catalogs there and no add-on to run a gateway, the cell says so and the row's one action is Add catalog", () => {
  served({ cloud: null });
  assert.deepEqual(gatewayGap("cloud"), { ready: true, why: "Needs a gateway add-on", extra: "" });
  const found = status([], VALID);
  assert.deepEqual(brief(found), ["noaddon", "No gateway add-on", "gateway", "Needs a gateway", "add-catalog"]);
  assert.deepEqual(found.action, { do: "add-catalog", label: "Add catalog", disabled: false, reason: "" });
  assert.deepEqual(found.gateway.tip, ["No gateway add-on", "No catalog has an add-on that runs a gateway for My Account.", "Add a catalog that has one."],
    "the platform is named by its record, never by the page");
  assert.deepEqual([found.gateway.dot, found.gateway.version, found.gateway.extra], ["", "", ""]);
  // Nothing is made of a gateway nobody can start here: not the other actions of an add-on, not a token that is no use
  assert.equal(status([], undefined).used, false);
  assert.equal(status([], undefined).action.do, "set-up", "an environment that is not in use is offered to set up, as it was");
  assert.equal(status([gateway({ here: true })], undefined).action.do, "add-catalog", "a token missing is none of the first things to fix");
  // A token that cannot be used comes first, as before a gateway
  assert.equal(status([], { state: "expired" }).action.do, "replace-token");
  assert.equal(status([], { state: "refused" }).action.do, "replace-token");
});

test("the popover of the cell is a card: a title, then why and what to do", () => {
  served({ cloud: null });
  const cell = dom.doc.createElement("div");
  cell.innerHTML = gatewayCell(status([], VALID).gateway);
  assert.deepEqual(bubble(cell.querySelector(".tip")), ["No gateway add-on", "No catalog has an add-on that runs a gateway for My Account.", "Add a catalog that has one."]);
  assert.equal(text(cell.querySelector(".status-word")), "No gateway add-on");
  assert.ok(cell.querySelector(".status").classList.contains("muted"), "a state with no dot is a muted word");
});

test("a gateway that another machine runs is still said as it was: what is missing here is not its state", () => {
  served({ cloud: null });
  assert.deepEqual(brief(status([gateway()], VALID)).slice(0, 2), ["elsewhere", "Connected elsewhere"]);
});

test("while the catalogs download, fail or are missing, the cell says why in the words of the lists, with the button that mends it, never that there is no add-on", () => {
  const rows = [
    ["downloading", content("downloading"), [FIRST({ commit: "" })], "downloading", "Downloading", "starting", ""],
    ["failed", content("failed"), [catalog({ id: "gravitee", commit: "" })], "nocontent", "Cannot reach github.com. Check your connection, then try again.", "", "retry"],
    ["none", content("none"), [], "nocontent", "No catalog added", "", "add"],
  ];
  for (const [why, said, catalogs, key, word, dot, act] of rows) {
    served({ cloud: null }, { content: said, catalogs });
    const found = status([], VALID), cell = dom.doc.createElement("div");
    assert.deepEqual([found.gateway.key, found.gateway.word, found.gateway.dot], [key, word, dot], why);
    assert.notEqual(found.action?.do, "add-catalog", `${why}: the cell has the button that mends it`);
    cell.innerHTML = gatewayCell(found.gateway, "<span data-count></span>");
    assert.deepEqual(cell.querySelectorAll("[data-content-act]").map((el) => el.getAttribute("data-content-act")), act ? [act] : [], why);
    assert.equal(cell.querySelectorAll("[data-count]").length, 1, "what the page puts after the word stays");
    assert.ok(!/No gateway add-on/.test(text(cell)), why);
  }
  // With no catalog to offer, the cell says what is wrong and leaves the Catalogs page, which is one click from the menu, to the person
  served({ cloud: null }, { content: content("none", { offer: null }) });
  const bare = dom.doc.createElement("div");
  bare.innerHTML = gatewayCell(status([], VALID).gateway);
  assert.deepEqual(bare.querySelectorAll("a").map((el) => [text(el), el.getAttribute("href")]), [["Browse catalogs", "#/catalogs"]]);
});

test("the Cloud controls say why they cannot be used: no gateway add-on, then the words of the lists while the catalogs are not there", () => {
  const cloud = () => { const holder = dom.doc.createElement("div"); holder.innerHTML = runsControl(); return holder.querySelector('[data-core="cloud"]'); };
  const machine = () => { const holder = dom.doc.createElement("div"); holder.innerHTML = runsControl(); return holder.querySelector('[data-core="machine"]'); };
  served({ cloud: "hyssop" });
  assert.equal(gatewayWhy(), "");
  assert.deepEqual([cloud().hasAttribute("aria-disabled"), cloud().hasAttribute("data-tip-card")], [false, true], "with an add-on it is the segment it was");
  served({ cloud: null });
  assert.equal(gatewayWhy(), "Needs a gateway add-on");
  assert.deepEqual([cloud().getAttribute("aria-disabled"), cloud().getAttribute("data-why"), cloud().hasAttribute("data-tip-card")], ["true", "Needs a gateway add-on", false]);
  assert.equal(machine().hasAttribute("aria-disabled"), false, "This machine is never held");
  served({ cloud: null }, { content: content("downloading") });
  assert.equal(cloud().getAttribute("data-why"), "Downloading playgrounds, setups and add-ons");
  served({ cloud: null }, { content: content("failed") });
  assert.equal(gatewayWhy(), "Cannot reach github.com. Check your connection, then try again.");
  served({ cloud: null }, { content: content("none") });
  assert.equal(gatewayWhy(), "No catalog added");
  served({ cloud: "hyssop" }, { content: content("downloading") });
  assert.equal(gatewayWhy(), "", "an add-on that is there runs a gateway, whatever the catalogs are doing");
});

test("a key that reaches the Cloud choice while nothing can run its gateway asks for nothing; with an add-on the same key goes on", async () => {
  const original = globalThis.fetch, sent = [];
  globalThis.fetch = async (path) => { sent.push(path); return { ok: true, status: 200, headers: { get: () => "" }, json: async () => (path === "/api/places" ? [] : {}) }; };
  try {
    for (const [cloud, content_] of [[null, content("ready")], [null, content("downloading")], [null, content("failed")], [null, content("none")]]) {
      served({ cloud }, { platforms: [PLATFORM], content: content_ });
      await chooseCore("cloud");
      assert.deepEqual(sent, [], `${content_.key}: it is refused where the control is, and here too`);
    }
    served({ cloud: "hyssop" }, { platforms: [PLATFORM] });
    await chooseCore("cloud");
    assert.ok(sent.length > 0, "with an add-on it is chosen: the places are read, and the account after them");
  } finally {
    globalThis.fetch = original;
  }
});
