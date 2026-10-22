// Gravitee Lab itself (web/js/dialogs/lab.js), run for real over a small DOM and a stub of what the server answers: the chip at the right of
// the Lab's name says the version, or Update while a newer one waits; the dialog says the version, then one card with where this Lab stands
// and the one thing to do about it (update, check again, or what keeps the update from being made here), then what the versions change with
// the links of their pull requests. An update keeps the dialog open while it runs, and reloads the page once the Lab UI answers again from a
// new run with the new version; after it, what is new is said once.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const change = (text, number) => ({ text, number, url: number ? `https://github.com/acme/gravitee-lab/pull/${number}` : null });
const RELEASES = {
  repository: "acme/gravitee-lab",
  url: "https://github.com/acme/gravitee-lab/releases",
  versions: [
    { version: "1.1.0", date: "2026-10-20T10:00:00Z", newer: true, current: false, url: "https://github.com/acme/gravitee-lab/releases/tag/v1.1.0",
      changes: [change("The guide can be hidden", 16), change("Playgrounds export to a file", 15)] },
    { version: "1.0.0", date: "2026-10-01T10:00:00Z", newer: false, current: true, url: "https://github.com/acme/gravitee-lab/releases/tag/v1.0.0",
      changes: [change("First version", null)] },
  ],
};
const LAB = (more = {}) => ({ version: "1.0.0", ahead: 0, commit: "abc1234", run: "run-1", latest: "1.1.0", update: true,
  checked: new Date(Date.now() - 5 * 60000).toISOString(), checking: false, error: null, blocked: null, ...more });

const server = { calls: [], releases: RELEASES };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  stored: { "lab:version": "1.0.0" },
  fetch: async (path, options = {}) => {
    const method = options.method || "GET";
    if (path === "/api/state") return reply(store.state);
    server.calls.push({ method, path, body: options.body ? JSON.parse(options.body) : undefined });
    if (path === "/api/lab") return reply(server.releases);
    if (path === "/api/lab/check") return reply(store.state.lab);
    if (path === "/api/lab/update") return reply({ id: "job-1", title: "Update Gravitee Lab to 1.1.0", done: false });
    return reply({});
  },
});
let reloads = 0;
location.reload = () => { reloads += 1; };
globalThis.EventSource = class { close() {} addEventListener() {} };
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
const { store } = await import("../../../web/js/core.js");
const { openLab, followLab, paintLab } = await import("../../../web/js/dialogs/lab.js");
const dialog = page.doc.querySelector(".lab-dialog");
const state = (lab) => { store.state = { blind: false, wait: "", docker: { error: null }, services: {}, playgrounds: [], setups: [], addons: [], lab }; };
const settle = async () => { for (let i = 0; i < 4; i += 1) await flush(); };
// What a node says, its elements apart: the small DOM puts no space between two of them
const said = (node) => node.innerHTML.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const close = () => { if (dialog.open) dialog.close(); };

const current = () => said(dialog.querySelector("#lab-current"));
const status = () => dialog.querySelector(".lab-status-card");
const notes = () => dialog.querySelector("#lab-notes");
// The card as a person reads it: its tone, what it says, and the face of its button (an action button keeps its working words hidden)
const card = () => { const face = status().querySelector(".pbtn-face"); return [status().getAttribute("data-tone"), said(status().querySelector(".lab-status-text")), face ? text(face) : ""]; };

