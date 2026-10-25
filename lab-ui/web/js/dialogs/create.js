// What is created from the Lab UI: the dialog of a new add-on, setup or playground, as a form that writes its compose
// file or as the prompt that has an AI agent write it, kept on this machine or in a catalog that is a folder of the person's. Its form also
// changes a setup of this machine. And what shares, later, an item that was kept here: a branch pushed to a catalog that takes
// contributions (the Lab UI runs the person's own git for that, with the credentials it already has), or a copy into a catalog that is a
// folder. A short form: unlike the editor, Cancel and Escape drop what was typed without asking.

import { $, $$, esc, icon, setHTML, api, store, splitKey, copyButton } from "../core.js";
import { toast, effectsDialog, confirmEffects, emptyHtml, contentStatus, actionButton, paintActions, safeHref } from "../components/ui.js";
import { busy } from "../activity/jobs.js";
import { selectField } from "../components/combo.js";
import { openAdd, openNew } from "./catalog.js";
import { openFile } from "./editor.js";
import { agentLinks } from "../components/agent.js";

const NOUN = { addon: "add-on", setup: "setup", playground: "playground" };   // a kind, in a sentence
const A = { addon: "an add-on", setup: "a setup", playground: "a playground" };
const PICKS = { setup: "Add-ons", playground: "Includes" };                   // what the checklist of a kind holds

// What an item is on disk: the file of a setup, the folder of an add-on or a playground
export const home = (kind, file) => (kind === "setup" ? file : file.replace(/[^/]+$/, ""));

// A label and its value; `copy` is the text its button copies
const line = (label, value, copy) => `<div><dt>${label}</dt><dd>${value}${copy ? copyButton(copy) : ""}</dd></div>`;
// What has an AI agent follow a skill, from the path of its file, and the line that gives it to copy
const ask = (path, what) => `Use the ${path.split("/").slice(-2)[0]} skill (${path}) to ${what}.`;
const promptLine = (text) => line("Prompt", `<code class="snippet">${esc(text)}</code>`, text);

const dialog = document.createElement("dialog");
dialog.className = "create-dialog";
dialog.setAttribute("aria-labelledby", "create-title");
dialog.innerHTML = `
  <form autocomplete="off">
    <h2 id="create-title"></h2>
    <div class="create-choices" id="create-choices">
      <div class="segmented" role="radiogroup" aria-label="How it is made" data-choice="how">
        <button type="button" role="radio" data-value="form">Form</button>
        <button type="button" role="radio" data-value="agent">AI agent</button>
      </div>
      <div class="segmented" role="radiogroup" aria-label="Where it is kept" data-choice="catalog" hidden></div>
    </div>
    <div id="create-fields">
      <div class="field"><label for="create-name">Name</label>
        <div class="search"><input id="create-name" maxlength="60" spellcheck="false" required></div></div>
      <div class="field"><label for="create-desc">Description</label>
        <div class="search"><input id="create-desc"></div></div>
      <div class="field"><label for="create-category">Category</label><select id="create-category"></select></div>
      <div class="field" data-kind="addon"><label for="create-image">Image</label>
        <div class="search"><input id="create-image" placeholder="repository:tag" spellcheck="false" required></div></div>
      <div class="field" data-kind="addon"><label for="create-port">Port</label>
        <div class="search"><input id="create-port" placeholder="Optional, as 8098 or 8098:80" spellcheck="false"></div></div>
      <div class="field" data-kind="setup playground"><span class="field-label" id="create-picks-label"></span>
        <div class="search">${icon("search")}<input type="search" id="create-search" placeholder="Search…" aria-labelledby="create-picks-label"></div></div>
      <div class="field top" data-kind="setup playground"><span></span><div class="picks" id="create-picks"></div></div>
      <p class="create-problem" id="create-problem" hidden></p>
    </div>
    <div id="create-agent">
      <div class="field top"><label for="create-task">Task</label>
        <div class="search lines"><textarea id="create-task" rows="10" placeholder="What it should do"></textarea></div></div>
      <dl class="pairs" id="create-prompt"></dl>
    </div>
    <div class="dialog-actions">
      <button class="btn" type="button" id="create-cancel"></button>
      <span class="agent-actions" id="create-agent-actions" hidden></span>
      <span class="act-slot" id="create-submit"></span>
    </div>
  </form>`;
