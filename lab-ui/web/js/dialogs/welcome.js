// How Gravitee Lab works: where API Management runs (the one choice it asks: This machine or Gravitee Cloud), what to do first, its layers,
// what Start and Stop mean and the Docker Compose underneath. Shown on a first visit, unless that was chosen on this machine already, then
// from the info button of the menu. What it names of the lab (the groups of the platform, the environment of API Management, an example)
// and what it offers first (Start, the license, a playground to choose once the catalogs are there) come from the state, and follow it while it
// is open: the foot draws the same slots in every state, and what the catalogs do changes them in place.

import { $, $$, esc, icon, setHTML, recall, remember, api, store } from "../core.js";
import { paintBusy, licenseForm, contentLead, setOff } from "../components/ui.js";
import { runPlatform } from "../activity/jobs.js";
import { CHOICES, coreOf, onCloud, movable, linkedPlaces, chooseCore, environmentControl, pickEnvironment, runCore, gatewayWhy } from "../platform/core-choice.js";
import { License } from "./license.js";
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

// What to do first, in three lines, for where API Management runs: what is started first, where the consoles are, then where a playground
// says what it needs. A generic page names no playground: the platform is a hub, each item says its own needs.
const start = () => (onCloud() ? [
  `${page("Start all", "#/dashboard")} starts the hybrid gateway of ${esc(coreOf().name)} and the rest of the platform on this machine.`,
  `Open a console from the ${page("Dashboard", "#/dashboard")}.`,
  "A playground's page says whether it can go to Gravitee Cloud, and what it needs.",
] : [
  "Start the platform (about 10 to 15 minutes the first time).",
  `Open a console from the ${page("Dashboard", "#/dashboard")}.`,
  "A playground's page lists what it needs: a license, versions, settings.",
]);

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

const lead = () => (onCloud() ? `The Gravitee platform, ${product()} in Gravitee Cloud and the rest on your machine, and what runs on top of it, in layers you turn on and off.`
  : "The Gravitee platform on your machine, and what runs on top of it, in layers you turn on and off.");

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

// What follows the choice is drawn apart (`render`): the choice, the first steps, the lead and the layers. Without a product that can run
// in the Cloud there is no choice to ask.
function body() {
  return `
    ${movable() ? `<h3 id="welcome-core">Where does ${esc(product())} run?</h3>
    <div class="welcome-core"></div>` : ""}

    <h3>Start here</h3>
    <ol class="welcome-start"></ol>

    <p class="welcome-lead"></p>
    <ol class="layers"></ol>

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
    <dl class="pairs">${PAGES.map(([name, text]) => `<div><dt>${name}</dt><dd>${text}</dd></div>`).join("")}</dl>

    <p class="sub welcome-source">Gravitee Lab, AGPL-3.0, <a class="text-link" href="${REPOSITORY}" target="_blank" rel="noopener">Source</a>,
      <a class="text-link" href="${TROUBLESHOOTING}" target="_blank" rel="noopener">Troubleshooting</a></p>`;
}

// What to do first, set a license while there is none (the License dialog, on its form). While nothing is on, a
// playground: it brings the platform and the versions it needs, where starting the platform alone would start the
// default ones first. While the platform is stopped and something is on, starting it; with API Management in the Cloud, Start all
// while nothing of what it starts runs, which waits for a gateway add-on when the Cloud needs one. Close otherwise. Enter stays on Close: a
// first visit does not start anything by itself. Which slots there are depends on the lab alone; what the catalogs do to them (paintLead)
// is written into those nodes.
function foot(state) {
  const cloud = onCloud(), core = coreOf();
  const stopped = cloud ? !!core.start?.needed && !!core.start.enabled && !core.stop?.needed : state?.platform.state.key === "stopped";
  const unlicensed = state?.license?.status === "none";
  const chosen = [...(state?.playgrounds || []), ...(state?.addons || [])].some((x) => x.on);
  const lead = !chosen ? "choose" : stopped ? "start" : "";
  const gap = cloud && gatewayWhy() ? ` aria-disabled="true" data-why="${esc(gatewayWhy())}"` : "";
  return `${unlicensed ? `<span class="welcome-license">No license: Enterprise features are off
      <button class="btn" ${licenseForm()}>Set license</button></span>` : ""}
    <button class="btn ${lead ? "" : "primary"}" data-close>Close</button>
    ${stopped ? `<button class="btn ${lead === "start" ? "primary" : ""}" data-start data-busy${gap}>${icon("play")}${cloud ? "Start all" : "Start the platform"}</button>` : ""}
    ${lead === "choose" ? `<a class="btn primary" href="#/playgrounds">Choose a playground</a>` : ""}`;
}

