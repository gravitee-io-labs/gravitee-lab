// What the browser kept from an earlier visit can be anything: another tool wrote to the same origin, or a value was edited by hand. The page
// reads it only as the kind of value it expects, and a foreign one never keeps it from drawing: the theme (the small script web/js/boot.js, then
// the button of the menu, web/js/components/sidebar.js) is light or dark, and the Stack's open groups (web/js/pages/stack.js) are an object.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";

import { installDom } from "./dom-stub.mjs";

const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$/.test(id));
const page = installDom({ ids });
const { recallObject } = await import("../../../web/js/core.js");

const FOREIGN = ["system", "Dark", " dark", "dark ", "auto", "", "0", "null", "undefined", "__proto__", "constructor", "toString", "<b>", "light dark"];

// The script that sets the theme and the menu before anything is drawn (web/js/boot.js, which index.html loads), run over the stand-ins of
// what it reaches
const inline = readFileSync(new URL("../../../web/js/boot.js", import.meta.url), "utf8");
function beforeDraw(stored, { narrow = false, broken = false } = {}) {
  const classes = new Set(), root = { dataset: {} };
  const document = { documentElement: root, body: { classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), add: (name) => classes.add(name) } } };
  const localStorage = { getItem: (key) => { if (broken) throw new Error("blocked"); return key in stored ? stored[key] : null; } };
  runInNewContext(inline, { document, localStorage, matchMedia: () => ({ matches: narrow }) });
  return { theme: root.dataset.theme, collapsed: classes.has("nav-collapsed") };
}

test("the page starts in the theme that was chosen, light or dark, and in the system's for any other value", () => {
  assert.equal(beforeDraw({ theme: "light" }).theme, "light");
  assert.equal(beforeDraw({ theme: "dark" }).theme, "dark");
  assert.equal(beforeDraw({}).theme, undefined);
  for (const value of FOREIGN) assert.equal(beforeDraw({ theme: value }).theme, undefined, JSON.stringify(value));
});

test("the menu starts as it was left, collapsed in a narrow window whatever was kept, and storage that is blocked stops nothing", () => {
  assert.equal(beforeDraw({ sidebar: "collapsed" }).collapsed, true);
  for (const value of ["open", "", "yes", "__proto__"]) assert.equal(beforeDraw({ sidebar: value }).collapsed, false, value);
  assert.equal(beforeDraw({ sidebar: "open" }, { narrow: true }).collapsed, true);
  assert.deepEqual(beforeDraw({ theme: "dark" }, { broken: true }), { theme: undefined, collapsed: false });
});

// The button of the menu as the page has it when initSidebar runs: a new one each time, so that none holds the clicks of an earlier run
function freshButton() {
  page.ids.theme.remove();
  const button = page.doc.createElement("button");
  button.id = "theme";
  page.body.append(button);
  page.ids.theme = button;
  return button;
}

test("the theme button says the system's theme for a value that is none, and goes on from there", async () => {
  const { initSidebar } = await import("../../../web/js/components/sidebar.js");
  const root = page.root;
  for (const value of FOREIGN) {
    root.dataset.theme = value;
    const button = freshButton();
    initSidebar();
    assert.equal(root.dataset.theme, undefined, `${JSON.stringify(value)} is no theme: the system's applies`);
    assert.equal(button.dataset.tip, "System theme", JSON.stringify(value));
  }
  root.dataset.theme = "toString";
  const button = freshButton();
  initSidebar();
  const labels = [];
  for (let click = 0; click < 3; click++) {
    button.click();
    labels.push([root.dataset.theme, button.dataset.tip, page.stored.theme]);
  }
  assert.deepEqual(labels, [["dark", "Dark theme", "dark"], ["light", "Light theme", "light"], [undefined, "System theme", undefined]]);
});

test("the open groups of the Stack are kept as an object, and anything else kept is none", () => {
  const kept = (value) => { page.stored.expanded = value; return recallObject("expanded"); };
  assert.deepEqual(kept('{"group:apim":true,"addon:kafka":false}'), { "group:apim": true, "addon:kafka": false });
  for (const value of ["{not json", "", "null", "[]", '["group:apim"]', "5", "true", '"group:apim"', "{", "undefined"]) {
    assert.deepEqual(kept(value), {}, JSON.stringify(value));
  }
  delete page.stored.expanded;
  assert.deepEqual(recallObject("expanded"), {}, "nothing kept");
});

test("the Stack loads whatever was kept of its open groups", async () => {
  for (const [turn, value] of ["{not json", "null", "[]", "5", '"x"', "true"].entries()) {
    page.stored.expanded = value;
    await import(`../../../web/js/pages/stack.js?kept=${turn}`);
  }
});
