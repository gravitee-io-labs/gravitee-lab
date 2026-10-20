// Dashboard: the page the Lab UI opens on. It answers its questions in the order a person asks them: is it all right (the head:
// the platform's state, what runs behind what is saved and why, and the one action that fits), then two cards side by side: what is
// in the Gravitee platform (a row for each product: its version, whether there is a newer one or something to see to, its consoles;
// API Management's row says where it runs and is where that is chosen; under them a row for Gravitee Cloud, how many hybrid gateways its
// accounts have) and what else is on (the playgrounds, setups and
// add-ons that run or are starred, with the search and the filters of the lists, so that it is also where one is found and
// started), and last what the lab's containers use of the machine (Resources: one line of figures, which unfolds into a strip of charts
// and what Docker stores). Every state is the store's, drawn live; the
// page reads for itself the versions, Docker's data and the Cloud, when it opens, when the lab moves and by Refresh, and listens to
// the load of the containers only while it is on screen. The load is drawn as a chart that slides: its path is moved by an animation
// frame from the clock, not redrawn by the sample. No link has an effect on the page it opens, and a click on a name acts on nothing.

import { $, $$, esc, icon, setHTML, loading, ago, api, store, router, hrefOf, recall, remember, splitKey } from "../core.js";
import { busy } from "../activity/jobs.js";
import { actionButton, paintActions, paintBusy, paintProgress, stateDot, noteHtml, noteBadge, dockerDown, onKnown, keyed,
  showing, starButton, toggleStar, openMenu, Pager, emptyHtml, tipHtml, cardHtml, tipAttrs, statusHtml, refocus, safeHref, applyBadge,
  contentStatus } from "../components/ui.js";
import { Combo } from "../components/combo.js";
import { platformHead, paintHead, onHeadClick } from "./stack.js";
import { itemButton, itemState, actOn, alive, words, startWarning } from "./list.js";
import { accountState } from "./platforms.js";
import { gatewayList, gatewayCount, environmentStatus, gatewayCell } from "../platform/environments.js";
import { connectAccount, perform } from "../platform/environment-actions.js";
import { coreOf, onCloud, movable, runsControl, environmentControl, chooseCore, pickEnvironment } from "../platform/core-choice.js";
import { KINDS as PLATFORMS, openReplaceToken } from "../dialogs/platform.js";
import { openSettings } from "../dialogs/editor.js";
import { License } from "../dialogs/license.js";

const SPAN = 60;      // seconds of load a chart shows, from the first to the last: its scale never changes
const FLOOR = 10;     // the percentage a quiet CPU is drawn against, so that its line is seen
const GAP = 5;        // seconds without a sample after which its line breaks: nothing was listening
const SILENT = 8;     // and after which its figure is a dash: the stream is not saying anything
const LAG = 1.5;      // seconds a chart runs behind the clock, so that its right edge is between two samples it knows
const EASE = 0.8;     // seconds a chart takes to follow its top edge down, when a high point leaves the window
const WIDTH = 240;    // a chart's width and height in its own units: it is drawn at 24 px high and as wide as its cell
const HEIGHT = 48;
const MARGIN = [5, 4];   // the room above and below the line, in the same units
const WAIT = 3000;    // milliseconds before the versions a registry is still listing are asked for again
const SOON = 1000;    // and before what Docker holds, which the server was still reading, is
const RETRY = 10000;  // and before a read that failed is tried again
const REOPEN = 3000;  // and before a stream that the browser gave up on is opened again, twice as long each time up to REOPEN_MAX
const REOPEN_MAX = 60000;
const FRESH = 30000;  // milliseconds a check of the Cloud is still new, for a tab that is seen again
const POLL = 5000;    // milliseconds before a gateway that runs here and has not connected yet is read again, twice as long each time
const POLLS = 5;      // and how many times: it is left as it is after about two and a half minutes
const ROWS = 8;       // the rows of the catalog a page holds
const CLOSED = 2;     // the readyState of a stream that the browser gave up on (EventSource.CLOSED)

// What the page draws and where: each is the element of its data-card. The cells of Resources are blocks of their own, so
// that a change of one leaves the others alone.
const BLOCKS = ["license", "count", "products", "cloud", "catalogs", "catalog", "summary", "cpu", "memory", "storage", "engine"];
// The three kinds of the catalog in the order of its lists, with the icon, the singular name and the plural of each
const KINDS = [["playground", "playgrounds", "flask", "Playground"], ["setup", "setups", "layers", "Setup"], ["addon", "add-ons", "addons", "Add-on"]];
const KIND_ICON = Object.fromEntries(KINDS.map(([kind, , symbol]) => [kind, symbol]));
const KIND_NAME = Object.fromEntries(KINDS.map(([kind, , , name]) => [kind, name]));
const CLOUD_NAME = "Gravitee Cloud";
// The charts: the column of the samples each draws, and its tile's name
const CHARTS = { cpu: 1, memory: 2 };
const WARN = 0.85;    // the share of Docker's memory above which the figure warns, and
const FULL = 0.95;    // the one above which it says there is none left: what a container killed for want of memory comes after
const HEAVY = 5;      // the containers a popover of the load lists
const STARRED = "list-starred:dashboard";   // starred is chosen once, as on the lists: it is the same at the next visit
const FOLD = "dash-resources";              // "1" while Resources is unfolded, in this browser: it is one line by default

let page = null;       // the section of the page
let data = null;       // the last /api/dashboard body
let failed = "";       // why it could not be read, in the server's words
let shown = false;     // the page is the one on screen
let source = null;     // the stream of the containers' load
let again = null;      // the timer of the next look at versions the server is still reading, or at a read that failed
let reopen = null;     // the timer of the next try at a stream that was closed
let tries = 0;         // the tries so far since a sample came: each waits twice as long
let lookout = null;    // the timer that notices a stream gone quiet
let live = [];         // the last minute of it: [seconds when it came, cpu_percent, memory_used]
let heavy = null;      // what the last sample said of each container, {top: [{name, cpu, memory}], memory}: the popovers of the load
let frame = 0;         // the animation frame asked for
let tops = {};         // chart -> the value of its top edge, which eases down
let spent = 0;         // the time of the frame before, for that
let seen = null;       // what the lab was when the numbers were read: when it moves, they are read again
let reading = null;    // the read on its way, and whether another is wanted once it ends
let rereads = false;
let platforms = null;  // the platforms registered, null until they are read
let cloudFailed = "";  // why they could not be read
let listing = null;    // the read of them on its way
// What each platform's last check answered and when, in milliseconds: the Gravitee Cloud page's too (store.cloud), so that an answer either
// page read, and what an action on that page changed, is what both say
const { checks, at: checked } = store.cloud;
const checking = new Map();  // platform id -> its check on its way
const asked = new Set();     // the ids whose check a person asked for again: the row says it is checking
let watch = null;      // the timer of the next look at a gateway that has not connected
let polls = 0;         // the looks at it so far
// What the catalog is filtered by, as the lists are, and kept as they keep it: the search, the kind ("" for all), Running and Starred
const filters = { q: "", kind: "", running: false, starred: recall(STARRED) === "1" };
let pager = null;      // the catalog's pages
let unfolded = recall(FOLD) === "1";   // Resources shows its strip, not its line

// --- Words ------------------------------------------------------------------------------

const NONE = '<span class="muted">—</span>';
const SPIN = '<span class="dash-wait" role="img" aria-label="Loading"><span class="spinner"></span></span>';
const known = (value) => value !== null && value !== undefined;
// Docker does not answer, said by the state (live) or by the last read
const down = () => !!(store.state?.docker.error || data?.docker.error);
const stamp = () => Date.now() / 1000;
// A person who asked the system not to animate gets a chart drawn when a sample comes
const motion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
const still = () => !!motion?.matches;

