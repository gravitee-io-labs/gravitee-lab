// What the Lab says of an environment of a platform (web/js/platform/environments.js): the rules the Gravitee Cloud page draws and the Dashboard can say
// in the same words, read from the platform's last check and from the state. The contract of environmentStatus: whether the Lab uses the
// environment, the state of its gateway on this machine, whether it can be deployed to, what was deployed there, and the one action. The
// add-on that runs a gateway is chosen by the server (`state.platform.gateways`); with none, the cell says that there is no add-on (the Cloud
// controls with it), or, while the catalogs are not there, why, in the words of the lists.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { bubble, installDom, text } from "./dom-stub.mjs";
import { FIRST, catalog, content } from "./fixtures.mjs";

const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const dom = installDom({ ids });
const { store } = await import("../../../web/js/core.js");
const { environmentStatus, splitEnvironments, gatewayList, gatewayCount, addonOf, gatewayGap, gatewayCell, hybrid, runs, stopOf, TERMS } = await import("../../../web/js/platform/environments.js");
const { gatewayWhy, runsControl, chooseCore } = await import("../../../web/js/platform/core-choice.js");

const PLATFORM = { id: "cloud-eu", name: "My Account", kind: "cloud" };
const ENV = { id: "env-dev", name: "Development", token: { state: "unset" } };
const gateway = (more) => ({ id: "g1", name: "lab", environmentId: "env-dev", type: "HYBRID", status: "DEPLOYED", connection: "CONNECTED",
  version: "4.12.20", domains: ["localhost:8090", "apim-hybrid-gateway:8082"], here: false, kept: true, ...more });
const ADDON = (more) => ({ id: "hybrid", name: "Hybrid gateway", state: { key: "running" }, note: "", note_tone: "", note_title: "",
  action: { do: "stop", enabled: true, reason: null }, gateway: { platform: "cloud", service: "apim-hybrid-gateway" }, ...more });
const STOPPED = { state: { key: "stopped" }, action: { do: "start", enabled: true, reason: null } };
const dayFrom = (days) => new Date(Date.now() + days * 86400000 + 3600000).toISOString();   // and an hour, which the test takes to run
const lab = (state = {}) => { store.state = { blind: false, addons: [ADDON()], playgrounds: [], ...state }; };
const status = (gateways = [], token, more = {}, check = {}) => environmentStatus(PLATFORM, { gateways, ...check }, { ...ENV, token: token || ENV.token, ...more });
const VALID = { state: "valid", expires: "2027-01-31T00:00:00Z" };
const brief = (found) => [found.gateway.key, found.gateway.word, found.deploy.key, found.deploy.word, found.action?.do ?? null];

test("an environment the Lab does nothing with is not in use, and is offered to set up", () => {
  lab();
  const found = status();
  assert.equal(found.used, false);
  assert.deepEqual(brief(found), ["none", "Not created", "unset", "Needs a Cloud Token", "set-up"]);
  assert.equal(found.action.label, "Set up");
  assert.deepEqual(found.deployed, []);
  assert.equal(found.summary, "");
  assert.equal(found.id, "env-dev");
  assert.equal(found.name, "Development");
});

test("a gateway that runs here, connected, with a valid token, is running and can deploy, and its one action is to stop it", () => {
  lab();
  const found = status([gateway({ here: true })], VALID);
  assert.equal(found.used, true);
  assert.deepEqual(brief(found), ["running", "Running here", "ready", "Can deploy", "stop-gateway"]);
  assert.equal(found.gateway.dot, "healthy");
  assert.equal(found.gateway.version, "4.12.20");
  assert.equal(found.gateway.name, "lab");
  assert.equal(found.gateway.id, "g1");
  assert.deepEqual(found.gateway.tip, ["lab"], "its name: where it answers is said under the state");
  assert.equal(found.deploy.dot, "healthy");
  assert.equal(found.deploy.expires, "2027-01-31T00:00:00Z");
  assert.deepEqual(found.action, { do: "stop-gateway", label: "Stop gateway", disabled: false, reason: "" });
});

