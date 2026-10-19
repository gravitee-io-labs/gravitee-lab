// The Playgrounds, Setups and Add-ons pages: one list, drawn from what the server says of each item. A row opens
// on its detail: what the item is, what it runs, what comes with it and what uses it. The address of an add-on or a
// setup (#/addons/<id>) opens its row. Where an item comes from is its chip, except for the first catalog's; and the list is filtered by
// catalog when there is more than one to tell apart. While the catalogs download or are missing, the list says so in place of its rows.

import { $, $$, esc, icon, setHTML, loading, api, store, router, recall, remember, ID_PATTERN, splitKey } from "../core.js";
import { toast, effectsDialog, openMenu, GAP, openButton, Pager, emptyHtml, paintActions, contentStatus, originLink,
  actionButton, keyed, dockerDown, stateDot, serviceCells, serviceOpen, noteHtml, noteBadge, blockerButton, itemLink, unopened, starButton,
  toggleStar, statusHtml, progressBar, TURNS, fileButtons } from "../components/ui.js";
import { CategoryFilter } from "../components/combo.js";
import { runJob, runAction, busy, showOutput } from "../activity/jobs.js";
import { selectLogs, hasLogs } from "./logs.js";
import { stopPlayground, pluginNames, shareEntry, deleteEntry, usesMachine, maybeComing } from "./playground.js";
import { MACHINE, THIS_MACHINE, registered, chosenPlace, barred, startPlayground, deployTo, keepItemPlace, goes, whyNot, placesOf } from "../platform/places.js";
import { openFile, openSettings } from "../dialogs/editor.js";
import { openCreate } from "../dialogs/create.js";
import { License } from "../dialogs/license.js";
import { KINDS, openCreateGateway, openCloudToken } from "../dialogs/platform.js";

// The categories and the catalogs have their own filter
const NO_FILTER = { q: "", running: false, starred: false };

// What the search looks into: the name, the catalog it comes from, the description, the category, the tags and the members, here and on the
// Dashboard
export const words = (x) => [x.name, x.description, x.category, ...(x.tags || []),
  ...[...(x.setups || []), ...(x.members || x.addons || [])].map((m) => m.name), ...(x.origin?.name ? [x.origin.name] : [])].join(" ").toLowerCase();

// What the Running filter keeps: what its dot says runs (something of it runs, and it is not off: a setup some add-ons
// of which run is off until they all are on), or what is on its way. What is on and does not run is not running.
export const alive = (x) => (x.up && x.state.key !== "off") || ["starting", "stopping"].includes(x.state.key);

// The consoles of an item as menu entries, in the server's order (the legacy ones last); one that is down cannot be
// opened, and says why
const consoles = (x) => x.links.map((l) => ({ label: l.label,
  hint: l.port ? `:${l.port}` : "", href: l.url, dot: l.up ? "healthy" : "", disabled: !l.up, reason: unopened(l) }));

// --- Deploy to: what a playground's row says once a platform is registered -----------

// A playground that can deploy to a platform's kind too, while a platform is registered; without one, none of this is drawn
const deploys = (x) => registered() && !!x.deploys_to?.length;

// A neutral mark after the name: where else it deploys
const cloudMark = (x) => {
  const said = x.runs_on ? `Runs on ${KINDS[x.runs_on]?.name || x.runs_on}`
    : `Deploys to ${x.deploys_to.map((kind) => KINDS[kind]?.name || kind).join(", ")}`;
  return `<span class="pill icon-pill" role="img" tabindex="0" aria-label="${esc(said)}" data-tip="${esc(said)}">${icon("cloud")}</span>`;
};

// The place the playground is on (the one chosen on any page), when it is a platform's and cannot be deployed to yet. This machine
// and a place not read yet are not.
function unready(x) {
  const place = chosenPlace(x);
  return place && place.state !== "ready" ? place : null;
}

// What a place lacks (its `missing`, else its `reason`), as the note of a row says it and where it is fixed: in the form that
// takes it (`fix`), or on the platform's row, where the note leads
const LACKS = {
  "No hybrid gateway": { note: "Needs a hybrid gateway", fix: "gateway" },
  "No Cloud Token": { note: "Needs a Cloud Token", fix: "token" },
  "Cloud Token expired": { note: "Needs a Cloud Token", fix: "token" },
  "Cloud Token refused": { note: "Needs a Cloud Token", fix: "token" },
  "Invalid token": { note: "Invalid token", tone: "err" },
  Unreachable: { note: "Unreachable", tone: "warn" },
};

