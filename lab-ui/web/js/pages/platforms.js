// The Gravitee Cloud page (#/cloud): the Gravitee Cloud accounts this lab is connected to, a card for each. A card says what the account is and
// whether it answers, then lists its environments the way a person uses them: the ones in use first, in one table (the gateway of the
// environment that runs on this machine, whether the Lab may deploy playgrounds into it and what it deployed there, the one action to
// take next), then the ones not set up, a quiet line each; then every hybrid gateway of the account, in a table of its own. What each
// environment says is `environmentStatus`, and each gateway `gatewayList` (environments.js). A hybrid gateway is created, started,
// stopped and removed here; it runs on this machine as an add-on, and one runs at a time. A gateway made here is started at once, through
// the same plan and confirmation as a Start.

import { $, esc, icon, setHTML, loading, api, store, router } from "../core.js";
import { toast, effectsDialog, paintActions, paintBusy, actionButton, openMenu, tipHtml, keyed, noticeHtml, statusHtml, copyButton } from "../components/ui.js";
import { busy } from "../activity/jobs.js";
import { selectLogs, hasLogs } from "./logs.js";
import { seeded } from "./playground.js";
import { environmentStatus, splitEnvironments, gatewayList, gatewayCount, addonOf, hybrid, runs, stopOf, TERMS, gatewayCell, deployCell } from "../platform/environments.js";
import { holder, startGateway, connectAccount, perform as act } from "../platform/environment-actions.js";
import { KINDS, openReplaceToken, openRename } from "../dialogs/platform.js";
import { runsControl, environmentControl, chooseCore, pickEnvironment } from "../platform/core-choice.js";

const POLL = 5000;    // how often the page looks again, in milliseconds
const SHOWN = 2;      // the environments not set up that a card lists before it offers the others, when that hides at least two
const FAST = 12;      // the looks (of POLL) a gateway that has not connected is read at every one of, then at every second one
const SLOW = 24;      // up to this one, which is 2 minutes of waiting: Cloud has not seen the gateway, it is said, and read at every sixth (30 s)

let section;
let platforms = null;       // the platforms registered, null until they are read
let failed = "";            // why they could not be read
const { checks } = store.cloud;   // platform id -> what its last check answered: the Dashboard's too
const reading = new Map();  // platform id -> its check on its way
const asked = new Set();    // the ids whose check a person asked for: the card says it is checking
const wide = new Set();     // the ids of the cards that list every environment not set up
const waits = new Map();    // platform id -> how many looks its gateway that runs here has waited to connect
let target = null;          // the card an address names, to bring into view once it is drawn
let working = false;        // a gateway job ran at the last tick: one more read once it has ended
let ticks = 0;
let timer = null;

// What a platform is: the kind it is of
const [FIRST] = Object.values(KINDS);   // there is one kind
const kindOf = (p) => KINDS[p.kind] || FIRST;
const gatewaysOf = (id) => (checks.get(id)?.gateways || []).filter(hybrid);
const byId = (list, id) => list.find((x) => x.id === id);

// --- The card -------------------------------------------------------------------------

// A platform's state is its last check's: its tone and its word ("Account Token refused"), the same on the Dashboard, and here a pill.
// Why it cannot be read is said once, by the notice under the head, with what changes it.
const STATES = { ok: ["ok", "Connected"], invalid: ["err", "Account Token refused"], unreachable: ["warn", "Unreachable"] };

export const accountState = (check) => STATES[check.state] || ["", check.state];

function checkPill(check) {
  const [tone, word] = accountState(check);
  return `<span class="pill ${tone}">${esc(word)}</span>`;
}

const stateCell = (id) => (!checks.has(id) ? loading("Loading") : asked.has(id) ? loading("Checking") : checkPill(checks.get(id)));

// The address without its scheme: where the platform is
function host(url) {
  try { return new URL(url).host; } catch { return url; }
}

const moreButton = (attrs, label) => `<button class="btn plat-more-btn" ${attrs} aria-haspopup="true" aria-label="${esc(label)}">More${icon("chevron")}</button>`;

// Who it is and whether it answers: the account's name (the page says it is Gravitee Cloud), where its console is, its state, and what
// is always to do with an account: its connection (the key, as every connection's settings), open its console, and the rest in a menu
function head(p) {
  return `<span class="plat-mark">${icon("cloud")}</span>
    <span class="plat-id"><strong class="plat-name" role="heading" aria-level="2" data-tip="${esc(p.name)}" data-tip-cut>${esc(p.name)}</strong>
      <span class="plat-host" data-tip="${esc(p.url)}">${esc(host(p.url))}</span></span>
    <span class="plat-state">${stateCell(p.id)}</span>
    <span class="plat-tools"><button class="icon-btn" data-connection="${esc(p.id)}" aria-haspopup="dialog" data-tip="Manage connection" aria-label="Manage connection, ${esc(p.name)}">${icon("key")}</button>
      <a class="btn" href="${esc(p.url)}" target="_blank" rel="noopener" aria-label="Open console, ${esc(p.name)}">${icon("external")}Open console</a>
      ${moreButton(`data-account-more="${esc(p.id)}"`, `More for ${p.name}`)}</span>`;
}