// A number of gigabytes as a person reads it, one decimal. The memory counts in binary gigabytes, as the system and
// Docker's settings say it; the disk and Docker's data in decimal ones, as Finder and `docker system df` do.
const gb = (bytes, binary = false) => (bytes / (binary ? 2 ** 30 : 1e9)).toFixed(1);
// Bytes with their unit; MB below one GB, kB below one MB
function size(bytes, binary = false) {
  if (!known(bytes)) return "—";
  const [kilo, mega, giga] = binary ? [2 ** 10, 2 ** 20, 2 ** 30] : [1e3, 1e6, 1e9];
  if (bytes >= giga) return `${gb(bytes, binary)} GB`;
  return bytes >= mega ? `${Math.round(bytes / mega)} MB` : bytes >= kilo ? `${Math.round(bytes / kilo)} kB` : `${bytes} B`;
}
// What a machine has in all, as a round number: "36 GB"
const whole = (bytes, binary = false) => (known(bytes) ? `${Math.round(bytes / (binary ? 2 ** 30 : 1e9))} GB` : "—");

const percent = (value) => (known(value) ? `${value < 10 ? value.toFixed(1) : Math.round(value)}%` : "—");

// The rows of a block, one element each, kept from one draw to the next (keyed): a row that changes writes itself, and the
// others keep the hover, the focus and an open menu. Each is {key, cls, html}.
function drawRows(el, rows) {
  keyed(el, rows.map((row) => row.key), () => document.createElement("div")).forEach((li, i) => {
    li.className = rows[i].cls;
    setHTML(li, rows[i].html);
  });
}

// --- Versions ---------------------------------------------------------------------------

const version = (value, muted = false) => (value ? `<span class="s-version${muted ? " muted" : ""}">${esc(value)}</span>` : NONE);

// A version against the latest of its channel, as the state component: "Update available" in amber when a newer one is out (its
// popover names it and where it is chosen), "Up to date", muted, when what runs is the newest of its channel (its popover which channel);
// nothing when it cannot be told, a spinner while the registry is being asked. The channel is the version's own: a pre-release is
// compared with the latest pre-release.
function updateCell(product, asking = false) {
  const { status, latest, deployed, channel } = product || {};
  if (status === "unknown" || !latest) return `<span class="dash-update">${asking ? SPIN : ""}</span>`;
  const of = channel === "pre-release" ? "pre-release" : "release";
  if (status === "latest") return statusHtml("done", "Up to date", { quiet: true, tip: ["Up to date", `${deployed} is the latest ${of}`] });
  return statusHtml("warn", "Update available", { tip: { title: "Update available", columns: ["Running", `Latest ${of}`],
    rows: [{ lines: [[{ versions: { running: deployed, saved: latest } }]] }], foot: "Choose it on the Images page." } });
}

// --- Products ---------------------------------------------------------------------------

// How many containers the platform has, and the page that lists them and holds their actions; and the page of the versions. Without
// Docker nothing is known of the count. Their words stay at every width: a card too narrow for them puts them under its title.
function toolsBody({ platform }) {
  const { total } = platform.containers, count = !dockerDown() && total ? total : "";
  return `<a class="btn" href="#/containers"${count ? ` aria-label="Containers, ${count}"` : ""}>Containers${count ? `<span class="muted">${count}</span>` : ""}</a>
    <a class="btn" href="#/images">Change versions</a>`;
}

// How many catalogs the Lab has, and the page that lists them and holds their actions. Without any, the page is where one is added.
function catalogsBody({ catalogs }) {
  const count = (Array.isArray(catalogs) ? catalogs : []).filter((c) => ["git", "folder"].includes(c.kind)).length;
  return `<a class="btn" href="#/catalogs"${count ? ` aria-label="Catalogs, ${count}"` : ""}>Catalogs${count ? `<span class="muted">${count}</span>` : ""}</a>`;
}

// A product some of whose containers do not run says how many do, in amber; nothing else is said of how many run
function warning(unit) {
  const { running, total } = unit.containers;
  return !dockerDown() && unit.state.key === "partly" && total ? `<span class="list-note warn">${running} of ${total} running</span>` : "";
}

// The Status of a product, the first that holds: an issue (its culprit, in red) or a license it lacks (which opens the form that
// takes one), how many of its containers run, what runs behind what is saved (the badge "Apply needed", its causes in its popover,
// so that the version beside it is read for what it is, and a newer version when one is out, which the badge stands in front of);
// else whether a newer version is out. What only informs (a service that stopped) is for the Containers page.
function statusOf(unit, product, asking) {
  if (unit.note_tone === "err" || unit.note_file) return noteHtml(unit);
  const partly = warning(unit);
  if (partly) return partly;
  if (unit.note_apply && unit.note) {
    const newer = product?.status === "newer" && product.latest ? [[{ lead: "Update available" }, { code: product.latest }]] : [];
    return applyBadge(unit.note_title || unit.note, "pill warn", unit.note_causes, newer);
  }
  return updateCell(product, asking);
}

// The consoles of a product that run, as one kind of control. A card with room has a button for each, in the order of the state's
// links, and the older ones (the `legacy` of the link) together in one More menu. A narrower card has one Open button: the console
// itself when there is one, else the menu of them all, so that a product with one console and a product with several look alike.
const linksOf = (unit, state) => state.links.filter((link) => link.up && unit.services.includes(link.service));
function consolesCell(unit, state) {
  const mine = linksOf(unit, state), main = mine.filter((link) => !link.legacy), older = mine.length - main.length;
  if (!mine.length) return "";
  const open = (link, label = link.label) => `<a class="btn" href="${esc(safeHref(link.url))}" target="_blank" rel="noopener" data-tip="Open ${esc(link.label)}" aria-label="Open ${esc(link.label)}">${icon("external")}<span class="dash-clip">${esc(label)}</span></a>`;
  const more = older ? `<button class="btn" data-consoles="older:${esc(unit.id)}" aria-haspopup="true" aria-label="More, consoles of ${esc(unit.name)}">More${icon("chevron")}</button>` : "";
  const all = mine.length === 1 ? open(mine[0], "Open")
    : `<button class="btn" data-consoles="all:${esc(unit.id)}" aria-haspopup="true" aria-label="Open, consoles of ${esc(unit.name)}">Open${icon("chevron")}</button>`;
  return `<span class="dash-full">${main.map((link) => open(link)).join("")}${more}</span><span class="dash-compact">${all}</span>`;
}

// The dot of a product's state, with its name for a screen reader
const dotOf = (unit) => `<span class="dash-dot" role="img" aria-label="${esc(dockerDown() ? "State not known" : unit.state.label || "Off")}">${stateDot(unit.state)}</span>`;

// A product's name, a heading, cut with its tooltip when it must be; on the row of the product that can run in the Cloud, the choice of where
// it runs after it (runsControl)
const nameOf = (unit) => `<strong class="list-name" role="heading" aria-level="3" data-tip="${esc(unit.name)}" data-tip-cut>${esc(unit.name)}</strong>${
  unit.id === movable()?.id ? runsControl() : ""}`;

