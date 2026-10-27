// Shared pieces of the interface: toasts, the dialogs, the menu, the buttons of a row, console links, a service's
// cells, progress bars and what waits, the state and the note of a row, the badge of what runs behind what is saved and its
// tooltip, where a link to the license leads, the button of an action that shows it at work, the keys of a segmented control, rows kept from one refresh to the next, the
// pager and what an empty list says, the keys of a row that opens and of the search, the focus given back after a
// redraw, copy buttons, stars, the link to the catalog an item comes from, what the lists say while their content is on its way
// or missing, and the dot of the Catalogs entry of the menu

import { $, $$, esc, icon, setHTML, api, store, router, drafts, hrefOf, bareTag, catalogRows } from "../core.js";
import { Popup } from "./popup.js";

// The dialogs in the order they were opened in. The top layer stacks a modal dialog over every one opened before it, wherever it is in the
// document (a confirmation of index.html opened over a dialog made later sits above it), and the document does not say which is on top. Every
// modal dialog of the page goes through showModal, so its prototype keeps the order. Where there is no such prototype (a page without
// dialogs), the last open dialog of the document stands in.
const stacked = [];

// The dialog on top of the page: the last one opened that is still open, else the last open one of the document, else none
function topDialog() {
  for (let at = stacked.length - 1; at >= 0; at -= 1) if (stacked[at].open && stacked[at].isConnected) return stacked[at];
  return $$("dialog[open]").pop() || null;
}

// An open dialog sits in the browser's top layer, and makes everything outside it inert: the toasts live in the one on top while it is open,
// to show above it and to be reached (an error stays while the pointer is on it, and has its close button)
function hostToasts() {
  const holder = $("#toasts");
  if (holder) (topDialog() || document.body).append(holder);
}
document.addEventListener("close", hostToasts, true);

// Every dialog has its cross at the top right. The large ones have it in their head (.dialog-head); the others get it here, in the corner,
// when they open, so that no dialog made later is without one. It does what Cancel does (a Cancel that waits, as a form on its way, makes
// it wait too), else what Escape does: the dialog hears `cancel` and may keep itself open, as the editor does for what was not saved.
const CANCEL = "[data-cancel], #choice-cancel, #confirm-cancel, #create-cancel";
function cross(dialog) {
  if ($(".dialog-head, .dialog-x", dialog)) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn dialog-x";
  button.setAttribute("aria-label", "Close");
  button.setAttribute("data-tip", "Close");
  button.innerHTML = icon("close");
  button.addEventListener("click", () => {
    const cancel = $(CANCEL, dialog);
    if (cancel) cancel.click();
    else if (dialog.dispatchEvent(new Event("cancel", { cancelable: true }))) dialog.close();
  });
  dialog.append(button);   // last in the document, at the corner by its style: a dialog still opens on its own first control
}

const proto = globalThis.HTMLDialogElement?.prototype;
if (proto && typeof proto.showModal === "function") {
  const showModal = proto.showModal, close = proto.close;
  proto.showModal = function (...args) {
    const was = this.open;
    if (!was) cross(this);
    showModal.apply(this, args);
    if (was) return;   // one that is open already keeps its place in the top layer (the call throws, or does nothing)
    for (let at = stacked.length - 1; at >= 0; at -= 1) if (stacked[at] === this || !stacked[at].open || !stacked[at].isConnected) stacked.splice(at, 1);
    stacked.push(this);
  };
  // The toasts and the bubble leave a dialog as it closes, not a task later when the browser says so: a toast made just before the close
  // (`toast("Updated")`, then `dialog.close()`) is not left in a dialog that no longer shows
  proto.close = function (...args) {
    close.apply(this, args);
    hostToasts();
    releaseFloat();
  };
}

// A confirmation goes by itself; an error is an alert, stays while the pointer or the keyboard is on it, and has its close button
export function toast(message, error = false) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.setAttribute("role", error ? "alert" : "status");
  el.innerHTML = `<span>${esc(message)}</span>${error ? `<button type="button" class="icon-btn toast-close" aria-label="Close">${icon("close")}</button>` : ""}`;
  hostToasts();
  $("#toasts").append(el);
  const go = () => { if (!el.matches(":hover, :focus-within")) el.remove(); else setTimeout(go, 1000); };
  setTimeout(go, error ? 8000 : 3500);
  $(".toast-close", el)?.addEventListener("click", () => el.remove());
}

// --- Dialogs ----------------------------------------------------------------------

// Cancel is a plain button: the action is the button of its form, the one Enter presses
for (const id of ["confirm", "choice"]) $(`#${id}-cancel`).addEventListener("click", () => $(`#${id}`).close());

// Resolves with true when confirmed. With `word`, the button waits for that word to be typed. The title is the
// label that was clicked, the action its verb.
export function confirmDialog({ title, text, action, danger = false, word }) {
  const dlg = $("#confirm"), input = $("#confirm-input"), ok = $("#confirm-ok");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text || "";
  $("#confirm-text").hidden = !text;
  ok.textContent = action;
  ok.className = `btn ${danger ? "danger" : "primary"}`;
  input.parentElement.hidden = !word;
  input.value = "";
  input.placeholder = word ? `Type ${word} to confirm` : "";
  ok.disabled = !!word;
  input.oninput = () => (ok.disabled = input.value.trim() !== word);
  return new Promise((resolve) => {
    dlg.returnValue = "";   // Escape leaves the answer of the previous use
    dlg.onclose = () => resolve(dlg.returnValue === "ok");
    dlg.showModal();
    // Enter confirms what is harmless, and a word once it is typed; on what is dangerous the focus stays on Cancel
    if (word) input.focus();
    else if (!danger) ok.focus();
  });
}

// What a line of a step names, by the kind of its row
const KINDS = { platform: "Platform", group: "Platform", service: "Container", addon: "Add-on", setup: "Setup",
  playground: "Playground", data: "Data", environment: "Environment", version: "Version", catalog: "Catalog" };

// The rows of an effects dialog as markup, the way the dialog draws them: the rows that have items, the ones that are not notes as numbered
// steps, the notes as lines of pills (a few) or one line that counts them (many). A dialog that is drawn again as its state changes (the notice
// of the move to catalogs, whose phase changes under it) draws its rows through it, so that they look as every other dialog's.
const named = (i) => (typeof i === "string" ? { name: i } : i);
const line = (i) => `<li><span class="step-name">${esc(named(i).name)}</span>${KINDS[named(i).kind]
  ? `<span class="step-kind">${KINDS[named(i).kind]}</span>` : ""}</li>`;
const step = (r) => `<li class="step ${esc(r.tone || "")}"><span class="step-num"></span><div class="step-body">
  <div class="step-head"><strong>${esc(r.label)}</strong>${r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}</div>
  <ul class="step-items">${r.items.map(line).join("")}</ul></div></li>`;
// A few items sit on the row as pills; many ("3 APIs", "12 access rules", ...) fold under a line that counts them, and open on a
// list of one line each: the count in its own column, then what it counts. An item that is a name (`literal`) counts as one
const counted = (i) => (named(i).literal ? null : /^(\d+)\s+(.+)$/.exec(named(i).name));   // a name is never a count
const more = (r) => {
  const total = r.items.reduce((sum, i) => sum + (+counted(i)?.[1] || 1), 0);
  const lines = r.items.map((i) => (counted(i) ? `<li><b>${esc(counted(i)[1])}</b><span>${esc(counted(i)[2])}</span></li>`
    : `<li><b></b><span>${esc(named(i).name)}</span></li>`)).join("");
  return `<details class="choice-row choice-more ${esc(r.tone || "")}"><summary><span class="choice-row-label">${esc(r.label)}</span>
    <span class="choice-summary">${total} item${total === 1 ? "" : "s"}${icon("chevron")}${r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}</span></summary><ul class="choice-detail">${lines}</ul></details>`;
};
const row = (r) => (r.items.length > 4 ? more(r) : `<div class="choice-row ${esc(r.tone || "")}"><span class="choice-row-label">${esc(r.label)}</span>
  <span class="choice-items">${r.items.map((i) => `<span class="pill ${r.tone === "danger" ? "err" : ""}"><span class="pill-text" data-tip="${esc(named(i).name)}" data-tip-cut>${esc(named(i).name)}</span></span>`).join("")}${
    r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}</span></div>`);
export const effectRows = (rows = []) => {
  const shown = rows.filter((r) => r.items.length), steps = shown.filter((r) => !r.note);
  return (steps.length ? `<ol class="steps">${steps.map(step).join("")}</ol>` : "")
    + shown.filter((r) => r.note).map(row).join("");
};

// What an action does, as numbered steps in the order they happen ("Stops", "Starts", "Restarts", "Deletes"), each
// one line per item, with its kind when it has one ({name, kind}, or a name), and what the step means when it is not
// plain (`hint`); then what it leaves as it is (`note` rows: "Keeps", "Stays in Gravitee"). A step or a row without
// items is not shown; a single step has no number. With several options, the steps they share (`steps`), then a
// choice among them, the first one chosen, each saying in rows what it does besides, and in a muted line (`sub`) what it comes
// to. An option that cannot be taken is `disabled` and says why (`reason`): under it, or, the only one, as its button's tooltip. Resolves with the value of the confirmed option (true when
// it has none), null when canceled. `other`, {label, value}: a second button beside the first, the same steps done another way, which
// resolves with its value.
// `unchosen`, a reason ("Choose where to share"), is for a choice that must be made: of several options none is chosen at first, and the button
// waits, saying why, until one that can be taken is (Enter does nothing meanwhile), so that no key can pick for the person. It also waits,
// with the reason of the option, for one that cannot be taken, and with the first one's reason when none can. `action` and `danger` are asked
// with undefined while none is chosen.
export function effectsDialog({ title, steps = [], options, action, danger = () => false, unchosen, other = null }) {
  const dlg = $("#choice"), ok = $("#choice-ok"), list = $("#choice-list"), second = $("#choice-alt");
  if (second) Object.assign(second, { hidden: !other, textContent: other?.label || "" });
  const single = options.length === 1, waits = unchosen !== undefined, blank = waits && !single;
  $("#choice-title").textContent = title;
  list.classList.toggle("single", single);
  list.innerHTML = effectRows(steps) + options.map((o, i) => `
    <label class="choice ${o.disabled ? "disabled" : ""}">
      <input type="radio" name="choice" value="${i}" ${i || blank ? "" : "checked"} ${o.disabled ? "disabled" : ""}>
      <span class="choice-text">${single ? "" : `<strong>${esc(o.label)}</strong>`}
        ${(o.disabled && !single) || o.sub ? `<span class="sub">${esc(o.disabled && !single ? o.reason : o.sub)}</span>` : ""}
        ${single ? effectRows(o.rows) : (o.rows || []).filter((r) => r.items.length).map(row).join("")}</span>
    </label>`).join("");
  const picked = () => list.querySelector("input:checked"), chosen = () => (picked() ? options[picked().value].value : undefined);
  // The reason the button waits, or null when it may confirm
  const holds = () => {
    if (!waits) return null;
    const found = picked() && options[picked().value];
    if (found) return found.disabled ? found.reason ?? "" : null;
    return options.every((o) => o.disabled) ? options[0].reason ?? "" : unchosen;
  };
  const sync = () => {
    ok.textContent = action(chosen());
    ok.className = `btn ${danger(chosen()) ? "danger" : "primary"}`;
    if (waits || ok._off) setOff(ok, holds() !== null, holds() ?? "");   // the button is shared: one that waited leaves it free
  };
  list.onchange = sync;
  // Enter on a choice confirms it, unless it is a dangerous one or there is nothing to confirm
  list.onkeydown = (e) => { if (e.key === "Enter" && (holds() !== null || danger(chosen()))) e.preventDefault(); };
  sync();
  return new Promise((resolve) => {
    dlg.returnValue = "";
    dlg.onclose = () => {
      if (waits) setOff(ok, false);
      resolve(dlg.returnValue === "alt" && other ? other.value : dlg.returnValue === "ok" && (!waits || picked()) ? chosen() ?? true : null);
    };
    dlg.showModal();
    if (single && !danger(chosen())) ok.focus();   // with a choice, the focus is on it
  });
}

