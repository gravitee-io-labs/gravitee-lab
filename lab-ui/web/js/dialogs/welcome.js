// Welcome to Gravitee Lab: How it works at the top of the rail at the left, then the steps from a fresh machine to a running platform, each
// with its state: your machine (what it needs, each line with its fix), where API Management runs (the one choice the Lab asks), and the start
// (the platform, followed until it runs, then its consoles and the playgrounds). A step after a machine that keeps the platform from starting is locked, and says why. A first visit opens
// it on your machine, unless where API Management runs was chosen on this machine already; the info button of the menu on How it works, or on
// your machine while a line of it is red; the notices about Docker on your machine. What it says follows the lab while it is open.

import { $, esc, icon, setHTML, recall, remember, api, store } from "../core.js";
import { paintBusy, licenseForm, contentLead, setOff, statusHtml, progressBar, paintProgress, safeHref } from "../components/ui.js";
import { runPlatform, showOutput } from "../activity/jobs.js";
import { CHOICES, coreOf, onCloud, movable, linkedPlaces, chooseCore, environmentControl, pickEnvironment, runCore, gatewayWhy } from "../platform/core-choice.js";
import { License } from "./license.js";
import { machine, machineBlocked, machineToFix, machineWorking, machineRowsHtml, machineSummary, watchMachine, unwatchMachine, refreshMachine, actMachine } from "./machine.js";
import { REGISTER } from "./platform.js";

const REPOSITORY = "https://github.com/gravitee-io-labs/gravitee-lab";   // where the source of the Lab is
const TROUBLESHOOTING = `${REPOSITORY}/blob/main/docs/troubleshooting.md`;
const SEEN = "welcomed";   // the browser keeps that it was shown
const FROM = "from a catalog";   // where a playground, a setup or an add-on lives, in the layers: no path, which the catalog decides

// The product that can run in the Cloud, by its name
const product = () => movable()?.name || "";

// From the top layer to the core, as they stack: [icon, name, what it is, where it lives, its parts]. The core is the platform, all its
// products, whichever runs in the Cloud: it says only where that one runs. What is on top of it comes from a catalog, whichever.
function layers(state) {
  const where = onCloud() ? ` ${product()} runs in Gravitee Cloud: ${coreOf().name}, with a hybrid gateway on this machine.` : "";
  const bottom = ["grid", "Core", `The Gravitee platform, always underneath.${where}`, "docker-compose.yml", (state?.platform.groups || []).map((g) => g.name)];
  return [
    ["flask", "Playground", "A use case: its apps, sample data and a guide. It brings the setups and add-ons it needs. One is on at a time.", FROM],
    ["layers", "Setup", "A ready-made group of add-ons, turned on and off as one.", FROM],
    ["addons", "Add-on", "One capability next to the platform. It brings the add-ons it needs.", FROM],
    bottom,
  ];
}

// The on file as the Lab UI writes it: a playground first, then an add-on turned on besides what it brings. A generic page names no item
// of the catalog: the paths say where they are, and a path is shown here because it is what a person reads in that file.
const EXAMPLE = ["include:", "  - .lab/catalogs/<catalog>/playgrounds/<id>/compose.yml", "  - .lab/catalogs/<catalog>/addons/<id>/compose.yml"].join("\n");

// What a Start or a Stop goes through: [step, whether it is a file or a command]
const FLOW = [["Start or Stop", false], [".lab/on.yml", true], ["docker compose up", true], ["Docker containers", false]];

// A page's name leads to it
const page = (name, hash) => `<a class="text-link" href="${hash}">${name}</a>`;

// Whether a person is invited to a Gravitee Cloud account: not with a license, nor as a Gravitee employee (GRAVITEE_EMPLOYEE), nor before
// the license is read
let invited = false;

