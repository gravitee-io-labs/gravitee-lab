// The Catalogs page (#/catalogs, #/catalogs/<ns>): where playgrounds, setups and add-ons come from. One card of the lists' own grid, a row
// for each catalog in the order the server gives (the first listed, Personal when it holds something, the others as they were added, then
// the ones not added), each with the number of its items, its state as a dot and a word and the one thing to press: Update, Try again, Add. A row
// opens on what the catalog is (its address, its version, who can read it), what it holds, and what in it cannot run. The server computes
// every state, word and reason (`state`, `held`); the page draws them. Update, Go back, Remove, Repair and Check again are plain requests,
// not jobs: they take the key `catalog-op:<ns>`, so that their button says so and Start never says "Wait for". While one is at work the row
// says so in its state (a menu entry has no button to show it on) and offers nothing else to press. The dialogs are dialogs/catalog.js's.

import { $, esc, icon, setHTML, api, store, router, ago, copyButton } from "../core.js";
import { toast, openMenu, keyed, statusHtml, progressBar, tipHtml, paintActions, actionButton, holdOff, contentStatus, confirmEffects, GAP } from "../components/ui.js";
import { busy } from "../activity/jobs.js";
import { selectCatalog } from "./list.js";
import { NO_GIT, usesCard, openAdd, addCatalog, tryAgain, openUpdate, openGoBack, openRemove, openAllow, openNew } from "../dialogs/catalog.js";

const NS = "[a-z][a-z0-9-]{0,22}[a-z0-9]";
const HOLDS = [["playground", "Playgrounds", "#/playgrounds"], ["setup", "Setups", "#/setups"], ["addon", "Add-ons", "#/addons"]];
// The states that offer Update: a newer version, and an address that now serves another history (Update anyway is what it leads to)
const UPDATES = new Set(["update", "rewritten"]);
// The dot of a state, by its tone: what is on its way pulses, what needs nothing is a muted check
const DOT = { error: "issue", warn: "warn", working: "starting" };
const ACCESS = "Measured when it was added. Private means Git asked for a sign-in.";
const IGNORED = "Settings, keys, links and agent instructions are never installed from a catalog.";

let section, target = null;
const opened = new Set();   // the ids of the rows that are open
// The rows that an address opened (#/catalogs/<ns>), which the next address closes again but the row it names: Back returns to the list as it was
const routed = new Set();

const keyOf = (row) => row.id ?? row.url ?? row.name;
const total = (counts = {}) => HOLDS.reduce((sum, [kind]) => sum + (counts[kind] || 0), 0);
// A row that opens is a catalog that is there, or this machine's own; one that is only offered has nothing to open on
const opens = (row) => ["git", "folder", "machine", "imports"].includes(row.kind);
const installed = (row) => row.kind !== "git" || !!row.commit;
const noGit = () => store.state?.git?.available === false;
const offline = () => (store.offline ? "Lab UI offline" : store.error ? "Lab UI error" : "");
const updates = (row) => row.kind === "git" && (UPDATES.has(row.state?.key) || (row.state?.key === "lab_old" && row.attention === "update"));
// A catalog that is being downloaded now; one with no copy that is not offers Try again (its last try failed, or none was made)
const downloading = (row) => typeof row.progress === "number" || row.state?.key === "downloading";
const retries = (row) => row.kind === "git" && !row.commit && !downloading(row);

// --- State ------------------------------------------------------------------------------------------------------------------

// What a plain request on a row is doing, said in its state meanwhile: the menu has no button to show it on. The server's own word replaces it
// once the request is over.
const WORKING = { check: ["checking", "Checking", "muted"], update: ["updating", "Updating", "working"], repair: ["updating", "Updating", "working"],
  remove: ["removing", "Removing", "working"] };

// The verb of the plain request at work on a row ("check", "auto"), or ""
const requested = (row) => (row.id && store.pending.get(`catalog-op:${row.id}`)) || "";
// What is at work on a row, in a word ("Checking"), for the reason a control waits; "" when nothing is
const working = (row) => (requested(row) ? WORKING[requested(row)]?.[1] ?? "Working" : "");

function stateOf(row) {
  const doing = WORKING[requested(row)];
  return doing ? { key: doing[0], label: doing[1], tone: doing[2], reason: "", next: "" } : row.state || {};
}