// A product of the platform, in one row: its state as a dot and its name, the version it runs and its channel, what to see to or
// whether there is a newer version, the consoles. Until the numbers are read nothing says what runs: the version saved is not it.
function productRow(unit, state) {
  const product = data?.products.find((found) => found.id === unit.id);
  const runs = product?.deployed_state === "running";
  const text = product ? version(product.deployed, !runs) : data || failed ? NONE : SPIN;
  const asking = !failed && !!data && !!product && data.pending;
  return { key: `product:${unit.id}`, cls: "dash-row dash-product", html: `
    <span class="dash-name">${dotOf(unit)}${nameOf(unit)}</span>
    <span class="dash-ver"><span class="sr-only">Version </span>${text}</span>
    <span class="dash-upd"><span class="sr-only">Status </span>${statusOf(unit, product, asking)}</span>
    <span class="dash-open">${consolesCell(unit, state)}</span>` };
}

// A product is a group that has a version: the databases are not one, and the Containers page has them. A platform without one is a
// file that does not load: the banner says why. With API Management in the Cloud, its row is its environment there, and under it its
// services of this machine, not used, though what of them still runs says so with its dot; the other products' rows are as they are.
function productsBody(state) {
  const units = state.platform.groups.filter((unit) => unit.version || data?.products.some((found) => found.id === unit.id));
  if (!units.length) return [{ key: "none", cls: "empty dash-empty", html: "<span>No product</span>" }];
  const moved = onCloud() ? movable()?.id : null;
  return units.flatMap((unit) => (unit.id === moved ? [cloudProductRow(unit), localRow(unit, state)] : [productRow(unit, state)]));
}

// --- With API Management in the Cloud ---------------------------------------------------

// The product in the Cloud: the environment it runs in, where its version would be (a picker of the linked ones when there are several),
// and the state of its gateway on this machine, as the Gravitee Cloud page says it; then the one thing to do that is not a start (Start
// all starts the gateway), else its console. Until the account is read, it says it is loading; one that cannot be read says why.
function cloudProductRow(unit) {
  const core = coreOf(), p = platforms?.find((found) => found.id === core.platform.id), check = p && checks.get(p.id);
  const env = check?.environments?.find((found) => found.id === core.environment.id);
  const st = env ? environmentStatus(p, check, env) : null;
  let status = loading("Loading"), dot = "", word = "State not known", open = "";
  if (st) {
    ({ dot, word } = st.gateway);
    status = gatewayCell(st.gateway);
    const act = st.action && !["start-gateway", "stop-gateway"].includes(st.action.do) ? st.action : null;
    open = act ? `<button class="btn" data-core-do="${esc(act.do)}">${esc(act.label)}</button>`
      : `<a class="btn" href="${esc(safeHref(p.url))}" target="_blank" rel="noopener" aria-label="Open console, ${esc(core.name)}">${icon("external")}<span class="dash-clip">Open console</span></a>`;
  } else if (check && check.state !== "ok") {
    const [tone, said] = accountState(check);
    status = stated(tone === "err" ? "issue" : tone, said, check.message);
  } else if (cloudFailed && !platforms) status = stated("issue", "Accounts cannot be read", cloudFailed);
  const where = environmentControl({ short: true }) || `<span class="dash-account" data-tip="${esc(core.name)}">${esc(core.environment.name)}</span>`;
  return { key: `product:${unit.id}`, cls: "dash-row dash-product", html: `
    <span class="dash-name"><span class="dash-dot" role="img" aria-label="${esc(word)}"><span class="dot-status ${esc(dot)}"></span></span>${nameOf(unit)}</span>
    <span class="dash-ver"><span class="sr-only">Gravitee Cloud </span>${where}</span>
    <span class="dash-upd"><span class="sr-only">Gateway </span>${status}</span>
    <span class="dash-open">${open}</span>` };
}

// Under it, its services of this machine, which the Cloud choice does not use: This machine, muted, with whether they run as its dot, its
// version as saved, Not used; its consoles while it still runs
function localRow(unit, state) {
  const product = data?.products.find((found) => found.id === unit.id);
  const text = product?.deployed ? version(product.deployed, true) : unit.version ? version(unit.version, true) : NONE;
  const status = statusHtml("off", "Not used", { quiet: true, tip: ["Not used", `${unit.name} runs in Gravitee Cloud: Start all does not start it here`] });
  return { key: `local:${unit.id}`, cls: "dash-row dash-product dash-idle dash-local", html: `
    <span class="dash-name">${dotOf(unit)}<span class="list-name">This machine<span class="sr-only">, ${esc(unit.name)}</span></span></span>
    <span class="dash-ver"><span class="sr-only">Version </span>${text}</span>
    <span class="dash-upd"><span class="sr-only">Status </span>${status}</span>
    <span class="dash-open">${consolesCell(unit, state)}</span>` };
}

// --- Gravitee Cloud ---------------------------------------------------------------------

const kindOf = (p) => PLATFORMS[p.kind] || Object.values(PLATFORMS)[0];
const account = (p) => (p.name === kindOf(p).name ? "" : p.name);

// A state whose reason is its popover: the word, then the server's sentence
const stated = (dot, word, why) => statusHtml(dot, word, { tip: why ? [word, why] : [] });
// A word with no dot, muted: what is not a state ("No account connected")
const said = (word) => `<span class="muted">${esc(word)}</span>`;

// The popover of the count: the gateways under their environment (and its account, when there are several: "Development (My Account)"),
// each with its state in the words of the Gravitee Cloud page
function gatewaysCard(found, several) {
  const rows = [];
  for (const { p, g } of found) {
    const where = several ? (g.envName ? `${g.envName} (${p.name})` : p.name) : g.envName;
    let row = rows.find((one) => one.subject === where);
    if (!row) rows.push(row = { subject: where, lines: [] });
    row.lines.push([{ who: g.name }, g.st.word]);
  }
  return { title: "Hybrid gateways", rows };
}

// How many hybrid gateways the accounts have, as words that lead to the Gravitee Cloud page; hovered or focused, which and in what state
function countLink(found, several) {
  const tip = found.length ? tipAttrs("", gatewaysCard(found, several)) : "";
  return `<a class="name-link dash-count" href="#/cloud"${tip}>${gatewayCount(found.length)}</a>`;
}

// Gravitee Cloud, one row of the platform's card on the grid of its products: its name, the accounts connected (muted), how many hybrid
// gateways they have, which leads to the Gravitee Cloud page. What keeps it from saying so is said in its place, with what changes it: no
// account (and Connect account; that a hybrid gateway runs here when one does, so that the two never contradict), an account that
// cannot be read (Replace Account Token, Check again), accounts that cannot be listed, one not read yet.
function cloudRow(state) {
  const row = (accounts, what, action = "") => ({ key: "cloud", cls: "dash-row dash-cloud", html: `
    <span class="dash-name"><strong class="list-name" role="heading" aria-level="3">${CLOUD_NAME}</strong></span>
    <span class="dash-ver">${accounts}</span>
    <span class="dash-upd">${what}</span>
    <span class="dash-open">${action}</span>` });
  if (cloudFailed && !platforms) {
    return row("", stated("issue", "Accounts cannot be read", cloudFailed), listing ? loading("Checking") : '<button class="btn" data-reload>Check again</button>');
  }
  if (!platforms) return row("", loading("Loading"));
  if (!platforms.length) {
    const here = !dockerDown() && state.addons.some((addon) => addon.gateway && addon.up);
    return row("", said(here ? "A hybrid gateway runs here, with no account connected" : "No account connected"),
      `<button class="btn" data-connect>${icon("plus")}Connect account</button>`);
  }
  const names = platforms.map(account).filter(Boolean).join(", ");
  const accounts = names ? `<span class="dash-account" data-tip="${esc(names)}" data-tip-cut>${esc(names)}</span>` : "";
  const wrong = platforms.find((p) => checks.has(p.id) && checks.get(p.id).state !== "ok");
  if (wrong) {
    const check = checks.get(wrong.id), [tone, word] = accountState(check);
    const action = asked.has(wrong.id) ? loading("Checking") : check.state === "invalid"
      ? `<button class="btn" data-account="${esc(wrong.id)}">Replace ${esc(kindOf(wrong).token)}</button>` : `<button class="btn" data-recheck="${esc(wrong.id)}">Check again</button>`;
    const why = platforms.length > 1 && check.message ? `${wrong.name}: ${check.message}` : check.message;
    return row(accounts, stated(tone === "err" ? "issue" : tone, word, why), action);
  }
  if (platforms.some((p) => !checks.has(p.id))) return row(accounts, loading("Loading"));
  const found = platforms.flatMap((p) => gatewayList(p, checks.get(p.id)).map((g) => ({ p, g })));
  return row(accounts, countLink(found, platforms.length > 1));
}