document.body.append(dialog);

const choices = $("#create-choices", dialog), fields = $("#create-fields", dialog), agent = $("#create-agent", dialog);
const where = $("[data-choice=catalog]", dialog);
const name = $("#create-name", dialog), description = $("#create-desc", dialog), category = $("#create-category", dialog);
const image = $("#create-image", dialog), port = $("#create-port", dialog), search = $("#create-search", dialog);
const list = $("#create-picks", dialog), problem = $("#create-problem", dialog), task = $("#create-task", dialog);
const cancel = $("#create-cancel", dialog);
selectField(category);   // chosen with the picker, as every list that opens

let kind = null, editing = null;   // what is created; the id of the setup being edited (null: a new item)
let picks = {};                    // the setups and the add-ons ticked: {setup, addon}, a set of ids each
let refused = "", empty = "";      // why what is ticked cannot be created, and what the checklist still lacks
let sending = false;               // the form is on its way: a second click or Enter is the same one
const submit = { hidden: false, reason: "" };   // Create, or Save: hidden for a prompt, and why it waits
// How it is made ("form", or "agent" for the prompt) and where it is kept ("" for this machine, else the namespace of a catalog that is a
// folder of the person's)
const chosen = { how: "form", catalog: "" };

// The rows of the catalogs the state lists
const catalogRows = () => (Array.isArray(store.state?.catalogs) ? store.state.catalogs : []);
// The catalogs an item can be made in besides this machine: those that are folders of the person's, which can be written
const editable = () => catalogRows().filter((c) => c.kind === "folder" && c.writable);
const nameOf = (id) => editable().find((c) => c.id === id)?.name || id;

// What the checklist was drawn for, so that it is drawn again when the catalogs arrive or fail while the dialog is open, and not at every
// refresh: the focus stays where it is
let listed = "";
const signature = (state) => JSON.stringify([state.content?.key, state.content?.line, state.addons.length, state.setups.length]);

// The checklist, from what is ticked: the add-ons of a setup; for a playground the setups, then the add-ons. What a
// ticked item brings (the members of a setup, what an add-on needs) comes with it and cannot be unticked. Two add-ons
// that cannot run together keep the item from being created; so does, in what is put in a catalog, an item of this machine.
// With no add-on at all it says why (the content of the catalogs, ui.js), and not that nothing matches.
function drawPicks() {
  const { addons, categories } = store.state, setups = kind === "playground" ? store.state.setups : [];
  listed = signature(store.state);
  const ticked = [...setups.filter((s) => picks.setup.has(s.id)), ...addons.filter((a) => picks.addon.has(a.id))];
  const needed = new Map();   // add-on id -> the names of the ticked items that bring it
  for (const x of ticked) {
    for (const { id } of x.members || x.needs) needed.set(id, [...(needed.get(id) || []), x.name]);
  }
  const all = addons.filter((a) => picks.addon.has(a.id) || needed.has(a.id));
  const clash = all.flatMap((a) => all.filter((b) => b.id in a.conflicts).map((b) =>
    `${a.name} and ${b.name} cannot run together${a.conflicts[b.id] ? `: ${a.conflicts[b.id]}` : ""}`))[0];
  const own = chosen.catalog && ticked.find((x) => x.personal);
  refused = clash || (own ? `${own.name} is of this machine: share it first` : "");
  empty = kind === "setup" && !picks.addon.size ? "Choose at least one add-on" : "";

  const q = search.value.trim().toLowerCase();
  const shown = (x) => `${x.name} ${x.description} ${x.category}`.toLowerCase().includes(q);
  const pick = (x) => {
    const by = x.kind === "addon" && needed.get(x.id);
    return `<label class="pick"><input type="checkbox" data-pick="${x.kind}:${esc(x.id)}" ${by || picks[x.kind].has(x.id) ? "checked" : ""} ${by ? "disabled" : ""}>
      <span class="pick-text"><strong>${esc(x.name)}${x.personal ? ' <span class="pill">Personal</span>' : ""}</strong>
        <span class="pick-note">${esc(x.members ? x.members.map((m) => m.name).join(", ") : x.description)}</span>
        ${by ? `<span class="pick-note">Needed by ${esc(by.join(", "))}</span>` : ""}</span></label>`;
  };
  const group = (title, items) => (items.length
    ? `<section class="pick-group"><h3>${esc(title)}</h3>${items.map(pick).join("")}</section>` : "");
  // The categories in their declared order, then any the add-ons use beyond them
  const nothing = !addons.length && !setups.length ? contentStatus(store.state.content ? store.state : { content: { key: "ready" } }, { kind: "addon" }) : "";
  list.innerHTML = nothing || group("Setups", setups.filter(shown)) + [...new Set([...categories, ...addons.map((a) => a.category)])]
    .map((c) => group(c, addons.filter((a) => a.category === c && shown(a)))).join("") || `<div class="empty">${emptyHtml()}</div>`;
  ready();
}

