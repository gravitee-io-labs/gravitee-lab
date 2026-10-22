// The Plugins page: the catalog of a product next to what the product loads, and changes applied together in one job

import { $, $$, esc, icon, setHTML, loading, ago, compareVersions, api, store, drafts } from "../core.js";
import { Pager, emptyHtml, confirmEffects, refocus, actionButton, paintActions, paintBusy, dockerDown,
  noteBadge, pluginWords, applyBadge, behindTip, behindWhy, setOff } from "../components/ui.js";
import { runJob, busy, runPlatform } from "../activity/jobs.js";
import { Combo, CategoryFilter } from "../components/combo.js";
import { openFile } from "../dialogs/editor.js";
import { License } from "../dialogs/license.js";

let product = null;   // the id of the product on screen
// By product: the catalog, the types of its plugins as their artifact names say them, what is installed, the changes not applied yet.
// Versions by catalog path, the highest first.
const catalog = {}, words = {}, installed = {}, pending = {}, versions = {};
// The changes of the product on screen, by plugin name: {type: "install", path, version} | {type: "remove", file}
let changes = new Map();
// The apply sent, until it ended: {id, product, changes, sent, ok}, the changes of its product and those it was sent.
// They stay chosen until it ended well and what is installed was read again; one that did not end well leaves them
// chosen, to apply again.
let applying = null;
const NO_FILTER = { q: "", state: "all", edition: "all" };
const filters = { ...NO_FILTER };
let categories = null;   // the category filter
let pager = null;
let stale = false;   // what is installed has to be read again
let ended = null;    // the last finished job seen
let license = "";    // the license saved, as the state last said it: what the rows say of it follows another one

// The type of a plugin as its artifact name says it, which the page says already: its category in the singular
// ("policies" gives "policy"; "secret-providers" gives "secret", "provider" and "secretprovider")
function typeWords(plugins) {
  const singular = (word) => word.replace(/ies$/, "y").replace(/s$/, "");
  const types = [...new Set(plugins.map((p) => p.category))];
  return types.flatMap((c) => [...c.split("-"), c.replace(/-/g, "")]).map(singular);
}

// "gravitee-policy-rate-limit" shows as "rate-limit", over the full artifact name
const pretty = (name) => pluginWords(name, words[product]).join("-");
const capital = (category) => category[0].toUpperCase() + category.slice(1);   // "policies" shows as "Policies"
const isRelease = (v) => !/-/.test(v);
// The tag of an Enterprise Edition plugin after its name: the two letters on screen, the words after the name for a screen reader
const EE = '<span class="pill"><span aria-hidden="true">EE</span><span class="sr-only">, Enterprise Edition</span></span>';
const bundledVersions = (r) => [...new Set(Object.values(r.bundled || {}))];
const size = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

// One line in place of the rows: loading, or why there are none (no product, a catalog or what the product loads
// that cannot be read)
function say(html) {
  setHTML($("#plugins"), "");
  if (setHTML($("#plugins-empty"), html)) {
    paintBusy($("#plugins-empty"));
    paintActions($("#plugins-empty"));
  }
  $("#plugins-empty").hidden = false;
  pager.slice([]);
}

// Why what the images of a product bundle is not known: "Bundled plugins unknown", then the server's reason. An image
// not pulled, with what pulls it; Docker not answering; anything else in the error tone.
function unknown(answer) {
  const why = esc(/^Bundled plugins unknown/.test(answer.bundled_error) ? answer.bundled_error
    : `Bundled plugins unknown: ${answer.bundled_error}`);
  if (answer.pull) {
    return `${why} ${actionButton({ key: "platform", verb: "pull", label: "Pull images",
      icon: "download", cls: "primary", attrs: `data-busy data-pull data-tip="Pull ${esc(answer.pull)}"` })}`;
  }
  return store.state && dockerDown() ? why : `<span class="err-text">${why}</span>`;
}

