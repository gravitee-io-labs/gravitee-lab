// Gravitee Lab itself: the version this Lab is, the newer ones and what each changed, and the update to the newest. The dialog is the one
// place that says it; the menu opens it from its tools, and from a chip while a newer version waits. An update is a job; the Lab UI then
// starts again on its new code, the page reloads by itself, and says once what is new.

import { $, esc, icon, api, store, recall, remember, setHTML, ago } from "../core.js";
import { toast, actionButton, paintActions } from "../components/ui.js";
import { busy } from "../activity/jobs.js";

const SEEN = "lab:version";       // the version this browser last showed: what is new is said once, after it changed
const UPDATING = "lab:updating";  // the version an update was started for, and the run of the Lab UI it was started from

const dialog = document.createElement("dialog");
dialog.className = "create-dialog platform-dialog lab-dialog";
dialog.setAttribute("aria-labelledby", "lab-title");
dialog.innerHTML = `
  <form method="dialog">
    <h2 id="lab-title">Gravitee Lab</h2>
    <dl class="pairs lab-facts" id="lab-facts"></dl>
    <div class="lab-notes" id="lab-notes"></div>
    <p class="create-problem" id="lab-problem" role="alert" hidden></p>
    <div class="dialog-actions">
      <button class="btn" type="button" data-close>Close</button>
      <span class="act-slot" id="lab-primary"></span>
    </div>
  </form>`;
document.body.append(dialog);

let shown = null;   // what the dialog draws: {releases, since}, `since` the version what is new is said after

// Numbers of a version, to compare two
const numbers = (version) => String(version || "").split(".").map(Number);
const newer = (a, b) => {
  const [x, y] = [numbers(a), numbers(b)];
  for (let i = 0; i < 3; i += 1) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  return false;
};
const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "");

// What this Lab is: its version and how many changes it is past it, or a development version by its commit
function versionHtml(lab) {
  if (!lab.version) return `<span>Development version</span>${lab.commit ? ` <code class="chip">${esc(lab.commit)}</code>` : ""}`;
  return `<code class="chip">${esc(lab.version)}</code>${lab.ahead ? ` <span class="muted">and ${lab.ahead} newer change${lab.ahead === 1 ? "" : "s"}</span>` : ""}`;
}

// Whether it is the newest, as the last fetch said, and when that was
function updatesHtml(lab) {
  const when = lab.checked ? `<span class="muted">Checked ${esc(ago(lab.checked))}</span>` : "";
  const state = lab.checking ? "Checking" : lab.update ? `${esc(lab.latest)} available` : lab.error ? esc(lab.error)
    : lab.checked ? "Up to date" : "Not checked yet";
  return `<span class="lab-updates"><span>${state}</span>${when}
    <button type="button" class="btn" data-check ${lab.checking ? 'aria-disabled="true" data-why="Checking"' : ""}>Check now</button></span>`;
}

// What each version changed: those newer than `since` (what an update brings, or what one brought), else the one this Lab is
function notesHtml(versions, lab, since) {
  const picked = since ? versions.filter((v) => newer(v.version, since) && !newer(v.version, lab.version))
    : lab.update ? versions.filter((v) => v.newer) : versions.filter((v) => v.current);
  const change = (c) => `<li><span>${esc(c.text)}</span>${c.url ? ` <a href="${esc(c.url)}" target="_blank" rel="noopener">#${c.number}</a>` : ""}</li>`;
  return picked.map((v) => `<section class="lab-version">
      <h3><span>${esc(v.version)}</span><span class="muted">${esc(day(v.date))}</span>${v.url ? `<a class="icon-btn" href="${esc(v.url)}" target="_blank" rel="noopener" data-tip="Release ${esc(v.version)}" aria-label="Release ${esc(v.version)}">${icon("external")}</a>` : ""}</h3>
      ${v.changes.length ? `<ul>${v.changes.map(change).join("")}</ul>` : ""}
    </section>`).join("");
}

