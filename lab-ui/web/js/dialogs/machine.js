// Your machine: what it needs to run the lab (Docker with Compose 5.0, its memory, the disk, the platform's ports, Git), each line with its
// state and the one fix that answers it. Asked again every few seconds while it is open, and when the window comes back to the front:
// a line turns right by itself once it is fixed. A first visit opens it before How Gravitee Lab works when something needs a look; the
// notices about Docker open it, and so does How Gravitee Lab works.

import { $, esc, icon, api, setHTML, recall, remember } from "../core.js";
import { toast, copyButton } from "../components/ui.js";

const CHECKED = "machine:checked";   // the browser keeps that the first visit looked at the machine
const EVERY = 3000;

const STATE_ICON = { ok: icon("check"), warn: icon("alert"), error: icon("alert"), idle: icon("info"),
  working: '<span class="spinner" aria-hidden="true"></span>' };
const STATE_WORD = { ok: "Ready", warn: "Needs a look", error: "Missing", idle: "Not known yet", working: "On its way" };

const dialog = document.createElement("dialog");
dialog.className = "machine-dialog";
dialog.setAttribute("aria-labelledby", "machine-title");
dialog.innerHTML = `
  <div class="dialog-head">
    <h2 id="machine-title">Your machine</h2>
    <button class="icon-btn" data-close aria-label="Close" data-tip="Close">${icon("close")}</button>
  </div>
  <div class="machine-body">
    <p class="machine-summary" id="machine-summary" aria-live="polite"></p>
    <ul class="machine-checks" id="machine-checks"></ul>
  </div>
  <div class="dialog-actions machine-actions">
    <button class="btn primary" data-done>Close</button>
  </div>`;
document.body.append(dialog);

let report = null;
let timer = null;
let then = null;   // what opens once it is closed: How Gravitee Lab works, on a first visit

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
    <span class="machine-fix">${check.actions.map(actionHtml).join("")}</span>
  </li>`;
}

// What the whole says, in one line over the lines
function summaryHtml() {
  if (!report) return `<span class="spinner" aria-hidden="true"></span><span>Checking</span>`;
  const missing = report.checks.filter((c) => c.state === "error").length;
  const look = report.checks.filter((c) => c.state === "warn").length;
  if (missing) return `${icon("alert")}<span>The platform cannot start until ${missing === 1 ? "the line in red is" : `the ${missing} lines in red are`} fixed</span>`;
  if (look) return `${icon("alert")}<span>The platform can start; ${look === 1 ? "one line needs" : `${look} lines need`} a look</span>`;
  return `${icon("check")}<span>Your machine is ready</span>`;
}

function draw() {
  const summary = $("#machine-summary", dialog);
  summary.dataset.tone = !report ? "" : report.checks.some((c) => c.state === "error") ? "err"
    : report.checks.some((c) => c.state === "warn") ? "warn" : "ok";
  setHTML(summary, summaryHtml());
  // Drawn again only when it changed: a button under the pointer stays where it is
  setHTML($("#machine-checks", dialog), report ? report.checks.map(rowHtml).join("")
    : ["Docker", "Memory for Docker", "Disk space", "Ports", "Git"].map((title) => rowHtml({ title, state: "working", detail: "Checking", actions: [] })).join(""));
  // While a line blocks the platform, its fix is the one primary button
  const done = $("[data-done]", dialog), blocked = !!report?.checks.some((c) => c.state === "error");
  done.textContent = then ? "Continue" : "Close";
  done.classList.toggle("primary", !blocked);
}

async function poll(fresh = false) {
  try {
    report = await api(fresh ? "/api/machine?fresh=1" : "/api/machine");
  } catch (err) {
    if (dialog.open) toast(err.message, true);
  }
  if (dialog.open) draw();
}

function tick() {
  clearTimeout(timer);
  if (!dialog.open) return;
  timer = setTimeout(async () => {
    await poll();
    tick();
  }, EVERY);
}

function openMachine({ next = null } = {}) {
  then = next;
  draw();
  if (!dialog.open) dialog.showModal();
  $("[data-done]", dialog).focus();
  poll(true).then(tick);
}

dialog.addEventListener("click", async (e) => {
  if (e.target.closest("[data-close], [data-done]")) return dialog.close();
  const button = e.target.closest("[data-act]");
  if (!button) return;
  button.setAttribute("aria-disabled", "true");
  try {
    report = await api("/api/machine/act", { id: button.dataset.act });
    draw();
  } catch (err) {
    toast(err.message, true);
    button.removeAttribute("aria-disabled");
  }
});
dialog.addEventListener("close", () => {
  clearTimeout(timer);
  remember(CHECKED, "1");
  const next = then;
  then = null;
  next?.();
});
// Back from Docker Desktop or an installer: asked again at once
window.addEventListener("focus", () => { if (dialog.open) poll(true); });

// Anywhere in the page, a control with data-machine opens it
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-machine]")) return;
  e.preventDefault();
  openMachine();
});

// The first visit of this browser: the machine is looked at first, and shown when something needs a look; `next` opens after it
export async function firstLook(next) {
  if (recall(CHECKED)) return next();
  try {
    report = await api("/api/machine?fresh=1");
  } catch {
    return next();
  }
  if (!Array.isArray(report?.checks) || report.checks.every((c) => c.state === "ok" || c.state === "idle")) {
    remember(CHECKED, "1");
    return next();
  }
  openMachine({ next });
}
