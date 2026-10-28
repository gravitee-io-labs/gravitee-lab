// Welcome to Gravitee Lab (web/js/dialogs/welcome.js): the steps from a fresh machine to a running platform, in a rail at the left, each with
// its state. Your machine comes first, and while a line in red keeps the platform from starting, what follows is locked and says why; then where
// API Management runs, a choice of two cards; then the start, which starts the platform and follows it until it runs, then lists its consoles
// with their sign-in and leads to the playgrounds once the catalogs are there. How it works stays in the rail, apart,
// and the rail ends with where the Lab comes from: its license, a link to its source and one to its troubleshooting guide, both built from the
// repository, which is written once, at the top of the module. What the catalogs do to it: its layers say they come from a catalog, the example
// of the on file names the folder of a catalog, and the foot of the start draws the same slots whatever the content is, writing into those nodes
// (not drawing the foot again) as the download goes on.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, catalog, content, lab as labState } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$/.test(id));
const calls = [];
const row = (id, state, detail, actions = []) => ({ id, state, title: id[0].toUpperCase() + id.slice(1), detail, actions });
const READY = { ready: true, problems: 0, checks: [row("docker", "ok", "Docker Desktop 29.8.2, Compose 5.6.0"), row("git", "ok", "Git 2.43.2")] };
const DOWN = { ready: false, problems: 1, checks: [
  row("docker", "error", "Docker Desktop is not running", [{ id: "open-docker", label: "Open Docker Desktop", primary: true }]),
  row("git", "ok", "Git 2.43.2"),
] };
const server = { machine: DOWN };
const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  calls.push({ path, body: options.body ? JSON.parse(options.body) : undefined });
  if (path.startsWith("/api/machine")) return reply(server.machine);
  return reply(path === "/api/state" ? store.state : {});
} });
const { store } = await import("../../../web/js/core.js");
const { Welcome } = await import("../../../web/js/dialogs/welcome.js");
const { refreshMachine } = await import("../../../web/js/dialogs/machine.js");
const REPOSITORY = readFileSync(new URL("../../../web/js/dialogs/welcome.js", import.meta.url), "utf8").match(/^const REPOSITORY = "([^"]+)";/m)[1];
const settle = async () => { for (let i = 0; i < 4; i += 1) await flush(); };

// The lab as the first state says it: the platform's groups, API Management the one that can run in the Cloud, chosen nowhere yet
const GROUP = { id: "api-management", name: "API Management" };
const GROUPS = [["API Management", ["gamma-ui", "apim-management-ui"]], ["Access Management", ["am-management-ui"]], ["Alert Engine", []],
  ["Databases", ["mongodb"]]].map(([name, services]) => ({ name, services }));
// The consoles as the state says them: the current ones and a legacy one of the platform, and one of an add-on, which is not the platform's
const LINKS = [
  { label: "AM Console", url: "http://localhost:8081", service: "am-management-ui", legacy: false, up: true, username: "admin", password: "adminadmin" },
  { label: "Gamma Console", url: "http://localhost:8086", service: "gamma-ui", legacy: false, up: true, username: "admin", password: "admin" },
  { label: "Kafka UI", url: "http://localhost:8180", service: "kafka-ui", legacy: false, up: true },
  { label: "APIM Console", url: "http://localhost:8084", service: "apim-management-ui", legacy: true, up: true, username: "admin", password: "admin" },
];
const START = { start: {} };   // the platform's Start, with no plan to confirm
store.state = { platform: { groups: GROUPS, state: { key: "running" }, ...START }, links: LINKS, products: [], addons: [], playgrounds: [],
  core: { place: "machine", chosen: false, group: GROUP } };
globalThis.EventSource ??= class { addEventListener() {} close() {} };   // the stream of a job that started
Welcome.init();
await settle();
const dialog = page.doc.querySelector(".welcome-dialog");
const body = dialog.querySelector(".welcome");
const actions = () => dialog.querySelector(".welcome-actions");
const rail = () => dialog.querySelectorAll(".welcome-steps .welcome-step");
const step = (id) => dialog.querySelector(`.welcome-step[data-step="${id}"]`);
const heading = () => text(body.querySelector("h2"));
const go = async (id) => { step(id).click(); await settle(); };