// Create waits for what the form lacks, and says why. Its problem line says what the checklist refuses: what the
// server refused stays there until the form changes.
function ready() {
  const blank = $("form", dialog).checkValidity() ? "" : "A required field is empty";
  problem.hidden = !refused;
  problem.textContent = refused;
  submit.reason = refused || empty || blank;
  drawSubmit();
}

// What the primary button acts on: a new item of the kind, which has no id yet, or the setup edited
const acting = () => (editing ? { key: `setup:${editing}`, verb: "save" } : { key: `new:${kind}`, verb: "create" });

// The primary button: from the click until the server answers it says so, then how it went (actionButton); the
// setup edited says it on its row too. Written again only when what it says changed: the new button takes the look its
// key has (paintActions), and the keyboard stays on it.
function drawSubmit() {
  const focused = document.activeElement?.id === "create-save";
  const attrs = `id="create-save"${submit.hidden ? " hidden" : ""}${submit.reason ? ` aria-disabled="true" data-why="${esc(submit.reason)}"` : ""}`;
  const html = actionButton({ ...acting(), label: editing ? "Save" : "Create", cls: "primary", attrs });
  if (!setHTML($("#create-submit", dialog), html)) return;
  paintActions(dialog);
  if (focused) $("#create-save", dialog).focus();
}

// The text to paste to an AI agent, from what is chosen and typed, then what goes with it: the skill the agent
// follows, the folder to open the agent in, and that the skill is called by its name
function drawPrompt() {
  const { skills, root } = store.state, path = skills[kind];
  const text = ask(path, chosen.catalog ? `create ${A[kind]} in the catalog ${nameOf(chosen.catalog)}` : `create a personal ${NOUN[kind]}`)
    + (task.value.trim() ? ` What it should do: ${task.value.trim()}` : "");
  $("#create-prompt", dialog).innerHTML = promptLine(text) + line("Folder", `<code>${esc(root)}</code>`, root);
  setHTML($("#create-agent-actions", dialog), agentLinks(text, root));
}

// An add-on has a category, and so has anything put in a catalog
function needCategory() {
  category.required = kind === "addon" || !!chosen.catalog;
  category.firstElementChild.textContent = category.required ? "Choose a category" : "No category";
}

// The two choices, then the form or the prompt: a prompt is copied, there is nothing to create here
function draw() {
  needCategory();
  const prompt = chosen.how === "agent";
  for (const button of $$("[role=radio]", choices)) {
    button.setAttribute("aria-checked", String(button.dataset.value === chosen[button.parentElement.dataset.choice]));
  }
  fields.hidden = submit.hidden = prompt;
  agent.hidden = !prompt;
  $("#create-agent-actions", dialog).hidden = !prompt;
  cancel.textContent = prompt ? "Close" : "Cancel";
  if (prompt) drawPrompt(); else drawPicks();
  drawSubmit();
}