// Why no product is listed, and where it is told: the core's file, which no service of mounts a plugins folder, or
// which does not load; nothing to do while Docker does not answer
function noProduct() {
  const { docker, blind, wait, platform } = store.state;
  if (docker.error) return esc(wait);
  const edit = `<button class="text-link" data-file="${esc(platform.file)}">Edit ${esc(platform.file)}</button>`;
  return `${esc(blind ? wait : "No product loads plugins")}${edit}`;
}

// What an apply that ended well was sent goes once what is installed was read again (`id` its product); what was
// changed meanwhile stays
function settleApply(id) {
  if (!applying?.ok || applying.product !== id) return;
  for (const [name, c] of applying.sent) if (applying.changes.get(name) === c) applying.changes.delete(name);
  applying = null;
}

// Reads the catalog of the product on screen and what it loads, then draws them. Answers whether both were read:
// what was not is said in place of the rows.
async function load(force = false) {
  const id = product;
  if (!id) {
    say(noProduct());
    return false;
  }
  // Refresh reads the catalog and the version lists from the download site again
  if (force) {
    delete catalog[id];
    for (const path of Object.keys(versions)) delete versions[path];
  }
  if (!catalog[id]) {
    say(loading("Loading"));
    try {
      const { plugins } = await api(`/api/plugins/catalog?product=${id}${force ? "&refresh=1" : ""}`);
      words[id] = typeWords(plugins);
      catalog[id] = plugins;
    } catch (err) {
      if (id === product) say(`<span class="err-text">${esc(err.message)}</span>`);
      return false;
    }
  }
  // Until what it loads is read, the rows of the product shown before do not stay under its head
  if (!installed[id] && id === product) say(loading("Loading"));
  // When what the images bundle cannot be read (an image not pulled, Docker not answering), the rows would say
  // "Not installed" of it: the reason shows in their place
  let read = true;
  try {
    const answer = await api(`/api/plugins/installed?product=${id}`);
    if (answer.bundled_error) {
      delete installed[id];
      if (id === product) say(unknown(answer));
      read = false;
    } else {
      installed[id] = answer;
      settleApply(id);
    }
  } catch (err) {
    delete installed[id];
    if (id === product) say(`<span class="err-text">${esc(err.message)}</span>`);
    read = false;
  }
  if (id === product) draw();   // the rows, or the line that says why there are none; Apply follows
  return read;
}

// Refresh: its button says so while the catalog is read again, then how it went. A catalog that could not be read
// makes it fail; why is said in place of the rows.
function refresh() {
  busy(`catalog:${product}`, "refresh", load(true).then((read) => read || Promise.reject(new Error("Not read"))));
}

// The edition of a plugin, from two signals: the folder of the download site that publishes it (the catalog's `edition`), and the
// license feature it needs (`feature`, which the plugin declares itself and is read once it is installed). Every tier of the license
// model is an Enterprise one, so a plugin that needs a feature of the license is an Enterprise Edition plugin wherever it is
// published (the Gamma authorization plugins bundled in the images, an XSLT policy of the Community folder); one of the Enterprise
// folder is one whether it needs a license or not. Any other is a Community Edition plugin, one missing from the catalog too.
const editionOf = (p, feature) => (p.edition === "enterprise" || feature ? "enterprise" : "community");

// The catalog of the product on screen with what it loads; nothing until both are read
function rows() {
  const inst = installed[product];
  if (!catalog[product] || !inst) return [];
  const byName = new Map(catalog[product].map((p) => [p.name, p]));
  // Installed plugins missing from the catalog still show, read-only
  for (const name of new Set([...Object.keys(inst.bundled), ...Object.keys(inst.ext)])) {
    if (!byName.has(name)) byName.set(name, { name, category: "other", edition: "", path: null });
  }
  return [...byName.values()].map((p) => ({ ...p, edition: editionOf(p, inst.features?.[p.name]), label: pretty(p.name),
    bundled: inst.bundled[p.name], ext: inst.ext[p.name] }));
}

