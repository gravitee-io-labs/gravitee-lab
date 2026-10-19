// Logs: what the chosen services write, followed live, through their restarts, filtered by level and by text

import { $, $$, esc, icon, setHTML, loading, store, router, api } from "../core.js";
import { emptyHtml, dockerDown, actionButton, paintActions, paintBusy, setOff } from "../components/ui.js";
import { runPlatform } from "../activity/jobs.js";
import { Combo } from "../components/combo.js";

const MAX_BUFFER = 30000, MAX_DOM = 4000;
// The last lines of each service arrive for a moment once the stream opens, interleaved
const FIRST_LINES = 2500;
const LEVELS = { ERROR: "Error", WARN: "Warn", INFO: "Info", DEBUG: "Debug", OTHER: "Other" };
const TAILS = ["100", "500", "2000", "5000"];   // the last lines read of each service as the stream opens
const NO_LINES = "No lines", NOTHING = "Nothing running";
// Before any service is chosen, the one thing to do, as a title and its button: the picker, open
const CHOOSE = '<span>Choose the services to read</span><button class="btn" data-choose>Choose services</button>';
const LEVEL_RE = /\b(ERROR|FATAL|SEVERE|WARN(?:ING)?|INFO|DEBUG|TRACE)\b/;
// JSON logs carry their severity as one letter in "s"
const SEVERITY_RE = /"s":"([EWIDF])/;
// Stack trace lines: "at ...", "Caused by", "Suppressed:", "java.lang.FooException: ...", indented lines
const CONT_RE = /^(\s+at |\s+\.\.\.|\s*Caused by|\s*Suppressed:|\s{2,}\S|\t|([a-z][\w$]*\.)+[A-Z][\w$]*(Exception|Error|Throwable)\b)/;

// A level chip shows by its eye whether its lines are shown
const EYE = icon("eye");

const noCounts = () => ({ ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, OTHER: 0 });
let selected = [], tail = "500", buffer = [], counts = noCounts(), source = null, paused = false, renderTimer = null;
let arriving = null;    // the timer of the moment the first lines arrive; null once it is over
let clipped = false;    // the view draws only the latest lines it shows
let seen = new Set();   // the services whose lines came since the stream opened: a new start of theirs is a restart
const lastLevel = {};
let view, lines, empty;

const SKELETON = `
  <div class="page-head"><h1>Logs</h1></div>
  <div class="card list-card logs-card grow">
    <div class="list-tools">
      <div class="search grow">${icon("search")}
        <input id="log-search" type="search" placeholder="Search…" aria-label="Search the logs" aria-describedby="log-search-wrong" autocomplete="off">
        <label class="toggle-chip" data-tip="Regular expression"><input type="checkbox" id="log-regex" aria-label="Regular expression"><span aria-hidden="true">.*</span></label>
        <label class="toggle-chip" data-tip="Match case"><input type="checkbox" id="log-case" aria-label="Match case"><span aria-hidden="true">Aa</span></label>
        <label class="toggle-chip"><input type="checkbox" id="log-only" checked><span>Only matches</span></label>
      </div>
      <div class="list-filters">
        <button class="combo-btn" id="log-services" aria-haspopup="dialog"><span class="sr-only">Services: </span><span class="value"></span>${icon("chevron")}</button>
        <button class="combo-btn" id="log-tail" aria-haspopup="dialog" data-tip="Lines read for each service"><span class="sr-only">Lines: </span><span class="value"></span>${icon("chevron")}</button>
      </div>
    </div>
    <div class="list-tools">
      <div class="levels" id="log-levels">
        <label class="toggle-chip"><input type="checkbox" value="ALL" checked>${EYE}<span>All</span></label>${Object.entries(LEVELS).map(([level, label]) => `
        <label class="toggle-chip lv-${level.toLowerCase()}"><input type="checkbox" value="${level}">${EYE}<span>${label}</span><b id="log-count-${level}">0</b></label>`).join("")}
      </div>
      <div class="log-controls">
        <label class="toggle-chip"><input type="checkbox" id="log-follow" checked><span>Follow</span></label>
        <label class="toggle-chip"><input type="checkbox" id="log-wrap"><span>Wrap</span></label>
        <label class="toggle-chip" data-tip="Docker timestamp of each line"><input type="checkbox" id="log-time"><span>Time</span></label>
        <button class="icon-btn" id="log-pause" data-tip="Pause the stream" aria-label="Pause the stream">${icon("pause")}</button>
        <button class="icon-btn" id="log-clear" data-tip="Clear" aria-label="Clear">${icon("trash")}</button>
        <button class="icon-btn" id="log-download" data-tip="Download the visible lines" aria-label="Download">${icon("download")}</button>
      </div>
    </div>
    <p class="field-note err log-wrong" id="log-search-wrong" role="status" hidden>Not a valid regular expression</p>
    <div class="log-view no-ts grow" id="log-view" tabindex="0" role="log" aria-label="Log lines" aria-live="off">
      <div class="empty" id="log-empty"></div>
      <div id="log-lines"></div>
    </div>
    <div class="log-status" id="log-status"></div>
  </div>`;