choices.addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  chosen[button.parentElement.dataset.choice] = button.dataset.value;
  draw();
});
list.addEventListener("change", (e) => {
  const [what, id] = splitKey(e.target.dataset.pick);
  if (e.target.checked) picks[what].add(id); else picks[what].delete(id);
  drawPicks();
  $(`[data-pick="${e.target.dataset.pick}"]`, list).focus();   // the list was drawn again: the keyboard stays where it was
});
search.addEventListener("input", drawPicks);
fields.addEventListener("input", ready);
// Enter in the search would create the item
search.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
task.addEventListener("input", drawPrompt);
cancel.addEventListener("click", () => dialog.close());
// What the catalogs say reaches the checklist while it is open (the add-ons arrive, or fail to): drawn again only when that changed
store.subscribe((state) => { if (dialog.open && kind && chosen.how === "form" && state && signature(state) !== listed) drawPicks(); });
// The form stays open until the server answered: a refusal shows in it, and a success closes this form, not one
// opened since
dialog.addEventListener("cancel", (e) => { if (sending) e.preventDefault(); });

// Creating writes the files and nothing else: what runs is not touched. The server reads the fields of the kind.
// An add-on or a playground is then completed in its compose file: the editor opens on it. A refusal shows where a
// conflict does, and in the error tone of the button. A setup of this machine renamed moves its file, and the files
// that include it are written again: the server says which first (a check that writes nothing), and they are
// confirmed before.
$("form", dialog).addEventListener("submit", async (e) => {
  e.preventDefault();
  if (sending) return;
  sending = cancel.disabled = true;
  const { key, verb } = acting();
  const body = {
    id: editing, name: name.value.trim(), description: description.value.trim(), category: category.value,
    image: image.value.trim(), port: port.value.trim(), setups: [...picks.setup], addons: [...picks.addon],
    catalog: chosen.catalog || undefined,
  };
  try {
    if (editing) {
      const check = await busy(key, verb, api(`/api/${kind}s`, { ...body, check: true }), { quiet: true });
      const rows = [{ label: "Moves to", items: [check.file] }, { label: "Changes", items: check.changes }];
      if (check.changes.length && !(await confirmEffects(`Save ${body.name}`, rows, "Save"))) return;
    }
    const made = await busy(key, verb, api(`/api/${kind}s`, body));
    dialog.close();
    if (kind !== "setup") openFile(made.file);
    toast(editing ? "Saved" : "Created");
    store.refresh();
  } catch (err) {
    problem.hidden = false;
    problem.textContent = err.message;
  } finally {
    sending = cancel.disabled = false;
  }
});

// mode: "new"; for a setup also "edit" (one of this machine, saved in place) or "duplicate" (any, saved as a new one)
export function openCreate({ kind: what, mode, setup }) {
  const { categories, skills, running } = store.state;
  kind = what;
  editing = mode === "edit" ? setup.id : null;
  $("h2", dialog).textContent = editing ? `Edit ${setup.name}` : mode === "duplicate" ? `Duplicate ${setup.name}` : `New ${NOUN[kind]}`;
  // A setup that exists stays where it is, changed by its form; without its skill, an agent has nothing to follow
  choices.hidden = !!editing;
  $("[data-choice=how]", choices).hidden = !skills[kind];
  Object.assign(chosen, { how: "form", catalog: "" });
  // Where it is kept: Personal, then the catalogs that are folders of the person's, each by name. With none the choice is not drawn.
  const folders = editable();
  where.hidden = !folders.length;
  where.innerHTML = folders.length ? `<button type="button" role="radio" data-value="" data-tip="This machine, out of git">Personal</button>${
    folders.map((c) => `<button type="button" role="radio" data-value="${esc(c.id)}"${c.address ? ` data-tip="${esc(c.address)}"` : ""}>${esc(c.name)}</button>`).join("")}` : "";
  // The fields another kind has are neither shown nor asked for
  for (const field of $$("[data-kind]", fields)) {
    field.hidden = !field.dataset.kind.includes(kind);
    for (const input of $$("input", field)) input.disabled = field.hidden;
  }
  // An add-on and a playground say what they are; an add-on has a category
  description.required = kind !== "setup";
  description.placeholder = description.required ? "" : "Optional";
  category.innerHTML = `<option value=""></option>` + categories.map((c) => `<option>${esc(c)}</option>`).join("");
  needCategory();
  $("#create-picks-label", dialog).textContent = PICKS[kind] || "";

  name.value = (mode === "duplicate" ? `Copy of ${setup.name}` : setup?.name || "").slice(0, name.maxLength);
  description.value = setup?.description || "";
  category.value = setup?.category || "";
  image.value = port.value = search.value = task.value = "";
  // A new setup starts from the add-ons turned on by hand, without what a playground brought
  picks = { setup: new Set(), addon: new Set(kind === "setup" ? setup?.includes || running.explicit : []) };
  draw();
  dialog.showModal();
  name.select();
}