// --- What a row shows ---------------------------------------------------------------

function isChecked(r) {
  const c = changes.get(r.name);
  if (c?.type === "remove") return !!r.bundled;          // the bundled copy takes over again
  if (c?.type === "install") return true;
  return !!(r.ext || r.bundled);
}

function lockReason(r) {
  if (!r.path && !r.ext) return "Not in the catalog";
  if (r.bundled && !r.ext && changes.get(r.name)?.type !== "install") return "Bundled";
  return "";
}

// The state of a row as the tone of its pill, its word and the line under it: what a change not applied yet will
// do, else where the running version comes from. The version itself is in the Version column.
function stateOf(r) {
  const c = changes.get(r.name), bundled = bundledVersions(r).join(", ");
  if (c?.type === "remove") return ["warn", "Will remove", bundled && `Bundled ${bundled} takes over`];
  if (c) return ["warn", r.ext ? "Will update" : "Will install", r.ext ? `from ${r.ext.version}` : bundled && `Replaces the bundled ${bundled}`];
  if (r.ext?.active) return ["", "Added", bundled && `Replaces the bundled ${bundled}`];
  if (r.ext) return ["warn", "Added, ignored", `The bundled ${bundled} is newer`];
  if (!r.bundled) return ["", "", "Not installed"];
  const services = Object.keys(r.bundled);
  return ["", "Bundled", services.length < installed[product].services.length
    ? `${services.map((s) => store.service(s)?.label || s).join(", ")} only` : ""];
}

// After the pill, on its line, the note of the state; in its place, in the server's words of a row (`licensed`), what the
// license says of the feature the plugin needs ("Needs a license", "License expired", "Not in the license"), as a badge
// that leads to the form that takes a new license, unless a change says what it will do
function stateCell(r) {
  const [tone, word, note] = stateOf(r), licensed = !changes.has(r.name) && installed[product].licensed?.[r.name];
  const under = licensed ? noteBadge(licensed)
    : note ? `<span class="list-note" data-tip="${esc(note)}" data-tip-cut>${esc(note)}</span>` : "";
  return `<div class="list-state">${word ? `<span class="pill ${tone}">${word}</span>` : ""}${under}</div>`;
}

// The version that runs, or will once applied: "" means the bundled one, or none
function chosenVersion(r) {
  const c = changes.get(r.name);
  if (c) return c.type === "install" && c.version || "";
  return r.ext?.active ? r.ext.version : "";
}

// The field stays on a removal: choosing the version again undoes it
function versionCell(r) {
  if (!r.path) return `<span class="field-text">${r.ext ? esc(r.ext.version) : r.bundled ? esc(bundledVersions(r).join(", ")) : "Not in the catalog"}</span>`;
  const shown = chosenVersion(r) || (r.bundled ? bundledVersions(r).join(", ") : "");
  return `<button class="combo-btn ${changes.has(r.name) ? "changed" : ""}" data-version-for="${esc(r.name)}">
    <span class="value ${shown ? "" : "placeholder"}">${esc(shown || "Choose a version")}</span>${icon("chevron")}</button>`;
}

// The version list of a plugin, for the shared picker
function versionSource(r) {
  const bundled = bundledVersions(r);
  const badges = (v) => [bundled.includes(v) && "Bundled", r.ext?.version === v && "Added"].filter(Boolean);
  return {
    current: chosenVersion(r),
    load: async () => { await loadVersions(r); if (versions[r.path]?.error) throw new Error(versions[r.path].error); },
    groups: (q) => {
      const list = (versions[r.path] || []).filter((v) => !q || v.version.toLowerCase().includes(q));
      const item = (v) => ({ value: bundled.includes(v.version) && !r.ext ? "" : v.version, name: v.version,
        badges: badges(v.version), meta: `${size(v.size)}, ${ago(v.updated)}` });
      const groups = [
        { label: "Releases", items: list.filter((v) => isRelease(v.version)).map(item) },
        { label: "Pre-releases", items: list.filter((v) => !isRelease(v.version)).map(item) },
      ];
      // The bundled version, when the download site does not have it
      if (r.bundled && !r.ext && !list.some((v) => bundled.includes(v.version)) && (!q || bundled.join(" ").includes(q))) {
        groups.unshift({ label: "In the image", items: [{ value: "", name: bundled.join(", "), badges: ["Bundled"], meta: "" }] });
      }
      return groups;
    },
    choose: (v) => setVersion(r, v),
  };
}

