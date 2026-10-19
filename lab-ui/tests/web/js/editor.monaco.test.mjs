// The editor popup (web/js/dialogs/editor.js) with Monaco there. editor.test.mjs has it fail to load, so that the plain text area stands in; this
// file has it come (a stand-in that keeps what the popup asks of it), because the read-only mode of a file of a git catalog is not the same code
// in the two: the text area has `readOnly`, Monaco takes it as an option of its editor, set each time a file is shown. One editor to a process
// (the popup starts it once), hence a file of its own.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, catalog, item, lab, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const HUB = ".lab/catalogs/acme-hub/playgrounds/bank";
const MINE = ".lab/catalogs/local/addons/mine";

const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path) => {
  const [route, query = ""] = path.split("?");
  const file = decodeURIComponent((query.match(/(?:path|file)=([^&]*)/) || [])[1] || "");
  if (route === "/api/state") return reply(store.state);
  if (route === "/api/files/content") return reply({ content: `# ${file}\n`, version: "v1" });
  if (route === "/api/files/validate") return reply({ problems: [] });
  if (route === "/api/files") return reply({ sections: [{ title: "Unit", on: false, runs: false, files: [{ path: file, name: file.split("/").pop(), kind: "compose" }] }] });
  return reply({});
} });
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "" });

// What the popup asks of Monaco: a model for each file, an editor with options it is given when it is created and again when they change
const options = [];
const models = [];
const model = (value) => {
  const one = { value, getValue: () => one.value, setValue: (text) => { one.value = text; }, dispose() {}, getLinesContent: () => one.value.split("\n"), getLineCount: () => 1, getLineMaxColumn: () => 1 };
  models.push(one);
  return one;
};
const monaco = {
  Uri: { parse: (address) => address },
  MarkerSeverity: { Error: 8, Warning: 4 },
  editor: {
    defineTheme() {}, setTheme() {}, setModelMarkers() {}, createModel: (value) => model(value),
    create: (host, given) => {
      options.push(["create", given.readOnly]);
      return { setModel(one) { this.model = one; }, updateOptions: (given2) => options.push(["update", given2.readOnly]), onDidChangeModelContent() {} };
    },
  },
};
window.require = Object.assign((wanted, done) => done(), { config() {} });
window.monaco = monaco;
page.doc.head.append = (script) => { queueMicrotask(() => script.onload()); };
const { store } = await import("../../../web/js/core.js");
const { openFile } = await import("../../../web/js/dialogs/editor.js");

store.state = lab({
  playgrounds: [item("playground", "@acme-hub/bank", "Acme Bakery", { file: `${HUB}/compose.yml`, display_file: "playgrounds/bank/compose.yml", readonly: true, origin: origin() })],
  addons: [item("addon", "mine", "Mine", { file: `${MINE}/compose.yml`, display_file: `${MINE}/compose.yml`, personal: true })],
  catalogs: [FIRST(), catalog()],
});
const dialog = page.doc.querySelector(".editor-dialog");
const $ = (selector) => dialog.querySelector(selector);
// The markup of the popup has the white space between its tags that a browser's segmented control skips (children is elements only there)
const trim = (el) => { el.children = el.children.filter((child) => child.tag); el.children.forEach(trim); };
trim($("#editor-view"));
const open = async (path) => {
  openFile(path);
  for (let i = 0; i < 4; i++) await flush();
};
const last = () => options[options.length - 1];

test("Monaco is made, and a file of an item of a catalog is shown with its readOnly option on, Save and Discard held with the reason", async () => {
  await open(`${HUB}/compose.yml`);
  assert.ok(options.length > 0, "Monaco came, so the plain text area is not the editor");
  assert.deepEqual(last(), ["update", true], "the file is shown read only");
  assert.deepEqual([isOff($("#editor-save")), offWhy($("#editor-save"))], [true, "Part of Acme Hub"]);
});

test("a file of this machine is shown with it off again, and the next catalog file with it on: the option follows the file, not the first one shown", async () => {
  await open(`${MINE}/compose.yml`);
  assert.deepEqual(last(), ["update", false], "a personal file can be typed in");
  assert.equal(offWhy($("#editor-save")), "No changes");
  await open(`${HUB}/compose.yml`);
  assert.deepEqual(last(), ["update", true]);
  await open(`${MINE}/compose.yml`);
  assert.deepEqual(last(), ["update", false]);
});
