// The badge of what runs behind what is saved (web/js/components/ui.js): its markup, how a row's note becomes one, and how it opens: by a tap, closed by a tap elsewhere, a second
// badge or Escape. Then where the Containers page uses it: the group and the line of a service that runs behind.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, El, bubble, installDom, text } from "./dom-stub.mjs";
import { BLOCKED, HOSTILE, HOSTILE_QUOTED, NEEDS, gains, item, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids, stored: { expanded: JSON.stringify({ "group:databases": true }) }, fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
globalThis.innerHeight = 800;
const { matches, closest } = El.prototype;
// ... and the keys of a segmented control, whose selector it does not know either: no node of its own is under the keys of a test
El.prototype.matches = function (selector) { return selector === ":focus-visible" || selector.startsWith(":is([role") ? false : matches.call(this, selector); };
El.prototype.closest = function (selector) { return selector.includes(":is([role") ? null : closest.call(this, selector.replace(":is(.busy, .said)", ".busy")); };
const { store } = await import("../../../web/js/core.js");
const { actionButton, applyBadge, behindWhy, catalogNeed, flash, noteBadge, noteCard, noteHtml, originLink, paintActions, tipHtml } = await import("../../../web/js/components/ui.js");
const { stackPage } = await import("../../../web/js/pages/stack.js");
Object.defineProperty(store, "platforms", { get: () => [], configurable: true });

const WHY = "4.12.20 is saved, 4.13.0-alpha.4 still runs.";
const draw = (markup) => {
  const root = page.doc.createElement("div");
  root.innerHTML = markup;
  page.doc.body.append(root);
  return root;
};
// A tap or a click, as the document sees it (the small DOM does not take an event up to it)
const press = (target, type = "click") => page.doc.dispatchEvent(new Ev(type, { target }));

test("a badge is a button named by its reason, and the bubble that shows it is a popover the screen reader does not read twice", () => {
  const root = draw(applyBadge(WHY));
  const button = root.querySelector(".tip-btn"), bubble = root.querySelector(".tip-card");
  assert.equal(button.tag, "button");
  assert.equal(button.getAttribute("type"), "button");
  assert.equal(button.getAttribute("aria-label"), `Apply needed: ${WHY}`);
  assert.equal(text(button), "Apply needed");
  assert.ok(button.querySelector("svg use").getAttribute("href").endsWith("#i-info"), "with the information mark");
  assert.deepEqual([bubble.getAttribute("role"), bubble.getAttribute("aria-hidden"), text(bubble.querySelector(".pop-text"))], ["tooltip", "true", WHY],
    "a sentence longer than a line is a popover");
  assert.ok(button.classList.contains("pill") && button.classList.contains("warn"), "a pill in the warn tone");
  assert.ok(draw(applyBadge(WHY, "state warn")).querySelector(".tip-btn").classList.contains("state"), "the head's size");
});

test("a reason is text: nothing of it becomes markup", () => {
  const root = draw(applyBadge('<img src=x onerror="boom()"> "quoted"'));
  assert.equal(root.querySelectorAll("img").length, 0);
  assert.equal(root.querySelector(".tip-btn").getAttribute("aria-label"), 'Apply needed: <img src=x onerror="boom()"> "quoted"');
});

test("a tooltip names what it sits on with its own label, for a name that is not the badge", () => {
  const root = draw(tipHtml("API Management", WHY, "tip-name", "API Management"));
  assert.equal(root.querySelector(".tip-btn").getAttribute("aria-label"), `API Management: ${WHY}`);
  assert.equal(draw(tipHtml("x", WHY)).querySelector(".tip-btn").getAttribute("aria-label"), WHY, "its line alone without a label");
});

test("one line is a tooltip; several lines are a popover whose first line is its title, read by a screen reader as sentences", () => {
  const lines = ["Images", "58.9 GB, 42.7 GB unused", "A volume can hold data."];
  const root = draw(tipHtml("", lines, "dash-hit", ""));
  assert.equal(root.querySelector(".tip-text"), null, "not the dark bubble of a line");
  assert.deepEqual(bubble(root), lines);
  assert.equal(text(root.querySelector(".pop-title")), "Images", "the first is the title");
  assert.deepEqual([root.querySelector(".tip-card").getAttribute("role"), root.querySelector(".tip-card").getAttribute("aria-hidden")], ["tooltip", "true"]);
  assert.equal(root.querySelector(".tip-btn").getAttribute("aria-label"), "Images. 58.9 GB, 42.7 GB unused. A volume can hold data.");
  assert.equal(draw(tipHtml("CPU", ["Load", "Of the cores"], "", "CPU")).querySelector(".tip-btn").getAttribute("aria-label"), "CPU: Load. Of the cores");
  assert.equal(draw(tipHtml("Up to date", ["Up to date", "4.13.1 is the latest release"], "", "Up to date")).querySelector(".tip-btn").getAttribute("aria-label"),
    "Up to date: 4.13.1 is the latest release", "a title that is the name of its control is said once");
  // Lines of equals (what is wrong, a line for each product) have no title
  const apart = draw(tipHtml("x", [`API Management: ${WHY}`, `Alert Engine: ${WHY}`], "", "", { title: false }));
  assert.equal(text(apart.querySelector(".pop-title")), "");
  assert.deepEqual(bubble(apart), [`API Management: ${WHY}`, `Alert Engine: ${WHY}`]);
  assert.equal(apart.querySelector(".tip-btn").getAttribute("aria-label"), `API Management: ${WHY.slice(0, -1)}. Alert Engine: ${WHY}`, "a full stop between sentences, not two");
  assert.equal(draw(tipHtml("x", "Read 1 min ago")).querySelector(".tip-text").children.length, 1, "a short line alone is a tooltip of one line");
  assert.equal(draw(tipHtml("x", "Read 1 min ago")).querySelector(".tip-card"), null);
  // A line longer than a tooltip holds (40 characters) is the sentence of a popover: a tooltip never wraps
  const long = draw(tipHtml("x", WHY));
  assert.equal(long.querySelector(".tip-text"), null);
  assert.deepEqual(bubble(long), [WHY]);
  assert.equal(long.querySelector(".tip-btn").getAttribute("aria-label"), WHY);
  // A bubble for the pointer only: its button is no stop of the keyboard
  assert.equal(draw(tipHtml("", WHY, "dash-hit", "", { tab: false })).querySelector(".tip-btn").getAttribute("tabindex"), "-1");
  assert.equal(draw(tipHtml("", WHY)).querySelector(".tip-btn").getAttribute("tabindex"), null);
});

test("the note of what runs behind is the badge, its tooltip the reason; any other note is what it was", () => {
  assert.equal(draw(noteHtml({ note: "Apply needed", note_tone: "warn", note_title: WHY, note_apply: true })).querySelector(".tip-btn").getAttribute("aria-label"), `Apply needed: ${WHY}`);
  assert.equal(draw(noteHtml({ note: "Apply needed", note_tone: "warn", note_apply: true })).querySelector(".tip-text").textContent, "Apply needed", "no reason: the note's words, a tooltip");
  const plain = draw(noteHtml({ note: "Needs settings", note_tone: "warn", note_title: "Fill them in" }));
  assert.equal(plain.querySelectorAll(".tip").length, 0);
  assert.equal(plain.querySelector(".list-note").getAttribute("data-tip"), "Fill them in", "the shared tooltip, never the browser's title");
  assert.equal(plain.querySelector(".list-note").getAttribute("title"), null);
  // A note whose words are all it says has them as its tooltip while they are cut
  const cut = draw(noteHtml({ note: "Developer Portal stopped" })).querySelector(".list-note");
  assert.deepEqual([cut.getAttribute("data-tip"), cut.hasAttribute("data-tip-cut")], ["Developer Portal stopped", true]);
});

test("a tap opens the bubble and a tap elsewhere, Escape or another badge closes it", () => {
  const root = draw(applyBadge(WHY) + tipHtml("Databases", WHY, "tip-name"));
  const [first, second] = root.querySelectorAll(".tip");
  const event = new Ev("click", { target: first.querySelector(".tip-btn") });
  page.doc.dispatchEvent(event);
  assert.ok(first.classList.contains("open"));
  assert.ok(event.defaultPrevented && event.cancelBubble, "a row that opens or a link under it does not take the tap");
  press(second.querySelector(".tip-btn"));
  assert.deepEqual([first.classList.contains("open"), second.classList.contains("open")], [false, true], "one open at a time");
  press(second.querySelector(".tip-btn"));
  assert.ok(!second.classList.contains("open"), "a second tap closes it");
  press(first.querySelector(".tip-btn"));
  press(page.doc.body);
  assert.ok(!first.classList.contains("open"), "a tap elsewhere");
  press(first.querySelector(".tip-btn"));
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape", target: page.doc.body }));
  assert.ok(!first.classList.contains("open"), "Escape");
});

test("Escape closes a bubble that the pointer or the keyboard holds open, until both have left", () => {
  const root = draw(applyBadge(WHY));
  const tip = root.querySelector(".tip"), button = tip.querySelector(".tip-btn");
  tip.states = new Set(["hover"]);   // the pointer is on it: CSS shows the bubble
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape", target: button }));
  assert.ok(tip.classList.contains("dismissed"), "closed, though the pointer is still there");
  // The pointer moves within the badge (into its bubble, which it can reach): it stays closed
  page.doc.dispatchEvent(new Ev("mouseout", { target: button, relatedTarget: tip.querySelector(".tip-card") }));
  assert.ok(tip.classList.contains("dismissed"));
  // It leaves: the next time it comes the bubble shows
  tip.states = new Set();
  page.doc.dispatchEvent(new Ev("mouseout", { target: button, relatedTarget: page.doc.body }));
  assert.ok(!tip.classList.contains("dismissed"));
  // The focus holds it too, and holds it closed once Escape was pressed
  tip.states = new Set(["focus-within"]);
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape", target: button }));
  assert.ok(tip.classList.contains("dismissed"));
  tip.states = new Set(["focus-within", "hover"]);
  page.doc.dispatchEvent(new Ev("focusout", { target: button, relatedTarget: page.doc.body }));
  assert.ok(tip.classList.contains("dismissed"), "the focus left, the pointer is still on it: closed");
  tip.states = new Set();
  page.doc.dispatchEvent(new Ev("focusout", { target: button, relatedTarget: page.doc.body }));
  assert.ok(!tip.classList.contains("dismissed"), "both have left");
  // A badge that nothing holds open is not marked
  tip.classList.remove("dismissed");
  page.doc.dispatchEvent(new Ev("keydown", { key: "Escape", target: page.doc.body }));
  assert.ok(!tip.classList.contains("dismissed"));
});

test("a click in the bubble is not the badge's: it selects the words and does not close it", () => {
  const root = draw(applyBadge(WHY));
  const tip = root.querySelector(".tip");
  press(tip.querySelector(".tip-btn"));
  assert.ok(tip.classList.contains("open"));
  const click = new Ev("click", { target: tip.querySelector(".tip-card") });
  page.doc.dispatchEvent(click);
  assert.ok(tip.classList.contains("open"), "still open");
  assert.ok(!click.defaultPrevented);
  press(page.doc.body);
});

test("Enter on a badge in a row that opens is the badge's, not the row's", () => {
  const root = draw(`<div role="button" tabindex="0">${applyBadge(WHY)}</div>`);
  const event = new Ev("keydown", { key: "Enter", target: root.querySelector(".tip-btn") });
  page.doc.dispatchEvent(event);
  assert.ok(event.cancelBubble);
  const row = new Ev("keydown", { key: "Enter", target: root.firstElementChild });
  page.doc.dispatchEvent(row);
  assert.ok(!row.cancelBubble, "a key of the row itself is the row's");
});

// --- The Containers page -----------------------------------------------------------------

const RUNNING = { key: "running", label: "Running", running: true };
const NONE = { note: "", note_tone: "", note_title: "", note_target: null, note_file: null };
const LATE = { note: "Apply needed", note_tone: "warn", note_title: WHY, note_apply: true, note_target: null, note_file: null };
const service = (label, more = {}) => ({ label, description: "", container: `gio-${label}`, image: `graviteeio/${label}:4.12.20`, tag: "4.12.20", registry: "graviteeio",
  built: false, role: null, needs_license: false, name: label, ports: [], state: RUNNING, uptime: "5 min", on: true, running_tag: "4.13.0-alpha.4",
  behind: false, brings: [], recreates: [], ...NONE, ...more });
const lab = () => ({
  blind: false, wait: "", docker: { error: null }, license: null, products: [], categories: [], playgrounds: [], addons: [], setups: [], links: [], problems: [],
  services: { mongodb: service("mongodb", { behind: true, ...LATE }), redis: service("redis") },
  platform: { name: "Gravitee platform", state: RUNNING, progress: null, file: "docker-compose.yml", env_file: ".env", settings: [],
    behind: [{ kind: "group", id: "databases", name: "Databases", reason: `Databases: ${WHY}` }],
    groups: [{ id: "databases", name: "Databases", services: ["mongodb", "redis"], state: RUNNING, progress: null, version: "", registry: null, registry_label: null,
      product: null, file: "docker-compose.yml", env_file: ".env", settings: [], brings: [], recreates: [], ...LATE }],
    start: { needed: false, starts: [], restarts: [], enabled: true, reason: null, dialog: null }, apply: { needed: true, restarts: [], starts: [], dialog: null },
    restart: { needed: true, enabled: true, reason: null }, update: { needed: true, enabled: true, reason: null, dialog: null } },
});

test("on the Containers page the group of what runs behind carries the badge with its reason, and its lines say it by their version alone", () => {
  const section = page.doc.createElement("section");
  page.doc.body.append(section);
  stackPage.init(section);
  store.state = lab();
  stackPage.render(store.state);
  const group = section.querySelector('.group[data-key="group:databases"] > .group-row');
  const line = section.querySelector('.container[data-service="mongodb"]'), other = section.querySelector('.container[data-service="redis"]');
  const button = group.querySelector(".tip-btn");
  assert.equal(button.getAttribute("aria-label"), `Apply needed: ${WHY}`);
  assert.equal(text(button), "Apply needed");
  assert.equal(group.querySelectorAll(".list-note").length, 0, "no orange text beside it");
  // The badge is said once per level that owns a cause: a container of the group says it by the version it runs, the one saved in
  // its tooltip, in the color of every version (the badge is the one signal)
  assert.equal(line.querySelectorAll(".tip").length, 0);
  const version = line.querySelector(".s-version");
  assert.deepEqual([version.getAttribute("class"), version.getAttribute("data-tip"), text(version)], ["s-version", "4.12.20 is saved", "4.13.0-alpha.4"]);
  assert.equal(other.querySelectorAll(".tip").length, 0);
});

// --- The popover of what runs behind what is saved, from the server's causes ------------------------------------------------

const TAG = { kind: "tag", saved: "4.12.20", running: "4.13.0-alpha.4", older: true, services: [] };
const SETTINGS = { kind: "settings", settings: ["GRAVITEE_LICENSE", "INTERNAL_API_PASSWORD"], services: [] };
const chips = (root) => root.querySelectorAll(".chip").map(text);

test("a row's badge opens the card of its causes: versions and settings as chips, running before saved, never a value", () => {
  const root = draw(applyBadge(WHY, "pill warn", [TAG]));
  assert.equal(root.querySelector(".tip-btn").getAttribute("aria-label"), `Apply needed: ${WHY}`, "its name is the sentence");
  assert.deepEqual(bubble(root), ["Differs from what is saved", "4.13.0-alpha.4 4.12.20 (older)", "Apply restarts it with what is saved, older versions included."]);
  assert.deepEqual(chips(root), ["4.13.0-alpha.4", "4.12.20"]);
  // The versions are a table: what runs and what is saved under their captions, the one saved marked when Apply goes back to it
  assert.equal(text(root.querySelector(".pop-captions")), "Running Saved");
  assert.deepEqual([root.querySelector(".pop-run .chip").getAttribute("class"), root.querySelector(".pop-saved .chip").getAttribute("class")], ["chip", "chip older"]);
  assert.match(root.querySelector(".tip-btn").getAttribute("aria-label"), /^Apply needed: /);
  assert.deepEqual(bubble(draw(applyBadge("x", "pill warn", [{ ...TAG, older: false }]))).slice(1), ["4.13.0-alpha.4 4.12.20", "Apply restarts it with what is saved."], "a newer one saved is no going back");
  const settings = draw(applyBadge("x", "pill warn", [SETTINGS]));
  assert.deepEqual(chips(settings), ["GRAVITEE_LICENSE", "INTERNAL_API_PASSWORD"], "the names of the settings, as chips");
  const many = draw(applyBadge("x", "pill warn", [{ ...SETTINGS, settings: ["A", "B", "C", "D", "E", "F"] }]));
  assert.deepEqual(chips(many), ["A", "B", "C", "D"]);
  assert.match(text(many.querySelector(".pop-line")), /and 2 more$/);
  assert.deepEqual(bubble(draw(applyBadge("x", "pill warn", [{ kind: "config", services: [] }])))[1], "Configuration changed");
  assert.deepEqual(bubble(draw(applyBadge("x", "pill warn", [{ kind: "image", tag: "3.0.2", built: false, services: [] }])))[1], "Image 3.0.2 changed");
  assert.deepEqual(bubble(draw(applyBadge("x", "pill warn", [{ kind: "image", tag: "latest", built: true, services: [] }])))[1], "Image built again");
  assert.deepEqual(bubble(draw(applyBadge("x", "pill warn", [{ kind: "tag", saved: null, running: null, services: [] }])))[1], "Another image is saved");
  // Without causes (an older server, the license's line) the reason is its explanation: a tooltip when short
  assert.ok(draw(applyBadge("Its image changed")).querySelector(".tip-text"));
  // A note of the server carries its causes to its badge
  assert.deepEqual(chips(draw(noteHtml({ note: "Apply needed", note_tone: "warn", note_title: WHY, note_apply: true, note_causes: [TAG] }))), ["4.13.0-alpha.4", "4.12.20"]);
});

test("the platform's card: how many containers in all, a row for each product with the services a cause is about, and what Apply does", () => {
  const behind = [
    { name: "API Management", reason: "a", count: 5, causes: [TAG] },
    { name: "Databases", reason: "b", count: 2, causes: [{ kind: "tag", saved: "9.5.2", running: "9.5.3", services: ["Elasticsearch"] },
      { kind: "tag", saved: "8.0.29", running: "8.3.11", services: ["MongoDB"] }] }];
  const card = behindWhy({ behind });
  assert.equal(card.title, "7 containers differ from what is saved");
  assert.equal(card.foot, "Apply restarts them with what is saved, older versions included.");
  const root = draw(applyBadge(["a", "b"], "pill warn", card));
  assert.deepEqual(root.querySelectorAll(".pop-subject").map(text), ["API Management", "Databases"]);
  // A product's one cause is on its line; a group's causes are under it, each with whom it is about
  assert.deepEqual(root.querySelectorAll(".pop-who").map(text), ["API Management", "Elasticsearch", "MongoDB"], "who, when not all of the product");
  assert.deepEqual(root.querySelectorAll(".pop-run").map(text), ["Running", "4.13.0-alpha.4", "9.5.3", "8.3.11"], "every version that runs in one column, under its caption");
  assert.equal(root.querySelector(".tip-btn").getAttribute("aria-label"), "Apply needed: a. b");
  assert.equal(behindWhy({ behind: [{ name: "Kafka", count: 1, causes: [{ kind: "config", services: [] }] }] }).title, "1 container differs from what is saved");
  assert.equal(behindWhy({ behind: [{ name: "Kafka", reason: "x" }] }), null, "no causes, no card: the lines say it");
  assert.equal(draw(applyBadge("x", "pill warn", [TAG, SETTINGS])).querySelectorAll(".pop-line").length, 2, "a row's card has a line for each of its causes");
});

test("a note of a catalog row is a badge: in its tone, a quiet button with an arrow when it leads to its fix, its explanation a tooltip", () => {
  const license = draw(noteBadge({ note: "Needs a license", note_tone: "warn", note_title: "No license saved", note_file: ".env" }));
  const button = license.querySelector("button");
  assert.deepEqual([button.getAttribute("class"), button.getAttribute("data-license"), button.getAttribute("data-tip"), text(button)],
    ["pill note warn", "form", "No license saved", "Needs a license"]);
  assert.ok(button.querySelector("svg use").getAttribute("href").endsWith("#i-arrow-right"));
  const via = draw(noteBadge({ note: "via Kafka Desk", note_tone: "", note_target: { kind: "addon", id: "kafka-desk" } }));
  assert.equal(via.querySelector("a").getAttribute("class"), "pill note");
  assert.equal(via.querySelector("a").getAttribute("data-tip"), null, "nothing more to say: no tooltip");
  const fix = draw(noteBadge({ note: "Needs a Cloud Token", note_tone: "warn", note_title: "No Cloud Token" }, "token"));
  assert.equal(fix.querySelector("button").getAttribute("data-fix"), "token");
  const outside = draw(noteBadge({ note: "Started by hand", note_title: "Kafka runs" }));
  assert.equal(outside.querySelector(".tip-btn").getAttribute("aria-label"), "Started by hand: Kafka runs", "more to say: it opens");
  const said = "Not from the Lab UI, so the platform does not start it.";
  assert.deepEqual(bubble(draw(noteBadge({ note: "Started by hand", note_title: said }))), ["Started by hand", said],
    "a sentence: a popover titled by its badge");
  assert.equal(text(draw(noteBadge({ note: "Partly on" })).querySelector(".pill")), "Partly on");
  assert.equal(noteBadge({ note: "" }), "");
  assert.ok(draw(noteBadge({ note: "Apply needed", note_tone: "warn", note_apply: true, note_causes: [TAG] })).querySelector(".tip-card"));
});

test("a note that says what an item needs is a card of its causes: the versions it needs as chips, the settings it lacks, each with where it is fixed", () => {
  const newer = draw(noteBadge({ note: "Needs newer versions", note_tone: "warn", note_target: { kind: "image", id: null }, note_title: "Needs 4.13",
    note_causes: [{ kind: "needs", name: "Access Management", version: "4.13" }, { kind: "needs", name: "API Management", version: "4.13.0-alpha.3" }] }));
  const link = newer.querySelector("a");
  assert.equal(link.getAttribute("href"), "#/images", "it leads to the Images page");
  const newerCard = JSON.parse(link.getAttribute("data-tip-card"));
  assert.equal(newerCard.title, "Needs newer versions");
  assert.equal(newerCard.foot, "Choose them on the Images page.");
  assert.deepEqual(newerCard.rows[0].lines, [[{ who: "Access Management" }, { code: "4.13" }, "or later"], [{ who: "API Management" }, { code: "4.13.0-alpha.3" }, "or later"]]);
  assert.equal(link.getAttribute("data-tip"), "Needs 4.13", "what a screen reader hears of it is the server's sentence");
  const unset = draw(noteBadge({ kind: "addon", id: "hybrid", note: "Needs settings", note_tone: "warn", note_title: "", note_causes: [{ kind: "unset", settings: ["HYBRID_CLOUD_TOKEN"] }] }));
  const button = unset.querySelector("button");
  assert.equal(button.getAttribute("data-settings"), "addon:hybrid", "it opens its own settings");
  assert.deepEqual(JSON.parse(button.getAttribute("data-tip-card")).rows[0].lines, [[{ code: "HYBRID_CLOUD_TOKEN" }]]);
  assert.equal(button.getAttribute("data-tip"), "Needs settings. HYBRID_CLOUD_TOKEN. Set it in its settings");
  // The foot agrees with what it is about: one setting is "it", several "them"; one version "it", several "them"
  const two = draw(noteBadge({ kind: "addon", id: "aws", note: "Needs settings", note_tone: "warn", note_causes: [{ kind: "unset", settings: ["A", "B"] }] }));
  assert.equal(JSON.parse(two.querySelector("button").getAttribute("data-tip-card")).foot, "Set them in its settings.");
  const one = draw(noteBadge({ note: "Needs newer versions", note_tone: "warn", note_target: { kind: "image", id: null }, note_causes: [{ kind: "needs", name: "API Management", version: "4.13" }] }));
  assert.equal(JSON.parse(one.querySelector("a").getAttribute("data-tip-card")).foot, "Choose it on the Images page.");
  // What of the license an item uses: under its lead, and where a license is set, which the badge opens
  const license = draw(noteBadge({ note: "Needs a license", note_tone: "warn", note_title: "Uses: Observability", note_file: ".env",
    note_causes: [{ kind: "license", features: ["Enterprise authorization engine", "Observability"] }] }));
  const form = license.querySelector("button");
  assert.equal(form.getAttribute("data-license"), "form");
  const card = JSON.parse(form.getAttribute("data-tip-card"));
  assert.deepEqual([card.title, card.rows[0].lines, card.foot], ["Needs a license", [[{ lead: "Uses" }, "Enterprise authorization engine, Observability"]], "Set a license in the license form."]);
});

// --- The link to the catalog an item comes from ----------------------------------------------------------------------------

const linkOf = (more = {}) => draw(originLink(item("addon", "@acme-hub/mock-bank", "Mock bank", { origin: origin(more) })));

test("an item of another catalog links to that catalog's row with its version after it, and draws no pill; the first catalog's and this machine's draw nothing", () => {
  const root = linkOf();
  const go = root.querySelector("a");
  assert.deepEqual([go.getAttribute("class"), go.getAttribute("href"), text(go)], ["name-link", "#/catalogs/acme-hub", "Acme Hub"]);
  assert.deepEqual(root.querySelectorAll(".muted").map(text), ["1.4.0"]);
  assert.equal(root.querySelectorAll(".pill, .tip").length, 0, "a name beside an item is not a badge");
  assert.equal(linkOf({ version: "" }).querySelectorAll(".muted").length, 0);
  // Nothing to tell apart, nothing drawn: no origin, a missing one, a nameless one, no item
  for (const found of [item("addon", "kafka", "Kafka"), item("addon", "local/x", "X", gains({ personal: true })), { origin: null }, { origin: {} }, { origin: { id: "acme-hub" } },
    {}, null, undefined]) {
    assert.equal(originLink(found), "", JSON.stringify(found));
  }
});

test("what a catalog calls itself is text: its name, its version and its namespace make no markup, in the link or in the address", () => {
  const root = linkOf({ name: HOSTILE, version: HOSTILE, id: HOSTILE_QUOTED });
  assert.equal(root.querySelectorAll("img, script").length, 0);
  assert.equal(text(root.querySelector("a")), HOSTILE);
  assert.equal(root.querySelector("a").getAttribute("href"), `#/catalogs/${HOSTILE_QUOTED}`);
  assert.equal(root.querySelectorAll("a").length, 1, "the address broke out of nothing");
  assert.equal(linkOf({ name: "<b>x</b>" }).querySelectorAll("b").length, 0);
});

// --- Notes that name a catalog or a refusal -------------------------------------------------------------------------------

const card = (root) => JSON.parse(root.querySelector("[data-tip-card]").getAttribute("data-tip-card"));
const needs = (reason, more = {}) => ({ note: "Needs a catalog", note_tone: "warn", note_title: "Needs the catalog Partner Bank", note_target: { kind: "catalog", id: null },
  note_causes: [{ ...NEEDS[reason], ...more }] });

test("the note of an item that needs a catalog is a pill that leads to the catalogs, with the catalog it lacks as its card", () => {
  const root = draw(noteBadge(needs("missing")));
  const link = root.querySelector("a");
  assert.deepEqual([link.getAttribute("class"), link.getAttribute("href"), link.getAttribute("data-tip"), text(link)],
    ["pill note warn", "#/catalogs", "Needs the catalog Partner Bank", "Needs a catalog"]);
  assert.deepEqual(card(root), { title: "Needs a catalog", rows: [{ lines: [["Needs the catalog Partner Bank"]] }], foot: "" });
  // Added already, but older than the item wants: the row opens
  const old = draw(noteBadge({ ...needs("old"), note: "Needs Partner Bank 2.0 or later", note_target: { kind: "catalog", id: "partner-bank" } }));
  assert.equal(old.querySelector("a").getAttribute("href"), "#/catalogs/partner-bank");
  assert.deepEqual(card(old).rows, [{ lines: [["Needs Partner Bank 2.0 or later"]] }]);
  // Several catalogs lacked: a line each
  const two = draw(noteBadge({ ...needs("missing"), note_causes: [NEEDS.missing, { ...NEEDS.failed, name: "Ledger Co" }] }));
  assert.deepEqual(card(two).rows[0].lines, [["Needs the catalog Partner Bank"], ["Needs Ledger Co"]]);
  assert.equal(card(two).foot, "");
  // Hostile names stay text
  assert.equal(draw(noteBadge(needs("missing", { name: HOSTILE }))).querySelectorAll("img").length, 0);
});

test("what an item needs of a catalog is said in a word for each reason, with the button that mends it", () => {
  const need = (reason, more = {}) => catalogNeed({ ...NEEDS[reason], ...more });
  assert.deepEqual(need("missing"), { line: "Needs the catalog Partner Bank", button: { act: "add", label: "Add Partner Bank" } });
  assert.deepEqual(need("downloading"), { line: "Downloading Partner Bank", button: null });
  assert.deepEqual(need("failed"), { line: "Needs Partner Bank", button: { act: "retry", label: "Try again" } });
  assert.deepEqual(need("gone"), { line: "Needs Ledger, which is no longer in Partner Bank", button: null });
  assert.deepEqual(need("old"), { line: "Needs Partner Bank 2.0 or later", button: { act: "update", label: "Update Partner Bank" } });
  // A version is said without a patch of 0, and with one when it is not
  assert.equal(need("old", { min: "1.2.0" }).line, "Needs Partner Bank 1.2 or later");
  assert.equal(need("old", { min: "2.1.3" }).line, "Needs Partner Bank 2.1.3 or later");
  assert.equal(need("old", { min: "2.0" }).line, "Needs Partner Bank 2.0 or later");
  assert.equal(need("old", { min: "10.0.0" }).line, "Needs Partner Bank 10.0 or later");
  assert.equal(need("old", { min: undefined }).line, "Needs Partner Bank or later");
  // The unit that is gone is named when the server names it, and an item is a unit like another when it does not
  assert.equal(need("gone", { unit: undefined }).line, "Needs an item, which is no longer in Partner Bank");
  // A reason this page does not know is a catalog that is not there
  assert.deepEqual(need("missing", { reason: "somewhere else" }), need("missing"));
  assert.deepEqual(catalogNeed({ name: "Partner Bank" }), need("missing"));
});

test("a note that blocks an item is a pill in the error tone with a card of its reasons, three at most, and who changes it", () => {
  const blocked = (cause, more = {}) => ({ note: "Blocked", note_tone: "err", note_title: cause.reasons?.[0] ?? "", note_causes: [cause], ...more });
  const root = draw(noteBadge(blocked(BLOCKED)));
  const tip = root.querySelector(".tip");
  assert.deepEqual([root.querySelector(".tip-btn").getAttribute("class"), text(root.querySelector(".tip-btn"))], ["tip-btn pill note err", "Blocked"]);
  assert.deepEqual(bubble(tip), ["Blocked", "Mounts the Docker socket", "Runs with full privileges", "Publishes a port to the network", "Ask the owner of Acme Hub to change it."]);
  // More than three: three, then how many more
  const five = draw(noteBadge(blocked({ ...BLOCKED, reasons: ["One", "Two", "Three", "Four", "Five"] })));
  assert.deepEqual(bubble(five.querySelector(".tip")).slice(1, -1), ["One", "Two", "Three", "and 2 more"]);
  assert.deepEqual(bubble(draw(noteBadge(blocked({ ...BLOCKED, reasons: ["One", "Two", "Three", "Four"] }))).querySelector(".tip")).slice(1, -1), ["One", "Two", "Three", "and 1 more"]);
  assert.deepEqual(bubble(draw(noteBadge(blocked({ ...BLOCKED, reasons: ["One"] }))).querySelector(".tip")).slice(1, -1), ["One"]);
  // A catalog that is a folder is changed by the person
  assert.equal(bubble(draw(noteBadge(blocked({ ...BLOCKED, folder: true }))).querySelector(".tip")).at(-1), "Change it in your catalog.");
  assert.equal(bubble(draw(noteBadge(blocked({ ...BLOCKED, owner: undefined }))).querySelector(".tip")).at(-1), "Ask the owner to change it.");
  // Text, not markup
  const hostile = draw(noteBadge(blocked({ ...BLOCKED, reasons: [HOSTILE, `${HOSTILE_QUOTED}`], owner: HOSTILE })));
  assert.equal(hostile.querySelectorAll("img, script").length, 0);
  assert.deepEqual(bubble(hostile.querySelector(".tip")).slice(1), [HOSTILE, HOSTILE_QUOTED, `Ask the owner of ${HOSTILE} to change it.`]);
  // The reasons come from a server: not a list is no reasons, and nothing of it is markup
  for (const reasons of [undefined, null, "Runs with full privileges", 5, {}]) {
    assert.deepEqual(bubble(draw(noteBadge(blocked({ ...BLOCKED, reasons }))).querySelector(".tip")), ["Blocked", "Ask the owner of Acme Hub to change it."], String(reasons));
  }
  // Its card opens though the server gave it no sentence of its own
  assert.ok(draw(noteBadge(blocked(BLOCKED, { note_title: "" }))).querySelector(".tip-card"));
  assert.ok(draw(noteBadge({ ...blocked(BLOCKED), note_title: undefined })).querySelector(".tip-card"));
  // ... and its name for a screen reader is its lines after its label, which is not said twice
  assert.equal(draw(noteBadge(blocked(BLOCKED, { note_title: "" }))).querySelector(".tip-btn").getAttribute("aria-label"),
    "Blocked: Mounts the Docker socket. Runs with full privileges. Publishes a port to the network. Ask the owner of Acme Hub to change it");
  assert.equal(draw(noteBadge({ note: "Needs a catalog", note_tone: "warn", note_causes: [NEEDS.missing] })).querySelector(".tip-btn").getAttribute("aria-label"),
    "Needs a catalog: Needs the catalog Partner Bank", "a note that leads nowhere, with its card only");
  assert.equal(draw(noteBadge(blocked(BLOCKED))).querySelector(".tip-btn").getAttribute("aria-label"), "Blocked: Mounts the Docker socket", "the sentence of the server, when it gives one, is as it was");
});

test("the card of a note says its foot for the kinds it knows, and none for one it does not: a page that does not know it still draws", () => {
  const odd = { note: "Odd", note_tone: "warn", note_title: "Something", note_causes: [{ kind: "something-new", services: [] }] };
  const root = draw(noteBadge(odd));
  assert.ok(root.querySelector(".tip-card"));
  assert.deepEqual(bubble(root.querySelector(".tip")), ["Odd", "Configuration changed"]);
  assert.equal(noteCard(odd), null, "the card of a note whose causes it does not know is not one to share");
  assert.equal(noteCard({ ...odd, note_causes: [] }), null);
  assert.equal(noteCard({ note: "Blocked", note_causes: [BLOCKED] }).foot, "Ask the owner of Acme Hub to change it.");
  assert.equal(noteCard({ note: "Needs a catalog", note_causes: [NEEDS.missing] }).foot, "");
  assert.equal(noteCard({ note: "Needs", note_causes: [{ kind: "needs", name: "API Management", version: "4.13" }] }).foot, "Choose it on the Images page.");
  // A name that every object has is not a kind: it is one this page does not know, as any other
  for (const kind of ["constructor", "toString", "valueOf", "hasOwnProperty", "__proto__"]) {
    const named = { ...odd, note_causes: [{ kind, services: [] }] };
    assert.equal(noteCard(named), null, kind);
    assert.deepEqual(bubble(draw(noteBadge(named)).querySelector(".tip")), ["Odd", "Configuration changed"], kind);
  }
  // The kinds of old say what they said
  assert.equal(noteCard({ note: "x", note_causes: [{ kind: "unset", settings: ["A"] }] }).foot, "Set it in its settings.");
  assert.equal(noteCard({ note: "x", note_causes: [{ kind: "unset", settings: ["A", "B"] }] }).foot, "Set them in its settings.");
  assert.equal(noteCard({ note: "x", note_causes: [{ kind: "license", features: ["Observability"] }] }).foot, "Set a license in the license form.");
});

// --- A popover that ends with a button -----------------------------------------------------------------------------------------

test("a popover may end with one button that leads somewhere; the others are as they were", () => {
  const plain = draw(tipHtml("x", { title: "Title", text: "A sentence." }));
  assert.equal(plain.querySelectorAll("a, button.btn").length, 0);
  assert.equal(plain.querySelector(".tip-card").getAttribute("aria-hidden"), "true");
  const led = draw(tipHtml("x", { title: "Title", text: "A sentence.", foot: "A foot.", action: { label: "Open", href: "#/somewhere" } }));
  const body = led.querySelector(".pop-body");
  assert.deepEqual(body.children.map((el) => el.getAttribute("class")), ["pop-title", "pop-text", "pop-foot", "btn"], "after everything else");
  assert.deepEqual([led.querySelector("a").getAttribute("href"), text(led.querySelector("a"))], ["#/somewhere", "Open"]);
  assert.equal(led.querySelector(".tip-btn").getAttribute("aria-label"), "Title. A sentence. A foot", "the words, not the button");
  const hostile = draw(tipHtml("x", { title: "T", action: { label: HOSTILE, href: `#/x${HOSTILE_QUOTED}` } }));
  assert.equal(hostile.querySelectorAll("img, script").length, 0);
  assert.equal(hostile.querySelectorAll("a").length, 1);
});

// --- The verb of a repair ---------------------------------------------------------------------------------------------------------

test("a repair is an action like the others: it works as Repairing and says Repair when it is over", () => {
  const root = draw(actionButton({ key: "catalog-op:acme-hub", verb: "repair", label: "Repair" }));
  const button = root.querySelector("button");
  assert.equal(button.querySelector(".pbtn-text").getAttribute("data-wait"), "Repairing\u2026");
  assert.equal(button.getAttribute("data-act-verb"), "repair");
  flash("catalog-op:acme-hub", "repair", true);
  paintActions(root);
  assert.ok(button.classList.contains("said") && button.classList.contains("done"));
  assert.equal(text(button.querySelector(".pbtn-text")), "Repair");
});
