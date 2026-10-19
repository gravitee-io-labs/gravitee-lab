// The editor popup (web/js/dialogs/editor.js) opens a file of an item of a git catalog to be read and not written: Monaco's (here the plain text
// area that stands in for it) is read only, Save and Discard say why they cannot be used, a neutral "Read only" pill stands beside "Unsaved", the
// path shown is the one in the catalog and never the one on this machine, and the file is not sent to be checked. The settings of the same item
// are the person's and open as ever, and so does any file of a folder catalog or of this machine. The file buttons of an item read View for
// a file that is only read.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, catalog, gains, item, lab, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const HUB = ".lab/catalogs/acme-hub/playgrounds/bank";
const FIRSTS = ".lab/catalogs/gravitee/addons/kafka";
const MINE = ".lab/catalogs/local/addons/mine";
const FOLDER = ".lab/catalogs/my-catalog/addons/own";

// What the server answers, and every call made
const server = { calls: [], files: {} };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  const [route, query = ""] = path.split("?");
  server.calls.push({ method: options.method || "GET", path, body: options.body ? JSON.parse(options.body) : undefined });
  const file = decodeURIComponent((query.match(/(?:path|file)=([^&]*)/) || [])[1] || "");
  if (route === "/api/state") return reply(store.state);
  if (route === "/api/files/content") return reply({ content: server.files[file] ?? `# ${file}\n`, version: "v1" });
  if (route === "/api/files/validate") return reply({ problems: [] });
  if (route === "/api/files/save") return reply({ content: "saved", version: "v2", problems: [] });
  if (route === "/api/env") return reply({ version: "e1", sections: [{ title: "Settings", kind: "plain", groups: [{ description: "", vars: [{ key: "A_SETTING", value: "1", default: "" }] }] }] });
  if (route === "/api/files") {
    const unit = file.replace(/[^/]+$/, "");
    return reply({ sections: [{ title: "Unit", on: false, runs: false, files: [{ path: file, name: file.split("/").pop(), kind: file.endsWith("guide.md") ? "guide" : "compose" },
      { path: `${unit}.env`, name: ".env", kind: "env" }] }] });
  }
  return reply({});
} });
// Monaco does not come: a plain text area is the editor, as it is without the internet
page.doc.head.append = (script) => { queueMicrotask(() => script.onerror()); };
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "" });
const { store } = await import("../../../web/js/core.js");
const { openFile } = await import("../../../web/js/dialogs/editor.js");
const { fileButtons } = await import("../../../web/js/components/ui.js");

const playground = (more = {}) => item("playground", "@acme-hub/bank", "Acme Bakery", { file: `${HUB}/compose.yml`, display_file: "playgrounds/bank/compose.yml", readonly: true,
  origin: origin(), settings: [], env_file: `${HUB}/.env`, ...more });
const state = () => lab({
  playgrounds: [playground(), item("playground", "personal", "Personal one", { file: ".lab/catalogs/local/playgrounds/personal/compose.yml", personal: true, ...gains({ personal: true }),
    display_file: ".lab/catalogs/local/playgrounds/personal/compose.yml" })],
  addons: [
    item("addon", "kafka", "Kafka", { file: `${FIRSTS}/compose.yml`, display_file: "addons/kafka/compose.yml", readonly: true }),
    item("addon", "own", "Own", { file: `${FOLDER}/compose.yml`, display_file: "addons/own/compose.yml", origin: origin({ id: "my-catalog", name: "My catalog" }) }),
    item("addon", "mine", "Mine", { file: `${MINE}/compose.yml`, display_file: `${MINE}/compose.yml`, personal: true }),
  ],
  catalogs: [FIRST(), catalog()],
});
store.state = state();
const dialog = page.doc.querySelector(".editor-dialog");
const $ = (selector) => dialog.querySelector(selector);
// The markup of the popup has the white space between its tags that a browser's segmented control skips (children is elements only there)
const trim = (el) => { el.children = el.children.filter((child) => child.tag); el.children.forEach(trim); };
trim($("#editor-view"));
$("#env-form").checkValidity = () => true;   // a browser tells whether the required fields are filled
const shown = () => ({ path: text($("#editor-path")), dirty: !$("#editor-dirty").hidden, readonly: !$("#editor-readonly").hidden, readonlyWords: text($("#editor-readonly")) });
const area = () => $("textarea");
const posted = (route) => server.calls.filter((call) => call.path.startsWith(route));
const open = async (path, options) => {
  server.calls = [];
  openFile(path, options);
  await flush();
  await flush();
  await flush();
};