// Says what an action does before it is done, as one list of effects {label, items}: what applying a draft
// changes and restarts, where sharing moves an item. `title` is the label that was clicked, `action` its verb when
// the label is more. Resolves with true when confirmed.
export async function confirmEffects(title, rows, action = title, danger = false) {
  return !!(await effectsDialog({ title, options: [{ rows }], action: () => action, danger: () => danger }));
}

// The rows of a plan as a dialog says them, in the order its job does them: what stops (it runs, and is turned off
// too), what is turned off (it is on and does not run), what starts, the versions it changes, those that go back to an older one in the
// danger tone, what restarts (recreated, the changes taken), and,
// for a deploy, where it deploys; what making them again does besides, in the danger tone: what loses the license it runs; then what it keeps, and what the hybrid gateway serves from now on
// and stops serving when a start swaps it; with API Management in the Cloud, what is not started and why (`hints`, the server's words).
// When the item comes from another catalog than the first, where it comes from: the catalog's name, with its version as the hint (`from`,
// the first of the rows that say what the plan leaves as it is). A row without items is not shown.
const PLAN = [["stops", "Stops", "Turned off too"], ["turns_off", "Turns off"], ["starts", "Starts"],
  ["versions", "Changes versions", "To the ones it was tested with"],
  ["goes_back", "Goes back", "To an older version: data a newer one wrote may not open", false, "danger"],
  ["restarts", "Restarts", "Recreated to take the changes"],
  ["loses_license", "Loses its license", "No license is saved: Enterprise features stop", false, "danger"],
  ["deploys_to", "Deploys to"], ["from", "From", "", true], ["keeps", "Keeps", "", true],
  ["serves", "Serves", "", true], ["stops_serving", "Stops serving", "", true],
  ["not_started", "Not started", "", true]];
export const planRows = (dialog) => PLAN.map(([part, label, hint, note, tone]) => ({ label, hint: dialog.hints?.[part] || hint, note, tone,
  items: (dialog[part] || []).map(({ name, kind }) => ({ name, kind })) }));
// Whether a plan does something that cannot be undone by applying again: its button is then in the danger tone
const risky = (dialog) => PLAN.some(([part, , , , tone]) => tone === "danger" && dialog[part]?.length);

// What the page sends back with an action (`accept`): every row it showed that stops, turns off or changes a version, as "<kind>:<id>".
// The server makes the plan again and refuses to stop, turn off or change anything else.
export const accepted = (dialog) => [...(dialog.stops || []), ...(dialog.turns_off || []), ...(dialog.versions || []),
  ...(dialog.goes_back || []).filter((row) => row.kind === "version")].map((row) => `${row.kind}:${row.id}`);

// Says what else an action starts, stops, turns off, keeps or restarts, when its plan does (`dialog`). Resolves with
// what it may stop or turn off (accepted), null when canceled.
export async function confirmPlan(title, word, dialog) {
  if (!dialog) return [];
  return (await confirmEffects(title, planRows(dialog), word, risky(dialog))) ? accepted(dialog) : null;
}

// A page is not left with a draft without a word: its changes are dropped first, or the page stays
router.guard((hash, again) => {
  const left = drafts.dirty();
  if (!left.length) return true;
  confirmDialog({ title: "Discard changes", action: "Discard", danger: true }).then((confirmed) => {
    if (!confirmed) return;
    left.forEach((draft) => draft.discard());
    again();
  });
  return false;
});