test("under the state: the version and, while it runs here, the address a person calls, never the name of a container", () => {
  lab();
  const running = status([gateway({ here: true })], VALID).gateway;
  assert.equal(running.address, "localhost:8090", "apim-hybrid-gateway is the name of the container: only the other containers know it");
  assert.deepEqual(running.details, ["4.12.20", "localhost:8090"]);
  const connecting = status([gateway({ here: true, connection: "NOT_CONNECTED" })], VALID).gateway;
  assert.deepEqual([connecting.key, connecting.details], ["connecting", ["4.12.20", "localhost:8090"]]);
  lab({ addons: [ADDON(STOPPED)] });
  assert.deepEqual(status([gateway({ here: true })], VALID).gateway.details, ["4.12.20"], "it answers nowhere while it is stopped");
  assert.equal(status([gateway({ here: true })], VALID).gateway.address, "");
  lab();
  const elsewhere = status([gateway({ name: "Alice laptop", kept: false, domains: ["alice.local:8082"] })], VALID).gateway;
  assert.deepEqual([elsewhere.key, elsewhere.details, elsewhere.address], ["elsewhere", ["Alice laptop", "4.12.20"], ""], "the machine says who runs it; its address is none to call");
  assert.deepEqual(status().gateway.details, []);
});

test("the gateway states in the words of the page, with the one action of each", () => {
  const rows = [
    ["stopped here", ADDON(STOPPED), [gateway({ here: true, connection: "NOT_CONNECTED" })], VALID, ["stopped", "Stopped here", "ready", "Can deploy", "start-gateway"]],
    ["starting", ADDON({ state: { key: "starting" } }), [gateway({ here: true })], VALID, ["starting", "Starting", "ready", "Can deploy", "stop-gateway"]],
    ["running, not seen by Cloud yet", ADDON(), [gateway({ here: true, connection: "NOT_CONNECTED" })], VALID, ["connecting", "Connecting", "ready", "Can deploy", "stop-gateway"]],
    ["connected elsewhere, kept", ADDON(), [gateway()], VALID, ["elsewhere", "Connected elsewhere", "ready", "Can deploy", "start-gateway"]],
    ["connected elsewhere, not kept", ADDON(), [gateway({ kept: false })], VALID, ["elsewhere", "Connected elsewhere", "gateway", "Needs a gateway here", "create-gateway"]],
    ["a license is missing", ADDON({ ...STOPPED, state: { key: "off" }, note: "Needs a license", note_tone: "warn", note_file: ".env" }), [gateway({ here: true })], VALID,
      ["license", "Needs a license", "ready", "Can deploy", "add-license"]],
    ["no gateway, a token", ADDON(), [], VALID, ["none", "Not created", "gateway", "Needs a gateway", "create-gateway"]],
    ["a gateway the Lab keeps and no license: none can start, whichever it is", ADDON({ ...STOPPED, state: { key: "off" }, note: "Needs a license", note_tone: "warn", note_file: ".env" }),
      [gateway({ connection: "NOT_CONNECTED" })], VALID, ["stopped", "Stopped here", "ready", "Can deploy", "add-license"]],
    ["running, started outside the Lab UI", ADDON({ action: { do: "start", label: "Turn on", enabled: true }, secondary: { do: "stop", label: "Turn off", enabled: true } }),
      [gateway({ here: true })], VALID, ["running", "Running here", "ready", "Can deploy", "stop-gateway"]],
  ];
  for (const [why, addon, gateways, token, expected] of rows) {
    lab({ addons: [addon] });
    assert.deepEqual(brief(status(gateways, token)), expected, why);
  }
});

test("the label of the gateway action says what it does: Create for none, Replace for one that is another machine's or that Cloud lost", () => {
  lab();
  assert.equal(status([], VALID).action.label, "Create gateway");
  assert.equal(status([gateway({ kept: false })], VALID).action.label, "Replace gateway");
  const lost = status([], VALID, {}, { orphan: { gateway: "gone", environmentId: "env-dev" } });
  assert.deepEqual([lost.action.do, lost.action.label], ["create-gateway", "Replace gateway"]);
});

