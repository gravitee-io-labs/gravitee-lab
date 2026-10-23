// The editor popup: a compose file, checked as it is typed; a playground's guide, as it is written or as it reads; or
// the secrets and settings of a .env as a form. It opens on one file, next to the ones that go with it: the core's,
// the on file, its own unit's, those of the setups a playground includes and of the add-ons it brings. A file of an item of a git catalog
// is there to be read before it runs: it opens read only, by its path in the catalog, and its settings stay the person's to change.

import { $, esc, icon, setHTML, loading, loadScript, api, store, drafts, theme, copyButton } from "../core.js";
import { toast, confirmDialog, actionButton, paintActions, applyBadge, setOff } from "../components/ui.js";
import { runJob, runPlatform, busy, confirmApply, savedOnly } from "../activity/jobs.js";
import { License } from "./license.js";
import { markdownHtml } from "../components/markdown.js";

// 0.52 is the last Monaco release with the AMD build that loads without a bundler
const MONACO = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min";
// The hash covers vs/loader.js alone: the loader fetches the other files of the editor at run time, which it does not check
const MONACO_LOADER_HASH = "sha384-pHG02SG8pId94Np3AbPmBEJ1yPqaH0IkJGLSNGXYmuGhkazT8Lr/57WYpbkGjJtu";

const LOADING = `<li class="empty">${loading("Loading")}</li>`;
const ICONS = { compose: "file", guide: "book", env: "key" };   // a file of the pane, by its kind

const dialog = document.createElement("dialog");
dialog.className = "editor-dialog";
dialog.setAttribute("aria-label", "Files");
dialog.innerHTML = `
  <div class="compose">
    <aside class="compose-files">
      <div class="card-head"><h2>Files</h2></div>
      <ul class="file-list" id="file-list">${LOADING}</ul>
    </aside>
    <div class="compose-editor">
      <div class="dialog-head editor-bar">
        <div class="editor-file">${icon("file")}<code id="editor-path">–</code><span class="pill warn" id="editor-dirty" hidden>Unsaved</span><span class="pill" id="editor-readonly" hidden>Read only</span></div>
        <div class="editor-status" id="editor-status"></div>
        <div class="editor-actions">
          <div class="segmented" role="radiogroup" aria-label="View" id="editor-view" hidden>
            <button role="radio" data-view="code">Code</button>
            <button role="radio" data-view="preview">Preview</button>
          </div>
          <button class="btn" id="editor-discard" aria-disabled="true" data-why="No changes">Discard</button>
          <span class="act-slot" id="editor-saves"></span>
          <button class="icon-btn" id="editor-close" aria-label="Close" data-tip="Close">${icon("close")}</button>
        </div>
      </div>
      <div class="editor-host" id="editor"><div class="editor-loading">${loading("Loading")}</div></div>
      <div class="editor-preview" id="editor-preview" hidden><div class="guide"></div></div>
      <div class="problems" id="editor-problems" hidden></div>
      <form class="env-form" id="env-form" autocomplete="off" hidden></form>
    </div>
  </div>`;
document.body.append(dialog);

let monaco = null, editor = null, area = null;   // Monaco and its editor, or the text area that stands in for them
let ready = null;                                // the editor is loading, or loaded
let shown = null;                                // the file in the editor
let sections = null;                             // the pane: what /api/files answered, null while it loads
let current = null, validateTimer = null, validating = 0;
const docs = new Map();   // path -> {model or value, saved, version, problems}
const envs = new Map();   // path -> {form, saved: {key: value}, values: {key: value}}
// {path, service}: the service a file shared by several groups opens at, once; {path, key}: the field of a form
let reveal = null;
let closeAfter = null;   // the .env of an item that is off: once it is saved, the popup is done
let view = "code";       // a markdown file shows as it is written ("code") or as it reads ("preview")