test("a first visit opens on your machine, and a line in red locks what follows: each step says why, and Continue waits", () => {
  assert.ok(dialog.hasAttribute("open"), "a first visit shows it");
  assert.equal(heading(), "Your machine");
  assert.deepEqual(rail().map((one) => one.getAttribute("data-step")), ["machine", "core", "start"]);
  assert.deepEqual(rail().map((one) => one.getAttribute("data-state")), ["attention", "locked", "locked"]);
  assert.deepEqual(rail().map((one) => one.querySelectorAll(".welcome-step-text > *").map(text)),
    [["Your machine", "1 to fix"], ["API Management", "Once your machine is ready"], ["Start", "Once your machine is ready"]]);
  const column = dialog.querySelector(".welcome-rail-steps");
  assert.deepEqual(column.children.filter((child) => child.tag).map((child) => child.getAttribute("data-step") || child.getAttribute("class")), ["guide", "welcome-steps"],
    "How it works first, apart: it is not a step");
  for (const locked of rail().slice(1)) assert.deepEqual([locked.getAttribute("aria-disabled"), locked.getAttribute("data-why")], ["true", "Fix your machine first"]);
  assert.equal(step("machine").getAttribute("aria-current"), "step");
  assert.equal(text(body.querySelector(".machine-summary")), "The platform cannot start until the line in red is fixed");
  const lines = body.querySelectorAll(".machine-check");
  assert.deepEqual(lines.map((line) => line.getAttribute("data-state")), ["error", "ok"]);
  assert.match(text(lines[0]), /Docker Desktop is not running Open Docker Desktop$/);
  const next = actions().querySelector("[data-next]");
  assert.deepEqual([text(next), next.classList.contains("primary"), next.getAttribute("aria-disabled"), next.getAttribute("data-why")],
    ["Continue", false, "true", "Fix the lines in red first"], "the fix of the line in red is the one primary button");
  assert.equal(actions().querySelector("[data-back]"), null, "the first step has no Back");
});

test("a locked step and a Continue that waits do nothing when they are clicked", async () => {
  await go("start");
  assert.equal(heading(), "Your machine");
  actions().querySelector("[data-next]").click();
  await settle();
  assert.equal(heading(), "Your machine");
});

test("a fix that runs here is asked of the server, and once the line is fixed the steps say so by themselves", async () => {
  calls.length = 0;
  server.machine = { ...DOWN, checks: [row("docker", "working", "Docker Desktop is starting"), row("git", "ok", "Git 2.43.2")] };
  body.querySelector('[data-act="open-docker"]').click();
  await settle();
  assert.deepEqual(calls.filter((call) => call.path === "/api/machine/act").map((call) => call.body), [{ id: "open-docker" }]);
  assert.equal(body.querySelector(".machine-check").getAttribute("data-state"), "working");
  server.machine = READY;
  mock.timers.tick(3000);
  await settle();
  assert.equal(text(body.querySelector(".machine-summary")), "Your machine is ready");
  assert.deepEqual(rail().map((one) => one.getAttribute("data-state")), ["done", "todo", "done"]);
  assert.ok(rail().every((one) => !one.hasAttribute("aria-disabled")));
  const next = actions().querySelector("[data-next]");
  assert.ok(next.classList.contains("primary") && !next.hasAttribute("aria-disabled"));
});

