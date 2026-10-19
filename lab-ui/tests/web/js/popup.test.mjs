// The rule of every dropdown (web/js/components/popup.js, `place`): which side of its control a list opens on, how high and how wide it is,
// and that it never leaves the window, from sizes alone. The pages' own lists are measured in a browser (DESIGN.md, Measuring). Then the
// picker that filters a list by one of its facts (web/js/components/combo.js, CategoryFilter), run over a small DOM: the categories of a list as it
// always drew them, and the catalogs an item comes from, with the same component.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

const { Popup } = await import("../../../web/js/components/popup.js");
const { place, MARGIN, GAP, MIN, MAX_WIDTH, SHEET } = Popup;

const WINDOW = { width: 1440, height: 900 };
// A control of 190 by 32 at (x, y), as the filters of a list are
const at = (x, y, width = 190, height = 32) => ({ left: x, top: y, right: x + width, bottom: y + height });
// The box a placing draws in a window, as a browser would: from its top, or from its bottom when it is above
const box = (placed, win, height) => {
  const shown = Math.min(height, placed.maxHeight);
  const top = placed.top ?? win.height - placed.bottom - shown;
  return { top, bottom: top + shown, left: placed.left, right: placed.left + placed.width };
};
// Nothing of it is out of the window: it keeps MARGIN from each edge, but a sheet, which lies on the sides and the bottom
function inside(placed, win, height) {
  const b = box(placed, win, height), edge = placed.sheet ? 0 : MARGIN;
  assert.ok(b.left >= edge && b.right <= win.width - edge, `within the sides: ${JSON.stringify(b)}`);
  assert.ok(b.top >= MARGIN && b.bottom <= win.height - edge, `within the top and the bottom: ${JSON.stringify(b)}`);
}

test("a list that fits under its control opens there, as high as its content: a short menu is not stretched", () => {
  const trigger = at(600, 140), placed = place(trigger, { width: 220, height: 154 }, WINDOW);
  assert.equal(placed.side, "below");
  assert.equal(placed.top, trigger.bottom + GAP);
  assert.equal(placed.maxHeight, 154);
  inside(placed, WINDOW, 154);
});

test("a long list takes nearly all the room of its side: everything down to MARGIN from the window's edge", () => {
  const trigger = at(1011, 147), placed = place(trigger, { width: 320, height: 1200 }, WINDOW);
  assert.equal(placed.side, "below");
  assert.equal(placed.maxHeight, WINDOW.height - MARGIN - (trigger.bottom + GAP));
  assert.equal(box(placed, WINDOW, 1200).bottom, WINDOW.height - MARGIN);
  inside(placed, WINDOW, 1200);
});

test("near the bottom of the window it opens above, whole when it fits there", () => {
  const trigger = at(600, 800), placed = place(trigger, { width: 220, height: 300 }, WINDOW);
  assert.equal(placed.side, "above");
  assert.equal(placed.top, null);
  assert.equal(placed.bottom, WINDOW.height - (trigger.top - GAP));   // its bottom edge GAP above the control: it grows upward
  assert.equal(placed.maxHeight, 300);
  inside(placed, WINDOW, 300);
});

test("a list that fits on neither side goes where there is more room, and scrolls in all of it", () => {
  const low = at(600, 500), tall = { width: 300, height: 2000 };
  const placed = place(low, tall, WINDOW);
  assert.equal(placed.side, "above");
  assert.equal(placed.maxHeight, low.top - GAP - MARGIN);
  inside(placed, WINDOW, 2000);
  const high = place(at(600, 300), tall, WINDOW);
  assert.equal(high.side, "below");
  assert.equal(high.maxHeight, WINDOW.height - MARGIN - (300 + 32 + GAP));
});

test("near the top of the window it opens below, whatever it holds", () => {
  const placed = place(at(600, 4), { width: 300, height: 3000 }, WINDOW);
  assert.equal(placed.side, "below");
  inside(placed, WINDOW, 3000);
});