// A .env opens as a form: the core's, or an add-on's own. A playground's guide is markdown: Compose does not check it,
// and nothing runs from it
const isEnv = (path) => /(^|\/)\.env$/.test(path || "");
const isGuide = (path) => /\/guide\.md$/.test(path || "");
const text = (doc) => (doc.model ? doc.model.getValue() : doc.value);
const dirtyKeys = (env) => (env ? Object.keys(env.values).filter((k) => env.values[k] !== env.saved[k]) : []);
const unsaved = (path) => {
  if (isEnv(path)) return dirtyKeys(envs.get(path)).length > 0;
  const doc = docs.get(path);
  return !!doc && text(doc) !== doc.saved;
};
const unsavedPaths = () => [...docs.keys(), ...envs.keys()].filter(unsaved);
const unsavedDot = (path) => (unsaved(path) ? '<span class="unsaved" role="img" aria-label="Unsaved changes" data-tip="Unsaved changes"></span>' : "");

const editorDirty = () => unsavedPaths().length > 0;
const sectionOf = (path) => sections?.find((s) => s.files.some((f) => f.path === path));

// --- Files of a catalog ---

// The item whose compose file or guide a path is, from the state: {item, display}, `display` being the path the person reads, relative to
// its catalog (the item's `display_file`), never the one on this machine. Its settings (.env) are the machine's and belong to no catalog.
const guideOf = (file) => file.replace(/[^/]+$/, "guide.md");
function itemOf(path) {
  const { playgrounds = [], setups = [], addons = [] } = store.state || {};
  for (const item of [...playgrounds, ...setups, ...addons]) {
    if (item.file === path) return { item, display: item.display_file || item.file };
    if (item.kind === "playground" && item.file && path === guideOf(item.file)) return { item, display: guideOf(item.display_file || item.file) };
  }
  return null;
}

// The catalog an item is part of, by name: its origin's, else the first catalog's, whose items have none
const partOf = (item) => item.origin?.name
  || (Array.isArray(store.state?.catalogs) ? store.state.catalogs.find((c) => c.listed && ["git", "folder"].includes(c.kind))?.name : "") || "its catalog";

// What keeps a file from being written: it is part of a catalog that is read and not edited (a git catalog's, `readonly`), and says which
// ({reason}); null for any other file. The path shown for a file is its own except for a file of a catalog.
const lockOf = (path) => {
  const found = itemOf(path);
  return found?.item.readonly ? { reason: `Part of ${partOf(found.item)}` } : null;
};
const shownPath = (path) => itemOf(path)?.display || path;

function setText(doc, value) {
  if (doc.model) return doc.model.setValue(value);
  doc.value = value;
  if (doc === shown) area.value = value;
}

// The file in the editor, once it is started; null shows none, and nothing can be typed
function show(doc) {
  shown = doc;
  const locked = !!lockOf(current);
  if (editor) {
    editor.setModel(doc?.model || null);
    editor.updateOptions({ readOnly: locked });
  } else if (area) {
    area.value = doc?.value || "";
    area.readOnly = !doc || locked;
  }
}

async function loadMonaco() {
  await loadScript(`${MONACO}/vs/loader.js`, MONACO_LOADER_HASH);
  // Monaco's web worker must come from this page's origin: wrap the CDN one
  window.MonacoEnvironment = {
    getWorkerUrl: () => `data:text/javascript;charset=utf-8,${encodeURIComponent(
      `self.MonacoEnvironment={baseUrl:'${MONACO}/'};importScripts('${MONACO}/vs/base/worker/workerMain.js');`)}`,
  };
  window.require.config({ paths: { vs: `${MONACO}/vs` } });
  return new Promise((resolve, reject) => window.require(["vs/editor/editor.main"], () => resolve(window.monaco), reject));
}