// --- Sharing ----------------------------------------------------------------------

// What sharing an item answered: what keeps it from being shared, or what is left to do once it is; with no catalog to share to, the way to
// one. Its actions are written each time.
const notice = document.createElement("dialog");
notice.className = "share-notice";
notice.setAttribute("aria-labelledby", "share-title");
notice.innerHTML = `
  <form method="dialog">
    <h2 id="share-title"></h2>
    <dl class="pairs"></dl>
    <div class="dialog-actions"></div>
  </form>`;
document.body.append(notice);
const CLOSE = '<button class="btn">Close</button>';

// Says something of sharing an item: its pairs (`pairs` as markup, "" for none: the list is then not there) and its actions, written each time
function say(item, pairs, actions) {
  const list = $("dl", notice);
  $("h2", notice).textContent = `Share ${item.name}`;
  list.hidden = !pairs;
  list.innerHTML = pairs;
  $(".dialog-actions", notice).innerHTML = actions;
  notice.showModal();
}

// New catalog and Add catalog open the dialogs of the Catalogs page; this one is closed first
notice.addEventListener("click", (e) => {
  const which = e.target.closest("[data-share-catalog]");
  if (!which) return;
  notice.close();
  if (which.dataset.shareCatalog === "new") openNew();
  else openAdd();
});

// The words of who can read a catalog, measured
const AUDIENCE = { public: "Public", private: "Private" };
// The repository an address names, as owner/repo: its host is not part of it
const repoOf = (address) => String(address || "").split("/").slice(1).join("/");
const rowOf = (id) => catalogRows().find((c) => c.id === id);

// The rows an option of the dialog says about a destination: where a branch opens, what goes with it, what changes in the file, what stays
// and what is left out, and under whom it is committed when everyone can read it; for a folder, where the copy goes, what goes with it and
// what stays
function rowsOf(destination, check) {
  const folder = destination.kind === "folder", note = true;
  // What only this machine has goes with it; what the catalog has already is the one it includes there
  const along = [{ label: "Also shares", items: check.carries || [], note }, { label: "Uses the catalog's", items: check.reuses || [], note },
    // Its own settings are named for the catalog it goes to, as an item of it reads them; the person's values follow it there
    { label: "Renames", items: (check.renames || []).map((one) => `${one.from} → ${one.to}`), note }];
  if (folder) return [{ label: "Copies to", items: [destination.name], note }, ...along, { label: "Keeps", items: ["Your copy and its settings"], note }];
  const repo = repoOf(rowOf(destination.id)?.address);
  return [{ label: "Opens", items: repo ? [`A branch in ${repo}`] : [], note }, ...along, { label: "Changes", items: check.changes || [], note },
    { label: "Keeps", items: ["Your copy and its settings"], note }, { label: "Leaves out", items: ["Your settings and data"], note },
    { label: "Commits as", items: check.identity ? [check.identity] : [], hint: "Visible publicly", note }];
}

// Asks where to share: the effects dialog with an option for each destination. From two destinations none is chosen and Share waits for a
// choice, so that Enter can never send private material to a public place; a destination that cannot take the item cannot be chosen, and
// says why. Each of several says who can read it; a single one is no choice, and its rows say where it goes. Resolves with the destination's
// id, null when canceled.
async function chooseWhere(item, destinations, checks) {
  const options = destinations.map((destination, i) => {
    const check = checks[i], why = destination.reason || check.error || check.problems?.[0] || "";
    return { value: destination.id, label: destination.name, sub: destinations.length > 1 ? AUDIENCE[check.audience || destination.audience] || "" : "",
      disabled: !!why, reason: why, rows: rowsOf(destination, check) };
  });
  return effectsDialog({ title: `Share ${item.name}`, options, action: () => "Share", unchosen: "Choose where to share" });
}

