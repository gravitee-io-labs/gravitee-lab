// The dialog that says how Gravitee Lab works (web/js/dialogs/welcome.js) starts with where API Management runs, a choice of two cards, then
// what to do first, which follows that choice as its layers do, and ends with where the Lab comes from: its license, a link to its source and one to its
// troubleshooting guide, both built from the repository, which is written once, at the top of the module. What the catalogs do to it: its layers
// say they come from a catalog, the example of the on file names the folder of a catalog, the Library and the Catalogs are the pages it points to,
// and its foot draws the same slots whatever the content is, writing into those nodes (not drawing the foot again) as the download goes on.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, catalog, content, lab as labState } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$/.test(id));
const calls = [];
// A machine with nothing to fix: the first visit goes straight to this dialog
const MACHINE_READY = { ready: true, problems: 0, checks: [{ id: "docker", state: "ok", title: "Docker", detail: "Docker Desktop 29.8.2, Compose 5.6.0", actions: [] }] };
const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  calls.push({ path, body: options.body ? JSON.parse(options.body) : undefined });
  if (path.startsWith("/api/machine")) return reply(MACHINE_READY);
  return reply(path === "/api/state" ? store.state : {});
} });
const { store } = await import("../../../web/js/core.js");
const { Welcome } = await import("../../../web/js/dialogs/welcome.js");
const REPOSITORY = readFileSync(new URL("../../../web/js/dialogs/welcome.js", import.meta.url), "utf8").match(/^const REPOSITORY = "([^"]+)";/m)[1];

// The lab as the first state says it: the platform's groups, API Management the one that can run in the Cloud, chosen nowhere yet
const GROUP = { id: "api-management", name: "API Management" };
const GROUPS = ["API Management", "Access Management", "Alert Engine", "Databases"].map((name) => ({ name }));
store.state = { platform: { groups: GROUPS, state: { key: "running" } }, products: [], addons: [], playgrounds: [],
  core: { place: "machine", chosen: false, group: GROUP } };
Welcome.init();
await flush();
const dialog = page.doc.querySelector(".welcome-dialog");
const body = dialog.querySelector(".welcome");

test("a first visit shows it, which asks first where API Management runs: two cards of one choice, This machine chosen until another is kept", () => {
  assert.ok(dialog.hasAttribute("open"), "a first visit shows it");
  const heading = body.querySelector("h3"), group = body.querySelector(".core-cards");
  assert.equal(text(heading), "Where does API Management run?");
  assert.equal(group.getAttribute("role"), "radiogroup");
  assert.equal(group.getAttribute("aria-labelledby"), heading.getAttribute("id"), "the group is named by its question");
  const cards = group.querySelectorAll(".core-card"), radios = group.querySelectorAll('input[type="radio"]');
  assert.deepEqual(cards.map((card) => text(card.querySelector("strong"))), ["On this machine", "Gravitee Cloud"]);
  assert.deepEqual(radios.map((radio) => radio.getAttribute("name")), ["welcome-core", "welcome-core"], "one choice: a radio each, which the arrows move between");
  assert.deepEqual(radios.map((radio) => radio.checked), [true, false]);
  const lines = (card) => card.querySelectorAll(".core-card-text > *").map(text);
  assert.deepEqual(lines(cards[0]), ["On this machine", "Docker runs API Management here, with the rest of the platform."]);
  assert.deepEqual(lines(cards[1]), ["Gravitee Cloud", "API Management runs in your Cloud environment, with a hybrid gateway here. The rest of the platform stays on this machine.",
    "Needs a Gravitee Cloud account"]);
  assert.doesNotMatch(text(group), /\d+\.\d+|without/, "what a platform runs, and in which version, is never a sentence of the page");
  const core = body.querySelectorAll(".layer").at(-1);
  assert.equal(text(core.querySelector("strong")), "Core", "the platform's base layer keeps its name");
  assert.deepEqual(core.querySelectorAll(".pill").map(text), ["API Management", "Access Management", "Alert Engine", "Databases"]);
});

test("what to do first is four lines, the machine first", () => {
  const heading = body.querySelectorAll("h3")[1], steps = body.querySelector(".welcome-start");
  assert.equal(text(heading), "Start here");
  assert.ok(steps.previousElementSibling === heading, "the list follows its heading");
  assert.deepEqual(steps.children.map(text), [
    "Check your machine: Docker, its memory, the disk and the ports.",
    "Start the platform (about 10 to 15 minutes the first time).",
    "Open a console from the Dashboard.",
    "A playground's page lists what it needs: a license, versions, settings.",
  ]);
  assert.ok(steps.children[0].querySelector("[data-machine]"), "the machine's checks open from it");
  assert.equal(steps.children[2].querySelector("a").getAttribute("href"), "#/dashboard", "the Dashboard's name leads to it");
});