// --- Catalog ----------------------------------------------------------------------------

// What is on now: known from the files while only Docker does not answer, and a row stays while its button has not said how its
// action went
const isOn = (x) => (onKnown() && (x.on || x.up)) || showing(`${x.kind}:${x.id}`);

// The three kinds of the catalog, the one chosen only when one is
const catalog = (state) => [...state.playgrounds, ...state.setups, ...state.addons].filter((x) => !filters.kind || x.kind === filters.kind);

// What the card lists, of the kind chosen: what is on and what is starred; Running and Starred, as the lists have them, narrow
// that to what runs and to what is starred; and a search looks through the whole catalog, as the lists' search does. What is on
// comes first, then a playground, the setups and the add-ons, each by name.
function catalogItems(state) {
  const q = filters.q.trim().toLowerCase(), narrowed = filters.running || filters.starred;
  const found = catalog(state).filter((x) => (!filters.running || alive(x) || showing(`${x.kind}:${x.id}`)) && (!filters.starred || x.starred)
    && (q || narrowed || isOn(x) || x.starred) && words(x).includes(q));
  return [...found.filter(isOn), ...found.filter((x) => !isOn(x))];
}

// An item of the catalog, in the anatomy of a list's row on two lines: its kind, its name that leads to it with its star, and under it
// its state (the lists' State) and its badges (Personal, or the catalog it comes from, and what its list says of it: why it cannot start, that it was started
// outside the Lab UI, what runs behind what is saved, as the list's note badges); then three slots of a fixed width, which keep their
// place when what they hold is absent: the warning of what its Start needs or does (as on the lists, just before it), its Start or Stop,
// the lists' own button on its own key, and its Settings. Its consoles are on its own page and in its list.
function itemRow(x) {
  const edit = x.settings?.length ? `<button class="icon-btn" data-settings="${x.kind}:${esc(x.id)}" data-tip="Settings" aria-label="${esc(`Settings ${x.name}`)}">${icon("key")}</button>` : "";
  const stuck = startWarning(x);   // what is in the way of its Start, or what it does, is the warning just before it
  const marks = `${x.personal ? '<span class="pill">Personal</span>' : ""}${stuck?.own ? "" : noteBadge(x)}`;
  return {
    key: `${x.kind}:${x.id}`, cls: "dash-row dash-item", html: `
      <span class="dash-kind" role="img" aria-label="${KIND_NAME[x.kind]}" data-tip="${KIND_NAME[x.kind]}">${icon(KIND_ICON[x.kind])}</span>
      <span class="dash-nameline"><a class="name-link list-name" href="${esc(hrefOf(x))}" data-tip="${esc(x.name)}" data-tip-cut>${esc(x.name)}</a>${x.starred ? starButton(x) : ""}</span>
      <span class="dash-sub-line">${itemState(x)}${marks ? `<span class="list-marks dash-marks">${marks}</span>` : ""}</span>
      <span class="dash-slot dash-warn">${stuck ? stuck.html : ""}</span>
      <span class="dash-slot dash-act">${itemButton(x)}</span>
      <span class="dash-slot dash-set">${edit}</span>`,
  };
}

// Nothing is on and nothing starred: a way to a playground, when there is one. While what is on is not known, why.
function emptyRow(state) {
  const text = onKnown() ? "Nothing running or starred" : state.wait;
  return { key: "empty", cls: "empty dash-empty", html: `<span>${esc(text)}</span>${onKnown() && state.playgrounds.length
    ? '<a class="btn" href="#/playgrounds">Browse playgrounds</a>' : ""}` };
}

// A search or a filter that finds nothing, in the words of the lists: "Nothing running", "Nothing starred" when that choice
// alone empties it, else "No matches" and the way to clear the filters
function noMatch(state) {
  const all = catalog(state);
  return { key: "nomatch", cls: "empty dash-empty", html: emptyHtml({ filtered: true, starred: filters.starred && !all.some((x) => x.starred),
    running: filters.running && !all.some(alive) }) };
}

// The page of the catalog that is shown. With no search and no filter and nothing to list, a way to a playground; else that
// nothing matches. Before all that, what the catalogs say while their content is on its way or missing: in place of everything, with no item
// at all (nothing to search, then), and as one line above the rows of the items there are (this machine's).
function catalogBody(state) {
  const total = state.playgrounds.length + state.setups.length + state.addons.length;
  $(".list-tools", page).hidden = !total;
  const said = contentStatus(state.content ? state : { content: { key: "ready" } }, { kind: "", items: total });
  const status = said ? [{ key: "content", cls: "", html: said }] : [];
  if (!total) {
    pager.slice([]);   // the pages of what was there are gone with it: the pager must not keep their buttons
    return status;
  }
  const rows = pager.slice(catalogItems(state)).map(itemRow);
  if (rows.length) return [...status, ...rows];
  return [...status, filters.q.trim() || filters.kind || filters.running || filters.starred ? noMatch(state) : emptyRow(state)];
}

// --- The catalog's filters --------------------------------------------------------------

const KIND_CHOICES = [["", "All kinds"], ...KINDS.map(([kind, plural]) => [kind, plural[0].toUpperCase() + plural.slice(1)])];

// The picker says its choice and the two toggle chips theirs
function paintFilters() {
  $("[data-kind] .value", page).textContent = KIND_CHOICES.find(([value]) => value === filters.kind)[1];
  $("[data-running]", page).checked = filters.running;
  $("[data-starred]", page).checked = filters.starred;
}

// A picker of the kinds, each with how many there are
function chooseKind(button) {
  const count = (kind) => (kind && store.state ? String(store.state[`${kind}s`].length) : "");
  Combo.open(button, {
    plain: true, current: filters.kind,
    groups: () => [{ items: KIND_CHOICES.map(([value, label]) => ({ value, name: label, meta: count(value) })) }],
    choose(value) {
      filters.kind = value;
      filter();
    },
  });
}

// The catalog is drawn again from its first page
function filter() {
  pager.reset();
  paintFilters();
  draw(["catalog"]);
}

// --- Resources --------------------------------------------------------------------------

// What the lab's containers use of the machine, on one row: the load, the memory, then what Docker stores. Each cell is a block of
// its own, so that a change of one leaves the others alone.

