// What is asked of a person about a catalog, and what is said first. Add is one dialog of two screens: the address (and a branch behind More
// options), then what the catalog adds, read from a preview that is the consent; a known address (a suggestion, a Needs a catalog line) opens
// the second screen at once, on rows that are still bars. Update and Go back say what changes before it does, with Show changes beside the
// button; Remove, Allow and Stop allowing are effects dialogs; New catalog is a form with one field or a prompt for an AI agent. The dialog
// of Add and of Update never traps a person: Cancel and Escape stay live while a request is at work, and closing drops its answer: what the
// request did stays done and is said, but nothing more is done on the strength of it (no allowance, no dialog closed, no page moved). This is
// also where the two actions of the content status live (Try again, Add the listed catalog), for every page that draws it: one click
// handler, so that none of the four places has a copy of it.

import { $, $$, esc, icon, setHTML, loading, api, store, router } from "../core.js";
import { toast, tipHtml, actionButton, paintActions, progressBar, effectsDialog, copyButton } from "../components/ui.js";
import { busy } from "../activity/jobs.js";
import { field, formDialog } from "./form.js";
import { agentLinks } from "../components/agent.js";

export const NO_GIT = "Git is not installed";

// The line of `ticket_expired`: the page's request throws the server's words alone, and this is the one refusal it acts on
const EXPIRED = "This took too long. Start again.";
// The refusal is the code when the request keeps it, else its line
const expired = (err) => err?.code === "ticket_expired" || err?.message === EXPIRED;
const UP_TO_DATE = "Already up to date.";
// The two requests of an update and of going back, each its own route: a path is written whole wherever it is used, so that a route that is
// missing is found by reading the page's files (test_static)
const ROUTES = { update: (id) => `/api/catalogs/${id}/update`, undo: (id) => `/api/catalogs/${id}/undo` };
const RUNS = "Its containers, builds and seeds run with this machine's Docker access.";
const FOLD = 4;        // past this many lines a row folds under a line that counts them (as the effects dialog does)
const GRACE = 120000;  // how long a download asked for is waited for before it says it is there

const KINDS = [["playground", "playground", "playgrounds"], ["setup", "setup", "setups"], ["addon", "add-on", "add-ons"]];
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
// What a catalog holds, one line for each kind that has some: "14 playgrounds", "1 setup"
const countsOf = (counts = {}) => KINDS.filter(([kind]) => counts[kind] > 0).map(([kind, one, many]) => plural(counts[kind], one, many));
const capital = (text) => String(text ?? "").replace(/^./, (c) => c.toUpperCase());
// What a table of words has for a word a server sent: its own entry or nothing (a word like "constructor" is no entry)
const own = (words, word) => (Object.prototype.hasOwnProperty.call(words, word) ? words[word] : undefined);

// What a catalog reads of this machine, as the surface names it: the words of a row, and of a line of Changes
const READS = { license: "your Gravitee license", "gateway token": "your Gravitee Cloud gateway token" };

// "Port 8140", "Ports 8140 and 8141", "Ports 8140, 8141 and 8142", "9 ports" past six
function portsOf(ports) {
  const n = ports.length;
  if (n === 1) return `Port ${ports[0]}`;
  return n > 6 ? `${n} ports` : `Ports ${ports.slice(0, -1).join(", ")} and ${ports[n - 1]}`;
}

// --- Rows ---------------------------------------------------------------------------------------------------------------------

// A row of what an action does: its label, then its items (a name or a count as a pill; `code` for a version or an address, `plain` for a
// word; each with its own `hint`, and `mark`, the information mark that opens what it counts), then the hint of the row. `html` is a value
// the caller made. The label is red in the `danger` tone. Past FOLD items a row is one line that counts them, which opens on a list. A row of
// `names` (the items of a plan) is a list instead, one item to a line with its kind, every one of them shown: the hint of the row over it.
const item = (it) => (typeof it === "string" ? { text: it } : it);
const cell = (it) => {
  const { text, code, plain, tone, hint, mark = "" } = item(it);
  const body = code ? `<code>${esc(text)}</code>` : plain ? `<span>${esc(text)}</span>`
    : `<span class="pill${tone === "danger" ? " err" : ""}">${esc(text)}</span>`;
  return `${body}${mark}${hint ? `<span class="muted">${esc(hint)}</span>` : ""}`;
};

function fold(r) {
  const lines = r.items.map((it) => `<li><span>${esc(item(it).text)}${item(it).hint ? ` <span class="muted">${esc(item(it).hint)}</span>` : ""}</span></li>`).join("");
  return `<details class="choice-row choice-more${r.tone === "danger" ? " danger" : ""}"><summary><span class="choice-row-label">${esc(r.label)}</span>
    <span class="choice-summary">${plural(r.items.length, "item", "items")}${icon("chevron")}${r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}</span></summary><ul class="choice-detail">${lines}</ul></details>`;
}