test("the example of the on file says what it is, and names no item of the catalog: the paths say where they are", () => {
  const snippet = body.querySelector(".welcome-code");
  assert.equal(text(snippet.previousElementSibling), "Example of .lab/on.yml");
  assert.equal(snippet.textContent, "include:\n  - .lab/catalogs/<catalog>/playgrounds/<id>/compose.yml\n  - .lab/catalogs/<catalog>/addons/<id>/compose.yml");
});

test("the dialog ends with the license of the Lab, a link to its source and one to its troubleshooting, each opening in a tab of its own", () => {
  const source = dialog.querySelector(".welcome-source");
  assert.equal(text(source), "Gravitee Lab, AGPL-3.0, Source, Troubleshooting");
  const [code, guide] = source.querySelectorAll("a");
  assert.equal(code.getAttribute("href"), REPOSITORY);
  assert.equal(guide.getAttribute("href"), `${REPOSITORY}/blob/main/docs/troubleshooting.md`);
  for (const link of [code, guide]) {
    assert.equal(link.getAttribute("target"), "_blank");
    assert.equal(link.getAttribute("rel"), "noopener");
  }
  assert.ok(body.children.at(-1) === source, "the last line of the dialog's text");
});

test("with API Management in the Cloud, Start all comes first, and the Core layer keeps every product and says only where API Management runs", () => {
  store.state = { platform: { groups: GROUPS, state: { key: "running" } }, products: [], addons: [{ id: "hybrid", name: "Hybrid gateway", file: "addons/hybrid/compose.yml", gateway: { platform: "cloud" } }],
    playgrounds: [], core: { place: "my-account/dev", chosen: true, name: "Development (My Account)", group: GROUP,
      start: { needed: true, enabled: true }, stop: { needed: false } } };
  store.notify();
  assert.deepEqual(body.querySelectorAll('input[name="welcome-core"]').map((radio) => radio.checked), [false, true]);
  assert.match(text(body.querySelectorAll(".core-card")[1]), /Development \(My Account\)$/, "the card says the environment it is");
  assert.equal(text(body.querySelector(".welcome-start li")), "Start all starts the hybrid gateway of Development (My Account) and the rest of the platform on this machine.");
  const core = body.querySelectorAll(".layer").at(-1);
  assert.equal(text(core.querySelector("strong")), "Core");
  assert.equal(text(core.querySelector(".layer-text > span")),
    "The Gravitee platform, always underneath. API Management runs in Gravitee Cloud: Development (My Account), with a hybrid gateway on this machine.");
  assert.deepEqual(core.querySelectorAll(".pill").map(text), ["API Management", "Access Management", "Alert Engine", "Databases"], "every product of the platform");
  assert.equal(text(core.querySelector("code")), "docker-compose.yml");
  assert.match(text(dialog.querySelector(".welcome-actions")), /Start all/, "Start all, which follows where API Management runs");
  store.state = null;
});

// --- What the catalogs change ---------------------------------------------------------------------------------------------------------

// The lab with the platform running and nothing on, and what the server says of the catalogs
const GATEWAY = { id: "hybrid", kind: "addon", name: "Hybrid gateway", gateway: { platform: "cloud" } };
function say(more = {}) {
  store.state = { ...labState({ platform: { name: "Gravitee platform", groups: GROUPS, state: { key: "running" }, gateways: { cloud: null } } }),
    products: [], core: { place: "machine", chosen: false, group: GROUP }, ...more };
  store.notify();
}
const actions = () => dialog.querySelector(".welcome-actions");
const slot = () => actions().querySelector('a[href="#/playgrounds"]');
const extra = () => actions().querySelectorAll("[data-content-act]").map((el) => [el.getAttribute("data-content-act"), el.getAttribute("data-catalog"), text(el)]);
const lead = () => [text(slot()), isOff(slot()), offWhy(slot())];
const WAITING = "Downloading playgrounds, setups and add-ons";