// Where API Management runs, as two cards of one choice. Gravitee Cloud says what it needs before it can be chosen (an account, an
// environment set up), or the environment it is, with the picker of the linked ones when there are several; a person with no license, and
// no Gravitee employee, is invited to an account.
function coreChoice() {
  const cloud = onCloud(), name = (key) => `welcome-core-${key}`, picker = environmentControl(), why = gatewayWhy();
  const where = cloud ? (picker ? "" : `<span class="pill">${esc(coreOf().name)}</span>`)
    : !store.platforms.length ? '<span class="muted">Needs a Gravitee Cloud account</span>'
      : linkedPlaces().length ? "" : '<span class="muted">Needs an environment set up</span>';
  // The Cloud card cannot be chosen while nothing can run a gateway for it: it keeps the pointer and the keyboard, and says why
  const card = (key) => {
    const choice = CHOICES[key], on = (key === "cloud") === cloud, off = key === "cloud" && !!why ? ` aria-disabled="true"` : "";
    return `<label class="core-card" for="${name(key)}"${off}${off ? ` data-why="${esc(why)}"` : ""}><input type="radio" id="${name(key)}" name="welcome-core" value="${key}"${off}${on ? " checked" : ""}>
      <span class="cta-icon">${icon(choice.icon)}</span>
      <span class="core-card-text"><strong>${choice.card}</strong><span>${esc(choice.text(product()))}</span>${
        key === "cloud" && where ? `<span class="core-card-where">${where}</span>` : ""}</span></label>`;
  };
  const invite = !store.platforms.length && invited
    ? `<a class="text-link" href="${REGISTER}" target="_blank" rel="noopener">Create a Gravitee Cloud account</a>` : "";
  const more = picker || invite ? `<div class="core-more">${picker}${invite}</div>` : "";
  return `<div class="core-cards" role="radiogroup" aria-labelledby="welcome-core">${card("machine")}${card("cloud")}</div>${more}`;
}

const lead = () => (onCloud() ? `The platform, ${product()} in Gravitee Cloud, and what runs on top of it, in layers you turn on and off.`
  : "The platform and what runs on top of it, in layers you turn on and off.");

const layersHtml = (state) => layers(state).map(([symbol, name, text, path, parts = []]) => `
      <li class="layer"><span class="layer-icon">${icon(symbol)}</span>
        <span class="layer-text"><strong>${name}</strong><span>${esc(text)}</span>
          ${parts.length ? `<span class="layer-parts">${parts.map((p) => `<span class="pill">${esc(p)}</span>`).join("")}</span>` : ""}</span>
        <code>${esc(path)}</code></li>`).join("");

// Each part of the menu, and what its pages are for
const PAGES = [
  [page("Dashboard", "#/dashboard"), "Whether the platform is all right and what is not applied, which version of each product runs here and in Gravitee Cloud, what else is on, and what the lab uses of your machine"],
  ["Library", `${page("Playgrounds", "#/playgrounds")}, ${page("Setups", "#/setups")} and ${page("Add-ons", "#/addons")}: what each one is and runs, to turn on and off`],
  ["Catalogs", `${page("Catalogs", "#/catalogs")}: where they come from, to add and to update`],
  ["Monitor", `${page("Containers", "#/containers")}: every container with its version, ports and consoles to open. ${page("Logs", "#/logs")}: several services at once`],
  ["Configure", `${page("Images", "#/images")}: the version of every image. ${page("Plugins", "#/plugins")}: the plugins of each product`],
];

// --- The steps ----------------------------------------------------------------------------------------------------

let current = "machine";
let choseWhere = false;   // Continue was pressed on where API Management runs: it is done, whichever was kept
let watching = false;   // Your machine shows, and is asked again on its timer

// In order: your machine, where API Management runs when a product can run in the Cloud, the start. How it works is apart, last.
const steps = () => ["machine", ...(movable() ? ["core"] : []), "start"];