// An address that a page opens as a link: a web address, else none (the server keeps only those; this is the page's own guard)
export const safeHref = (url) => (/^https?:\/\//i.test(url || "") ? url : "");

// --- Menu -------------------------------------------------------------------------

// One menu for the whole page, outside the rows: the refresh draws the rows without touching it
let anchor = null, entries = [];   // the button it belongs to, its items
let placed = null;                                // what keeps it at its button while it is open (popup.js)

// items: {label, hint, href, disabled, reason, danger, dot, onSelect}, or {separator: true}.
// A link opens in a new tab; `dot` is a state class of .dot-status ("" for gray).
export function openMenu(button, items) {
  const again = anchor === button;
  closeMenu();
  if (again) return;
  const menu = $("#menu");
  menu.innerHTML = items.map((it, i) => {
    if (it.separator) return '<div class="menu-sep" role="separator"></div>';
    const hint = it.disabled && it.reason || it.hint;   // a disabled entry says why
    const body = `${it.dot === undefined ? "" : `<span class="dot-status ${esc(it.dot)}"></span>`}<span>${esc(it.label)}</span>`
      + (hint ? `<span class="menu-hint">${esc(hint)}</span>` : "");
    const attrs = `class="menu-item ${it.danger ? "danger" : ""}" role="menuitem" data-i="${i}"`;
    if (!safeHref(it.href) || it.disabled) return `<button ${attrs} ${it.disabled ? "disabled" : ""}>${body}</button>`;
    return `<a ${attrs} href="${esc(safeHref(it.href))}" target="_blank" rel="noopener">${body}</a>`;
  }).join("");
  menu.hidden = false;
  menu.setAttribute("aria-label", button.getAttribute("aria-label") || button.textContent.trim() || "Menu");
  anchor = button;
  entries = items;
  button.setAttribute("aria-expanded", "true");
  // By the rule of every dropdown (popup.js): its right edge on the button's, under it or over it, as high as the room there allows
  placed = Popup.attach(menu, button, { align: "end", minWidth: 200, away: closeMenu });
  if (button.matches(":focus-visible")) $(".menu-item:not(:disabled)", menu)?.focus();   // opened from the keyboard
}

function closeMenu() {
  if (!anchor) return;
  placed?.stop();
  placed = null;
  $("#menu").hidden = true;
  anchor.removeAttribute("aria-expanded");
  anchor = null;
}

document.addEventListener("click", (e) => {
  if (!anchor) return;
  const el = e.target.closest("#menu .menu-item");
  if (el && !el.disabled) {
    const item = entries[el.dataset.i];
    closeMenu();
    item.onSelect?.();
  } else if (!anchor.contains(e.target) && !e.target.closest("#menu")) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (!anchor) return;
  if (e.key === "Escape") {
    const button = anchor;
    closeMenu();
    button.focus();
  } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
    // The arrows move through its entries, as in the picker; Home and End go to the first and the last
    const items = $$("#menu .menu-item:not(:disabled)"), at = items.indexOf(document.activeElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : at + (e.key === "ArrowDown" ? 1 : -1);
    e.preventDefault();
    items[Math.max(0, Math.min(items.length - 1, next))]?.focus();
  } else if (e.key === "Tab" && document.activeElement?.closest("#menu")) {
    // Tab leaves it: the focus goes back to its button, then on as Tab takes it
    const button = anchor;
    closeMenu();
    button.focus();
  }
});
// A scroll or a resize moves it with its button (popup.js), which closes it once the button leaves the window
router.onLeave(closeMenu);
// Once the pages have drawn a new state: a row drawn again has other actions, so a menu whose button is gone closes
store.subscribe(() => setTimeout(() => {
  if (anchor && !anchor.isConnected) closeMenu();
}, 0));

// --- Row buttons ------------------------------------------------------------------

// A button that is not there keeps its place where other rows have it: the buttons of every row line up
export const GAP = '<span class="icon-gap"></span>';

// The files of an add-on, a setup, a playground or a group of the Stack: its compose file, then its settings when
// it has some, named "<kind>:<id>". `aligned`: among rows that may have settings. The file of an item of a catalog that is only read
// (`readonly`) reads View, with its own name and no path; any other reads Edit, with the path the person reads (`display_file`: relative
// to its catalog, else the file itself), never one of this machine once the server says it. The editor is opened on `data-file`, the real path.
export function fileButtons(unit, aligned = true) {
  const said = esc(unit.readonly ? `View ${String(unit.file).split("/").pop()}` : `Edit ${unit.display_file || unit.file}`);
  return `
  <button class="icon-btn" data-file="${esc(unit.file)}" data-tip="${said}" aria-label="${said}">${icon("file")}</button>
  ${unit.settings.length ? `<button class="icon-btn" data-settings="${esc(`${unit.kind}:${unit.id}`)}" data-tip="Settings" aria-label="${esc(`Settings ${unit.name || ""}`.trim())}">${icon("key")}</button>`
    : aligned ? GAP : ""}`;
}

// What a row opens: nothing, a console, or the choice among several (`menu` names whose: "<kind>:<id>"). A console
// that is down cannot be opened, and says so. An icon in its slot, with `text` the Open button of a list row, or with `named`
// the button of a page's head, which says what it opens ("Open Acme Bakery"); both leave no gap when there is nothing to open.
export function openButton(links, menu, { text = false, named = false } = {}) {
  const [link] = links;
  if (!link) return text || named ? "" : GAP;
  const cls = named || text ? "btn" : "icon-btn";
  const face = named ? `${icon("external")}Open ${esc(link.label)}` : text ? "Open" : icon("external");
  if (links.length > 1) return `<button class="${cls}" data-open="${esc(menu)}" data-tip="Open" aria-label="Open" aria-haspopup="true">${text ? face + icon("chevron") : face}</button>`;
  return link.up
    ? `<a class="${cls}" href="${esc(safeHref(link.url))}" target="_blank" rel="noopener" data-tip="Open ${esc(link.label)}" aria-label="Open ${esc(link.label)}">${face}</a>`
    : `<button class="${cls}" aria-disabled="true" data-tip="${esc(unopened(link))}" aria-label="Open ${esc(link.label)}">${face}</button>`;
}

// --- States and services ----------------------------------------------------------

// Without Docker, or without docker-compose.yml, what runs is not known (`blind`): no dot is colored, no state is said
export const dockerDown = () => !!store.state.blind;

// What is on is known: the files say it, also while Docker does not answer; not while docker-compose.yml does not load
export const onKnown = () => !store.state.blind || !!store.state.docker.error;

// While only Docker does not answer, what is on keeps its ring: it starts with the platform
export const stateDot = (state) => {
  const key = !dockerDown() ? state.key : onKnown() && state.key === "stopped" ? "stopped" : "";
  return `<span class="dot-status ${esc(key)}"></span>`;
};

// Why a console cannot be opened: it does not run, or what runs is not known ("Docker is not reachable")
export const unopened = (link) => (dockerDown() ? store.state.wait : `${link.label} is not running`);

// Where a console answers on this machine, after its name: its port, and the path of its address when it has one, so that two
// consoles of one port read apart (":8085/next", ":8085")
function whereOf(link) {
  let path = "";
  try { path = new URL(link.url).pathname.replace(/\/+$/, ""); } catch { /* no address: its port alone */ }
  return `:${link.port}${path}`;
}

// A console to open, with the state of the service behind it; one that is down cannot be opened, and says so. The console
// of a platform has no service here: a dot with no state. Where it answers takes the room its name leaves, and says itself
// whole on hover when that cuts it.
function linkHtml(link, service) {
  const target = link.up ? `href="${esc(safeHref(link.url))}" target="_blank" rel="noopener"`
    : `aria-disabled="true" data-tip="${esc(unopened(link))}"`;
  const where = link.port ? `<span class="link-port" data-tip="${esc(whereOf(link))}" data-tip-cut>${esc(whereOf(link))}</span>` : "";
  return `<a class="link ${link.up ? "" : "off"}" ${target}>
    ${stateDot(service?.state || { key: "" })}<strong>${esc(link.label)}</strong>${where}${icon("external")}</a>`;
}

// The consoles of the platforms a playground deployed to, as paintLinks takes them: a group named after each platform, in the order
// the lab lists the platforms, a link per environment that holds what it created, named after the environment, to the platform's
// own console, in the order the places list the environments (a record whose place is gone last)
export function platformLinks(playground) {
  const records = (playground.deployed || []).filter((d) => d.created.length);
  return store.platforms.map((platform) => {
    const order = store.places.filter((p) => p.platform?.id === platform.id).map((p) => p.id);
    const rank = ({ place }) => (order.includes(place) ? order.indexOf(place) : order.length);
    const mine = records.filter((d) => d.place.startsWith(`${platform.id}/`)).sort((a, b) => rank(a) - rank(b));
    return { name: platform.name, links: mine.map((d) => ({ label: d.environment, url: platform.url, up: true })) };
  }).filter((group) => group.links.length);
}

// What to open, as the Open card of the Stack and the details of a running playground show it: the consoles of the
// components under their section (the platform's groups in their order, the playground's own, then the categories of
// the add-ons), each link opening in a browser tab. The columns are as wide as the widest link, its name and port in full,
// as far as the card allows (`--link-width`, components.css): a link's width does not depend on the page's, so it is
// measured once the links change and are in view, the moment they come into it too (a tab shown later). It is measured
// `fitting`, where its port and path count up to a few characters: a long path is cut, it never widens the columns.
const drawn = new WeakMap();    // the links of each element: {html, measured}
function fitLinks(el) {
  const now = drawn.get(el);
  if (now.measured || !el.offsetParent) return;
  const all = $$(".link", el);
  el.classList.add("fitting");
  all.forEach((a) => (a.style.width = "max-content"));
  const widest = Math.max(0, ...all.map((a) => a.offsetWidth));
  all.forEach((a) => (a.style.width = ""));
  el.classList.remove("fitting");
  el.style.setProperty("--link-width", `${widest}px`);
  now.measured = true;
}

// The consoles under their sections, [[name, links]], in the order every page shows them: the platform's groups in their
// order, then the platforms a playground deployed to (`platforms`: the groups of platformLinks), the playground's own,
// then the categories of the add-ons. A section with no console is left out.
function linkSections(state, links, platforms = []) {
  const mine = new Set(state.playgrounds.flatMap((p) => p.services));
  const owned = [...new Set(links.filter((l) => mine.has(l.service)).map((l) => l.section))];
  const all = [...links, ...platforms.flatMap((g) => g.links.map((l) => ({ ...l, section: g.name })))];
  const sections = [...new Set([...state.platform.groups.map((g) => g.name), ...platforms.map((g) => g.name), ...owned,
    ...state.categories, ...all.map((l) => l.section)])];
  return sections.map((name) => [name, all.filter((l) => l.section === name)]).filter(([, group]) => group.length);
}

// `platforms`: the groups of platformLinks, after the platform's own
export function paintLinks(el, state, links, platforms = []) {
  const html = linkSections(state, links, platforms)
    .map(([name, group]) => `<div class="link-group"><span class="link-section">${esc(name)}</span>
      ${group.map((l) => linkHtml(l, state.services[l.service])).join("")}</div>`)
    .join("") || `<div class="empty">${emptyHtml({ running: true })}</div>`;
  setHTML(el, html);
  if (!drawn.has(el)) new ResizeObserver(() => fitLinks(el)).observe(el);
  if (drawn.get(el)?.html !== html) drawn.set(el, { html, measured: false });
  fitLinks(el);
}

// A link to an item of the catalog, in its name: its row opened in its list, or a playground's page
export const itemLink = (unit, text = unit.name) => `<a class="name-link" href="${esc(hrefOf(unit))}">${esc(text)}</a>`;

// The words of a plugin's artifact name that say what it is, without those a page says already: "gravitee", the
// products, "plugin", and the types of plugins it knows (`types`, the words of a catalog's categories). Under
// Policies, "gravitee-policy-rate-limit" says rate and limit; without its types, policy first.
export function pluginWords(name, types = []) {
  const known = new Set([...(store.state?.products || []).map((p) => p.id), "plugin", ...types]);
  const words = name.replace(/^gravitee-/, "").split("-");
  while (words.length > 1 && known.has(words[0])) words.shift();
  return words;
}

// Where what is said of the license leads: the License dialog, open on the form that takes a new one, where "Edit
// .env" still leads to its field. The attribute of a button that opens it so, which its page opens with
// License.show({form: true}); `data-license` alone opens the dialog on what the license gives.
export const licenseForm = () => 'data-license="form"';

// --- Tooltips and popovers ----------------------------------------------------------------
//
// One component explains what a word, a mark or a badge means: a button that keeps the look of what it sits in, and what it opens
// on hover (after a short delay), on keyboard focus and on a tap, placed in the window so that no card clips it, with an arrow toward
// the button. The button carries the whole explanation as its name for a screen reader, so the bubble itself is not read twice.
// - A TOOLTIP is a label or one short line (LINE characters at most): a dark bubble, 12 px, on one line that never wraps.
// - A POPOVER explains anything longer: several lines, one long line, or a card {title, text, rows, foot}. A card of the surface, 260 to 460
//   px at most: a title, a sentence (`text`), then rows under a hairline (a row is {subject, lines}, a line a list of parts: a word,
//   {who} for whom it is about, {code} for a version or a setting's name, ARROW, {pair: parts} for parts that stay on one line,
//   {versions: {running, saved, older}} for a version that runs and the one saved), then a foot (what the action will do). Of several
//   lines the first is the title (a term, a state, a part of the disk), unless `title` is false: a list of equals; a title and one
//   sentence are a title over its sentence.
//   The rows of a card are one table of two columns, the whole card's: the components a line is about, one to a line, then why and in
//   what (a leading word muted over its chips, which wrap, or the version that runs and the one saved, each after its caption: `columns`,
//   "Running" and "Saved" by default). A row's subject (a product) is a heading over both columns, between hairlines.
// `inner` is the button's HTML, `cls` its look (a pill, nothing for a word), `label` what it is called when it is not the
// explanation's first words. `tab: false`: a bubble for the pointer only, whose words a stop of the keyboard says elsewhere.
// Escape closes it while the pointer or the focus stays, until both have left (WCAG 1.4.13); a tap elsewhere, a scroll or a resize
// closes it.
const LINE = 40;   // the longest line a tooltip says: what is longer is a popover
const ARROW = { arrow: true };
const COLUMNS = ["Running", "Saved"];
const chipHtml = (code, older = false) => `<code class="chip${older ? " older" : ""}">${esc(code)}</code>${older ? '<span class="sr-only"> (older)</span>' : ""}`;
const partHtml = (part) => (part?.arrow ? `<span class="pop-arrow" aria-hidden="true">${icon("arrow-right")}</span>`
  : typeof part === "string" ? `<span>${esc(part)}</span>`
  : part.pair ? `<span class="pop-pair">${part.pair.map(partHtml).join(" ")}</span>`
  : chipHtml(part.code, part.older));
const partSaid = (parts, columns = COLUMNS) => parts.flatMap((part) => part?.pair || [part]).map((part) => (part?.arrow ? ","
  : typeof part === "string" ? part : part.lead || (part.versions ? `${columns[0]} ${part.versions.running}, ${columns[1].toLowerCase()} ${part.versions.saved}${part.versions.older ? " (older)" : ""}`
  : part.who || `${part.code}${part.older ? " (older)" : ""}`))).join(" ").replace(/ ,/g, ",");
// A line: whom it is about (or the subject of its row, `subject`), then what it says: two versions in their columns, or its words and
// chips across them. Words that lead it (`{lead}`: "Settings changed", "Uses") stand muted in a column of their own, and the parts after
// them wrap under their first one (`.pop-chips`), never under the leading words (under them, after whom the line is about, where the
// room is narrow: components.css); spaced, so that its words copy apart.
const lineHtml = (parts, subject = "", columns = COLUMNS) => {
  const who = parts.find((part) => part?.who), rest = parts.filter((part) => part !== who), versions = rest.find((part) => part?.versions);
  // The components a line is about, one to a line (a list of names joined by commas, as the server says them)
  const names = (text) => text.split(", ").map((name) => `<span>${esc(name)}</span>`).join("");
  const first = subject ? `<span class="pop-subject pop-who">${names(subject)}</span>` : who ? `<span class="pop-who">${names(who.who)}</span>` : "";
  const what = versions ? `<span class="pop-pairs"><span class="pop-run" data-cap="${esc(columns[0])}">${chipHtml(versions.versions.running)}</span> <span class="pop-saved" data-cap="${esc(columns[1])}">${chipHtml(versions.versions.saved, versions.versions.older)}</span></span>`
    : rest[0]?.lead ? `<span class="pop-what"><span class="pop-lead">${esc(rest[0].lead)}</span> <span class="pop-chips">${rest.slice(1).map(partHtml).join(" ")}</span></span>`
    : `<span class="pop-what">${rest.map(partHtml).join(" ")}</span>`;
  return `<span class="pop-line">${first}${first ? " " : ""}${what}</span>`;
};
// A row: its subject on the line of its cause when it has one line about all of it, else over its lines, which say whom each is about
const rowHtml = (row, columns = COLUMNS) => {
  const alone = row.subject && row.lines.length === 1 && !row.lines[0].some((part) => part?.who);
  if (alone) return `<span class="pop-row">${lineHtml(row.lines[0], row.subject, columns)}</span>`;
  return `<span class="pop-row">${row.subject ? `<span class="pop-subject pop-head">${esc(row.subject)}</span>` : ""}${row.lines.map((line) => lineHtml(line, "", columns)).join("")}</span>`;
};
// The captions of the two columns of versions, over the rows of a card that has any
const hasVersions = (card) => (card.rows || []).some((row) => row.lines.some((line) => line.some((part) => part?.versions)));
const captions = (card) => `<span class="pop-captions" aria-hidden="true"><span class="pop-run">${esc((card.columns || COLUMNS)[0])}</span> <span class="pop-saved">${esc((card.columns || COLUMNS)[1])}</span></span>`;

// A card from lines of text: the first its title when `title` says so, then its sentence when there is one (no hairline: a term and what
// it means), else each other one a row; one long line is its sentence
const cardOf = (lines, title) => (lines.length === 1 ? { text: lines[0], rows: [] }
  : title && lines.length === 2 ? { title: lines[0], text: lines[1], rows: [] }
  : { title: title ? lines[0] : "", rows: (title ? lines.slice(1) : lines).map((one) => ({ lines: [[one]] })) });

// What a card says to a screen reader: its sentences, in order
const cardSaid = (card) => [card.title, card.text, ...(card.rows || []).map((row) => [row.subject, row.lines.map((line) => partSaid(line, card.columns)).join(". ")].filter(Boolean).join(": ")), card.foot]
  .filter(Boolean).map((one) => one.replace(/\.$/, "")).join(". ");

// The inside of a popover, which the bubble of a control that acts on a click shows too. Its body scrolls when the card is taller than the
// window allows: the card itself keeps its arrow and the bridge to its button, outside its box. A card may end with one button that leads
// somewhere (`action`: {label, href}, or {label, attrs} for a button that the page's own handler answers): a link the pointer reaches, and the keyboard after its badge, which keeps the card open meanwhile.
export const cardHtml = (card) => `<span class="pop-body"><span class="pop-title">${esc(card.title || "")}</span>${card.text ? `<span class="pop-text">${esc(card.text)}</span>` : ""}${
  card.rows?.length ? `<span class="pop-rows">${hasVersions(card) ? captions(card) : ""}${card.rows.map((row) => rowHtml(row, card.columns || COLUMNS)).join("")}</span>` : ""}${card.foot ? `<span class="pop-foot">${esc(card.foot)}</span>` : ""}${
  card.action ? (card.action.attrs ? `<button type="button" class="btn" ${card.action.attrs}>${esc(card.action.label)}</button>`
    : `<a class="btn" href="${esc(card.action.href)}">${esc(card.action.label)}</a>`) : ""}</span>`;

// Whether lines are said by a popover: more than one, or one too long for a tooltip
const popover = (lines) => lines.length > 1 || String(lines[0] ?? "").length > LINE;

// `short`: the button is named by its label alone and the explanation is its description (a column's head, which every cell of
// the column would otherwise repeat).
export function tipHtml(inner, line, cls = "", label = "", { title = true, tab = true, said = "", short = false } = {}) {
  const lines = typeof line === "object" && !Array.isArray(line) ? null : [].concat(line);
  const card = lines ? (popover(lines) ? cardOf(lines, title) : null) : line;
  const words = said || (lines ? lines.map((one, i) => (i < lines.length - 1 ? one.replace(/\.$/, "") : one)).join(". ") : cardSaid(card));
  const bubble = card ? `<span class="tip-card" role="tooltip"${card.action ? "" : ' aria-hidden="true"'}>${cardHtml(card)}</span>`
    : `<span class="tip-text" role="tooltip" aria-hidden="true"><span>${esc(lines[0])}</span></span>`;
  const names = short ? `aria-label="${esc(label)}" aria-description="${esc(words)}"` : `aria-label="${esc(label ? `${label}: ${words}` : words)}"`;
  return `<span class="tip"><button type="button" class="tip-btn ${cls}"${tab ? "" : ' tabindex="-1"'} ${names}>${inner}</button>${bubble}</span>`;
}

// The attributes of a control that acts on a click and is explained by the one bubble of the page (`data-tip`): a line, or a card
// (`data-tip-card`, the card as JSON) that the bubble draws as a popover. `data-tip` is what a screen reader hears of it.
export const tipAttrs = (said, card = null) => (said || card ? ` data-tip="${esc(said || cardSaid(card))}"${card ? ` data-tip-card="${esc(JSON.stringify(card))}"` : ""}` : "");

// What one cause of a note says, as the parts of a line (the server's `causes`). What differs from what is saved (reasons.causes): `who`,
// the services it is about when they are not all of its owner's, then what changed, versions and settings as chips, never a value; a
// version saved that is older than the one that runs is marked, as Apply goes back to it. What keeps an item from starting: a version it
// needs (`needs`), settings without a value (`unset`), what of the license it uses (`license`). Every line starts the same way, with a
// capital, after whom it is about.
const SETTINGS_SHOWN = 4;
const chips = (names) => {
  const shown = names.slice(0, SETTINGS_SHOWN).map((name) => ({ code: name })), more = names.length - shown.length;
  return [...shown, ...(more > 0 ? [`and ${more} more`] : [])];
};
function causeLine(cause) {
  const who = cause.services?.length ? [{ who: cause.services.join(", ") }] : [];
  if (cause.kind === "tag") {
    return cause.saved ? [...who, { versions: { running: cause.running, saved: cause.saved, older: !!cause.older } }] : [...who, "Another image is saved"];
  }
  if (cause.kind === "image") return cause.built ? [...who, "Image built again"] : [...who, "Image", { code: cause.tag }, "changed"];
  if (cause.kind === "settings") return [...who, { lead: "Settings changed" }, ...chips(cause.settings)];
  if (cause.kind === "switch") return [...who, { versions: { running: cause.have, saved: cause.to, older: !!cause.older } }];
  if (cause.kind === "needs") return [{ who: cause.name }, { code: cause.version }, ...(/\d/.test(cause.version) ? ["or later"] : [])];
  if (cause.kind === "unset") return chips(cause.settings);
  if (cause.kind === "license") return [{ lead: "Uses" }, cause.features.join(", ")];
  if (cause.kind === "catalog") return [catalogNeed(cause).line];
  return [...who, "Configuration changed"];
}

// What blocks an item is a line for each reason it was refused, three at most, then how many more
const BLOCKED_SHOWN = 3;
function causeLines(cause) {
  if (cause.kind !== "blocked") return [causeLine(cause)];
  const reasons = Array.isArray(cause.reasons) ? cause.reasons.map(String) : [], shown = reasons.slice(0, BLOCKED_SHOWN).map((reason) => [reason]);
  return reasons.length > BLOCKED_SHOWN ? [...shown, [`and ${reasons.length - BLOCKED_SHOWN} more`]] : shown;
}

// What an item that includes another catalog's units says of the catalog it lacks, by the `reason` the server gives: the line of its
// note and of its Before you start, and the button that mends it when there is one: {act, label}, `act` being "add" (the
// Add dialog on its second screen, or the catalog itself when it is listed), "retry" (download it again) or "update" (the catalog's row).
// A version of three numbers is said without its patch when that is 0 ("2.0.0" is 2.0). The unit that is gone (`unit`) is named when the server says it.
const minimum = (version) => String(version ?? "").replace(/^(\d+\.\d+)\.0$/, "$1");
export function catalogNeed(cause) {
  const name = cause.name ?? "";
  switch (cause.reason) {
    case "downloading": return { line: `Downloading ${name}`, button: null };
    case "failed": return { line: `Needs ${name}`, button: { act: "retry", label: "Try again" } };
    case "gone": return { line: `Needs ${cause.unit || "an item"}, which is no longer in ${name}`, button: null };
    case "old": return { line: `Needs ${[name, minimum(cause.min)].filter(Boolean).join(" ")} or later`, button: { act: "update", label: `Update ${name}` } };
    default: return { line: `Needs the catalog ${name}`, button: { act: "add", label: `Add ${name}` } };
  }
}
const count = (n) => `${n} ${n === 1 ? "container differs" : "containers differ"} from what is saved`;
// Whether causes are about several services: Apply restarts them
const several = (causes) => new Set(causes.flatMap((cause) => cause.services || [])).size > 1;
// What Apply does to them: it restarts them as saved, and goes back to a version saved that is older than the one that runs
const applies = (them, causes) => `Apply restarts ${them} with what is saved${causes.some((cause) => cause.older) ? ", older versions included" : ""}.`;

// The card of what differs from what is saved: for a row, its causes; for the platform, a row for each product, add-on or playground
// (the state's `platform.behind`), under how many containers it is in all, and what Apply does
const rowCard = (causes) => ({ title: "Differs from what is saved", rows: [{ lines: causes.map(causeLine) }],
  foot: applies(several(causes) ? "them" : "it", causes) });

// The card of what keeps an item from starting, from the causes of its note: the versions it needs, the settings it lacks, what of
// the license it uses; its foot is where it is fixed, which the badge leads to
// A foot may depend on its cause: a function of it. Who changes what blocks an item is the owner of its catalog, or the person for a folder
// catalog; a missing catalog is mended by the note itself, which leads to it: no foot. A kind this page does not know says none.
const FOOT = { needs: ["Choose it on the Images page.", "Choose them on the Images page."], unset: ["Set it in its settings.", "Set them in its settings."],
  license: ["Set a license in the license form."], catalog: [""], switch: [""],
  blocked: [(cause) => (cause.folder ? "Change it in your catalog." : cause.owner ? `Ask the owner of ${cause.owner} to change it.` : "Ask the owner to change it.")] };
// The feet of a kind, by its own name: a kind the server writes is a word, never a property every object has ("constructor")
const feetOf = (kind) => (Object.hasOwn(FOOT, kind) ? FOOT[kind] : null);
const footOf = ([first, ...others]) => {
  const feet = feetOf(first?.kind);
  if (!feet) return "";
  const one = first.kind === "unset" ? first.settings.length === 1 : !others.length, [it, them = it] = feet;
  const foot = one ? it : them;
  return typeof foot === "function" ? foot(first) : foot;
};
// A switch of versions is each image as it is and as it was tested; its foot says what Start does: goes to them when the ones saved are too
// old, offers them when they only differ
const SWITCH = { columns: ["Current", "Tested"], goes: "Start goes to the versions it was tested with, or choose others on the Images page.",
  offers: "Start offers the versions it was tested with, or choose others on the Images page." };
const needCard = (x) => (x.note_causes[0]?.kind === "switch"
  ? { title: x.note, columns: SWITCH.columns, rows: [{ lines: x.note_causes.map(causeLine) }], foot: x.action?.offer ? SWITCH.offers : SWITCH.goes }
  : { title: x.note, rows: [{ lines: x.note_causes.flatMap(causeLines) }], foot: footOf(x.note_causes) });
// ... for whoever says that note elsewhere (the gateway of an environment that the add-on runs): null for a note without causes of that
// kind (what runs behind what is saved has its own card)
export const noteCard = (x) => (x.note_causes?.length && feetOf(x.note_causes[0]?.kind) ? needCard(x) : null);
function behindCard(behind) {
  const total = behind.reduce((sum, row) => sum + (row.count || 1), 0);
  return { title: count(total), rows: behind.map((row) => ({ subject: row.name, lines: (row.causes || []).map(causeLine) })),
    foot: applies(total === 1 ? "it" : "them", behind.flatMap((row) => row.causes || [])) };
}

// What runs behind what is saved, as a badge: an amber pill, "Apply needed" and an information mark, its explanation the popover of
// its causes when the server gives them (`causes`, or a card), else its reason as a tooltip. `cls` is its look: a row's pill, or the
// head's state chip. Its name for a screen reader is the reason, in the words of a sentence. `more`: lines the card says after its
// causes, of what the badge stands in front of (a newer version of a product, which its Status would say otherwise).
export function applyBadge(reason, cls = "pill warn", causes = null, more = []) {
  const found = Array.isArray(causes) && causes.length ? rowCard(causes) : causes && !Array.isArray(causes) ? causes : null;
  const card = found && more.length ? { ...found, rows: [...found.rows, { lines: more }] } : found;
  const reasons = [...[].concat(reason).filter(Boolean), ...(card ? more.map((line) => partSaid(line)) : [])];
  const said = reasons.map((one, i, all) => (i < all.length - 1 ? one.replace(/\.$/, "") : one)).join(". ");
  return tipHtml(`Apply needed${icon("info")}`, card || (reasons.length ? reasons : "Differs from what is saved"), cls, "Apply needed", { said, title: false });
}

// What the platform says runs behind what is saved: a line for each product, add-on or playground, as the state gives them
export const behindTip = (platform) => platform.behind.map((row) => row.reason);
// ... and as the popover of the head and the notice, when the server gives the causes
export const behindWhy = (platform) => (platform.behind.length && platform.behind.every((row) => row.causes) ? behindCard(platform.behind) : null);

// Where a bubble goes: under its button, its left edge at the button's, kept 12 px inside the window; above it when there is more room
// there. A popover taller than the room it has scrolls inside (components.css). Its arrow points at the button's middle.
function place(bubble, at, cap) {
  const room = document.documentElement.clientWidth;
  bubble.style.maxWidth = `${Math.min(cap, room - 24)}px`;
  bubble.style.maxHeight = "";
  const wide = bubble.offsetWidth, left = Math.max(12, Math.min(at.left - 4, room - wide - 12));
  bubble.style.left = `${left}px`;
  const tall = bubble.offsetHeight, under = window.innerHeight - at.bottom - 16, over = at.top - 16;
  const below = tall <= under || under >= over, space = Math.max(48, below ? under : over);
  if (tall > space) bubble.style.maxHeight = `${space}px`;
  bubble.style.top = `${below ? at.bottom + 8 : at.top - 8 - bubble.offsetHeight}px`;
  bubble.style.setProperty("--arrow", `${Math.max(12, Math.min(wide - 12, at.left + at.width / 2 - left))}px`);
  bubble.classList.toggle("above", !below);   // the gap between the button and the bubble is bridged on the side it is
}
const placeTip = (tip) => {
  const bubble = $(".tip-text", tip) || $(".tip-card", tip);
  place(bubble, $(".tip-btn", tip).getBoundingClientRect(), bubble.classList.contains("tip-card") ? 460 : 280);
};

// The tooltip of a control that acts on a click (a note that leads to its fix, a mark): its line is its `data-tip`, shown by one bubble
// of the page on hover and on keyboard focus, never on a click, which is the control's. It is the control's description for a screen
// reader too (aria-describedby).
let floating = null;
let floatingFor = null;   // the control the bubble shows for
// An open dialog sits in the browser's top layer, above everything that is not in it: the bubble is hosted in the dialog of the control that
// asks for it, so that the reason a control of a dialog cannot be used shows there, else in the body. Not in the last dialog of the document: the
// order of the top layer is the order the dialogs were opened in, which the document does not know (a confirmation of index.html opened over
// a dialog made later would hide it). A control the person can reach is always in the top one; the dialog on top (topDialog) is for one that is in none.
function hostFloat(el) {
  const host = el.closest("dialog[open]") || topDialog() || document.body;
  if (floating.parentElement !== host) host.append(floating);
}
function showFloat(el) {
  // A text cut says itself whole; one that is not has nothing to add, and what it is in says its own (a console that is down, why)
  if (el.hasAttribute("data-tip-cut") && el.scrollWidth <= el.clientWidth) {
    const outer = el.parentElement?.closest("[data-tip]");
    if (outer) showFloat(outer);
    return;
  }
  // The name of an item of the menu, while only its icon shows. An entry that says more than its name (`data-tip`) says that otherwise, and
  // its name, in `data-tip-collapsed`, while only its icon shows.
  const collapsed = document.body.classList.contains("nav-collapsed");
  if (el.hasAttribute("data-tip-collapsed") && !collapsed && !el.dataset.tipCollapsed) return;
  const said = (collapsed && el.dataset.tipCollapsed) || el.dataset.tip;
  if (!floating) {
    floating = document.createElement("span");
    floating.id = "tip-float";
    floating.setAttribute("role", "tooltip");
  }
  hostFloat(el);
  // One short line is a tooltip; a card, or a longer line, a popover
  let card = null;
  try { card = el.dataset.tipCard ? JSON.parse(el.dataset.tipCard) : null; } catch { /* a line, then */ }
  if (!card && said.length > LINE) card = { text: said, rows: [] };
  floating.className = `${card ? "tip-card" : "tip-text"} tip-float shown`;
  floating.innerHTML = card ? cardHtml(card) : `<span>${esc(said)}</span>`;
  el.setAttribute("aria-describedby", "tip-float");
  floatingFor = el;
  place(floating, el.getBoundingClientRect(), card ? 460 : 280);
}
const hideFloat = () => floating?.classList.remove("shown");
// A dialog that closes while the bubble is in it takes it away: the bubble goes back to the page, hidden. Asked as `close()` is called and again
// when the browser says the dialog closed (a task later), which then finds nothing to do.
function releaseFloat() {
  if (!floating?.closest("dialog:not([open])")) return;
  hideFloat();
  document.body.append(floating);
}
document.addEventListener("close", releaseFloat, true);
// A control that waits and has not been set yet says why as soon as it is reached
const tipped = (e) => {
  const el = e.target.closest?.("[data-tip], [data-why]");
  if (el?.hasAttribute("data-why")) adopt(el);
  return el?.dataset.tip ? el : null;
};
// Over what has no tooltip, none shows: the control that had one may have gone from under the pointer (Stop all, once it stopped)
document.addEventListener("mouseover", (e) => {
  const el = tipped(e);
  if (el) showFloat(el);
  else if (!floating?.contains(e.target)) hideFloat();
});
document.addEventListener("focusin", (e) => { const el = tipped(e); if (el) showFloat(el); });
document.addEventListener("mouseout", (e) => { const el = e.target.closest?.("[data-tip]"); if (el && !el.contains(e.relatedTarget)) hideFloat(); });
document.addEventListener("focusout", (e) => { if (e.target.closest?.("[data-tip]")) hideFloat(); });
const closeTips = (except) => { for (const tip of $$(".tip.open")) if (tip !== except) tip.classList.remove("open"); };
for (const type of ["mouseover", "focusin"]) {
  document.addEventListener(type, (e) => { const tip = e.target.closest?.(".tip"); if (tip) placeTip(tip); });
}
// A bubble that Escape closed while the pointer or the focus is on it stays closed until both have left
document.addEventListener("mouseout", (e) => {
  const tip = e.target.closest?.(".tip");
  if (tip && !tip.contains(e.relatedTarget) && !tip.matches(":focus-within")) tip.classList.remove("dismissed");
});
document.addEventListener("focusout", (e) => {
  const tip = e.target.closest?.(".tip");
  if (tip && !tip.contains(e.relatedTarget) && !tip.matches(":hover")) tip.classList.remove("dismissed");
});
// In the capture phase: a tip inside a row that opens or a link must not open it
document.addEventListener("click", (e) => {
  const tip = e.target.closest?.(".tip");
  closeTips(tip);
  if (!tip || e.target.closest(".tip-text") || e.target.closest(".tip-card")) return;   // a click in the bubble selects its words, it does not close it
  placeTip(tip);
  tip.classList.toggle("open");
  e.preventDefault();
  e.stopPropagation();
}, true);
// Whether a bubble shows: one opened by a tap, one the pointer or the focus holds open, the shared one
const bubbleShown = () => !!floating?.classList.contains("shown")
  || $$(".tip").some((tip) => !tip.classList.contains("dismissed") && (tip.classList.contains("open") || tip.matches(":hover, :focus-within")));
// Escape closes the bubble that shows, and that alone: in a dialog, the next Escape closes the dialog (what was typed stays)
let swallowed = false;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    swallowed = bubbleShown();
    if (swallowed) {
      e.preventDefault();
      e.stopPropagation();
    }
    closeTips();
    hideFloat();
    for (const tip of $$(".tip")) if (tip.matches(":hover") || tip.matches(":focus-within")) tip.classList.add("dismissed");
  } else if (e.target.closest?.(".tip") && (e.key === "Enter" || e.key === " ")) e.stopPropagation();   // the click it makes is the tip's
}, true);
document.addEventListener("cancel", (e) => {
  if (!swallowed) return;
  swallowed = false;
  e.preventDefault();
}, true);
// A scroll moves the button away from its bubble: it closes, but for a scroll inside a popover, which is reading it. So does a window
// that changes size (a phone turned): the bubble would be left where the button was.
window.addEventListener("scroll", (e) => {
  if (e.target.closest?.(".tip-card")) return;
  closeTips();
  hideFloat();
}, true);
window.addEventListener("resize", () => {
  closeTips();
  hideFloat();
});
// A page that is left closes what it had open, as a menu does
router.onLeave(() => {
  closeTips();
  hideFloat();
});