// A chart: two guides, the top of its scale and zero, the line and a dot at its right edge, moved by `paintCharts`, and beside it the
// numbers of its scale: what its top is, written as it changes (`paintAxis`), and zero. Its tooltip says what the line covers. Its clip has
// the id of the cell, so that two charts never share one.
const chart = (id) => `<span class="dash-plot" data-plot="${id}" data-tip="${esc(scaleSaid(axisTop(id)))}"><span class="dash-axis" aria-hidden="true"><span data-axis="${id}">${axisTop(id)}</span><span>0</span></span>
  <svg class="dash-chart" data-chart="${id}" viewBox="0 0 ${WIDTH} ${HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
  <defs><clipPath id="dash-clip-${id}"><rect x="0" y="-10" width="${WIDTH}" height="${HEIGHT + 20}"/></clipPath></defs>
  <path class="chart-guide" d="M0,${MARGIN[0]}H${WIDTH}M0,${HEIGHT - MARGIN[1]}H${WIDTH}"/>
  <g clip-path="url(#dash-clip-${id})"><path class="chart-line"/></g><path class="chart-dot"/></svg></span>`;

// What a chart's tooltip says: the time it covers, and what it is drawn against
const scaleSaid = (top) => `Last ${SPAN} seconds${top ? `, 0 to ${top}` : ""}`;

// What the top of a scale says: the memory Docker has, the percentage a quiet CPU is drawn against until a higher point needs more
const axisTop = (id, top = null) => (id === "cpu" ? `${Math.round(top ?? FLOOR)}%` : (top ?? data?.docker?.memory) ? `${gb(top ?? data.docker.memory, true)} GB` : "");

// A figure: its value (a live one is written apart, `id`, so that a sample changes a word and leaves the chart in place), then what it
// is of, muted, on its baseline: the same markup in every cell
const figure = (text, of = "", id = "") => `<span class="dash-figure"><span class="dash-value"${id ? ` data-value="${id}"` : ""}>${text}</span>${
  of ? ` <span class="dash-of muted">${of}</span>` : ""}</span>`;

// A cell: its label, which says which containers weigh most when there is a load to say it of (its popover is written by
// `paintValues`, as the load comes), its live figure and what it is a share of, its chart
const meter = (label, id, of) => {
  const mark = `<span class="dash-mark" aria-hidden="true">${icon("info")}</span>`;
  const named = data && !down() ? `<span class="dash-weigh" data-top="${id}">${tipHtml(`${label}${mark}`, { title: "Heaviest containers" }, "", label, { short: true })}</span>` : label;
  return `<span class="dash-label">${named}</span>${figure("—", data && !down() ? `of ${of}` : "", id)}${data && !down() ? chart(id) : ""}`;
};

// The load of the containers as a share of the cores Docker has, against the larger of FLOOR percent and its highest point.
// Without Docker there is no load to say.
function cpuBody() {
  const cores = data ? data.docker.cpus ?? data.machine.cpus : 0;
  return meter("CPU", "cpu", `${cores} ${cores === 1 ? "core" : "cores"}`);
}

// The memory the containers use, against the memory Docker has
function memoryBody() {
  const docker = data?.docker;
  return meter("Memory", "memory", docker && known(docker.memory) ? `${gb(docker.memory, true)} GB` : whole(data?.machine.memory_total, true));
}

// Storage's button counts what Docker stores again (`docker system df`, and Docker's versions with it); its tooltip says how old the count is
function refreshButton(kept) {
  const age = kept?.read_at ? ago(typeof kept.read_at === "number" ? kept.read_at * 1000 : kept.read_at) : "";
  return actionButton({ key: "dashboard", verb: "refresh", icon: "restart", cls: "icon-btn",
    attrs: `data-refresh data-tip="Count again${age ? `, counted ${age} ago` : ""}" aria-label="Count storage again"` });
}

// What `docker system df` counts, in the order it lists them: the key of its answer and the name a person reads
const TYPES = [["images", "Images"], ["containers", "Containers"], ["volumes", "Volumes"], ["cache", "Build cache"]];

// A type's size and what of it no container uses, in the same words in its part's popover and in the popover of what is unused
const usage = (bytes, unused) => `${size(bytes)}, ${unused ? `${size(unused)} unused` : "all in use"}`;

// What Docker stores by type, each with what no container uses of it, as `docker system df` says it, and what its tooltip says: its
// name as a title, its size and how much of it is unused (volumes add that they can hold data). A type that takes nothing has no part.
function diskParts(kept) {
  return TYPES.map(([key, name]) => {
    const bytes = kept[key]?.size || 0, unused = kept[key]?.reclaimable || 0;
    return { key, name, bytes, unused, lines: [name, usage(bytes, unused),
      ...(key === "volumes" && unused ? ["A volume can hold data: removing one loses it."] : [])] };
  }).filter((part) => part.bytes > 0);
}

// What Docker stores (images, containers, volumes and build cache, the total of `docker system df`) as the figure, then how much of it
// no container uses (its popover: how much of each type) and Refresh. A line, not a chart: what Docker stores is
// slow to change and seldom what a person came for, so it takes no more room than a figure. Docker's data is the slowest of the answers,
// so the page is drawn before it: it says it is loading. Without Docker the figure is a dash.
function storageBody() {
  const kept = down() ? null : data?.data;
  let shown = figure("—");
  const left = kept?.total?.reclaimable;
  const words = left > 0 ? `${size(left)} unused` : "Nothing unused";
  const parts = kept ? diskParts(kept) : [];
  // Its popover: what unused is, then how much of each type, and the one thing to know before removing any
  const card = { title: words, text: "What no container uses",
    rows: [{ lines: parts.map((part) => [{ who: part.name }, usage(part.bytes, part.unused)]) }],
    foot: parts.some((part) => part.key === "volumes" && part.unused) ? "Unused volumes can hold data: removing one loses it." : "" };
  const unused = known(left) ? `<span class="dash-unused muted">${tipHtml(`${words}<span class="dash-mark" aria-hidden="true">${icon("info")}</span>`, card)}</span>` : "";
  if (kept) shown = figure(size(kept.total?.size), "in Docker");
  else if (!down() && !failed && (!data || data.pending)) shown = `<span class="dash-figure">${loading("Loading")}</span>`;
  return `<span class="dash-label">Storage</span>${shown}<span class="dash-tail">${unused}${refreshButton(data?.data)}</span>`;
}

// The versions of Docker, a section of the strip like the others, in the same markup: its label over its figure, the engine's version,
// then Compose's, muted
function engineBody() {
  const { engine, compose } = data?.docker || {};
  return `<span class="dash-label">Docker</span> ${engine || compose ? figure(esc(engine || "—"), compose ? `Compose ${esc(compose)}` : "") : figure("—")}`;
}

// What the line of Resources says while it is folded: the figures a person looks for, the load and the memory, from the same samples as the
// strip (written apart by `paintValues`), then Docker's versions, muted, which give way first. No chart. What Docker stores is the strip's.
function summaryBody() {
  const item = (label, value) => `<span class="dash-res-item"><span class="dash-res-label">${label}</span> <span class="dash-res-value">${value}</span></span>`;
  const { engine, compose } = data?.docker || {};
  const docker = failed && !data ? `<span class="dash-res-docker err-text" data-tip="${esc(failed)}" data-tip-cut>${esc(failed)}</span>`
    : engine || compose ? `<span class="dash-res-docker muted"><span>Docker ${esc(engine || "—")}</span>${compose ? ` <span>Compose ${esc(compose)}</span>` : ""}</span>` : "";
  return `${item("CPU", '<span data-value="cpu">—</span>')}${item("Memory", '<span data-value="memory">—</span>')}${docker}`;
}