test("a file of an item of another catalog opens read only: its path in the catalog, a Read only pill, Save and Discard held with the reason", async () => {
  await open(`${HUB}/compose.yml`);
  assert.deepEqual(shown(), { path: "playgrounds/bank/compose.yml", dirty: false, readonly: true, readonlyWords: "Read only" });
  assert.equal(area().readOnly, true, "nothing can be typed");
  assert.equal(area().value, `# ${HUB}/compose.yml\n`);
  assert.deepEqual([isOff($("#editor-save")), offWhy($("#editor-save"))], [true, "Part of Acme Hub"]);
  assert.deepEqual([isOff($("#editor-discard")), offWhy($("#editor-discard"))], [true, "Part of Acme Hub"]);
  assert.equal($("#editor-readonly").getAttribute("class"), "pill", "neutral: nothing is wrong with it");
  assert.equal($("#editor-dirty").getAttribute("class"), "pill warn");
  assert.ok(!page.doc.querySelector("#editor-path").textContent.includes(".lab"), "the machine's path is never shown");
});

test("it is not sent to be checked, and no key, button or Discard saves it", async () => {
  assert.deepEqual(posted("/api/files/validate"), [], "the server would refuse it: it is not asked");
  $("#editor-status").innerHTML = "";
  assert.equal(text($("#editor-status")), "", "no Valid, no problems: nothing of it can change");
  assert.equal($("#editor-problems").hidden, true);
  server.calls = [];
  dialog.dispatchEvent(Object.assign(new Ev("keydown"), { key: "s", metaKey: true, target: area() }));
  $("#editor-save").click();
  $("#editor-discard").click();
  await flush();
  assert.deepEqual(posted("/api/files/save"), []);
});

test("the pane names its files by their path in the catalog", async () => {
  const tips = () => $("#file-list").querySelectorAll(".file-item").map((button) => [button.getAttribute("data-path"), button.getAttribute("data-tip")]);
  assert.deepEqual(tips(), [[`${HUB}/compose.yml`, "playgrounds/bank/compose.yml"], [`${HUB}/.env`, `${HUB}/.env`]]);
});

test("the settings of the same item are the person's: the form opens, is editable and saves", async () => {
  await open(`${HUB}/.env`);
  assert.equal($("#env-form").hidden, false);
  assert.equal($("#editor-readonly").hidden, true);
  assert.equal(text($("#editor-path")), `${HUB}/.env`, "a file that is no catalog's is shown as it is");
  const input = $("#env-form input");
  input.setAttribute("value", "2");
  $("#env-form").dispatchEvent(Object.assign(new Ev("input"), { target: input }));
  assert.equal(isOff($("#editor-save")), false, "a setting changed can be saved");
  assert.equal($("#editor-dirty").hidden, false);
  await flush();
});

test("the guide of a playground of a catalog is read only too, by its own path in the catalog", async () => {
  await open(`${HUB}/guide.md`);
  assert.deepEqual(shown(), { path: "playgrounds/bank/guide.md", dirty: false, readonly: true, readonlyWords: "Read only" });
  assert.equal(offWhy($("#editor-save")), "Part of Acme Hub");
});

test("an item of the first catalog says that catalog's name, which its items do not carry", async () => {
  await open(`${FIRSTS}/compose.yml`);
  assert.deepEqual(shown(), { path: "addons/kafka/compose.yml", dirty: false, readonly: true, readonlyWords: "Read only" });
  assert.equal(offWhy($("#editor-save")), "Part of Gravitee public");
  store.state = { ...state(), catalogs: [] };
  await open(`${FIRSTS}/compose.yml`);
  assert.equal(offWhy($("#editor-save")), "Part of its catalog", "a state that does not say which: still not written");
  store.state = state();
});

test("a file of a folder catalog or of this machine opens for editing, as ever: checked as it is typed, and saved; a catalog's is shown by its path in it", async () => {
  for (const [path, said] of [[`${FOLDER}/compose.yml`, "addons/own/compose.yml"], [`${MINE}/compose.yml`, `${MINE}/compose.yml`]]) {
    await open(path);
    assert.deepEqual(shown(), { path: said, dirty: false, readonly: false, readonlyWords: "Read only" }, path);
    assert.equal(area().readOnly, false, path);
    assert.ok(posted("/api/files/validate").length > 0, `${path}: it is checked`);
    assert.equal(offWhy($("#editor-save")), "No changes", path);
    area().value = "services: {}\n";
    area().dispatchEvent(new Ev("input"));
    mock.timers.tick(700);   // the check follows typing
    await flush();
    assert.deepEqual([$("#editor-dirty").hidden, isOff($("#editor-save"))], [false, false], `${path}: typed, it can be saved`);
    $("#editor-discard").click();
    await flush();
  }
});