// What the server says of a row (an item, a group, a container), in its tone, its tooltip the reason when it has one
// ("Failed": the job's last line). One that names an item links to it ("via Kafka Desk"); one that settings answer
// opens them: the license's notes are the ones with a file, they open the form that takes a new license. What runs behind
// what is saved (`note_apply`) is the badge, its tooltip the reason. `cls` adds the classes of where it sits.
export function noteHtml(x, cls = "") {
  if (!x.note) return "";
  if (x.note_apply) return applyBadge(x.note_title || x.note, "pill warn", x.note_causes);
  // Its tooltip: the reason when it says more than its words, else its words when they are cut
  const look = `list-note ${x.note_tone || ""} ${cls}`, title = x.note_title && x.note_title !== x.note
    ? `data-tip="${esc(x.note_title)}"` : `data-tip="${esc(x.note)}" data-tip-cut`;
  if (x.note_target) return `<a class="${esc(look)} text-link" href="${esc(hrefOf(x.note_target))}" ${title}>${esc(x.note)}</a>`;
  if (x.note_file) return `<button class="${esc(look)} text-link" ${licenseForm()} ${title}>${esc(x.note)}</button>`;
  return `<span class="${esc(look)}" ${title}>${esc(x.note)}</span>`;
}

// NOTE BADGE: what the server says of a catalog row, as a badge after its name, never in its description. A pill in the note's tone
// (neutral, warn, err). One that leads to its fix (the item it names, the license form, the form a place lacks: `fix`) is a quiet
// pill-button with an arrow, its explanation a tooltip; one with more to say opens it with an information mark; Apply needed is its
// badge, with the popover of its causes.
export function noteBadge(x, fix = "") {
  if (!x.note) return "";
  if (x.note_apply) return applyBadge(x.note_title || x.note, "pill warn", x.note_causes);
  const look = `pill note ${x.note_tone || ""}`.trim(), more = x.note_title && x.note_title !== x.note ? x.note_title : "";
  // What it says beyond its label: a card of its causes (the versions it needs, the settings it lacks), a sentence under its label
  // when it is long, else one line
  const card = x.note_causes?.length ? needCard(x) : more.length > LINE ? { title: x.note, text: more } : null;
  const words = `<span class="pill-text">${esc(x.note)}</span>`, tip = tipAttrs(more, card), go = `${words}${icon("arrow-right")}`;
  if (x.note_target) return `<a class="${look}" href="${esc(hrefOf(x.note_target))}"${tip}>${go}</a>`;
  if (x.note_file) return `<button type="button" class="${look}" ${licenseForm()}${tip}>${go}</button>`;
  // Settings without a value: its own settings take them
  if (x.note_causes?.[0]?.kind === "unset") return `<button type="button" class="${look}" data-settings="${esc(`${x.kind}:${x.id}`)}"${tip}>${go}</button>`;
  if (fix) return `<button type="button" class="${look}" data-fix="${esc(fix)}"${tip}>${go}</button>`;
  // A card without a sentence of the server's says its lines, not its title again: the note is the label already
  return more || card ? tipHtml(`${words}${icon("info")}`, card || more, look, x.note, { said: more || cardSaid({ ...card, title: "" }) }) : `<span class="${look}">${words}</span>`;
}

