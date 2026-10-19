// Containers (the Stack): every container under its group, the platform's actions, and what there is to open

import { $, $$, esc, icon, setHTML, loading, api, store, router, recallObject, remember, bareTag, ofPlatform } from "../core.js";
import { confirmDialog, confirmPlan, openMenu, paintLinks, progressBar, GAP, fileButtons,
  keyed, dockerDown, onKnown, serviceVersion, serviceCells, serviceOpen, actionButton,
  paintActions, showing, noteBadge, itemLink, applyBadge, behindTip, behindWhy, statusHtml, holdOff } from "../components/ui.js";
import { runJob, runPlatform, showOutput } from "../activity/jobs.js";
import { recordName } from "../platform/places.js";
import { onCloud, coreOf, runCore, gatewayWhy } from "../platform/core-choice.js";
import { selectLogs } from "./logs.js";
import { selectProduct } from "./plugins.js";
import { openFile, openSettings } from "../dialogs/editor.js";
import { License } from "../dialogs/license.js";

// An action of the platform in the head: it shows on its button as it works
const headButton = (verb, label, glyph, title = "") => actionButton({ key: "platform", verb, label, icon: glyph,
  attrs: `data-busy data-stack="${verb}"${title ? ` data-tip="${title}"` : ""} hidden` });

// The head of a page of the platform: its title, the badge "Apply needed" while something runs behind what is saved (the
// platform's state is the sidebar's chip, on every page), then its actions. The Containers' has all the actions, and
// the menu whose two actions show on its button: a spinner in place of its icon. Apply also shows the apply of a change (the
// Save & apply of Images and of the editor). The Dashboard's is `lean`: the one action that fits, Start all or Stop all (the whole platform), and
// neither Restart, Update nor the menu, which are the Containers page's. One head, one set of buttons on one key: both pages
// start and stop the platform the same way, and show the same progress. As in every head, the primary (Start or Apply) is the
// last button, before the menu.
export const platformHead = (title, { lean = false } = {}) => `
  <div class="page-head">
    <div class="page-title"><h1>${title}</h1></div>
    <div class="page-actions"${lean ? " data-lean" : ""}>
      ${headButton("stop", lean ? "Stop all" : "Stop", "stop")}${lean ? "" : `
      ${headButton("restart", "Restart", "restart", "Restart as it is")}
      ${headButton("update", "Update", "download", "Pull and recreate")}`}
      ${headButton("start", lean ? "Start all" : "Start", "play")}
      ${headButton("apply", "Apply", "apply", "Recreate the containers that changed")}${lean ? "" : `
      <span class="row-icons">${actionButton({ key: "platform", verb: ["down", "reset"], icon: "more", cls: "icon-btn",
        attrs: 'data-more data-tip="More" aria-label="More" aria-haspopup="true"' })}</span>`}
    </div>
  </div>`;

const SKELETON = `
  ${platformHead("Containers")}
  <div class="stack-layout">
    <div class="card">
      <div class="cols stack-head" aria-hidden="true"><span></span><span>Service</span><span>State</span><span>Version</span><span>Ports</span><span>Uptime</span><span></span></div>
      <ul class="groups"><li class="empty">${loading("Loading")}</li></ul>
    </div>
    <div class="side">
      <div class="card">
        <div class="card-head"><h2>Consoles</h2></div>
        <div class="links"></div>
      </div>
    </div>
  </div>`;

const SECTIONS = { playground: "Playground", addon: "Other add-ons" };
const CORE = "core", ADDONS = "section:Add-ons";   // the keys of the two bands that fold: the platform's and, inside it, its own add-ons
const ACTIONS = { logs: ["Logs", "logs"], restart: ["Restart", "restart"], stop: ["Stop", "stop"], start: ["Start", "play"] };
// The two that remove something ask first, in the words of their menu entry; Reset waits for its word to be typed
const CONFIRM = {
  down: { title: "Remove containers", text: "Data is kept", action: "Remove", danger: true },
  reset: { title: "Reset", text: "Deletes every container and volume", word: "reset", action: "Reset", danger: true },
};

let page = null;   // the section of the page
let groups = [];   // the groups drawn last, which the buttons act on
// group -> expanded. The groups shape the page: the browser remembers which are open, unlike the detail of a list row
const folds = recallObject("expanded");
// band -> folded. The platform's band holds its groups and the add-ons', the add-ons' band theirs: both open until a person folds them
const shut = recallObject("shut");

