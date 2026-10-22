// Gravitee Lab itself (web/js/dialogs/lab.js), run for real over a small DOM and a stub of what the server answers: the button of the menu says
// the version, a chip says that a newer one waits; the dialog says the version, whether it is the newest and when that was checked, what the
// newer versions change with the links of their pull requests, and the update, or in full what keeps it from being made here. An update
// started reloads the page once the Lab UI answers again from a new run with the new version; after it, what is new is said once.
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

test("after an update, what is new since the version this browser showed last is said once", async () => {
  state(LAB({ version: "1.1.0", latest: "1.1.0", update: false }));
  followLab();
  await settle();
  assert.ok(dialog.open);
  assert.equal(text(dialog.querySelector("#lab-title")), "What's new in Gravitee Lab 1.1.0");
  assert.match(said(dialog.querySelector("#lab-notes")), /1\.1\.0.*The guide can be hidden #16.*Playgrounds export to a file #15/);
  assert.doesNotMatch(said(dialog.querySelector("#lab-notes")), /First version/, "what the version it was already had is not said");
  assert.equal(dialog.querySelector("#lab-facts").textContent.includes("Check now"), false, "no checking in what is new");
  assert.equal(page.stored["lab:version"], "1.1.0");
  close();
  followLab();
  await settle();
  assert.equal(dialog.open, false, "said once");
});

test("the menu says the version, and a chip says that a newer one waits", () => {
  state(LAB({ update: false, latest: "1.0.0" }));
  paintLab();
  assert.equal(page.ids.lab.dataset.tip, "Gravitee Lab 1.0.0");
  assert.equal(page.ids["lab-update"].hidden, true);
  state(LAB());
  paintLab();
  assert.equal(page.ids["lab-update"].hidden, false);
  assert.equal(page.ids["lab-update-text"].textContent, "Update to 1.1.0");
  store.offline = true;
  paintLab();
  assert.equal(page.ids["lab-update"].hidden, true, "no chip while the Lab UI does not answer");
  store.offline = false;
  state(LAB({ version: null }));
  paintLab();
  assert.equal(page.ids.lab.dataset.tip, "Gravitee Lab");
});

test("the dialog says the version, the newer one, what it changes and the update", async () => {
  state(LAB());
  openLab();
  await settle();
  assert.equal(text(dialog.querySelector("#lab-title")), "Gravitee Lab");
  const facts = said(dialog.querySelector("#lab-facts"));
  assert.match(facts, /Version 1\.0\.0/);
  assert.match(facts, /Updates 1\.1\.0 available Checked 5 min Check now/);
  const notes = dialog.querySelector("#lab-notes");
  assert.match(said(notes), /^1\.1\.0 .*The guide can be hidden #16 Playgrounds export to a file #15$/);
  assert.equal(notes.querySelector("a[href]").getAttribute("href"), "https://github.com/acme/gravitee-lab/releases/tag/v1.1.0");
  assert.equal(notes.querySelector("li a").getAttribute("href"), "https://github.com/acme/gravitee-lab/pull/16");
  const button = dialog.querySelector("#lab-primary button");
  assert.equal(text(button.querySelector(".pbtn-face")), "Update to 1.1.0");
  assert.equal(isOff(button), false);
  assert.equal(dialog.querySelector("#lab-problem").hidden, true);
  close();
});

test("a Lab on the newest version says the changes of its own, and a development version its commit", async () => {
  state(LAB({ latest: "1.0.0", update: false }));
  openLab();
  await settle();
  assert.match(said(dialog.querySelector("#lab-facts")), /Updates Up to date Checked 5 min/);
  assert.match(said(dialog.querySelector("#lab-notes")), /^1\.0\.0 .*First version$/);
  assert.equal(dialog.querySelector("#lab-primary button"), null);
  state(LAB({ version: null, latest: null, update: false, ahead: 0, checked: null }));
  openLab();
  await settle();
  assert.match(said(dialog.querySelector("#lab-facts")), /Version Development version abc1234 Updates Not checked yet/);
  state(LAB({ ahead: 2, update: false, latest: "1.0.0" }));
  openLab();
  await settle();
  assert.match(said(dialog.querySelector("#lab-facts")), /Version 1\.0\.0 and 2 newer changes/);
  close();
});

test("what keeps the update from being made here is said in full, and the button says it too", async () => {
  const why = "This Lab is on the branch mine. Switch to main to update";
  state(LAB({ blocked: why }));
  openLab();
  await settle();
  assert.equal(text(dialog.querySelector("#lab-problem")), why);
  assert.equal(dialog.querySelector("#lab-problem").hidden, false);
  const button = dialog.querySelector("#lab-primary button");
  assert.ok(isOff(button));
  assert.equal(offWhy(button), why);
  server.calls.length = 0;
  button.click();
  await settle();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST"), []);
  close();
});

test("Check now asks the server, and a failed check says why", async () => {
  state(LAB({ update: false, latest: "1.0.0" }));
  openLab();
  await settle();
  server.calls.length = 0;
  dialog.querySelector("[data-check]").click();
  await settle();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => call.path), ["/api/lab/check"]);
  state(LAB({ update: false, latest: "1.0.0", error: "github.com did not answer", checked: null }));
  paintLab();
  assert.match(said(dialog.querySelector("#lab-facts")), /Updates github\.com did not answer Check now/);
  state(LAB({ checking: true }));
  paintLab();
  assert.ok(isOff(dialog.querySelector("[data-check]")));
  close();
});

test("the update starts, then the page reloads once a new run of the Lab UI is the new version", async () => {
  state(LAB());
  openLab();
  await settle();
  server.calls.length = 0;
  dialog.querySelector("[data-update]").click();
  await settle();
  assert.deepEqual(server.calls.filter((call) => call.method === "POST").map((call) => [call.path, call.body]), [["/api/lab/update", { version: "1.1.0" }]]);
  assert.equal(dialog.open, false);
  assert.deepEqual(JSON.parse(page.stored["lab:updating"]), { version: "1.1.0", run: "run-1" });
  assert.equal(page.stored["lab:version"], "1.0.0");
  state(LAB({ version: "1.1.0", update: false, latest: "1.1.0" }));
  followLab();
  assert.equal(reloads, 0, "the Lab UI the update was started from says the new version before it starts again");
  state(LAB({ version: "1.1.0", update: false, latest: "1.1.0", run: "run-2" }));
  followLab();
  assert.equal(reloads, 1);
  assert.equal(page.stored["lab:updating"], undefined);
  followLab();
  assert.equal(reloads, 1, "once");
});