// An item of a plan on its line: its name, what is said of it, and its kind
const KIND = Object.fromEntries(KINDS.map(([kind, one]) => [kind, capital(one)]));
const named = (it) => `<li><span class="step-name">${esc(it.text)}${it.hint ? ` <span class="muted">${esc(it.hint)}</span>` : ""}</span>${
  own(KIND, it.kind) ? `<span class="step-kind">${KIND[it.kind]}</span>` : ""}</li>`;

function row(r) {
  if (!r.html && !r.items?.length) return "";
  if (r.names) r = { ...r, html: `${r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}<ul class="step-items">${r.items.map(named).join("")}</ul>` };
  if (!r.html && r.items.length > FOLD) return fold(r);
  const value = r.html ?? `${r.items.map(cell).join("")}${r.hint ? `<span class="muted">${esc(r.hint)}</span>` : ""}`;
  return `<div class="choice-row${r.tone === "danger" ? " danger" : ""}"><span class="choice-row-label">${esc(r.label)}</span><span class="choice-items">${value}</span></div>`;
}

// A list of sentences, one to a line, each said whole (what is new, what a change adds): text, not a frame of names
const sentences = (lines) => `<ul class="choice-lines">${lines.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>`;

const mark = (card, label) => tipHtml(icon("info"), card, "plat-info", label);

// What a catalog's items take of this machine, one row for each: what it takes, then the author's reason, which is the author's claim
export const usesCard = (uses = []) => ({ rows: uses.map((u) => ({ subject: u.name, lines: [[capital((u.what || []).join(", "))], ...(u.why ? [[u.why]] : [])] })) });
const blockedCard = (blocked) => ({ rows: blocked.map((b) => ({ subject: b.name, lines: [[(b.reasons || [])[0] ?? ""]] })) });
// The same item in two lists, by its id or by its name: the lists of a preview do not spell an id alike (`@acme-hub/x`, `kubernetes`)
const alike = (a, b) => (a.id != null && a.id === b.id) || (a.name != null && a.name === b.name);

// The items of a preview or of a plan that cannot run: the Blocked ones, and, while the person keeps the items that use this machine blocked,
// those too (an item counts once, however the server names it)
function cannot(blocked = [], uses = [], allow = false) {
  if (allow) return blocked.filter((b) => !uses.some((u) => alike(b, u)));
  return [...blocked, ...uses.filter((u) => !blocked.some((b) => alike(b, u))).map((u) => ({ id: u.id, name: u.name, reasons: (u.what || []).map(capital) }))];
}

const usesRow = (uses) => ({ label: "Uses this machine", items: uses.length
  ? [{ text: plural(uses.length, "item", "items"), plain: true, mark: mark(usesCard(uses), "Uses this machine") }] : [] });

// What the preview of a catalog says it adds, in a fixed order, a row omitted when it is empty
function previewRows(p, allow) {
  const r = p.rows || {}, c = p.catalog || {}, uses = r.uses_machine || [], list = (names) => names.map((name) => ({ text: name }));
  const builds = r.builds || {}, blocked = cannot(p.blocked, uses, allow);
  const built = (builds.local ? [plural(builds.local, "image from source", "images from source")] : [])
    .concat((builds.remote || []).map((source) => `Builds from ${source}`));
  return [
    { label: "Adds", items: countsOf(p.counts) },
    { label: "From", items: p.address ? [{ text: p.address, code: true }] : [], hint: c.kind === "folder" ? "On this machine" : p.audience === "public" ? "Public" : p.audience === "private" ? "Private" : "" },
    // The newest version needs a newer Lab: the one installed is older
    { label: "Version", items: p.newer && c.version ? [{ text: c.version, code: true }] : [], hint: "Newer ones need a newer Lab" },
    { label: "Runs", html: `<span>Containers on this machine</span>${mark(RUNS, "Runs")}` },
    usesRow(uses),
    { label: "Builds", items: built },
    { label: "Publishes", items: (r.ports || []).length ? [portsOf(r.ports)] : [] },
    { label: "Changes", items: list(r.core || []) },
    // The words of the surface start a sentence; the names a catalog gave to its shared settings are shown as they are
    { label: "Reads", items: list((r.reads || []).map((name) => (own(READS, name) ? capital(READS[name]) : name))) },
    { label: "Needs", items: (r.needs || []).map((n) => ({ text: n.name || n.id })), hint: "Not added" },
    { label: "Settings found", items: list((r.found || []).map((f) => f.name || f.id)), hint: "From before the move" },
    { label: "Cannot run", tone: "danger", items: blocked.length ? [{ text: plural(blocked.length, "item", "items"), tone: "danger", mark: mark(blockedCard(blocked), "Cannot run") }] : [] },
  ];
}

// One line for a growth of power, as the plan names it: the item, what it now does (`fact`) and the value of it
const CHANGES = {
  port: (n, v) => `${n} now publishes port ${v}`,
  build: (n, v) => (v === "local" ? `${n} now builds from source` : `${n} now builds from ${v}`),
  core: (n, v) => `${n} now changes the ${v}`,
  reads: (n, v) => `${n} now reads ${own(READS, v) ?? v}`,
  plugins: (n, v) => `${n} now installs plugins for ${v}`,
  seed: (n, v) => `${n} now runs a ${v === "custom" ? "custom " : ""}seed`,
  host: (n, v) => `${n} now ${v}`,
};
const changeLine = ({ name, fact, value }) => (own(CHANGES, fact) ? CHANGES[fact](name, value) : `${name}: ${fact} ${value ?? ""}`.trim());