// The note of a row whose place cannot be deployed to yet, with the form that fixes it when there is one
function placeLack(x) {
  const place = unready(x), lacks = place && LACKS[place.missing || place.reason];
  if (!lacks) return null;
  if (lacks.fix) return { note: { note: lacks.note, note_tone: "warn", note_title: place.reason }, fix: lacks.fix };
  return { note: { note: lacks.note, note_tone: lacks.tone, note_target: { kind: "platform", id: place.platform.id } }, fix: "" };
}

// What keeps a Start from working is said beside the Start, never in the State: the server's note of the row when its Start is
// refused, else what its place lacks (`own`: the server's note is the warning, so the State does not say it too). The State says what the
// item is, and the notes that are not in the way of a start.
function blocker(x) {
  const a = x.action;
  if (a.do !== "start") return null;
  if (!a.enabled) return x.note ? { note: x, fix: "", own: true } : null;
  const lack = placeLack(x);
  return lack && { ...lack, own: false };
}

// The warning of a row, drawn, and whether it is the row's own note (the State then leaves the note out): null when nothing is in the
// way of its Start. The lists and the Dashboard say it the same way.
export function startWarning(x) {
  const stuck = blocker(x);
  return stuck && { html: blockerButton(stuck.note, stuck.fix), own: stuck.own };
}

// The form that takes what the place lacks, in its environment
function fix(x, what) {
  const place = unready(x);
  if (!place) return;
  const form = { platform: place.platform, environmentId: place.environment.id, done: () => store.refreshPlaces() };
  if (what === "gateway") openCreateGateway(form);
  else openCloudToken({ ...form, replacing: place.token.state !== "unset" });
}

// The state of an item, in the one component of every state: its dot and its word, as the Containers page says a line's state. What
// is on its way says how far it is under its word; what is off or stopped is a muted word; nothing while what runs is not known. The
// State of a row of the lists and of the Dashboard's catalog.
export function itemState(x) {
  if (dockerDown()) return "";
  const { key, label } = x.state, moving = ["starting", "stopping"].includes(key);
  return statusHtml(key, label || "Off", { quiet: !label || ["off", "stopped", "absent"].includes(key), extra: moving ? progressBar(`${x.kind}:${x.id}`) : "" });
}

// The Start or Stop of an item: Start, Stop, Turn on or Turn off, as the server says it. Start and Stop, which start or stop containers,
// have their icon; Turn on and Turn off change only what is on, and have none, and their tooltip says what they change. Any action on
// the item shows on it as it works, those of its menu too. A button that cannot be used says why (an add-on without its settings waits
// for them: its note opens them), and so does one that waits for the Lab UI. A row has the button of its width (.act).
const GLYPH = { Start: "play", Stop: "stop" };
export function itemButton(x) {
  const a = x.action, glyph = GLYPH[a.label] || "";
  // An item set to run on a place that is not ready cannot start, and says why
  const lacking = a.enabled && a.do === "start" ? barred(x) : "";
  const off = !a.enabled || !!lacking, tip = TURNS[a.label] ? ` data-tip="${TURNS[a.label]}"` : "";
  return actionButton({ key: `${x.kind}:${x.id}`, label: a.label, icon: glyph, cls: "act",
    attrs: `data-act data-busy aria-label="${esc(`${a.label} ${x.name}`)}"${tip} ${off ? `aria-disabled="true" data-why="${esc(lacking || a.reason)}"` : ""}` });
}

// What a click on an item's Start or Stop does: a playground is stopped as it is on its page
export function actOn(x, action = x.action) {
  if (x.kind === "playground" && action.do === "stop") return stopPlayground(x);
  // A playground's Start deploys to the place chosen on any page, as on its page
  if (x.kind === "playground" && action === x.action) return startPlayground(x);
  // An add-on or a setup starts on the place chosen for it, when it is not This machine
  const place = x.kind !== "playground" && action.do === "start" && chosenPlace(x);
  if (place) return deployTo(x, place, { route: "start", verb: "start", label: action.label });
  runAction(x, action);
}

// --- Detail: what an item is, what it runs or brings, what uses it ------------------

// The add-ons that come with an item: those an add-on needs, the members of a setup, the add-ons a playground
// includes itself (those of its setups are under each setup)
const brought = (x) => (x.kind === "playground" ? x.includes : (x.needs || x.members).map(({ id }) => id))
  .map((id) => store.item("addon", id));

// What uses an item: the playgrounds that include a setup, the setups and the playgrounds that bring an add-on
function users(x) {
  const { setups, playgrounds } = store.state;
  if (x.kind === "setup") return playgrounds.filter((p) => p.setups.some(({ id }) => id === x.id));
  if (x.kind === "addon") return [...setups.filter((s) => s.members.some(({ id }) => id === x.id)),
    ...playgrounds.filter((p) => p.addons.some(({ id }) => id === x.id))];
  return [];
}