// The state of a row: a dot and a word, and a popover with its title, the reason on one line and the next step on one line; nothing to add
// where the word says it all (Checking, Updating). The download or the update shows how far it is under the word.
function stateHtml(row) {
  const state = stateOf(row), key = state.key;
  if (!key || key === "machine") return "";
  const dot = key === "uptodate" || key === "linked" ? "done" : key === "suggested" ? "off" : DOT[state.tone] || "";
  const lines = [state.label, state.reason, state.next].filter(Boolean);
  const bar = row.id && (downloading(row) || key === "updating") ? progressBar(`catalog:${row.id}`) : "";
  return statusHtml(dot, state.label || key, { tip: lines.length > 1 ? lines : [], quiet: state.tone === "muted", extra: bar });
}

// --- Rows ---------------------------------------------------------------------------------------------------------------------

// The one thing to press: Update where there is something to update, Try again where no copy was downloaded, Add where it is only offered. A
// button that needs Git while it is missing waits and says so.
function actions(row) {
  const off = (why) => (why ? ` aria-disabled="true" data-why="${esc(why)}"` : "");
  const git = noGit() ? NO_GIT : "";
  // Another request at work on the row holds its buttons (the button of Update says its own at work, the check that comes first too: that is
  // its paint)
  const other = ["update", "check"].includes(requested(row)) ? "" : working(row);
  const at = `catalog-op:${row.id}`;
  let button = "";
  if (row.kind === "suggested") button = actionButton({ key: `catalog-op:${row.id ?? row.url}`, verb: "add", label: "Add", cls: "act", attrs: `data-add aria-label="${esc(`Add ${row.name}`)}"${off(git)}` });
  else if (updates(row)) button = actionButton({ key: at, verb: ["update", "check"], label: "Update", cls: "act", attrs: `data-update aria-label="${esc(`Update ${row.name}`)}"${off(other)}` });
  else if (retries(row)) button = `<button type="button" class="btn act" data-retry aria-label="${esc(`Try again, ${row.name}`)}"${off(git || working(row))}>Try again</button>`;
  const more = menu(row).length
    ? `<button class="icon-btn" data-more data-tip="More" aria-label="${esc(`More for ${row.name}`)}" aria-haspopup="true">${icon("more")}</button>` : GAP;
  return `${button}<span class="row-icons">${more}</span>`;
}

function rowHtml(row) {
  const toggle = opens(row)
    ? `<button type="button" class="s-toggle" data-toggle aria-label="${esc(row.name)}" aria-controls="${detailId(row)}">${icon("chevron")}</button>` : "<span></span>";
  const said = row.description ? `<span class="list-sub"><span class="list-desc" data-tip="${esc(row.description)}" data-tip-cut>${esc(row.description)}</span></span>` : "";
  return `${toggle}<span class="list-main"><strong class="list-name" data-tip="${esc(row.name)}" data-tip-cut>${esc(row.name)}</strong>${said}</span>
    <span class="list-col col-cat">${row.kind === "suggested" ? "" : total(row.counts)}</span>
    <span class="list-state">${stateHtml(row)}</span>
    <span class="list-actions">${actions(row)}</span>`;
}

const detailId = (row) => `detail-catalog-${String(keyOf(row)).replace(/\W/g, "-")}`;

// --- The detail ------------------------------------------------------------------------------------------------------------

// A label and its value, when it has one; the heading of a panel
const fact = (label, value) => (value ? `<div class="panel-row panel-fact"><span>${esc(label)}</span><span>${value}</span></div>` : "");
const heading = (title) => `<div class="panel-head"><span>${esc(title)}</span></div>`;
const info = (card, label) => tipHtml(icon("info"), card, "plat-info", label);
const mono = (text) => `<span class="s-version" data-tip="${esc(text)}" data-tip-cut>${esc(text)}</span>`;
const old = (iso) => (ago(iso) ? `${ago(iso)} ago` : "");