// Editor colors from the page's own tokens, so both follow the light / dark theme. Text takes the tokens made for text
// (4.5:1 at least on --surface): the accent as ink for the keys, the warn tone for numbers, muted line numbers
function applyTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  monaco.editor.defineTheme("gravitee", {
    base: theme() === "dark" ? "vs-dark" : "vs", inherit: true,
    rules: [
      { token: "type", foreground: v("--accent-ink").slice(1) },
      { token: "string", foreground: v("--ok").slice(1) },
      { token: "number", foreground: v("--warn").slice(1) },
      { token: "comment", foreground: v("--muted").slice(1), fontStyle: "italic" },
    ],
    colors: {
      "editor.background": v("--surface"), "editor.lineHighlightBackground": v("--surface-2"),
      "editorLineNumber.foreground": v("--muted"), "editorLineNumber.activeForeground": v("--text-2"),
      "editor.selectionBackground": v("--accent-soft"), "editorCursor.foreground": v("--accent"),
      "editorIndentGuide.background1": v("--border"), "editorWidget.background": v("--surface"),
    },
  });
  monaco.editor.setTheme("gravitee");
}

async function startEditor() {
  const host = $("#editor"), edited = () => { renderState(); scheduleValidation(); };
  try {
    monaco = await loadMonaco();
  } catch {
    // Monaco did not come (no network): a plain text area, checked and saved the same way
    host.innerHTML = '<textarea class="editor-plain" spellcheck="false" aria-label="File content"></textarea>';
    area = host.firstElementChild;
    area.addEventListener("input", () => { if (shown) { shown.value = area.value; edited(); } });
    area.addEventListener("keydown", (e) => {
      if (e.key !== "Tab" || e.shiftKey) return;
      e.preventDefault();   // Tab indents instead of leaving the field
      area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input"));
    });
    return;
  }
  host.innerHTML = "";
  applyTheme();
  editor = monaco.editor.create(host, {
    language: "yaml", automaticLayout: true, minimap: { enabled: false }, fontSize: 13, lineHeight: 20,
    fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--mono").trim(), tabSize: 2, insertSpaces: true,
    scrollBeyondLastLine: false, renderWhitespace: "boundary", padding: { top: 10 }, smoothScrolling: true,
    bracketPairColorization: { enabled: true }, guides: { indentation: true }, stickyScroll: { enabled: false },
  });
  editor.onDidChangeModelContent(edited);
  new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
}

// The kind of a file as the pane lists it, else as its path says until the pane is read
const kindOf = (path) => sections?.flatMap((s) => s.files).find((f) => f.path === path)?.kind
  || (isEnv(path) ? "env" : isGuide(path) ? "guide" : "compose");

// The pane: a heading per section, then its compose file and its .env; the file on screen has its icon in the head too
function renderList() {
  $(".editor-file use", dialog).setAttribute("href", `#i-${ICONS[kindOf(current)]}`);
  const row = (f) => `<li><button class="file-item ${f.path === current ? "active" : ""}" data-path="${esc(f.path)}" data-tip="${esc(shownPath(f.path))}">
    ${icon(ICONS[f.kind])}<span class="name">${esc(f.name)}</span>${unsavedDot(f.path)}</button></li>`;
  setHTML($("#file-list"), sections
    ? sections.map((s) => `<li class="file-group">${esc(s.title)}</li>${s.files.map(row).join("")}`).join("") : LOADING);
}

// --- .env form ---

async function openEnv(path, forceReload) {
  $("#editor-status").className = "editor-status";
  $("#editor-status").innerHTML = "";
  if (forceReload || !unsaved(path)) {
    try {
      setEnv(path, await api(`/api/env?file=${encodeURIComponent(path)}`));
    } catch (err) {
      unreadable(path, err);
      return;
    }
  }
  if (current !== path) return;   // another file was asked for meanwhile
  renderEnv();
  renderState();
  // Opened for one of its settings (the license): its field, in view
  const field = path === reveal?.path && reveal.key && $(`.env-row[data-key="${CSS.escape(reveal.key)}"] input`, dialog);
  if (field) {
    field.scrollIntoView({ block: "center" });
    field.focus({ preventScroll: true });
    reveal = null;
  }
}