test("in a window a few rows high, where no side has MIN, it lies over its control within the window", () => {
  const short = { width: 1440, height: 300 }, trigger = at(600, 130);
  assert.ok(short.height - MARGIN - (trigger.bottom + GAP) < MIN && trigger.top - GAP - MARGIN < MIN);
  const placed = place(trigger, { width: 300, height: 700 }, short);
  assert.equal(placed.side, "over");
  assert.equal(placed.maxHeight, short.height - 2 * MARGIN);
  inside(placed, short, 700);
  // What fits under it there still opens under it
  assert.equal(place(trigger, { width: 300, height: 100 }, short).side, "below");
});

test("at 1440 by 600 the list of the services of Logs opens under its picker and ends MARGIN above the bottom", () => {
  const win = { width: 1440, height: 600 }, placed = place(at(1011, 147), { width: 320, height: 790 }, win);
  assert.deepEqual([placed.side, placed.top, placed.maxHeight], ["below", 185, 399]);
  inside(placed, win, 790);
});

test("it is as wide as its control at least, as its longest line when that is wider, up to MAX_WIDTH", () => {
  assert.equal(place(at(100, 100, 326), { width: 200, height: 100 }, WINDOW).width, 326);
  assert.equal(place(at(100, 100, 128), { width: 200, height: 100 }, WINDOW, { minWidth: 280 }).width, 280);
  assert.equal(place(at(100, 100, 128), { width: 420, height: 100 }, WINDOW, { minWidth: 280 }).width, 420);
  assert.equal(place(at(100, 100, 128), { width: 2000, height: 100 }, WINDOW).width, MAX_WIDTH);
  // and never wider than the window less its margins
  const narrow = { width: 560, height: 900 };
  assert.equal(place(at(20, 100, 128), { width: 900, height: 100 }, narrow).width, narrow.width - 2 * MARGIN);
});

test("at the right edge a picker moves left to stay in the window; at the left edge a menu moves right", () => {
  const picker = place(at(1380, 100, 40), { width: 400, height: 100 }, WINDOW);
  assert.equal(picker.left + picker.width, WINDOW.width - MARGIN);
  inside(picker, WINDOW, 100);
  const menu = place(at(1300, 100, 26), { width: 260, height: 100 }, WINDOW, { align: "end" });
  assert.equal(menu.left + menu.width, 1326, "its right edge on its button's");
  const left = place(at(4, 100, 26), { width: 260, height: 100 }, WINDOW, { align: "end" });
  assert.equal(left.left, MARGIN);
  inside(left, WINDOW, 100);
});

test("a search that narrows the list does not move it to the other side: the side it opened on is kept while it fits", () => {
  const trigger = at(600, 500), win = WINDOW;
  const opened = place(trigger, { width: 300, height: 2000 }, win);
  assert.equal(opened.side, "above");
  assert.equal(place(trigger, { width: 300, height: 100 }, win, { side: opened.side }).side, "above");
  // Grown past it again, the side is chosen anew
  assert.equal(place(trigger, { width: 300, height: 120 }, win, { side: "below" }).side, "below");
  assert.equal(place(at(600, 700), { width: 300, height: 600 }, win, { side: "below" }).side, "above");
});

test(`at ${SHEET} px of window and narrower it is a sheet along the bottom, as high as its content up to MARGIN from the top`, () => {
  for (const win of [{ width: 375, height: 700 }, { width: 480, height: 400 }, { width: 320, height: 240 }]) {
    const tall = place(at(97, 323), { width: 320, height: 1500 }, win);
    assert.deepEqual([tall.sheet, tall.left, tall.width, tall.bottom, tall.maxHeight], [true, 0, win.width, 0, win.height - MARGIN]);
    inside(tall, win, 1500);
    const short = place(at(97, 323), { width: 320, height: 154 }, win);
    assert.equal(short.maxHeight, Math.min(154, win.height - MARGIN));
  }
  assert.equal(place(at(97, 323), { width: 320, height: 154 }, { width: 481, height: 700 }).sheet, false);
});

test("every control of a tall and of a tiny window, near each edge, keeps its list inside the window", () => {
  for (const win of [{ width: 1966, height: 1300 }, WINDOW, { width: 1440, height: 600 }, { width: 900, height: 360 }, { width: 600, height: 260 }]) {
    for (const x of [0, 8, win.width / 2, win.width - 200, win.width - 40]) {
      for (const y of [0, 10, win.height / 2, win.height - 80, win.height - 34]) {
        for (const height of [40, 154, 600, 4000]) {
          for (const align of ["start", "end"]) {
            const placed = place(at(x, y, 40), { width: 300, height }, win, { align, minWidth: 200 });
            assert.ok(placed.maxHeight > 0);
            inside(placed, win, height);
          }
        }
      }
    }
  }
});