test("Continue asks where API Management runs: two cards of one choice, This machine chosen until another is kept; the machine is no longer asked", async () => {
  actions().querySelector("[data-next]").click();
  await settle();
  const title = body.querySelector("h2"), group = body.querySelector(".core-cards");
  assert.equal(text(title), "Where does API Management run?");
  assert.equal(group.getAttribute("role"), "radiogroup");
  assert.equal(group.getAttribute("aria-labelledby"), title.getAttribute("id"), "the group is named by its question");
  const cards = group.querySelectorAll(".core-card"), radios = group.querySelectorAll('input[type="radio"]');
  assert.deepEqual(cards.map((card) => text(card.querySelector("strong"))), ["On this machine", "Gravitee Cloud"]);
  assert.deepEqual(radios.map((radio) => radio.getAttribute("name")), ["welcome-core", "welcome-core"], "one choice: a radio each, which the arrows move between");
  assert.deepEqual(radios.map((radio) => radio.checked), [true, false]);
  const lines = (card) => card.querySelectorAll(".core-card-text > *").map(text);
  assert.deepEqual(lines(cards[0]), ["On this machine", "Docker runs API Management here, with the rest of the platform."]);
  assert.deepEqual(lines(cards[1]), ["Gravitee Cloud", "API Management runs in your Cloud environment, with a hybrid gateway here. The rest of the platform stays on this machine.",
    "Needs a Gravitee Cloud account"]);
  assert.doesNotMatch(text(group), /\d+\.\d+|without/, "what a platform runs, and in which version, is never a sentence of the page");
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Continue"]);
  calls.length = 0;
  mock.timers.tick(9000);
  await settle();
  assert.ok(!calls.some((call) => call.path.startsWith("/api/machine")), "asked only while it shows");
});

test("Continue keeps This machine and moves on at once to the start, which says the platform runs, lists its consoles with their sign-in and leads to the playgrounds", async () => {
  calls.length = 0;
  actions().querySelector("[data-next]").click();
  assert.equal(body.getAttribute("data-step"), "start", "without waiting for the save");
  await settle();
  assert.deepEqual(calls.filter((call) => call.path === "/api/core").map((call) => call.body), [{ place: "machine" }]);
  assert.equal(step("core").getAttribute("data-state"), "done");
  assert.equal(heading(), "Your platform is running");
  const items = body.querySelectorAll(".start-item");
  assert.deepEqual(items.map((item) => item.querySelectorAll(".start-text > *").map(text)), [["Gamma Console", "admin / admin"], ["AM Console", "admin / adminadmin"]],
    "the platform's current consoles, product by product: an add-on's is not one, the older ones are on the Dashboard");
  assert.deepEqual(items.map((item) => [item.querySelector("a").getAttribute("href"), item.querySelector("a").getAttribute("target")]),
    [["http://localhost:8086", "_blank"], ["http://localhost:8081", "_blank"]]);
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Discover the playgrounds"]);
  assert.equal(actions().querySelector('a[href="#/playgrounds"]').getAttribute("class"), "btn primary");
});

test("Done closes the dialog from the start, to go on later", () => {
  const done = actions().querySelectorAll(".btn").find((b) => text(b) === "Done");
  assert.equal(done.classList.contains("primary"), false);
  done.click();
  assert.ok(!dialog.hasAttribute("open"));
  dialog.showModal();
});

