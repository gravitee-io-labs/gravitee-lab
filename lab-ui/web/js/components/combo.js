// The picker: one value (a tag, a version, a registry) or several (what filters a list), in one popover at the field it
// was opened from, placed and sized by the rule of every dropdown (popup.js). Its search shows when the list is long, or
// when a value can be typed. A native select of a form is chosen with it too (selectField).

import { $, $$, esc, icon, setHTML, loading, router } from "../core.js";
import { emptyHtml } from "./ui.js";
import { Popup } from "./popup.js";

const FEW = 8;     // up to this many values, the list is read at a glance: no search
const WIDE = 280;  // the least width of the picker, whatever its field: its search and its buttons have room

let anchor = null;              // the field it is open under
let placed = null;              // what keeps it at its field while it is open (popup.js)
let source = null;              // what it lists (see open)
let ready = false;              // loaded: typing filters the list
let failure = "";               // why the list could not be read, while a value can still be typed
let options = [], active = 0;   // the items listed, and the one the keyboard is on

// In place of the options: that the list loads, why it cannot be read, that nothing matches
const say = (html) => `<div class="empty">${html}</div>`;
const searching = () => !$(".combo-search").hidden;

// The keyboard is on the search, or on the list when it has none. On a touch screen it is on the list: the search would open
// the keyboard over half of what it lists, and a tap on the search opens it when it is wanted.
const touch = () => matchMedia("(pointer: coarse)").matches;
const focusPicker = () => (searching() && !touch() ? $("#combo-input") : $("#combo-list")).focus({ preventScroll: true });

// src: {load(), groups(q), current, choose(value), typed, multi, quick, segments(), plain, empty, width}
//   load(): when the list has to be read first; it throws when the list cannot be read, and a list that takes a
//   typed value still takes one
//   groups(q) -> [{label, items: [{value, name, meta, badges, dot, icon, disabled}], more}]: what matches q, in lower
//   case. A `disabled` item is listed and cannot be chosen: its `meta` says why. An item `{heading}` is a title inside its group.
//   `more`: the group shows its first items only, a search finds the others
//   typed: the pattern of a value that can be typed instead of chosen
//   multi: several can be chosen. `current` is then the list of the chosen values, choose(values) gets the new one
//   at each click and the picker stays open. An item `all` stands for every value: it clears the choice, and is
//   ticked while nothing is chosen.
//   quick: [{label, values()}], buttons above the list that choose those values at once
//   segments() -> {label, options: [{value, label}], value, set(value)}, or nothing: a segmented control above the
//   list that narrows it
//   plain: the names are words, not tags
//   empty: what it says when it has nothing to list ("Nothing running"), alone: in place of the list, without the
//   buttons above it. Without, an empty list says "No matches", as a search that finds nothing does.
//   width: the least width of this picker when its lines want more room than WIDE (a name and, apart from it, its id)
async function open(button, src) {
  if (anchor === button) return close();
  close();
  anchor = button;
  source = src;
  active = 0;
  anchor.classList.add("open");
  anchor.setAttribute("aria-expanded", "true");
  const pop = $("#combo-pop");
  pop.hidden = false;
  placed = Popup.attach(pop, anchor, { minWidth: Math.max(WIDE, src.width || 0), scroller: $("#combo-list"), away: close });
  // Named for the field it chooses, as is its list, which the search and the list say the option of the keyboard of
  const named = anchor.getAttribute("aria-label") || anchor.textContent.trim() || "Choose a value";
  pop.setAttribute("aria-label", named);
  $("#combo-list").setAttribute("aria-label", named);
  $("#combo-list").setAttribute("aria-multiselectable", String(!!src.multi));
  // Names in the font of the page, not of a tag; boxes to tick when several can be chosen
  pop.classList.toggle("plain", !!src.plain);
  pop.classList.toggle("multi", !!src.multi);
  $(".combo-search").hidden = false;
  $("#combo-input").value = "";
  $("#combo-bar").innerHTML = $("#combo-foot").innerHTML = "";
  $("#combo-list").innerHTML = say(loading("Loading"));
  placed.update();
  focusPicker();
  try {
    await src.load?.();
  } catch (err) {
    if (source !== src) return;
    failure = err.message;
    if (!src.typed) {
      $("#combo-list").innerHTML = say(`<span class="err-text">${esc(failure)}</span>`);
      return;
    }
  }
  if (source !== src) return;
  ready = true;
  // A short list goes without its search, unless a value can be typed or one is typed already
  const count = src.groups("").reduce((n, g) => n + g.items.length, 0);
  if (!src.typed && !$("#combo-input").value && count <= FEW) {
    $(".combo-search").hidden = true;
    focusPicker();
  }
  render();
}