// A panel is headings and rows: a heading names what follows, one title per column
const heading = (...titles) => `<div class="panel-head">${titles.map((t) => `<span>${t}</span>`).join("")}</div>`;
const panel = (body, cls = "") => (body ? `<section class="panel ${cls}">${body}</section>` : "");
// A label and its value, when it has one
const fact = (label, value) => (value ? `<div class="panel-row panel-fact"><span>${label}</span><span>${value}</span></div>` : "");

// What is said of an item under its description
const ABOUT = {
  addon: (x) => fact("Not with", Object.entries(x.conflicts).map(([id, reason]) =>
    `<div>${itemLink(store.item("addon", id))}<span class="muted">${esc(reason)}</span></div>`).join(""))
    + fact("Restarts", esc(x.restarts.join(", "))),
  setup: () => "",
  playground: (x) => fact("Tags", x.tags.length ? `<div class="pg-tags">${x.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>` : "")
    + fact("Plugins", pluginNames(x))
    + fact("Duration", esc(x.duration))
    + fact("Links", x.source ? `<a class="text-link" href="${esc(x.source)}" target="_blank" rel="noopener">Source</a>` : ""),
};

// What an item takes of this machine beyond its containers, in its author's words: one pair, cut where the row is short, whole in its tooltip
const uses = (x) => {
  const said = usesMachine(x), whole = [...new Set((x.uses_machine || []).map((use) => use.why).filter(Boolean))].join(", ");
  return fact("Uses this machine", said ? `<span class="muted" ${whole === said ? "" : `data-tip="${esc(whole)}" `}data-tip-cut>${esc(said)}</span>` : "");
};

const about = (x) => panel(heading("About") + (x.description ? `<p class="panel-row">${esc(x.description)}</p>` : "") + ABOUT[x.kind](x)
  + fact("Catalog", originLink(x)) + uses(x));

// The state and the name of a line (`name` as HTML, `label` as its tooltip), then in a lighter tone what it is, when it
// is said, and what the server notes of it; the chevron of a line that opens first, a button named for the line (`opens`:
// whether it is open, null for a line that does not open). A line too long gives way: what ends it first, then the name
// (components.css).
const named = (state, name, label, what = "", opens = null, note = "") => `<span class="panel-name">
  ${opens === null ? "" : `<button type="button" class="s-toggle" data-sub-toggle aria-expanded="${opens}" aria-label="${esc(label)}">${icon("chevron")}</button>`}${stateDot(state)}
  <span data-tip="${esc(label)}">${name}</span>${what ? `<span class="muted" data-tip="${esc(what)}" data-tip-cut>${esc(what)}</span>` : ""}${note}</span>`;

// A service as on the Stack: its state, its name and the word of its state, what is noted of it ("Apply needed"),
// its version, the ports it publishes, its console to open. `aligned`: above lines that have file buttons too.
function serviceRow(name, aligned) {
  const service = store.service(name);
  const note = service.note ? noteHtml(service) : "";
  return `<div class="panel-row">${named(service.state, esc(service.label), [service.label, service.description].filter(Boolean).join("\n"), dockerDown() ? "" : service.state.label, null, note)}
    ${serviceCells(service)}
    <span class="panel-actions">${serviceOpen(name)}${aligned ? GAP + GAP : ""}</span></div>`;
}

// A setup, an add-on or a playground that comes with the item or uses it: its state, its name that leads to it, what
// it opens, its files. `open`: true or false for a line that opens on what it brings, as the setup of a playground;
// `inner`: such a line.
const unitRow = (unit, open = null, inner = false) => `<div class="panel-row ${inner ? "panel-inner" : ""}" ${open === null ? ""
  : `data-sub="${esc(unit.id)}"`}>${named(unit.state, itemLink(unit), unit.name, unit.description, open)}
  <span class="muted">${dockerDown() ? "" : esc(unit.state.label)}</span>
  <span class="panel-actions">${openButton(unit.links, `${unit.kind}:${unit.id}`)}${fileButtons(unit)}</span></div>`;

