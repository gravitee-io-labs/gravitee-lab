// The license of the platform: what its chip on the Stack and the line under its field in the settings say, the
// dialog behind them, and its form that saves a license file or its text. What a row that needs a feature says of it
// is the server's (`note`).

import { $, $$, esc, icon, setHTML, loading, api, store } from "../core.js";
import { toast, copyButton, emptyHtml, onRowKey, actionButton, paintActions, paintBusy, dockerDown, applyBadge } from "../components/ui.js";
import { runJob, busy } from "../activity/jobs.js";
import { openFile } from "./editor.js";

const ACRONYMS = ["mfa", "oem"];   // words of a tier or pack id that are written in capitals
// Each status of the license as the dialog's head says it, with its tone: an issue only for what keeps Enterprise
// features off although a license is set; no license is the Community edition
const STATUS = { none: ["No license", ""], unreadable: ["Unreadable", "err"], invalid: ["Invalid signature", "err"],
  expired: ["Expired", "err"], expiring: ["Expiring", "warn"], valid: ["Valid", "ok"] };
// What /api/license answered, and the request on its way: who the license is for, its tier, and what it counts with
// the license model (packs, features, model), null without one, `model_error` saying why and `pull` naming the image
// to pull when that is what it takes. `read`: the license it was read for, as the state says it.
let info = null, pending = null, read = "";
let filter = "all", showOwner = false;
let plugins = {};                  // product id -> {plugin: the license feature it needs}, among the installed ones
const open = new Set();            // the packs that are expanded

// The license saved, as the state says it every few seconds, without Docker: {status (none, unreadable, invalid,
// expired, expiring, valid), expires, days_left, problem, tone, applied}; null without a variable that holds one
const saved = () => store.state?.license || null;
const variable = () => store.state?.platform.license_var || "";
// Expiry dates are instants: shown in UTC, as the license was issued
const date = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
// What a reading of the license is for: another license saved is read again
const version = (lic) => (lic ? `${lic.status} ${lic.expires}` : "");

// "enterprise-mfa" reads "Enterprise MFA"; a product's id is written in capitals too
function title(id) {
  const capitals = [...ACRONYMS, ...(store.state?.products || []).map((p) => p.id)];
  return id.split("-").map((w, i) => capitals.includes(w) ? w.toUpperCase() : i ? w : w[0].toUpperCase() + w.slice(1)).join(" ");
}

function load() {
  if (pending) return pending;
  const asked = version(saved());
  pending = api("/api/license")
    .then((data) => { info = data; }, (err) => { info = { error: err.message }; })
    .finally(() => {
      pending = null;
      read = asked;
      render();
      store.notify();   // what says its tier draws again: the chip of the Stack, the line under its field
    });
  return pending;
}

// The installed plugins that need a license feature, by product: read when the dialog opens
async function loadPlugins() {
  await Promise.all((store.state?.products || []).map(async ({ id }) => {
    try {
      plugins[id] = (await api(`/api/plugins/installed?product=${encodeURIComponent(id)}`)).features;
    } catch { /* the product cannot be read now: the packs show without its plugins */ }
  }));
  if (dialog.open) renderPacks();
}

// What the chip on the core's line of the Stack says: {label, tone, tip, card}, in words, never an acronym. "Enterprise Edition" and its
// tier with a license, what is wrong with it when something is; "Community Edition" without one. With no license saved while what runs
// still reads one, it is not Community yet: "License not saved", amber, its popover where it still runs and what the next Apply or restart
// does. Another license saved than the one that runs is said by the rows it restarts (Apply needed), and by its tooltip. Null without a
// variable that holds a license.
function chip() {
  const lic = saved();
  if (!lic) return null;
  const present = lic.status !== "none", late = lic.applied === false ? lic.reason : "";
  const wrong = { unreadable: "Unreadable license", invalid: "Invalid signature", expired: "License expired" }[lic.status];
  const tier = info?.tier ? title(info.tier) : "";
  const expiry = lic.expires ? `${lic.status === "expired" ? "Expired on" : "Expires"} ${date(lic.expires)}` : "";
  if (!present && late) {
    return { label: "License not saved", tone: "warn", tip: "License not saved",
      card: { title: "License not saved", text: late, foot: "The next Apply or restart turns Enterprise features off." } };
  }
  if (!present) return { label: "Community Edition", tone: "", tip: "No license: Enterprise features are off" };
  const label = wrong || `Enterprise Edition${tier ? `, ${tier}` : ""}`;
  return { label, tone: lic.tone || "", tip: [expiry, late].filter(Boolean).join(". ") || label };
}