// Resources unfolded or folded, as a person chose it, kept for the next visit. Folded, its charts are not drawn and their frames not
// asked for; unfolded, they are drawn at once and move again.
function fold(open) {
  unfolded = open;
  remember(FOLD, open ? "1" : "");
  paintFold();
  if (!open) {
    globalThis.cancelAnimationFrame?.(frame);
    frame = 0;
    return;
  }
  paintCharts();
  animate();
}

function paintFold() {
  $(".dash-resources", page).classList.toggle("open", unfolded);
  $("[data-res-toggle]", page).setAttribute("aria-expanded", String(unfolded));
}

// --- The charts -------------------------------------------------------------------------

// The line of one column of the samples as the clock says it is. It ends at `edge`, a little behind the clock: there the
// value is read between the two samples that surround it, or held from the last one while the next is late (but not past
// GAP, where nothing was listening). A line breaks where the samples are more than GAP seconds apart. Each is [time, value].
function track(samples, column, edge) {
  const lines = [[]];
  let before = null;
  for (const sample of samples) {
    const at = sample[0], now = [at, sample[column]];
    if (at > edge) {
      if (before && at - before[0] <= GAP) lines.at(-1).push([edge, before[1] + (now[1] - before[1]) * (edge - before[0]) / (at - before[0])]);
      return lines;
    }
    if (before && at - before[0] > GAP) lines.push([]);
    lines.at(-1).push(now);
    before = now;
  }
  if (before && edge - before[0] <= GAP) lines.at(-1).push([edge, before[1]]);
  return lines;
}

// A curve through points: quadratic ones through the midpoints, which never go beyond the points
function curve(points) {
  const at = (point) => `${point[0].toFixed(1)},${point[1].toFixed(1)}`;
  let d = `M${at(points[0])}`;
  for (let i = 1; i < points.length - 1; i++) d += `Q${at(points[i])} ${at([(points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2])}`;
  return `${d}L${at(points.at(-1))}`;
}

// The top edge of a chart: where the line needs it, at once when it needs more, easing down when it needs less
function ceiling(id, wanted, now) {
  const elapsed = Math.min(1, Math.max(0, now - spent));
  tops[id] = !tops[id] || wanted >= tops[id] ? wanted : tops[id] + (wanted - tops[id]) * (1 - Math.exp(-elapsed / EASE));
  return tops[id];
}

// What a tile draws its line against: the larger of FLOOR percent and its highest point for the CPU, what Docker has for the memory
function limit(id, lines) {
  const highest = Math.max(0, ...lines.flat().map((point) => point[1]));
  return id === "cpu" ? Math.max(FLOOR, highest) : data?.docker.memory || Math.max(1, highest);
}

// The top of a scale in words, written only when it changes: where the line needs it, not where it is easing to, so that a number does not tick
// as the edge settles. Its tooltip says what the chart covers and what it is drawn against.
function paintAxis(id, wanted) {
  const text = axisTop(id, wanted), el = $(`[data-axis="${id}"]`, page);
  if (!el || el.textContent === text) return;
  el.textContent = text;
  $(`[data-plot="${id}"]`, page).setAttribute("data-tip", scaleSaid(text));
}

// One chart, moved to where the clock puts it: its line, its area, its dot. The newest sample is at the right edge and the
// width is always SPAN seconds: the line grows in from the right, then passes, and a sample older than that has left.
function paintChart(svg, id, now, lag) {
  const edge = now - lag, lines = track(live, CHARTS[id], edge).filter((line) => line.length);
  const wanted = limit(id, lines), top = ceiling(id, wanted, now), span = SPAN;
  paintAxis(id, wanted);
  const placed = lines.map((line) => line.map(([at, v]) => [WIDTH - (edge - at) * WIDTH / span,
    HEIGHT - MARGIN[1] - Math.min(1, Math.max(0, v / top)) * (HEIGHT - MARGIN[0] - MARGIN[1])]));
  const drawn = placed.filter((line) => line.length > 1);
  const set = (name, d) => $(`.chart-${name}`, svg).setAttribute("d", d);
  set("line", drawn.map(curve).join(" "));
  const end = placed.at(-1)?.at(-1);
  set("dot", end && end[0] >= WIDTH - 0.5 && drawn.length ? `M${end[0].toFixed(1)},${end[1].toFixed(1)}h0` : "");
}

// Every chart on the page at `now`; the figures beside them are written when a sample comes
function paintCharts(now = stamp()) {
  if (!page || !unfolded) return;   // a folded strip draws nothing
  const lag = still() ? 0 : LAG;
  for (const id of Object.keys(CHARTS)) {
    const svg = $(`[data-chart="${id}"]`, page);
    if (svg) paintChart(svg, id, now, lag);
  }
  spent = now;
}

// Whether the stream has said nothing for long: what it last said is no longer the load
const silent = () => !live.length || stamp() - live.at(-1)[0] > SILENT;

// The container a name of `docker stats` is, as the lab names it
const labelOf = (name) => Object.values(store.state?.services || {}).find((service) => service.container === name)?.name || name;

// The popover of a load: the containers that weigh most, with what each takes, and for the memory what is left of Docker's. Nothing
// is said of a load that is not heard.
function weighing(id, last) {
  const total = data?.docker?.memory;
  const each = (last?.top || []).filter((one) => (id === "cpu" ? one.cpu > 0 : one.memory > 0));
  const shown = [...each].sort((a, b) => (id === "cpu" ? b.cpu - a.cpu : b.memory - a.memory)).slice(0, HEAVY);
  const rows = shown.length ? [{ lines: shown.map((one) => [{ who: labelOf(one.name) }, id === "cpu" ? percent(one.cpu) : size(one.memory, true)]) }] : [];
  const left = id === "memory" && last && known(total) && total > last.memory ? `${size(total - last.memory, true)} left in Docker` : "";
  return { title: shown.length ? "Heaviest containers" : last ? "Nothing runs" : "No load", rows, foot: left };
}

// How full Docker's memory is, as the figure says it: warm above WARN, and hot above FULL, as nothing is left for the next container
function level(used) {
  const total = data?.docker?.memory;
  if (!known(total) || !total || !known(used)) return "";
  return used / total >= FULL ? "err" : used / total >= WARN ? "warn" : "";
}

// The figures of the load: the newest sample, or a dash without one, without Docker, or once the stream went quiet; the memory's
// warns when little is left, and the popovers of the heaviest containers follow the sample
function paintValues() {
  const last = down() || silent() ? null : live.at(-1);
  for (const [id, column] of Object.entries(CHARTS)) {
    const text = last ? (id === "cpu" ? percent(last[column]) : size(last[column], true)) : "—";
    const tone = id === "memory" && last ? level(last[column]) : "";
    for (const el of $$(`[data-value="${id}"]`, page)) {   // the strip's and the line's
      if (el.textContent !== text) el.textContent = text;
      if ((el.dataset.level || "") !== tone) tone ? el.setAttribute("data-level", tone) : el.removeAttribute("data-level");
    }
  }
  for (const el of $$("[data-top]", page)) {
    const bubble = $(".tip-card", el);
    if (bubble) setHTML(bubble, cardHtml(weighing(el.dataset.top, last ? heavy : null)));
  }
}

// The animation: a frame after another while the page is on screen, the tab seen and the load streaming, the line moved
// by the clock. Nothing runs otherwise, and with reduced motion a chart is drawn when a sample comes.
function tick() {
  frame = 0;
  if (!animating()) return;
  paintCharts();
  frame = requestAnimationFrame(tick);
}

const animating = () => shown && unfolded && !document.hidden && !!source && source.readyState !== CLOSED && !still() && !!globalThis.requestAnimationFrame;