// What changed in an item, as the server names the parts of its folder ("README.md", "seed/"): the first three, then how many more
const parts = (what = []) => (what.length > 3 ? `${what.slice(0, 3).join(", ")} and ${what.length - 3} more` : what.join(", "));

// The version an update goes to, from the one installed: both when the catalog raised it, the one alone when it did not (the update is then
// the commits since, which the version does not tell apart)
function versionItems(p) {
  if (!p.version) return [];
  if (p.version_from && p.version_from !== p.version) return [{ text: p.version_from, code: true }, { text: "→", plain: true }, { text: p.version, code: true }];
  return [{ text: p.version, code: true, hint: p.version_from ? "Unchanged" : "" }];
}

// What an update, or going back, says it does: its version, the items it touches, each with what changed in it, then what its commits say
// (the authors' words). A history that changed is an error for an update, which goes on only after the person said so (`confirm_history`);
// going back is the person's own choice of an older version, which the server takes as confirmed, so the line is said without the tone that
// asks for care.
function planRows(p, allow, kind) {
  const uses = p.uses_machine || [], blocked = cannot(p.blocked, uses, allow);
  const names = (list = [], hint = () => "") => list.map((x) => ({ text: x.name, kind: x.kind, hint: hint(x) }));
  const tone = kind === "update" ? "danger" : undefined;
  return [
    { label: "Rewritten", tone, items: p.rewritten ? [{ text: "This catalog was rewritten or moved", tone }] : [] },
    { label: "Version", items: versionItems(p) },
    { label: "Updates", names: true, items: names(p.updates, (u) => parts(u.what)) },
    { label: "Adds", names: true, items: names(p.adds) },
    { label: "Removes", names: true, hint: "No longer in the catalog", items: names(p.removes) },
    { label: "Breaks", names: true, items: names(p.breaks, (b) => `Needs ${b.needs}`) },
    { label: "What's new", html: (p.whatsnew || []).length ? sentences(p.whatsnew.slice(0, 5)) : "" },
    usesRow(uses),
    { label: "Changes", html: (p.changes || []).length ? sentences(p.changes.map(changeLine)) : "" },
    { label: "Cannot run", tone: "danger", names: true, items: names(blocked, (b) => (b.reasons || [])[0] ?? "") },
  ];
}

// --- The dialog of Add, Update and Go back ----------------------------------------------------------------------------------------

const dialog = document.createElement("dialog");
dialog.className = "create-dialog platform-dialog catalog-dialog";
dialog.setAttribute("aria-labelledby", "catalog-title");
dialog.innerHTML = `
  <form autocomplete="off">
    <h2 id="catalog-title"></h2>
    <div id="catalog-address">
      <div id="catalog-field"></div>
      <details class="more-options" id="catalog-more"><summary>${icon("chevron")}More options</summary>
        ${field("catalog-branch", "Branch", '<div class="search"><input id="catalog-branch" placeholder="Default" spellcheck="false" autocapitalize="off"></div>')}</details>
      ${progressBar("catalog:check")}
    </div>
    <div id="catalog-effects" hidden>
      <div class="choice-list single" id="catalog-rows"></div>
      <div class="choice-list" id="catalog-choice" role="radiogroup" aria-label="Uses this machine" hidden></div>
    </div>
    <p class="create-problem" role="alert" hidden></p>
    <div class="dialog-actions">
      <button class="btn" type="button" data-cancel>Cancel</button>
      <span class="act-slot" id="catalog-secondary"></span>
      <span class="act-slot" id="catalog-primary"></span>
    </div>
  </form>`;
document.body.append(dialog);

const rowsEl = $("#catalog-rows", dialog), choiceEl = $("#catalog-choice", dialog);
const screens = { address: $("#catalog-address", dialog), effects: $("#catalog-effects", dialog) };
let seq = 0;           // the attempt on screen: an answer that comes for an older one, or after the dialog closed, is dropped
let asking = false;    // the address is being read: a second Enter is the same request
let sending = false;   // the button of the second screen is on its way: a second click or Enter is the same request
let shown = null;      // what the second screen is about: {kind: "read" | "add" | "update" | "undo", ...}
let asked = { text: "", branch: "" };   // the address read last, which a ticket that expired comes back to

function problem(text = "") {
  const el = $(".create-problem", dialog);
  el.hidden = !text;
  el.textContent = text;
}

const slot = (name, html) => {
  if (setHTML($(`#catalog-${name}`, dialog), html)) paintActions(dialog);
};

// A button that waits, saying why: its words are the state it is in
const waiting = (label, why) => `<button class="btn" type="submit" aria-disabled="true" data-why="${esc(why)}">${loading(label)}</button>`;