// A service shows when it should run or still has a container, or says that it needs a license; only a container has
// logs
const created = (service) => service.state.key !== "absent";
const exists = (service) => !!service && (service.on || created(service) || service.needs_license);

// The platform's groups in their declared order, then its own add-ons (Gravitee's) that are on or still run, in the order of the
// catalog, which are the platform's too; then the other add-ons, and the playground. `inside` is what a band of the platform holds.
// While docker-compose.yml does not load, what is on is not known: the platform alone.
function stackGroups(state) {
  const items = onKnown() ? [...state.addons, ...state.playgrounds].filter((x) => x.on || x.up) : [];
  const own = items.filter((x) => x.kind === "addon" && ofPlatform(x)), others = items.filter((x) => !own.includes(x));
  return [...state.platform.groups, ...own, ...others].map((unit) => {
    const kind = unit.kind || "group", inside = kind === "group" || own.includes(unit);
    return { unit, kind, inside, key: `${kind}:${unit.id}`, section: inside ? (kind === "addon" ? "Add-ons" : undefined) : SECTIONS[kind],
      services: unit.services.filter((n) => exists(state.services[n])) };
  }).filter((g) => g.services.length);
}

// Every row has the same icons in the same places: open, file, settings, then logs, restart, stop or start.
// A missing one keeps its place. Reading logs never waits; the rest waits for Docker and for the running job, and
// shows on its icon as it works on the row's `key`. Stop and Start share their slot: whichever of the two is drawn
// shows the other one at work too, as the icon changes under it.
function rowButton(act, key, name = "") {
  if (!act) return GAP;
  const [label, symbol] = ACTIONS[act], said = act === "logs" ? `Logs of ${name}` : `${label} ${name}`;
  const attrs = `data-act="${act}" data-tip="${label}" aria-label="${esc(name ? said : label)}"`;
  if (act === "logs") return `<button class="icon-btn" ${attrs}>${icon(symbol)}</button>`;
  return actionButton({ key, verb: act === "restart" ? act : ["stop", "start"], icon: symbol, cls: "icon-btn",
    attrs: `data-busy ${attrs}` });
}

// Whether a restart of `key` has something to say (showing): its button keeps its place while it works, the
// containers it restarts being down for a moment, and until it has said how it went, though they stayed down
const restarting = (key) => showing(key, "restart");

// The State cell of a row, a group's or a container's: the state component, its dot and its word, the word muted while what
// it says is stopped or off, a bar under it while it starts or stops. Nothing is said of what is off, nor while Docker does
// not answer.
const QUIET = new Set(["stopped", "off", "absent", "done", "exited"]);
function stateCell(st, key) {
  if (dockerDown() || !st.label) return "<span></span>";
  const moving = ["starting", "stopping"].includes(st.key);
  return `<span class="s-state">${statusHtml(st.key, st.label, { quiet: QUIET.has(st.key), extra: moving ? progressBar(key) : "" })}</span>`;
}

// The containers of a group start, stop and restart here, those of a platform group as those of an add-on or a
// playground, which stays on or off as it is. A playground's seed runs with its start and its Re-seed: one without
// apps has nothing to start or restart here. Nor has a group of services that need a license and have none: none is on.
function groupActions(g, services) {
  const running = services.some((s) => s.state.running), logs = services.some(created) && "logs";
  const own = g.kind !== "playground" || g.unit.apps.length > 0;
  const restart = own && (running || restarting(g.key)) && "restart";
  return [logs, restart, running ? "stop" : own && services.some((s) => s.on) && "start"];
}

// A container: its name and what the server notes of it ("Apply needed"), its state, the version it runs, its ports,
// its uptime while it runs, its actions. A one-shot that ended well did its part: it has no Start. A playground's
// seed has neither Start nor Restart: its playground's Re-seed runs it again.
function containerHtml(name, state) {
  const service = state.services[name], st = service.state, key = `service:${name}`, seed = service.role === "seed";
  const restart = !seed && (st.running || restarting(key)) && "restart";
  const actions = [created(service) && "logs", restart,
    st.running ? "stop" : !seed && service.on && st.key !== "done" && "start"];
  return `
    <li class="container cols" data-service="${esc(name)}">
      <span></span>
      <span class="s-name"><span class="c-name" data-tip="${esc([`${service.label} (${name})`, service.description].filter(Boolean).join(": "))}">${esc(service.label)}</span><span class="c-id">${esc(name)}</span>${service.note_apply ? "" : noteBadge(service)}</span>
      ${stateCell(st, key)}
      ${serviceCells(service)}
      <span class="s-uptime"></span>
      <span class="row-icons">${serviceOpen(name)}${GAP}${GAP}${actions.map((act) => rowButton(act, key, service.label)).join("")}</span>
    </li>`;
}

