// Gravitee Lab itself: the version this Lab is, the newer ones and what each changed, and the update to the newest. The dialog is the one
// place that says it; the chip at the right of the Lab's name opens it. An update is a job, which the dialog follows; the Lab UI then starts
// again on its new code, the page reloads by itself, and says once what is new.

import { $, esc, icon, api, store, recall, remember, setHTML, ago } from "../core.js";
import { toast, actionButton, paintActions } from "../components/ui.js";
import { busy } from "../activity/jobs.js";

const SEEN = "lab:version";       // the version this browser last showed: what is new is said once, after it changed
const UPDATING = "lab:updating";  // the version an update was started for, and the run of the Lab UI it was started from

const dialog = document.createElement("dialog");
dialog.className = "lab-dialog";
dialog.setAttribute("aria-labelledby", "lab-title");
dialog.innerHTML = `
  <header class="lab-hero">
    <span class="lab-logo"></span>
    <div class="lab-hero-text"><h2 id="lab-title">Gravitee Lab</h2><p class="lab-current" id="lab-current"></p></div>
  </header>
  <section class="lab-status" id="lab-status" aria-live="polite"></section>
  <div class="lab-notes-head" id="lab-notes-head"></div>
  <div class="lab-notes" id="lab-notes"></div>
  <div class="dialog-actions lab-actions"><span class="grow"></span><button class="btn" type="button" data-close>Close</button></div>`;
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
const dated = (versions, version) => day(versions.find((v) => v.version === version)?.date);

// What this Lab is: its version, the day it came out and how many changes it is past it; or a development version by its commit
function currentHtml(lab, versions) {
  if (!lab.version) return `<span>Development version</span>${lab.commit ? `<code class="chip">${esc(lab.commit)}</code>` : ""}`;
  const when = dated(versions, lab.version);
  return `<span class="lab-number">${esc(lab.version)}</span>${when ? `<span class="muted">${esc(when)}</span>` : ""}${
    lab.ahead ? `<span class="muted">${lab.ahead} newer change${lab.ahead === 1 ? "" : "s"}</span>` : ""}`;
}

// The one card that says where this Lab stands, with the one thing to do about it: an update waits, the check runs or failed, it is the newest
function statusHtml(lab, versions) {
  const card = (tone, mark, title, line, action = "") => `<div class="lab-status-card" data-tone="${tone}">
      <span class="lab-mark">${mark}</span>
      <span class="lab-status-text"><strong>${title}</strong>${line ? `<span>${line}</span>` : ""}</span>${action}</div>`;
  const check = (label) => actionButton({ key: "lab", verb: "check", label, icon: "restart", attrs: "data-check" });
  const when = lab.checked ? `Checked ${esc(ago(lab.checked))}` : "";
  if (shown.since) return card("ok", icon("check"), "Updated", `From ${esc(shown.since)}`);
  const going = updating();
  if (going) return card("working", '<span class="spinner" aria-hidden="true"></span>', `Updating to ${esc(going)}`, "Restarts when done");
  if (lab.update) {
    const released = dated(versions, lab.latest);
    const action = lab.blocked ? "" : actionButton({ key: "lab", verb: "update", label: "Update now", icon: "download", cls: "primary", attrs: "data-update" });
    return card("update", icon("download"), `Gravitee Lab ${esc(lab.latest)} is available`,
      lab.blocked ? esc(lab.blocked) : released ? `Released ${esc(released)}` : "", action);
  }
  if (lab.checking) return card("working", '<span class="spinner" aria-hidden="true"></span>', "Checking for updates", when);
  if (lab.error) return card("error", icon("alert"), "Could not check for updates", [esc(lab.error), when].filter(Boolean).join(". "), check("Try again"));
  if (!lab.checked) return card("idle", icon("info"), "Not checked yet", "", check("Check now"));
  return card("ok", icon("check"), "Up to date", when, check("Check now"));
}

// What each version changed: those newer than `since` (what an update brings, or what one brought), else the one this Lab is
function picked(versions, lab, since) {
  return since ? versions.filter((v) => newer(v.version, since) && !newer(v.version, lab.version))
    : lab.update ? versions.filter((v) => v.newer) : versions.filter((v) => v.current);
}