function screen(name, title) {
  for (const [at, el] of Object.entries(screens)) el.hidden = at !== name;
  $("#catalog-title", dialog).textContent = title;
}

// --- Screen 1: the address ---

const addressTerm = (host) => (host
  ? `A Git address you can read, such as owner/repo, https://${host}/owner/repo or git@${host}:owner/repo.git, or a folder on this machine. Private catalogs use the Git sign-in of this machine.`
  : "A Git address you can read, such as owner/repo or a folder on this machine. Private catalogs use the Git sign-in of this machine.");

// The host that the examples of an address are written with is the first catalog's: it comes from the data, never from this page's words
const exampleHost = () => String(((store.state?.catalogs || []).find((c) => c.kind === "git" && c.address) || {}).address ?? "").split("/")[0];

const address = () => $("#catalog-input", dialog).value.trim();
const whyNot = () => (!address() ? "Enter an address" : /[\s\u0000-\u001f\u007f]/.test(address()) ? "This is not an address Git can read" : "");

// Continue waits for an address Git can read; at work it reads "Reading"
function drawContinue(reading = false) {
  const why = reading ? "Reading the catalog" : whyNot();
  slot("primary", reading ? waiting("Reading", why)
    : `<button class="btn" type="submit" id="catalog-go" ${why ? `aria-disabled="true" data-why="${esc(why)}"` : ""}>Continue</button>`);
  slot("secondary", "");
}

function addressScreen(text = "") {
  shown = null;
  screen("address", "Add catalog");
  setHTML(rowsEl, "");
  setHTML($("#catalog-field", dialog), field("catalog-input", "Address", '<div class="search"><input id="catalog-input" placeholder="owner/repo, a Git address or a folder" spellcheck="false" autocapitalize="off"></div>', [addressTerm(exampleHost())]));
  $("#catalog-input", dialog).value = text;
  drawContinue();
}

// --- Screen 2: what it adds ---

const allowing = () => $$("input", choiceEl).some((el) => el.checked && el.getAttribute("value") === "allow");

// The choice of two, only while the catalog has items that use this machine; drawn once, so that a choice is not taken back
function drawChoice(uses) {
  choiceEl.hidden = !uses.length;
  if (!uses.length) {
    choiceEl.innerHTML = "";
    return;
  }
  if (choiceEl.innerHTML) return;
  const option = (value, label, sub, on) => `<label class="choice"><input type="radio" name="catalog-host" value="${value}" ${on ? "checked" : ""}>
    <span class="choice-text"><strong>${label}</strong><span class="sub">${sub}</span></span></label>`;
  choiceEl.innerHTML = option("keep", "Keep them blocked", "They are listed and cannot start", !shown.host)
    + option("allow", "Allow them to use this machine", "Privileged containers, the Docker socket or folders of this machine", shown.host);
}

function drawRows() {
  const uses = shown.preview ? shown.preview.rows?.uses_machine || [] : shown.plan.uses_machine || [];
  drawChoice(uses);
  const rows = shown.preview ? previewRows(shown.preview, allowing()) : planRows(shown.plan, allowing(), shown.kind);
  setHTML(rowsEl, rows.map(row).join(""));
}

function effectsScreen(title) {
  screen("effects", title);
  choiceEl.innerHTML = "";
  problem();
}

// The rows are bars until the preview comes; the button reads "Reading" and says why it waits
function reading(name) {
  shown = { kind: "read" };
  effectsScreen(name ? `Add ${name}` : "Add");
  choiceEl.hidden = true;
  setHTML(rowsEl, '<div class="plat-skel" aria-hidden="true"><span></span><span></span><span></span></div>'.repeat(3));
  rowsEl.setAttribute("aria-busy", "true");
  slot("secondary", "");
  slot("primary", waiting("Reading", "Reading the catalog"));
}

async function read(text, branch, direct, host = false) {
  const mine = ++seq;
  asked = { text, branch };
  asking = true;
  if (!direct) drawContinue(true);
  try {
    const preview = await api("/api/catalogs/check", branch ? { address: text, branch } : { address: text });
    if (mine !== seq) return;
    rowsEl.removeAttribute("aria-busy");
    shown = { kind: "add", preview, host };
    effectsScreen(`Add ${preview.catalog?.name ?? ""}`.trim());
    drawRows();
    slot("secondary", "");
    slot("primary", actionButton({ key: `catalog-op:${preview.catalog.id}`, verb: "add", label: "Add" }));
    ($$("input", choiceEl).find((el) => el.checked) || $("#catalog-primary button", dialog))?.focus();
  } catch (err) {
    if (mine !== seq) return;
    // A known address that cannot be read leaves the person on the address, which they can mend, with the refusal in the server's words
    if (direct) addressScreen(text);
    else drawContinue();
    problem(err.message);
    $("#catalog-input", dialog).focus();
  } finally {
    if (mine === seq) asking = false;
  }
}