// A group with an issue stays open on what is wrong: its row takes no click
const forced = (g) => g.unit.state.key === "issue";
const isOpen = (g) => forced(g) || !!folds[g.key];
// A band folds the groups under it, unless one of them is in an issue: what is wrong is not hidden
const under = { [CORE]: (g) => g.inside, [ADDONS]: (g) => g.inside && g.kind === "addon" };
const bandForced = (key) => groups.some((g) => under[key](g) && forced(g));
const bandOpen = (key) => !shut[key] || bandForced(key);

// The ports a group publishes, in the Ports column of its row: the first, and how many more, all of them in its tooltip
function portsOf(services) {
  const ports = [...new Set(services.flatMap((s) => s.ports))];
  if (!ports.length) return '<span class="s-ports"></span>';
  return `<span class="s-ports"${ports.length > 1 ? ` data-tip="${esc(ports.join(", "))}"` : ""}><span>${esc(ports[0])}</span>${
    ports.length > 1 ? `<span>+${ports.length - 1}</span>` : ""}</span>`;
}

// How long a container has run, in seconds, from the words of its uptime ("5 min", "14 h"); null when they say none
const UNITS = { s: 1, min: 60, h: 3600, d: 86400, mo: 2592000, y: 31536000 };
function seconds(uptime) {
  const [, n, unit] = /^(\d+) (s|min|h|d|mo|y)$/.exec(uptime || "") || [];
  return n ? Number(n) * UNITS[unit] : null;
}

// How long a group has run whole: the uptime of its youngest container that runs
function groupUptime(services) {
  const times = services.filter((s) => s.state.running && s.state.key !== "issue" && seconds(s.uptime) !== null);
  return times.length ? times.reduce((a, b) => (seconds(b.uptime) < seconds(a.uptime) ? b : a)).uptime : "";
}

// The version of a platform group, {text, title}: the one its Gravitee images share, as saved; the tag one of them
// runs instead when it is another, its tooltip naming the one saved, as a container's does. An add-on or a playground
// has none.
function groupVersion(g, state) {
  const saved = g.unit.version || "";
  const late = saved && g.services.map((n) => state.services[n]).find((s) => s.registry && s.running_tag && s.running_tag !== s.tag);
  return late ? { text: bareTag(late.running_tag), title: `${saved} is saved` } : { text: saved, title: "" };
}

// What a group's <li> holds: its row, and its containers once it is open. An add-on's name leads to its row in its
// list, a playground's to its page. A group of the platform is in the core's file and settings, whose icons are on
// the core's line: its own slots for them stay empty, and its first slot opens its product's plugins. The row is the
// anatomy of a catalog row: its name, then what the server notes of it as a badge when it says more than its state
// ("Starts with the platform", not "Stopped" again, "Needs a license" leading to the form, "Apply needed" with its
// causes), and the output of its last job when it failed; its state in the State column, as its containers' are.
function groupHtml(g, state) {
  const { unit } = g, services = g.services.map((n) => state.services[n]), version = groupVersion(g, state);
  const note = unit.note && unit.note !== unit.state.label ? noteBadge(unit) : "";
  const output = unit.failed_job ? `<button type="button" class="pill note err" data-output="${esc(unit.failed_job)}"><span class="pill-text">Show output</span>${icon("arrow-right")}</button>` : "";
  const plugins = unit.product ? `<button class="icon-btn" data-plugins="${esc(unit.product)}" data-tip="Plugins" aria-label="Plugins of ${esc(unit.name)}">${icon("plug")}</button>` : GAP;
  // Where its images come from, as Images names it, when it is not Docker Hub, after its badges
  const registry = unit.registry_label ? `<span class="pill" data-tip="${esc(unit.registry)}">${esc(unit.registry_label)}</span>` : "";
  return `
    <div class="group-row cols ${forced(g) ? "" : "opens"}">
      ${forced(g) ? "<span></span>" : `<button type="button" class="s-toggle" data-toggle aria-expanded="${isOpen(g)}" aria-label="${esc(unit.name)}">${icon("chevron")}</button>`}
      <span class="s-name"><span class="g-name">${g.kind === "group" ? esc(unit.name) : itemLink(unit)}</span>${note}${output}${registry}</span>
      ${stateCell(unit.state, g.key)}
      <span class="s-version" ${version.title ? `data-tip="${esc(version.title)}"` : ""}>${esc(version.text)}</span>${portsOf(services)}<span class="s-uptime"></span>
      <span class="row-icons">${plugins}${g.kind === "group" ? GAP + GAP : fileButtons(unit)}${groupActions(g, services).map((act) => rowButton(act, g.key, unit.name)).join("")}</span>
    </div>
    ${isOpen(g) ? `<ul class="containers">${g.services.map((n) => containerHtml(n, state)).join("")}</ul>` : ""}`;
}