// BLOCKER: what keeps an item from starting, as a small warning button beside its Start (the server's note of the row, or what its place
// lacks): the popover says what is missing, with the button that fixes it when there is one. The State keeps only what the item is. It
// takes the same fields as noteBadge: `note`, `note_title`, `note_tone`, `note_causes`, and where it leads (`note_target`, `note_file`, the
// settings it lacks) or `fix`, the form of a place.
export function blockerButton(x, fix = "") {
  if (!x.note) return "";
  const more = x.note_title && x.note_title !== x.note ? x.note_title : "", causes = x.note_causes?.length ? needCard(x) : null;
  let action = null;
  if (x.note_target) action = { label: "Open", href: hrefOf(x.note_target) };
  else if (x.note_file) action = { label: "Add a license", attrs: licenseForm() };
  else if (x.note_causes?.[0]?.kind === "unset") action = { label: "Open settings", attrs: `data-settings="${esc(`${x.kind}:${x.id}`)}"` };
  else if (fix) action = { label: "Fix it", attrs: `data-fix="${esc(fix)}"` };
  // The causes say what the explanation would repeat (the settings it lacks, the versions it needs)
  const card = { ...causes, title: x.note, text: causes ? "" : more, rows: causes?.rows || [], action };
  return tipHtml(icon("alert"), card, `blocker ${x.note_tone === "err" ? "err" : ""}`.trim(), x.action?.enabled ? x.note : "Cannot start");
}

// The catalog an item comes from, as a link to its row with the version after it, for the About of an item (never next to its name).
// The items of this machine have no origin (they are Personal), nor have those of the first catalog while it is the only one:
// there is nothing to tell apart, and nothing is drawn.
export function originLink(item) {
  const origin = item?.origin;
  if (!origin?.name) return "";
  return `<a class="name-link" href="${esc(hrefOf({ kind: "catalog", id: origin.id }))}">${esc(origin.name)}</a>${
    origin.version ? ` <span class="muted">${esc(origin.version)}</span>` : ""}`;
}

// A service's version, as text: the tag its container runs, else the one saved, without the system it is built on (as every page says a
// version); what the version column of the Stack is as wide as
export const serviceVersion = (service) => (service.built ? "local build" : bareTag(service.running_tag || service.tag || ""));

// How another container of the lab reaches a service: its name and the port inside it, not the one published ("In the
// lab: smtp4dev:80"), which the endpoint of an API made in a platform's console needs. Said only while a platform is
// registered, for a service that publishes a port (`targets`: the port inside it, by the one published).
function inLab(service) {
  if (!store.platforms.length) return "";
  const name = Object.keys(store.state.services).find((key) => store.state.services[key] === service);
  const inside = service.ports.map((port) => service.targets?.[port]).filter(Boolean);
  return name && inside.length ? ` data-tip="In the lab: ${esc(inside.map((port) => `${name}:${port}`).join(", "))}"` : "";
}

// A service's version and the ports it publishes, as the Stack and a detail show them. The version is the one its
// container runs, without its system, its tooltip its image; when another one is saved its tooltip says which (the badge
// "Apply needed" of its group says why, once: the version is not colored for it).
export function serviceCells(service) {
  const saved = service.running_tag && service.running_tag !== service.tag ? `${service.tag} is saved` : "";
  return `<span class="s-version" data-tip="${esc(saved || service.image)}">${service.built
    ? '<span class="muted">local build</span>' : esc(serviceVersion(service))}</span>
  <span class="s-ports"${inLab(service)}>${service.ports.map((port) => `<span>${esc(port)}</span>`).join("")}</span>`;
}

// NOTICE: a fact about the lab as a whole, in a slim callout above the page (the banner): its icon in its tone (warn, err, info, calm),
// its headline, a short summary (HTML: a popover may explain it), its action at the right. `title` and `text` are HTML the caller
// escaped.
const NOTICE_ICON = { warn: "alert", err: "alert", info: "info", calm: "info" };
export const noticeHtml = ({ tone = "warn", title, text = "", action = "" }) => `<div class="notice ${tone}">${icon(NOTICE_ICON[tone]).replace("<svg", '<svg class="notice-icon"')}<p class="notice-body"><strong class="notice-title">${title}</strong>${
  text ? `<span class="notice-text">${text}</span>` : ""}</p>${action ? `<span class="notice-actions">${action}</span>` : ""}</div>`;