// What the catalog is: where it is read from and by whom, which version, when it changed and was looked at, what this machine allows it
function about(row) {
  if (row.kind === "machine" || row.kind === "imports") return "";
  const git = row.kind === "git", folder = row.kind === "folder", uses = !row.listed && (row.uses_machine || []).length > 0;
  const where = row.address ? `<code>${esc(row.address)}</code>${copyButton(row.url || row.address, "Copy the address")}` : "";
  const count = Number(row.ignored) || 0;
  return `<section class="panel">${heading("About")}${row.description ? `<p class="panel-row">${esc(row.description)}</p>` : ""}
    ${fact("Address", where)}
    ${git ? fact("Version", row.version && mono(row.version)) + fact("Commit", row.commit && mono(String(row.commit).slice(0, 7)))
      + fact("Updated", esc(old(row.updated))) + fact("Checked", esc(old(row.checked)))
      + fact("Access", row.audience && `${esc(row.audience === "public" ? "Public" : "Private")}${info(ACCESS, "Access")}`) : ""}
    ${(git || folder) && uses ? fact("Uses this machine", `${row.host === "allowed" ? "Allowed" : "Not allowed"}${info(usesCard(row.uses_machine), "Uses this machine")}`) : ""}
    ${count ? fact("Ignored", `${count} ${count === 1 ? "file" : "files"}${info(IGNORED, "Ignored")}`) : ""}</section>`;
}

// The playgrounds imported from a file and kept as catalogs of their own: one place to the person, Imported, which lists each of them with its
// menu (Remove, and the allowance of what uses this machine), and the settings that an import, a removal or a move kept, each with Remove
const IMPORTED = "imported";
// The settings the Lab keeps (an import removed with Keep settings, an item of Personal that a new version replaced): each list is
// with where they came from, those of Personal on Personal's row and those of an import on Imported's
const keptOf = (row) => (store.state?.kept_settings || []).filter((k) => (row.kind === "machine" ? k.from === "Personal"
  : row.kind === "imports" && k.from !== "Personal"));

function grouped(rows) {
  const imports = rows.filter((r) => r.imported), kept = keptOf({ kind: "imports" });
  if (!imports.length && !kept.length) return rows;
  const counts = Object.fromEntries(HOLDS.map(([kind]) => [kind, imports.reduce((sum, r) => sum + (r.counts?.[kind] || 0), 0)]));
  const group = { id: IMPORTED, kind: "imports", name: "Imported", counts, members: imports, state: { key: "machine" },
    blocked: imports.flatMap((r) => r.blocked || []), problems: imports.flatMap((r) => r.problems || []) };
  const rest = rows.filter((r) => !r.imported), at = rest.findIndex((r) => r.kind === "git" || r.kind === "suggested");
  return at < 0 ? [...rest, group] : [...rest.slice(0, at), group, ...rest.slice(at)];
}

// A line of a panel with its More at the end of the row, as every row has it
const more = (attr, label) => `<button class="icon-btn panel-more" ${attr} data-tip="More" aria-label="${esc(label)}" aria-haspopup="true">${icon("more")}</button>`;

function members(row) {
  if (row.kind !== "imports" || !row.members.length) return "";
  const line = (m) => `<div class="panel-row panel-fact" data-member="${esc(m.id)}"><span>${esc(m.name)}</span><span class="panel-value">${
    total(m.counts)} ${total(m.counts) === 1 ? "item" : "items"}${menu(m).length ? more("data-member-more", `More for ${m.name}`) : ""}</span></div>`;
  return `<section class="panel">${heading("Imports")}${row.members.map(line).join("")}</section>`;
}

function kept(row) {
  const list = keptOf(row);
  if (!list.length) return "";
  const line = (k) => {
    const names = k.items.map((one) => one.name).join(", ");
    return `<div class="panel-row panel-fact" data-kept="${esc(k.folder)}"><span>${esc(names)}</span><span class="panel-value">${
      esc([k.from !== "Personal" ? `From ${k.from}` : "", old(k.at)].filter(Boolean).join(", "))}${more("data-kept-more", `More for the settings kept of ${names}`)}</span></div>`;
  };
  return `<section class="panel">${heading("Kept settings")}${list.map(line).join("")}</section>`;
}

// Kept settings go at the person's word only: they are theirs, and nothing else deletes them. The dialog says what goes, item by item
async function removeKept(k) {
  const rows = [{ label: "Deletes", items: k.items.map((one) => `${one.name}: ${one.kinds.join(", ")}`) }];
  if (!(await confirmEffects("Remove kept settings", rows, "Remove", true))) return;
  try {
    const answer = await api("/api/kept-settings/delete", { folder: k.folder });
    toast(answer.warning || "Removed", !!answer.warning);
  } catch (err) {
    toast(err.message, true);
  } finally {
    store.refresh();
  }
}