test("a missing license is its own state, in amber, that the action adds: Add license, or Replace license when it expired or lacks a feature", () => {
  const note = (word) => ADDON({ state: { key: "off" }, note: word, note_tone: word === "License expired" ? "err" : "warn", note_title: "Uses: Enterprise features", note_file: ".env" });
  lab({ addons: [note("Needs a license")] });
  const found = status([gateway({ here: true })], VALID);
  assert.deepEqual([found.gateway.key, found.gateway.dot, found.gateway.word, found.gateway.tip], ["license", "warn", "Needs a license", ["Needs a license", "Uses: Enterprise features"]]);
  assert.deepEqual(found.action, { do: "add-license", label: "Add license", disabled: false, reason: "" });
  for (const word of ["License expired", "Not in the license"]) {
    lab({ addons: [note(word)] });
    assert.equal(status([gateway({ here: true })], VALID).action.label, "Replace license", word);
  }
  lab({ addons: [ADDON({ note: "Apply needed", note_tone: "warn" })] });
  const late = status([gateway({ here: true })], VALID).gateway;
  assert.deepEqual([late.key, late.dot], ["issue", "warn"], "only a license and settings are states of their own; a warning is amber, never the green of what runs");
});

test("a gateway whose add-on lacks settings says so in amber, never with the green of what runs, and its action opens those settings", () => {
  // The server says what it lacks as causes (its note's), and keeps its Stop: the state is the note's, its popover the card of its own badge
  lab({ addons: [ADDON({ note: "Needs settings", note_tone: "warn", note_title: "Not set: HYBRID_CLOUD_TOKEN", note_causes: [{ kind: "unset", settings: ["HYBRID_CLOUD_TOKEN"] }],
    action: { do: "stop", label: "Stop", enabled: true }, secondary: null })] });
  const found = status([gateway({ here: true })], VALID);
  assert.deepEqual([found.gateway.key, found.gateway.dot, found.gateway.word], ["settings", "warn", "Needs settings"]);
  assert.deepEqual(found.gateway.tip, { title: "Needs settings", rows: [{ lines: [[{ code: "HYBRID_CLOUD_TOKEN" }]] }], foot: "Set it in its settings." },
    "the card the add-on's own badge opens: the settings as chips, where they are set");
  assert.deepEqual(found.action, { do: "settings", label: "Settings", disabled: false, reason: "" });
  lab({ addons: [ADDON({ note: "Hybrid gateway exited (1)", note_tone: "err" })] });
  assert.equal(status([gateway({ here: true })], VALID).gateway.dot, "issue", "a failure is red");
});

test("a missing Cloud Token comes after the gateway (create, start, then the token, then stop), one that expired or is refused before it", () => {
  lab();
  assert.equal(status([gateway({ here: true })]).action.do, "add-token");
  assert.equal(status([gateway({ here: true })], { state: "expired" }).action.do, "replace-token");
  assert.equal(status([gateway({ here: true })], { state: "refused" }).action.label, "Replace Cloud Token");
  lab({ addons: [ADDON(STOPPED)] });
  assert.equal(status([gateway({ here: true })]).action.do, "start-gateway", "the gateway first");
  // What the row says in red is what its button does: a token refused or expired, before a gateway to start or to create
  assert.equal(status([gateway({ here: true })], { state: "refused" }).action.do, "replace-token");
  lab();
  assert.equal(status([]).action.do, "set-up", "nothing in use: it is set up");
  assert.equal(status([], { state: "expired" }).action.do, "replace-token");
  assert.equal(status([], { state: "valid" }).action.do, "create-gateway", "then the gateway");
});