// Adds the catalog the preview showed; with the choice made to allow, then the allowance. The dialog may be closed while the request is at
// work: the catalog is added all the same and that is said, but the allowance the person chose is not given once they walked away from the
// dialog that asked for it, and the page does not move under whatever they have opened since.
async function add() {
  const mine = seq, { preview } = shown, id = preview.catalog.id, allow = allowing();
  const live = () => mine === seq;
  sending = true;
  try {
    const answer = await busy(`catalog-op:${id}`, "add", api("/api/catalogs", { ticket: preview.ticket }));
    if (allow && live() && answer.catalog?.host !== "allowed") {
      try {
        await api(`/api/catalogs/${id}/allow`, { enabled: true });
      } catch (err) {
        toast(err.message, true);
      }
    }
    const stay = live();
    if (stay) dialog.close();
    toast("Added");
    await store.refresh();
    if (stay) reveal(id);
  } catch (err) {
    if (!live()) toast(err.message, true);
    else if (expired(err)) {
      addressScreen(asked.text);
      problem(err.message);
    } else problem(err.message);
    store.refresh();
  } finally {
    if (live()) sending = false;   // an attempt that was left is no longer the one that is sending
  }
}

// --- Update and Go back ---

// A listed catalog whose plan has nothing to decide is updated at once: no history change, no growth, nothing Blocked, held or broken
const plain = (p) => !p.rewritten && !(p.changes || []).length && !(p.blocked || []).length && !p.held && !(p.breaks || []).length && !(p.uses_machine || []).length;

// What applies a plan: its ticket, and, for an update, the confirmation of a history that changed. Going back sends no confirmation: the server
// takes an undo as confirmed, and the dialog asks nothing more of it than of an update with nothing to decide (a neutral button, Enter).
const applying = (route, p) => (route === "update" && p.rewritten ? { ticket: p.ticket, confirm_history: true } : { ticket: p.ticket });
// Whether a plan asks for care: an update of a history that changed (its button is Update anyway, in the danger tone, and Enter never presses it)
const careful = (kind, p) => kind === "update" && !!p?.rewritten;

// The check of a plan, and, for a listed catalog with nothing to decide, its apply: one request at work from the click to the end of it. A
// key that was released between the two would put the row's button back to rest while the update is still applied, and let a second click
// start a second one. The row's word is "Checking" while the plan is read, and "Updating" while it is applied: the apply takes the key
// before the check lets it go.
async function ask(row, route, title, word) {
  const at = `catalog-op:${row.id}`, update = route === "update";
  const flow = async () => {
    const plan = await api(ROUTES[route](row.id), { check: true });
    if (plan.upToDate || !update || !row.listed || !plain(plan)) return { plan, applied: false };
    const sent = busy(at, "update", (async () => {
      await api(ROUTES[route](row.id), applying(route, plan));
      await store.refresh();   // the row is released with the state it has now, not the one before
    })(), { quiet: true });
    store.notify();   // the row says Updating from now
    await sent;
    return { plan, applied: true };
  };
  let found;
  try {
    const sent = busy(at, "check", flow(), { quiet: true });
    store.notify();   // the row says what is at work from the click, not from the next poll
    found = await sent;
  } catch (err) {
    toast(err.message, true);
    store.refresh();
    return;
  }
  const { plan: p, applied } = found;
  if (p.upToDate) {
    toast(UP_TO_DATE);
    store.refresh();
    return;
  }
  if (applied) {
    toast("Updated");
    return;
  }
  seq += 1;
  sending = false;
  shown = { kind: route, row, plan: p, host: false };
  effectsScreen(title);
  drawRows();
  rowsEl.removeAttribute("aria-busy");
  const danger = careful(route, p), why = p.held || "";
  slot("secondary", '<button class="btn" type="button" data-changes>Show changes</button>');
  // The dialog's button has a key of its own: a dialog opened while the check still holds the row's key at work does not open at work too
  slot("primary", actionButton({ key: `catalog-dialog:${row.id}`, verb: "update", label: danger ? `${word} anyway` : word, cls: danger ? "danger" : "",
    attrs: why ? `aria-disabled="true" data-why="${esc(why)}"` : "" }));
  if (!dialog.open) dialog.showModal();
  // Enter confirms what is harmless: with the history changed the keyboard starts on Cancel
  ($$("input", choiceEl).find((el) => el.checked) || (danger ? $("[data-cancel]", dialog) : $("#catalog-primary button", dialog)))?.focus();
}

// Update opens what changes; a listed catalog with nothing to decide is updated by the click
export const openUpdate = (row) => ask(row, "update", `Update ${row.name}`, "Update");
export const openGoBack = (row) => ask(row, "undo", "Go back to previous version", "Go back");

// A plan whose ticket expired is dead: nothing in the dialog can send it again, so the dialog goes and the person starts again from the row
function lapse(err) {
  dialog.close();
  toast(err.message, true);
}