// Only a container has logs: a service chosen without one is followed once it has one
export const hasLogs = (name) => (store.service(name)?.state.key ?? "absent") !== "absent";

// A service as a person reads it, as the server names it ("Gateway (API Management)"); its id until the state is read
const nameOf = (name) => store.service(name)?.name || name;

// Why there is nothing to choose: what runs is not known (Docker does not answer, or docker-compose.yml does not
// load), or no service has a container; "" when there is
function nothing() {
  if (!store.state) return "";
  if (dockerDown()) return store.state.wait;
  return Object.keys(store.state.services).some(hasLogs) ? "" : NOTHING;
}

// The levels shown: All, or the ones ticked. Ticking a level shows it alone, ticking more adds them; All, or
// unticking the last one, shows every level again
function pickLevel(input) {
  const all = $('#log-levels input[value="ALL"]'), levels = $$('#log-levels input:not([value="ALL"])');
  if (input === all) levels.forEach((l) => { l.checked = false; });
  all.checked = !levels.some((l) => l.checked);
  [all, ...levels].forEach((l) => {
    $("use", l.parentElement).setAttribute("href", l.checked || (all.checked && l !== all) ? "#i-eye" : "#i-eye-off");
  });
}

function levelOf(svc, msg) {
  if (CONT_RE.test(msg)) return { level: lastLevel[svc] || "OTHER", cont: true };
  let level = "OTHER";
  const head = msg.slice(0, 240);
  const m = head.match(LEVEL_RE);
  if (m) level = { FATAL: "ERROR", SEVERE: "ERROR", WARNING: "WARN", TRACE: "DEBUG" }[m[1]] || m[1];
  else {
    const mm = head.match(SEVERITY_RE);
    if (mm) level = { E: "ERROR", F: "ERROR", W: "WARN", I: "INFO", D: "DEBUG" }[mm[1]];
  }
  lastLevel[svc] = level;
  return { level, cont: false };
}

function hue(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

// The search as a pattern; one that is no valid regular expression says so under the search, and the field is marked invalid
function matcher() {
  const input = $("#log-search"), q = input.value, box = input.parentElement;
  const wrong = (bad) => {
    box.classList.toggle("invalid", bad);
    $("#log-search-wrong").hidden = !bad;
    if (bad) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  };
  wrong(false);
  if (!q) return null;
  try {
    return new RegExp($("#log-regex").checked ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), $("#log-case").checked ? "g" : "gi");
  } catch {
    wrong(true);
    return null;
  }
}

// What the toolbar keeps, read once: the search (null when empty or invalid), and whether a line shows. search()
// always starts from the beginning of the line, whatever the last match. A restart shows whatever level is kept: it
// says why the lines of its service go on after a gap; not among the matches of a search alone, which it is not.
function shownBy() {
  const re = matcher(), picked = $$("#log-levels input:checked").map((i) => i.value).filter((v) => v !== "ALL");
  const levels = new Set(picked.length ? picked : Object.keys(LEVELS));
  const only = re && $("#log-only").checked;
  return { re, keep: (e) => (e.mark ? !only : levels.has(e.level) && (!only || e.msg.search(re) >= 0)) };
}

// The search marked in a line: matched on the text as it is, every piece then escaped
function marked(msg, re) {
  if (!re) return esc(msg);
  let html = "", at = 0;
  for (const m of msg.matchAll(re)) {
    if (!m[0]) continue;   // an empty match has nothing to mark
    html += `${esc(msg.slice(at, m.index))}<mark>${esc(m[0])}</mark>`;
    at = m.index + m[0].length;
  }
  return html + esc(msg.slice(at));
}