const dialog = document.createElement("dialog");
dialog.className = "welcome-dialog";
dialog.setAttribute("aria-labelledby", "welcome-title");
dialog.innerHTML = `
  <div class="dialog-head">
    <h2 id="welcome-title">How Gravitee Lab works</h2>
    <button class="icon-btn" data-close aria-label="Close" data-tip="Close">${icon("close")}</button>
  </div>
  <div class="welcome"></div>
  <div class="dialog-actions welcome-actions"></div>`;
document.body.append(dialog);

// While the catalogs are not there, the lead slot stays where it is, disabled, saying why in the words of the lists; a failed download has a
// Try again before it, and a lab with no catalog the button that adds the one that is listed. Written into the nodes, never the foot again:
// the keyboard stays where it was and nothing moves under the pointer when the download ends.
function paintLead(actions, state) {
  const lead = $('a[href="#/playgrounds"]', actions), { ready, why, extra } = contentLead(state), wanted = lead ? extra : "";
  if (lead) setOff(lead, !ready, why);
  if ((actions._extra || "") === wanted) return;
  actions._extra = wanted;
  actions._extraEl?.remove();
  actions._extraEl = null;
  if (!wanted) return;
  const holder = document.createElement("div");
  holder.innerHTML = wanted;
  actions._extraEl = holder.firstElementChild;
  lead.before(actions._extraEl);
}

// Its Start waits for Docker and for the running job, as everywhere
function renderFoot() {
  const actions = $(".welcome-actions", dialog);
  if (setHTML(actions, foot(store.state))) {
    paintBusy(actions);
    actions._extra = "";
    actions._extraEl = null;
  }
  paintLead(actions, store.state);
}

// What follows the choice, written only where it changed: the keyboard stays on the choice it was on
function render() {
  const content = $(".welcome", dialog), state = store.state, cards = () => $(".core-cards", content);
  const focused = !!cards()?.contains(document.activeElement);
  const choice = $(".welcome-core", content);
  if (choice) setHTML(choice, coreChoice());
  setHTML($(".welcome-start", content), start().map((line) => `<li>${line}</li>`).join(""));
  setHTML($(".welcome-lead", content), esc(lead()));
  setHTML($(".layers", content), layersHtml(state));
  // A choice that is not kept yet (an account to connect first) is not shown as made
  for (const input of $$('input[name="welcome-core"]', content)) input.checked = input.value === (onCloud() ? "cloud" : "machine");
  if (focused && !cards().contains(document.activeElement)) $("input:checked", cards())?.focus();
  renderFoot();
}

// A page's name leads to it and closes the dialog, also the name of the page under it, whose address does not change
dialog.addEventListener("click", (e) => {
  const on = (selector) => e.target.closest(selector);
  if (on('[aria-disabled="true"]')) return;   // a control that says why it waits does nothing
  if (on('[data-close], a[href^="#/"]')) dialog.close();
  else if (on("[data-start]")) { dialog.close(); if (onCloud()) runCore("start"); else runPlatform("start"); }
  else if (on("[data-core-env]")) pickEnvironment(on("[data-core-env]"));
  else if (on("[data-license]")) { dialog.close(); License.show({ form: true }); }
});
// A card chosen, with the keyboard too: This machine is kept at once, Gravitee Cloud once an environment is linked
dialog.addEventListener("change", (e) => {
  if (e.target.name !== "welcome-core") return;
  if (e.target.value === "cloud" && gatewayWhy()) return render();   // a key reached it all the same: the card is not chosen
  chooseCore(e.target.value, { anchor: e.target.closest(".core-card") });
});
dialog.addEventListener("close", () => remember(SEEN, "1"));
$("#welcome").addEventListener("click", show);
// What it says follows the lab while it is open: the choice made meanwhile, the platform started meanwhile, which has no Start
store.subscribe(() => { if (dialog.open) render(); });

// From its top, Enter on Close. The environments linked are read for the choice.
function show() {
  const content = $(".welcome", dialog);
  content.innerHTML = body();
  render();
  dialog.showModal();
  content.scrollTop = 0;
  $(".btn[data-close]", dialog).focus();
  store.refreshPlaces();
  api("/api/license").then((info) => {
    invited = !info.error && !info.present && !info.employee;
    if (dialog.open) render();
  }, () => {});
}

// The first visit opens it, once the page behind it has drawn, unless where API Management runs was chosen on this machine already
function init() {
  if (!recall(SEEN) && !coreOf().chosen) show();
}

export const Welcome = { init };