// What an account that cannot say what it holds says, and what to do about it: the cause first. One that answered before keeps its
// table, which this stands above (`stale`).
function problem(p, check, stale = false) {
  const refused = check.state === "invalid";
  const action = refused ? `<button class="btn" data-account="${esc(p.id)}">Replace ${esc(kindOf(p).token)}</button>`
    : `<button class="btn" data-recheck="${esc(p.id)}">Check again</button>`;
  return `<div class="plat-notice">${noticeHtml({ tone: refused ? "err" : "warn", title: stale ? "Showing what was read before" : "Environments cannot be listed",
    text: esc(check.message || ""), action })}</div>`;
}

const bare = () => '<div class="plat-skel"><span></span><span></span><span></span></div>'.repeat(3);

// --- The environments ------------------------------------------------------------------

// The one action of an environment, in the column that has them: the add-on's own Start and Stop show their job at work wherever it
// was started; the others open the form that does them
function primary(p, env, st) {
  const { action } = st, addon = addonOf(p);
  if (!action) return "";
  const data = `data-do="${action.do}" data-p="${esc(p.id)}" data-env="${esc(env.id)}"`;
  if (["start-gateway", "stop-gateway"].includes(action.do)) {
    const swap = action.do === "start-gateway" && !st.gateway.here ? holder(p, checks.get(p.id), env.id) : "";
    const said = action.disabled ? `aria-disabled="true" data-why="${esc(action.reason)}"` : swap ? `data-tip="${esc(`Stops the gateway of ${swap}`)}"` : "";
    return actionButton({ key: `addon:${addon.id}`, verb: ["stop", "start"], label: action.label, cls: "plat-primary",
      attrs: `data-busy ${data} ${said}` });
  }
  return `<button class="btn plat-primary" ${data}>${esc(action.label)}</button>`;
}

const termLabel = (text, term) => `<span class="plat-label">${text}${term ? tipHtml(icon("info"), TERMS[term], "plat-info", text) : ""}</span>`;

// The slot of More in a row without one: the actions of every row end at one place
const NO_MORE = '<span class="plat-more-gap"></span>';

// How many gateways an environment has, when it has more than the one its state is about: a quiet button that leads to their list, under
// the environments
function countButton(p, env, st) {
  const n = st.gateway.count;
  return n > (st.gateway.name ? 1 : 0) ? `<button type="button" class="plat-count" data-gateways="${esc(p.id)}" aria-label="${esc(`${gatewayCount(n)} in ${env.name}`)}">${gatewayCount(n)}</button>` : "";
}

// An environment in use: its name, then its gateway and its deploys, then what to do
function row(p, env, st, first, more) {
  return `<div class="plat-grid plat-row" role="row" data-env="${esc(env.id)}">
    <strong class="plat-env" role="rowheader" data-tip="${esc(env.name)}" data-tip-cut>${esc(env.name)}</strong>
    <span class="plat-gateway" role="cell">${termLabel("Gateway", first && "gateway")}<span class="plat-cell">${gatewayCell(st.gateway, countButton(p, env, st))}</span></span>
    <span class="plat-deploys" role="cell">${termLabel("Deploys", first && "deploys")}<span class="plat-cell">${deployCell(st, seeded)}</span></span>
    <span class="plat-actions" role="cell">${primary(p, env, st)}${more ? moreButton(`data-env-more="${esc(env.id)}"`, `More for ${env.name}`) : NO_MORE}</span></div>`;
}

// An environment that is not set up: a line, and the way in
function quiet(p, env) {
  return `<div class="plat-grid plat-row plat-quiet" role="row" data-env="${esc(env.id)}">
    <span class="plat-env" role="rowheader" data-tip="${esc(env.name)}" data-tip-cut>${esc(env.name)}</span>
    <span class="plat-actions" role="cell"><button class="btn plat-primary" data-do="set-up" data-p="${esc(p.id)}" data-env="${esc(env.id)}" aria-label="Set up, ${esc(env.name)}">Set up</button>${NO_MORE}</span></div>`;
}

const heads = () => `<div class="plat-grid plat-heads" role="row">
  <span role="columnheader">Environment</span>
  <span role="columnheader">${tipHtml(`Gateway${icon("info")}`, TERMS.gateway, "plat-info", "Gateway", { short: true })}</span>
  <span role="columnheader">${tipHtml(`Deploys${icon("info")}`, TERMS.deploys, "plat-info", "Deploys", { short: true })}</span>
  <span role="columnheader" aria-label="Actions"></span></div>`;