// A line, or the mark of a restart (muted)
function lineHtml(e, re) {
  const t = e.ts ? new Date(e.ts) : null;
  const ts = t && !isNaN(t) ? t.toLocaleTimeString([], { hour12: false }) + "." + String(t.getMilliseconds()).padStart(3, "0") : "";
  return `<div class="log-line ${e.mark ? "restarted" : `${e.level}${e.cont ? " cont" : ""}`}"><span class="ts">${ts}</span>` +
    `<span class="svc" style="color:hsl(${hue(e.svc)} 55% 52%)" data-tip="${esc(e.svc)}">${esc(nameOf(e.svc))}</span><span class="msg">${e.mark ? esc(e.msg) : marked(e.msg, re)}</span></div>`;
}

function follow() {
  if ($("#log-follow").checked) view.scrollTop = view.scrollHeight;
}

// The lines of each level, then what the view does not show by itself: that it is paused, that it draws only the
// latest lines
function paint() {
  for (const k of Object.keys(counts)) $(`#log-count-${k}`).textContent = counts[k].toLocaleString();
  const status = [paused && "Paused", clipped && `latest ${MAX_DOM.toLocaleString()} lines shown`].filter(Boolean).join(", ");
  $("#log-status").textContent = status.charAt(0).toUpperCase() + status.slice(1);
}

// With nothing running, the next step: the platform's Start, as the banner offers it elsewhere
const START = actionButton({ key: "platform", verb: "start", label: "Start the platform", icon: "play", cls: "primary",
  attrs: 'data-busy data-platform="start"' });

// Whether the view offers the platform's Start: nothing has a container, and the platform has something to start. The
// banner leaves its own Start out meanwhile.
export const offersStart = () => !buffer.length && nothing() === NOTHING && !!store.state.platform.start.needed;

// Once lines are drawn. In their place: that the filters hide them all, that there is nothing to choose (Docker does
// not answer, nothing has a container: then the platform's Start), that no service is chosen, that their lines load, or
// that they have none.
function drawn() {
  empty.hidden = lines.children.length > 0;
  const html = buffer.length ? emptyHtml() : offersStart() ? `${NOTHING} ${START}` : nothing()
    || (!selected.length ? CHOOSE : source && arriving ? loading("Loading") : NO_LINES);
  $("#log-levels").classList.toggle("idle", !selected.length);   // no count before there is something to count
  if (setHTML(empty, html)) {
    paintBusy(empty);
    paintActions(empty);
  }
  paint();
  follow();
}

function renderAll() {
  const { re, keep } = shownBy();
  const shown = buffer.filter(keep);
  clipped = shown.length > MAX_DOM;
  lines.innerHTML = shown.slice(-MAX_DOM).map((e) => lineHtml(e, re)).join("");
  drawn();
}

function append(entries) {
  const { re, keep } = shownBy();
  const html = entries.filter(keep).map((e) => lineHtml(e, re)).join("");
  if (html) lines.insertAdjacentHTML("beforeend", html);
  const extra = lines.children.length - MAX_DOM;
  for (let i = 0; i < extra; i++) lines.firstElementChild.remove();
  clipped ||= extra > 0;
  drawn();
}

function onBatch(batch) {
  const entries = batch.map(([svc, ts, msg]) => ({ svc, ts, msg, ...levelOf(svc, msg) }));
  for (const e of entries) {
    counts[e.level]++;
    seen.add(e.svc);
  }
  add(entries);
}

// A container followed runs again after a new start: a muted line says so, where the lines of its service go on.
// The first start of a service since the stream opened is no restart: its lines just begin.
function onRestart({ service, timestamp }) {
  if (seen.has(service)) add([{ svc: service, ts: timestamp, msg: "Restarted", mark: true }]);
}

// New lines kept, then drawn, unless paused; the lines of a level are counted, not the marks of restarts
function add(entries) {
  buffer.push(...entries);
  if (buffer.length > MAX_BUFFER) for (const e of buffer.splice(0, buffer.length - MAX_BUFFER)) if (!e.mark) counts[e.level]--;
  if (paused) {   // the lines wait for Resume: the view no longer says they load
    empty.hidden = true;
    return paint();
  }
  if (arriving) {
    // The first lines of each service arrive interleaved: keep them in time order
    buffer.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    clearTimeout(renderTimer);
    renderTimer = setTimeout(renderAll, 120);
  } else {
    append(entries);
  }
}

// Nothing is kept from before: the stream starts with the last lines of each service. Once they had the time to
// arrive, a service that wrote nothing says so instead of loading, paused or not.
function reset() {
  buffer = []; counts = noCounts(); seen = new Set();
  clearTimeout(arriving);
  arriving = setTimeout(() => {
    arriving = null;
    if (!paused) renderAll();
    else if (!buffer.length) drawn();
  }, FIRST_LINES);
  renderAll();
}