const featureOf = (r) => installed[product]?.features?.[r.name];
// How a plugin is installed, in the words of its row: added (an ignored one too), bundled, or not installed
const installedAs = (r) => (r.ext ? "added" : r.bundled ? "bundled" : "none");

function row(r) {
  const lock = lockReason(r);
  return `
    <tr class="${changes.has(r.name) ? "pending" : ""}" data-name="${esc(r.name)}">
      <td><input type="checkbox" ${isChecked(r) ? "checked" : ""} ${lock ? `aria-disabled="true" data-why="${esc(lock)}"` : ""} aria-label="${esc(r.label)} installed"></td>
      <td><div class="list-name" data-tip="${esc(r.label)}" data-tip-cut>${esc(r.label)}${r.edition === "enterprise" ? EE : ""}</div>
          <div class="plugin-artifact" data-tip="${esc(r.name)}" data-tip-cut>${esc(r.name)}</div></td>
      <td class="list-col">${esc(capital(r.category))}</td>
      <td>${stateCell(r)}</td>
      <td class="version-cell">${versionCell(r)}</td>
    </tr>`;
}

function drawTable() {
  if (!catalog[product] || !installed[product]) return;   // the loading line, or why it cannot be read, stays
  const q = filters.q.toLowerCase();
  const visible = categories.items().filter((r) => {
    if (q && !`${r.name} ${r.label} ${featureOf(r) || ""}`.toLowerCase().includes(q)) return false;
    if (filters.edition !== "all" && r.edition !== filters.edition) return false;
    // The State filter has the words of the rows, as installed: a change not applied yet keeps its row in place
    if (filters.state !== "all" && filters.state !== installedAs(r)) return false;
    return true;
  }).sort((a, b) => (changes.has(b.name) - changes.has(a.name)) || (!!b.ext - !!a.ext) || a.label.localeCompare(b.label));
  setHTML($("#plugins"), pager.slice(visible).map(row).join(""));
  $("#plugins-empty").hidden = visible.length > 0;
  setHTML($("#plugins-empty"), emptyHtml({ filtered: true }));
}

// A change of `list` sent to the apply on its way, or to one that ended well until what is installed is read again:
// it is being applied, no draft
const sent = (list, name, c) => applying?.changes === list && applying.sent.get(name) === c;
// The changes of `list` that are drafts, by plugin name
const drafted = (list) => [...list].filter(([name, c]) => !sent(list, name, c));

// What was chosen, in the page head as in the editor: that it is not applied yet, and its buttons. Applying says
// first what it installs and restarts, from what the product loads: it waits for that to be read. With nothing chosen, Apply is the
// platform's while something differs from what is saved, as on Images: one Apply on the page, the head's, with the badge Apply needed
// beside the title to say why (the banner is not drawn here).
// Refresh and Apply show their action at work on the product on screen: each is written again only when what it says
// changes (the product, whether it can be used), and the new one takes at once the look of its action (paintActions).
function drawActions() {
  const wait = store.wait(), known = !!installed[product], mine = drafted(changes);
  const ready = mine.length && mine.every(([, c]) => c.type === "remove" || c.version);
  const why = wait || (!mine.length ? "No changes" : !ready ? "Choose a version"
    : !known ? "Installed plugins unknown" : "");
  $("#plugin-unsaved").hidden = !mine.length;
  setOff($("#plugin-discard"), !mine.length, "No changes");
  setHTML($("#plugin-refresh-slot"), actionButton({ key: `catalog:${product}`, verb: "refresh", label: "Refresh",
    icon: "restart", attrs: 'id="plugin-refresh" data-tip="Read the list again"' }));
  setHTML($("#plugin-apply-slot"), actionButton({ key: `plugins:${product}`, verb: "apply", label: "Apply", icon: "apply", cls: "primary",
    attrs: `id="plugin-apply" ${why ? `aria-disabled="true" data-why="${esc(why)}"` : ""}` }));
  paintActions($("#plugin-actions"));
}