const group = (label, inner) => `<div role="rowgroup" aria-label="${label}"><div class="stack-sep" aria-hidden="true">${label}</div>${inner}</div>`;

// The environments of an account: those in use under their own label, then those not set up
function table(p, check) {
  const late = (waits.get(p.id) || 0) >= SLOW;
  const { used, idle } = splitEnvironments(p, check, { late });
  // A line that hides one only would take the room of the one it hides
  const folds = idle.length > SHOWN + 1, listed = wide.has(p.id) || !folds ? idle : idle.slice(0, SHOWN), hidden = idle.length - listed.length;
  const toggle = folds ? `<div role="row"><div role="cell"><button class="plat-toggle" data-show="${esc(p.id)}" aria-expanded="${wide.has(p.id)}">${icon("chevron")}${
    wide.has(p.id) ? "Show fewer environments" : `Show ${hidden} more environment${hidden === 1 ? "" : "s"}`}</button></div></div>` : "";
  return `<div class="plat-table" role="table" aria-label="Environments of ${esc(p.name)}">
    ${used.length ? heads() + group("In use", used.map(({ env, st }, i) => row(p, env, st, i === 0, menuOf(p, env, st).length > 0)).join("")) : ""}
    ${idle.length ? group("Not set up", listed.map(({ env }) => quiet(p, env)).join("") + toggle) : ""}</div>`;
}

// --- The hybrid gateways ---------------------------------------------------------------

// The one action of a gateway, in the slot every row has: Stop for the one that runs here, Start for one whose token the Lab keeps (which
// swaps the one that runs here), both the add-on's own action, shown at work wherever it was started; Replace gateway for the one Cloud
// deleted while it ran here. Named for its gateway for a screen reader.
function gatewayAction(p, g) {
  const addon = addonOf(p);
  if (!addon) return "";
  const data = (what, label) => `data-gw-do="${what}" data-gw="${esc(g.id)}" aria-label="${esc(`${label}, ${g.name}`)}"`;
  if (g.orphan) return `<button class="btn plat-primary" ${data("replace", "Replace gateway")}>Replace gateway</button>`;
  if (g.here && runs(addon)) {
    const stop = stopOf(addon), off = !stop?.enabled ? `aria-disabled="true" data-why="${esc(stop?.reason || "")}"` : "";
    return actionButton({ key: `addon:${addon.id}`, verb: ["stop", "start"], label: "Stop gateway", cls: "plat-primary", attrs: `data-busy ${data("stop", "Stop gateway")} ${off}` });
  }
  if (!(g.here || g.kept)) return "";
  const unlicensed = !!addon.note_file && ["warn", "err"].includes(addon.note_tone);
  const swap = !g.here ? holder(p, checks.get(p.id), g.env?.id) : "";
  const said = unlicensed ? `aria-disabled="true" data-why="${esc(addon.note)}"` : swap ? `data-tip="${esc(`Stops the gateway of ${swap}`)}"` : "";
  return actionButton({ key: `addon:${addon.id}`, verb: ["stop", "start"], label: "Start gateway", cls: "plat-primary", attrs: `data-busy ${data("start", "Start gateway")} ${said}` });
}

// What a gateway offers besides its one action: its logs and its address while it runs here, then its removal, in red
function gatewayMenu(p, g) {
  const addon = addonOf(p), items = [];
  if (g.here && addon && hasLogs(addon.gateway.service)) {
    items.push({ label: "Show logs", onSelect: () => { selectLogs([addon.gateway.service]); router.go("#/logs"); } });
  }
  if (g.address) items.push({ label: "Copy address", hint: g.address, onSelect: () => copy(g.address) });
  const removal = g.orphan ? [] : [{ label: "Remove gateway", danger: true, hint: "Deleted in Gravitee Cloud", onSelect: () => removeGateway(p, g.id) }];
  return removal.length && items.length ? [...items, { separator: true }, ...removal] : [...items, ...removal];
}

// The state of a gateway: the dot and the word of an environment's Gateway, what else is known of it in its popover (the version and the
// domains have their columns)
function gatewayState(g) {
  const tip = Array.isArray(g.st.tip) ? (g.st.tip.length > 1 ? g.st.tip.slice(1) : []) : g.st.tip;
  const dot = g.st.key === "unknown" ? "" : g.st.dot;
  return statusHtml(dot, g.st.word, { tip, quiet: !dot || ["stopped", "off"].includes(dot) });
}