function setEnv(path, form) {
  const saved = {};
  for (const s of form.sections) for (const g of s.groups) for (const v of g.vars) saved[v.key] = v.value;
  envs.set(path, { form, saved, values: { ...saved } });
}

// A form's rows, as the fields of a dialog: what a setting is for as its label (the comment of its group, on its first setting), its
// name in .env under it in mono, then its field; hairlines between rows, no box for each
function renderEnv() {
  const env = envs.get(current), licenseVar = store.state?.platform.license_var;
  const row = (v, secret, what) => {
    const value = env.values[v.key], id = `env-${esc(v.key)}`;
    return `<div class="env-row ${value !== env.saved[v.key] ? "changed" : ""}" data-key="${esc(v.key)}">
      <label for="${id}">${what ? `<span class="env-what">${esc(what)}</span>` : ""}<code class="env-key" data-tip="${esc(v.key)}" data-tip-cut>${esc(v.key)}</code></label>
      <div class="search ${secret ? "masked" : ""}">
        <input id="${id}" value="${esc(value)}" placeholder="${esc(v.default || "Not set")}" ${v.required ? "required" : ""} spellcheck="false" autocomplete="off" data-1p-ignore>
        ${v.required ? '<span class="pill">Required</span>' : ""}
        ${secret ? `<button type="button" class="icon-btn" data-reveal aria-label="Show" data-tip="Show">${icon("eye")}</button>` : ""}
        ${copyButton(value)}
      </div>
      ${v.key === licenseVar ? '<div class="env-license"></div>' : ""}</div>`;
  };
  $("#env-form").innerHTML = env.form.sections.map((s) => `<section class="env-section"><h3>${esc(s.title)}</h3>
    ${s.groups.map((g) => g.vars.map((v, i) => row(v, s.kind === "secret", i ? "" : g.description)).join("")).join("")}</section>`).join("");
  renderLicense();
}

// Under the field that holds it, the license saved, and "Apply needed" until what runs reads it: it follows a save,
// not what is typed
function renderLicense() {
  const line = $(".env-license", dialog), license = License.summary();
  if (!line) return;
  line.hidden = !license;
  setHTML(line, license ? `<span class="dot-status ${license.dot}"></span><span>${esc(license.text)}</span>${license.late ? applyBadge(license.late) : ""}
    <button type="button" class="text-link" data-license>See more</button>` : "");
}

// --- Compose files ---

// A markdown file shows as it is written or as it reads; what is shown follows the text not saved yet
async function showView() {
  const guide = isGuide(current), preview = guide && view === "preview", doc = docs.get(current);
  $("#editor-view").hidden = !guide;
  for (const b of $("#editor-view").children) b.setAttribute("aria-checked", String(b.dataset.view === view));
  $("#editor").hidden = isEnv(current) || preview;
  $("#editor-preview").hidden = !preview;
  if (preview && doc) {
    const path = current, html = await markdownHtml(text(doc));
    if (path === current && view === "preview") $("#editor-preview .guide").innerHTML = html;
  }
}