test("after an update, what is new since the version this browser showed last is said once", async () => {
  state(LAB({ version: "1.1.0", latest: "1.1.0", update: false }));
  followLab();
  await settle();
  assert.ok(dialog.open);
  assert.equal(text(dialog.querySelector("#lab-title")), "What's new in Gravitee Lab 1.1.0");
  assert.deepEqual(card(), ["ok", "Updated From 1.0.0", ""]);
  assert.equal(text(dialog.querySelector("#lab-notes-head h3")), "What's new");
  assert.match(said(notes()), /1\.1\.0.*The guide can be hidden #16.*Playgrounds export to a file #15/);
  assert.doesNotMatch(said(notes()), /First version/, "what the version it was already had is not said");
  assert.equal(dialog.querySelector("[data-check]"), null, "no checking in what is new");
  assert.equal(page.stored["lab:version"], "1.1.0");
  close();
  followLab();
  await settle();
  assert.equal(dialog.open, false, "said once");
});

test("the version stands at the right of the name of the Lab, and turns into Update while a newer one waits", () => {
  const button = page.ids.lab, dot = page.ids["lab-dot"];
  state(LAB({ update: false, latest: "1.0.0" }));
  paintLab();
  assert.equal(button.hidden, false);
  assert.equal(text(button), "1.0.0");
  assert.equal(button.classList.contains("update"), false);
  assert.equal(button.dataset.tip, "Gravitee Lab 1.0.0");
  assert.equal(dot.hidden, true);
  state(LAB());
  paintLab();
  assert.equal(text(button), "Update");
  assert.ok(button.classList.contains("update"));
  assert.equal(button.dataset.tip, "Gravitee Lab 1.1.0 is available");
  assert.equal(dot.hidden, false, "the collapsed menu shows a dot on the logo");
  store.offline = true;
  paintLab();
  assert.equal(dot.hidden, true, "no update said while the Lab UI does not answer");
  assert.equal(text(button), "1.0.0");
  store.offline = false;
  state(LAB({ version: null, update: false, latest: null }));
  paintLab();
  assert.equal(button.dataset.tip, "Development version abc1234");
  assert.equal(text(button), "dev");
});

test("the dialog says the version, then the newer one with Update now, then what it changes", async () => {
  state(LAB());
  openLab();
  await settle();
  assert.equal(text(dialog.querySelector("#lab-title")), "Gravitee Lab");
  assert.match(current(), /^1\.0\.0 /);
  assert.equal(status().getAttribute("data-tone"), "update");
  assert.match(said(status()), /^Gravitee Lab 1\.1\.0 is available Released /);
  const button = status().querySelector("[data-update]");
  assert.equal(text(button.querySelector(".pbtn-face")), "Update now");
  assert.equal(isOff(button), false);
  assert.equal(text(dialog.querySelector("#lab-notes-head h3")), "What's new");
  const all = dialog.querySelector("#lab-notes-head a");
  assert.deepEqual([all.getAttribute("href"), all.getAttribute("target")], ["https://github.com/acme/gravitee-lab/releases", "_blank"]);
  assert.match(said(notes()), /^1\.1\.0 .*The guide can be hidden #16 Playgrounds export to a file #15$/);
  assert.equal(notes().querySelector("a[href]").getAttribute("href"), "https://github.com/acme/gravitee-lab/releases/tag/v1.1.0");
  assert.equal(notes().querySelector("li a").getAttribute("href"), "https://github.com/acme/gravitee-lab/pull/16");
  close();
});

test("a Lab on the newest version says so with Check now and the changes of its own; a development version says its commit", async () => {
  state(LAB({ latest: "1.0.0", update: false }));
  openLab();
  await settle();
  assert.deepEqual(card(), ["ok", "Up to date Checked 5 min", "Check now"]);
  assert.equal(text(dialog.querySelector("#lab-notes-head h3")), "In this version");
  assert.match(said(notes()), /^1\.0\.0 .*First version$/);
  assert.equal(dialog.querySelector("[data-update]"), null);
  state(LAB({ version: null, latest: null, update: false, ahead: 0, checked: null }));
  openLab();
  await settle();
  assert.equal(current(), "Development version abc1234");
  assert.deepEqual(card(), ["idle", "Not checked yet", "Check now"]);
  state(LAB({ ahead: 2, update: false, latest: "1.0.0" }));
  openLab();
  await settle();
  assert.match(current(), /^1\.0\.0 .* 2 newer changes$/);
  close();
});

test("what keeps the update from being made here is said in full in the card, which then offers no button", async () => {
  const why = "This Lab is on the branch mine. Switch to main to update";
  state(LAB({ blocked: why }));
  openLab();
  await settle();
  assert.deepEqual(card(), ["update", `Gravitee Lab 1.1.0 is available ${why}`, ""]);
  assert.equal(status().querySelector("button"), null);
  close();
});

test("Check now asks the server, a check that runs says so, and a failed one says why with Try again", async () => {
  state(LAB({ update: false, latest: "1.0.0" }));
  openLab();
  await settle();
  server.calls.length = 0;
  dialog.querySelector("[data-check]").click();
  await settle();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => call.path), ["/api/lab/check"]);
  state(LAB({ update: false, latest: "1.0.0", error: "github.com did not answer", checked: null }));
  paintLab();
  assert.deepEqual(card(), ["error", "Could not check for updates github.com did not answer", "Try again"]);
  state(LAB({ update: false, latest: "1.0.0", checking: true }));
  paintLab();
  assert.deepEqual(card(), ["working", "Checking for updates Checked 5 min", ""]);
  close();
});

test("the update starts and the dialog stays on it while it runs, then the page reloads once a new run of the Lab UI is the new version", async () => {
  state(LAB());
  openLab();
  await settle();
  server.calls.length = 0;
  dialog.querySelector("[data-update]").click();
  await settle();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => [call.path, call.body]), [["/api/lab/update", { version: "1.1.0" }]]);
  assert.ok(dialog.open, "the dialog follows the update");
  assert.deepEqual(JSON.parse(page.stored["lab:updating"]), { version: "1.1.0", run: "run-1" });
  assert.equal(page.stored["lab:version"], "1.0.0");
  paintLab();
  assert.deepEqual(card(), ["working", "Updating to 1.1.0 Restarts when done", ""]);
  state(LAB({ version: "1.1.0", update: false, latest: "1.1.0" }));
  followLab();
  assert.equal(reloads, 0, "the Lab UI the update was started from says the new version before it starts again");
  state(LAB({ version: "1.1.0", update: false, latest: "1.1.0", run: "run-2" }));
  followLab();
  assert.equal(reloads, 1);
  assert.equal(page.stored["lab:updating"], undefined);
  followLab();
  assert.equal(reloads, 1, "once");
  close();
});

test("an update whose job failed is no longer said as running", async () => {
  state(LAB());
  page.stored["lab:updating"] = JSON.stringify({ version: "1.1.0", run: "run-1" });
  store.state.job = { for: "lab", verb: "update", done: true, ok: false };
  followLab();
  assert.equal(page.stored["lab:updating"], undefined);
  openLab();
  await settle();
  assert.equal(status().getAttribute("data-tone"), "update");
  close();
});