function close() {
  placed?.stop();
  placed = null;
  $("#combo-pop").hidden = true;
  anchor?.classList.remove("open");
  anchor?.setAttribute("aria-expanded", "false");
  anchor = source = null;
  ready = false;
  failure = "";
  options = [];
}

function option(i, it, selected) {
  const badges = (it.badges || []).map((b) => `<span class="pill">${esc(b)}</span>`).join("");
  return `<div class="combo-option ${selected ? "selected" : ""} ${it.disabled ? "disabled" : ""}" id="combo-opt-${i}" role="option" aria-selected="${selected}"${it.disabled ? ' aria-disabled="true"' : ""} data-i="${i}">
    <span class="check">${selected ? icon("check") : ""}</span>${it.dot === undefined ? "" : `<span class="dot-status ${esc(it.dot)}"></span>`}${it.icon ? icon(it.icon) : ""}
    <span class="name">${esc(it.name)}</span><span class="combo-badges">${badges}</span>
    <span class="age">${esc(it.meta || "")}</span></div>`;
}

// Above the list: the buttons that choose several values at once, and the control that narrows the list
function bar() {
  const quick = (source.quick || []).map((b, i) => `<button class="btn" data-quick="${i}">${esc(b.label)}</button>`);
  const segments = source.segments?.();
  const radio = (o) => `<button role="radio" data-segment="${esc(o.value)}" aria-checked="${o.value === segments.value}">${esc(o.label)}</button>`;
  return quick.join("") + (segments
    ? `<div class="segmented" role="radiogroup" aria-label="${esc(segments.label)}">${segments.options.map(radio).join("")}</div>` : "");
}

function render() {
  const typed = $("#combo-input").value.trim(), q = typed.toLowerCase();
  const chosen = source.multi ? new Set(source.current) : null;
  const picked = (it) => (chosen ? (it.all ? !chosen.size : chosen.has(it.value)) : it.value === source.current);
  let html = "";
  options = [];
  for (const g of source.groups(q)) {
    if (!g.items.length) continue;
    if (g.label) html += `<div class="combo-group">${esc(g.label)}</div>`;
    // An item with only a `heading` is a title inside the group (a part of it, not a choice): the options after it belong to it
    for (const it of g.items) html += it.heading ? `<div class="combo-subgroup" role="presentation">${esc(it.heading)}</div>` : option(options.push(it) - 1, it, picked(it));
    if (g.more) html += '<div class="combo-more">Search to see more</div>';
  }
  // Why the list could not be read stays above what can still be chosen. With nothing to list, nothing typed, a picker
  // that has words for it says them alone
  const nothing = !html && !q && !failure && !!source.empty;
  // A list with badges keeps them in one column, whether an option has one or not
  $("#combo-list").classList.toggle("has-badges", options.some((it) => it.badges?.length));
  $("#combo-list").innerHTML = (failure ? say(`<span class="err-text">${esc(failure)}</span>`) : "")
    + (html || (failure ? "" : say(nothing ? esc(source.empty) : emptyHtml())));
  $("#combo-bar").innerHTML = nothing ? "" : bar();
  // The keyboard starts on the value in use; typed in full, a value is the one Enter takes
  if (!chosen) active = Math.max(0, options.findIndex(picked));
  const exact = q ? options.findIndex((o) => o.value.toLowerCase() === q || o.name.toLowerCase() === q) : -1;
  if (exact >= 0) active = exact;
  // A value the list does not have can be typed, when the list takes one
  $("#combo-foot").innerHTML = source.typed?.test(typed) && !options.some((o) => o.value === typed)
    ? `<button data-use="${esc(typed)}">Use <code>${esc(typed)}</code></button>` : "";
  placed?.update();   // its height follows what it lists, and it stays at its field
  highlight("center");
}