// The line of the core, above its groups: the platform's name, its license as a pill that leads to the license (its tooltip says until
// when, or a popover what is not saved yet), and its files in the places of a group's
function coreHtml(state) {
  return `${bandToggle(CORE, state.platform.name)}<span class="core-name">${esc(state.platform.name)}${License.pill()}</span>
    <span class="row-icons">${GAP}${fileButtons(state.platform)}${GAP}${GAP}${GAP}</span>`;
}

// The toggle of a band, as a group's: none while an issue keeps the band open
const bandToggle = (key, name) => (bandForced(key) ? "<span></span>"
  : `<button type="button" class="s-toggle" data-fold aria-expanded="${bandOpen(key)}" aria-label="${esc(name)}">${icon("chevron")}</button>`);
const bandClass = (key, ...more) => ["stack-sep", "core-sep", "cols", ...more, bandForced(key) ? "" : "opens"].join(" ");

// The version column is as wide as the longest version shown, in the 12 px monospace of a version
function sizeVersions(state) {
  const versions = groups.flatMap((g) => [groupVersion(g, state).text, ...g.services.map((n) => serviceVersion(state.services[n]))]);
  const longest = Math.max(9, ...versions.map((v) => v.length));
  page.style.setProperty("--version-width", `${Math.ceil(longest * 7.3) + 4}px`);
}

// The core's line, then its groups, the add-ons' band with theirs, and the playground's separator with its group, as <li>s kept
// from one refresh to the next (data-key): a container that gets healthy writes its own group again, and the focus stays where
// it is elsewhere. A folded band leaves out what is under it.
function renderGroups(state) {
  groups = stackGroups(state);
  sizeVersions(state);
  const lines = new Map([[CORE, { cls: bandClass(CORE), html: coreHtml(state) }]]);
  groups.forEach((g, i) => {
    const first = g.section !== groups[i - 1]?.section;
    if (g.inside && !bandOpen(CORE)) return;
    if (g.inside && g.kind === "addon" && first) {
      lines.set(ADDONS, { cls: bandClass(ADDONS, "sub"), html: `${bandToggle(ADDONS, g.section)}<span class="core-name">${esc(g.section)}</span>` });
    }
    if (g.inside && g.kind === "addon" && !bandOpen(ADDONS)) return;
    if (!g.inside && first) lines.set(`section:${g.section}`, { cls: "stack-sep", html: g.section });
    lines.set(g.key, { cls: `group ${isOpen(g) ? "open" : ""}`, html: groupHtml(g, state) });
  });
  keyed($(".groups", page), [...lines.keys()], () => document.createElement("li")).forEach((li) => {
    const { cls, html } = lines.get(li.dataset.key);
    li.className = cls;
    setHTML(li, html);
  });
  // Uptimes tick on their own: written apart, a tick replaces no row (hover and focus stay). A container in an issue has
  // none, its State says which (it keeps restarting, it is unhealthy). Without Docker, nothing is said.
  for (const li of $$(".container", page)) {
    const { state: st, uptime } = state.services[li.dataset.service];
    $(".s-uptime", li).textContent = dockerDown() || !st.running || st.key === "issue" ? "" : uptime;
  }
  for (const g of groups) {
    const cell = $(`.group[data-key="${CSS.escape(g.key)}"] > .group-row > .s-uptime`, page);
    if (cell) cell.textContent = dockerDown() ? "" : groupUptime(g.services.map((n) => state.services[n]));
  }
}

// Consoles under the platform's groups, then the playground's, then under the categories of the add-ons: the order
// of the services. Only those of the services listed: while docker-compose.yml does not load, the platform's alone.
function renderLinks(state) {
  const listed = new Set(groups.flatMap((g) => g.services));
  paintLinks($(".links", page), state, state.links.filter((l) => listed.has(l.service)));
}

// The action of the head that leads, its one primary button: Start while the platform is stopped, else Apply when something
// runs behind what is saved; none otherwise
const leadOf = (platform) => (platform.state.key === "stopped" && platform.start.needed ? "start" : platform.apply.needed ? "apply" : "");