const stopped = () => {
  const core = coreOf();
  return onCloud() ? !!core.start?.needed && !!core.start.enabled && !core.stop?.needed : store.state?.platform?.state?.key === "stopped";
};
const running = () => (onCloud() ? !coreOf().start?.needed : store.state?.platform?.state?.key === "running");
// The platform's last Start, while it is the lab's job; on its way while it runs or its containers get healthy; failed when it ended
// badly or left a container with an issue, said by the job or else by the groups
const startJob = () => { const job = store.state?.job; return job?.for === "platform" && job.verb === "start" ? job : null; };
const starting = () => !running() && (startJob()?.done === false || store.state?.platform?.state?.key === "starting");
function failure() {
  if (running() || starting()) return "";
  const job = startJob();
  if (job?.done && !job.ok && job.error !== "Canceled") return job.error || "The start failed";
  if (store.state?.platform?.state?.key !== "issue") return "";
  const group = store.state.platform.groups.find((g) => g.note && ["err", "warn"].includes(g.note_tone));
  return group ? `${group.name}: ${group.note}` : "A container has an issue";
}

// Each step as the rail draws it: {state: done, todo, attention, checking or locked, line: what it says under its name}
function stepOf(id) {
  if (id === "machine") {
    if (!machine.report) return { state: "checking", line: "Checking" };
    const working = machineWorking();
    if (working) return { state: "checking", line: `Waiting for ${working.title}` };
    if (machineBlocked()) return { state: "attention", line: `${machineToFix()} to fix` };
    return { state: "done", line: machineToFix() ? `Ready, ${machineToFix()} to look at` : "Ready" };
  }
  if (machineBlocked()) return { state: "locked", line: "Once your machine is ready" };
  if (id === "core") return { state: coreOf().chosen || choseWhere ? "done" : "todo", line: onCloud() ? "Gravitee Cloud" : "This machine" };
  if (running()) return { state: "done", line: "Running" };
  if (starting()) return { state: "checking", line: "Starting" };
  return failure() ? { state: "attention", line: "Did not start" } : { state: "todo", line: "Not started yet" };
}

const TITLE = { machine: "Your machine", core: () => product(), start: "Start", guide: "How it works" };
const title = (id) => (typeof TITLE[id] === "function" ? TITLE[id]() : TITLE[id]);

function railHtml() {
  const mark = (id, i, { state }) => ({ done: icon("check"), attention: icon("alert"), checking: '<span class="spinner" aria-hidden="true"></span>' }[state]
    || `<span>${i + 1}</span>`);
  const items = steps().map((id, i) => {
    const step = stepOf(id), locked = step.state === "locked";
    return `<li><button type="button" class="welcome-step" data-step="${id}" data-state="${step.state}"${id === current ? ' aria-current="step"' : ""}${
      locked ? ` aria-disabled="true" data-why="Fix your machine first"` : ""}>
      <span class="welcome-mark">${mark(id, i, step)}</span>
      <span class="welcome-step-text"><strong>${esc(title(id))}</strong><span>${esc(step.line)}</span></span></button></li>`;
  }).join("");
  return `<button type="button" class="welcome-step guide" data-step="guide"${current === "guide" ? ' aria-current="step"' : ""}>
      <span class="welcome-mark">${icon("book")}</span><span class="welcome-step-text"><strong>How it works</strong><span>Layers, Start and Stop, pages</span></span></button>
    <ol class="welcome-steps">${items}</ol>`;
}

function machinePanel() {
  const summary = machineSummary();
  return `<h2>Your machine</h2>
    <p class="welcome-lead">Gravitee Lab runs the platform in Docker, on this machine.</p>
    <p class="machine-summary" data-tone="${summary.tone}" aria-live="polite">${summary.html}</p>
    <ul class="machine-checks">${machineRowsHtml()}</ul>`;
}

function corePanel() {
  return `<h2 id="welcome-core">Where does ${esc(product())} run?</h2>
    <p class="welcome-lead">You can change it later.</p>
    <div class="welcome-core">${coreChoice()}</div>`;
}