// What it holds: a button for each kind that sets that list's catalog filter to this catalog and goes there; below, what in it cannot run, one
// line for each item with its first reason, then each file that did not load
function holds(row) {
  const kinds = HOLDS.filter(([kind]) => row.counts?.[kind] > 0);
  const blocked = (row.blocked || []).map((b) => fact(b.name, esc((b.reasons || [])[0] ?? "")) || fact(b.name, "&nbsp;"));
  const problems = (row.problems || []).map((text) => {
    const at = String(text).indexOf(": ");
    return at > 0 ? fact(text.slice(0, at), esc(text.slice(at + 2))) : fact(text, "&nbsp;");
  });
  if (!kinds.length && !blocked.length && !problems.length) return "";
  const lists = kinds.map(([kind, name]) => `<div class="panel-row panel-fact"><span>${name}</span><span><button type="button" class="text-link" data-holds="${kind}"
    aria-label="${esc(`Show ${row.counts[kind]} ${name.toLowerCase()}`)}">${row.counts[kind]}</button></span></div>`);
  return `<section class="panel">${lists.length ? heading("Holds") + lists.join("") : ""}${blocked.length || problems.length ? heading("Cannot run") + blocked.join("") + problems.join("") : ""}</section>`;
}

// --- The menu -----------------------------------------------------------------------------------------------------------------

async function copy(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast("Copied");
  } catch {
    toast("Could not copy", true);
  }
}

// Why stopping the allowance waits: an item that uses this machine is on (the server's `held` is for updating, going back and removing)
function allowHeld(row) {
  const on = (row.uses_machine || []).map((u) => ["addon", "setup", "playground"].map((kind) => store.item(kind, u.id)).find(Boolean)).find((x) => x?.on || x?.up);
  return on ? `${on.up ? "Stop" : "Turn off"} ${on.name} first` : "";
}

// A request on a row: what it answers is said in the toast of its word, a refusal in the server's words. Its key is quiet: no button shows how
// it went, and the row is drawn again with the state it has once the key is released.
async function request(row, verb, path, body, word) {
  try {
    const sent = busy(`catalog-op:${row.id}`, verb, api(path, body), { quiet: true });
    store.notify();   // the row says what is at work from the click, not from the next poll
    const answer = await sent;
    if (word) toast(word);
    return answer;
  } catch (err) {
    toast(err.message, true);
    return null;
  } finally {
    store.refresh();
  }
}

// Asks the address again; the answer is the state of the row. Its word is the toast where it is an answer and not a trouble ("Up to date",
// "Update available"); a state that is a trouble (Unreachable, No access, Rewritten...) is said as the error it is, with its reason and
// its next step, as a refusal of the request would be.
async function check(row) {
  const state = (await request(row, "check", `/api/catalogs/${row.id}/check`, {}))?.catalog?.state;
  if (!state?.label) return;
  const trouble = !["uptodate", "update"].includes(state.key) && ["warn", "error"].includes(state.tone);
  const said = trouble ? [state.reason, state.next].filter(Boolean).join(" ") : "";
  toast(said || state.label, !!said);
}