function draw() {
  drawTable();
  drawActions();
}

// --- Interactions -------------------------------------------------------------------

// A list that could not be read is asked for again the next time it is needed
async function loadVersions(r) {
  if (!r.path || Array.isArray(versions[r.path])) return;
  try {
    const list = (await api(`/api/plugins/versions?path=${encodeURIComponent(r.path)}`)).versions;
    versions[r.path] = list.sort((a, b) => compareVersions(a.version, b.version));
  } catch (err) {
    versions[r.path] = { error: err.message };
  }
}

// A row is drawn again whole when it changes: the keyboard goes back to its box, or to its version
const refocusRow = (r, control) => refocus(`#plugins tr[data-name="${CSS.escape(r.name)}"] ${control}`);

// Ticked, a plugin starts on its highest release, a pre-release only when it has none
async function check(r, on) {
  if (on) {
    if (r.ext && changes.get(r.name)?.type === "remove") changes.delete(r.name);   // undo a removal
    else {
      const mine = changes;   // the product on screen may be another one once the versions are read
      mine.set(r.name, { type: "install", path: r.path, version: null });
      draw();
      refocusRow(r, "input");
      await loadVersions(r);
      const c = mine.get(r.name);
      if (c && !c.version && Array.isArray(versions[r.path])) {
        c.version = (versions[r.path].find((v) => isRelease(v.version)) || versions[r.path][0])?.version || null;
      }
    }
  } else if (r.ext) {
    changes.set(r.name, { type: "remove", file: r.ext.file });
  } else {
    changes.delete(r.name);
  }
  draw();
  refocusRow(r, "input");
}

// Choosing the version that runs undoes the change. "" is the bundled one, offered only for a plugin that is not
// added: an added one goes back to it by unticking it.
function setVersion(r, version) {
  const keep = r.ext?.active ? r.ext.version : "";
  if (version === keep) changes.delete(r.name);
  else changes.set(r.name, { type: "install", path: r.path, version });
  draw();
  refocusRow(r, "[data-version-for]");
}

const rowOf = (el) => rows().find((r) => r.name === el.closest("tr")?.dataset.name);

// What applying does, said first as for any action that restarts something: the plugins installed and removed,
// wherever they are in the list, the playgrounds that are on and need a plugin that goes (no image bundles it), and
// the product that loads them again when one of its services runs, as the server restarts those only
function effects() {
  const { ext, services, bundled } = installed[product], mine = drafted(changes);
  const named = (type) => mine.filter(([, c]) => c.type === type).map(([name, c]) => {
    const from = ext[name]?.version;
    return type === "remove" ? `${pretty(name)} ${from || ""}` : `${pretty(name)} ${from ? `${from} → ` : ""}${c.version}`;
  });
  const gone = mine.filter(([name, c]) => c.type === "remove" && !bundled[name]).map(([name]) => name);
  const needing = store.state.playgrounds.filter((p) => p.on
    && (p.requires.plugins?.[product] || []).some((name) => gone.includes(name)));
  const group = store.state.platform.groups.find((g) => g.product === product);
  const runs = services.some((s) => store.service(s)?.state.running);
  return [
    { label: "Installs", items: named("install") },
    { label: "Removes", items: named("remove") },
    { label: "Needed by", items: needing.map((p) => p.name), tone: "danger", note: true },
    { label: "Restarts", items: runs ? [group.name] : [] },
  ];
}