// The chip as a pill that leads to the license, wherever the platform is named (the core's line of Containers, the head of the
// Dashboard's platform card): its tooltip until when, or a popover what is not saved yet and what the next Apply does. "" without a
// variable that holds a license.
function pill() {
  const license = chip();
  if (!license) return "";
  const card = license.card ? ` data-tip-card="${esc(JSON.stringify(license.card))}"` : "";
  return `<button type="button" class="pill ${esc(license.tone)}" data-license${license.card ? '="form"' : ""} data-tip="${esc(license.tip)}"${card}><span>${esc(license.label)}</span>${icon("arrow-right")}</button>`;
}

// What the line under the field that holds the license says: {dot, text}, `dot` a state class of .dot-status. The
// license saved, its tier and packs once read, and the badge `late` while what runs reads another one. Null without a
// variable that holds a license.
function summary() {
  const lic = saved();
  if (!lic) return null;
  const until = lic.expires ? date(lic.expires) : "";
  const state = { none: "No license", unreadable: "Unreadable", invalid: "Invalid signature", expired: `Expired on ${until}`,
    expiring: `Valid until ${until}`, valid: until ? `Valid until ${until}` : "Valid, never expires" }[lic.status];
  const facts = info?.readable ? [info.tier && title(info.tier), info.packs && count(info.packs.length, "pack")] : [];
  return {
    dot: { err: "issue", warn: "starting" }[lic.tone] || (lic.status === "none" ? "" : "healthy"),
    text: [state, ...facts].join(", "), late: lic.applied === false ? lic.reason : "",
  };
}

// feature -> [{name, product}] among the installed plugins
function pluginsByFeature() {
  const out = {};
  for (const [id, features] of Object.entries(plugins)) {
    for (const [name, feature] of Object.entries(features)) {
      // Under its product, "gravitee-am-factor-otp" reads "factor-otp"
      const short = name.replace(/^gravitee-/, "");
      (out[feature] ||= []).push({ name: short.startsWith(`${id}-`) ? short.slice(id.length + 1) : short, product: id.toUpperCase() });
    }
  }
  return out;
}

// The head: the license saved, as the state says it, whether what runs reads it, until when; the image to pull to
// read its model, when that is what it takes
function renderState() {
  const lic = saved(), [word, tone] = lic ? STATUS[lic.status] : ["–", ""];
  const pill = $("#license-state");
  pill.className = `state ${tone}`;
  pill.textContent = word;
  setHTML($("#license-late"), lic?.applied === false ? applyBadge(lic.reason) : "");
  // Its field in .env, named for what it is for: adding the first one, or changing the one there
  $("#license-settings span").textContent = !lic || lic.status === "none" ? "Add license" : "Change license";
  const expired = lic?.status === "expired";
  $("#license-expiry").textContent = $("#license-expiry").dataset.tip = lic?.expires
    ? `${expired ? "Expired on" : "Expires on"} ${date(lic.expires)}${expired ? "" : `, in ${count(lic.days_left, "day")}`}` : "";
  const pull = info?.pull ? actionButton({ key: "platform", verb: "pull", label: "Pull images", icon: "download",
    attrs: `data-busy data-pull data-tip="Pull ${esc(info.pull)}"` }) : "";
  if (setHTML($("#license-pull"), pull)) {
    paintBusy($("#license-pull"));
    paintActions($("#license-pull"));
  }
}