test("what a Cloud Token says: Can deploy, Needs a gateway, Needs a Cloud Token, expired, refused, each with its expiry", () => {
  lab();
  const rows = [[VALID, "ready", "Can deploy", "healthy", ["Expires "]], [{ state: "valid" }, "ready", "Can deploy", "healthy", []],
    [{ state: "unset" }, "unset", "Needs a Cloud Token", "", []], [undefined, "unset", "Needs a Cloud Token", "", []],
    [{ state: "revoked" }, "unset", "Needs a Cloud Token", "", []], [{ state: "refused" }, "refused", "Cloud Token refused", "issue", []],
    [{ state: "expired", expires: "2026-10-01T00:00:00Z" }, "expired", "Cloud Token expired", "issue", ["Expired on "]]];
  for (const [token, key, word, dot, tip] of rows) {
    const { deploy } = status([gateway({ here: true })], token);
    assert.deepEqual([deploy.key, deploy.word, deploy.dot], [key, word, dot], JSON.stringify(token));
    assert.equal(deploy.tip.length, tip.length, JSON.stringify(token));
    if (tip.length) assert.ok(deploy.tip[0].startsWith(tip[0]), deploy.tip[0]);
  }
});

test("the gateway of an environment is the one that runs here, else one the Lab keeps, else one that is connected; the others are counted", () => {
  lab();
  const mine = gateway({ id: "a", name: "mine", here: true }), kept = gateway({ id: "b", name: "kept" }), seen = gateway({ id: "c", name: "seen", kept: false });
  const idle = gateway({ id: "d", name: "idle", kept: false, connection: "NOT_CONNECTED" }), other = gateway({ id: "e", environmentId: "env-test" });
  const hosted = gateway({ id: "f", type: "SAAS" });
  assert.equal(status([seen, kept, mine, other, hosted]).gateway.name, "mine");
  assert.deepEqual(status([seen, kept, mine, other, hosted]).gateway.others.map((g) => g.name), ["kept", "seen"]);
  assert.equal(status([seen, kept]).gateway.name, "kept");
  assert.equal(status([seen, idle]).gateway.name, "seen");
  assert.equal(status([other, hosted]).gateway.key, "none");
  assert.equal(status([other, hosted]).used, false);
});

test("a gateway nobody here keeps, and that is not connected, is none the Lab can use, and the environment is in use all the same so that it can be removed", () => {
  lab();
  const idle = gateway({ id: "d", name: "idle", kept: false, connection: "NOT_CONNECTED" }), failed = gateway({ id: "x", name: "failed", kept: false, connection: "UNKNOWN_HYBRID", status: "FAILED" });
  const found = status([idle, failed]);
  assert.equal(found.gateway.key, "none");
  assert.deepEqual(found.gateway.others.map((g) => g.name), ["idle", "failed"], "both are there to be removed");
  assert.equal(found.used, true);
  assert.equal(found.action.do, "create-gateway");
  assert.equal(found.action.label, "Create gateway");
});

test("a gateway Cloud deleted while it runs here is the environment's, in red, and what to do is to replace it", () => {
  lab();
  const orphan = { gateway: "gone", environmentId: "env-dev" };
  const found = status([], undefined, {}, { orphan });
  assert.equal(found.used, true);
  assert.deepEqual([found.gateway.key, found.gateway.dot, found.gateway.word, found.gateway.id, found.gateway.here], ["orphan", "issue", "Removed in Cloud", "gone", true]);
  assert.deepEqual(found.gateway.tip, ["Removed in Gravitee Cloud", "It still runs here, with a token Cloud no longer accepts"]);
  assert.deepEqual(found.gateway.details, ["Still runs here"]);
  assert.equal(found.action.do, "create-gateway");
  lab({ addons: [ADDON(STOPPED)] });
  assert.equal(status([], undefined, {}, { orphan }).gateway.tip[1], "Its token is still in the settings of the Hybrid gateway");
  assert.deepEqual(status([], undefined, {}, { orphan }).gateway.details, ["Its token is still set"]);
  assert.equal(status([], undefined, { id: "env-test", name: "Test" }, { orphan }).used, false, "it is the environment of the token's, not another");
  lab();
  assert.equal(status([gateway({ here: true })], VALID, {}, { orphan }).gateway.key, "running", "a gateway that is listed is not lost");
});