// Sends the plan on screen. The dialog's own button works with the row's key. As with Add, the dialog may be closed meanwhile: the update
// is done all the same and said, but the allowance chosen with it is not given once the person walked away from it.
async function applyShown() {
  const mine = seq, { row, plan, kind } = shown, allow = allowing();
  const live = () => mine === seq;
  sending = true;
  try {
    const sent = busy(`catalog-op:${row.id}`, "update", api(ROUTES[kind](row.id), applying(kind, plan)), { quiet: true });
    busy(`catalog-dialog:${row.id}`, "update", sent).catch(() => {});
    store.notify();
    await sent;
    if (allow && live()) {
      try {
        await api(`/api/catalogs/${row.id}/allow`, { enabled: true });
      } catch (err) {
        toast(err.message, true);
      }
    }
    toast("Updated");
    store.refresh();
    if (live()) dialog.close();
  } catch (err) {
    if (!live()) toast(err.message, true);
    else if (expired(err)) lapse(err);
    else problem(err.message);
    store.refresh();
  } finally {
    if (live()) sending = false;
  }
}

// --- Show changes: the files an update changes, with a unified diff of each ---

const changes = document.createElement("dialog");
changes.className = "editor-dialog";
changes.setAttribute("aria-labelledby", "catalog-changes-title");
changes.innerHTML = `
  <div class="dialog-head"><h2 id="catalog-changes-title"></h2>
    <button class="icon-btn" type="button" data-close aria-label="Close" data-tip="Close">${icon("close")}</button></div>
  <div class="compose">
    <div class="compose-files"><ul class="file-list" id="catalog-files"></ul></div>
    <div class="compose-editor"><pre class="diff" id="catalog-diff" tabindex="0"></pre></div>
  </div>`;
document.body.append(changes);

const STATUS = { added: "Added", changed: "Changed", removed: "Removed" };
// The kind of each line of a unified diff. The file's own lines (`---`, `+++`) are only the ones before the first hunk; inside a hunk a line
// is what its first character says, so that a removed `---` (a YAML document mark) or an added `++` stays red or green.
function kindsOf(lines) {
  let inHunk = false;
  return lines.map((line) => {
    if (line.startsWith("@@")) {
      inHunk = true;
      return "hunk";
    }
    if (!inHunk && /^(\+\+\+|---)/.test(line)) return "meta";
    return line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
  });
}
let files = [];

// The files, the one at `at` chosen, and its diff: a file that is binary or too large (the server sends no diff) reads "Changed, not shown"
function drawFiles(at) {
  setHTML($("#catalog-files", changes), files.map((f, i) => `<li><button type="button" class="file-item${i === at ? " active" : ""}" data-file-at="${i}" aria-current="${i === at}">
    <span class="name">${esc(f.path)}</span><span class="muted">${esc(own(STATUS, f.status) ?? f.status)}</span></button></li>`).join(""));
  const diff = files[at]?.diff, lines = typeof diff === "string" ? diff.split("\n") : null, kinds = lines && kindsOf(lines);
  setHTML($("#catalog-diff", changes), lines ? lines.map((line, i) => `<span class="diff-${kinds[i]}">${esc(line)}</span>`).join("")
    : '<span class="diff-meta">Changed, not shown</span>');
}

changes.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) changes.close();
  const at = e.target.closest("[data-file-at]")?.dataset.fileAt;
  if (at !== undefined) drawFiles(Number(at));
});

async function showChanges() {
  const mine = seq, { row, plan } = shown;
  try {
    const answer = await api(`/api/catalogs/${row.id}/changes`, { ticket: plan.ticket });
    if (mine !== seq) return;   // the dialog it was asked for is gone
    files = answer.files || [];
    $("#catalog-changes-title", changes).textContent = `Changes in ${row.name}`;
    drawFiles(0);
    changes.showModal();
    $(".file-item", changes)?.focus();
  } catch (err) {
    if (mine !== seq) return;
    if (expired(err)) lapse(err);
    else problem(err.message);
  }
}

// --- The dialog's own events ---

dialog.addEventListener("input", (e) => {
  if (e.target.id === "catalog-input") {
    problem();
    if (!asking) drawContinue();
  }
});
dialog.addEventListener("change", (e) => {
  if (e.target.getAttribute("name") === "catalog-host" && shown) drawRows();
});
dialog.addEventListener("click", (e) => {
  if (e.target.closest("[data-cancel]")) dialog.close();
  else if (e.target.closest("[data-changes]")) showChanges();
});
// Enter confirms a neutral dialog, never one that goes on after the history changed
dialog.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && shown && careful(shown.kind, shown.plan) && e.target.matches("input")) e.preventDefault();
});
dialog.addEventListener("close", () => {
  seq += 1;   // an answer on its way is dropped; what the server fetched stays in its cache for the next try
  shown = null;
  asking = sending = false;
});
$("form", dialog).addEventListener("submit", (e) => {
  e.preventDefault();
  if (sending || asking) return;
  if (!shown && !screens.address.hidden) {
    if (whyNot()) return;
    problem();
    read(address(), $("#catalog-branch", dialog).value.trim(), false);
  } else if (shown?.kind === "add") add();
  else if (["update", "undo"].includes(shown?.kind) && !shown.plan.held) applyShown();
});

