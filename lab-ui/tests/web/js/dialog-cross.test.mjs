// The cross at the top right of every dialog (web/js/components/ui.js): the one of the large dialogs' head, or the one made when a dialog opens.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { Ev, installDom } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids, fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
await import("../../../web/js/components/ui.js");

const made = (markup) => {
  const dialog = page.doc.createElement("dialog");
  dialog.innerHTML = markup;
  page.doc.body.append(dialog);
  return dialog;
};
const crossOf = (dialog) => dialog.querySelectorAll(".dialog-x");

test("a dialog that opens has its cross, once, however many times it opens", () => {
  const dialog = made('<form><h2>New</h2><div class="dialog-actions"><button type="button" data-cancel>Cancel</button></div></form>');
  assert.equal(crossOf(dialog).length, 0);
  dialog.showModal();
  dialog.close();
  dialog.showModal();
  const [cross] = crossOf(dialog);
  assert.equal(crossOf(dialog).length, 1);
  assert.deepEqual([cross.getAttribute("aria-label"), cross.getAttribute("class"), cross.getAttribute("type")], ["Close", "icon-btn dialog-x", "button"]);
  assert.equal(dialog.children[dialog.children.length - 1], cross, "the last thing of the dialog: it opens on its own first control, and Tab reaches the cross last");
  dialog.close();
});

test("a dialog with a head has its own cross and gets no second one", () => {
  const dialog = made('<div class="dialog-head"><h2>Large</h2><button class="icon-btn" data-close aria-label="Close"></button></div><form></form>');
  dialog.showModal();
  assert.equal(crossOf(dialog).length, 0);
  dialog.close();
});

test("the cross does what Cancel does", () => {
  const dialog = made('<form><h2>New</h2><button type="button" data-cancel>Cancel</button></form>');
  let cancelled = 0;
  dialog.querySelector("[data-cancel]").addEventListener("click", () => { cancelled += 1; });
  dialog.showModal();
  crossOf(dialog)[0].click();
  assert.equal(cancelled, 1);
  dialog.close();
});

test("with no Cancel, the cross is Escape: the dialog hears cancel and may stay open", () => {
  const dialog = made("<form><h2>Done</h2><button>Close</button></form>");
  dialog.showModal();
  const keep = (event) => event.preventDefault();
  dialog.addEventListener("cancel", keep);
  crossOf(dialog)[0].click();
  assert.equal(dialog.open ?? dialog.hasAttribute("open"), true, "kept by the dialog");
  dialog.removeEventListener("cancel", keep);
  crossOf(dialog)[0].click();
  assert.equal(dialog.hasAttribute("open"), false);
});
