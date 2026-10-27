// What this machine needs to run the lab (Docker with Compose 5.0, its memory, the disk, the platform's ports, Git), each line with its state and
// the one fix that answers it: the first step of Welcome to Gravitee Lab (welcome.js). Asked again every few seconds while that step shows, and
// when the window comes back to the front: a line turns right by itself once it is fixed. A fix is a link, a command to copy, or one the Lab UI
// runs here (POST /api/machine/act).

import { esc, icon, api, copyButton } from "../core.js";
import { toast } from "../components/ui.js";

const EVERY = 3000;
const STATE_ICON = { ok: icon("check"), warn: icon("alert"), error: icon("alert"), idle: icon("info"),
  working: '<span class="spinner" aria-hidden="true"></span>' };
const STATE_WORD = { ok: "Ready", warn: "Needs a look", error: "Missing", idle: "Not known yet", working: "On its way" };
const TITLES = ["Docker", "Memory for Docker", "Disk space", "Ports", "Git"];

export const machine = { report: null };
let timer = null;
let listener = null;

// The lines in red keep the platform from starting
// A line in red, or a fix on its way (Docker starting, Git installing), keeps the platform from starting: the server says it
export const machineBlocked = (report = machine.report) => !!report && !report.ready;
export const machineToFix = (report = machine.report) => (report?.checks || []).filter((c) => c.state === "error" || c.state === "warn").length;

export async function refreshMachine(fresh = false) {
  try {
    const found = await api(fresh ? "/api/machine?fresh=1" : "/api/machine");
    if (Array.isArray(found?.checks)) machine.report = found;
  } catch (err) {
    if (listener) toast(err.message, true);
  }
  listener?.();
  return machine.report;
}

function tick() {
  clearTimeout(timer);
  if (!listener) return;
  timer = setTimeout(async () => {
    await refreshMachine();
    tick();
  }, EVERY);
}

// While a step shows the machine: `onChange` draws it each time it is asked
export function watchMachine(onChange) {
  listener = onChange;
  refreshMachine(true).then(tick);
}

export function unwatchMachine() {
  listener = null;
  clearTimeout(timer);
}

// Back from Docker Desktop or an installer: asked again at once
window.addEventListener("focus", () => { if (listener) refreshMachine(true); });

// A fix: a link to open, a command to copy, or one the Lab UI runs here
function actionHtml(action) {
  const cls = `btn${action.primary ? " primary" : ""}`;
  if (action.href) return `<a class="${cls}" href="${esc(action.href)}" target="_blank" rel="noopener">${esc(action.label)}${icon("external")}</a>`;
  if (action.copy) return `<span class="machine-command"><code>${esc(action.copy)}</code>${copyButton(action.copy, `Copy ${action.copy}`)}</span>`;
  return `<button type="button" class="${cls}" data-act="${esc(action.id)}">${esc(action.label)}</button>`;
}

function rowHtml(check) {
  return `<li class="machine-check" data-state="${esc(check.state)}">
    <span class="machine-state ${esc(check.state)}" role="img" aria-label="${esc(STATE_WORD[check.state] || "")}">${STATE_ICON[check.state] || ""}</span>
    <span class="machine-text"><strong>${esc(check.title)}</strong><span>${esc(check.detail)}</span></span>
    <span class="machine-fix">${(check.actions || []).map(actionHtml).join("")}</span>
  </li>`;
}

export function machineRowsHtml(report = machine.report) {
  return report ? report.checks.map(rowHtml).join("")
    : TITLES.map((title) => rowHtml({ title, state: "working", detail: "Checking", actions: [] })).join("");
}

// What the whole says, in one line
export function machineSummary(report = machine.report) {
  if (!report) return { tone: "", html: '<span class="spinner" aria-hidden="true"></span><span>Checking your machine</span>' };
  const missing = report.checks.filter((c) => c.state === "error").length, look = report.checks.filter((c) => c.state === "warn").length;
  const working = report.checks.find((c) => c.state === "working");
  if (working && !missing) return { tone: "", html: `<span class="spinner" aria-hidden="true"></span><span>${esc(working.detail)}</span>` };
  if (missing) {
    return { tone: "err", html: `${icon("alert")}<span>The platform cannot start until ${missing === 1 ? "the line in red is" : `the ${missing} lines in red are`} fixed</span>` };
  }
  if (look) return { tone: "warn", html: `${icon("alert")}<span>Ready to run, ${look === 1 ? "one line needs" : `${look} lines need`} a look</span>` };
  return { tone: "ok", html: `${icon("check")}<span>Your machine is ready</span>` };
}

// A fix that runs here, from its button
export async function actMachine(button) {
  button.setAttribute("aria-disabled", "true");
  try {
    machine.report = await api("/api/machine/act", { id: button.dataset.act });
  } catch (err) {
    toast(err.message, true);
    button.removeAttribute("aria-disabled");
  }
  listener?.();
}