// A gateway of the account: its name, its environment, its state, its version, the domains it answers on, then what to do
function gatewayRow(p, g) {
  const cell = (cls, label, inner) => `<span class="plat-gw-cell ${cls}" role="cell"><span class="plat-label">${label}</span><span class="plat-cell">${inner}</span></span>`;
  const mono = (text) => (text ? `<span class="s-version" data-tip="${esc(text)}" data-tip-cut>${esc(text)}</span>` : '<span class="muted">—</span>');
  const more = gatewayMenu(p, g).length > 0;
  return `<div class="plat-gw-grid plat-gw-row" role="row" data-gw-row="${esc(g.id)}">
    <strong class="plat-env" role="rowheader" data-tip="${esc(g.name)}" data-tip-cut>${esc(g.name)}</strong>
    ${cell("plat-gw-env", "Environment", `<span class="plat-gw-text" data-tip="${esc(g.envName)}" data-tip-cut>${esc(g.envName || "—")}</span>`)}
    ${cell("plat-gw-state", "State", gatewayState(g))}
    ${cell("plat-gw-ver", "Version", mono(g.version))}
    ${cell("plat-gw-dom", "Domains", mono(g.domains.join(", ")))}
    <span class="plat-actions" role="cell">${gatewayAction(p, g)}${more ? moreButton(`data-gw-more="${esc(g.id)}"`, `More for ${g.name}`) : NO_MORE}</span></div>`;
}

// Every hybrid gateway of the account under the environments, in a table of its own under its band: the one that runs here first, then
// the connected ones, then the others. None: the empty block, with the Set up of the first environment when gateways can run here.
function gateways(p, check) {
  const list = gatewayList(p, check, { late: (waits.get(p.id) || 0) >= SLOW });
  const band = `<h3 class="stack-sep plat-gw-band" data-gateways-of="${esc(p.id)}" tabindex="-1">Hybrid gateways</h3>`;
  if (!list.length) {
    const [first] = check.environments, way = first && addonOf(p)
      ? `<button class="btn" data-do="set-up" data-p="${esc(p.id)}" data-env="${esc(first.id)}">Set up ${esc(first.name)}</button>` : "";
    return `<div class="plat-gws">${band}<div class="empty"><span>No hybrid gateway</span>${way}</div></div>`;
  }
  const heads = `<div class="plat-gw-grid plat-heads" role="row"><span role="columnheader">Gateway</span><span role="columnheader">Environment</span>
    <span role="columnheader">State</span><span role="columnheader">Version</span><span role="columnheader">Domains</span><span role="columnheader" aria-label="Actions"></span></div>`;
  return `<div class="plat-gws">${band}<div class="plat-table plat-gw-table" role="table" aria-label="Hybrid gateways of ${esc(p.name)}">${heads}${
    list.map((g) => gatewayRow(p, g)).join("")}</div></div>`;
}

function body(p) {
  const check = checks.get(p.id);
  if (!check) return `<div class="plat-table" aria-busy="true">${bare()}</div>`;
  if (check.state !== "ok") return check.stale ? problem(p, check, true) + table(p, check) + gateways(p, check) : problem(p, check);
  if (!check.environments.length) {
    return `<div class="plat-notice">${noticeHtml({ tone: "calm", title: "No environment in this account",
      action: `<button class="btn" data-recheck="${esc(p.id)}">Check again</button>` })}</div>`;
  }
  return table(p, check) + gateways(p, check);
}

// --- The page --------------------------------------------------------------------------

// What the page holds besides cards: while the platforms are read, when they cannot be, and before there is one
function empty() {
  if (failed) {
    return `<div class="plat-notice">${noticeHtml({ tone: "err", title: "Accounts cannot be read", text: esc(failed),
      action: '<button class="btn" data-reload>Try again</button>' })}</div>`;
  }
  if (!platforms) return `<div class="plat-table" aria-busy="true">${bare()}</div>`;
  // The one block of an empty list: its words, its action a plain button (the page's primary is the banner's Start or Apply)
  return `<div class="empty"><span>No account connected</span><button class="btn" data-new>${icon("plus")}Connect account</button></div>`;
}

function make() {
  return Object.assign(document.createElement("div"), { className: "card plat-card", tabIndex: -1,
    innerHTML: '<div class="plat-head"></div><div class="plat-body"></div>' });
}

// Every redraw replaces the elements it changes, and keyboard focus would fall to the page: the control a person is on is found again
// by what names it (its card, its attribute, its environment)
const CONTROLS = ["data-core", "data-core-env", "data-account-more", "data-connection", "data-recheck", "data-account", "data-show", "data-env-more", "data-do", "data-gw-do", "data-gw-more", "data-gateways", "data-new", "data-reload"];
function keyOf(el) {
  const control = el.closest?.("button, a"), attr = control && CONTROLS.find((name) => control.hasAttribute(name));
  if (!attr) return "";
  const card = control.closest(".plat-card")?.dataset.key, value = control.getAttribute(attr);
  return `${card ? `.plat-card[data-key="${CSS.escape(card)}"] ` : ""}[${attr}${value ? `="${CSS.escape(value)}"` : ""}]${
    attr === "data-do" ? `[data-env="${CSS.escape(control.dataset.env)}"]` : attr === "data-gw-do" ? `[data-gw="${CSS.escape(control.dataset.gw)}"]`
      : attr === "data-gateways" ? `[aria-label="${CSS.escape(control.getAttribute("aria-label"))}"]` : ""}`;
}