test("the layers on top of the platform say they come from a catalog, and the core keeps its file", () => {
  say();
  const layers = body.querySelectorAll(".layer");
  assert.deepEqual(layers.map((layer) => text(layer.querySelector("strong"))), ["Playground", "Setup", "Add-on", "Core"]);
  assert.deepEqual(layers.map((layer) => text(layer.querySelector("code"))), ["from a catalog", "from a catalog", "from a catalog", "docker-compose.yml"]);
  assert.ok(!/playgrounds\/|setups\/|addons\/|local/.test(text(body.querySelector(".layers"))), "no folder of a catalog is named by a layer");
});

test("a person's own items are said to stay on the machine until they are shared to a catalog, and not a word of the project's folders", () => {
  const parts = body.children.filter((child) => child.tag), heading = parts.find((h) => h.tag === "h3" && text(h) === "Yours or shared");
  const paragraph = parts[parts.indexOf(heading) + 1];
  assert.equal(text(paragraph), "New playground, New setup and New add-on make one from a form or with an AI agent. Personal stays on this machine until you share it to a catalog.");
  assert.ok(!/project|local|git/i.test(text(paragraph)));
});

test("the pages it points to are the Library's and the Catalogs, each leading to its page", () => {
  const rows = body.querySelectorAll(".pairs > div").map((row) => [text(row.querySelector("dt")), row.querySelectorAll("a").map((a) => a.getAttribute("href"))]);
  assert.deepEqual(rows, [["Dashboard", ["#/dashboard"]], ["Library", ["#/playgrounds", "#/setups", "#/addons"]], ["Catalogs", ["#/catalogs"]],
    ["Monitor", ["#/containers", "#/logs"]], ["Configure", ["#/images", "#/plugins"]]]);
  assert.equal(text(body.querySelectorAll(".pairs > div")[2].querySelector("dd")), "Catalogs: where they come from, to add and to update");
});

test("with the catalogs there, or a server that does not say, the foot is what it always was: Close, and Choose a playground that works", () => {
  for (const state of [{ content: content("ready"), catalogs: [FIRST()] }, { content: undefined }]) {
    say({ ...state, platform: { name: "Gravitee platform", groups: GROUPS, state: { key: "running" } } });
    assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close", "Choose a playground"]);
    assert.deepEqual(lead(), ["Choose a playground", false, ""]);
    assert.equal(slot().getAttribute("class"), "btn primary");
    assert.deepEqual(extra(), []);
  }
});

test("while the catalogs download the lead stays in its place and waits, saying why; a failure adds Try again before it, no catalog Add Gravitee public", () => {
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.4 })] });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close", "Choose a playground"]);
  assert.deepEqual(lead(), ["Choose a playground", true, WAITING]);
  assert.deepEqual(extra(), []);
  say({ content: content("failed"), catalogs: [catalog({ id: "gravitee", commit: "" })] });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close", "Try again", "Choose a playground"], "before the lead");
  assert.deepEqual(lead(), ["Choose a playground", true, "Cannot reach github.com. Check your connection, then try again."]);
  assert.deepEqual(extra(), [["retry", "gravitee", "Try again"]]);
  say({ content: content("none") });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close", "Add Gravitee public", "Choose a playground"]);
  assert.deepEqual(lead(), ["Choose a playground", true, "No catalog added"]);
  assert.deepEqual(extra(), [["add", "gravitee", "Add Gravitee public"]]);
  say({ content: content("none", { offer: null }) });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close", "Browse catalogs", "Choose a playground"]);
});

test("when the download ends while the dialog is open the nodes are updated, not drawn again: the focus stays and nothing moves under the pointer", () => {
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.4 })] });
  const close = actions().querySelector("[data-close]"), anchor = slot();
  close.focus();
  say({ content: content("failed"), catalogs: [catalog({ id: "gravitee", commit: "" })] });
  const tryAgain = actions().querySelector("[data-content-act]");
  tryAgain.focus();
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.1 })] });
  assert.ok(!actions().querySelector("[data-content-act]"), "the secondary button left");
  say({ content: content("ready"), catalogs: [FIRST()] });
  assert.ok(actions().querySelector("[data-close]") === close && slot() === anchor, "the same nodes, with their new words");
  assert.deepEqual(lead(), ["Choose a playground", false, ""]);
  assert.ok(!anchor.hasAttribute("aria-disabled") && !anchor.hasAttribute("data-why"));
  assert.equal(page.focused(), null, "the button that was focused left with the failure, which the person mended: the focus is not moved to another");
  close.focus();
  say({ content: content("downloading") });
  assert.ok(page.focused() === close, "a state that comes while a button has the focus leaves it there");
});