function menu(row) {
  if (!["git", "folder"].includes(row.kind)) return [];
  const git = row.kind === "git", wait = offline(), held = row.held || {}, items = [], doing = working(row);
  // An entry that asks something of the Lab waits while the Lab is offline or a request is at work on the row, and says which
  const hold = (why) => ({ disabled: !!(wait || doing || why), reason: wait || doing || why });
  if (git && !installed(row)) items.push({ label: "Try again", ...hold(downloading(row) ? "Downloading" : noGit() ? NO_GIT : ""), onSelect: () => tryAgain(row.id) });
  else if (git) items.push({ label: "Check again", ...hold(noGit() ? NO_GIT : ""), onSelect: () => check(row) });
  if (git) {
    const on = row.auto !== false;
    items.push({ label: on ? "Stop checking automatically" : "Check automatically", ...hold(""),
      onSelect: () => request(row, "auto", `/api/catalogs/${row.id}/auto`, { enabled: !on }) });
  }
  // The allowance of items that use this machine is the person's for a catalog that is not listed (a listed one has it by being listed)
  if (!row.listed && (row.uses_machine || []).length) {
    const allowed = row.host === "allowed";
    items.push({ label: allowed ? "Stop allowing use of this machine" : "Allow use of this machine", ...hold(allowed ? allowHeld(row) : ""),
      onSelect: () => openAllow(row, !allowed) });
  }
  const web = /^https:\/\//i.test(row.web_url || "") ? row.web_url : "";
  // Where the Lab keeps an imported playground is its own business: no repository to open, no address to copy
  if (web && !row.imported) items.push({ label: "Open repository", href: web });
  if (!row.imported) items.push({ label: "Copy address", onSelect: () => copy(row.url || row.address) });
  if (git) items.push({ label: "Go back to previous version", ...hold(!row.has_previous ? "No previous version" : held.undo === "No previous version" ? "" : held.undo), onSelect: () => openGoBack(row) });
  if (row.state?.key === "damaged") items.push({ label: "Repair", ...hold(""), onSelect: () => repair(row) });
  items.push({ separator: true }, { label: "Remove", danger: true, ...hold(held.remove), onSelect: () => openRemove(row) });
  return items;
}

async function repair(row) {
  await request(row, "repair", `/api/catalogs/${row.id}/repair`, {}, "Repaired");
}

// --- The page -----------------------------------------------------------------------------------------------------------------

// Whether an entry of the list is a row: a catalog that is there or offered, and Personal once it holds something or the Lab keeps
// settings of it. Any other entry (the one that carries how far the fetch of the Add dialog is, `kind: "progress"`) is no row.
const ROWS = ["git", "folder", "suggested"];
const shown = (row) => ROWS.includes(row?.kind) || (row?.kind === "machine" && (total(row.counts) > 0 || keptOf(row).length > 0));

// The table has two sections, on a band each: what is on this machine (Personal, a folder linked as a catalog), then what is fetched
// from a repository (an added catalog, a suggested one). A section with no row has no band.
const SECTIONS = [["Local", (row) => row.kind === "machine" || row.kind === "folder" || row.kind === "imports"], ["Remote", (row) => row.kind === "git" || row.kind === "suggested"]];
const bandKey = (name) => `band:${name}`;

function drawRows(ul, all) {
  const made = SECTIONS.map(([name, test]) => [name, all.filter(test)]).filter(([, rows]) => rows.length);
  const entries = made.flatMap(([name, rows]) => [{ band: name }, ...rows]);
  const make = (key) => (key.startsWith("band:") ? Object.assign(document.createElement("li"), { className: "stack-sep" })
    : Object.assign(document.createElement("li"), { className: "list-item", innerHTML: '<div class="list-row list-grid"></div><div class="list-detail"></div>' }));
  keyed(ul, entries.map((entry) => (entry.band ? bandKey(entry.band) : keyOf(entry))), make).forEach((li, i) => {
    const row = entries[i];
    if (row.band) { li.textContent = row.band; return; }
    const [top, detail] = li.children, open = opens(row) && opened.has(keyOf(row));
    li.classList.toggle("open", open);
    setHTML(top, rowHtml(row));
    $(".s-toggle", top)?.setAttribute("aria-expanded", String(open));
    detail.id = detailId(row);
    detail.hidden = !open;
    setHTML(detail, open ? about(row) + members(row) + kept(row) + holds(row) : "");
  });
}

// The control the keyboard is on, named by its row and what it is, to be found again when the row is drawn anew
const CONTROLS = ["data-toggle", "data-update", "data-retry", "data-add", "data-more"];
function controlOf(el) {
  const control = el.closest?.("button"), attr = control && CONTROLS.find((name) => control.hasAttribute(name)), li = control?.closest(".list-item");
  return attr && li ? `.list-item[data-key="${CSS.escape(li.dataset.key)}"] [${attr}]` : "";
}