// scroll: "center" when the list opens, "nearest" for the keyboard, "none" for the mouse
function highlight(scroll = "nearest") {
  $$("#combo-list .combo-option").forEach((el) => el.classList.toggle("active", Number(el.dataset.i) === active));
  // What has the focus says which option the keyboard is on, for a screen reader
  const on = options.length ? `combo-opt-${active}` : "";
  for (const host of [$("#combo-input"), $("#combo-list")]) {
    if (on) host.setAttribute("aria-activedescendant", on);
    else host.removeAttribute("aria-activedescendant");
  }
  // Scroll the list only, never the page; an option is never left under the title of its group, which stays at the top
  const el = $(`#combo-list .combo-option[data-i="${active}"]`), list = $("#combo-list");
  if (!el || scroll === "none") return;
  const head = $("#combo-list .combo-group")?.offsetHeight || 0;
  const top = el.offsetTop - list.offsetTop, bottom = top + el.offsetHeight;
  if (scroll === "center") list.scrollTop = top - list.clientHeight / 2 + el.offsetHeight / 2;
  else if (top - head < list.scrollTop) list.scrollTop = top - head;
  else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
}

function choose(value) {
  if (options.find((it) => it.value === value)?.disabled) return;
  if (source.multi) return tick(value);
  const chosen = source;
  close();
  chosen.choose(value);
}

// Several can be chosen: a click ticks or unticks a value, the one for all clears the rest; the list stays where it is
function tick(value) {
  const item = options.find((it) => it.value === value);
  const now = new Set(source.current);
  if (item?.all) now.clear();
  else if (!now.delete(value)) now.add(value);
  pick([...now]);
}

function pick(values) {
  const at = active, top = $("#combo-list").scrollTop;
  source.current = values;
  source.choose(values);
  render();
  active = Math.min(at, options.length - 1);
  highlight("none");
  $("#combo-list").scrollTop = top;
  focusPicker();
}

function init() {
  // A new search starts from the first value it finds
  $("#combo-input").addEventListener("input", () => {
    if (!ready) return;
    active = 0;
    render();
  });
  // The keys of the search, or of the list when it has none; a button above the list keeps its own. Closed from
  // the keyboard, it gives the focus back to its field: Escape, or a value chosen while the field is still there
  // (the page may draw it again).
  $("#combo-pop").addEventListener("keydown", (e) => {
    const field = anchor;
    if (e.key === "Escape") {
      // It alone closes: not the dialog it is in, nor the drawer
      e.preventDefault();
      e.stopPropagation();
      close();
      return field.focus();
    }
    // Tab leaves it, as it leaves a menu: it closes, and the keyboard goes on from its field
    if (e.key === "Tab" && e.target.matches("#combo-input, #combo-list")) {
      close();
      return field?.focus();
    }
    if (!ready || !e.target.matches("#combo-input, #combo-list")) return;
    if (e.key === "ArrowDown") { active = Math.min(options.length - 1, active + 1); highlight(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); highlight(); e.preventDefault(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const use = $("#combo-foot button");
      if (options[active]) choose(options[active].value);
      else if (use) choose(use.dataset.use);
      if (!anchor && field.isConnected) field.focus();
    }
  });
  $("#combo-list").addEventListener("click", (e) => {
    const o = e.target.closest(".combo-option");
    if (o) choose(options[Number(o.dataset.i)].value);
  });
  $("#combo-list").addEventListener("mousemove", (e) => {
    const o = e.target.closest(".combo-option[data-i]");
    if (o && Number(o.dataset.i) !== active) { active = Number(o.dataset.i); highlight("none"); }
  });
  $("#combo-bar").addEventListener("click", (e) => {
    const quick = e.target.closest("button[data-quick]"), segment = e.target.closest("button[data-segment]");
    if (quick) pick(source.quick[Number(quick.dataset.quick)].values());
    if (segment) {
      source.segments().set(segment.dataset.segment);
      render();
      focusPicker();
    }
  });
  $("#combo-foot").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-use]");
    if (b) choose(b.dataset.use);
  });
  document.addEventListener("mousedown", (e) => {
    if (anchor && !e.target.closest("#combo-pop") && !anchor.contains(e.target)) close();
  });
  router.onLeave(close);
}

export const Combo = { init, open, close };