async function apply() {
  if (!(await confirmEffects("Apply", effects()))) return;
  const id = product, list = changes, install = [], remove = [];
  const out = new Map(drafted(list));
  for (const c of out.values()) {
    if (c.type === "install") install.push({ path: c.path, version: c.version });
    else remove.push(c.file);
  }
  // The selection stays as it is until the job ended well (settleApply): a refusal, a failure or a cancel leaves it
  // to apply again. What is changed meanwhile is a draft of its own.
  runJob(() => api("/api/plugins/apply", { product: id, install, remove }).then((job) => {
    applying = { id: job.id, product: id, changes: list, sent: out };
    draw();
    return job;
  }), { key: `plugins:${id}`, verb: "apply" });
}

// The apply sent ended: well, what it was sent goes once what is installed is read again (load); not, it stays
// chosen. Another job seen in its place means that it ended unseen: its changes stay chosen too.
store.subscribe((state) => {
  const job = state?.job;
  if (!applying || applying.ok !== undefined || !job || (job.id === applying.id && !job.done)) return;
  applying.ok = job.id === applying.id && job.ok;
  if (!applying.ok) applying = null;
});

// The product the page opens on, chosen before going to it. Each product keeps the changes made on it, and is read
// again once on screen.
export function selectProduct(id) {
  if (id === product) return;
  product = id;
  changes = pending[id] ||= new Map();
  pager.reset();
  stale = true;
}