function draw() {
  if (!section) return;
  const at = document.activeElement, key = at && section.contains(at) ? keyOf(at) : "";
  const cards = !!platforms?.length && !failed;
  const lonely = $(".plat-lonely", section);
  lonely.hidden = cards;
  setHTML(lonely, cards ? "" : empty());
  $("[data-new]", $(".page-head", section)).hidden = !cards;
  setHTML($(".plat-runs", section), runsControl({ words: true }) + environmentControl());   // where API Management runs, as on the Dashboard
  keyed($(".plat-cards", section), cards ? platforms.map((p) => p.id) : [], make).forEach((card, i) => {
    const [top, bottom] = card.children, p = platforms[i];
    setHTML(top, head(p));
    setHTML(bottom, body(p));
  });
  paintBusy(section);
  paintActions(section);
  if (key && !at.isConnected) $(key, section)?.focus({ preventScroll: true });
  if (target && platforms?.some((p) => p.id === target)) {
    reveal(target);
    target = null;
  }
}

// The card an address opened, in view and focused, once it is drawn
function reveal(id) {
  requestAnimationFrame(() => {
    const card = $(`.plat-card[data-key="${CSS.escape(id)}"]`, section);
    card?.scrollIntoView({ block: "nearest" });
    card?.focus({ preventScroll: true });
  });
}

// --- Reading --------------------------------------------------------------------------

// What an answer changes in what the page knows. An account that does not answer keeps what it said before, with its new state: a
// moment of trouble does not empty its card.
function keep(id, answer) {
  const before = checks.get(id);
  if (answer.state === "unreachable" && before?.environments?.length) checks.set(id, { ...before, state: answer.state, message: answer.message, stale: true });
  else checks.set(id, answer);
}

// Asks the platform, through the server, what it holds for the account. One read at a time for a platform: whoever asks
// meanwhile gets the one on its way. A read that fails is said as the state of the platform. `loud`: a person asked, so the card says
// it is checking; the reads the page makes by itself change nothing until they find something.
function check(id, loud = false) {
  if (loud) {
    asked.add(id);
    draw();
  }
  if (!reading.has(id)) {
    reading.set(id, api(`/api/platforms/${id}/check`, {}).then(
      (answer) => keep(id, answer),
      (err) => keep(id, { state: "unreachable", message: err.message, environments: [], gateways: [] }),
    ).finally(() => {
      store.cloud.at.set(id, Date.now());
      reading.delete(id);
      asked.delete(id);
      draw();
    }));
  }
  return reading.get(id);
}

async function load() {
  if (!platforms) draw();   // the rows they will have, while they are read
  try {
    platforms = (await api("/api/platforms")).platforms;
    failed = "";
  } catch (err) {
    failed = err.message;
  }
  draw();
  for (const p of platforms || []) check(p.id);
  store.refreshPlaces();   // the environments linked, which the picker of where API Management runs lists
}

// The gateway that runs here is read again until Cloud has seen it connect, while its add-on runs
const waiting = (p) => ["running", "starting", "partly"].includes(addonOf(p)?.state.key)
  && gatewaysOf(p.id).some((g) => g.here && g.connection !== "CONNECTED");

// Whether the job that starts or stops the gateway of a platform runs
function gatewayJob(p) {
  const job = store.state?.job, addon = addonOf(p);
  return !!job && !job.done && !!addon && job.for === `addon:${addon.id}`;
}

// What the page reads again: a gateway that has not connected yet, every 5 seconds for a minute, every 10 for another, then every 30 and
// said not to be connected; an account that does not answer, every 30; all of them while a gateway job runs and once more after it
function poll() {
  if (document.hidden) return;
  ticks += 1;
  const was = working;
  working = (platforms || []).some(gatewayJob);
  for (const p of platforms || []) {
    const n = waiting(p) && !working && !was ? (waits.get(p.id) || 0) + 1 : 0, every = n <= FAST ? 1 : n < SLOW ? 2 : 6;
    if (n) waits.set(p.id, n);
    else waits.delete(p.id);
    if (n === SLOW) draw();   // it has waited long enough to be said
    const lost = checks.get(p.id)?.state === "unreachable" && ticks % 6 === 0;
    if ((n && n % every === 0) || working || was || lost) check(p.id);
  }
}

// --- Actions --------------------------------------------------------------------------

const confirmRemove = (title, rows, label = "Remove") => effectsDialog({ title, options: [{ rows }], action: () => label, danger: () => true });
const turnsOff = (addon) => (addon ? [{ name: addon.name, kind: "addon" }] : []);