// What an item runs itself (an add-on's services, a playground's apps and seed), then what comes with it: the setups
// a playground includes, each opening on its add-ons, and the add-ons an add-on needs, a setup groups or a playground
// includes; then what uses it. `opened(id)`: whether the line of that setup is open.
function runs(x, opened) {
  const part = (titles, rows) => (rows.length ? heading(...titles) + rows.join("") : "");
  const setups = (x.setups || []).map(({ id }) => store.item("setup", id)), addons = brought(x), used = users(x);
  const setupRows = setups.map((s) => unitRow(s, opened(s.id))
    + (opened(s.id) ? brought(s).map((a) => unitRow(a, null, true)).join("") : ""));
  return panel(part(["Service", "Version", "Ports"], (x.services || []).map((name) => serviceRow(name, setups.length + addons.length + used.length > 0)))
    + part(["Setup", "State"], setupRows)
    + part([x.kind === "addon" ? "Needs" : "Add-on", "State"], addons.map((a) => unitRow(a)))
    + part(["Used in", "State"], used.map((u) => unitRow(u))), "panel-table");
}

const detail = (x, opened) => about(x) + runs(x, opened);

// --- Rows ---------------------------------------------------------------------------

// One <li> per item, its data-key the item's id, kept from one refresh to the next; its row and its detail are
// written only when their content changed: nothing moves or blinks, a menu opened on a row stays open, and a row
// keeps the focus when it is opened. html(x) gives the two as {row, detail}, the detail empty when the row is closed.
// A row opens from a click anywhere on it but its controls, and from its toggle (the chevron, named for the item), which
// the keyboard reaches: the row itself is no button, so that its controls are controls of their own.
function drawRows(ul, items, html) {
  const make = () => Object.assign(document.createElement("li"), { className: "list-item",
    innerHTML: '<div class="list-row list-grid"></div><div class="list-detail"></div>' });
  keyed(ul, items.map((x) => x.id), make).forEach((li, i) => {
    const [row, detail] = li.children, content = html(items[i]), open = !!content.detail;
    li.classList.toggle("open", open);
    setHTML(row, content.row);
    $(".s-toggle", row)?.setAttribute("aria-expanded", String(open));
    detail.id = `detail-${li.dataset.key.replace(/\W/g, "-")}`;
    detail.hidden = !open;
    setHTML(detail, content.detail);
  });
}

// --- Where the items come from ------------------------------------------------------

// The catalogs the state lists that hold items: those added, git or folder, in the order the server gives
const added = () => (Array.isArray(store.state?.catalogs) ? store.state.catalogs : []).filter((c) => ["git", "folder"].includes(c.kind));
const LOCAL = "local";   // the source of this machine's items: the id of its row
// The first catalog is the one whose items have bare ids, and so no origin: the first of those listed, which the picker lists first
const firstId = () => added().find((c) => c.listed)?.id ?? "@first";
const sourceOf = (x) => (x.personal ? LOCAL : x.origin?.id ?? firstId());
const sourceName = (id) => (id === LOCAL ? "Personal" : added().find((c) => c.id === id)?.name ?? "Catalog");

// The picker lists the first catalog, then the others as they were added, then this machine
function sourceOrder(entries) {
  const rank = (id) => (id === firstId() ? -1 : id === LOCAL ? Infinity : added().findIndex((c) => c.id === id) >= 0 ? added().findIndex((c) => c.id === id) : added().length);
  return entries.sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}

// Whether the list tells the sources of its items apart, from the items it has: not while one catalog (or this machine) holds them all;
// else with a picker of the catalogs, this machine's items among them as Personal
const scopeOf = (all) => (new Set(all.map(sourceOf)).size < 2 ? "" : "picker");

const PICKER = '<button class="combo-btn" data-catalogs aria-haspopup="dialog"><span class="sr-only">Catalog: </span><span class="value"></span>';