// STATE: what something is doing, in one component wherever a line says it (a product, an environment's gateway, an account, an
// item of the catalog): its dot (`dot`, a key of .dot-status: running, starting, stopping, stopped, off, issue, warn, or "" for what is
// not known), then its word. The word is muted while what it says is off or stopped (`quiet`), amber or red while it warns or fails.
// A tooltip or a popover (`tip`, lines or a card) adds what is known of it, with no mark of its own: the word is underlined while it is
// hovered or focused. `extra` is HTML after the word (+1, a progress bar).
const STATUS_TONE = { issue: "err", warn: "warn" };
export function statusHtml(dot, word, { tip = [], quiet = false, extra = "" } = {}) {
  const words = `<span class="status-word">${esc(word)}</span>`, told = Array.isArray(tip) ? tip.length > 0 : !!tip;
  const look = ["status", quiet && "muted", STATUS_TONE[dot]].filter(Boolean).join(" ");
  return `<span class="${look}"><span class="dot-status ${esc(dot)}" aria-hidden="true"></span>${told ? tipHtml(words, tip, "", word) : words}${extra}</span>`;
}

// The console of a service in its slot: the first one it has
export const serviceOpen = (name) => openButton(store.state.links.filter((l) => l.service === name).slice(0, 1));

// --- Progress ---------------------------------------------------------------------

// A bar for what store.progress(key) measures, next to its spinner or its state. Its markup never changes: the
// element survives a refresh, and its width glides from one value to the next.
export function progressBar(key) {
  return `<span class="progress" data-progress="${esc(key)}" role="progressbar" aria-valuemin="0" aria-valuemax="100" hidden><span></span></span>`;
}

// Sets every bar under `root`: hidden while nothing is measured. It runs once the state is drawn (main.js); whoever
// draws bars at another time (a filter, a toggle) calls it on what it drew.
export function paintProgress(root = document) {
  for (const bar of $$("[data-progress]", root)) {
    const value = store.progress(bar.dataset.progress), percent = (value || 0) * 100;
    bar.hidden = value === null;
    bar.setAttribute("aria-valuenow", Math.round(percent));
    bar.firstElementChild.style.width = `${percent}%`;
  }
}

// --- Content ----------------------------------------------------------------------

// The content of the catalogs, as the server says it in one field (`state.content`: key none, downloading, failed or ready), drawn by the
// one component of the place where the content will be: the Dashboard's card, the three lists (kind "playground", "setup", "addon"), the
// Catalogs page (kind "catalog") and the foot of the Welcome dialog (contentLead). This is the only place that knows its words. The block
// is the `.empty` one, a status for a screen reader; with items of this machine to show (`items`) it is one line above them (`data-line`),
// never in their place. A server without the field draws nothing. The buttons it draws say what the page does with a click:
// `data-content-act` ("retry": download again, "add": add the listed catalog) and the namespace it is about (`data-catalog`).
const WAITING = "Downloading playgrounds, setups and add-ons";
const NO_CATALOG = "No catalog added";
const NOTHING = { playground: "No playground yet", setup: "No setup yet", addon: "No add-on yet", "": "No playground, setup or add-on yet" };

const contentButton = (act, catalog, label) => `<button type="button" class="btn" data-content-act="${act}" data-catalog="${esc(catalog)}">${esc(label)}</button>`;

// The catalog a Try again is for: the first with no copy installed that is not being downloaded now, else the first there is
function retryOf(state) {
  const git = catalogRows(state).filter((catalog) => catalog.kind === "git");
  return (git.find((catalog) => !catalog.commit && typeof catalog.progress !== "number") || git[0])?.id ?? "";
}

// What none offers: to add the first listed catalog that is not added, else (outside the Catalogs page, which is there already) to browse
function offerOf(content, onPage) {
  const offer = content.offer;
  if (offer?.id) return contentButton("add", offer.id, `Add ${offer.name ?? offer.id}`);
  return onPage ? "" : '<a class="btn" href="#/catalogs">Browse catalogs</a>';
}

export function contentStatus(state, { kind = "", items = 0, line = items > 0 } = {}) {
  const content = state?.content, onPage = kind === "catalog";
  if (!content) return "";
  const block = (key, inner) => `<div class="empty" role="status" data-content="${key}"${line ? " data-line" : ""}>${inner}</div>`;
  if (content.key === "downloading" && !onPage) {
    // Where a title says what is fetched, the block says only that it is: the address is behind its mark
    const word = content.address ? tipHtml(`<span>Downloading</span>${icon("info")}`, `From ${content.address}`, "", "Downloading", { title: false }) : "Downloading";
    return block("downloading", `<span class="loading"><span class="spinner"></span>${word}</span>${progressBar("content")}`);
  }
  if (content.key === "failed" && !onPage) {
    return block("failed", `${content.line ? `<span>${esc(content.line)}</span>` : ""}${contentButton("retry", retryOf(state), "Try again")}`);
  }
  if (content.key === "none") return block("none", `<span>${NO_CATALOG}</span>${offerOf(content, onPage)}`);
  if (content.key === "ready" && !items && !onPage) return block("ready", Object.hasOwn(NOTHING, kind) ? NOTHING[kind] : NOTHING[""]);
  return "";
}

// The lead slot of the Welcome foot while the content is not there, and the secondary button beside it: {ready, why, extra}. The slot
// stays where it is, disabled, saying why (`data-why`: the same words as the block's, the long sentence only here); `extra` is the
// button's markup (Try again, Add the listed catalog), empty when there is none. `ready`: the slot is the dialog's own.
export function contentLead(state) {
  const content = state?.content;
  if (content?.key === "downloading") return { ready: false, why: WAITING, extra: "" };
  if (content?.key === "failed") return { ready: false, why: content.line ?? "", extra: contentButton("retry", retryOf(state), "Try again") };
  if (content?.key === "none") return { ready: false, why: NO_CATALOG, extra: offerOf(content, false) };
  return { ready: true, why: "", extra: "" };
}

// The dot of the Catalogs entry of the menu: on while a catalog row has a button to press (`attention`: an update, a repair, a Lab to update,
// a download that failed or is long), saying which, the first that applies, to a screen reader and in its tooltip. Otherwise the entry is
// as the page wrote it. Called once the state is drawn (main.js).
const ATTENTION = [["update", "update available"], ["repair", "needs repair"], ["lab", "update the Lab"], ["download", "not downloaded"]];
export function paintMenuDot(state = store.state) {
  const entry = $('.nav-item[data-nav="catalogs"]'), dot = entry && $(".nav-dot", entry);
  if (!dot) return;
  entry._plain ??= { name: entry.getAttribute("aria-label"), tip: entry.dataset.tip };
  const rows = catalogRows(state);
  const found = ATTENTION.find(([key]) => rows.some((row) => row.attention === key));
  const name = found ? `${entry._plain.name}, ${found[1]}` : entry._plain.name;
  dot.hidden = !found;
  entry.setAttribute("aria-label", name);
  entry.dataset.tip = found ? name : entry._plain.tip;
  entry.dataset.tipCollapsed = name;
}

// A control that cannot be used now says why, and keeps the pointer and the keyboard so that it can be asked: it is aria-disabled
// rather than disabled, its reason the shared bubble's (data-tip, on hover, focus and tap, and its description for a screen reader), and
// a click on it does nothing (below). It is drawn `aria-disabled="true" data-why="<reason>"`, its own tooltip (`data-tip`, the name of an
// icon) beside it, which comes back once it can be used; the first time it is set (setOff, paintBusy, paintActions) or hovered or
// focused it is taken over (adopt). Nothing of the Lab UI has the browser's `title`; one drawn so is taken over the same way.
function adopt(b) {
  if (b._adopted) return;
  b._adopted = true;
  const why = b.dataset.why ?? b.title;
  b._off = b.disabled || b.getAttribute("aria-disabled") === "true";
  b._tip = b.dataset.tip || (b._off ? "" : b.title);
  b._why = b._off ? why : "";
  b.removeAttribute("title");
  b.removeAttribute("data-why");
  b.disabled = false;
  showOff(b);
}
function showOff(b) {
  if (b._off) b.setAttribute("aria-disabled", "true");
  else b.removeAttribute("aria-disabled");
  const tip = (b._off && b._why) || b._tip;
  if (tip) b.dataset.tip = tip;
  else delete b.dataset.tip;
  // The bubble of the control it shows for says what it says now ("Checking your machine", then what to fix)
  if (b === floatingFor && floating?.classList.contains("shown")) {
    if (tip) showFloat(b);
    else hideFloat();
  }
}
// `off`: whether it cannot be used, `why` what it says then; `tip`, when given, its own tooltip from now on ("" for none). A button at
// work says what runs, and gets this back as its face comes back (paint).
export function setOff(b, off, why = "", tip = undefined) {
  adopt(b);
  b._off = !!off;
  b._why = off ? why : "";
  if (tip !== undefined) b._tip = tip;
  if (!b.matches(".busy, .said")) showOff(b);
}
// The click of a control that cannot be used, or of a button at work, is no click, wherever it is drawn: asked first, before the
// page's own handlers (and before a form is sent by its button)
document.addEventListener("click", (e) => {
  if (!e.target.closest?.('[aria-disabled="true"], .pbtn.busy, .pbtn.said')) return;
  e.preventDefault();
  e.stopImmediatePropagation();
}, true);

// A control that the state makes unusable now, or usable again, besides what it waits for (paintBusy): its reason, and its tooltip
// when it is given. One drawn once and kept (a button of a head) takes what the state says each time it changes.
export function holdOff(b, off, why = "", tip = undefined) {
  adopt(b);
  b._idle = { off: !!off, why: off ? why : "" };
  setOff(b, off, why, tip);
}

// What waits for Docker and for the running job, usable or not, saying why. A button that the state makes unusable itself (an
// item's Start that needs settings) stays so, with the server's reason, which names the wait too; while the Lab UI does not answer,
// that is what it says.
function waitOn(b, wait) {
  adopt(b);
  b._idle ??= { off: b._off, why: b._why };
  const own = b._idle.off && !store.offline && !store.error;
  setOff(b, !!wait || b._idle.off, (own ? "" : wait) || b._idle.why);
}

// Makes unusable, under `root`, what waits for Docker, for the running job and for the Lab UI (data-busy), saying
// why. It runs once the state is drawn (main.js); whoever draws such a button at another time calls it
// on what it drew. The button of the action at work is left to paintActions: it is busy, not unusable.
export function paintBusy(root = document) {
  const wait = store.wait();
  for (const b of $$("[data-busy]", root)) if (!b.matches(".busy, .said")) waitOn(b, wait);
}

// --- Actions at work --------------------------------------------------------------

// What a verb says while its action works: one word for each
const ING = { start: "Starting", stop: "Stopping", restart: "Restarting", update: "Updating", pull: "Pulling",
  apply: "Applying", install: "Installing", remove: "Removing", reset: "Resetting", down: "Removing",
  reseed: "Seeding", deploy: "Deploying", login: "Logging in", create: "Creating", save: "Saving", share: "Sharing",
  delete: "Deleting", replace: "Replacing", refresh: "Refreshing", cancel: "Canceling", add: "Adding", connect: "Connecting",
  setup: "Setting up", repair: "Repairing", export: "Exporting", import: "Importing", move: "Moving", copy: "Copying", check: "Checking" };
const ing = (verb) => ING[verb] || "Working";
// What a verb says once its action is over, with its check or its error mark: the word of the button that started it
const WORD = { start: "Start", stop: "Stop", restart: "Restart", update: "Update", pull: "Pull", apply: "Apply",
  install: "Install", remove: "Remove", reset: "Reset", down: "Remove", reseed: "Re-seed", deploy: "Deploy", login: "Log in",
  create: "Create", save: "Save", share: "Share", delete: "Delete", replace: "Replace", refresh: "Refresh", cancel: "Cancel",
  add: "Add", connect: "Connect", setup: "Set up", repair: "Repair", export: "Export", import: "Import", move: "Move", copy: "Copy", check: "Check" };
// What Turn on and Turn off change, as the tooltip of their button wherever it is (a row, a playground's head): what is on, which
// starts with the platform, and nothing that runs now
export const TURNS = { "Turn on": "Start it with the platform from now on", "Turn off": "No longer start it with the platform" };
// The verbs whose job says how far it is: the others ("Saving…", "Canceling…") never show a percentage
const MEASURED = new Set(["start", "stop", "restart", "update", "pull", "apply", "install", "remove", "reset", "down", "reseed",
  "deploy"]);