// The start: the platform started here and followed until it runs, then its consoles, each with its demo sign-in; the license while there
// is none
function startPanel() {
  const cloud = onCloud(), why = failure();
  const unlicensed = store.state?.license?.status === "none";
  const license = unlicensed ? `<div class="welcome-license">${icon("key")}<span>No license: Enterprise features are off</span>
      <button class="btn" ${licenseForm()}>Set license</button></div>` : "";
  const first = cloud ? `Start all starts the hybrid gateway of ${esc(coreOf().name)} and the rest of the platform on this machine.`
    : "Its first start downloads about 15 GB of images: about 10 to 15 minutes.";
  if (running()) {
    return `<h2>Your platform is running</h2>
      <ul class="start-list">${consoles().map(consoleItem).join("") || '<li class="empty">No console runs</li>'}</ul>${license}`;
  }
  if (starting()) {
    return `<h2>Starting the platform</h2>
      <p class="welcome-lead">${first}</p>
      <div class="welcome-starting">${statusHtml("starting", "Starting")}${progressBar("platform")}</div>${license}`;
  }
  if (why) {
    return `<h2>The platform did not start</h2>
      <p class="welcome-lead err-text">${esc(why)}</p>${license}`;
  }
  return `<h2>Start the platform</h2>
    <p class="welcome-lead">${first}</p>${license}`;
}

// The consoles of the platform that run, the current ones (the older ones are on the Dashboard), product by product as the platform lists
// them, and with the product in Gravitee Cloud its console there first
function consoles() {
  const state = store.state, links = (state.links || []).filter((link) => link.up && !link.legacy);
  const local = state.platform.groups.flatMap((g) => links.filter((link) => (g.services || []).includes(link.service)));
  const core = coreOf(), cloud = onCloud() ? store.platforms.find((p) => p.id === core.platform?.id) : null;
  return [...(cloud?.url ? [{ label: core.name, url: cloud.url }] : []), ...local];
}

const consoleItem = (link) => `<li class="start-item"><span class="start-num">${icon("external")}</span>
    <span class="start-text"><strong>${esc(link.label)}</strong><span>${link.username ? `<code>${esc(link.username)}</code> / <code>${esc(link.password || "")}</code>` : esc(link.url)}</span></span>
    <a class="btn" href="${esc(safeHref(link.url))}" target="_blank" rel="noopener" aria-label="Open ${esc(link.label)}">Open</a></li>`;

function guidePanel() {
  return `<h2>How Gravitee Lab works</h2>
    <p class="welcome-lead">${esc(lead())}</p>
    <ol class="layers">${layersHtml(store.state)}</ol>

    <h3>Start and Stop</h3>
    <p><em>Start</em> turns a playground, a setup or an add-on on and runs it; <em>Stop</em> stops it and turns it off.
      Stopping the platform keeps your choices: they come back at its next start.</p>

    <h3>Under the hood: Docker Compose</h3>
    <ol class="flow">${FLOW.map(([step, mono]) => `<li><span class="pill">${mono ? `<code>${step}</code>` : step}</span></li>`).join("")}</ol>
    <p>Each layer is a Compose file. The Lab UI lists what is on, a setup as its add-ons, in
      <code>.lab/on.yml</code>, which Compose reads with the core. Without the Lab UI: edit that file and run
      <code>make all</code>.</p>
    <p class="sub">Example of <code>.lab/on.yml</code></p>
    <pre class="snippet welcome-code">${esc(EXAMPLE)}</pre>

    <h3>Yours or shared</h3>
    <p><em>New playground</em>, <em>New setup</em> and <em>New add-on</em> make one from a form or with an AI agent.
      Personal stays on this machine until you share it to a catalog.</p>

    <h3>Where to go</h3>
    <dl class="pairs">${PAGES.map(([name, text]) => `<div><dt>${name}</dt><dd>${text}</dd></div>`).join("")}</dl>`;
}

const PANELS = { machine: machinePanel, core: corePanel, start: startPanel, guide: guidePanel };