function animate() {
  if (!frame && animating()) frame = requestAnimationFrame(tick);
}

// --- Drawing ----------------------------------------------------------------------------

const SKELETON = `
  ${platformHead("Dashboard", { lean: true })}
  <div class="dash">
    <div class="dash-pair">
      <div class="card dash-card">
        <div class="card-head"><h2>Gravitee Platform</h2><span class="dash-license" data-card="license"></span><span class="dash-tools" data-card="count"></span></div>
        <div class="dash-heads" aria-hidden="true"><span>Product</span><span>Version</span><span>Status</span><span></span></div>
        <div class="dash-rows" data-card="products"></div>
        <div class="dash-rows" data-card="cloud"></div>
      </div>
      <div class="card dash-card">
        <div class="card-head"><h2>Playgrounds, setups and add-ons</h2><span class="dash-tools" data-card="catalogs"></span></div>
        <div class="list-tools">
          <label class="search grow">${icon("search")}<input type="search" data-filter="q" placeholder="Search…" aria-label="Search the playgrounds, setups and add-ons" autocomplete="off"></label>
          <div class="list-filters">
            <button class="combo-btn" data-kind aria-haspopup="dialog"><span class="sr-only">Kind: </span><span class="value"></span>${icon("chevron")}</button>
            <label class="toggle-chip"><input type="checkbox" data-running>Running</label>
            <label class="toggle-chip"><input type="checkbox" data-starred>Starred</label>
          </div>
        </div>
        <div class="dash-rows" data-card="catalog"></div>
        <div class="pager" hidden></div>
      </div>
    </div>
    <div class="card dash-resources">
      <div class="dash-res-head" data-res-head>
        <h2 class="dash-res-title"><button type="button" class="dash-res-toggle" data-res-toggle aria-expanded="false" aria-controls="dash-res-body"><span class="s-toggle">${icon("chevron")}</span>Resources</button></h2>
        <div class="dash-res-sum" data-card="summary"></div>
      </div>
      <div class="dash-res-body" id="dash-res-body">
        <div class="dash-res-cells">
          <div class="dash-cell" data-card="cpu"></div>
          <div class="dash-cell" data-card="memory"></div>
          <div class="dash-cell dash-storage" data-card="storage"></div>
          <div class="dash-cell dash-engine" data-card="engine"></div>
        </div>
      </div>
    </div>
  </div>`;

// What each block writes in its element. A failed read of the numbers is said in the place of Docker's versions, on the line of Resources
// and in its strip; the rest of the page does not wait for it.
const PAINT = {
  license: (el) => setHTML(el, License.pill()),   // the edition, and what the next Apply does to it, where Apply is pressed
  count: (el, state) => setHTML(el, toolsBody(state)),
  products: (el, state) => drawRows(el, productsBody(state)),
  cloud: (el, state) => drawRows(el, [cloudRow(state)]),
  catalogs: (el, state) => setHTML(el, catalogsBody(state)),
  catalog: (el, state) => drawRows(el, catalogBody(state)),
  summary: (el) => setHTML(el, summaryBody()),
  cpu: (el) => setHTML(el, cpuBody()),
  memory: (el) => setHTML(el, memoryBody()),
  storage: (el) => setHTML(el, storageBody()),
  engine: (el) => setHTML(el, failed && !data ? `<span class="dash-label">Docker</span> <span class="dash-figure err-text">${esc(failed)}</span>` : engineBody()),
};

// Each block is written again only when its own text changed (setHTML), a row of the products or the catalog on its own. What
// was drawn gets its progress bars, what waits for Docker or for the running job, and its buttons at work, as after every page.
function draw(ids = BLOCKS) {
  const { state } = store;
  if (!state) return;
  for (const id of ids) PAINT[id]($(`[data-card="${id}"]`, page), state);
  paintValues();
  paintCharts();
  paintProgress(page);
  paintBusy(page);
  paintActions(page);
}

// --- Reading ----------------------------------------------------------------------------

// What of the lab the versions depend on: whether Docker answers, what state each group of the platform is in, and the tag each
// service runs
const signature = (state) => JSON.stringify([state.blind, state.platform.groups.map((group) => group.state.key),
  Object.values(state.services).map((service) => service.running_tag)]);

// The next look at what is read, in `wait` milliseconds, unless the page is not seen
const later = (wait) => {
  clearTimeout(again);
  again = shown && !document.hidden ? setTimeout(() => read().catch(() => {}), wait) : null;
};

// Reads the numbers; `fresh` has the server read `docker system df` again. A failure is drawn and thrown, for the
// button that asked, and tried again after a while: the page does not stay as it was when it failed.
async function read(fresh = false) {
  let body;
  try {
    body = await api(fresh ? "/api/dashboard?refresh=1" : "/api/dashboard");
  } catch (err) {
    failed = err.message;
    later(RETRY);
    draw();
    throw err;
  }
  data = body;
  failed = "";
  if (down()) disconnect();   // no load to listen to
  else connect();             // and it is listened to again once Docker answers again
  // The registries and Docker's data are asked in the background: what they give fills in as it comes
  if (data.pending) later(data.data || down() ? WAIT : SOON);
  else {
    clearTimeout(again);
    again = null;
  }
  draw();
}

// A read when the lab moved. One at a time: what moves meanwhile is read once it ends.
function reread() {
  if (reading) {
    rereads = true;
    return;
  }
  reading = read().catch(() => {}).finally(() => {
    reading = null;
    if (rereads && shown) {
      rereads = false;
      reread();
    }
  });
}

// What was heard in the last minute and a little, none of it later than now: a clock set back would hold a chart's line
const recent = (now) => live.filter(([at]) => at <= now && now - at <= SPAN + GAP);

// The load of the containers, from the server's stream, kept a minute and a little. A sample is dated by the moment it
// came, which the chart reads its clock against; its figure is written at once and its chart moves by itself.
function connect() {
  if (source || !shown || document.hidden || down()) return;
  live = recent(stamp());
  const own = new EventSource("/api/dashboard/stats");
  source = own;
  own.onmessage = (event) => {
    const sample = JSON.parse(event.data), at = stamp();
    tries = 0;
    live = [...recent(at), [at, sample.cpu_percent, sample.memory_used]];
    heavy = { top: sample.top || [], memory: sample.memory_used };
    if (!data) return;
    paintValues();
    if (still()) paintCharts(at);
    else animate();
  };
  // A cut is tried again by the browser, unless Docker does not answer; one it gave up on (the server answered with an
  // error, which closes it for good) is opened again here after a while
  own.onerror = () => {
    if (down()) return disconnect();
    if (own.readyState !== CLOSED) return;
    disconnect();
    clearTimeout(reopen);
    reopen = setTimeout(connect, Math.min(REOPEN_MAX, REOPEN * 2 ** tries++));
  };
  lookout ||= setInterval(paintValues, 2000);
  animate();
}

function disconnect() {
  source?.close();
  source = null;
}

// --- The Cloud --------------------------------------------------------------------------

// Whether the last check of a platform is more than FRESH milliseconds old: a tab that is seen again leaves the Cloud alone for a moment
const stale = (id) => Date.now() - (checked.get(id) || 0) > FRESH;