// Why the model is not known: an image not pulled, with Pull images in the head; Docker not answering; else what
// failed
function modelWhy() {
  if (info.pull) return `<span data-tip="${esc(info.model_error)}">${esc(info.pull)} is not pulled</span>`;
  return store.state && dockerDown() ? `<span>${esc(info.model_error)}</span>` : `<span class="err-text">${esc(info.model_error)}</span>`;
}

// Where a license, or no need of one, comes from: the Gravitee Cloud, which runs API Management for the person (an account of their own
// or a trial of 14 days), or a self-hosted license for what runs on this machine (Gamma, Acme Bakery: Gravitee Cloud does not run them)
const SELF_HOSTED_URL = "https://www.gravitee.io/self-hosted-trial";
const link = (href, label, cls = "btn") => `<a class="${cls}" href="${href}" target="_blank" rel="noopener">${icon("external")}${label}</a>`;
// The Cloud has no link of its own here: its button opens the form that connects an account, which asks whether there is one already and
// offers the trial of 14 days if there is not
const GET_ONE = `<p class="license-intro">Community edition: Enterprise features are off</p>
  <div class="license-cta" role="group" aria-label="Get a license">
    <section class="cta-card">
      <span class="cta-icon">${icon("cloud")}</span>
      <h3>Use Gravitee Cloud</h3>
      <p>API Management runs in the Cloud, with a gateway on this machine</p>
      <div class="cta-actions"><button type="button" class="btn" data-connect-cloud>${icon("cloud")}Connect my account</button></div>
    </section>
    <section class="cta-card">
      <span class="cta-icon">${icon("key")}</span>
      <h3>Run it on this machine</h3>
      <p>A self-hosted license unlocks the Enterprise features of the platform on this machine</p>
      <div class="cta-actions">${link(SELF_HOSTED_URL, "Request a license")}</div>
    </section>
  </div>`;