// A native select of a form, chosen with the picker, so that every list that opens is one (the Environment of the Gravitee Cloud
// forms, the Category of New add-on). The select stays the field: the form reads its value, checks it when it is `required`,
// and hears the `input` and `change` it sends; it is out of sight, and a .combo-btn in its place shows its option and lists
// the others, in the dialog it is in. Its label names the button and a click on the label goes to it. What the code of the
// form sets (its value, its options, their words) shows on the button.
export function selectField(select) {
  if (!("options" in select)) return;   // a page without a browser's select (a test's small DOM)
  const button = document.createElement("button");
  button.type = "button";
  button.className = "combo-btn";
  button.setAttribute("aria-haspopup", "dialog");
  button.innerHTML = `<span class="value" id="${esc(select.id)}-value"></span>${icon("chevron")}`;
  const value = $(".value", button), label = select.id && $(`label[for="${CSS.escape(select.id)}"]`);
  if (label) {
    label.id ||= `${select.id}-label`;
    button.setAttribute("aria-labelledby", `${label.id} ${value.id}`);
    label.addEventListener("click", (e) => { e.preventDefault(); button.focus(); });
  }
  select.hidden = true;
  select.after(button);
  // The option chosen; one of no value as a placeholder ("Choose a category"), which is no choice while the field is required
  const paint = () => {
    const option = select.options[select.selectedIndex];
    value.textContent = option?.textContent.trim() || "";
    value.classList.toggle("placeholder", !select.value);
  };
  for (const key of ["value", "selectedIndex"]) {
    const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), key);
    Object.defineProperty(select, key, { configurable: true, get: () => own.get.call(select),
      set: (v) => { own.set.call(select, v); paint(); } });
  }
  new MutationObserver(paint).observe(select, { childList: true, subtree: true, characterData: true, attributes: true });
  button.addEventListener("click", () => open(button, {
    plain: true, current: select.value,
    groups: (q) => [{ items: [...select.options]
      .filter((o) => !o.hidden && (o.value || !select.required) && o.textContent.toLowerCase().includes(q))
      .map((o) => ({ value: o.value, name: o.textContent.trim(), disabled: o.disabled })) }],
    choose: (chosen) => {
      select.value = chosen;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      button.focus();
    },
  }));
  paint();
}

// The filter of a list by one of its facts, on its .combo-btn: several values at once, "All" first, then the fullest first, each with its
// number of items. list(): what the list holds; onChange(): the choice changed. The fact is the item's category, said by name(category),
// unless `options` says another: get(item) -> the value an item is filed under (none, no entry), label: {none, all, many, few} the words of
// the button with no choice, of the entry that stands for every value, and of several chosen ("2 categories", from their number), and how
// many values the button names before it counts them (`few`, 2: "Kafka, Jade", then "3 categories"; 1: one name, then "2 catalogs"), and
// order(entries) -> the entries [value, count] in the order they are listed, for a list that is not the fullest first (the catalogs an
// item comes from: the first, the others as they were added, this machine's last).
const CATEGORIES = { none: "All categories", all: "All", many: (n) => `${n} categories`, few: 2 };
export function CategoryFilter(button, list, onChange, name = (category) => category, { get = (x) => x.category, label = CATEGORIES,
  order = (entries) => entries.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])) } = {}) {
  let chosen = [];
  // The button says the choice, from the start: a list may take a while to load
  const paint = () => setHTML($(".value", button), esc(!chosen.length ? label.none
    : chosen.length <= (label.few ?? 2) ? chosen.map(name).join(", ") : label.many(chosen.length)));
  paint();
  const counted = () => {
    const counts = new Map();
    for (const x of list()) {
      const value = get(x);
      if (value) counts.set(value, (counts.get(value) || 0) + 1);
    }
    return order([...counts]);
  };
  button.addEventListener("click", () => open(button, {
    multi: true, plain: true, current: chosen,
    groups: (q) => [{ items: [{ value: "", name: label.all, all: true, meta: String(list().length) },
      ...counted().map(([value, count]) => ({ value, name: name(value), meta: String(count) }))]
      .filter((it) => it.name.toLowerCase().includes(q)) }],
    choose: (values) => { chosen = values; onChange(); },
  }));
  return {
    // The values chosen; set, the ones chosen from then on: a list shown again as it was
    get chosen() { return chosen; },
    set chosen(values) {
      chosen = values;
      paint();
    },
    // What the list holds in the values chosen, all of it while none is. A value the list no longer has is no longer chosen.
    items() {
      const all = list(), known = new Set(all.map(get));
      chosen = chosen.filter((value) => known.has(value));
      paint();
      return chosen.length ? all.filter((x) => chosen.includes(get(x))) : all;
    },
    clear() {
      if (anchor === button) close();
      chosen = [];
    },
  };
}