// What playgrounds deployed in a platform, one line each ("Acme Hostels: 3 APIs in Development")
function deployed(p) {
  const environments = checks.get(p.id)?.environments || [];
  return (store.state?.playgrounds || []).flatMap((pg) => (pg.deployed || []).filter((d) => d.place.startsWith(`${p.id}/`))
    .map((d) => `${pg.name}: ${seeded(d).join(", ")} in ${byId(environments, d.place.slice(p.id.length + 1))?.name || d.environment}`));
}

// The record and the tokens the Lab keeps go; what the platform holds, and what was deployed there, stays
async function removePlatform(p) {
  const gateways = gatewaysOf(p.id), here = gateways.some((g) => g.here);
  const rows = [{ label: "Removes", hint: "With its tokens", items: [p.name], tone: "danger" },
    { label: "Turns off", items: here ? turnsOff(addonOf(p)) : [] },
    { label: "Stays there", items: [...gateways.map((g) => g.name), ...deployed(p)], note: true }];
  if (!(await confirmRemove(`Remove ${p.name}`, rows))) return;
  try {
    await busy(`remote:${p.id}`, "delete", api(`/api/platforms/${p.id}/delete`, {}));
    toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
  checks.delete(p.id);
  wide.delete(p.id);
  load();
  store.refresh();
}

const environmentName = (p, id) => byId(checks.get(p.id)?.environments || [], id)?.name || "";

// What says that a gateway is no one's but the Lab's to remove: another machine runs it
const elsewhere = (g) => !g.here && !g.kept && g.connection === "CONNECTED";

// The gateway goes from the platform, where it lives; the one that runs here is turned off with it
async function removeGateway(p, id) {
  const gateway = byId(gatewaysOf(p.id), id);
  if (!gateway) return;
  const rows = [{ label: "Removes", hint: elsewhere(gateway) ? "In Gravitee Cloud, connected from another machine" : "In Gravitee Cloud", items: [gateway.name], tone: "danger" },
    { label: "Turns off", items: gateway.here ? turnsOff(addonOf(p)) : [] }];
  if (!(await confirmRemove(`Remove gateway of ${environmentName(p, gateway.environmentId) || gateway.name}`, rows))) return;
  try {
    await busy(`remote:${p.id}:${id}`, "delete", api(`/api/platforms/${p.id}/gateways/${id}/delete`, {}));
    toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
  check(p.id);
  store.refresh();
}

// Only the Lab forgets the token: the one in the platform stays
async function removeToken(p, id) {
  const env = byId(checks.get(p.id)?.environments || [], id);
  if (!env) return;
  const rows = [{ label: "Removes", items: [`Cloud Token of ${env.name}`], tone: "danger" },
    { label: "Stays there", items: [`the token in ${kindOf(p).name}`], note: true }];
  if (!(await confirmRemove("Remove Cloud Token", rows))) return;
  try {
    await busy(`remote:${p.id}:token:${id}`, "delete", api(`/api/platforms/${p.id}/environments/${encodeURIComponent(id)}/token/delete`, {}));
    toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
  check(p.id);
  store.refresh();
}

// What the Lab keeps of an environment, as the gateways it could remove with it: those that run here or whose token it keeps. The one
// that runs here goes last, because its removal turns the add-on off.
const kept = (p, env) => gatewaysOf(p.id).filter((g) => g.environmentId === env.id && (g.here || g.kept)).sort((a, b) => a.here - b.here);

// The two things undone at once: the token and the gateways of an environment, with one confirmation that says what goes and what stays
async function stopUsing(p, env) {
  const st = environmentStatus(p, checks.get(p.id), env), own = kept(p, env), token = st.deploy.key !== "unset";
  const rows = [{ label: "Removes", hint: "In Gravitee Cloud", items: own.map((g) => g.name), tone: "danger" },
    { label: "Removes", hint: "From the Lab", items: token ? [`Cloud Token of ${env.name}`] : [], tone: "danger" },
    { label: "Turns off", items: own.some((g) => g.here) ? turnsOff(addonOf(p)) : [] },
    { label: "Stays there", items: [...(token ? [`the token in ${kindOf(p).name}`] : []), ...st.deployed.map((d) => `${d.name}: ${seeded(d).join(", ")}`)], note: true }];
  if (!(await confirmRemove(`Stop using ${env.name}`, rows, "Stop using"))) return;
  try {
    if (token) await busy(`remote:${p.id}:token:${env.id}`, "delete", api(`/api/platforms/${p.id}/environments/${encodeURIComponent(env.id)}/token/delete`, {}));
    for (const g of own) await busy(`remote:${p.id}:${g.id}`, "delete", api(`/api/platforms/${p.id}/gateways/${g.id}/delete`, {}));
    toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
  check(p.id);
  store.refresh();
}

// The forms and jobs of an environment's action (environment-actions.js), and the Start of a gateway of the list, which swaps the one that
// runs here
const perform = (p, env, what) => act(p, () => checks.get(p.id), env, what, () => check(p.id));
const startOther = (p, id) => startGateway(p, byId(gatewaysOf(p.id), id), () => check(p.id));

// What a gateway of the list does: the add-on's Start or Stop (perform's own, on the environment of the gateway), or the gateway that
// replaces one Cloud deleted
function gatewayDo(p, id, what) {
  const g = byId(gatewayList(p, checks.get(p.id)), id);
  if (!g) return;
  if (what === "start") startOther(p, id);
  else if (what === "stop" && g.env) perform(p, g.env, "stop-gateway");
  else if (what === "replace" && g.env) perform(p, g.env, "create-gateway");
}

// The list of the gateways of an account, in view, the keyboard on its band
function showGateways(id) {
  const band = $(`.plat-card[data-key="${CSS.escape(id)}"] [data-gateways-of]`, section);
  band?.scrollIntoView({ block: "start", behavior: globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  band?.focus({ preventScroll: true });
}

// A platform just connected shows its environments
function added() {
  load();
}

async function copy(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast("Copied");
  } catch {
    toast("Could not copy", true);
  }
}

// --- Menus ----------------------------------------------------------------------------

// --- The connection -------------------------------------------------------------------

// What the Lab holds to reach an account, opened from the key of its card: its name, its Account ID (to copy: it is what Gravitee Cloud
// shows next to its Account Tokens), its address, and whether its Account Token is set, never the token itself; with the one thing to
// do with it, replace it. A modal dialog, built once.
const connection = document.createElement("dialog");
connection.className = "create-dialog platform-dialog";
connection.setAttribute("aria-labelledby", "connection-title");
connection.innerHTML = `<h2 id="connection-title"></h2><dl class="pairs" id="connection-facts"></dl>
  <div class="dialog-actions"><button type="button" class="btn" data-connection-replace>Replace Account Token</button><button type="button" class="btn" data-connection-close>Close</button></div>`;
document.body.append(connection);
let connected = null;   // the platform whose connection the dialog shows

// The state of the Account Token: refused when the last check says so, set while the Lab keeps one (its value is never said)
function tokenState(p) {
  if (checks.get(p.id)?.state === "invalid") return statusHtml("issue", "Refused", { tip: ["Refused", checks.get(p.id).message || "Gravitee Cloud refuses it"] });
  return p.tokenSet === false ? statusHtml("", "Not set", { quiet: true }) : statusHtml("healthy", "Set");
}

function openConnection(p) {
  connected = p;
  const kind = kindOf(p), row = (label, value) => `<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`, id = p.account?.id || "";
  $("#connection-title", connection).textContent = `Connection of ${p.name}`;
  $("[data-connection-replace]", connection).textContent = `Replace ${kind.token}`;
  $("#connection-facts", connection).innerHTML = [
    row("Account", `<span>${esc(p.account?.name || p.name)}</span>`),
    row(kind.account, id ? `<code>${esc(id)}</code>${copyButton(id, `Copy the ${kind.account}`)}` : '<span class="muted">—</span>'),
    row("Address", `<code>${esc(p.url)}</code>`),
    row(kind.token, tokenState(p)),
  ].join("");
  if (!connection.open) connection.showModal();
  $("[data-copy], [data-connection-replace]", connection)?.focus();
}

connection.addEventListener("click", (e) => {
  if (e.target.closest("[data-connection-close]")) connection.close();
  else if (e.target.closest("[data-connection-replace]") && connected) {
    const p = connected;
    connection.close();
    openReplaceToken({ platform: p, done: () => check(p.id) });
  }
});

// What an account offers besides its connection and its console
function accountMenu(p) {
  return [
    { label: "Rename", onSelect: () => openRename({ platform: p, done: load }) },
    { label: "Check again", onSelect: () => check(p.id, true) },
    { separator: true },
    { label: "Remove account", danger: true, onSelect: () => removePlatform(p) },
  ];
}

// What an environment offers besides its one action, in groups: its gateway, its Cloud Token, then what removes. The console is the
// account's: Gravitee Cloud has no address for an environment that the Lab knows.
function menuOf(p, env, st) {
  const g = st.gateway, addon = addonOf(p), now = st.action?.do, stop = stopOf(addon);
  const here = g.here && addon, items = [], add = (item) => items.push(item);
  if (here && runs(addon) && stop && now !== "stop-gateway") add({ label: "Stop gateway", disabled: !stop.enabled, reason: stop.reason, onSelect: () => perform(p, env, "stop-gateway") });
  if (here && hasLogs(addon.gateway.service)) {
    add({ label: "Show logs", onSelect: () => { selectLogs([addon.gateway.service]); router.go("#/logs"); } });
  }
  if (g.address) add({ label: "Copy address", hint: g.address, onSelect: () => copy(g.address) });
  if (now === "add-license" && addon && g.id) add({ label: "Start gateway", disabled: true, reason: "Needs a license" });
  // A Cloud Token that expired or is refused is the row's action: what the gateway needs comes here meanwhile. These entries exist only with
  // the add-on that runs the gateway (`addon`), which is what the server names only when one can: no hold for want of one is possible here (the
  // page offers Add catalog in the cell of an environment that has none)
  if (now === "replace-token" && addon) {
    if (!(g.here || g.kept)) add({ label: g.id || g.key === "orphan" ? "Replace gateway" : "Create gateway", onSelect: () => perform(p, env, "create-gateway") });
    else if (!(g.here && runs(addon))) add({ label: "Start gateway", onSelect: () => perform(p, env, "start-gateway") });
  }
  if (st.deploy.key === "unset" && now !== "add-token") add({ label: "Add Cloud Token", onSelect: () => perform(p, env, "add-token") });
  if (["ready", "gateway"].includes(st.deploy.key) || (["expired", "refused"].includes(st.deploy.key) && now !== "replace-token")) {
    add({ label: "Replace Cloud Token", onSelect: () => perform(p, env, "replace-token") });
  }
  const removals = [], own = kept(p, env), token = st.deploy.key !== "unset";
  if (token) removals.push({ label: "Remove Cloud Token", danger: true, hint: "Deploys stop", onSelect: () => removeToken(p, env.id) });
  if (g.id && g.key !== "orphan") removals.push({ label: "Remove gateway", danger: true, hint: "Deleted in Gravitee Cloud", onSelect: () => removeGateway(p, g.id) });
  if (token && own.length) removals.push({ label: `Stop using ${env.name}`, danger: true, hint: "Removes its token and gateway", onSelect: () => stopUsing(p, env) });
  return removals.length && items.length ? [...items, { separator: true }, ...removals] : [...items, ...removals];
}

const environmentMenu = (p, env) => menuOf(p, env, environmentStatus(p, checks.get(p.id), env));

// --- The page's own events -----------------------------------------------------------

function init(el) {
  section = el;
  el.innerHTML = `
    <div class="page-head">
      <h1>Gravitee Cloud</h1>
      <div class="page-actions"><span class="plat-runs"></span><button class="btn" data-new hidden>${icon("plus")}Connect account</button></div>
    </div>
    <div class="plat-cards"></div>
    <div class="card plat-lonely">${loading("Loading")}</div>`;
  el.addEventListener("click", (e) => {
    const button = e.target.closest("button, a"), data = button?.dataset;
    if (!button || button.disabled || button.ariaDisabled === "true" || button.tagName === "A") return;
    if ("core" in data) return chooseCore(data.core, { anchor: button, connected: added });
    if ("coreEnv" in data) return pickEnvironment(button);
    const p = platforms?.find((x) => x.id === (data.p || data.accountMore || data.connection || data.recheck || data.account || data.show
      || e.target.closest(".plat-card")?.dataset.key));
    if ("new" in data) connectAccount(added);
    else if ("reload" in data) load();
    else if (!p) return;
    else if ("accountMore" in data) openMenu(button, accountMenu(p));
    else if ("connection" in data) openConnection(p);
    else if ("recheck" in data) check(p.id, true);
    else if ("account" in data) openReplaceToken({ platform: p, done: () => check(p.id) });
    else if ("show" in data) {
      if (!wide.delete(p.id)) wide.add(p.id);
      draw();
    } else if ("envMore" in data) openMenu(button, environmentMenu(p, byId(checks.get(p.id).environments, data.envMore)));
    else if ("gateways" in data) showGateways(p.id);
    else if ("gwDo" in data) gatewayDo(p, data.gw, data.gwDo);
    else if ("gwMore" in data) {
      const g = byId(gatewayList(p, checks.get(p.id)), data.gwMore);
      if (g) openMenu(button, gatewayMenu(p, g));
    }
    else if ("do" in data) perform(p, byId(checks.get(p.id).environments, data.env), data.do);
  });
}

// The page reads every platform as it opens, and again the ones that wait for a gateway to connect. An address that names a platform
// (#/cloud/<id>) brings its card into view.
function show([id] = []) {
  clearInterval(timer);
  if (id) target = id;
  waits.clear();
  load();
  timer = setInterval(poll, POLL);
}

function hide() {
  clearInterval(timer);
  timer = null;
  working = false;
  ticks = 0;
  waits.clear();
}

export const platformsPage = { id: "cloud", pattern: /^#\/cloud(?:\/([a-z0-9-]+))?$/, nav: "cloud", title: "Gravitee Cloud", init, show, hide, render: draw };