// Add catalog: the address first. With an address already known (a suggestion, a Needs a catalog line) the second screen opens at once,
// its rows still bars, and the Add button reads "Reading" until the preview comes. `name` is what it is called, `host` that the suggestion
// says its items use this machine (the choice is then preselected to allow them).
export function openAdd({ address: known = "", name = "", host = false } = {}) {
  seq += 1;
  asking = sending = false;
  $("#catalog-branch", dialog).value = "";
  $("#catalog-more", dialog).open = false;
  addressScreen(known);
  problem();
  if (!dialog.open) dialog.showModal();
  if (known) {
    reading(name);
    read(known, "", true, host);
  } else {
    $("#catalog-input", dialog).focus();
  }
}

// --- Adding without this dialog, and the actions of the content status ---------------------------------------------------------

// A success the person is told of once it is there: a download that was asked for ends in the state, not in the answer. It is waited for
// a while: one that failed, and is tried again by itself much later, is nothing the person asked for.
const announced = new Map();

store.subscribe(() => {
  const rows = Array.isArray(store.state?.catalogs) ? store.state.catalogs : [];
  for (const [id, one] of [...announced]) {
    const found = rows.find((c) => c.id === id && c.kind !== "suggested");
    if (found?.commit && typeof found.progress !== "number") {
      announced.delete(id);
      toast(one.word);
    } else if (Date.now() - one.at > GRACE) announced.delete(id);
  }
});

function announce(id, word, found) {
  if (found?.commit && typeof found.progress !== "number") toast(word);
  else announced.set(id, { word, at: Date.now() });
}