// Asks a platform, through the server, what it holds for the account. One read at a time for a platform; one that fails
// is said as its state. `loud`: a person asked again, so its row says it is checking.
function check(id, loud = false) {
  if (loud) {
    asked.add(id);
    draw(["cloud"]);
  }
  if (!checking.has(id)) {
    checking.set(id, api(`/api/platforms/${id}/check`, {}).then(
      (answer) => checks.set(id, answer),
      (err) => checks.set(id, { state: "unreachable", message: err.message, environments: [], gateways: [] }),
    ).finally(() => {
      checked.set(id, Date.now());
      checking.delete(id);
      asked.delete(id);
      draw(["cloud", "products"]);   // the row of API Management in the Cloud is its environment
      follow();
    }));
  }
  return checking.get(id);
}

// The gateway that runs here and has not connected yet is read again soon, until it has: a person who just started it is
// waiting to see it. Each look is twice as late as the one before, and after POLLS of them it is left as it is, a gateway that
// does not connect (a token that is not right) not being asked for for ever. Nothing else is asked again before the page is
// shown again.
function follow() {
  clearTimeout(watch);
  watch = null;
  const waiting = (platforms || []).filter((p) => (checks.get(p.id)?.gateways || []).some((gateway) => gateway.here && gateway.connection !== "CONNECTED"));
  if (!waiting.length) polls = 0;
  else if (shown && !document.hidden && polls < POLLS) watch = setTimeout(() => { polls += 1; waiting.forEach((p) => check(p.id)); }, POLL * 2 ** polls);
}

// The platforms registered, then each one's check, all at once: what is read draws as it comes. One read at a time: whoever asks
// meanwhile gets the one on its way. `fresh` false leaves alone the check of an account that was read a moment ago.
function loadPlatforms(fresh = true) {
  listing ||= (async () => {
    try {
      platforms = (await api("/api/platforms")).platforms;
      cloudFailed = "";
    } catch (err) {
      cloudFailed = err.message;
    }
    draw(["cloud"]);
    (platforms || []).filter((p) => fresh || stale(p.id)).forEach((p) => check(p.id));
    follow();   // a gateway that has not connected is looked at again, whether it was read now or a moment ago
  })().finally(() => {
    listing = null;
    if (cloudFailed) draw(["cloud"]);
  });
  if (cloudFailed) draw(["cloud"]);   // the row says it is checking
  return listing;
}

// Nothing is listened to or asked for while the page is not seen
function stop() {
  disconnect();
  globalThis.cancelAnimationFrame?.(frame);
  frame = 0;
  clearTimeout(again);
  clearTimeout(watch);
  clearTimeout(reopen);
  clearInterval(lookout);
  again = watch = reopen = lookout = null;
  listing = null;   // a read that was on its way is not waited for when the page is seen again
}

// --- Clicks -----------------------------------------------------------------------------

// The item of the catalog a row is, from its key "<kind>:<id>"
const rowOf = (el) => store.item(...splitKey(el.closest(".dash-row")?.dataset.key || ""));

function init(section) {
  page = section;
  section.innerHTML = SKELETON;
  paintFold();
  pager = Pager($(".pager", section), ROWS, () => draw(["catalog"]));
  $("[data-filter]", section).value = filters.q;
  paintFilters();
  section.addEventListener("input", (e) => {
    if (e.target.dataset.filter !== "q") return;
    filters.q = e.target.value;
    filter();
  });
  // Running and Starred are options of their own, which combine, as on the lists; Starred is kept for the next visit
  section.addEventListener("change", (e) => {
    const box = e.target;
    if ("running" in box.dataset) filters.running = box.checked;
    else if ("starred" in box.dataset) {
      filters.starred = box.checked;
      remember(STARRED, filters.starred ? "1" : "");
    } else return;
    filter();
  });
  section.addEventListener("click", (e) => {
    const on = (selector) => e.target.closest(selector);
    if (onHeadClick(e)) return;
    if (on("[data-kind]")) return chooseKind(on("[data-kind]"));
    if (on("[data-clear]")) {
      Object.assign(filters, { q: "", kind: "", running: false, starred: false });
      remember(STARRED, "");
      $("[data-filter]", page).value = "";
      filter();
      return $("[data-filter]", page).focus();   // the button that was pressed is gone
    }
    if (on("[data-refresh]")) busy("dashboard", "refresh", read(true)).catch(() => {});
    else if (on("[data-connect]")) connectAccount(() => loadPlatforms());
    else if (on("[data-core]")) chooseCore(on("[data-core]").dataset.core, { anchor: on("[data-core]"), connected: () => loadPlatforms() });
    else if (on("[data-core-env]")) pickEnvironment(on("[data-core-env]"));
    else if (on("[data-core-do]")) {
      const what = on("[data-core-do]").dataset.coreDo, core = coreOf(), p = platforms?.find((found) => found.id === core.platform.id);
      const env = p && checks.get(p.id)?.environments?.find((found) => found.id === core.environment.id);
      if (what === "add-catalog") router.go("#/catalogs");   // no add-on runs its gateway: a catalog is where one comes from
      else if (env) perform(p, () => checks.get(p.id), env, what, () => check(p.id));
    }
    else if (on("[data-star]")) toggleStar(rowOf(on("[data-star]"))).then(() => refocus("#page-dashboard [data-filter]"));   // a row that left took the focus
    else if (on("[data-reload]")) loadPlatforms();
    else if (on("[data-recheck]")) check(on("[data-recheck]").dataset.recheck, true);
    else if (on("[data-account]")) {
      const id = on("[data-account]").dataset.account, p = platforms?.find((x) => x.id === id);
      if (p) openReplaceToken({ platform: p, done: () => check(id, true) });
    }
    else if (on("[data-res-head]")) fold(!unfolded);   // the chevron, or anywhere on the line of Resources
    else if (on("[data-license]")) License.show({ form: on("[data-license]").dataset.license === "form" });   // the note of what needs one
    else if (on("[data-consoles]")) {
      const [mode, id] = on("[data-consoles]").dataset.consoles.split(":"), unit = store.state.platform.groups.find((found) => found.id === id);
      const link = (one) => ({ label: one.label, href: one.url, dot: "healthy" }), mine = linksOf(unit, store.state);
      const older = mine.filter((one) => one.legacy).map(link), main = mine.filter((one) => !one.legacy).map(link);
      openMenu(on("[data-consoles]"), mode === "older" ? older : [...main, ...(main.length && older.length ? [{ separator: true }] : []), ...older]);
    } else if (on("[data-settings]")) {
      const x = rowOf(on("[data-settings]"));
      if (x) openSettings(x);
    } else if (on("button[data-act]")) {
      const x = rowOf(on("button[data-act]"));
      if (x) actOn(x);
    }
  });
  // A hidden tab listens to nothing: the stream closes, and opens again with the numbers when the tab is seen
  document.addEventListener("visibilitychange", () => {
    if (!shown) return;
    if (document.hidden) return stop();
    connect();
    read().catch(() => {});
    loadPlatforms(false);
  });
}

export const dashboardPage = {
  id: "dashboard", pattern: /^#\/dashboard$/, nav: "dashboard", title: "Dashboard",
  init,
  show() {
    shown = true;
    polls = 0;
    seen = store.state ? signature(store.state) : null;
    store.refreshPlaces();   // what a Start of a playground waits for is in the places
    connect();
    read().catch(() => {});   // what was read before stays drawn meanwhile
    loadPlatforms(false);     // an account read less than FRESH ago is not asked again: a trip through the menu costs Cloud nothing
  },
  hide() {
    shown = false;
    stop();
  },
  // The lab moved: what the state says is drawn at once; when it is what the versions depend on (the platform started or
  // stopped, a container runs another version), they are read again
  render(state) {
    paintHead(page, state);
    draw();
    const now = signature(state);
    if (now === seen) return;
    seen = now;
    reread();
  },
};