// What is left to do once the branch is pushed: its name, to copy, and the address that opens the pull request when the host has one
function sharedNotice(item, done) {
  const open = safeHref(done.pull_request);
  say(item, line("Branch", `<code>${esc(done.branch)}</code>`, done.branch),
    `${open ? `<a class="btn" href="${esc(open)}" target="_blank" rel="noopener">${icon("external")}Open pull request</a>` : ""}${CLOSE}`);
}

// The catalogs a share can go to, from the server's answer to the first check: its list, else (an answer that has none) the catalogs added
// that are a repository or a folder, as the Catalogs page has them, the first catalog first. Whether one takes contributions, or can be pushed
// to, is what the check of each says.
const destinationsOf = (answer) => (Array.isArray(answer?.destinations) ? answer.destinations
  : catalogRows().filter((c) => ["git", "folder"].includes(c.kind)).map((c) => ({ id: c.id, name: c.name, kind: c.kind, writable: true, audience: c.audience || "", reason: "" })));

// Shares an item of this machine to a catalog that takes it, as a copy: the item stays, runs and keeps its settings. The server says first
// which catalogs there are, and what sharing to each would do (a check that writes nothing); the dialog says it, the person chooses, and only
// then is it done: a branch pushed to a catalog that is a repository, a copy into a folder. A problem that every destination finds is a
// notice of what to fix first, with the prompt that has an AI agent fix it.
export async function share(item) {
  const route = `/api/${item.kind}s/${item.id}/share`, key = `${item.kind}:${item.id}`, skill = store.state.skills.share;
  const fixFirst = (problems) => {
    const text = skill ? ask(skill, `share the personal ${NOUN[item.kind]} ${item.name}`) : "";
    say(item, problems.map((p, i) => line(i ? "" : "Fix first", esc(p))).join("") + (text ? promptLine(text) : ""),
      `${text ? agentLinks(text, store.state.root) : ""}${CLOSE}`);   // the agents of the creation dialog, for the same prompt
  };
  try {
    // The check of each destination that can take the item, said on the item's button as "Sharing"; in the error tone when nothing can be
    // shared until a problem is fixed
    const checking = (async () => {
      const destinations = destinationsOf(await api(route, { check: true }));
      const checks = await Promise.all(destinations.map((d) => (d.reason || d.writable === false ? {}
        : api(route, { check: true, to: d.id }).catch((err) => ({ error: err.message })))));
      return { destinations, checks };
    })();
    const stuck = (found) => (found.destinations.length && !found.checks.some((c, i) => !found.destinations[i].reason && found.destinations[i].writable !== false && !c.error && !c.problems?.length)
      ? [...new Set(found.checks.flatMap((c) => c.problems || []))] : []);
    busy(key, "share", checking.then((found) => { if (stuck(found).length) throw new Error(stuck(found)[0]); }), { quiet: true }).catch(() => {});   // said by the button
    const found = await checking;
    // With no catalog the way to one: no option, and the two dialogs of the Catalogs page in place of Share
    if (!found.destinations.length) {
      return say(item, "", `<button type="button" class="btn" data-share-catalog="new">New catalog</button>
        <button type="button" class="btn" data-share-catalog="add">Add catalog</button>${CLOSE}`);
    }
    if (stuck(found).length) return fixFirst(stuck(found));
    const to = await chooseWhere(item, found.destinations, found.checks);
    if (!to) return;
    const done = await busy(key, "share", api(route, { to }));
    if (done.copied !== undefined) toast(done.warning ? `Copied. ${done.warning}` : "Copied", !!done.warning);
    else {
      sharedNotice(item, done);
      toast("Shared");
    }
    store.refresh();
  } catch (err) {
    toast(err.message, true);
  }
}