test("a stopped platform is started from the start, which stays open and follows it until it runs; a start that fails says why and offers Try again", async () => {
  const platform = (key) => ({ groups: GROUPS, state: { key }, ...START });
  store.state = { ...store.state, platform: platform("stopped") };
  store.notify();
  assert.equal(heading(), "Start the platform");
  assert.equal(text(body.querySelector(".welcome-lead")), "Its first start downloads about 15 GB of images: about 10 to 15 minutes.");
  assert.equal(step("start").getAttribute("data-state"), "todo");
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Start the platform"]);
  assert.ok(actions().querySelector("[data-start]").classList.contains("primary"), "the start is the one thing to do");
  calls.length = 0;
  actions().querySelector("[data-start]").click();
  await settle();
  page.doc.body.hidden = false;   // the drawer follows the job, and hides the parent of its output: here the body, which holds every id flat
  assert.ok(dialog.hasAttribute("open"), "the dialog stays, to follow the start");
  assert.deepEqual(calls.filter((call) => call.path === "/api/stack/start").map((call) => call.body), [{}]);
  store.state = { ...store.state, platform: platform("starting"), job: { id: "j1", for: "platform", verb: "start", done: false } };
  store.notify();
  assert.equal(heading(), "Starting the platform");
  assert.deepEqual([step("start").getAttribute("data-state"), text(step("start").querySelector(".welcome-step-text > span"))], ["checking", "Starting"]);
  assert.ok(body.querySelector('[data-progress="platform"]'), "how far it is");
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done"], "nothing to do while it starts: Done leaves it starting");
  store.state = { ...store.state, platform: platform("issue"), job: { id: "j1", for: "platform", verb: "start", done: true, ok: false, error: "Container gio-apim-gateway exited (1)" } };
  store.notify();
  assert.equal(heading(), "The platform did not start");
  assert.equal(text(body.querySelector(".welcome-lead")), "Container gio-apim-gateway exited (1)");
  assert.deepEqual([step("start").getAttribute("data-state"), text(step("start").querySelector(".welcome-step-text > span"))], ["attention", "Did not start"]);
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Show output", "Try again"]);
  store.state = { ...store.state, platform: platform("running"), job: { id: "j2", for: "platform", verb: "start", done: true, ok: true } };
  store.notify();
  assert.equal(heading(), "Your platform is running");
  assert.equal(step("start").getAttribute("data-state"), "done");
});

test("the info button of the menu opens How it works once the machine is ready, and your machine while a line of it is red", async () => {
  dialog.close();
  page.doc.getElementById("welcome").click();
  await settle();
  assert.equal(heading(), "How Gravitee Lab works");
  dialog.close();
  server.machine = DOWN;
  await refreshMachine(true);
  page.doc.getElementById("welcome").click();
  await settle();
  assert.equal(heading(), "Your machine");
  server.machine = READY;
  await refreshMachine(true);
  await go("start");
});

test("How it works: the layers, the example of the on file, which names no item of the catalog, and the pages", async () => {
  await go("guide");
  assert.equal(heading(), "How Gravitee Lab works");
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Close"]);
  const core = body.querySelectorAll(".layer").at(-1);
  assert.equal(text(core.querySelector("strong")), "Core", "the platform's base layer keeps its name");
  assert.deepEqual(core.querySelectorAll(".pill").map(text), ["API Management", "Access Management", "Alert Engine", "Databases"]);
  const snippet = body.querySelector(".welcome-code");
  assert.equal(text(snippet.previousElementSibling), "Example of .lab/on.yml");
  assert.equal(snippet.textContent, "include:\n  - .lab/catalogs/<catalog>/playgrounds/<id>/compose.yml\n  - .lab/catalogs/<catalog>/addons/<id>/compose.yml");
});

test("the rail ends with the license of the Lab, a link to its source and one to its troubleshooting, each opening in a tab of its own", () => {
  const source = dialog.querySelector(".welcome-source");
  assert.equal(text(source), "AGPL-3.0 Source Troubleshooting");
  const [code, guide] = source.querySelectorAll("a");
  assert.equal(code.getAttribute("href"), REPOSITORY);
  assert.equal(guide.getAttribute("href"), `${REPOSITORY}/blob/main/docs/troubleshooting.md`);
  for (const link of [code, guide]) {
    assert.equal(link.getAttribute("target"), "_blank");
    assert.equal(link.getAttribute("rel"), "noopener");
  }
});