test("lines wider than the room a list can have are drawn in two: wider than MAX_WIDTH, or than the window less its margins", () => {
  assert.equal(place(at(100, 100, 128), { width: 600, height: 100 }, WINDOW).tight, false);
  assert.equal(place(at(100, 100, 128), { width: MAX_WIDTH + 1, height: 100 }, WINDOW).tight, true);
  assert.equal(place(at(20, 100, 128), { width: 520, height: 100 }, { width: 560, height: 900 }).tight, false);
  assert.equal(place(at(20, 100, 128), { width: 530, height: 100 }, { width: 560, height: 900 }).tight, true);
  // a sheet has the whole width of the window
  assert.equal(place(at(20, 100, 128), { width: 420, height: 100 }, { width: 440, height: 900 }).tight, false);
  assert.equal(place(at(20, 100, 128), { width: 441, height: 100 }, { width: 440, height: 900 }).tight, true);
});

// --- The picker of a list ----------------------------------------------------------------------------------------------------

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids });
page.el("combo-pop").innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { Combo, CategoryFilter } = await import("../../../web/js/components/combo.js");
Combo.init();

const button = (prefix) => {
  const el = page.doc.createElement("button");
  el.innerHTML = `<span class="sr-only">${prefix}</span><span class="value"></span>`;
  page.doc.body.append(el);
  return el;
};
const options = () => page.doc.querySelectorAll("#combo-list .combo-option");
const listed = () => options().map((el) => [text(el.querySelector(".name")), text(el.querySelector(".age")), el.getAttribute("aria-selected")]);
const opened = async (el) => {
  el.click();
  await flush();
  return listed();
};
const pick = async (name) => {
  options().find((el) => text(el.querySelector(".name")) === name).click();
  await flush();
};
const said = (el) => text(el.querySelector(".value"));

const thing = (id, category, more = {}) => ({ id, category, ...more });
const THINGS = [thing("a", "Messaging"), thing("b", "Messaging"), thing("c", "Observability"), thing("d", "Messaging"), thing("e", "Identity"),
  thing("f", "Identity"), thing("g", ""), thing("h", undefined)];

test("the category filter of a list is what it was: all first, then the fullest first and by name, each with its count, several at once", async () => {
  const el = button("Category: "), changed = [];
  // The page draws its rows again when the choice changes, and asks the filter for what it keeps: that is when the button says the choice
  const filter = CategoryFilter(el, () => THINGS, () => { changed.push(filter.chosen.slice()); filter.items(); });
  assert.equal(said(el), "All categories");
  assert.deepEqual(await opened(el), [["All", "8", "true"], ["Messaging", "3", "false"], ["Identity", "2", "false"], ["Observability", "1", "false"]],
    "the items that have no category are in All and in no entry");
  await pick("Identity");
  assert.equal(said(el), "Identity");
  await pick("Messaging");
  assert.equal(said(el), "Identity, Messaging");
  assert.deepEqual(filter.items().map((x) => x.id), ["a", "b", "d", "e", "f"], "what the list holds in the categories chosen, in its own order");
  await pick("Observability");
  assert.equal(said(el), "3 categories");
  await pick("All");
  assert.equal(said(el), "All categories");
  assert.deepEqual(changed.at(-1), []);
  assert.equal(filter.items().length, 8);
  Combo.close();
  // A category the list no longer has is no longer chosen
  filter.chosen = ["Identity", "Gone"];
  assert.equal(said(el), "Identity, Gone");
  assert.deepEqual(filter.items().map((x) => x.id), ["e", "f"]);
  assert.deepEqual(filter.chosen, ["Identity"]);
  assert.equal(said(el), "Identity");
  filter.clear();
  assert.deepEqual(filter.chosen, []);
});

test("a category is said by the function it is given", async () => {
  const el = button("Category: ");
  CategoryFilter(el, () => THINGS, () => {}, (category) => category.toUpperCase());
  assert.deepEqual((await opened(el)).map((row) => row[0]), ["All", "MESSAGING", "IDENTITY", "OBSERVABILITY"]);
  Combo.close();
});