function renderFacts() {
  if (!info) return;
  // Without a license, where to get one; the place stays when there is one, empty
  const none = !info.error && !info.present;
  $(".license", dialog).classList.toggle("none", none);
  setHTML($("#license-cta"), none && !info.employee ? GET_ONE : none ? '<p class="license-intro">Community edition: Enterprise features are off</p>' : "");
  if (none) {
    setHTML($("#license-facts"), "");
    return;
  }
  if (info.error || !info.readable) {
    const why = info.error ? `<span class="err-text">${esc(info.error)}</span>`
      : esc(info.present ? "The license cannot be read" : "Community edition: Enterprise features are off");
    setHTML($("#license-facts"), `<div class="empty">${why}</div>`);
    return;
  }
  // A value that can be too long for its line is an element of its own: only an element ends with an ellipsis
  const row = (k, v) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`;
  // What is counted with the license model shows only with one; without, the Model row says why
  const { model } = info;
  setHTML($("#license-facts"), [
    row("Tier", `${esc(info.tier ? title(info.tier) : "None")}${info.tier && info.tier_known === false ? ' <span class="pill warn">Unknown to this version</span>' : ""}`),
    ...(model ? [
      row("Packs", `${info.packs.length} <span class="muted">(${info.tier_packs.length} from the tier, ${info.extra_packs.length} added)</span>`),
      row("Features", `${info.features.length}`),
    ] : []),
    row("Expires", info.expires ? esc(date(info.expires)) : "Never"),
    row("Signature", info.valid ? '<span class="ok-text">GraviteeSource</span>' : '<span class="err-text">Not verified</span>'),
    row("Model", model ? `<code data-tip="From the ${esc(model.service)} image">${esc(model.source)}</code>` : modelWhy()),
    row("Source", `<code>.env</code> <span class="muted">(${esc(variable())})</span>`),
    // Who the license belongs to: hidden until asked, for screen sharing
    ...(showOwner ? [
      row("Company", `<span>${esc(info.company || "–")}</span>`),
      row("Contact", `<span>${esc(info.email || "–")}</span>`),
      row("License ID", `<code>${esc(info.id || "–")}</code>${info.id ? copyButton(info.id, "Copy the license ID") : ""}`),
    ] : []),
    `<button class="text-link" data-owner>${showOwner ? "Hide" : "Show"} company, contact and ID</button>`,
  ].join(""));
}

// The packs of the license model, with what the license includes: none without a readable license, or without the
// model
function renderPacks() {
  const list = $("#license-packs");
  if (!info?.model) { setHTML(list, ""); return; }
  const q = $("#license-search").value.trim().toLowerCase();
  const used = pluginsByFeature();
  const packs = Object.entries(info.model.packs).map(([id, features]) => ({ id, features: [...new Set(features)] }));
  const inPacks = new Set(packs.flatMap((p) => p.features));
  const extra = info.extra_features.filter((f) => !inPacks.has(f));
  if (extra.length) packs.push({ id: "additional-features", features: extra, extra: true });

  const included = (p) => p.extra || info.packs.includes(p.id);
  const source = (p) => p.extra || info.extra_packs.includes(p.id) && !info.tier_packs.includes(p.id) ? "Added" : "Tier";
  const featureMatch = (f) => `${f} ${(used[f] || []).map((x) => x.name).join(" ")}`.toLowerCase().includes(q);

  const shown = packs
    .filter((p) => filter === "all" || (filter === "included") === included(p))
    .map((p) => ({ ...p, hits: q ? (title(p.id).toLowerCase().includes(q) ? p.features : p.features.filter(featureMatch)) : p.features }))
    .filter((p) => p.hits.length)
    .sort((a, b) => included(b) - included(a) || a.id.localeCompare(b.id));

  setHTML(list, shown.map((p) => {
    const isOpen = open.has(p.id) || !!q;
    const on = included(p);
    // A search opens every pack: its row then has nothing to fold, and takes no click
    const fold = q ? "" : `data-pack="${esc(p.id)}" role="button" tabindex="0" aria-expanded="${isOpen}"`;
    const features = isOpen ? `<ul class="features">${p.hits.map((f) => {
      const allowed = info.features.includes(f);
      return `<li class="feature">
        <span class="lic ${allowed ? "yes" : "no"}" role="img" aria-label="${allowed ? "In the license" : "Not in the license"}" data-tip="${allowed ? "In the license" : "Not in the license"}">${icon(allowed ? "check" : "close")}</span>
        <code>${esc(f)}</code>
        <span class="feature-plugins">${(used[f] || []).map((x) => `<span class="pill">${esc(x.product)} ${esc(x.name)}</span>`).join("")}</span>
      </li>`;
    }).join("")}</ul>` : "";
    return `<li class="pack ${isOpen ? "open" : ""} ${on ? "" : "off"}">
      <div class="pack-row" ${fold}>
        <span class="s-toggle">${q ? "" : icon("chevron")}</span>
        <span class="lic ${on ? "yes" : "no"}">${icon(on ? "check" : "close")}</span>
        <span class="pack-name">${esc(p.extra ? "Additional features" : title(p.id))}</span>
        ${on ? `<span class="pill">${source(p)}</span>` : ""}
        <span class="pack-meta">${p.features.length} feature${p.features.length > 1 ? "s" : ""}</span>
      </div>${features}</li>`;
  }).join("") || `<li class="empty">${emptyHtml({ filtered: true })}</li>`);
}

// Without a license model there are no packs to show: once that is known, the dialog holds the facts alone, at their
// size
function render() {
  renderState();
  renderFacts();
  const none = !!info && !info.model;
  $(".license-packs", dialog).hidden = none;
  dialog.classList.toggle("compact", none);
  renderPacks();
}

// --- A license given: a file chosen or dropped, or its text pasted -------------------------------------------------

// What the variable holds is the license in base64. A file is the license itself, or already its base64 (a key
// kept as text): either way the variable gets base64. Pasted text is taken as base64 when it is, else encoded.
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const base64 = (bytes) => {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
};
function encode(bytes) {
  const text = new TextDecoder().decode(bytes).replace(/\s+/g, "");
  return text.length % 4 === 0 && BASE64.test(text) ? text : base64(bytes);
}

let given = null;   // the file chosen or dropped: {name, value}, its value in base64

// What Save writes: the file given, else the text pasted; "" without either
const licenseValue = () => given?.value || ($("#license-text").value.trim() && encode(new TextEncoder().encode($("#license-text").value.trim())));

function drawForm() {
  $("#license-file-name").textContent = given ? given.name : "Drop a license file";
  $("#license-drop").classList.toggle("chosen", !!given);
  const key = `file:${store.state?.platform.env_file || ""}`, none = !licenseValue();
  setHTML($("#license-save-slot"), actionButton({ key, verb: "save", label: "Save", cls: "primary",
    attrs: `id="license-save"${none ? ' aria-disabled="true" data-why="No license"' : ""}` }));
  paintActions($("#license-save-slot"));
}

function openForm(on) {
  given = null;
  $("#license-text").value = "";
  $("#license-file").value = "";
  $("#license-form").hidden = !on;
  $("#license-settings").hidden = on;
  drawForm();
  if (on) $("#license-choose").focus();
}

async function takeFile(file) {
  if (!file) return;
  given = { name: file.name, value: encode(new Uint8Array(await file.arrayBuffer())) };
  $("#license-text").value = "";
  drawForm();
}

// Saved as the settings form saves the core's .env: at the version read just before, the license's variable alone.
// What runs reads it once applied: the head says "Apply needed" meanwhile.
async function saveLicense() {
  const value = licenseValue(), platform = store.state?.platform;
  if (!value || !platform) return;
  const { env_file: file, license_var: key } = platform;
  try {
    await busy(`file:${file}`, "save", api(`/api/env?file=${encodeURIComponent(file)}`)
      .then(({ version }) => api("/api/env", { file, values: { [key]: value }, version })));
  } catch (err) {
    toast(err.message, true);
    return;
  }
  toast("Saved");
  openForm(false);
  await store.refresh();
  load();
}

// On what the license gives, or straight on the form that takes a new one (`form`): where every "Set license" leads
function show({ form = false } = {}) {
  showOwner = false;
  openForm(false);
  dialog.showModal();
  render();
  load();
  loadPlugins();
  if (form) openForm(true);
}

const dialog = document.createElement("dialog");
dialog.className = "license-dialog";
dialog.setAttribute("aria-label", "License");
dialog.innerHTML = `
  <div class="dialog-head">
    <h2>License</h2><span class="state" id="license-state">–</span>
    <span id="license-late"></span><span class="sub" id="license-expiry"></span>
    <div class="license-tools">
      <span class="act-slot" id="license-pull"></span>
      <button class="btn" id="license-settings">${icon("key")}<span>Add license</span></button>
      <button class="icon-btn" id="license-close" aria-label="Close" data-tip="Close" autofocus>${icon("close")}</button>
    </div>
  </div>
  <form class="license-form" id="license-form" hidden>
    <div class="field"><span class="field-label">File</span>
      <div class="license-drop" id="license-drop">${icon("file")}<span id="license-file-name"></span>
        <button type="button" class="btn" id="license-choose">Choose</button>
        <input type="file" id="license-file" accept=".txt,.key" hidden></div></div>
    <div class="field top"><label for="license-text">Text</label>
      <div class="search lines"><textarea id="license-text" rows="3" placeholder="Paste the license" spellcheck="false"
        autocomplete="off" data-1p-ignore></textarea></div></div>
    <div class="dialog-actions">
      <button type="button" class="text-link" id="license-env">Edit .env</button>
      <button type="button" class="btn" id="license-cancel">Cancel</button>
      <span class="act-slot" id="license-save-slot"></span>
    </div>
  </form>
  <div class="license">
    <dl class="pairs facts" id="license-facts"><div class="empty">${loading("Loading")}</div></dl>
    <div id="license-cta"></div>
    <div class="license-packs">
      <div class="list-tools">
        <label class="search grow">${icon("search")}<input id="license-search" type="search" placeholder="Search…" aria-label="Search the license's packs" autocomplete="off"></label>
        <div class="list-filters">
          <div class="segmented" id="license-filter" role="radiogroup" aria-label="Show">
            <button role="radio" data-filter="all" aria-checked="true">All</button>
            <button role="radio" data-filter="included">Included</button>
            <button role="radio" data-filter="excluded">Not included</button>
          </div>
        </div>
      </div>
      <ul class="packs" id="license-packs"></ul>
    </div>
  </div>`;
document.body.append(dialog);

$("#license-close").addEventListener("click", () => dialog.close());
// A way out of the dialog to a page of the Lab UI
dialog.addEventListener("click", (e) => {
  if (e.target.closest?.("[data-connect-cloud]")) {
    dialog.close();
    document.dispatchEvent(new CustomEvent("lab:connect-cloud"));   // the form that connects an account opens, wherever the Lab UI is
    return;
  }
  const go = e.target.closest?.("[data-go]");
  if (!go) return;
  dialog.close();
  location.hash = go.dataset.go;
});
$("#license-settings").addEventListener("click", () => openForm(true));
$("#license-cancel").addEventListener("click", () => openForm(false));
$("#license-choose").addEventListener("click", () => $("#license-file").click());
$("#license-file").addEventListener("change", (e) => takeFile(e.target.files[0]));
$("#license-text").addEventListener("input", () => {
  given = null;
  drawForm();
});
// A file dropped anywhere on the form
$("#license-form").addEventListener("dragover", (e) => {
  e.preventDefault();
  $("#license-drop").classList.add("over");
});
$("#license-form").addEventListener("dragleave", (e) => {
  if (!e.currentTarget.contains(e.relatedTarget)) $("#license-drop").classList.remove("over");
});
$("#license-form").addEventListener("drop", (e) => {
  e.preventDefault();
  $("#license-drop").classList.remove("over");
  takeFile(e.dataTransfer.files[0]);
});
$("#license-form").addEventListener("submit", (e) => e.preventDefault());
$("#license-save-slot").addEventListener("click", (e) => {
  const button = e.target.closest("#license-save");
  if (button && !button.disabled) saveLicense();
});
// The core's settings, at the field that holds the license: this dialog makes way for the editor
$("#license-env").addEventListener("click", () => {
  const platform = store.state?.platform;
  if (!platform) return;
  dialog.close();
  openFile(platform.env_file, { key: platform.license_var });
});
// The images the license model is read from: once pulled, it is read again (below)
$("#license-pull").addEventListener("click", (e) => {
  if (e.target.closest("[data-pull]")) runJob(() => api("/api/stack/pull", {}), { key: "platform", verb: "pull" });
});
$("#license-facts").addEventListener("click", (e) => {
  if (e.target.closest("[data-owner]")) { showOwner = !showOwner; renderFacts(); }
});
$("#license-search").addEventListener("input", renderPacks);
const setFilter = (value) => {
  filter = value;
  $$("#license-filter button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.filter === filter)));
  renderPacks();
};
$("#license-filter").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-filter]");
  if (b) setFilter(b.dataset.filter);
});
const toggle = (row) => { const id = row.dataset.pack; open.has(id) ? open.delete(id) : open.add(id); renderPacks(); };
$("#license-packs").addEventListener("click", (e) => {
  if (e.target.closest("[data-clear]")) { $("#license-search").value = ""; return setFilter("all"); }
  const row = e.target.closest("[data-pack]");
  if (row) toggle(row);
});
onRowKey($("#license-packs"), "[data-pack]", (row) => {
  toggle(row);
  return `[data-pack="${CSS.escape(row.dataset.pack)}"]`;
});

// Read again once another license is saved (here or in .env itself), and after a job, when it was not read or its
// model was not known: the job may have pulled the images it is read from, or started the platform. The dialog
// follows the state meanwhile.
let working = false;   // a job was running at the last state
store.subscribe((state) => {
  const running = !!state?.job && !state.job.done;
  if ((info && version(saved()) !== read) || (working && !running && (info?.error || info?.model_error))) load();
  working = running;
  if (dialog.open) renderState();
});

export const License = { load, show, chip, pill, summary };