const FULL = 500;     // how long a measured action that got there says 100%, before its button gives its face back
const FLASH = 1600;   // how long the working face says how its action went, before the button gives its face back
const UNSEEN = 4000;  // how long it waits for a button to say it: one that no button showed by then is dropped
const MARK = { done: "check", failed: "alert" };   // its icon meanwhile, before the word
// "<key>|<verbs>" → what its buttons show: {look, from, at}, the look, the one before it and when it changed. A button
// drawn again goes on from there.
const shown = new Map();
// job id → {key, verb, title, seen}: the jobs a button showed or started, until it is known how they went: once they
// end, or for one that ended well, once what it started or stopped got there. `seen`: a state said it runs, and none
// said yet that it ended.
const followed = new Map();
const flashes = new Map();    // key → {verb, word, ok, at, until, full}: how the last action on it went
let arrived = null;   // the last job that ended and was seen there since: what it acts on got where it took it (there)

// A button whose action shows on it as it works. `key` is what it acts on ("addon:<id>", "platform", "job"…);
// `verb` (one, or several) restricts it to those actions: a Restart shows a restart only, a Start/Stop toggle shows any
// action on its key. `cls` adds the button's classes ("act", "primary", "icon-btn" for an icon alone), `attrs`
// its attributes (data-*, disabled and its title, aria-label). Its markup never depends on whether it is busy: a
// refresh that draws the same leaves the element in place, and paintActions fades it onto its working face (a
// soft fill that grows, the verb and how far it is), which says at the end how it went ("✓ Start"), and back onto
// its face, which then says what it offers now ("Stop").
export function actionButton({ key, verb = "", label = "", icon: glyph = "", cls = "", attrs = "" }) {
  const verbs = [].concat(verb).filter(Boolean).join(" "), first = verbs.split(" ")[0];
  const base = /\bicon-btn\b/.test(cls) ? "pbtn" : "btn pbtn", words = first ? ing(first) : "";
  // The working face is as wide as the longest it says of its verb: the verb with "…", and at 100% when its job is
  // measured, so that it does not move as the job goes on. At rest the button has the size of its face: it takes the
  // width of its working face only while it works (components.css).
  const measured = verbs.split(" ").some((v) => MEASURED.has(v));
  const sizes = !words ? "" : `data-wait="${esc(words)}…"${measured ? ` data-size="${esc(words)} 100%"` : ""}`;
  const face = `${glyph ? icon(glyph) : ""}${label ? `<span>${esc(label)}</span>` : ""}`;
  return `<button class="${base} ${cls}" data-act-for="${esc(key)}"${verbs ? ` data-act-verb="${esc(verbs)}"` : ""} ${attrs}>`
    + `<span class="pbtn-wrap"><span class="pbtn-face">${face}</span>`
    + `<span class="pbtn-side" aria-hidden="true"><span class="pbtn-fill"></span><span class="pbtn-text" ${sizes}>`
    + `<span><span class="spinner"></span><span class="pbtn-mark"></span><span>${words ? `${esc(words)}…` : ""}</span></span>`
    + `</span></span></span></button>`;
}

// The state of what `key` starts and stops (store.unit) while it is on its way: starting or stopping, its health
// checks running. Null otherwise, for a key that names no such thing, and while Docker does not answer.
function onItsWay(key) {
  const state = store.state && !dockerDown() ? store.unit(key)?.state : null;
  return ["starting", "stopping"].includes(state?.key) ? state : null;
}

// What an action on `key` is doing, as its button shows it ({verb, progress, title, full}), or null: the running job
// on it; else the click that waits for its answer (store.pending); else 100% for a moment once it got there (`full`,
// FULL). A job that ended well leaves its button at work until what it started or stopped got there (settle): an
// add-on, a setup, a playground, a group or a container while it is on its way, its health checks running; the
// platform, and a key that names no such thing (plugins:<product>), while the server measures anything the job
// started. Meanwhile it says how far the server says its item is, else how far the job is. One that failed or was
// canceled leaves it at once. A toggle (no `verbs`) also says what its item is on its way to, whoever moves it (an
// add-on that a playground starts), but not while its own job that failed or was canceled left it on its way. While
// the Lab UI does not answer, how far the job is is not known.
function acting(key, verbs) {
  const allowed = (verb) => !verbs || verbs.split(" ").includes(verb);
  const job = store.state?.job, running = job && !job.done ? job : null;
  if (running?.for === key && allowed(running.verb)) {
    return { verb: running.verb, progress: store.offline ? null : running.progress, title: running.title };
  }
  const verb = store.pending.get(key);
  if (verb && allowed(verb)) return { verb, progress: null, title: ing(verb) };
  const f = flashes.get(key);
  if (f?.full > Date.now() && allowed(f.verb)) return { verb: f.verb, progress: 1, title: ing(f.verb), full: true };
  const ended = job?.done && job.for === key && job.id !== arrived ? job : null;
  if (ended?.ok && allowed(ended.verb)) {
    return { verb: ended.verb, progress: store.progress(key) ?? ended.progress, title: ended.title };
  }
  const state = verbs || ended ? null : onItsWay(key);
  return state && { verb: state.key === "starting" ? "start" : "stop", progress: store.progress(key),
    title: running?.title || state.label };
}

// The word of the button that started a job: its title without the name of what it acts on ("Turn off Kafka" → "Turn
// off"), else the verb's own word
function wordOf(key, verb, title = "") {
  const name = store.unit(key)?.name;
  return name && title.endsWith(` ${name}`) ? title.slice(0, -name.length - 1) : WORD[verb] || "";
}

// How an action on `key` went, for its button to say before it gives its face back; with `full`, it says 100% that long first
function record(key, verb, ok, full = 0, title = "") {
  const at = Date.now();
  flashes.set(key, { verb, word: wordOf(key, verb, title), ok, at, until: 0, full: at + full });
  if (full) setTimeout(() => store.notify(), full + 20);   // its face comes back then: every page draws again
}
// Whether the flash `f` is for a button of `verbs` and still to be said: shown less than FLASH ago, or not shown yet
const says = (f, verbs, now) => !!f && (!verbs || verbs.split(" ").includes(f.verb))
  && (f.until ? now < f.until : now - f.at < UNSEEN);

// Whether a button of `key` showing `verbs` has something to say: its action at work, waiting for its answer or for
// its item to get there, or for a moment how it went. A page keeps such a button drawn meanwhile, though the state no
// longer offers it: the Stop of a platform that stopped, a Restart whose containers are down. Once it has said it,
// every page draws again.
export function showing(key, verbs = "") {
  settle();   // a job that just ended says how it went from this draw on
  return !!acting(key, verbs) || says(flashes.get(key), verbs, Date.now());
}

// How an action ended, said by its button for a moment before it gives its face back: a check, or the error tone, and its word
export function flash(key, verb, ok) {
  record(key, verb, ok);
  paintActions();
}

// A job just started: its button says how it went once it is over, even when it ends before a refresh sees it run
export function follow(job) {
  if (job?.for && !followed.has(job.id)) followed.set(job.id, { key: job.for, verb: job.verb, title: job.title, seen: false });
}

// Whether the job of the state, which ended, left what it acts on where it took it. What starts and stops by its key
// (store.unit) is there once it is no longer on its way, which nothing knows while Docker does not answer. The
// platform also waits until the server measures nothing more on its way: the add-ons and the playground its job
// started are there too. A key that names no such thing (plugins:<product>) is there once the server measures nothing
// more. A job that ended well before this page followed it (the page loaded since) is there unless the server still
// measures it: what moves its item now is not its doing.
function there(job) {
  const unit = store.unit(job.for), alone = !!unit && job.for !== "platform";
  const measured = alone ? store.progress(job.for) !== null : store.measuring();
  if (job.ok && !followed.has(job.id) && !measured) return true;
  if (unit && dockerDown()) return false;
  return !onItsWay(job.for) && (alone || !measured);
}

// How the jobs followed went, said once it is known. The job of the state, once it ended, is seen where it left what
// it acts on (`arrived`) as soon as it is there. A job that failed says so at once, a canceled one says nothing, one
// that ended well says how it went once it is there: done, after 100% for a moment when the job got all the way,
// or the error tone when its item got there with an issue. One job runs at a time: another one running means that a
// job followed ended unseen. A state without a job, once one was seen running and not seen ending, comes from a
// Lab UI that restarted meanwhile: that job was interrupted.
function settle() {
  const job = store.state?.job;
  if (!job) return lost();
  if (job.done && job.id !== arrived && there(job)) arrived = job.id;
  // A job first seen once it ended, what it acts on still on its way (the page loaded meanwhile), is followed as well
  if (!job.done || (job.ok && job.id !== arrived)) follow(job);
  for (const [id, f] of followed) {
    const { key, verb } = f;
    if (job.id !== id) {
      if (!job.done) followed.delete(id);
    } else if (!job.done) {
      f.seen = true;
    } else if (!job.ok || arrived === id) {
      followed.delete(id);
      if (job.error === "Canceled") continue;
      const ok = job.ok && store.unit(key)?.state.key !== "issue";
      // 100% only when the job got all the way: not when something else it started is still on its way (the gateway
      // an add-on restarts), nor when what it waits for will not get there (the server stopped measuring it)
      record(key, verb, ok, ok && MEASURED.has(verb) && (job.progress ?? 1) >= 1 ? FULL : 0, f.title);
    } else {
      // It ended well, what it moved is still on its way: a Lab UI that restarts now interrupts nothing
      f.seen = false;
    }
  }
}

// The jobs followed that the state no longer has: the Lab UI restarted meanwhile. Those seen running and not
// seen ending were interrupted: their buttons say so in the error tone, and a toast says which.
function lost() {
  for (const [id, { key, verb, title, seen }] of followed) {
    followed.delete(id);
    if (!seen) continue;
    record(key, verb, false, 0, title);
    toast(`${title}: interrupted`, true);
  }
}

// What a button of `key` says now of how its action went: {mark: "done" or "failed", word}, or null. It lasts FLASH
// from the first time a button shows it; one that no button showed within UNSEEN is dropped. Once it ends, every page
// draws again: a button kept for it (showing) makes way for what the state offers.
function flashOf(key, verbs) {
  const f = flashes.get(key), now = Date.now();
  if (!f || (verbs && !verbs.split(" ").includes(f.verb))) return null;
  if (!f.until && now - f.at < UNSEEN) {
    f.until = now + FLASH;
    setTimeout(() => store.notify(), FLASH + 20);
  }
  if (now < f.until) return { mark: f.ok ? "done" : "failed", word: f.word || (f.ok ? "Done" : "Failed") };
  flashes.delete(key);
  return null;
}

// Whether a look shows the working face: at work, or saying how it went
const swapped = (look) => !!(look.act || look.flash);

// One button in its look ({act, flash}): on its working face while it acts, with the verb, how far it is and
// the fill, then, once it is over, a check or the error mark with the word of the action ("✓ Start"); else on its
// face. A button on its working face takes no click, without the gray of a disabled one; what it was is given back
// once its face is back.
function paint(b, { act, flash: ended }) {
  adopt(b);
  const was = b.classList.contains("busy") || b.classList.contains("said");
  const [face, side] = b.firstElementChild.children, fill = $(".pbtn-fill", side), up = !!(act || ended);
  if (up && !was) {
    unsay(b);
    // A new action fills from nothing, not from where the last one on this button ended
    fill.style.transition = "none";
    fill.style.width = "0";
    void fill.offsetWidth;
    fill.style.transition = "";
  }
  b.classList.toggle("busy", !!act);
  b.classList.toggle("said", !act && !!ended);
  if (!up && was) {
    showOff(b);
    if (b.hasAttribute("data-busy")) waitOn(b, store.wait());
  }
  const aria = [[b, "aria-busy", act], [face, "aria-hidden", up], [side, "aria-hidden", !up]];
  for (const [el, name, on] of aria) {
    if (on) el.setAttribute(name, "true");
    else el.removeAttribute(name);
  }
  if (up) b.setAttribute("aria-disabled", "true");
  if (act) {
    const measured = act.progress !== null && act.progress !== undefined;
    const percent = Math.round((act.progress || 0) * 100);
    b.dataset.tip = act.title;
    b.classList.toggle("measured", measured);
    fill.style.width = measured ? `${percent}%` : "";
    $(".pbtn-text > span > :last-child", side).textContent = measured ? `${ing(act.verb)} ${percent}%` : `${ing(act.verb)}…`;
  }
  const said = act || !ended ? "" : `${ended.mark}|${ended.word}`;
  if (b._said === said) return;
  const leaving = !said && !act && !!b._said;
  b._said = said;
  if (said) {
    unsay(b);
    b.classList.add(ended.mark);
    delete b.dataset.tip;
    $(".pbtn-mark", side).innerHTML = icon(MARK[ended.mark]);
    $(".pbtn-text > span > :last-child", side).textContent = ended.word;
  } else if (leaving) {
    // Its face fades back while the working face still says how it went, then the working face is made ready for the next action
    const swap = parseFloat(getComputedStyle(b).getPropertyValue("--swap")) * 1000 || 0;
    b.classList.add("leaving");
    b._leave = setTimeout(() => unsay(b), swap);
  } else unsay(b);
}