function draw(state) {
  if (!section) return;
  const at = document.activeElement, again = at && section.contains(at) ? controlOf(at) : "";
  const readable = Array.isArray(state?.catalogs), rows = readable ? grouped(state.catalogs.filter(shown)) : [];
  holdOff($("[data-add-catalog]", section), noGit(), NO_GIT);
  const status = $("[data-status]", section), list = $(".list-rows", section);
  $(".list-head", section).hidden = !rows.length;
  list.hidden = !rows.length;
  // Nothing to list: the content says there is no catalog (and offers the one the server names); a state that cannot be read says so, with Try again
  setHTML(status, !readable ? '<div class="empty"><span>Catalogs cannot be read</span><button type="button" class="btn" data-reload>Try again</button></div>'
    : rows.length ? "" : contentStatus(state, { kind: "catalog" }) || contentStatus({ ...state, content: { ...state.content, key: "none" } }, { kind: "catalog" }));
  drawRows(list, rows);
  paintActions(section);
  if (again && !at.isConnected) $(again, section)?.focus({ preventScroll: true });
  if (target && rows.some((row) => keyOf(row) === target)) {
    reveal(target);
    target = null;
  }
}

// The row an address opened, in view and its toggle focused, once it is drawn and the router has set the page's scroll
function reveal(id) {
  requestAnimationFrame(() => {
    const li = $(`.list-item[data-key="${CSS.escape(id)}"]`, section);
    li?.scrollIntoView({ block: "nearest" });
    $(".s-toggle", li)?.focus({ preventScroll: true });
  });
}

function toggle(id) {
  if (!opened.delete(id)) opened.add(id);
  draw(store.state);
}

// Sets the catalog filter of a list to this catalog (the list's own: pages/list.js) and goes there
function showHeld(kind, id) {
  selectCatalog(kind, id);
  router.go(HOLDS.find(([k]) => k === kind)[2]);
}

function init(el) {
  section = el;
  el.innerHTML = `
    <div class="page-head">
      <h1>Catalogs</h1>
      <div class="page-actions"><button class="btn" data-new>New catalog</button><button class="btn" data-add-catalog>${icon("plus")}Add catalog</button></div>
    </div>
    <div class="card list-card">
      <div class="list-table">
        <div class="list-head list-grid"><span></span><span>Catalog</span><span class="col-cat">Items</span><span>State</span><span></span></div>
        <ul class="list-rows"></ul>
      </div>
      <div data-status></div>
    </div>`;
  el.addEventListener("click", (e) => {
    const on = (selector) => e.target.closest(selector);
    const button = on("button"), li = on(".list-item"), id = li?.dataset.key;
    // A row that opens does so from a click anywhere on it but its controls, and from its toggle
    if ((!button || "toggle" in button.dataset) && on(".list-row") && !on("a") && $(".s-toggle", li)) return toggle(id);
    if (!button || button.disabled || button.ariaDisabled === "true" || button.getAttribute("aria-disabled") === "true") return;
    const data = button.dataset, row = grouped((store.state?.catalogs || []).filter(shown)).find((x) => keyOf(x) === id);
    const member = row?.members?.find((m) => m.id === button.closest("[data-member]")?.dataset.member);
    if ("new" in data) openNew();
    else if ("addCatalog" in data) openAdd();
    else if ("reload" in data) store.refresh();
    else if (!row) return;
    else if ("update" in data) openUpdate(row);
    else if ("retry" in data) tryAgain(row.id);
    else if ("add" in data) addCatalog(row);
    else if ("memberMore" in data && member) openMenu(button, menu(member));
    else if ("keptMore" in data) {
      const k = keptOf(row).find((one) => one.folder === button.closest("[data-kept]")?.dataset.kept);
      if (k) openMenu(button, [{ label: "Remove", danger: true, disabled: !!offline(), reason: offline(), onSelect: () => removeKept(k) }]);
    }
    else if ("more" in data) openMenu(button, menu(row));
    else if ("holds" in data) showHeld(data.holds, row.id);
  });
}

// An address that names a catalog (#/catalogs/<ns>) opens its row, in view and focused. The rows that other addresses opened close again,
// those opened by hand stay.
function show([id] = []) {
  const again = routed.has(id);
  routed.forEach((key) => { if (key !== id) opened.delete(key); });
  routed.clear();
  if (!id) return;
  if (again || !opened.has(id)) routed.add(id);
  opened.add(id);
  target = id;
}

export const catalogsPage = { id: "catalogs", pattern: new RegExp(`^#/catalogs(?:/(${NS}))?$`), nav: "catalogs", title: "Catalogs", init, show,
  hide: () => { target = null; }, render: draw };