function init(section) {
  section.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h1>Plugins</h1><span class="pill warn" id="plugin-unsaved" hidden>Unsaved</span></div>
      <div class="page-actions" id="plugin-actions">
        <div class="segmented" id="plugin-product" role="radiogroup" aria-label="Product"></div>
        <span class="act-slot" id="plugin-refresh-slot"></span>
        <button class="btn" id="plugin-discard" aria-disabled="true" data-why="No changes">Discard</button>
        <span class="act-slot" id="plugin-apply-slot"></span>
      </div>
    </div>

    <div class="card list-card">
      <div class="list-tools">
        <label class="search grow">${icon("search")}<input id="plugin-search" type="search" placeholder="Search…" aria-label="Search the plugins" autocomplete="off"></label>
        <div class="list-filters">
          <button class="combo-btn" id="plugin-category" aria-haspopup="dialog"><span class="sr-only">Category: </span><span class="value"></span>${icon("chevron")}</button>
          <div class="segmented" id="plugin-state" role="radiogroup" aria-label="State">
            <button role="radio" data-state="all" aria-checked="true">All</button>
            <button role="radio" data-state="bundled">Bundled</button>
            <button role="radio" data-state="added">Added</button>
            <button role="radio" data-state="none">Not installed</button>
          </div>
          <div class="segmented" id="plugin-edition" role="radiogroup" aria-label="Edition">
            <button role="radio" data-edition="all" aria-checked="true">All</button>
            <button role="radio" data-edition="community">Community</button>
            <button role="radio" data-edition="enterprise">Enterprise</button>
          </div>
        </div>
      </div>
      <div class="table-wrap">
        <table class="table plugins">
          <thead><tr><th><span class="sr-only">Installed</span></th><th>Plugin</th><th>Category</th><th>State</th><th>Version</th></tr></thead>
          <tbody id="plugins"></tbody>
        </table>
        <div class="empty" id="plugins-empty">${loading("Loading")}</div>
      </div>
      <div class="pager" id="plugins-pager" hidden></div>
    </div>`;
  // A new page is read from its top
  pager = Pager($("#plugins-pager"), 25, () => { draw(); scrollTo(0, 0); });
  const filter = () => { pager.reset(); draw(); };
  categories = CategoryFilter($("#plugin-category"), rows, filter, capital);
  const segments = [["#plugin-state", "state"], ["#plugin-edition", "edition"]];
  // A segmented control says the filter it sets
  const mark = ([id, key]) => $$(`${id} button`).forEach((b) => b.setAttribute("aria-checked", String(b.dataset[key] === filters[key])));

  $("#plugin-product").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-product]");
    if (!b || b.dataset.product === product) return;
    selectProduct(b.dataset.product);
    render(store.state);
  });
  // Refresh and Apply are written again as they change: their clicks are heard on the head
  $("#plugin-actions").addEventListener("click", (e) => {
    const id = e.target.closest("button")?.id;
    if (id === "plugin-refresh") refresh();
    else if (id === "plugin-apply") {
      apply();
    }
  });
  $("#plugin-search").addEventListener("input", (e) => { filters.q = e.target.value.trim(); filter(); });
  for (const segment of segments) {
    const [id, key] = segment;
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button[role=radio]");
      if (!b) return;
      filters[key] = b.dataset[key];
      mark(segment);
      filter();
    });
  }
  $("#plugins-empty").addEventListener("click", (e) => {
    // The images of the product, not pulled: once they are, what they bundle is read again (render, after the job)
    if (e.target.closest("[data-pull]")) return runJob(() => api("/api/stack/pull", {}), { key: "platform", verb: "pull" });
    const file = e.target.closest("[data-file]");
    if (file) return openFile(file.dataset.file);
    if (!e.target.closest("[data-clear]")) return;
    Object.assign(filters, NO_FILTER);
    $("#plugin-search").value = "";
    categories.clear();
    segments.forEach(mark);
    filter();
  });
  $("#plugins").addEventListener("change", (e) => {
    const r = e.target.matches("input[type=checkbox]") && rowOf(e.target);
    if (r) check(r, e.target.checked);
  });
  $("#plugins").addEventListener("click", (e) => {
    if (e.target.closest("[data-license]")) return License.show({ form: true });   // what the license says
    const b = e.target.closest("[data-version-for]");
    const r = b && rowOf(b);
    if (r) Combo.open(b, versionSource(r));
  });
  // What is being applied is no draft: Discard, and leaving the page, drop the others
  const discard = (list) => drafted(list).forEach(([name]) => list.delete(name));
  $("#plugin-discard").addEventListener("click", () => { discard(changes); draw(); });
  // Leaving the page drops the changes of every product: it asks first
  drafts.add(() => Object.values(pending).some((list) => drafted(list).length),
    () => Object.values(pending).forEach(discard));
  drawActions();   // the head has its buttons before a product is read
}

function render(state) {
  if (!state.products.some((p) => p.id === product)) selectProduct(state.products[0]?.id);
  $("#plugin-product").hidden = state.products.length < 2;
  // A product that holds changes while another one is on screen is marked as a file of the editor is
  const unsaved = (p) => (p.id !== product && pending[p.id] && drafted(pending[p.id]).length
    ? '<span class="unsaved" role="img" aria-label="Unsaved changes" data-tip="Unsaved changes"></span>' : "");
  setHTML($("#plugin-product"), state.products.map((p) =>
    `<button role="radio" data-product="${esc(p.id)}" aria-checked="${p.id === product}">${esc(p.name)}${unsaved(p)}</button>`).join(""));
  // A job that ended may have changed what is installed; another license saved, what the rows say of it
  if (state.job?.done && state.job.id !== ended) { ended = state.job.id; stale = true; }
  const saved = JSON.stringify(state.license && [state.license.status, state.license.expires]);
  if (saved !== license) { license = saved; stale = true; }
  if (stale) { stale = false; load(); }
  draw();
}

export const pluginsPage = {
  id: "plugins", pattern: /^#\/plugins$/, nav: "plugins", title: "Plugins",
  init,
  show() { stale = true; },
  render,
};