function draw() {
  const lab = store.state?.lab;
  if (!lab || !shown) return;
  const versions = shown.releases?.versions || [];
  $("#lab-title", dialog).textContent = shown.since ? `What's new in Gravitee Lab ${lab.version}` : "Gravitee Lab";
  setHTML($("#lab-facts", dialog), `<div><dt>Version</dt><dd>${versionHtml(lab)}</dd></div>
    ${shown.since ? "" : `<div><dt>Updates</dt><dd>${updatesHtml(lab)}</dd></div>`}`);
  setHTML($("#lab-notes", dialog), notesHtml(versions, lab, shown.since));
  // What keeps the update from being made here is said in full where the button is
  const problem = $("#lab-problem", dialog), why = lab.update && !shown.since ? lab.blocked || "" : "";
  problem.hidden = !why;
  problem.textContent = why;
  const primary = lab.update && !shown.since ? actionButton({ key: "lab", verb: "update", label: `Update to ${lab.latest}`, cls: "primary",
    attrs: lab.blocked ? `aria-disabled="true" data-why="${esc(lab.blocked)}"` : "data-update" }) : "";
  if (setHTML($("#lab-primary", dialog), primary)) paintActions(dialog);
}

let loading = false;
async function load() {
  loading = true;
  try {
    shown = { ...shown, releases: await api("/api/lab") };
  } catch (err) {
    if (!store.offline) toast(err.message, true);   // a Lab UI that starts again is said by the menu; it is read once it answers
  } finally {
    loading = false;
  }
  draw();
}

// The dialog: what this Lab is and what is newer; `since`: what an update brought, said once after it
export function openLab({ since = null } = {}) {
  shown = { releases: shown?.releases || null, since };
  draw();
  if (!dialog.open) dialog.showModal();
  load();
}

async function check() {
  try {
    await busy("lab", "check", api("/api/lab/check", {}));
  } catch (err) {
    toast(err.message, true);
  }
  await store.refresh();
  load();
}

// The update: a job, then the Lab UI starts again; the page reloads once it answers with the new version
async function update() {
  const lab = store.state?.lab;
  if (!lab?.update || lab.blocked) return;
  try {
    remember(SEEN, lab.version || null);
    remember(UPDATING, JSON.stringify({ version: lab.latest, run: lab.run }));
    await busy("lab", "update", api("/api/lab/update", { version: lab.latest }));
    dialog.close();
  } catch (err) {
    remember(UPDATING, null);
    toast(err.message, true);
  }
  store.refresh();
}

dialog.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) dialog.close();
  else if (e.target.closest("[data-check]:not([aria-disabled])")) check();
  else if (e.target.closest("[data-update]")) update();
});
dialog.addEventListener("close", () => { shown = shown ? { releases: shown.releases, since: null } : null; });

// --- What the menu says, and what follows an update ------------------------------------------------------------

// A Lab UI that started again answers with the version an update was started for: the page loads its new code. The one the update was
// started from says that version too, between the move of the files and its end: what is waited for is another run. After a load, a
// version that is not the one this browser showed last says what is new, once
let told = false;
const waitedFor = () => {
  try {
    return JSON.parse(recall(UPDATING) || "null");
  } catch {
    return null;
  }
};
export function followLab() {
  const lab = store.state?.lab;
  if (!lab) return;
  const waited = waitedFor();
  if (waited && lab.version === waited.version && lab.run !== waited.run) {
    remember(UPDATING, null);
    location.reload();
    return;
  }
  if (dialog.open && shown && !shown.releases && !store.offline && !loading) load();   // what is new, once the Lab UI answers again
  if (told) return;
  told = true;
  const seen = recall(SEEN);
  if (lab.version && seen && newer(lab.version, seen)) {
    toast(`Updated to ${lab.version}`);
    openLab({ since: seen });
  }
  if (lab.version) remember(SEEN, lab.version);
}

// The chip of the menu while a newer version waits, and the tooltip of the button that opens the dialog
export function paintLab() {
  const lab = store.state?.lab, chip = $("#lab-update"), button = $("#lab");
  if (!chip || !button) return;
  const name = lab?.version ? `Gravitee Lab ${lab.version}` : "Gravitee Lab";
  button.dataset.tip = name;
  button.setAttribute("aria-label", name);
  const waiting = !!lab?.update && !store.offline;
  chip.hidden = !waiting;
  const words = waiting ? `Update to ${lab.latest}` : "";
  $("#lab-update-text").textContent = words;
  if (waiting) {
    chip.dataset.tip = words;
    chip.setAttribute("aria-label", words);
  }
  if (dialog.open) draw();
}