// The foot of a step: Back, then what moves on. On the start: Done, to go on later, then Start the platform while it is stopped (Try again
// once it failed, with its output), nothing more while it starts, and Discover the playgrounds once it runs (paintLead writes into it).
function footHtml() {
  const order = steps(), at = order.indexOf(current), back = at > 0 ? '<button type="button" class="btn" data-back>Back</button>' : "";
  if (current === "guide") return '<span class="grow"></span><button type="button" class="btn primary" data-close>Close</button>';
  if (current === "machine") {
    const blocked = machineBlocked() || !machine.report;
    return `<span class="grow"></span><button type="button" class="btn${blocked ? "" : " primary"}" data-next${
      blocked ? ` aria-disabled="true" data-why="${esc(!machine.report ? "Checking your machine" : machineWorking()?.detail || "Fix the lines in red first")}"` : ""}>Continue</button>`;
  }
  if (current === "core") return `${back}<span class="grow"></span><button type="button" class="btn primary" data-next>Continue</button>`;
  const gap = onCloud() && gatewayWhy() ? ` aria-disabled="true" data-why="${esc(gatewayWhy())}"` : "";
  const done = '<button type="button" class="btn" data-close>Done</button>';
  if (running()) return `${back}<span class="grow"></span>${done}<a class="btn primary" href="#/playgrounds">Discover the playgrounds</a>`;
  if (starting() || !stopped() && !failure()) return `${back}<span class="grow"></span>${done}`;
  const output = failure() && startJob() ? `<button type="button" class="btn" data-output>${icon("logs")}Show output</button>` : "";
  return `${back}<span class="grow"></span>${done}${output}
    <button class="btn primary" data-start data-busy${gap}>${icon("play")}${failure() ? "Try again" : onCloud() ? "Start all" : "Start the platform"}</button>`;
}

const dialog = document.createElement("dialog");
dialog.className = "welcome-dialog";
dialog.setAttribute("aria-labelledby", "welcome-title");
dialog.innerHTML = `
  <div class="welcome-frame">
    <aside class="welcome-rail">
      <div class="welcome-brand"><span class="welcome-logo"></span><h2 id="welcome-title">Welcome to Gravitee Lab</h2></div>
      <nav class="welcome-rail-steps" aria-label="Steps"></nav>
      <p class="welcome-source">AGPL-3.0 <a class="text-link" href="${REPOSITORY}" target="_blank" rel="noopener">Source</a>
        <a class="text-link" href="${TROUBLESHOOTING}" target="_blank" rel="noopener">Troubleshooting</a></p>
    </aside>
    <section class="welcome-main">
      <button class="icon-btn dialog-x welcome-x" data-close aria-label="Close" data-tip="Close">${icon("close")}</button>
      <div class="welcome-panel welcome"></div>
      <div class="dialog-actions welcome-actions"></div>
    </section>
  </div>`;
document.body.append(dialog);

// While the catalogs are not there, Discover the playgrounds stays where it is, disabled, saying why in the words of the lists; a failed download has
// a Try again before it, and a lab with no catalog the button that adds the one that is listed. Written into the nodes, never the foot again:
// the keyboard stays where it was and nothing moves under the pointer when the download ends.
function paintLead(actions, state) {
  const lead = $('a[href="#/playgrounds"]', actions);
  if (!lead) return;
  const { ready, why, extra } = contentLead(state);
  setOff(lead, !ready, why);
  if ((actions._extra || "") === extra) return;
  actions._extra = extra;
  actions._extraEl?.remove();
  actions._extraEl = null;
  if (!extra) return;
  const holder = document.createElement("div");
  holder.innerHTML = extra;
  actions._extraEl = holder.firstElementChild;
  lead.before(actions._extraEl);
}