test("with API Management in the Cloud, the start says Start all, and the Core layer keeps every product and says only where API Management runs", async () => {
  store.state = { platform: { groups: GROUPS, state: { key: "running" } }, products: [], addons: [{ id: "hybrid", name: "Hybrid gateway", file: "addons/hybrid/compose.yml", gateway: { platform: "cloud" } }],
    playgrounds: [], core: { place: "my-account/dev", chosen: true, name: "Development (My Account)", group: GROUP,
      start: { needed: true, enabled: true }, stop: { needed: false } } };
  store.notify();
  const core = body.querySelectorAll(".layer").at(-1);
  assert.equal(text(core.querySelector(".layer-text > span")),
    "The Gravitee platform, always underneath. API Management runs in Gravitee Cloud: Development (My Account), with a hybrid gateway on this machine.");
  assert.deepEqual(core.querySelectorAll(".pill").map(text), ["API Management", "Access Management", "Alert Engine", "Databases"], "every product of the platform");
  assert.equal(text(core.querySelector("code")), "docker-compose.yml");
  await go("core");
  assert.deepEqual(body.querySelectorAll('input[name="welcome-core"]').map((radio) => radio.checked), [false, true]);
  assert.match(text(body.querySelectorAll(".core-card")[1]), /Development \(My Account\)$/, "the card says the environment it is");
  assert.equal(text(step("core").querySelector(".welcome-step-text > span")), "Gravitee Cloud");
  await go("start");
  assert.equal(text(body.querySelector(".welcome-lead")), "Start all starts the hybrid gateway of Development (My Account) and the rest of the platform on this machine.");
  assert.match(text(actions()), /Start all/, "Start all, which follows where API Management runs");
});

// --- What the catalogs change ---------------------------------------------------------------------------------------------------------

// The lab with the platform running and nothing on, and what the server says of the catalogs
const GATEWAY = { id: "hybrid", kind: "addon", name: "Hybrid gateway", gateway: { platform: "cloud" } };
function say(more = {}) {
  store.state = { ...labState({ platform: { name: "Gravitee platform", groups: GROUPS, state: { key: "running" }, gateways: { cloud: null } } }),
    products: [], core: { place: "machine", chosen: false, group: GROUP }, ...more };
  store.notify();
}
const slot = () => actions().querySelector('a[href="#/playgrounds"]');
const extra = () => actions().querySelectorAll("[data-content-act]").map((el) => [el.getAttribute("data-content-act"), el.getAttribute("data-catalog"), text(el)]);
const lead = () => [text(slot()), isOff(slot()), offWhy(slot())];
const WAITING = "Downloading playgrounds, setups and add-ons";

test("the layers on top of the platform say they come from a catalog, and the core keeps its file", async () => {
  say();
  await go("guide");
  const layers = body.querySelectorAll(".layer");
  assert.deepEqual(layers.map((layer) => text(layer.querySelector("strong"))), ["Playground", "Setup", "Add-on", "Core"]);
  assert.deepEqual(layers.map((layer) => text(layer.querySelector("code"))), ["from a catalog", "from a catalog", "from a catalog", "docker-compose.yml"]);
  assert.ok(!/playgrounds\/|setups\/|addons\/|local/.test(text(body.querySelector(".layers"))), "no folder of a catalog is named by a layer");
});

test("a person's own items are said to stay on the machine until they are shared to a catalog, and not a word of the project's folders", () => {
  const parts = body.children.filter((child) => child.tag), title = parts.find((h) => h.tag === "h3" && text(h) === "Yours or shared");
  const paragraph = parts[parts.indexOf(title) + 1];
  assert.equal(text(paragraph), "New playground, New setup and New add-on make one from a form or with an AI agent. Personal stays on this machine until you share it to a catalog.");
  assert.ok(!/project|local|git/i.test(text(paragraph)));
});

test("the pages it points to are the Library's and the Catalogs, each leading to its page", () => {
  const rows = body.querySelectorAll(".pairs > div").map((one) => [text(one.querySelector("dt")), one.querySelectorAll("a").map((a) => a.getAttribute("href"))]);
  assert.deepEqual(rows, [["Dashboard", ["#/dashboard"]], ["Library", ["#/playgrounds", "#/setups", "#/addons"]], ["Catalogs", ["#/catalogs"]],
    ["Monitor", ["#/containers", "#/logs"]], ["Configure", ["#/images", "#/plugins"]]]);
  assert.equal(text(body.querySelectorAll(".pairs > div")[2].querySelector("dd")), "Catalogs: where they come from, to add and to update");
});