// The catalogs an item comes from: the first, the others as they were added, this machine's last; each said by its name
const NAMES = { gravitee: "Gravitee public", "acme-hub": "Acme Hub", "partner-bank": "Partner Bank", local: "Personal" };
const ORDER = ["gravitee", "acme-hub", "partner-bank", "local"];
const WORDS = { none: "All catalogs", all: "All catalogs", many: (n) => `${n} catalogs` };
const origin = (x) => x.origin?.id || (x.personal ? "local" : "gravitee");
const catalogs = (entries) => ORDER.map((id) => entries.find(([value]) => value === id)).filter(Boolean);
const FROM = [thing("one", "x"), thing("two", "x"), thing("three", "y"), thing("four", "x", { origin: { id: "acme-hub" } }), thing("five", "x", { personal: true }),
  thing("six", "y", { personal: true }), thing("seven", "x", { origin: { id: "partner-bank" } }), thing("eight", "x", { origin: { id: "partner-bank" } }),
  thing("nine", "x", { origin: { id: "partner-bank" } })];

test("the same filter files the items by the catalog they come from, in the order the page gives and with the words it gives", async () => {
  const el = button("Catalog: ");
  const filter = CategoryFilter(el, () => FROM, () => filter.items(), (id) => NAMES[id], { get: origin, label: WORDS, order: catalogs });
  assert.equal(said(el), "All catalogs");
  assert.deepEqual(await opened(el), [["All catalogs", "9", "true"], ["Gravitee public", "3", "false"], ["Acme Hub", "1", "false"], ["Partner Bank", "3", "false"],
    ["Personal", "2", "false"]], "not the fullest first: the first catalog, the others as they were added, this machine's last");
  await pick("Partner Bank");
  assert.equal(said(el), "Partner Bank");
  assert.deepEqual(filter.items().map((x) => x.id), ["seven", "eight", "nine"]);
  await pick("Personal");
  assert.equal(said(el), "Partner Bank, Personal", "one or two choices show their names");
  assert.deepEqual(filter.items().map((x) => x.id), ["five", "six", "seven", "eight", "nine"]);
  await pick("Acme Hub");
  assert.equal(said(el), "3 catalogs", "several show their number");
  await pick("Acme Hub");
  await pick("Partner Bank");
  await pick("Personal");
  assert.equal(said(el), "All catalogs");
  assert.equal(filter.items().length, 9);
  Combo.close();
});

test("a catalog the list does not hold is not offered, and one that left it is no longer chosen", async () => {
  let held = FROM;
  const el = button("Catalog: ");
  const filter = CategoryFilter(el, () => held, () => {}, (id) => NAMES[id], { get: origin, label: WORDS, order: catalogs });
  held = FROM.filter((x) => !x.personal);
  assert.deepEqual((await opened(el)).map((row) => row[0]), ["All catalogs", "Gravitee public", "Acme Hub", "Partner Bank"], "no Personal while the list has none of it");
  Combo.close();
  filter.chosen = ["local", "acme-hub"];
  assert.deepEqual(filter.items().map((x) => x.id), ["four"]);
  assert.deepEqual(filter.chosen, ["acme-hub"]);
  held = FROM.filter((x) => x.origin?.id !== "acme-hub" && !x.personal);
  assert.deepEqual(filter.items().map((x) => x.id), held.map((x) => x.id), "the choice that nothing is left of is no choice");
  assert.equal(said(el), "All catalogs");
});

test("what a catalog is called is text in the picker", async () => {
  const el = button("Catalog: ");
  const hostile = '<img src=x onerror="boom()">';
  const filter = CategoryFilter(el, () => FROM, () => {}, () => hostile, { get: origin, label: WORDS, order: catalogs });
  assert.deepEqual((await opened(el)).map((row) => row[0]), ["All catalogs", hostile, hostile, hostile, hostile]);
  assert.equal(page.doc.querySelectorAll("#combo-list img").length, 0);
  await pick(hostile);
  assert.equal(page.doc.querySelectorAll("img").length, 0);
  Combo.close();
  filter.chosen = ["gravitee"];
  assert.equal(said(el), hostile);
  assert.equal(el.querySelectorAll("img").length, 0);
});