test("a gateway that runs here and does not connect is said so once it has waited long enough", () => {
  lab();
  const waiting = [gateway({ here: true, connection: "NOT_CONNECTED" })];
  assert.equal(environmentStatus(PLATFORM, { gateways: waiting }, { ...ENV, token: VALID }).gateway.key, "connecting");
  const late = environmentStatus(PLATFORM, { gateways: waiting }, { ...ENV, token: VALID }, { late: true }).gateway;
  assert.deepEqual([late.key, late.dot, late.word], ["late", "warn", "Not connected"]);
  assert.deepEqual(late.tip, ["lab", "Gravitee Cloud has not seen it for 2 minutes", "Its logs say why"]);
  assert.equal(environmentStatus(PLATFORM, { gateways: [gateway({ here: true })] }, { ...ENV, token: VALID }, { late: true }).gateway.key, "running", "one that connected is never late");
});

test("Start or Stop depends on what the add-on does, not on the verb of its action: it can run without being on", () => {
  assert.deepEqual([runs(ADDON()), runs(ADDON(STOPPED)), runs(ADDON({ state: { key: "starting" } })), runs(ADDON({ state: { key: "stopping" } })), runs(undefined)], [true, false, true, true, false]);
  const outside = ADDON({ action: { do: "start", label: "Turn on", enabled: true }, secondary: { do: "stop", label: "Turn off", enabled: false, reason: "Wait" } });
  assert.equal(stopOf(outside).label, "Turn off");
  assert.equal(stopOf(ADDON()).do, "stop");
  assert.equal(stopOf(ADDON(STOPPED)), null);
  lab({ addons: [outside] });
  assert.deepEqual(status([gateway({ here: true })], VALID).action, { do: "stop-gateway", label: "Stop gateway", disabled: true, reason: "Wait" });
  lab({ addons: [ADDON(STOPPED)] });
  assert.equal(status([gateway({ here: true })], VALID).action.do, "start-gateway");
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

test("without an add-on that runs gateways there is no gateway action, and nothing to set up beyond the token", () => {
  lab({ addons: [] });
  assert.equal(addonOf(PLATFORM), undefined);
  assert.equal(status([gateway({ here: true })], VALID).action, null);
  assert.equal(status([], VALID).action, null);
  assert.equal(status([], undefined).action.do, "set-up");
  assert.equal(status([gateway()], undefined).action.do, "add-token");
});

test("while Docker does not answer, a gateway that runs here says only what Cloud says of it", () => {
  lab({ blind: true, addons: [ADDON({ note: "Failed", note_tone: "err" })] });
  assert.deepEqual(brief(status([gateway({ here: true })], VALID)).slice(0, 2), ["unknown", "Connected"]);
  assert.equal(status([gateway({ here: true, connection: "NOT_CONNECTED" })], VALID).gateway.word, "Not connected");
  assert.equal(status([gateway({ here: true })], VALID).gateway.dot, "");
});

test("Stop carries the reason of the add-on while it cannot be used", () => {
  lab({ addons: [ADDON({ action: { do: "stop", enabled: false, reason: "Wait for Start Acme Hostels" } })] });
  assert.deepEqual(status([gateway({ here: true })], VALID).action, { do: "stop-gateway", label: "Stop gateway", disabled: true, reason: "Wait for Start Acme Hostels" });
});

test("a word of Cloud's own is said while a gateway is not deployed", () => {
  lab();
  for (const [state, word, dot] of [["PENDING", "Pending", "starting"], ["DELETING", "Deleting", "stopping"], ["FAILED", "Failed", "issue"]]) {
    const found = status([gateway({ status: state, connection: "UNKNOWN_HYBRID" })], VALID);
    assert.deepEqual([found.gateway.key, found.gateway.word, found.gateway.dot], ["issue", word, dot]);
  }
});

test("the terms are explained once, each with a title first", () => {
  assert.equal(TERMS.gateway[0], "Hybrid gateway");
  assert.equal(TERMS.token[0], "Cloud Token");
  assert.equal(TERMS.deploys[0], "Deploys", "what the column means, and what it needs");
  assert.equal(TERMS.account[0], "Account Token", "told from the Cloud Token");
  assert.ok(hybrid({ type: "HYBRID" }) && !hybrid({ type: "SAAS" }));
});

test("the environments of an account are split the way the Lab uses them: those it has something in, and the others, each with its status", () => {
  lab();
  const check = { gateways: [gateway({ environmentId: "env-prod", here: true })] };
  const environments = [{ ...ENV, id: "env-prod", name: "Production" }, { ...ENV, id: "env-test", name: "Test" }, { ...ENV, id: "env-demo", name: "Demo", token: { state: "valid" } }];
  const { used, idle } = splitEnvironments(PLATFORM, { ...check, environments });
  assert.deepEqual(used.map(({ env }) => env.name), ["Production", "Demo"], "a gateway of its own, a Cloud Token");
  assert.deepEqual(idle.map(({ env }) => env.name), ["Test"]);
  assert.deepEqual(used.map(({ st }) => st.action.do), ["add-token", "create-gateway"], "each carries its status, with the one action to offer");
  assert.deepEqual(idle.map(({ st }) => st.action.do), ["set-up"]);
  assert.deepEqual(splitEnvironments(PLATFORM, undefined), { used: [], idle: [] });
  assert.deepEqual(splitEnvironments(PLATFORM, { gateways: [] }), { used: [], idle: [] });
});

test("an add-on that runs without having been turned on from the Lab UI is stopped, never started, and one that lacks a license is offered one", () => {
  lab({ addons: [ADDON({ action: { do: "start", label: "Turn on", enabled: true }, secondary: { do: "stop", label: "Turn off", enabled: true } })] });
  assert.equal(status([gateway({ here: true })], VALID).action.do, "stop-gateway");
  lab({ addons: [ADDON({ ...STOPPED, state: { key: "off" }, note: "Needs a license", note_tone: "warn", note_file: ".env" })] });
  assert.equal(status([gateway({ here: true })], VALID).action.do, "add-license");
});

test("every hybrid gateway of an account is listed: the one that runs here first, then the connected ones, then the others in the order of the environments", () => {
  lab();
  const environments = [{ ...ENV }, { ...ENV, id: "env-test", name: "Test" }, { ...ENV, id: "env-prod", name: "Production" }];
  const check = { environments, gateways: [
    gateway({ id: "g4", name: "pending", environmentId: "env-test", kept: false, status: "PENDING", connection: "NOT_CONNECTED" }),
    gateway({ id: "g3", name: "idle", environmentId: "env-dev", kept: false, connection: "NOT_CONNECTED" }),
    gateway({ id: "g2", name: "other", environmentId: "env-prod", kept: false }),
    gateway({ id: "g1", name: "here", environmentId: "env-prod", here: true }),
    gateway({ id: "h1", name: "hosted", type: "MANAGED" })] };
  const list = gatewayList(PLATFORM, check);
  assert.deepEqual(list.map((g) => [g.name, g.envName, g.st.key, g.st.word]), [
    ["here", "Production", "running", "Running here"],
    ["other", "Production", "elsewhere", "Connected elsewhere"],
    ["idle", "Development", "idle", "Not connected"],
    ["pending", "Test", "issue", "Pending"],
  ], "a hosted gateway is none of the Lab's");
  assert.equal(list[0].address, "localhost:8090", "where the one that runs here answers");
  assert.deepEqual(list.slice(1).map((g) => g.address), ["", "", ""]);
  assert.deepEqual(list.map((g) => g.env?.id), ["env-prod", "env-prod", "env-dev", "env-test"]);
  assert.equal(list[2].st.dot, "off", "gray: nothing runs it");
  // A gateway Cloud deleted while it runs here is a row of its own, by its id
  const lost = gatewayList(PLATFORM, { environments, gateways: [], orphan: { gateway: "gone", environmentId: "env-test" } });
  assert.deepEqual(lost.map((g) => [g.name, g.envName, g.st.key, g.orphan]), [["gone", "Test", "orphan", true]]);
  assert.deepEqual(gatewayList(PLATFORM, undefined), []);
  // How an environment counts them
  assert.equal(status([gateway({ here: true }), gateway({ id: "g2" }), gateway({ id: "g3", environmentId: "env-prod" })]).gateway.count, 2);
});

test("a number of gateways is said in words: none, one, several", () => {
  assert.deepEqual([0, 1, 2, 12].map(gatewayCount), ["No gateway", "1 gateway", "2 gateways", "12 gateways"]);
});

// --- The add-on that runs a gateway is the server's choice ------------------------------------------------------------------------------

// A lab whose server says what runs the gateways of each kind of platform (`platform.gateways`), and what its catalogs are doing
const served = (gateways, more = {}) => { store.state = { blind: false, addons: [ADDON(), ADDON({ id: "other", name: "Other gateway" })], playgrounds: [],
  platform: { gateways }, content: content("ready"), catalogs: [FIRST()], core: { place: "machine", chosen: false, group: { id: "api-management", name: "API Management" } }, ...more }; };

test("the add-on that runs the gateways of a platform is the one the server names, whatever else declares a gateway; without a word of the server it is the one that declares it", () => {
  served({ cloud: "other" });
  assert.equal(addonOf(PLATFORM).id, "other", "not the first that declares one, nor the one alphabetically first");
  served({ cloud: null });
  assert.equal(addonOf(PLATFORM), undefined, "none can: one that is blocked or needs a catalog is no candidate");
  served({ cloud: "gone" });
  assert.equal(addonOf(PLATFORM), undefined, "an id the state does not list is none");
  served({});
  assert.equal(addonOf(PLATFORM), undefined, "a kind the server does not list has none");
  lab();
  assert.equal(addonOf(PLATFORM).id, "hybrid", "a state that does not say, as before the catalogs: the add-on that declares one");
  store.state = { ...store.state, platform: { gateways: "somewhere" } };
  assert.equal(addonOf(PLATFORM).id, "hybrid", "a field that is no list of kinds says nothing");
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
  assert.deepEqual([found.gateway.dot, found.gateway.details, found.gateway.version, found.gateway.extra], ["", [], "", ""]);
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
  served({ cloud: "hybrid" });
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
  served({ cloud: "hybrid" }, { content: content("downloading") });
  assert.equal(gatewayWhy(), "", "an add-on that is there runs a gateway, whatever the catalogs are doing");
  served(undefined);
  assert.equal(gatewayWhy(), "", "a state that does not say holds nothing");
  assert.deepEqual([cloud().hasAttribute("aria-disabled"), cloud().hasAttribute("data-why")], [false, false]);
});

test("wherever an add-on is named for a platform, nothing holds its gateway for want of one: the entries of the environment's menu that create a gateway have no hold to make", () => {
  // The menu of an environment (pages/platforms.js) offers Create gateway and Replace gateway only with the add-on found by addonOf, and
  // asks nothing of gatewayWhy: this is what lets it not. No state of the contract has an add-on named and the hold of a missing one.
  const states = [undefined, null, {}, { cloud: null }, { cloud: "hybrid" }, { cloud: "other" }, { cloud: "gone" }, { cloud: "" }, { cloud: 0 }, { cloud: "hybrid", other: "other" }];
  for (const content_ of ["ready", "downloading", "failed", "none"]) {
    for (const gateways of states) {
      served(gateways, { content: content(content_) });
      for (const kind of ["cloud", "other", "constructor", "__proto__", "toString", "x"]) {
        if (addonOf({ ...PLATFORM, kind })) assert.equal(gatewayWhy(kind), "", `${JSON.stringify(gateways)} ${content_} ${kind}`);
      }
    }
  }
});

test("a key that reaches the Cloud choice while nothing can run its gateway asks for nothing; with an add-on the same key goes on", async () => {
  const original = globalThis.fetch, sent = [];
  globalThis.fetch = async (path) => { sent.push(path); return { ok: true, status: 200, headers: { get: () => "" }, json: async () => ({}) }; };
  try {
    for (const [cloud, content_] of [[null, content("ready")], [null, content("downloading")], [null, content("failed")], [null, content("none")]]) {
      served({ cloud }, { platforms: [PLATFORM], content: content_ });
      await chooseCore("cloud");
      assert.deepEqual(sent, [], `${content_.key}: it is refused where the control is, and here too`);
    }
    served({ cloud: "hybrid" }, { platforms: [PLATFORM] });
    await chooseCore("cloud");
    assert.ok(sent.length > 0, "with an add-on it is chosen: the places are read, and the account after them");
  } finally {
    globalThis.fetch = original;
  }
});