function notesHtml(list) {
  const change = (c) => `<li><span>${esc(c.text)}</span>${c.url ? `<a class="text-link" href="${esc(c.url)}" target="_blank" rel="noopener">#${c.number}</a>` : ""}</li>`;
  return list.map((v) => `<section class="lab-version">
      <div class="lab-version-head"><span class="lab-number">${esc(v.version)}</span><span class="muted">${esc(day(v.date))}</span>${
        v.url ? `<a class="icon-btn" href="${esc(v.url)}" target="_blank" rel="noopener" data-tip="Release ${esc(v.version)}" aria-label="Release ${esc(v.version)}">${icon("external")}</a>` : ""}</div>
      ${v.changes.length ? `<ul>${v.changes.map(change).join("")}</ul>` : ""}
    </section>`).join("");
}

function draw() {
  const lab = store.state?.lab;
  if (!lab || !shown) return;
  const releases = shown.releases || {}, versions = releases.versions || [];
  const logo = $(".lab-logo", dialog);
  if (!logo.firstChild) logo.innerHTML = ($(".sidebar .logo")?.outerHTML || "").replace(/<defs>[\s\S]*?<\/defs>/, "");
  // While an update runs, the Lab is still the version it is updated from, and what it brings stays said
  const going = updating(), from = going ? recall(SEEN) : null;
  const seen = from ? { ...lab, version: from, ahead: 0 } : lab;
  setHTML($("#lab-current", dialog), currentHtml(seen, versions));
  if (setHTML($("#lab-status", dialog), statusHtml(lab, versions))) paintActions(dialog);
  const list = from ? picked(versions, { ...lab, version: going }, from) : picked(versions, lab, shown.since);
  const all = releases.url ? `<a class="text-link" href="${esc(releases.url)}" target="_blank" rel="noopener">All releases${icon("external")}</a>` : "";
  const title = !list.length ? "" : shown.since || from || lab.update ? "What's new" : "In this version";
  setHTML($("#lab-notes-head", dialog), title || all ? `<h3>${title}</h3>${all}` : "");
  setHTML($("#lab-notes", dialog), notesHtml(list));
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
  } catch (err) {
    remember(UPDATING, null);
    toast(err.message, true);
  }
  store.refresh();
}

dialog.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) dialog.close();
  else if (e.target.closest("[data-check]")) check();
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
// The version an update runs to, while it runs and while the Lab UI starts again on it; null otherwise
function updating() {
  const job = store.state?.job, waited = waitedFor();
  if (job && !job.done && job.for === "lab" && job.verb === "update") return waited?.version || store.state?.lab?.latest || "";
  return waited && (store.offline || store.state?.lab?.run === waited.run) ? waited.version : null;
}

export function followLab() {
  const lab = store.state?.lab;
  if (!lab) return;
  const waited = waitedFor(), job = store.state?.job;
  if (waited && job?.done && !job.ok && job.for === "lab" && lab.run === waited.run) remember(UPDATING, null);   // the update failed: its job says why
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

// The version at the right of the name of the Lab, which opens the dialog; while a newer one waits, Update, and a dot on the logo when
// the menu is collapsed
export function paintLab() {
  const lab = store.state?.lab, button = $("#lab"), dot = $("#lab-dot");
  if (!button) return;
  const waiting = !!lab?.update && !store.offline;
  const version = lab?.version || (lab?.commit ? "dev" : "");
  button.hidden = !lab;
  setHTML(button, waiting ? `${icon("download")}<span>Update</span>` : `<span>${esc(version)}</span>`);
  button.classList.toggle("update", waiting);
  const tip = waiting ? `Gravitee Lab ${lab.latest} is available` : lab?.version ? `Gravitee Lab ${lab.version}`
    : lab?.commit ? `Development version ${lab.commit}` : "Gravitee Lab";
  button.dataset.tip = tip;
  button.setAttribute("aria-label", tip);
  if (dot) {
    dot.hidden = !waiting;
    dot.dataset.tip = tip;
    dot.setAttribute("aria-label", tip);
  }
  if (dialog.open) draw();
}