// The head offers what fits the state, as the row of a group does: Start while something that is on does not run;
// Apply while something runs behind what is saved; Stop, Restart and Update while something of what is on runs, an
// add-on alone too (`restart.needed`, `update.needed`). The primary is Start while the platform is stopped, which
// makes again what runs behind on its way, else Apply. A button that shows its action stays in view, in the look it
// had, until it has said how it went (showing): Stop is still there to say it once everything is stopped. A lean head offers
// the one that leads, else Stop while something runs, else Start, and nothing beside a button that is still at work. With API
// Management in the Cloud, the lean head's Start all and Stop all follow it (state.core): its gateway here, the rest of the platform and
// what is on, without the local API Management; Start all leads while none of that runs, and says why it cannot start when it cannot.
function renderActions(root, platform) {
  const lean = !!$("[data-lean]", root), cloud = lean && onCloud(), core = coreOf();
  const runs = cloud ? core.stop.needed : platform.restart.needed, starts = cloud ? core.start.needed : platform.start.needed;
  const lead = cloud ? (!runs && starts ? "start" : platform.apply.needed ? "apply" : "") : leadOf(platform);
  const buttons = $$("[data-stack]", root), kept = (button) => showing("platform", button.dataset.actVerb);
  let offered = { start: platform.start.needed, apply: platform.apply.needed, stop: runs, restart: runs, update: platform.update.needed };
  if (lean) offered = buttons.some(kept) ? {} : { [lead === "start" ? "start" : runs ? "stop" : starts ? "start" : ""]: true };
  for (const button of buttons) {
    const verb = button.dataset.stack;
    button.hidden = !kept(button) && !offered[verb];
    if (!kept(button)) button.classList.toggle("primary", verb === lead);
    if (lean && verb === "start" && (cloud || button._core)) {
      button._core = cloud;
      // No add-on that runs the gateway, or the catalogs not there: it says why before the server would refuse it (gatewayWhy)
      const why = cloud ? gatewayWhy() : "";
      holdOff(button, cloud && (!core.start.enabled || !!why), cloud ? why || core.start.reason || "" : "",
        cloud ? `Starts the hybrid gateway of ${core.name} and the rest of the platform, not the local ${core.group?.name || ""}` : "");
    }
  }
}

// The head as the state says it: the actions that fit, and while something runs behind what is saved the badge "Apply needed"
// beside the title, its popover a row for each product that does, in whatever state the platform is: the Apply button is never
// there with no explanation.
export function paintHead(root, state) {
  renderActions(root, state.platform);
}

function render(state) {
  paintHead(page, state);
  renderGroups(state);
  renderLinks(state);
  paintActions(page);
}

// --- Actions ----------------------------------------------------------------------------

// What a Reset leaves in the platforms that playgrounds deployed to, "Acme Hostels in Development (My Account)": a
// record of `deployed` names its place by id and its environment by name
const stays = (state) => state.playgrounds.flatMap((p) => (p.deployed || []).map((record) => `${p.name} in ${recordName(record)}`));

// What Reset says: that it turns off the playground that is on (`reset_turns_off`), what its seed created being gone
// with the volumes, and what stays in a platform
function resetConfirm(state) {
  const off = state.platform.reset_turns_off, left = stays(state);
  const text = [CONFIRM.reset.text + (off ? `, and turns off ${off}` : ""), left.length && `Stays: ${left.join(", ")}`];
  return { ...CONFIRM.reset, text: text.filter(Boolean).join(". ") };
}

// The platform's actions. Start and Apply say first what they start and restart beyond what runs, when they do
// (runPlatform); Restart restarts what runs as it is; Update pulls, then makes again what runs with what it pulled:
// what it makes again is not known before, it asks nothing.
async function stackAction(action, lean = false) {
  if (lean && onCloud() && ["start", "stop"].includes(action)) return runCore(action);
  if (["start", "apply"].includes(action)) return runPlatform(action);
  const confirm = action === "reset" ? resetConfirm(store.state) : CONFIRM[action];
  if (confirm && !(await confirmDialog(confirm))) return;
  runJob(() => api(`/api/stack/${action}`, {}), { key: "platform", verb: action });
}

// A click on a button of the head, wherever the head is drawn: whether it was one
export function onHeadClick(e) {
  const action = e.target.closest("[data-stack]"), menu = e.target.closest("[data-more]");
  if (action) stackAction(action.dataset.stack, !!action.closest("[data-lean]"));
  else if (menu) platformMenu(menu);
  return !!(action || menu);
}