// Downloads the catalog again: the request answers at once, the worker does the work, the row says how far it is
export async function tryAgain(id) {
  if (!id) return;
  try {
    announce(id, "Downloaded", (await api(`/api/catalogs/${id}/check`, {})).catalog);
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}

async function addListed(id) {
  try {
    announce(id, "Added", (await busy(`catalog-op:${id}`, "add", api("/api/catalogs", { listed: id }))).catalog);
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}

// The row that was just added or created, in view and focused, when the Catalogs page is the one on screen. A dialog the person has open
// meanwhile is not closed under them (the router closes every dialog as the address changes): the row is there to be found.
function reveal(id) {
  if (router.current?.page.id === "catalogs" && !$$("dialog").some((d) => d.open)) router.go(`#/catalogs/${id}`);
}

// Adds a catalog that is listed, with no dialog, or one that is not, through the preview: `row` is a Not added row of the page, or a catalog
// an item needs ({id, name, url})
export function addCatalog(row) {
  const listed = (store.state?.catalogs || []).some((c) => c.kind === "suggested" && c.listed && c.id === row.id);
  if (listed) return addListed(row.id);
  return openAdd({ address: row.url || row.address || "", name: row.name || "", host: row.host_access === true });
}

// Try again and Add the listed catalog, wherever the content status draws them (the Dashboard, the three lists, the Welcome foot, the Catalogs page)
document.addEventListener("click", (e) => {
  const button = e.target.closest?.("[data-content-act]");
  if (!button || button.getAttribute("aria-disabled") === "true") return;
  const { contentAct: act, catalog: id } = button.dataset;
  if (act === "retry") tryAgain(id);
  else if (act === "add") addListed(id);
});

// --- Remove, Allow ------------------------------------------------------------------------------------------------------------

// What a removal leaves in Gravitee, as lines like "3 APIs", "1 API", "2 applications": a list of {count, what}, of words, or an object of counts
// by word. The server says the kind in the plural; one of it is said in the singular, and "API" is the acronym it is.
const counted = (n, what) => {
  const kind = String(what);
  if (/^apis?$/i.test(kind)) return `${n} ${n === 1 ? "API" : "APIs"}`;
  return `${n} ${n === 1 ? kind.replace(/ies$/, "y").replace(/s$/, "") : kind}`;
};
const stayed = (stays) => (Array.isArray(stays) ? stays.map((s) => (typeof s === "string" ? s : counted(s.count, s.what)))
  : Object.entries(stays || {}).map(([what, n]) => counted(n, what)));

export async function openRemove(row) {
  const at = `catalog-op:${row.id}`;
  let plan;
  try {
    // What reads says "Checking" on the row; the word of removing is for the request that removes
    const sent = busy(at, "check", api(`/api/catalogs/${row.id}/delete`, { check: true }), { quiet: true });
    store.notify();
    plan = await sent;
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (plan.held) {
    toast(plan.held, true);
    store.refresh();
    return;
  }
  const steps = [
    { label: "Removes", items: [plan.folder ? "The link to this folder" : plural(plan.items ?? 0, "item", "items")], hint: plan.folder ? "Your files stay" : "", tone: "danger" },
    { label: "Breaks", items: (plan.breaks || []).map((b) => b.name), hint: `Needs ${row.name}`, note: true },
    { label: "Stays in Gravitee", items: stayed(plan.stays), note: true },
  ];
  // A folder catalog has no settings of its own to keep: only the link goes
  const options = plan.folder ? [{ label: "Remove", value: { keep: true } }] : [
    { label: "Keep my settings", sub: "Settings and what its playgrounds created, if you add it again", value: { keep: true } },
    { label: "Delete them too", sub: "Settings and records of what its playgrounds created", value: { keep: false } }];
  const chosen = await effectsDialog({ title: `Remove ${row.name}`, steps, options, action: () => "Remove", danger: () => true });
  if (!chosen) return;
  try {
    const sent = busy(at, "remove", api(`/api/catalogs/${row.id}/delete`, { keep_settings: chosen.keep }), { quiet: true });
    store.notify();
    const done = await sent;
    // Files that Docker made could not all be deleted: the line says where, which is the fix
    if (done.warning) toast(done.warning, true);
    else toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}

// Lets the items that use this machine run, or stops it: stopping is held while one of them is on (the page disables its entry). Allowing is
// the one consent that gives a catalog's items the Docker socket or privileged containers: the button is in the danger tone and the keyboard
// starts on Cancel, so that Enter never grants it; stopping takes power away and is harmless.
export async function openAllow(row, enabled) {
  const uses = row.uses_machine || [], title = enabled ? "Allow use of this machine" : "Stop allowing use of this machine";
  const steps = enabled ? [{ label: "Uses this machine", items: uses.map((u) => `${u.name}: ${(u.what || []).join(", ")}`) }]
    : [{ label: "Blocks", hint: "They stay listed and cannot start", items: uses.map((u) => u.name) }];
  if (!(await effectsDialog({ title, steps, options: [{ label: title, value: true }], action: () => (enabled ? "Allow" : "Stop allowing"), danger: () => enabled }))) return;
  try {
    await api(`/api/catalogs/${row.id}/allow`, { enabled });
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}

// --- New catalog ----------------------------------------------------------------------------------------------------------------

// A folder catalog made from the template and linked, from one field; or the prompt that has an AI agent do it, as New playground has
const NEW_FIELDS = `
  <div class="create-choices" id="catalog-new-how">
    <div class="segmented" role="radiogroup" aria-label="How it is made">
      <button type="button" role="radio" data-how="form">Form</button>
      <button type="button" role="radio" data-how="agent">AI agent</button>
    </div>
  </div>
  <div id="catalog-new-form">${field("catalog-new-name", "Name", '<div class="search"><input id="catalog-new-name" maxlength="60" spellcheck="false" autocapitalize="off" required></div>')}</div>
  <div class="field top" id="catalog-new-task" hidden><label for="catalog-task">Task</label>
    <div class="search lines"><textarea id="catalog-task" rows="6" placeholder="What it should hold"></textarea></div></div>
  <dl class="pairs" id="catalog-new-agent" hidden></dl>`;

let how = "form";
const line = (name, value, copy) => `<div><dt>${name}</dt><dd>${value}${copy ? copyButton(copy, `Copy ${name.toLowerCase()}`) : ""}</dd></div>`;

// The form or the prompt: a prompt is copied, there is nothing to create here
function drawNew() {
  const skill = store.state?.skills?.catalog, agent = how === "agent" && !!skill, root = store.state?.root;
  $$("[data-how]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.how === (agent ? "agent" : "form"))));
  $("#catalog-new-form").hidden = agent;
  $("#catalog-new-agent").hidden = !agent;
  $("#catalog-new-task").hidden = !agent;
  $("#catalog-new-how").hidden = !skill;
  $("[data-act-for='new:catalog']").hidden = agent;
  const dialog = $("#catalog-new-how").closest("dialog");
  $("[data-cancel]", dialog).textContent = agent ? "Close" : "Cancel";
  let links = $("#catalog-new-go", dialog);
  if (!links) {
    links = Object.assign(document.createElement("span"), { id: "catalog-new-go", className: "agent-actions" });
    $("[data-cancel]", dialog).after(links);
  }
  links.hidden = !agent;
  if (!agent) return;
  const task = $("#catalog-task").value.trim();
  const prompt = `Use the ${skill.split("/").slice(-2)[0]} skill (${skill}) to create a catalog.${task ? ` What it should hold: ${task}` : ""}`;
  $("#catalog-new-agent").innerHTML = line("Prompt", `<code class="snippet">${esc(prompt)}</code>`, prompt) + (root ? line("Folder", `<code>${esc(root)}</code>`, root) : "");
  setHTML(links, agentLinks(prompt, root));
}

const openForm = formDialog("catalog-new", NEW_FIELDS, { key: "new:catalog", verb: "create", label: "Create" },
  () => api("/api/catalogs/new", { name: $("#catalog-new-name").value.trim() }),
  async (answer) => {
    toast("Created");
    await store.refresh();
    reveal(answer.catalog.id);
  }, () => ($("#catalog-new-name").value.trim() ? "" : "Enter a name"));

$("#catalog-task").addEventListener("input", drawNew);
$("#catalog-new-how").addEventListener("click", (e) => {
  const choice = e.target.closest("[data-how]");
  if (!choice) return;
  how = choice.dataset.how;
  drawNew();
});

export function openNew() {
  how = "form";
  openForm("New catalog", () => {
    $("#catalog-new-name").value = "";
    drawNew();
  });
}