// What follows the lab, written only where it changed: the keyboard stays where it was
function render() {
  if (!steps().includes(current) && current !== "guide") current = "machine";
  if (current !== "machine" && current !== "guide" && stepOf(current).state === "locked") current = "machine";
  const logo = $(".welcome-logo", dialog);
  if (!logo.firstChild) logo.innerHTML = ($(".sidebar .logo")?.outerHTML || "").replace(/<defs>[\s\S]*?<\/defs>/, "");
  setHTML($(".welcome-rail-steps", dialog), railHtml());
  const panel = $(".welcome-panel", dialog), cards = () => $(".core-cards", panel);
  const focused = !!cards()?.contains(document.activeElement);
  if (panel.dataset.step !== current) panel.scrollTop = 0;
  panel.dataset.step = current;
  setHTML(panel, PANELS[current]());
  for (const input of panel.querySelectorAll('input[name="welcome-core"]')) input.checked = input.value === (onCloud() ? "cloud" : "machine");
  if (focused && !cards().contains(document.activeElement)) $("input:checked", cards())?.focus();
  const actions = $(".welcome-actions", dialog);
  if (setHTML(actions, footHtml())) {
    paintBusy(actions);
    actions._extra = "";
    actions._extraEl = null;
  }
  paintLead(actions, store.state);
  paintProgress(dialog);
  // Your machine is asked again on a timer while it shows, and no longer once another step does
  if (current === "machine" && !watching) {
    watching = true;
    watchMachine(render);
  } else if (current !== "machine" && watching) {
    watching = false;
    unwatchMachine();
  }
}

function go(step) {
  current = step;
  render();
  $(".welcome-actions .btn.primary, .welcome-actions .btn:last-child", dialog)?.focus();
}

// Continue on where API Management runs keeps what shows, and moves on at once: the save follows the lab while the next step shows
function next() {
  const order = steps(), at = order.indexOf(current);
  if (current === "core") {
    choseWhere = true;
    if (!onCloud()) chooseCore("machine");
  }
  go(order[Math.min(at + 1, order.length - 1)]);
}

dialog.addEventListener("click", (e) => {
  const on = (selector) => e.target.closest(selector);
  if (on('[aria-disabled="true"]')) return;   // a control that says why it waits does nothing
  if (on(".welcome-step")) return go(on(".welcome-step").dataset.step);
  if (on("[data-next]")) return next();
  if (on("[data-back]")) { const order = steps(); return go(order[Math.max(order.indexOf(current) - 1, 0)]); }
  if (on("[data-act]")) return actMachine(on("[data-act]"));
  if (on('[data-close], a[href^="#/"]')) dialog.close();
  else if (on("[data-start]")) { if (onCloud()) runCore("start"); else runPlatform("start"); }
  else if (on("[data-output]")) { dialog.close(); showOutput(startJob().id); }
  else if (on("[data-core-env]")) pickEnvironment(on("[data-core-env]"));
  else if (on("[data-license]")) { dialog.close(); License.show({ form: true }); }
});
// A card chosen, with the keyboard too: This machine is kept at once, Gravitee Cloud once an environment is linked
dialog.addEventListener("change", (e) => {
  if (e.target.name !== "welcome-core") return;
  if (e.target.value === "cloud" && gatewayWhy()) return render();   // a key reached it all the same: the card is not chosen
  choseWhere = true;
  chooseCore(e.target.value, { anchor: e.target.closest(".core-card") });
});
dialog.addEventListener("close", () => {
  watching = false;
  unwatchMachine();
  remember(SEEN, "1");
});
// What it says follows the lab while it is open: the choice made meanwhile, the platform started meanwhile
store.subscribe(() => { if (dialog.open) render(); });

// On a step, or on How it works: on your machine instead while it keeps the platform from starting
async function show(step = null) {
  if (!step) {
    if (!machine.report) await refreshMachine(true);
    step = stepOf("machine").state === "done" ? "guide" : "machine";
  }
  current = step;
  render();
  if (!dialog.open) dialog.showModal();
  $(".welcome-actions .btn.primary, .welcome-actions .btn:last-child", dialog)?.focus();
  store.refreshPlaces();
  api("/api/license").then((info) => {
    invited = !info.error && !info.present && !info.employee;
    if (dialog.open) render();
  }, () => {});
}

$("#welcome").addEventListener("click", () => show());
// Anywhere in the page, a control with data-machine opens it on your machine
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-machine]")) return;
  e.preventDefault();
  show("machine");
});

// The first visit opens it on your machine, once the page behind it has drawn, unless where API Management runs was chosen on this machine
// already
function init() {
  if (!recall(SEEN) && !coreOf().chosen) show("machine");
}

export const Welcome = { init, show };