// `noun` names what a row is, as the header of its first column
function list(kind, title, noun) {
  // Starred or not is chosen once for each list: it is the same at the next visit
  const STARRED = `list-starred:${kind}`;
  const filters = { ...NO_FILTER, starred: recall(STARRED) === "1" };
  // The ids of the rows that are open, and of the lines open in a detail ("<row>>setup"). A detail is read and
  // closed: unlike the groups of the Stack, which shape their page, it is not remembered from one visit to the next.
  const opened = new Set();
  // The rows that an address opened (#/addons/<id>), which the next address closes again, but the row it names: Back
  // returns to the list as it was. `target`: the row to bring into view and focus once it is drawn.
  const routed = new Set();
  let section, pager, categories, target = null;
  // The control that tells the catalogs apart, drawn from the items there are (scope): none, or the picker; the block of
  // the content of the catalogs, and the places both go in
  let scopeEl = null, scopeKind = "", catalogs = null, wanted = null;
  let rowsEl, emptyEl, contentEl = null, contentHtml = "";
  // The list as it was before an address showed one of its rows, which may clear its filters and turn its page: its
  // filters, its search, its categories, its catalogs and its page, given back by the list's own address. What is changed by hand
  // meanwhile is kept instead.
  let before = null;

  function actions(x) {
    // A playground's row opens its page, in every state. An add-on or a setup with a running container opens its consoles
    const open = kind === "playground" ? `<a class="btn" href="#/playgrounds/${esc(x.id)}" aria-label="Open ${esc(x.name)}">Open</a>`
      : x.up ? openButton(x.links, `${kind}:${x.id}`, { text: true }) : "";
    // Add-ons and playgrounds have settings, setups none: a row without keeps the slot of the key, as it keeps the
    // menu's when that has nothing to offer
    const stuck = startWarning(x);
    return `${open}${stuck ? stuck.html : ""}${itemButton(x)}
      <span class="row-icons">${fileButtons(x, kind !== "setup")}
        ${more(x).length ? `<button class="icon-btn" data-more data-tip="More" aria-label="${esc(`More for ${x.name}`)}" aria-haspopup="true">${icon("more")}</button>` : GAP}</span>`;
  }

  // Five cells, in the order of the header. After the name its star (a hollow one shows on hover, in its place: nothing moves),
  // then what it is: whether it is personal, where else it deploys. Under the name, what it is, one muted line. Then its State: the
  // state component and what the server says of the row as a badge (why it cannot start, what brought it, what its start stops or
  // turns off: noteBadge), as the Dashboard says them; its category and its actions. The toggle is named for the item and says
  // whether its detail is open (drawRows).
  function row(x) {
    return `<button type="button" class="s-toggle" data-toggle aria-label="${esc(x.name)}" aria-controls="detail-${esc(x.id.replace(/\W/g, "-"))}" aria-keyshortcuts="S">${icon("chevron")}</button>
      <span class="list-main">
        <strong class="list-name" data-tip="${esc(x.name)}" data-tip-cut>${esc(x.name)}</strong>
        <span class="list-marks">${starButton(x, true)}${x.personal ? `<span class="pill">Personal</span>` : ""}${deploys(x) ? cloudMark(x) : ""}</span>
        <span class="list-sub"><span class="list-desc" data-tip="${esc(x.description)}" data-tip-cut>${esc(x.description)}</span></span></span>
      <span class="list-state">${itemState(x)}${startWarning(x)?.own ? "" : noteBadge(x)}</span>
      <span class="list-cat">${esc(x.category)}</span>
      <span class="list-actions">${actions(x)}</span>`;
  }

  // The items the filters keep, in the server's order: by name
  function kept() {
    const q = filters.q.trim().toLowerCase(), narrowed = catalogs && new Set(catalogs.items());
    return categories.items().filter((x) => (!narrowed || narrowed.has(x)) && (!filters.running || alive(x)) && (!filters.starred || x.starred)
      && words(x).includes(q));
  }

  // The control that tells the catalogs apart, mounted when the items need one and taken off when they no longer do: it is drawn from the
  // items, never from the page's markup, and what was chosen with it is not kept. A catalog asked for from outside (selectCatalog) is chosen.
  function mountScope(all) {
    const want = scopeOf(all);
    if (want !== scopeKind) {
      scopeEl?.remove();
      catalogs?.clear();
      scopeEl = catalogs = null;
      scopeKind = want;
      if (want) {
        const holder = document.createElement("div");
        holder.innerHTML = `${PICKER}${icon("chevron")}</button>`;
        scopeEl = holder.firstElementChild;
        $(".list-filters", section).append(scopeEl);
        catalogs = CategoryFilter(scopeEl, () => store.state?.[`${kind}s`] || [], () => filter({}), sourceName,
          { get: sourceOf, order: sourceOrder, label: { none: "All catalogs", all: "All catalogs", many: (n) => `${n} catalogs`, few: 1 } });
      }
    }
    if (wanted) {
      if (catalogs) catalogs.chosen = wanted;
      wanted = null;
    }
  }

  // What the catalogs say, in place of the rows when there is no item at all and as one line above them when there are (this machine's):
  // written again only when it changed, and never in the place of the rows that are there
  function paintContent(html) {
    if (html === contentHtml) return;
    contentHtml = html;
    contentEl?.remove();
    contentEl = null;
    if (!html) return;
    const holder = document.createElement("div");
    holder.innerHTML = html;
    contentEl = holder.firstElementChild;
    emptyEl.parentElement.insertBefore(contentEl, contentEl.hasAttribute("data-line") ? rowsEl : emptyEl);
  }

  function draw(state) {
    const all = state[`${kind}s`];
    mountScope(all);
    let shown = kept();
    // The row an address names shows, whatever the filters hid, on the page of the list that holds it. One that is not there yet while the
    // catalogs download is waited for: the address stays, and the row opens when it arrives (not one of this machine, which is listed at once)
    const known = !!target && all.some((x) => x.id === target), waiting = !!target && !known && maybeComing(state, target);
    if (known && !shown.some((x) => x.id === target)) {
      clear();
      shown = kept();
    }
    if (target && !waiting) pager.reveal(Math.max(0, shown.findIndex((x) => x.id === target)));
    $("[data-running]", section).checked = filters.running;
    $("[data-starred]", section).checked = filters.starred;
    // With no item at all there is nothing to filter, and the catalogs' content says why (it says "No setup yet" itself when it is all there)
    $(".list-tools", section).hidden = !all.length;
    paintContent(contentStatus(state.content ? state : { content: { key: "ready" } }, { kind, items: all.length }));
    emptyEl.hidden = shown.length > 0 || !all.length;
    setHTML(emptyEl, emptyHtml({ filtered: true, starred: filters.starred && !all.some((x) => x.starred),
      running: filters.running && !all.some(alive) }));
    const sub = (x) => (id) => opened.has(`${x.id}>${id}`);
    drawRows(rowsEl, pager.slice(shown), (x) => ({ row: row(x), detail: opened.has(x.id) ? detail(x, sub(x)) : "" }));
    paintActions(section);
    if (known) reveal(target);
    else if (target && !waiting) {
      // Given up: it is not there, and it is not coming
      opened.delete(target);
      routed.delete(target);
    }
    if (!waiting) target = null;
  }

  // The row an address opened, in view and focused, once the router has set the page's scroll
  function reveal(id) {
    requestAnimationFrame(() => {
      const li = $$(".list-item", section).find((one) => one.dataset.key === id);
      if (!li) return;   // drawn again meanwhile, without it
      li.scrollIntoView({ block: "nearest" });
      $(".s-toggle", li)?.focus({ preventScroll: true });
    });
  }

  // Filtering, paging and opening a row draw again from the last state
  const redraw = () => store.state && draw(store.state);

  // Every filter off, its controls too
  function clear() {
    $("[data-filter]", section).value = "";
    categories.clear();
    catalogs?.clear();
    Object.assign(filters, NO_FILTER);
  }

  function filter(changes) {
    Object.assign(filters, changes);
    before = null;
    pager.reset();
    redraw();
  }

  // The list as an address found it, given back once
  function restore() {
    if (!before) return;
    Object.assign(filters, before.filters);
    $("[data-filter]", section).value = filters.q;
    categories.chosen = before.categories;
    if (catalogs && before.catalogs) catalogs.chosen = before.catalogs;   // the catalogs it was narrowed to, if the picker is still there
    pager.page = before.page;
    before = null;
  }

  function toggle(id) {
    if (!opened.delete(id)) opened.add(id);
    redraw();
  }

  // --- Actions ---------------------------------------------------------------------

  // Why the item of the catalog cannot run on this machine, in the words the server refuses with (contribute.use_catalog_version): what its
  // verdict says, the catalogs it lacks, a level this Lab does not read. "" when it can. The personal copy is the one that works until then.
  function cannotRun(other) {
    const why = [...(other?.blocked?.reasons || [])], lacks = (other?.needs_catalogs || []).map((need) => need.name || need.id);
    if (lacks.length) why.push(`Needs ${lacks.join(", ")}`);
    if (other?.compat === "new") why.push("Needs a newer Lab");
    else if (other?.compat === "old") why.push("Was written for an older Lab");
    return why.join("; ");
  }

  // The item of this machine that a catalog now holds too (the same name and services): its copy goes and the catalog's is used, with the
  // settings and what it created. Held while either runs, as the server holds it, and refused while the catalog's cannot run here (it
  // is blocked, needs a catalog, or needs another level of the Lab): the server says that first, so does this.
  function twin(x) {
    const other = store.item(kind, x.twin.id), wait = store.offline ? store.wait() : "";
    const why = wait || cannotRun(other) || (x.on || x.up ? `Turn off ${x.name} first` : other?.on || other?.up ? `Turn off ${other.name} first` : "");
    return { label: "Use the catalog version", disabled: !!why, reason: why, onSelect: () => useCatalogVersion(x) };
  }

  async function useCatalogVersion(x) {
    const rows = [{ label: "Deletes", items: ["Your copy"], tone: "danger" }, { label: "Keeps", items: ["Your settings and data"], note: true }];
    if (!(await effectsDialog({ title: "Use the catalog version", options: [{ rows }], action: () => "Use" }))) return;
    try {
      await busy(`${kind}:${x.id}`, "replace", api(`/api/${kind}s/${x.id}/use-catalog`, {}));
      toast("Replaced");   // the word of the same verb on the button ("Replacing"), and of Replace Cloud Token
    } catch (err) {
      toast(err.message, true);
    }
    store.refresh();
  }

  // Where an add-on or a setup runs: This machine or a place of a platform it can go to, each a choice of its own; one it cannot
  // go to says why. The one it runs on now says so.
  function runOn(x) {
    if (x.kind === "playground" || !deploys(x)) return [];
    const here = chosenPlace(x)?.id || MACHINE;
    const entry = (id, name, why = "") => ({ label: `Run on ${name}`, hint: here === id ? "Current" : "", disabled: !!why,
      reason: why, onSelect: () => { keepItemPlace(x, id); store.notify(); } });
    return [{ separator: true }, entry(MACHINE, THIS_MACHINE.name),
      ...placesOf().map((place) => entry(place.id, place.name, whyNot(x, place) || (goes(x, place) ? "" : "Cannot run there"))),
      { separator: true }];
  }

  function more(x) {
    // What the primary button waits for, Restart and Log in wait for too. While the Lab UI does not answer,
    // nothing is asked of it: what the server allowed last waits with the rest.
    const wait = store.wait(), offline = store.offline ? wait : "";
    const can = (action) => ({ disabled: !!offline || !action.enabled, reason: offline || action.reason });
    return [
      // The form is that of a setup: an add-on or a playground is changed in its file, and two copies of one could
      // not load together
      kind === "setup" && x.personal && { label: "Edit", onSelect: () => openCreate({ kind, mode: "edit", setup: x }) },
      kind === "setup" && { label: "Duplicate", onSelect: () => openCreate({ kind, mode: "duplicate", setup: x }) },
      // What is of this machine is shared to a catalog, as a copy, or deleted unless something holds it: the server says what
      x.personal && shareEntry(x),
      // The same item in a catalog, as the server finds it: its copy is dropped for the catalog's, once neither runs
      x.personal && x.twin && twin(x),
      ...runOn(x),
      x.services?.some(hasLogs) && { label: "Logs", onSelect: () => { selectLogs(x.services); router.go("#/logs"); } },
      // Its containers restart as they are, as from its group on the Stack: it stays on or off
      kind === "addon" && x.up && { label: "Restart", disabled: !!wait, reason: wait,
        onSelect: () => runJob(() => api(`/api/addons/${x.id}/containers/restart`, {}), { key: `addon:${x.id}`, verb: "restart" }),
      },
      // What starts until its health check passes can be stopped meanwhile, from here: its button says Starting
      x.state.key === "starting" && x.action.do === "stop" && { label: x.action.label, ...can(x.action), onSelect: () => actOn(x) },
      // What is on, or runs, and has Start (or Turn on) as its button keeps its Turn off, or its Stop, here
      // An item that is off and cannot be stopped (what is on of it is used by something else) is not offered it
      x.secondary && !(x.state.key === "off" && !x.secondary.enabled)
        && { label: x.secondary.label, ...can(x.secondary), onSelect: () => actOn(x, x.secondary) },
      // A registry refused its last pull for want of a login: logging in is what it takes. Its job is the item's: the
      // row's button says "Logging in" meanwhile
      x.login && { label: "Log in", disabled: !!wait, reason: wait,
        onSelect: () => runJob(() => api("/api/registry/login", { host: x.login, key: `${kind}:${x.id}` }),
          { key: `${kind}:${x.id}`, verb: "login" }) },
      x.failed_job && { label: "Show output", onSelect: () => showOutput(x.failed_job) },
      ...(x.personal ? [{ separator: true }, deleteEntry(x)] : []),
    ].filter(Boolean);
  }

  function init(el) {
    section = el;
    el.innerHTML = `
      <div class="page-head">
        <h1>${title}</h1>
        <div class="page-actions"><button class="btn" data-new>${icon("plus")}New ${noun.toLowerCase()}</button></div>
      </div>
      <div class="card list-card">
        <div class="list-tools">
          <label class="search grow">${icon("search")}<input type="search" data-filter="q" placeholder="Search…" aria-label="Search the ${title.toLowerCase()}" autocomplete="off"></label>
          <div class="list-filters">
            <button class="combo-btn" data-categories aria-haspopup="dialog"><span class="sr-only">Category: </span><span class="value"></span>${icon("chevron")}</button>
            <label class="toggle-chip"><input type="checkbox" data-running>Running</label>
            <label class="toggle-chip"><input type="checkbox" data-starred>Starred</label>
          </div>
        </div>
        <div class="list-head list-grid"><span></span><span>${noun}</span><span>State</span><span>Category</span><span></span></div>
        <ul class="list-rows"></ul>
        <div class="empty">${loading("Loading")}</div>
        <div class="pager" hidden></div>
      </div>`;
    rowsEl = $(".list-rows", el);
    emptyEl = $(".empty", el);
    // A new page is read from its top
    pager = Pager($(".pager", el), 25, () => { before = null; redraw(); scrollTo(0, 0); });
    categories = CategoryFilter($("[data-categories]", el), () => store.state?.[`${kind}s`] || [], () => filter({}));

    el.addEventListener("input", (e) => { if (e.target.dataset.filter) filter({ q: e.target.value }); });
    // Running and Starred are options of their own, which combine: Starred is kept for the next visit
    el.addEventListener("change", (e) => {
      const box = e.target;
      if ("running" in box.dataset) filter({ running: box.checked });
      else if ("starred" in box.dataset) {
        remember(STARRED, box.checked ? "1" : "");
        filter({ starred: box.checked });
      }
    });
    el.addEventListener("click", (e) => {
      const on = (selector) => e.target.closest(selector);
      const button = on("button"), id = on(".list-item")?.dataset.key, sub = on("[data-sub]");
      // A row opens and closes from anywhere on it but its controls and links, and from its toggle; so does a line of its detail,
      // whose toggle keeps the focus as it is drawn again
      if ((!button || "subToggle" in button.dataset) && !on("a") && sub) {
        toggle(`${id}>${sub.dataset.sub}`);
        if (button) $(`.list-item[data-key="${CSS.escape(id)}"] [data-sub="${CSS.escape(sub.dataset.sub)}"] .s-toggle`, section)?.focus();
        return;
      }
      if ((!button || "toggle" in button.dataset) && on(".list-row") && !on("a")) return toggle(id);
      if (!button || button.disabled || button.ariaDisabled === "true") return;
      const data = button.dataset, x = store.item(kind, id);
      if ("new" in data) { if (store.state) openCreate({ kind, mode: "new" }); }   // its form is drawn from the state
      else if ("clear" in data) {
        remember(STARRED, "");
        clear();
        filter({});
      } else if ("license" in data) License.show({ form: true });   // a license note: the form that takes a new one
      else if ("file" in data) openFile(data.file);
      else if ("settings" in data) openSettings(store.item(...splitKey(data.settings)));
      else if (!x) return;
      else if ("fix" in data) fix(x, data.fix);
      else if ("star" in data) toggleStar(x);
      else if ("open" in data) openMenu(button, consoles(store.item(...splitKey(data.open))));
      else if ("more" in data) openMenu(button, more(x));
      else if ("act" in data) actOn(x);
    });
    // S on a row's toggle turns its star, as its star does
    el.addEventListener("keydown", (e) => {
      if (!e.target.matches(".list-row > .s-toggle") || e.key.toLowerCase() !== "s" || e.metaKey || e.ctrlKey || e.altKey) return;
      const x = store.item(kind, e.target.closest(".list-item").dataset.key);
      if (!x) return;
      e.preventDefault();
      toggleStar(x);
    });
  }

  // An address that names an add-on or a setup opens its row, in view and focused. The rows that other addresses
  // opened close again, those opened by hand stay: the list's own address shows it as it was before, its filters and
  // its page too.
  function show([id]) {
    if (kind === "playground") store.refreshPlaces();   // what a row holds its Start for is in the places
    const again = routed.has(id);
    routed.forEach((key) => { if (key !== id) opened.delete(key); });
    routed.clear();
    if (!id) return restore();
    before ??= { filters: { ...filters }, categories: categories.chosen, catalogs: catalogs?.chosen, page: pager.page };
    if (again || !opened.has(id)) routed.add(id);
    opened.add(id);
    target = id;
  }

  // The list as it opens from outside, filtered to the catalogs `ids` (the Catalogs page: what a catalog holds), drawn at its next draw
  function select(ids) {
    wanted = ids;
  }

  // A playground's own address is its page
  const pattern = kind === "playground" ? /^#\/playgrounds$/ : new RegExp(`^#/${kind}s(?:/(${ID_PATTERN}))?$`);
  return { id: `${kind}s`, pattern, nav: `${kind}s`, title, init, show, select, render: draw };
}

export const playgroundsPage = list("playground", "Playgrounds", "Playground");
export const setupsPage = list("setup", "Setups", "Setup");
export const addonsPage = list("addon", "Add-ons", "Add-on");
const LISTS = { playground: playgroundsPage, setup: setupsPage, addon: addonsPage };

// Filters the list of a kind to the items of a catalog (by its id: "local" for this machine's) the next time it is drawn, as the Catalogs
// page leads to what a catalog holds
export const selectCatalog = (kind, id) => LISTS[kind]?.select([id]);