test("a file that no item of the state names opens as it always did", async () => {
  await open("docker-compose.yml");
  assert.deepEqual(shown(), { path: "docker-compose.yml", dirty: false, readonly: false, readonlyWords: "Read only" });
  assert.equal(area().readOnly, false);
  store.state = null;
  await open("docker-compose.yml");
  assert.equal(shown().readonly, false, "before the state is read nothing is held");
  store.state = state();
});

// --- The file buttons of an item -----------------------------------------------------------------------------------------------------

test("the file button of an item that is only read says View with the file's name alone; any other says Edit with the path it is read by", () => {
  const buttons = (unit, aligned) => {
    const holder = page.doc.createElement("div");
    holder.innerHTML = fileButtons(unit, aligned);
    return holder.querySelectorAll("button").map((button) => [button.getAttribute("data-file"), button.getAttribute("data-tip"), button.getAttribute("aria-label")]);
  };
  assert.deepEqual(buttons(playground()), [[`${HUB}/compose.yml`, "View compose.yml", "View compose.yml"]]);
  // The file it opens is always its own; the path it says is the one the item is read by (`display_file`), and its own when there is none
  assert.deepEqual(buttons(playground({ readonly: false })), [[`${HUB}/compose.yml`, "Edit playgrounds/bank/compose.yml", "Edit playgrounds/bank/compose.yml"]]);
  assert.deepEqual(buttons(playground({ readonly: false, display_file: "" }))[0].slice(1), [`Edit ${HUB}/compose.yml`, `Edit ${HUB}/compose.yml`], "none given: its own");
  assert.deepEqual(buttons({ ...playground({ readonly: false }), display_file: undefined })[0].slice(1), [`Edit ${HUB}/compose.yml`, `Edit ${HUB}/compose.yml`]);
  assert.deepEqual(buttons({ ...playground(), readonly: undefined }).length, 1, "a state that does not say is an item that can be edited");
  // Its settings are its owner's to change, a key like any
  const keyed = buttons(playground({ settings: [{ key: "A_SETTING" }] }));
  assert.deepEqual(keyed.map((button) => button[1]), ["View compose.yml", "Settings"]);
  assert.equal(fileButtons(playground({ settings: [] }), false).includes("icon-gap"), false, "not aligned: no gap");
  assert.ok(fileButtons(playground({ settings: [] }), true).includes("icon-gap"), "aligned among rows that have settings: the slot is kept");
});

test("the words of a file button are drawn by ui.js alone, in its tooltip and its name, and a file that is only read never says Edit", () => {
  const unit = playground();
  const drawn = fileButtons(unit, false);
  assert.ok(drawn.includes('data-tip="View compose.yml" aria-label="View compose.yml"'));
  assert.ok(!drawn.includes("Edit"), "a file that is only read never says Edit");
  assert.ok(fileButtons({ ...unit, readonly: false }, false).includes('data-tip="Edit playgrounds/bank/compose.yml" aria-label="Edit playgrounds/bank/compose.yml"'));
  // The editor opens on the real file, whatever the words say
  assert.ok(drawn.includes(`data-file="${unit.file}"`));
});

test("a name that holds the patterns of a replacement is text too", () => {
  const holder = page.doc.createElement("div");
  for (const [more, said] of [[{ file: "a$&b/$1/compose.yml", display_file: "" }, "View compose.yml"],
    [{ file: "x/compose.yml", display_file: "dir$`/$'x/compose.yml", readonly: false }, "Edit dir$`/$'x/compose.yml"]]) {
    holder.innerHTML = fileButtons(playground(more));
    assert.deepEqual([holder.querySelector("button").getAttribute("data-tip"), holder.querySelector("button").getAttribute("aria-label")], [said, said]);
  }
});

test("a name that is hostile is escaped in the file button", () => {
  const hostile = playground({ file: '"><img src=x onerror=boom()>/compose.yml' });
  const holder = page.doc.createElement("div");
  holder.innerHTML = fileButtons(hostile);
  assert.equal(holder.querySelectorAll("img").length, 0);
  assert.equal(holder.querySelector("button").getAttribute("data-tip"), "View compose.yml");
});