test("with the catalogs there, or a server that does not say, the foot of the start is what it always was: Back, and Discover the playgrounds that works", async () => {
  await go("start");
  for (const state of [{ content: content("ready"), catalogs: [FIRST()] }, { content: undefined }]) {
    say({ ...state, platform: { name: "Gravitee platform", groups: GROUPS, state: { key: "running" } } });
    assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Discover the playgrounds"]);
    assert.deepEqual(lead(), ["Discover the playgrounds", false, ""]);
    assert.equal(slot().getAttribute("class"), "btn primary");
    assert.deepEqual(extra(), []);
  }
});

test("while the catalogs download the lead stays in its place and waits, saying why; a failure adds Try again before it, no catalog Add Gravitee public", () => {
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.4 })] });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Discover the playgrounds"]);
  assert.deepEqual(lead(), ["Discover the playgrounds", true, WAITING]);
  assert.deepEqual(extra(), []);
  say({ content: content("failed"), catalogs: [catalog({ id: "gravitee", commit: "" })] });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Try again", "Discover the playgrounds"], "before the lead");
  assert.deepEqual(lead(), ["Discover the playgrounds", true, "Cannot reach github.com. Check your connection, then try again."]);
  assert.deepEqual(extra(), [["retry", "gravitee", "Try again"]]);
  say({ content: content("none") });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Add Gravitee public", "Discover the playgrounds"]);
  assert.deepEqual(lead(), ["Discover the playgrounds", true, "No catalog added"]);
  assert.deepEqual(extra(), [["add", "gravitee", "Add Gravitee public"]]);
  say({ content: content("none", { offer: null }) });
  assert.deepEqual(actions().querySelectorAll(".btn").map(text), ["Back", "Done", "Browse catalogs", "Discover the playgrounds"]);
});

test("when the download ends while the dialog is open the nodes are updated, not drawn again: the focus stays and nothing moves under the pointer", () => {
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.4 })] });
  const back = actions().querySelector("[data-back]"), anchor = slot();
  back.focus();
  say({ content: content("failed"), catalogs: [catalog({ id: "gravitee", commit: "" })] });
  const tryAgain = actions().querySelector("[data-content-act]");
  tryAgain.focus();
  say({ content: content("downloading"), catalogs: [catalog({ id: "gravitee", commit: "", progress: 0.1 })] });
  assert.ok(!actions().querySelector("[data-content-act]"), "the secondary button left");
  say({ content: content("ready"), catalogs: [FIRST()] });
  assert.ok(actions().querySelector("[data-back]") === back && slot() === anchor, "the same nodes, with their new words");
  assert.deepEqual(lead(), ["Discover the playgrounds", false, ""]);
  assert.ok(!anchor.hasAttribute("aria-disabled") && !anchor.hasAttribute("data-why"));
  assert.equal(page.focused(), null, "the button that was focused left with the failure, which the person mended: the focus is not moved to another");
  back.focus();
  say({ content: content("downloading") });
  assert.ok(page.focused() === back, "a state that comes while a button has the focus leaves it there");
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

test("the Cloud card cannot be chosen while nothing can run its gateway, and says why in the words of the catalogs; with an add-on it is as it was", async () => {
  await go("core");
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

test("Start all in the Cloud waits for a gateway add-on too, and the platform's Start is never held by it", async () => {
  await go("start");
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

test("a control anywhere in the page that checks the machine opens the dialog on it", async () => {
  dialog.close();
  const button = page.doc.createElement("button");
  button.setAttribute("data-machine", "");
  page.doc.body.append(button);
  button.click();
  page.doc.dispatchEvent(new Ev("click", { target: button }));
  await settle();
  assert.ok(dialog.hasAttribute("open"));
  assert.equal(heading(), "Your machine");
  dialog.close();
});