// The stream of what is chosen. The server follows each service: one without a container from the moment it has one,
// one whose container stops until it runs again, after a new start (the `restarted` event, then its lines). It is
// opened again only when the choice changes.
function connect() {
  source?.close();
  source = selected.length ? new EventSource(`/api/logs?services=${selected.join(",")}&tail=${tail}`) : null;
  if (source) {
    // The browser connects again by itself after a cut, and the last lines are sent again
    source.onopen = reset;
    source.onmessage = (e) => onBatch(JSON.parse(e.data));
    source.addEventListener("restarted", (e) => onRestart(JSON.parse(e.data)));
  }
  reset();
}

const running = () => Object.keys(store.state.services).filter((name) => store.service(name).state.running);

// The sections of the picker, each {label, services}: the platform's groups, then the playground and the add-ons that are on or
// run, or hold a service chosen, in the order of the Stack. The gateway of an add-on is a gateway of the group that runs its
// image (`gateway.group`, which the server tells from the images, never from names): the add-on's services are listed in that
// group's section after the group's own, its gateway first, and in no section of their own (the Hybrid gateway and its Redis in
// API Management). It stays an add-on all the same, turned on and off on its own.
function sections(state) {
  const shown = (item) => item.on || item.up || item.services.some((name) => selected.includes(name));
  const groups = state.platform.groups, joined = (addon) => groups.some((g) => g.id === addon.gateway?.group);
  const gatewayFirst = (addon) => [...addon.services].sort((a, b) => (b === addon.gateway.service) - (a === addon.gateway.service));
  return [
    // One section for a group; the gateway of an add-on is a part of it, under a title of its own that says whom it serves: the
    // add-on's name, the account and the environment, when it runs for one
    ...groups.map((g) => ({ label: g.name, services: g.services,
      parts: state.addons.filter((addon) => addon.gateway?.group === g.id && shown(addon))
        .map((addon) => ({ heading: `${addon.name}${serving ? ` (${serving})` : ""}`, services: gatewayFirst(addon) })) })),
    ...[...state.playgrounds, ...state.addons.filter((addon) => !joined(addon))].filter(shown)
      .map((item) => ({ label: item.name, services: item.services, parts: [] })),
  ];
}

// Whom the gateway that runs here serves, "Acme Cloud, Development" (the account and the environment), read from the checks of the
// accounts, which the Dashboard and the Gravitee Cloud page keep: asked here when the picker opens and they are not known yet.
// Nothing while no gateway runs here or no account is registered.
let serving = "";
async function loadServing() {
  const found = [];
  for (const platform of store.platforms) {
    let check = store.cloud.checks.get(platform.id);
    if (!check) {
      try {
        check = await api(`/api/platforms/${platform.id}/check`, {});
        store.cloud.checks.set(platform.id, check);
      } catch {
        continue;
      }
    }
    const here = (check.gateways || []).find((gateway) => gateway.here);
    if (here) found.push(`${platform.name}, ${(check.environments || []).find((env) => env.id === here.environmentId)?.name || here.name}`);
  }
  serving = found.join("; ");
}

// The services in the picker, by section. What can be chosen has a container; what was chosen before it lost its own stays,
// to be unticked. With nothing to choose, it says so.
async function chooseServices() {
  await loadServing();
  Combo.open($("#log-services"), {
    multi: true, plain: true, current: selected, empty: NOTHING,
    quick: [
      { label: "All running", values: running },
      { label: "Gravitee only", values: () => running().filter((name) => store.service(name).registry) },
      { label: "None", values: () => [] },
    ],
    groups(q) {
      const state = store.state;
      const listed = (names) => names.filter((name) => hasLogs(name) || selected.includes(name))
        .map((name) => ({ value: name, name: state.services[name].label, meta: name, dot: state.services[name].state.key }))
        .filter((it) => `${it.name} ${it.value}`.toLowerCase().includes(q));
      return sections(state).map((section) => ({
        label: section.label,
        items: [...listed(section.services), ...section.parts.flatMap((part) => {
          const items = listed(part.services);
          return items.length ? [{ heading: part.heading }, ...items] : [];
        })],
      }));
    },
    choose(values) {
      selected = values;
      renderLabels();
      connect();
    },
  });
}