function platformMenu(button) {
  const wait = store.wait(), waits = { disabled: !!wait, reason: wait };
  openMenu(button, [
    { label: CONFIRM.down.title, hint: CONFIRM.down.text, ...waits, onSelect: () => stackAction("down") },
    { label: CONFIRM.reset.title, hint: CONFIRM.reset.text, danger: true, ...waits, onSelect: () => stackAction("reset") },
  ]);
}

const groupOf = (el) => groups.find((g) => g.key === el.closest(".group")?.dataset.key);

// A Start that starts what its services depend on first, or makes again what runs behind what is saved, says so
// before it runs, as the rows of a plan ("Start Ace", "Starts: Gateway, MongoDB"); one that starts only
// them does not ask. Resolves with null when canceled.
const confirmStart = (name, { brings = [], recreates = [] }) => confirmPlan(`Start ${name}`, "Start",
  brings.length || recreates.length ? { starts: brings, restarts: recreates } : null);

// The Start or Stop of a platform group, from its row
async function actOnGroup(unit, action) {
  if (action === "start" && !(await confirmStart(unit.name, unit))) return;
  runJob(() => api(`/api/groups/${unit.id}/${action}`, {}), { key: `group:${unit.id}`, verb: action });
}

// A button acts on the container of its row, else on its group: a platform group, or the containers of an add-on or
// a playground
async function act(button) {
  const action = button.dataset.act, service = button.closest("[data-service]")?.dataset.service;
  const g = groupOf(button);
  if (action === "logs") {
    selectLogs(service ? [service] : g.services);
    return router.go("#/logs");
  }
  if (service) {
    if (action === "start" && !(await confirmStart(store.service(service).label, store.service(service)))) return;
    return runJob(() => api(`/api/services/${service}/${action}`, {}), { key: `service:${service}`, verb: action });
  }
  const { unit } = g;
  if (g.kind === "group") return actOnGroup(unit, action);
  if (action === "start" && !(await confirmStart(unit.name, unit))) return;
  runJob(() => api(`/api/${g.kind}s/${unit.id}/containers/${action}`, {}), { key: g.key, verb: action });
}

// A file button opens what its add-on or playground has, the one of the core's line what the core has
function edit(button) {
  const g = groupOf(button);
  if ("settings" in button.dataset) openSettings(g ? g.unit : store.state.platform);
  else openFile(button.dataset.file);
}

function toggle(key) {
  folds[key] = !folds[key];
  remember("expanded", JSON.stringify(folds));
  store.notify();
}

function foldBand(key) {
  shut[key] = !shut[key];
  remember("shut", JSON.stringify(shut));
  store.notify();
}

function init(section) {
  page = section;
  section.innerHTML = SKELETON;
  section.addEventListener("click", (e) => {
    const on = (selector) => e.target.closest(selector);
    // The chip, on what the license gives; a note that says what is wrong with it, on the form that takes a new one
    if (on("[data-license]")) return License.show({ form: on("[data-license]").dataset.license === "form" });
    if (onHeadClick(e)) return;
    if (on("[data-output]")) return showOutput(on("[data-output]").dataset.output);
    if (on("[data-plugins]")) {
      selectProduct(on("[data-plugins]").dataset.plugins);
      return router.go("#/plugins");
    }
    if (on("button[data-act]")) return act(on("button[data-act]"));
    if (on("[data-file], [data-settings]")) return edit(on("[data-file], [data-settings]"));
    if (on("a")) return;
    // A band folds the same way: from its toggle, or a click on its line but its controls
    const band = on(".core-sep.opens");
    if (band && (!on("button") || on("[data-fold]"))) {
      foldBand(band.dataset.key);
      if (on("[data-fold]")) $(`.core-sep[data-key="${CSS.escape(band.dataset.key)}"] [data-fold]`, section)?.focus();
      return;
    }
    // A group opens from its toggle, or a click anywhere on its row but its controls; the toggle keeps the focus as it is drawn again
    const row = on(".group-row.opens");
    if (row && (!on("button") || on("[data-toggle]"))) {
      const key = row.parentElement.dataset.key;
      toggle(key);
      if (on("[data-toggle]")) $(`.group[data-key="${CSS.escape(key)}"] [data-toggle]`, section)?.focus();
    }
  });
}

export const stackPage = { id: "stack", pattern: /^#\/containers$/, nav: "stack", title: "Containers", init, render };