test("a lead that waits does nothing when it is clicked, and the dialog stays", () => {
  say({ content: content("downloading") });
  slot().click();
  assert.ok(dialog.hasAttribute("open"));
  say({ content: content("ready") });
  slot().click();
  assert.ok(!dialog.hasAttribute("open"), "once it is there, it leads to the playgrounds and closes the dialog");
  dialog.showModal();
});

test("Try again and Add the catalog that is listed keep the dialog open and are answered by the Catalogs page's one handler, which asks the routes of the catalogs", async () => {
  await import("../../../web/js/dialogs/catalog.js");   // where the actions of the content status live, once, for every page that draws it
  say({ content: content("failed"), catalogs: [catalog({ id: "gravitee", commit: "" })] });
  calls.length = 0;
  const retry = actions().querySelector("[data-content-act]");
  retry.click();
  page.doc.dispatchEvent(new Ev("click", { target: retry }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.map((call) => [call.path, call.body]).filter(([path]) => path !== "/api/state"), [["/api/catalogs/gravitee/check", {}]]);
  assert.ok(dialog.hasAttribute("open"), "the foot follows the download: the person is not sent anywhere");
  say({ content: content("none") });
  calls.length = 0;
  const add = actions().querySelector("[data-content-act]");
  add.click();
  page.doc.dispatchEvent(new Ev("click", { target: add }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.map((call) => [call.path, call.body])[0], ["/api/catalogs", { listed: "gravitee" }]);
  assert.ok(dialog.hasAttribute("open"));
});

test("the Cloud card cannot be chosen while nothing can run its gateway, and says why in the words of the catalogs; with an add-on it is as it was", () => {
  const cloud = () => body.querySelectorAll(".core-card")[1];
  const radio = () => cloud().querySelector("input");
  say({ content: content("ready"), catalogs: [FIRST()] });
  assert.deepEqual([cloud().getAttribute("aria-disabled"), cloud().getAttribute("data-why"), radio().getAttribute("aria-disabled")], ["true", "Needs a gateway add-on", "true"]);
  assert.ok(!body.querySelectorAll(".core-card")[0].hasAttribute("aria-disabled"), "This machine is never held");
  say({ content: content("downloading") });
  assert.equal(cloud().getAttribute("data-why"), WAITING);
  say({ content: content("failed") });
  assert.equal(cloud().getAttribute("data-why"), "Cannot reach github.com. Check your connection, then try again.");
  say({ content: content("none") });
  assert.equal(cloud().getAttribute("data-why"), "No catalog added");
  say({ content: content("ready"), addons: [GATEWAY], platform: { name: "Gravitee platform", groups: GROUPS, state: { key: "running" }, gateways: { cloud: "hybrid" } } });
  assert.deepEqual([cloud().hasAttribute("aria-disabled"), cloud().hasAttribute("data-why"), radio().hasAttribute("aria-disabled")], [false, false, false]);
  // A radio reached by the keyboard all the same does not change where it runs
  say({ content: content("ready") });
  calls.length = 0;
  Object.assign(radio(), { checked: true, name: "welcome-core" });   // a browser's input has its name
  dialog.dispatchEvent(Object.assign(new Ev("change"), { target: radio() }));
  assert.deepEqual(calls, []);
  assert.equal(body.querySelectorAll('input[name="welcome-core"]').map((input) => input.checked)[1], false, "the card is not shown as chosen");
});

test("Start all in the Cloud waits for a gateway add-on too, and the platform's Start is never held by it", () => {
  const stopped = { name: "Gravitee platform", groups: GROUPS, state: { key: "stopped" }, gateways: { cloud: null } };
  const cloudCore = { place: "my-account/development", chosen: true, name: "Development (My Account)", group: GROUP, start: { needed: true, enabled: true }, stop: { needed: false } };
  say({ content: content("ready"), platform: stopped });
  const start = () => actions().querySelector("[data-start]");
  assert.ok(!isOff(start()), "on this machine it starts the platform");
  say({ content: content("ready"), platform: stopped, core: cloudCore });
  assert.equal(text(start()), "Start all");
  assert.deepEqual([isOff(start()), offWhy(start())], [true, "Needs a gateway add-on"]);
  say({ content: content("ready"), platform: { ...stopped, gateways: { cloud: "hybrid" } }, addons: [GATEWAY], core: cloudCore });
  assert.ok(!isOff(start()));
});