function chooseTail() {
  Combo.open($("#log-tail"), {
    plain: true, current: tail,
    groups: () => [{ items: TAILS.map((value) => ({ value, name: `Last ${Number(value).toLocaleString()}` })) }],
    choose(value) {
      tail = value;
      renderLabels();
      connect();
    },
  });
}

// The services chosen by their names, or how many when the names do not fit the button: its tooltip then names them. While what
// runs is not known, the picker says why, and waits, unless something chosen is left to untick (setOff).
// The lines' column of names is as wide as the longest of them, up to a point.
function renderLabels() {
  const picker = $("#log-services"), value = $(".value", picker), chosen = selected.map(nameOf);
  value.textContent = chosen.length ? chosen.join(", ") : "Choose services";
  const long = value.scrollWidth > value.clientWidth;
  if (long) value.textContent = `${chosen.length} services`;
  setOff(picker, !!store.state && dockerDown() && !selected.length, nothing(), long ? chosen.join(", ") : "");
  view.style.setProperty("--svc-width", `${Math.min(Math.max(...chosen.map((n) => n.length), 8), 34)}ch`);
  $("#log-tail .value").textContent = `Last ${Number(tail).toLocaleString()}`;
}

// What another page asks to read: the logs of these services that have a container
export function selectLogs(serviceNames) {
  selected = [...new Set(serviceNames)].filter(hasLogs);
  Combo.close();
  renderLabels();
  connect();
}

// The picker and the view follow the state. While what runs is not known, the picker says why on its button, which
// waits, unless something chosen is left to untick; with nothing to choose, it opens on "Nothing running". The view
// says either in place of the lines. Paused, the view stays as it is until Resume.
function render() {
  renderLabels();
  if (!paused) drawn();
}

function init(section) {
  section.innerHTML = SKELETON;
  view = $("#log-view"); lines = $("#log-lines"); empty = $("#log-empty");
  renderLabels();
  drawn();
  empty.addEventListener("click", (e) => {
    if (e.target.closest("[data-platform]")) runPlatform("start");
    else if (e.target.closest("[data-choose]") && store.state) chooseServices();
  });
  $("#log-services").addEventListener("click", () => { if (store.state) chooseServices(); });
  $("#log-tail").addEventListener("click", chooseTail);

  let searchTimer;
  $("#log-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderAll, 150); });
  ["#log-regex", "#log-case", "#log-only"].forEach((s) => $(s).addEventListener("change", renderAll));
  $("#log-levels").addEventListener("change", (e) => { pickLevel(e.target); renderAll(); });
  $("#log-wrap").addEventListener("change", (e) => view.classList.toggle("wrap", e.target.checked));
  $("#log-time").addEventListener("change", (e) => view.classList.toggle("no-ts", !e.target.checked));
  $("#log-follow").addEventListener("change", follow);
  view.addEventListener("scroll", () => {
    const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 30;
    if ($("#log-follow").checked !== atBottom) $("#log-follow").checked = atBottom;
  });
  $("#log-pause").addEventListener("click", (e) => {
    const button = e.currentTarget;
    paused = !paused;
    button.innerHTML = icon(paused ? "play" : "pause");
    button.dataset.tip = paused ? "Resume the stream" : "Pause the stream";
    button.setAttribute("aria-label", button.dataset.tip);
    if (paused) return paint();
    renderAll();   // the lines that arrived meanwhile
  });
  $("#log-clear").addEventListener("click", () => { buffer = []; counts = noCounts(); renderAll(); });
  // The lines the view shows, as the services wrote them: without the "Restarted" marks, which no service wrote
  $("#log-download").addEventListener("click", () => {
    const { keep } = shownBy();
    const text = buffer.filter((e) => !e.mark && keep(e)).map((e) => `${e.ts} ${e.svc} ${e.msg}`).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = `gravitee-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.log`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

export const logsPage = {
  id: "logs", pattern: /^#\/logs$/, nav: "logs", title: "Logs",
  init,
  // The stream is open while the page is shown and the window seen: it opens again with the last lines of each service when it
  // shows again. The names of the services chosen elsewhere are measured once the button is in view.
  show() {
    renderLabels();
    if (selected.length && !source) connect();
    follow();
  },
  hide: () => disconnect(),
  render,
};

function disconnect() {
  source?.close();
  source = null;
}

document.addEventListener("visibilitychange", () => {
  if (router.current?.page.id !== "logs") return;
  if (document.hidden) disconnect();
  else if (selected.length && !source) connect();
});