// The working face no longer says how the last action went
function unsay(b) {
  clearTimeout(b._leave);
  b.classList.remove("done", "failed", "leaving");
  $(".pbtn-mark", b).innerHTML = "";
}

// Sets every button of an action under `root` (actionButton): it runs once the state is drawn (main.js), and after a
// page draws its own buttons. A row written again replaces its button: the new one takes, without moving, the look
// its key had, and goes on with a fade the old one had begun. So a button fades to its working face at the click and back once its
// action is over (its job ended and what it started or stopped got there), whatever the refreshes draw meanwhile, and
// a button shown for the first time while its action works does not fade at all.
export function paintActions(root = document) {
  settle();
  const now = Date.now();
  const buttons = $$("[data-act-for]", root).map((b) => {
    const key = b.dataset.actFor, verbs = b.dataset.actVerb || "", working = acting(key, verbs);
    // An icon alone says no percentage: rather than 100% for a moment, it says at once how its action went
    const act = working?.full && b.matches(".icon-btn") ? null : working;
    return { b, id: `${key}|${verbs}`, look: { act, flash: act ? null : flashOf(key, verbs) } };
  });
  // A new button: where its key was, or where its fade began when the replaced one was fading (`gone` ms ago)
  let swap;
  const fresh = buttons.filter(({ b }) => !b._painted).map(({ b, id, look }) => {
    const was = shown.get(id) || { look, from: look, at: 0 }, gone = now - was.at;
    const fading = swapped(was.from) !== swapped(was.look) && gone < 2000
      && gone < (swap ??= parseFloat(getComputedStyle(b).getPropertyValue("--swap")) * 1000);
    b._painted = true;
    b.classList.add("still");
    paint(b, fading ? was.from : was.look);
    return { b, was, gone: fading ? gone : null };
  });
  if (!fresh.length) return paintAll(buttons, root, now);
  void document.body.offsetWidth;   // the browser takes these looks as the ones they move from
  const going = fresh.filter(({ gone }) => gone !== null);
  for (const { b, was, gone } of fresh) {
    b.classList.remove("still");
    if (gone === null) continue;
    for (const part of b.firstElementChild.children) part.style.transitionDelay = `${-gone}ms`;
    paint(b, was.look);
  }
  if (going.length) {
    void document.body.offsetWidth;   // their fades begin, already `gone` along
    going.forEach(({ b }) => { for (const part of b.firstElementChild.children) part.style.transitionDelay = ""; });
  }
  paintAll(buttons, root, now);
}

// Every button in its look now, and what its key shows kept
function paintAll(buttons, root, now) {
  for (const { b, id, look } of buttons) {
    paint(b, look);
    const was = shown.get(id);
    shown.set(id, !was ? { look, from: look, at: 0 }
      : swapped(was.look) === swapped(look) ? { ...was, look } : { look, from: was.look, at: now });
  }
  // Painted whole, the page tells which keys are still drawn: one that comes back later starts from what it is then
  if (root === document) for (const id of [...shown.keys()]) if (!buttons.some((x) => x.id === id)) shown.delete(id);
}


// --- Segmented controls -----------------------------------------------------------

// A segmented control is one stop of Tab, on its checked button; the arrows move the choice inside it, as in a group
// of radio buttons, or only the focus, as in tabs (a tab is selected, a radio checked). Every page draws and checks its own buttons: their
// stops follow aria-checked and aria-selected wherever they change.
const CHOICE = '[role="radio"], [role="tab"]';
const chosen = (el) => el.getAttribute("aria-checked") === "true" || el.getAttribute("aria-selected") === "true";

function rove() {
  for (const group of $$(".segmented")) {
    const radios = $$(CHOICE, group);
    const on = radios.find(chosen) || radios[0];
    for (const r of radios) {
      const stop = r === on ? "0" : "-1";
      if (r.getAttribute("tabindex") !== stop) r.setAttribute("tabindex", stop);
    }
  }
}
// SEARCH: a field that a search is typed in has a clear button of the Lab UI's, which shows while it holds text (components.css; the
// browser's own is hidden: it is the system's blue and ignores the theme). Every search of every page and dialog gets it once it is
// drawn; a click on it empties the field as typing would, and leaves the keyboard in it.
const CLEAR = `<button type="button" class="icon-btn search-clear" aria-label="Clear the search" data-tip="Clear">${icon("close")}</button>`;
function clearButtons() {
  for (const input of $$('.search > input[type="search"]')) {
    if (!input.nextElementSibling?.matches(".search-clear")) input.insertAdjacentHTML("afterend", CLEAR);
  }
}
document.addEventListener("click", (e) => {
  const input = e.target.closest?.(".search-clear")?.previousElementSibling;
  if (!input) return;
  input.value = "";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
});

new MutationObserver(() => { rove(); clearButtons(); }).observe(document.body, { subtree: true, childList: true, attributeFilter: ["aria-checked", "aria-selected"] });
rove();

const STEPS = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
document.addEventListener("keydown", (e) => {
  const radio = e.target.closest?.(`.segmented :is(${CHOICE})`);
  if (!radio || !(e.key in STEPS || e.key === "Home" || e.key === "End")) return;
  e.preventDefault();
  const group = radio.closest(".segmented"), radios = $$(`:is(${CHOICE}):not(:disabled)`, group), n = radios.length;
  const next = radios[e.key === "Home" ? 0 : e.key === "End" ? n - 1 : (radios.indexOf(radio) + STEPS[e.key] + n) % n];
  if (!next || next === radio) return;
  next.focus();
  if (radio.getAttribute("role") === "tab") return;   // a tab opens its panel with Enter or Space: a UI is not loaded on the way
  next.click();
  // The page may draw the control again as it takes the choice: the keyboard stays on what is checked
  const label = group.getAttribute("aria-label"), parent = group.parentElement;
  const drawn = group.isConnected ? group : $$(".segmented", parent).find((g) => g.getAttribute("aria-label") === label);
  if (drawn) $$(CHOICE, drawn).find(chosen)?.focus();
});

// --- Lists ------------------------------------------------------------------------

// The children of `parent`, one per key and in the order of `keys`, kept from one refresh to the next by their
// data-key: those no longer listed are removed, a missing one is made by make(key). Each is then written apart
// (setHTML): a change rewrites its own child, and the others keep the focus, the hover and an open menu.
export function keyed(parent, keys, make) {
  const wanted = new Set(keys);
  for (const el of [...parent.children]) if (!wanted.has(el.dataset.key)) el.remove();
  const kept = new Map([...parent.children].map((el) => [el.dataset.key, el]));
  return keys.map((key, i) => {
    let el = kept.get(key);
    if (!el) {
      el = make(key);
      el.dataset.key = key;
    }
    if (parent.children[i] !== el) parent.insertBefore(el, parent.children[i] || null);
    return el;
  });
}

// What a list says in its .empty block when it has no row to show: "Nothing starred" or "Nothing running" when the
// Starred or the Running filter alone empties it (`starred`, `running`), else "No matches", with "Clear filters" where
// more than a search filters it (`filtered`): a plain button under the words, as every empty block has its action. The page clears them
// on a click on [data-clear].
export const emptyHtml = ({ filtered, running, starred } = {}) => (starred ? "Nothing starred" : running ? "Nothing running"
  : `No matches${filtered ? '<button class="btn" data-clear>Clear filters</button>' : ""}`);

// Long lists, one page at a time: "1–50 of 197" with previous / next, hidden when everything fits
export function Pager(el, size, onChange) {
  let page = 0;
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-page]");
    if (!b || b.disabled) return;
    page += Number(b.dataset.page);
    onChange();
  });
  return {
    // The page shown, from 0; set, the one it slices next: a list shown again as it was
    get page() { return page; },
    set page(value) { page = value; },
    reset() { page = 0; },
    // The page that holds the item at `index` of the list it slices next
    reveal(index) { page = Math.floor(index / size); },
    slice(items) {
      const pages = Math.max(1, Math.ceil(items.length / size));
      page = Math.min(page, pages - 1);
      el.hidden = items.length <= size;
      const from = page * size;
      setHTML(el, `<span>${from + 1}–${Math.min(items.length, from + size)} of ${items.length}</span>
        <button class="icon-btn prev" data-page="-1" ${page === 0 ? "disabled" : ""} aria-label="Previous page">${icon("chevron")}</button>
        <button class="icon-btn next" data-page="1" ${page >= pages - 1 ? "disabled" : ""} aria-label="Next page">${icon("chevron")}</button>`);
      return items.slice(from, from + size);
    },
  };
}

// Enter and Space open or close a row that opens, as a click does: under `root`, the rows that match `selector`.
// toggle(row) answers the selector of the row when it drew it again: the focus goes back to it.
export function onRowKey(root, selector, toggle) {
  root.addEventListener("keydown", (e) => {
    if (!e.target.matches(selector) || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    const drawn = toggle(e.target);
    if (drawn) $(drawn, root)?.focus();
  });
}

// After rows were drawn again whole: the focus goes to `selector` (the control the keyboard used, in its row drawn
// again) when it is lost, on the body or on what is hidden (the picker that closed), not when it went elsewhere
export function refocus(selector) {
  const at = document.activeElement;
  if (!at || at === document.body || at.closest("[hidden]")) $(selector)?.focus();
}

// "/" goes to the search of the open dialog, or of the visible page, unless it is typed in a field: a box to tick
// takes no text
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || !router.current) return;
  if (e.target.closest("textarea, select, [contenteditable], input:not([type=checkbox], [type=radio])")) return;
  const input = $("input[type=search]", $$("dialog[open]").pop() || $(`#page-${router.current.page.id}`));
  if (!input) return;
  e.preventDefault();
  input.focus();
});

// A click on a copy button (core.js), wherever it is drawn, copies its data-copy value: the button turns into a check for a
// moment.

document.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    // Clipboard permission refused: copy through a selection instead
    const area = Object.assign(document.createElement("textarea"), { value: button.dataset.copy });
    button.after(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) return;
  }
  const was = button.innerHTML, icon_only = button.classList.contains("icon-btn");   // a button with words says Copied in them
  button.innerHTML = icon_only ? icon("check") : `${icon("check")}Copied`;
  button.classList.add("done");
  setTimeout(() => { button.innerHTML = icon_only ? icon("copy") : was; button.classList.remove("done"); }, 1500);
});

// --- Stars ------------------------------------------------------------------------

// The star of an add-on, a setup or a playground, next to its name: what is starred is kept at hand on this machine,
// and a list can show only that. It turns at the click; the server's word follows with the next state. On a row of a
// list (`row`) it is no stop of Tab: S on the row turns it (the list's keys), and Tab goes on to the row's buttons.
// It is one control with one name ("Star Acme Bakery") whose pressed state says whether it is starred; the keyboard reaches it, and S on a
// row's toggle turns it too.
export const starButton = (x, row = false) => `<button class="icon-btn star" data-star aria-pressed="${!!x.starred}"
    data-tip="${row ? `${x.starred ? "Unstar" : "Star"} (S)` : x.starred ? "Unstar" : "Star"}" aria-label="${esc(`Star ${x.name}`)}">${icon("star")}</button>`;

export async function toggleStar(x) {
  x.starred = !x.starred;
  store.notify();
  try {
    await api(`/api/${x.kind}s/${x.id}/star`, { starred: x.starred });
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}