async function open(path, forceReload = false) {
  const env = isEnv(path);
  current = path;
  $("#editor").hidden = env;
  $("#editor-view").hidden = $("#editor-preview").hidden = true;
  $("#env-form").hidden = !env;
  if (env) $("#editor-problems").hidden = true;
  $("#editor-path").textContent = shownPath(path);
  renderList();
  if (env) return openEnv(path, forceReload);
  await (ready ||= startEditor());
  let doc = docs.get(path);
  if (!doc || forceReload || !unsaved(path)) {
    let file;
    try { file = await api(`/api/files/content?path=${encodeURIComponent(path)}`); }
    catch (err) { unreadable(path, err); return; }
    doc = docs.get(path);   // another open of the same file may have read it meanwhile
    if (!doc) {
      doc = { saved: file.content, version: file.version, problems: [] };
      if (monaco) doc.model = monaco.editor.createModel(file.content, isGuide(path) ? "markdown" : "yaml", monaco.Uri.parse(`file:///${path}`));
      else doc.value = file.content;
      docs.set(path, doc);
    } else if (file.version !== doc.version || forceReload) {
      setText(doc, file.content);   // changed on disk while it had no edits here
      doc.saved = file.content;
      doc.version = file.version;
    }
  }
  if (current !== path) return;   // another file was asked for meanwhile
  show(doc);
  // A group of the core shares its file: start at its first service
  if (editor && path === reveal?.path && reveal.service) {
    const lines = doc.model.getLinesContent();
    const line = lines.findIndex((l) => l === `  ${reveal.service}:`) + 1;
    let top = line;   // from the section's heading comment above it
    while (top > 1 && /^\s*(#.*)?$/.test(lines[top - 2])) top--;
    while (top < line && !lines[top - 1].trim()) top++;
    if (line) { editor.setScrollTop(editor.getTopForLineNumber(top)); editor.setPosition({ lineNumber: line, column: 3 }); }
    reveal = null;
  }
  renderState();
  validate();
  showView();
}

// A file that cannot be read is not shown: nothing of another file stays under its name, and nothing acts on it.
// What was kept of it goes too (it had no edits, or they were given up): it is read again when it is opened again.
function unreadable(path, err) {
  toast(err.message, true);
  if (current !== path) return;   // another file was asked for meanwhile
  const doc = docs.get(path);
  docs.delete(path);
  envs.delete(path);
  show(null);
  doc?.model?.dispose();
  $("#env-form").innerHTML = $("#editor-status").innerHTML = "";
  $("#editor-problems").hidden = true;
  renderState();
}

// Save acts on the file on screen, its key; Save & apply on the platform, as the one of Images does, and so does
// Apply, which it reads while nothing is unsaved and something runs behind what is saved. The one clicked says its
// action at work until the server answers, then how it went. Each is {disabled, title, hidden}, the second one also
// its `label`. They are written again only when what they say changed: the new buttons take the look their key has
// (paintActions), and the keyboard stays on the one it was on.
function drawSaves(save, apply) {
  const focused = document.activeElement?.id;
  const attrs = (id, b) => `id="${id}" ${b.disabled ? `aria-disabled="true" data-why="${esc(b.title || "")}"` : `data-tip="${esc(b.title || "")}"`}${b.hidden ? " hidden" : ""}`;
  const html = actionButton({ key: `file:${current || ""}`, verb: "save", label: "Save", attrs: attrs("editor-save", save) })
    + actionButton({ key: "platform", verb: ["apply", "save"], label: apply.label || "Save & apply", icon: "apply", cls: "primary",
      attrs: attrs("editor-apply", apply) });
  if (!setHTML($("#editor-saves"), html)) return;
  paintActions($("#editor-saves"));
  if (["editor-save", "editor-apply"].includes(focused)) $(`#${focused}`).focus();
}
drawSaves({ disabled: true, title: "No changes" }, { label: "Apply", disabled: true, title: "No changes", hidden: true });

// A save of `path`, said on the button clicked: "Saving" on Save, or on Save & apply, whose apply the buttons of the
// platform show once the popup is closed (Images, the Stack)
const working = (path, apply, request) => busy(apply ? "platform" : `file:${path}`, "save", request);

// The head's buttons and the pane, for the file on screen. A disabled button says why: "No changes" when it has
// nothing to act on. Applying recreates what runs: Save & apply shows only while something of the file's unit runs
// (`runs`: never for a setup, whose add-ons are what runs); otherwise what is saved applies at the next start, as
// Save's tooltip says. With nothing unsaved it reads Apply, usable while something runs behind what is saved: one word for each.
function renderState() {
  renderList();
  const env = isEnv(current), doc = docs.get(current), discard = $("#editor-discard");
  if (env ? !envs.has(current) : !doc) {   // still loading, or unreadable
    $("#editor-dirty").hidden = $("#editor-readonly").hidden = true;
    setOff(discard, true, "No changes");
    const none = { disabled: true, title: "No changes" };
    return drawSaves(none, { ...none, label: "Apply", hidden: $("#editor-apply").hidden });
  }
  const changed = unsaved(current), wait = store.wait(), locked = isEnv(current) ? null : lockOf(current);
  // A form waits for its required fields, a file for the check of what it holds now: from the first key typed
  const checking = !env && (validating > 0 || text(doc) !== doc.checked);
  const invalid = env ? !$("#env-form").checkValidity() : checking || doc.problems.some((p) => p.severity === "error");
  // Applying closes the popup: the edits of another file would be left behind, neither saved nor applied
  const [other] = unsavedPaths().filter((path) => path !== current);
  $("#editor-dirty").hidden = !changed;
  $("#editor-readonly").hidden = !locked;
  setOff(discard, !changed || !!locked, locked ? locked.reason : "No changes", "Back to the saved file");
  const section = sectionOf(current), guide = isGuide(current), runs = !!section?.runs && !guide;
  const needed = !changed && runs && !!store.state?.platform.apply.needed;
  // What keeps both buttons from being used is said on both, before what keeps one only
  const reason = !invalid ? "" : env ? "A required setting is empty" : checking ? "Checking" : "Fix the problems first";
  const held = (other ? `Save or discard ${other} first` : wait) || "";
  drawSaves({
    disabled: !changed || invalid || !!locked,
    title: locked ? locked.reason : reason || (!changed ? "No changes" : section && !runs && !guide ? "Applied on next start" : "Save (⌘S / Ctrl+S)"),
  }, needed ? { label: "Apply", disabled: !!held, title: held || "Recreate the containers that changed" } : {
    label: changed ? "Save & apply" : "Apply",
    hidden: !runs,
    disabled: !changed || invalid || !!held,
    title: reason || held || (changed ? "Save, then recreate the containers that changed" : "No changes"),
  });
}

function renderProblems() {
  const doc = docs.get(current), status = $("#editor-status"), panel = $("#editor-problems");
  if (!doc) return;
  if (doc.model) {
    monaco.editor.setModelMarkers(doc.model, "compose", doc.problems.map((p) => {
      // Compose places an error at the end of the file on the line after the last one
      const line = Math.min(p.line || 1, doc.model.getLineCount());
      return {
        severity: p.severity === "error" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
        message: p.message, startLineNumber: line, startColumn: p.column || 1,
        endLineNumber: line, endColumn: p.line ? doc.model.getLineMaxColumn(line) : 2,
      };
    }));
  }
  const errors = doc.problems.filter((p) => p.severity === "error").length;
  status.className = "editor-status " + (errors ? "err" : "ok");
  if (isGuide(current) || lockOf(current)) {
    status.innerHTML = "";
    panel.hidden = true;
    return;
  }
  status.innerHTML = errors ? `${icon("alert")}${errors} problem${errors > 1 ? "s" : ""}`
    : `${icon("check")}Valid${doc.problems.length ? `, ${doc.problems.length} warning${doc.problems.length > 1 ? "s" : ""}` : ""}`;
  panel.hidden = !doc.problems.length;
  panel.innerHTML = doc.problems.map((p, i) => `<button class="problem ${esc(p.severity)}" data-i="${i}">${icon("alert")}
    <span class="where">${p.line ? `Ln ${p.line}` : ""}</span><span>${esc(p.message)}</span></button>`).join("");
}

// The check starts once typing pauses; what is typed is said to be checked from the first key
function scheduleValidation() {
  if (isGuide(current)) return validate();
  $("#editor-status").className = "editor-status";
  $("#editor-status").innerHTML = loading("Checking");
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validate, 600);
}

async function validate() {
  const path = current, doc = docs.get(path);
  if (!doc) return;
  // A file that is only read is not checked: nothing of it can change
  if (isGuide(path) || lockOf(path)) {
    Object.assign(doc, { problems: [], checked: text(doc) });
    renderProblems();
    return renderState();
  }
  validating++;
  $("#editor-status").className = "editor-status";
  $("#editor-status").innerHTML = loading("Checking");
  renderState();
  const content = text(doc);
  try {
    const res = await api("/api/files/validate", { path, content });
    if (text(doc) === content) Object.assign(doc, { problems: res.problems, checked: content });
  } catch (err) {
    Object.assign(doc, { problems: [{ message: err.message, severity: "error", line: null }], checked: content });
  }
  validating--;
  if (path !== current) return;
  // A check of what was typed before the last keys says nothing of the text now: the next one will
  if (doc.checked === text(doc)) renderProblems();
  renderState();
}

// --- Saving ---

// What the file on screen holds and is not saved: the values a form changes, or the text of a compose file
function draftOf(path) {
  if (!isEnv(path)) return { content: text(docs.get(path)) };
  const env = envs.get(path);
  return { values: Object.fromEntries(dirtyKeys(env).map((key) => [key, env.values[key]])) };
}

// Saves a draft of `path` (draftOf), said on the button clicked (`apply`: Save & apply). Answers whether it was
// saved; a refusal is said in a toast, a file changed on disk is read again when its edits may go.
async function write(path, draft, apply) {
  try {
    if (draft.values) {
      const sent = { file: path, values: draft.values, version: envs.get(path).form.version };
      setEnv(path, await working(path, apply, api("/api/env", sent)));
      if (current === path) renderEnv();
      if (store.state?.platform.license_var in draft.values) License.load();   // its chip and the banner follow
    } else {
      const doc = docs.get(path);
      const res = await working(path, apply, api("/api/files/save", { path, content: draft.content, version: doc.version }));
      Object.assign(doc, { saved: res.content, version: res.version, problems: res.problems, checked: res.content });
      if (current === path) renderProblems();
    }
    return true;
  } catch (err) {
    if (await reloadInstead(path, err)) open(path, true);
    else toast(err.message, true);
    return false;
  }
}

// Save, or Save & apply (`apply`), of the file on screen as it is when clicked. What applying recreates is said
// first: when it is nothing, Save & apply only saves, and says why. With nothing unsaved, Apply is the platform's,
// which says first what it restarts: the popup closes once it goes ahead.
async function save(apply) {
  const path = current;
  if (apply && !unsaved(path)) {
    if (await runPlatform("apply")) dialog.close();
    return;
  }
  if (!unsaved(path)) return renderState();
  const draft = draftOf(path);
  let applying = false, said = "Saved";
  if (apply) {
    const body = draft.values ? { file: path, values: draft.values } : { path, content: draft.content };
    const found = await confirmApply("Save & apply", body,
      [{ label: "Changes", items: draft.values ? Object.keys(draft.values) : [path] }]);
    if (!found) return;
    applying = found.apply;
    if (!applying) said = savedOnly(found);
  }
  if (!(await write(path, draft, apply))) return;
  toast(said);
  renderState();
  if (applying) {
    dialog.close();
    return runJob(() => api("/api/stack/apply", {}), { key: "platform", verb: "apply" });
  }
  store.refresh();
  // Nothing to apply on an item that is off: the popup is done, unless another file has edits
  if (path === closeAfter && !editorDirty()) dialog.close();
}

// A save refused because the file changed on disk: reload it, when the edits made here may go
function reloadInstead(path, err) {
  return /changed on disk/.test(err.message) && confirmDialog({
    title: "The file changed on disk", text: path, action: "Reload and discard changes", danger: true,
  });
}

// A file goes back to what is saved
function discardFile(path) {
  if (isEnv(path)) envs.get(path).values = { ...envs.get(path).saved };
  else setText(docs.get(path), docs.get(path).saved);
}

// Every file goes back to what is saved
function discard() {
  unsavedPaths().forEach(discardFile);
}

// Closes the popup: at once, or after its unsaved changes were given up
async function close() {
  const paths = unsavedPaths();
  if (paths.length && !(await confirmDialog({
    title: "Discard changes", action: "Discard", danger: true,
    text: `${paths.join(", ")} ${paths.length > 1 ? "have" : "has"} changes that are not saved`,
  }))) return;
  discard();
  dialog.close();
}

dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
$("#editor-close").addEventListener("click", close);
$("#file-list").addEventListener("click", (e) => {
  const item = e.target.closest(".file-item");
  if (!item) return;
  open(item.dataset.path);
  $(".file-item.active", dialog).focus();   // the list was drawn again
});
$("#env-form").addEventListener("input", (e) => {
  const row = e.target.closest(".env-row"), env = envs.get(current);
  env.values[row.dataset.key] = e.target.value;
  row.classList.toggle("changed", e.target.value !== env.saved[row.dataset.key]);
  const copy = $("[data-copy]", row);   // it copies what the field holds now
  copy.dataset.copy = e.target.value;
  setOff(copy, !e.target.value, "Nothing to copy");
  renderState();
});
$("#env-form").addEventListener("click", (e) => {
  if (e.target.closest("[data-license]")) return License.show();
  const eye = e.target.closest("[data-reveal]");
  if (eye) {
    const visible = eye.parentElement.classList.toggle("masked") === false;
    eye.innerHTML = icon(visible ? "eye-off" : "eye");
    eye.dataset.tip = eye.ariaLabel = visible ? "Hide" : "Show";
  }
});
$("#editor-problems").addEventListener("click", (e) => {
  const row = e.target.closest(".problem");
  const p = row && docs.get(current)?.problems[Number(row.dataset.i)];
  if (editor && p?.line) { editor.revealLineInCenter(p.line); editor.setPosition({ lineNumber: p.line, column: p.column || 1 }); editor.focus(); }
});
$("#editor-discard").addEventListener("click", () => {
  if (!unsaved(current)) return;
  discardFile(current);
  if (isEnv(current)) renderEnv();
  else scheduleValidation();
  renderState();
});
$("#editor-saves").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (button && !button.disabled) save(button.id === "editor-apply");
});
$("#editor-view").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  view = b.dataset.view;
  showView();
  if (view === "code") editor?.focus();
});
// ⌘S saves, and so does Enter in a field of a form, whatever number of fields it has. While a save is at work its
// button takes no click, and these keys are that click. Save & apply may be busy with an apply started elsewhere: that
// one does not keep a save from going.
dialog.addEventListener("keydown", (e) => {
  const enter = e.key === "Enter" && e.target.matches("#env-form input");
  if (!enter && (!(e.metaKey || e.ctrlKey) || e.key !== "s")) return;
  e.preventDefault();
  if ($("#editor-save.busy") || store.pending.get("platform") === "save") return;
  // A file is checked again by the server when it is saved: no need to wait for the check that follows typing
  if (!isEnv(current) || $("#editor-save").getAttribute("aria-disabled") !== "true") save(false);
});
// Save & apply waits for Docker and for the running job, the license line for the license read again
store.subscribe(() => { if (dialog.open) { renderState(); renderLicense(); } });
// Leaving the page closes the popup: with unsaved changes it asks first, as closing the window does
drafts.add(editorDirty, discard);

// Opens the popup on a file, with the ones that go with it in the pane. `service`: the service the file starts at;
// `key`: the field of a .env it opens at (the license); `off`: the file is the .env of an item that does not run.
export async function openFile(path, options) {
  reveal = options?.service || options?.key ? { path, service: options.service, key: options.key } : null;
  closeAfter = options?.off ? path : null;
  sections = null;
  if (!dialog.open) dialog.showModal();
  open(path);
  let found = [];
  try { found = (await api(`/api/files?path=${encodeURIComponent(path)}`)).sections; } catch (err) { toast(err.message, true); }
  if (current !== path) return;   // another file was asked for meanwhile
  sections = found;
  renderState();
}

// The settings of an add-on, of a platform group or of the core. The last two have no kind: their settings are the
// core's .env.
export function openSettings(target) {
  return openFile(target.env_file, { off: !!target.kind && target.state.key === "off" });
}
