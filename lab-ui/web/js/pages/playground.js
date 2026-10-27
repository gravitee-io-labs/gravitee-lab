// A playground's page: what it is, what starting it takes and its guide to read first, its progress while it starts,
// then its guide next to everything it runs. With a platform registered it also says where it deploys: This machine or an
// environment of the platform, a place chosen in its head. A playground of a catalog says which in its head, and what it lacks of the
// catalogs and of this Lab before it can start; one of this machine has a More, with its Share and its Delete.

import { $, $$, esc, icon, setHTML, loading, api, store, router, recall, remember, hrefOf, ID_PATTERN, splitKey, credHtml } from "../core.js";
import { effectsDialog, paintLinks, platformLinks, progressBar, dockerDown, stateDot, toast, openMenu, originLink, catalogNeed,
  actionButton, paintActions, planRows, accepted, itemLink, openButton, noteHtml, licenseForm, starButton, toggleStar, pluginWords,
  GAP, applyBadge, TURNS, fileButtons } from "../components/ui.js";
import { runJob, busy, showOutput, cancelJob, jobTitle } from "../activity/jobs.js";
import { openFile, openSettings } from "../dialogs/editor.js";
import { share, home } from "../dialogs/create.js";
import { addCatalog, tryAgain } from "../dialogs/catalog.js";
import { openCreateGateway, openCloudToken } from "../dialogs/platform.js";
import { MACHINE, THIS_MACHINE, registered, goes, whyNot, entry, keepPlace, chosenPlace, chosenId, holding, barred, placesOf,
  recordName, deployTo, startPlayground } from "../platform/places.js";
import { License } from "../dialogs/license.js";
import { markdownHtml } from "../components/markdown.js";
import { Combo } from "../components/combo.js";
import { Workspace } from "../workspace/workspace.js";
import { plainClick } from "../workspace/split.js";

const PLACES = 60000;   // how often the places are read again while the page is on screen
const PREP_ICON = { warn: icon("alert"), auto: icon("restart"), ok: icon("check") };
// The title of what will not work by itself, before the start and once it runs
const ATTENTION = "Needs your attention";

let root = null;     // the page's section
let shown = null;    // the id of the playground on screen
let checks = null;   // what starting it takes; null while it is read
// What its Start says before it runs, as the checks read it (their `dialog`, null when it does no more than the
// playground): what installing its plugins restarts too. Undefined while they are read.
let said;
let settled;         // the finished job the checks were read after; undefined until they are read
let asked = 0;       // the last read of the checks: only its answer is kept
let timer = null;    // ticks the seconds of the running job while the page is on screen
let refresher = null;   // reads the places again every minute while the page is on screen
let read;            // the place the checks were asked for ("machine" or a place's id); undefined until they are
const guides = {};   // the guides read so far, by the version of their text: {version: markdown}
let step = "";       // the page of the guide the address names ("4"); "" for the one read last
let stepping = false;   // the page is left for another page of the same guide: what the checks say still stands
let toGuide = false;    // a click asked for another page of the guide: it is brought into view (see renderGuide)

// Another page of this playground's guide, at `href`, in the place of the one on screen in the history, always: a UI beside the
// guide adds its own entries to it, and a Back inside one must never go back through a turn of the guide. The same turn
// before a start, which has no UI, so that a page of the guide is turned one way.
function turn(href) {
  toGuide = true;
  router.replace(href);
}

// The plugins a playground requires, whatever the product
const requiredPlugins = (item) => Object.values(item.requires.plugins || {}).flat();
// What its seed created in Gravitee, as a person counts it: "3 APIs", "1 Application"
export const seeded = (item) => item.created.map((c) => `${c.count} ${c.count === 1 ? c.what.replace(/ies$/, "y").replace(/s$/, "") : c.what}`);

// --- Items of this machine and of catalogs ----------------------------------------------

// What an item uses of this machine beyond its containers, in the words its author gave (`uses_machine`): their reasons, once each, cut at
// USES characters, and the whole in a tooltip where the page cuts it. "" for most items.
const USES = 120;
export function usesMachine(item) {
  const letters = Array.from([...new Set((item.uses_machine || []).map((use) => use.why).filter(Boolean))].join(", "));
  return letters.length > USES ? `${letters.slice(0, USES - 1).join("").trimEnd()}\u2026` : letters.join("");
}

// What keeps Share and Delete of an item of this machine: the reason the server holds them for, and the Lab UI not answering
function held(why) {
  const wait = store.wait(), offline = store.offline ? wait : "";
  return { disabled: !!(offline || why), reason: offline || why };
}
export const shareEntry = (x) => ({ label: "Share", ...held(x.held?.share), onSelect: () => share(x) });

// A playground in a file, which another Lab imports from its Playgrounds page: one of this machine, or of a catalog that not every Lab
// has. One of the default catalog (a bare id) comes with every Lab, and has nothing to export.
export const exportable = (x) => x?.kind === "playground" && /[/@]/.test(String(x.id));
export const exportEntry = (x) => ({ label: "Export", ...held(""), onSelect: () => exportPlayground(x) });

// What the export carries, what it needs of the other Lab, and what that Lab will make of it, as the server judges the file it would
// make with the rules of that Lab; then the file, saved where the browser saves downloads. What would be blocked there is said, and
// keeps the file from being made: it would not start. What comes from a private catalog leaves with the file: it is said, and the
// button says it too.
async function exportPlayground(x) {
  const key = `playground:${x.id}`, route = `/api/playgrounds/${x.id}/export`;
  let plan;
  try {
    plan = await busy(key, "export", api(route, { check: true }));
  } catch (err) {
    return toast(err.message, true);
  }
  const blocked = (plan.blocked || []).flatMap((one) => (one.reasons || []).map((reason) => `${one.name}: ${reason}`));
  const rows = [
    { label: "Carries", items: plan.carries || [] },

    { label: "Needs", items: (plan.needs || []).map(({ name }) => ({ name, kind: "catalog" })) },
    { label: "Private", items: (plan.private || []).map((one) => ({ name: one.name, literal: true })), note: true },
    // A registry that asks for a login: the person who receives the file needs that access to pull its images
    { label: "Needs access", items: (plan.registries || []).map((one) => one.registry), note: true },
    { label: "Asks to use the machine", items: (plan.uses_machine || []).map((use) => `${use.name}: ${(use.what || []).join(", ")}`), note: true },
    { label: "Leaves out", items: ["Its settings", "The license", ...(plan.problems || [])], note: true },
    { label: "Blocked on another Lab", items: blocked, tone: "danger", note: true },
  ];
  const why = blocked.length ? "It would be blocked on another Lab" : "";
  const go = await effectsDialog({ title: `Export ${x.name}`, options: [{ rows, disabled: !!why, reason: why }],
    action: () => ((plan.private || []).length ? "Export with private content" : "Export"), unchosen: "" });
  if (!go) return;
  try {
    // The items of private content the person saw, each under each origin: the server makes the file only if it carries no other
    const seen = (plan.private || []).flatMap((one) => (one.item_ids || []).map((id) => `${one.key}|${id}`));
    const made = await busy(key, "export", api(route, seen.length ? { private: seen } : {}));
    save(made.file, made.data);
    toast(`Exported ${made.file}`);
  } catch (err) {
    toast(err.message, true);
  }
}

// The same playground for Docker Compose alone, with no Lab: a folder with one docker-compose.yml, its .env.example and the guide as its
// README. Any playground, those of the default catalog too. What it runs and leaves out, what it needs, and what leaves with it from a
// private catalog are said first, as the server makes the folder.
export const standaloneEntry = (x) => ({ label: "Export for Docker Compose", ...held(""), onSelect: () => exportStandalone(x) });

async function exportStandalone(x) {
  const key = `playground:${x.id}`, route = `/api/playgrounds/${x.id}/standalone`;
  let plan;
  try {
    plan = await busy(key, "export", api(route, { check: true }));
  } catch (err) {
    return toast(err.message, true);
  }
  // The features of the license by name while they fit a line, else how many: the README of the folder names each
  const features = plan.license || [];
  const license = !features.length ? "A license" : features.length <= 2 ? `A license with ${features.join(", ")}`
    : `A license with ${features.length} features`;
  const rows = [
    { label: "Runs", items: plan.runs || [] },
    { label: "Leaves out", items: [...(plan.leaves_out || []), "The Lab UI", "Its settings", "The license"], note: true },
    { label: "Needs", items: [license, ...(plan.host || []), ...(plan.plugins_missing || []).map((id) => `The plugin ${id}`)], note: true },
    { label: "Private", items: (plan.private || []).map((one) => ({ name: one.name, literal: true })), note: true },
    { label: "Needs access", items: (plan.registries || []).map((one) => one.registry), note: true },
  ];
  const go = await effectsDialog({ title: `Export ${x.name} for Docker Compose`, options: [{ rows }],
    action: () => ((plan.private || []).length ? "Export with private content" : "Export"), unchosen: "" });
  if (!go) return;
  try {
    // The items of private content the person saw, each under each origin: the server makes the file only if it carries no other
    const seen = (plan.private || []).flatMap((one) => (one.item_ids || []).map((id) => `${one.key}|${id}`));
    const made = await busy(key, "export", api(route, seen.length ? { private: seen } : {}));
    save(made.file, made.data);
    toast(`Exported ${made.file}`);
  } catch (err) {
    toast(err.message, true);
  }
}

// An item imported from a file and kept as a catalog of its own becomes the person's own: alone, or with what it brings from the file that
// nothing else uses. What it moves, what stays and why, and what it replaces in Personal are said first, as the server plans them.
export const movable = (x) => x?.origin?.id === "imported";
export const moveEntry = (x) => ({ label: "Move to Personal", ...held(""), onSelect: () => moveToPersonal(x) });

async function moveToPersonal(x) {
  const key = `${x.kind}:${x.id}`, route = `/api/${x.kind}s/${x.id}/move`;
  let plan;
  try {
    plan = await busy(key, "move", api(route, { check: true }));
  } catch (err) {
    return toast(err.message, true);
  }
  if (plan.used_by) return toast(`Used by ${plan.used_by}: move that one to Personal instead`, true);
  // What takes more of this machine, which nothing judges once it is Personal's, moves only once the person allows it: the button says so
  const rows = (p) => [{ label: "Moves", items: p.moves }, { label: "Replaces", items: p.replaces, tone: "danger" },
    { label: "Uses this machine", items: p.uses_machine || [], note: true },
    { label: "Stays", items: p.stays.map((one) => `${one.name}: ${one.why}`), note: true }];
  const planOf = (value) => (value === "together" ? plan.together : plan.alone) || plan.alone;
  const allows = (value) => (planOf(value).uses_machine || []).length > 0;
  const option = (p, value, label, sub) => ({ value, label, sub, rows: rows(p), disabled: !!p.held, reason: p.held });
  const options = plan.together
    ? [option(plan.together, "together", "With its setups and add-ons", "What nothing else uses"), option(plan.alone, "alone", `Only the ${x.kind === "playground" ? "playground" : "item"}`, "Its setups and add-ons stay in Imported")]
    : [option(plan.alone, "alone", "")];
  const chosen = await effectsDialog({ title: `Move ${x.name} to Personal`, options, action: (value) => (allows(value) ? "Allow and move" : "Move") });
  if (!chosen) return;
  try {
    const moved = await busy(key, "move", api(route, { together: chosen === "together", ...(allows(chosen) ? { allow: true } : {}) }));
    toast(moved.warning ? `Moved. ${moved.warning}` : "Moved", !!moved.warning);
    await store.refresh();
    // The page of what was imported is gone: the one of the item in Personal takes its place in the history
    router.replace(x.kind === "playground" ? `#/playgrounds/${moved.id}` : `#/${x.kind}s/${moved.id}`);
  } catch (err) {
    toast(err.message, true);
  }
}

// The person's own version of an item of a catalog, to change as they like: alone, or with what uses it in the catalogs, which is then
// copied too and uses the copy. What it copies, what of Personal then uses the copy, and what takes more of this machine are said first.
export const copyable = (x) => !!x && !x.personal && !movable(x) && !x.twin;
export const copyEntry = (x) => ({ label: "Copy to Personal", ...held(""), onSelect: () => copyToPersonal(x) });

async function copyToPersonal(x) {
  const key = `${x.kind}:${x.id}`, route = `/api/${x.kind}s/${x.id}/copy`;
  let plan;
  try {
    plan = await busy(key, "copy", api(route, { check: true }));
  } catch (err) {
    return toast(err.message, true);
  }
  const rows = (p) => [{ label: "Copies", items: p.copies }, { label: "Uses the copy", items: p.switches, note: true },
    { label: "Uses this machine", items: p.uses_machine || [], note: true }];
  const option = (p, value, label, sub) => ({ value, label, sub, rows: rows(p), disabled: !!p.held, reason: p.held });
  const options = plan.with_users
    ? [option(plan.alone, "alone", `Only ${x.name}`, "What uses it keeps the original"), option(plan.with_users, "users", `${x.name} and what uses it`, "Copied too, so that it uses your copy")]
    : [option(plan.alone, "alone", "")];
  const allows = (value) => (((value === "users" ? plan.with_users : plan.alone) || plan.alone).uses_machine || []).length > 0;
  const chosen = await effectsDialog({ title: `Copy ${x.name} to Personal`, options, action: (value) => (allows(value) ? "Allow and copy" : "Copy") });
  if (!chosen) return;
  try {
    const copied = await busy(key, "copy", api(route, { users: chosen === "users", ...(allows(chosen) ? { allow: true } : {}) }));
    toast(copied.warning ? `Copied. ${copied.warning}` : "Copied", !!copied.warning);
    await store.refresh();
    router.replace(x.kind === "playground" ? `#/playgrounds/${copied.id}` : `#/${x.kind}s/${copied.id}`);
  } catch (err) {
    toast(err.message, true);
  }
}

// The menu of a playground's head: Export when it can be, Export for Docker Compose, Move to Personal for an imported one, Copy to Personal for one of a catalog,
// Share and Delete for one of this machine
const menuOf = (x) => [exportable(x) && exportEntry(x), x.kind === "playground" && standaloneEntry(x), movable(x) && moveEntry(x), copyable(x) && copyEntry(x), x.personal && shareEntry(x),
  ...(x.personal ? [{ separator: true }, deleteEntry(x)] : [])].filter(Boolean);

// A file the server made, as base64, saved where the browser saves downloads
function save(name, data) {
  const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
export const deleteEntry = (x) => ({ label: "Delete", danger: true, ...held(x.held?.delete), onSelect: () => deleteItem(x) });

// A personal item goes with its folder. What a playground's seed created stays in Gravitee: the dialog says so. The menu of a row and of
// a playground's page do it alike.
async function deleteItem(x) {
  const rows = [{ label: "Deletes", items: [home(x.kind, x.display_file || x.file)], tone: "danger" },
    { label: "Stays in Gravitee", items: x.kind === "playground" ? seeded(x) : [], note: true }];
  if (!(await effectsDialog({ title: `Delete ${x.name}`, options: [{ rows }], action: () => "Delete", danger: () => true }))) return;
  try {
    await busy(`${x.kind}:${x.id}`, "delete", api(`/api/${x.kind}s/${x.id}/delete`, {}));
    toast("Deleted");
  } catch (err) {
    toast(err.message, true);
  }
  store.refresh();
}

// A button that cannot be used says why, in the server's words; one that can says what it does (`title`), both as its tooltip
const lock = (action, title = "") => (action.enabled ? (title ? `data-tip="${esc(title)}"` : "") : `aria-disabled="true" data-why="${esc(action.reason)}"`);
// Items by name, each leading to its row in its list
const linked = (rows, kind) => rows.map((row) => itemLink({ kind, ...row })).join(", ");
// A plugin as people read it, its name then its type, the artifact name in its tooltip: "gravitee-policy-interrupt"
// reads "Interrupt policy"
function pluginName(id) {
  const [type, ...name] = pluginWords(id), text = name.length ? `${name.join(" ")} ${type}` : type;
  return `<span data-tip="${esc(id)}">${esc(text[0].toUpperCase() + text.slice(1))}</span>`;
}
// The plugins a playground requires, as its page and its row in the list name them; "" without any
export const pluginNames = (item) => requiredPlugins(item).map(pluginName).join(", ");

// What the buttons of the page act on, as its jobs name it (`for`)
const keyOf = (item) => `playground:${item.id}`;

// --- Places -----------------------------------------------------------------------------

// Whether the seed recorded something in a place of a platform
const holds = (item, place) => (item.deployed || []).some((d) => d.place === place.id && d.created.length);

// What the seed recorded in each place, This machine first: [{id, name, what}], `what` as seeded() counts it. A place
// without a record is not listed.
function records(item) {
  const local = seeded(item);
  return [...(local.length ? [{ id: MACHINE, name: THIS_MACHINE.name, what: local }] : []),
    ...(item.deployed || []).filter((d) => d.created.length).map((d) => ({ id: d.place, name: recordName(d), what: seeded(d) }))];
}
const at = (record) => `${record.what.join(", ")} in ${record.name}`;

// Why what a remote place holds cannot be deleted now: the playground is off (the seed runs only then), or the place cannot
// be reached; "" when it can
const TOKENS = { unset: "No Cloud Token", expired: "Cloud Token expired", refused: "Cloud Token refused" };
function unreachable(item, record) {
  if (!item.on) return `${item.name} is off`;
  const place = entry(store.places, record.id);
  if (!place) return store.placesRead ? "Account not connected" : "Loading";
  return place.state === "unusable" ? place.reason : TOKENS[place.token?.state] || "";
}

// What stops the playground, as the server words it: its Stop while it runs, its Turn off while it is on and does not
// run; the primary action, or the one of its menu
const stopOf = (item) => (item.action.do === "stop" ? item.action : item.secondary);

// The Start and the Stop of the playground. Drawn side by side (`apart`), each shows its own action. Drawn alone,
// either one is the playground's toggle, as on its row: it shows a start and a stop alike, so that the button a job
// put at work gives its face back, once the playground got where the job took it, as the one drawn then (Stop once it runs, Start
// once it stopped), with the same key and verbs. The stop first: at work, the button takes the room of "Stopping
// 100%", the wider. Both wait for the Lab UI; their words are the server's (Start or Turn on, Stop or Turn
// off).
const startStop = (apart, verb) => (apart ? verb : ["stop", "start"]);

// On a remote place it waits for the place (`barred`), not for what the server says of This machine. Start, which starts its
// containers, is the page's primary with its icon; Turn on, for a playground that already runs (started outside the Lab UI), only
// keeps it with the platform: a plain button, no icon.
const startButton = (item, apart, cls = "primary") => {
  const why = barred(item), turn = item.action.label !== "Start";
  return actionButton({ key: keyOf(item), verb: startStop(apart, "start"), label: item.action.label, icon: turn ? "" : "play",
    cls: turn ? cls.replace("primary", "").trim() : cls,
    attrs: `data-act="start" data-busy ${lock(why ? { enabled: false, reason: why } : item.action, TURNS[item.action.label])}` });
};

const stopButton = (item, apart) => {
  const stop = stopOf(item);
  return actionButton({ key: keyOf(item), verb: startStop(apart, "stop"), label: stop?.label || "Stop", icon: "stop",
    attrs: `data-act="stop" data-busy ${stop ? lock(stop, TURNS[stop.label]) : ""}` });
};

// Re-seed waits for what the seed needs to run, the server says which
const reseedButton = (item, why) => actionButton({ key: keyOf(item), verb: "reseed", label: "Re-seed", icon: "restart",
  attrs: `data-act="reseed" data-busy ${lock(why ? { enabled: false, reason: why } : item.reseed, "Create again what the playground needs in Gravitee")}` });

// What seeds again: Re-seed, or Deploy on a remote place that holds nothing yet. On a remote place the seed runs apart from
// the platform, so it waits for the place alone.
function seedButton(item) {
  const place = chosenPlace(item), why = barred(item);
  if (!place) return reseedButton(item, why);
  const deploy = !holds(item, place);
  return actionButton({ key: keyOf(item), verb: ["deploy", "reseed"], label: deploy ? "Deploy" : "Re-seed", icon: "restart",
    attrs: `data-act="${deploy ? "deploy" : "reseed"}" data-busy ${lock({ enabled: !why, reason: why },
      `Create ${deploy ? "" : "again "}what the playground needs in ${place.name}`)}` });
}

// Where it deploys, in the head of every playground once a platform is registered: This machine, then the environments of the
// platforms, each with what it lacks. A place that lacks something can be chosen, its Before you start offers the way out; one
// the playground cannot go to, and a platform that cannot be used, are options that cannot be chosen, each with its reason.
function picker(item) {
  if (!registered()) return "";
  const held = holding(item), text = `Deploy to ${chosenPlace(item)?.name || THIS_MACHINE.name}`;
  return `<button class="combo-btn" data-place data-busy ${held ? 'aria-disabled="true" data-why="Loading"' : `data-tip="${esc(text)}"`} aria-haspopup="dialog"
    ><span class="value">${held ? loading("Loading") : esc(text)}</span>${icon("chevron")}</button>`;
}

// A place in the picker: what keeps it from being chosen, else what it lacks, as its muted tail
function option(item, place) {
  if (!goes(item, place)) return { value: place.id, name: place.name, meta: whyNot(item, place), disabled: true };
  return { value: place.id, name: place.name, meta: place.state === "unusable" ? place.reason : place.missing || "", disabled: place.state === "unusable" };
}

function choosePlace(item, button) {
  const things = [{ value: MACHINE, name: THIS_MACHINE.name }, ...placesOf().map((p) => option(item, p))];
  Combo.open(button, {
    plain: true, current: chosenId(item),
    groups: (q) => [{ items: things.filter((it) => it.name.toLowerCase().includes(q)) }],
    choose: (value) => {
      keepPlace(value);
      render(store.state);
    },
  });
}

// The job the page shows: the one running on this playground, or its last one when it failed or was canceled
function shownJob(item, state) {
  const job = state.job;
  return job?.for === keyOf(item) && !(job.done && job.ok) ? job : null;
}

// Whether it is on, up, or being set up: its page shows it running
const living = (item, state) => item.on || item.up || !!shownJob(item, state);

// --- Data -------------------------------------------------------------------------------

// The places, read again: what they lack may have changed. The page is drawn once they are known, for the place it kept.
function readPlaces() {
  return store.refreshPlaces().then(() => {
    if (store.item("playground", shown) && registered() && !root.hidden) render(store.state);
  });
}

// The checks, and what its Start says first, for the place the picker is on. While they are read again, and when they cannot
// be read, its Start says what the state says. The places are read with them.
async function loadChecks() {
  const turn = ++asked, item = store.item("playground", shown), place = item && chosenPlace(item);
  let lines, dialog;
  said = undefined;
  read = place?.id || MACHINE;
  readPlaces();
  try {
    const path = `/api/playgrounds/${shown}/check`;
    ({ checks: lines, dialog } = await api(place ? `${path}?place=${encodeURIComponent(place.id)}` : path));
  } catch (err) {
    lines = [{ name: "Requirements", state: "warn", detail: err.message }];
  }
  if (turn !== asked) return;   // read again since: after a job, or for another playground
  checks = lines;
  said = dialog;
  if (!root.hidden) render(store.state);
}

// --- Guide ------------------------------------------------------------------------------

// The guide in pages: what comes before its first section, then one page per "## " section. A numbered section is a
// step, its address its number ("4"); another one is named after its title ("if-something-does-not-work"). A line
// of a code block never starts a page.
const INTRO = "introduction";
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
function guidePages(markdown) {
  const pages = [{ slug: INTRO, title: "Introduction", step: null, lines: [] }];
  let fenced = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const heading = !fenced && line.match(/^## +(.+?)\s*$/);
    if (heading) {
      const numbered = heading[1].match(/^(\d+)\.\s*(.+)$/);
      pages.push({ slug: numbered ? numbered[1] : slug(heading[1]), title: heading[1],
        step: numbered ? Number(numbered[1]) : null, lines: [] });
    }
    pages.at(-1).lines.push(line);
  }
  return pages.map(({ lines, ...page }) => ({ ...page, markdown: lines.join("\n") }))
    .filter((page) => page.slug !== INTRO || page.markdown.trim());
}

const stepHref = (item, page) => `#/playgrounds/${item.id}/${page.slug}`;
const lastRead = (item) => `guide:${item.id}`;   // the page of its guide this browser read last

// The page on screen: the one the address names, else the one read last, else the first
function currentPage(item, pages) {
  const wanted = step || recall(lastRead(item));
  return pages.find((page) => page.slug === wanted) || pages[0];
}

// Previous and next: at the top, beside the picker of every page; at the bottom, with the title of where they lead
function guideNav(item, pages, at) {
  const [prev, next] = [pages[at - 1], pages[at + 1]];
  const arrow = (page, cls, label) => (page
    ? `<a class="icon-btn ${cls}" href="${esc(stepHref(item, page))}" data-tip="${esc(page.title)}" aria-label="${label}">${icon("chevron")}</a>`
    : `<button class="icon-btn ${cls}" disabled aria-label="${label}">${icon("chevron")}</button>`);
  const steps = pages.filter((page) => page.step !== null).length, page = pages[at];
  return `<nav class="guide-nav">${arrow(prev, "guide-prev", "Previous")}
      <button class="combo-btn" data-steps aria-haspopup="dialog"><span class="sr-only">Page of the guide: </span><span class="value">${esc(page.title)}</span>${icon("chevron")}</button>
      ${arrow(next, "guide-next", "Next")}
      ${page.step !== null && steps ? `<span class="sub guide-count">Step ${pages.filter((p) => p.step !== null).indexOf(page) + 1} of ${steps}</span>` : ""}</nav>`;
}

function guideFoot(item, pages, at) {
  const link = (page, cls) => (page ? `<a class="guide-link ${cls}" href="${esc(stepHref(item, page))}">
      <span class="sub">${cls === "guide-prev" ? "Previous" : "Next"}</span><strong>${esc(page.title)}</strong></a>` : "<span></span>");
  return `<nav class="guide-foot">${link(pages[at - 1], "guide-prev")}${link(pages[at + 1], "guide-next")}</nav>`;
}

// Every page of the guide, the steps first, then what is not a step
function choosePage(item, button) {
  const pages = guidePages(guides[item.guide] || ""), at = currentPage(item, pages);
  const items = (list) => list.map((page) => ({ value: page.slug, name: page.title }));
  Combo.open(button, {
    plain: true, current: at.slug,
    groups: (q) => [{ items: items(pages.filter((page) => page.slug === INTRO || page.step !== null)) },
      { label: "More", items: items(pages.filter((page) => page.slug !== INTRO && page.step === null)) }]
      .map((group) => ({ ...group, items: group.items.filter((it) => it.name.toLowerCase().includes(q)) })),
    choose: (value) => turn(stepHref(item, { slug: value })),
  });
}

// The address names the page on screen: the one read last, which comes up when the address names none, is written into it,
// in place of the entry it is in. A reload, a link or a Back then shows the page the address says.
function nameThePage(item, pages) {
  const page = currentPage(item, pages);
  if (!step && page.slug !== INTRO && shown === item.id && !root.hidden) router.replace(stepHref(item, page));
}

// The guide, read once per version of its text, one page at a time: drawn again when the page or the text changes
async function renderGuide(item, el) {
  if (!step && recall(lastRead(item)) && guides[item.guide]) nameThePage(item, guidePages(guides[item.guide]));
  const key = `${item.guide}:${step || recall(lastRead(item)) || ""}`;
  if (el.dataset.shown === key) return;
  el.dataset.shown = key;
  let markdown = "";
  try {
    if (item.guide) markdown = guides[item.guide] ??= (await api(`/api/playgrounds/${item.id}/guide`)).markdown;
  } catch (err) {
    el.innerHTML = `<p class="err-text">${esc(err.message)}</p>`;
    return;
  }
  if (!markdown) {
    el.innerHTML = `<p class="sub">No guide</p>`;
    return;
  }
  const pages = guidePages(markdown), page = currentPage(item, pages), at = pages.indexOf(page);
  remember(lastRead(item), page.slug === INTRO ? null : page.slug);
  nameThePage(item, pages);
  const body = await markdownHtml(page.markdown, { accounts: item.accounts });
  if (el.dataset.shown !== key) return;   // another page was asked for meanwhile
  el.innerHTML = pages.length > 1 ? guideNav(item, pages, at) + `<div class="guide-page">${body}</div>` + guideFoot(item, pages, at)
    : body;
  // Another page, asked for by a click, opens at the top of the page, as any address does. Before a start, the
  // details above the guide would hide it: the guide comes up under the page's head, as far as the page scrolls. Once
  // it runs, the top of the page is where the progress of a job shows, with the guide under it: it stays there. Next to
  // the UIs, the guide scrolls by itself: its panel goes back to the top.
  const moved = el.dataset.page !== undefined && el.dataset.page !== page.slug;
  el.dataset.page = page.slug;
  if (moved && toGuide && el.closest(".pg-read")) {
    scrollBy(0, el.getBoundingClientRect().top - $(".page-head", root).getBoundingClientRect().bottom);
  }
  if (moved) el.closest(".pg-pane")?.scrollTo(0, 0);
  toGuide = false;
  Workspace.actions(el, item.name, living(item, store.state));   // its action links, enabled or not
}

// --- Requirements -----------------------------------------------------------------------

// What a playground of a catalog lacks of the catalogs, a line for each, in the words of the note on its row (ui.js catalogNeed): the
// catalog to add (at once when it is listed, else in the Add dialog on its second screen, with the address the playground gives), one that
// downloads, one that failed to (with why, and Try again), one that no longer holds what it needs, one that is too old (and the way to its
// row to update it). The button of each is `button`: `data-need` says what it does, which the page answers with the Catalogs page's own
// actions (dialogs/catalog.js).
function catalogLine(need) {
  const { line, button } = catalogNeed(need), row = (Array.isArray(store.state?.catalogs) ? store.state.catalogs : []).find((c) => c.id === need.id);
  const why = need.reason === "failed" ? [row?.state?.reason, row?.state?.next].filter(Boolean).join(" ") : "";
  let mend = "";
  if (button?.act === "update") mend = `<a class="btn" href="${esc(hrefOf({ kind: "catalog", id: need.id }))}">${esc(button.label)}</a>`;
  else if (button) mend = `<button type="button" class="btn" data-need="${esc(button.act)}" data-catalog="${esc(need.id || "")}">${esc(button.label)}</button>`;
  return { name: line, sub: why, button: mend };
}

// What a Lab says of a playground it does not read: it uses what a newer Lab reads, or it was written for an older one
const COMPAT = { new: ["Needs a newer Gravitee Lab", "Run make update, then restart the Lab."], old: ["Written for an older Gravitee Lab", "Ask its owners to update it."] };

// The checks in three groups: what needs you, what Start does for you, what is already fine.
// A row says what (name, linked to it when it is an item) and what happens to it (action); a problem is told in full
// under the name. One that settings answer keeps where they are (file); one of an image's version, the variable that
// holds it (variable), when the server names it; one of a remote place that a form answers here, what it lacks (fix). What it lacks of the
// catalogs and of this Lab comes first, and what it uses of this machine is one line among what Start does.
function prepGroups(item) {
  const attention = (item.needs_catalogs || []).map(catalogLine), auto = [], ready = [];
  if (COMPAT[item.compat]) attention.push({ name: COMPAT[item.compat][0], sub: COMPAT[item.compat][1] });
  if (usesMachine(item)) auto.push({ name: "Uses this machine", action: usesMachine(item) });
  for (const x of checks || []) {
    const problem = x.state === "warn";
    (x.state === "ok" ? ready : problem ? attention : auto).push({ name: x.name, action: problem ? "" : x.detail,
      sub: problem ? x.detail : "", reason: x.reason, help: x.help, target: x.target, file: x.file, variable: x.variable, plugin: x.plugin, fix: x.fix });
  }
  return { attention, auto, ready };
}

// What answers a line: the key of an add-on's settings, or the form that takes a new license, a button of its words
const settingsButton = (it) => (it.target?.kind === "addon"
  ? `<button class="icon-btn" data-settings="${esc(`${it.target.kind}:${it.target.id}`)}" data-tip="Settings" aria-label="Settings">${icon("key")}</button>`
  : `<button class="btn" ${licenseForm()}>Set license</button>`);

// The button of a line of a remote place that the person answers here, `fix` being what it lacks: a plus creates the hybrid
// gateway of the environment, a key gives it its Cloud Token (or replaces the one it has)
function fixButton(fix) {
  const place = chosenPlace(store.item("playground", shown));
  const [glyph, label] = fix === "gateway" ? ["plus", "Create hybrid gateway"]
    : fix === "token" ? ["key", `${place?.token?.state === "unset" ? "Add" : "Replace"} Cloud Token`] : [];
  return label ? `<button class="icon-btn" data-fix="${esc(fix)}" data-tip="${label}" aria-label="${label}">${icon(glyph)}</button>` : "";
}

// What to do about a line, as written in the playground's file: its `commands` as code, "the Images page" leading to
// it, at the row of the version the line is about when it names its variable. Escaped first: nothing of the file
// becomes markup but those two.
const helpHtml = (text, variable) => esc(text).replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/the Images page/g, `<a class="text-link" href="#/images${variable ? `/${esc(variable)}` : ""}">the Images page</a>`);

const prepItem = (it, cls) => `<li class="pg-prep-item ${cls}">
  <span class="pg-prep-icon">${PREP_ICON[cls]}</span>
  <span class="pg-prep-name">${it.target ? itemLink(it.target, it.name) : it.plugin ? pluginName(it.plugin) : esc(it.name)}</span>
  <span class="pg-prep-action">${esc(it.action)}${it.file ? settingsButton(it) : ""}${it.fix ? fixButton(it.fix) : ""}${it.button || ""}</span>
  ${it.sub || it.help ? `<div class="pg-prep-more">
    ${it.reason ? applyBadge(it.reason) : it.sub ? `<div class="sub">${esc(it.sub)}</div>` : ""}
    ${it.help ? `<div class="pg-prep-help">${helpHtml(it.help, it.variable)}</div>` : ""}</div>` : ""}</li>`;

// --- Details: what it is, what it takes, Start ------------------------------------------

// Where its seed put what it created, as a list of places: the place, what is there, and the button that deletes it from a
// remote one (This machine's goes with the playground's Stop). Nothing until a remote place holds a record.
function deployed(item) {
  const found = records(item);
  if (!found.some((r) => r.id !== MACHINE)) return "";
  const trash = (r) => {
    const why = unreachable(item, r);
    return actionButton({ key: keyOf(item), verb: "delete", icon: "trash", cls: "icon-btn",
      attrs: `data-delete-place="${esc(r.id)}" data-busy aria-label="Delete" data-tip="Delete" ${why ? `aria-disabled="true" data-why="${esc(why)}"` : ""}` });
  };
  return `<h3>Deployed to</h3><ul class="pg-list">${found.map((r) => `<li class="pg-place">
    <span class="pg-place-name" data-tip="${esc(r.name)}" data-tip-cut>${esc(r.name)}</span><span class="sub">${esc(r.what.join(", "))}</span>
    <span class="row-icons">${r.id === MACHINE ? GAP : trash(r)}</span></li>`).join("")}</ul>`;
}

function about(item) {
  const plugins = pluginNames(item);
  return `
    <div class="pg-details">
      <div class="card pg-main">
        <p class="pg-lead">${esc(item.description)}</p>
        <div class="pg-tags">${item.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>
        ${item.chapters.length ? `<h3>What you will explore</h3><ol class="pg-chapters">${item.chapters.map((c, i) => `<li><a class="text-link" href="#/playgrounds/${esc(item.id)}/${i + 1}">${esc(c)}</a></li>`).join("")}</ol>` : ""}
        <h3>What runs</h3>
        <dl class="pairs">
          <div><dt>Apps</dt><dd>${esc(item.apps.map((n) => store.service(n).label).join(", ") || "None")}</dd></div>
          ${item.setups.length ? `<div><dt>Setups</dt><dd>${linked(item.setups, "setup")}</dd></div>` : ""}
          <div><dt>Add-ons</dt><dd>${linked(item.addons, "addon") || "None"}</dd></div>
          ${originLink(item) ? `<div><dt>Catalog</dt><dd>${originLink(item)}</dd></div>` : ""}
          ${plugins ? `<div><dt>Plugins</dt><dd>${plugins}</dd></div>` : ""}
        </dl>${deployed(item)}
        <div class="pg-meta">
          ${item.duration ? `<span>About ${esc(item.duration)}</span>` : ""}
          ${item.source ? `<a class="text-link" href="${esc(item.source)}" target="_blank" rel="noopener">Source</a>` : ""}
        </div>
      </div>
      <aside class="card pg-prep" id="pg-prep"></aside>
    </div>`;
}

function prep(item) {
  const { attention, auto, ready } = prepGroups(item);
  if (item.apps.length) {
    auto.push({ name: "Its apps", action: item.apps.some((n) => store.service(n).built) ? "Built and started" : "Started" });
  }
  const group = (title, items, cls) => (items.length
    ? `<section class="pg-prep-group"><h4>${title}</h4><ul class="pg-prep-list">${items.map((x) => prepItem(x, cls)).join("")}</ul></section>` : "");
  return `<h2>Before you start</h2>
    <div>${checks ? group(ATTENTION, attention, "warn") + group("Done for you at start", auto, "auto")
      + group("Already in place", ready, "ok") : loading("Checking")}</div>
    ${startButton(item, false, "primary pg-start-btn")}
    ${attention.some((x) => x.file && !x.target) ? '<p class="pg-start-note muted">Starts without what needs the license</p>' : ""}
    ${!chosenPlace(item) && item.first_start && !item.created.length ? `<p class="pg-start-note">First start: about ${esc(item.first_start)}</p>` : ""}
    ${!chosenPlace(item) && item.note && !(checks && attention.length) ? `<p class="pg-start-note">${noteHtml(item)}</p>` : ""}`;
}

// The guide, to read before a first start that takes long: as it shows once the playground runs, with its accounts
function reading(item) {
  const accounts = accountsHtml(item);
  if (!item.guide && !accounts) return "";
  return `<div class="card pg-workspace pg-read ${accounts ? "" : "alone"}" data-id="${esc(item.id)}">
      <article class="guide" id="pg-guide">${loading("Loading")}</article>
      ${accounts ? `<aside class="pg-side">${accounts}</aside>` : ""}
    </div>`;
}

function details(item) {
  Workspace.leave(root);
  setHTML($("#pg-view", root), about(item) + reading(item));
  if ($("#pg-guide", root)) renderGuide(item, $("#pg-guide", root));
  setHTML($("#pg-prep", root), prep(item));
}

// --- Session: progress while a job runs on it, the guide and everything to open ---------

// Open, where it deploys (the picker, for a playground that has other places), then what can be done to the playground:
// Start (or Turn on) while it is on or up without running, Re-seed (Deploy, where the place holds nothing) while it is on
// and has a seed, Stop (or Turn off) while it is on or its apps run (after a stop that failed, or started outside the Lab
// UI). While a job runs on it, the button of that job stays, at work: the Stop for a start or a stop, the Re-seed or the
// Deploy for a seed, with the Stop after it waiting, so that it does not move. Open is its first console, when it lists
// one, whatever the page shows beside the guide.
function actions(item, job) {
  const running = job && !job.done ? job : null;
  const start = item.action.do === "start" && !job;   // after a failure, the progress card offers it
  const apart = item.action.do === "start" && !running;   // a Start is drawn too, here or under the failed job
  return `
    ${openButton(item.tabs.slice(0, 1), null, { named: true })}${picker(item)}
    ${start ? startButton(item, true) : ""}
    ${item.on && item.reseed && (!running || ["reseed", "deploy"].includes(running.verb)) ? seedButton(item) : ""}
    ${item.on || item.up || running ? stopButton(item, apart) : ""}`;
}

// The accounts its guide signs in with, as a section of the side; nothing without any. Each value says what it is: a
// user and its password, or a key alone (an API key).
function accountsHtml(item) {
  const accounts = item.accounts.map((a) => `<li class="pg-account">
    <span class="sub">${esc(a.for)}</span>${credHtml(a.username, "User")}${credHtml(a.password, "Password")}${credHtml(a.key, "Key")}</li>`).join("");
  return accounts ? `<section><h3>Accounts</h3><ul class="pg-list">${accounts}</ul></section>` : "";
}

// What the side holds: what to open, the accounts, what needs attention, everything it runs. Next to the UIs it is
// their last tab, Details.
function side(item) {
  return `
    <section id="pg-open"><h3>Open</h3><div class="links" id="pg-links"></div></section>
    ${accountsHtml(item)}
    <section id="pg-attention" hidden></section>
    <section class="pg-side-services"><h3>Services</h3><ul class="pg-list" id="pg-services"></ul></section>`;
}

// The guide is written once per playground and per version of its text: reading it, its scroll and a selection
// survive the refresh. It is the left panel of the split (workspace.js), which is as long as the playground is on; the
// UIs are frames, which a refresh must not draw again.
function workspace(item) {
  return `
    <div id="pg-progress"></div>
    ${Workspace.html(item, side(item))}`;
}

// The seconds of the running job and of its current phase
function tick() {
  for (const el of $$("[data-since]", root)) el.textContent = `${Math.round(Date.now() / 1000 - el.dataset.since)} s`;
}

// The phases the job went through; the last one is where it is, or where it failed or was canceled. What changes at
// every refresh while it runs (its last line of output, the seconds, the width of its bars) is written apart: the
// spinner keeps turning. A canceled job is said in a muted tone: nothing went wrong.
function progress(item, job) {
  const ended = job.done, failed = ended && job.error !== "Canceled";
  const seconds = (from, to) => (to ? `<span class="sub">${Math.round(to - from)} s</span>` : `<span class="sub" data-since="${esc(from)}"></span>`);
  const rows = job.phases.map((p, i) => {
    const cls = i < job.phases.length - 1 ? "done" : !ended ? "current" : failed ? "failed" : "canceled";
    return `<li class="pg-phase ${cls}">
      <span class="pg-phase-icon">${cls === "done" ? icon("check") : ended ? icon("close") : '<span class="spinner"></span>'}</span>
      <div><strong>${esc(p.name)}</strong>
        ${cls === "done" ? "" : `<div class="pg-phase-detail ${failed ? "err-text" : ""}"></div>`}</div>
      ${seconds(p.started, job.phases[i + 1]?.started || job.ended)}
      ${cls === "current" ? progressBar("phase") : ""}</li>`;
  }).join("");
  return `<div class="card pg-progress ${failed ? "failed" : ""}">
    <div class="card-head"><h2>${esc(jobTitle(job))}</h2>${seconds(job.started, job.ended)}
      ${ended ? "" : actionButton({ key: "job", verb: "cancel", label: "Cancel", attrs: 'data-act="cancel"' })}${progressBar("job")}</div>
    <ol class="pg-phases">${rows}</ol>
    ${ended ? `<div class="pg-progress-foot">
      <button class="btn" data-act="output">${icon("logs")}Show output</button>
      ${item.action.do === "start" ? startButton(item, item.on || item.up) : ""}</div>` : ""}
  </div>`;
}

function session(item, job, state) {
  const { attention } = prepGroups(item);
  setHTML($("#pg-view", root), workspace(item));
  Workspace.attach(root, item, { attention: attention.length ? ATTENTION : "" });
  renderGuide(item, $("#pg-guide", root));
  setHTML($("#pg-progress", root), job ? progress(item, job) : "");
  const said = $(".pg-phase-detail", root);
  if (said) said.textContent = (job.done ? job.error : job.detail) || "";
  tick();

  // Everything it runs: its apps and its seed, then what its add-ons bring. What to open is the server's list: its
  // own consoles, its add-ons', then those of the platform its guide opens
  const services = [...item.services, ...item.addons.flatMap((a) => store.item("addon", a.id).services)];
  // Everything it offers to open, in a browser tab of its own whatever shows in a tab here, as the Stack groups it: the
  // consoles of its own apps and add-ons and of the platform (the sections of its products), in the Stack's order, each gray
  // while its service does not run
  const listed = new Set([...state.platform.groups.flatMap((g) => g.services), ...item.links.map((l) => l.service)]);
  const open = state.links.filter((l) => listed.has(l.service)), platforms = platformLinks(item);
  $("#pg-open", root).hidden = !open.length && !platforms.length;
  paintLinks($("#pg-links", root), state, open, platforms);
  setHTML($("#pg-services", root), services.map((n) => `<li class="pg-service">${stateDot(state.services[n].state)}
    <span>${esc(state.services[n].label)}</span><span class="sub">${dockerDown() ? "" : esc(state.services[n].state.label)}</span></li>`).join(""));

  // Only what will not work: everything else went fine
  const section = $("#pg-attention", root);
  section.hidden = !attention.length;
  setHTML(section, `<h3>${ATTENTION}</h3><ul class="pg-list">${attention.map((x) => prepItem(x, "warn")).join("")}</ul>`);

  // Where its seed put what it created, once a remote place holds something: a section made then, so that a lab with no
  // platform has none
  const placed = deployed(item);
  let where = $("#pg-deployed", root);
  if (placed && !where) {
    where = Object.assign(document.createElement("section"), { id: "pg-deployed" });
    section.after(where);
  }
  if (where) {
    where.hidden = !placed;
    setHTML(where, placed);
  }
}

// --- Render -----------------------------------------------------------------------------

// Whether an address that names an item the state does not list may still be one: while the catalogs download, an item of a catalog may
// arrive; an item of this machine is listed at once, whatever the network, so it is not coming and the address is a dead one
export const maybeComing = (state, id) => state.content?.key === "downloading" && !id.startsWith("local/");

// An address that names a playground the state does not list: while the catalogs download it may be about to arrive, so the page stays and
// says it loads (the address is kept); once the download is over and it is still not there, the list is where the person goes
function missing(state) {
  if (!maybeComing(state, shown)) return router.replace("#/playgrounds");
  Workspace.leave(root);
  for (const id of ["#pg-title", "#pg-star", "#pg-actions"]) setHTML($(id, root), "");
  setHTML($("#pg-view", root), `<div class="empty">${loading("Loading")}</div>`);
}

function render(state) {
  const item = store.item("playground", shown);
  if (!item) return missing(state);
  // The checks are read when the page opens, then after each job: it may have changed what they say. And for the place
  // the picker is on, whichever place that turns out to be once the places are read.
  const ended = state.job?.done ? state.job.id : null;
  if (settled === undefined || (ended && ended !== settled)) {
    settled = ended;
    loadChecks();
  } else if (read !== chosenId(item)) {
    checks = null;
    loadChecks();
  }
  const job = shownJob(item, state), live = living(item, state);
  setHTML($("#pg-title", root), esc(item.name));
  setHTML($("#pg-star", root), starButton(item));
  // Its file is within reach in every state, as on its row in the list
  setHTML($("#pg-actions", root), `${live ? actions(item, job) : picker(item)}<span class="row-icons">${fileButtons(item, false)}${
    menuOf(item).length ? `<button class="icon-btn" data-more data-tip="More" aria-label="${esc(`More for ${item.name}`)}" aria-haspopup="true">${icon("more")}</button>` : ""}</span>`);
  if (live) session(item, job, state);
  else details(item);
  Workspace.actions($("#pg-guide", root), item.name, live);
  // Drawn again when the checks are read too, outside a refresh: its buttons take their look at once
  paintActions(root);
}

// --- Actions ----------------------------------------------------------------------------

// Two ways to stop (or to turn off, while it does not run), the second only when its seed recorded something: it
// deletes that from Gravitee too, through Gravitee's APIs, which must run for it: the server says when they do not.
// Where a remote place holds a record too, each place says what stays or goes, and the deletion waits for the first one
// that cannot be reached. What else it starts, stops, turns off, keeps or restarts is said first. The list of playgrounds
// stops one the same way.
// The choice of what a playground created in Gravitee when it stops (its own Stop, or the start of another that stops it): kept,
// or deleted from This machine and from every place that holds a record. `shown` is the dialog of the action, whose steps it
// shares between the two. Null when there is nothing to choose: nothing was created, or the seed cannot delete it.
function keepOrDelete(item, shown) {
  const found = records(item), remove = item.stop.delete, remote = found.some((r) => r.id !== MACHINE);
  if (!found.length || !remove) return null;
  const why = found.map((r) => (r.id === MACHINE ? !remove.enabled && remove.reason : unreachable(item, r))).find(Boolean) || "";
  const rows = (word, tone) => (remote ? found.map((r) => ({ label: word, items: [at(r)], tone })) : [{ label: word, items: found[0].what, tone }]);
  return { steps: planRows({ ...shown, keeps: (shown.keeps || []).filter((row) => row.kind !== "data") }),
    options: [{ value: false, label: "Keep what it created", rows: remote ? rows("Stays") : [] },
      { value: true, label: "Delete what it created", disabled: !!why, reason: why, rows: rows("Deletes", "danger") }] };
}

export async function stopPlayground(item) {
  const action = stopOf(item), label = action?.label || "Stop", plan = action?.dialog || item.stop;
  const choice = keepOrDelete(item, plan);
  const deletes = await effectsDialog({
    title: `${label} ${item.name}`, steps: choice?.steps || [], options: choice?.options || [{ value: false, label, rows: planRows(plan) }],
    action: (chosen) => (chosen ? `${label} and delete` : label),
    danger: (chosen) => chosen,
  });
  if (deletes === null) return;
  runJob(() => api(`/api/playgrounds/${item.id}/stop`, { remove: deletes, accept: accepted(plan) }),
    { key: keyOf(item), verb: "stop" });
}

// What a start asks first. On images it was not tested with, and not older than it asks (then its own plan switches them), the server
// offers the start that goes to those it was tested with (`offer`, its dialog): its steps, as when it must switch, with a second button
// that starts on the images as they are. When it stops a playground that created something in Gravitee, what to do with it, as that
// one's Stop asks. Resolves with {accept, remove, switch?} once confirmed, null when canceled, and undefined when there is nothing to
// ask beyond the plan's own dialog.
export async function confirmStart(item, label, dialog, { offer } = {}) {
  let shown = dialog, more = {};
  if (offer) {
    const switching = await effectsDialog({ title: `${label} ${item.name}`, options: [{ value: true, rows: planRows(offer) }],
      action: () => `Switch and ${label.toLowerCase()}`, danger: () => !!offer.goes_back?.length,
      other: { label: `${label} without switching`, value: false } });
    if (switching === null) return null;
    if (switching) [shown, more] = [offer, { switch: true }];
  }
  const leaving = (shown?.keeps || []).find((row) => row.kind === "data");
  const gone = leaving && store.item("playground", leaving.id);
  const choice = gone && keepOrDelete(gone, shown);
  if (!choice) return offer ? { accept: accepted(shown || {}), remove: false, ...more } : undefined;
  const deletes = await effectsDialog({ title: `${label} ${item.name}`, steps: choice.steps, options: choice.options,
    action: (chosen) => (chosen ? `${label} and delete` : label), danger: (chosen) => chosen });
  return deletes === null ? null : { accept: accepted(shown), remove: deletes, ...more };
}

// Re-seed, or Deploy: the same job on a remote place, which holds nothing yet for a Deploy
function seedPlayground(item, verb) {
  const place = chosenPlace(item);
  if (!place) return runJob(() => api(`/api/playgrounds/${item.id}/reseed`, {}), { key: keyOf(item), verb: "reseed" });
  return deployTo(item, place, { route: "reseed", verb, label: verb === "deploy" ? "Deploy" : "Re-seed" });
}

// What the seed created in one remote place goes, and its record with it; a failure keeps both
async function deletePlace(item, id) {
  const record = records(item).find((r) => r.id === id);
  if (!record) return;
  const rows = [{ label: "Deletes", items: [at(record)], tone: "danger" }];
  if ((await effectsDialog({ title: "Delete", options: [{ rows }], action: () => "Delete", danger: () => true })) === null) return;
  runJob(() => api(`/api/playgrounds/${item.id}/delete`, { place: id }), { key: keyOf(item), verb: "delete" });
}

// The form that answers a line of a remote place's checks, in the environment of the place; the checks and the places
// are read again once it is done
function answer(item, fix) {
  const place = chosenPlace(item);
  if (!place?.environment) return;   // a platform that cannot be used has none to answer for
  const form = { platform: place.platform, environmentId: place.environment.id, done: loadChecks,
    environments: store.places.filter((p) => p.platform.id === place.platform.id).map((p) => p.environment) };
  return fix === "gateway" ? openCreateGateway(form) : openCloudToken({ ...form, replacing: place.token?.state !== "unset" });
}

// The buttons of the page
const ACTIONS = {
  start: (item) => startPlayground(item, said),
  stop: stopPlayground,
  reseed: (item) => seedPlayground(item, "reseed"),
  deploy: (item) => seedPlayground(item, "deploy"),
  output: () => showOutput(store.state.job.id),
  cancel: () => cancelJob(store.state.job.id),
};

// --- Page -------------------------------------------------------------------------------

export const playgroundPage = {
  id: "playground",
  // The id of the first catalog's is bare, one of this machine's local/<name>, any other catalog's @<namespace>/<name>; a page of its guide
  // follows ("/4")
  pattern: new RegExp(`^#/playgrounds/(${ID_PATTERN})(?:/([a-z0-9-]+))?$`),
  nav: "playgrounds",
  title: ([id]) => store.item("playground", id)?.name || "Playgrounds",

  init(section) {
    root = section;
    root.innerHTML = `
      <div class="page-head">
        <div class="page-title pg-heading"><nav class="crumbs" aria-label="Breadcrumb"><a class="crumb" href="#/playgrounds">Playgrounds</a><span class="crumb-sep" aria-hidden="true">/</span></nav><h1 id="pg-title"></h1><span id="pg-star"></span></div>
        <div class="page-actions" id="pg-actions"></div>
      </div>
      <div id="pg-view"><div class="empty">${loading("Loading")}</div></div>`;
    root.addEventListener("click", (e) => {
      const on = (selector) => e.target.closest(selector), item = store.item("playground", shown);
      // The license, from its note or its line of the checks: the form that takes a new one
      if (on("[data-license]")) return License.show({ form: true });
      // A page of the guide, from it or from the details: turned in place, as the browser would for a link otherwise
      const page = item && on(`#pg-view a[href^="#/playgrounds/${item.id}/"]`);
      if (page && plainClick(e)) {
        e.preventDefault();
        return turn(page.getAttribute("href"));
      }
      // An action link of the guide asks its app to do it; a link under the address of one of its UIs loads there, one to
      // another page of the guide turns it in place. What the details list to open opens in a browser tab of its own
      const action = on("#pg-guide .guide-action[data-action]");
      if (action) return Workspace.send(action.dataset.action);
      const run = on("#pg-guide .guide-run");
      if (run) return Workspace.run(run);
      const link = on("#pg-guide a[href]");
      if (link && Workspace.follow(e, link)) return;
      const file = on("[data-file]");
      if (file) return openFile(file.dataset.file);
      const more = on("[data-more]");
      if (more) return openMenu(more, menuOf(item));
      // A catalog it needs: added (at once when it is listed), or downloaded again
      const needed = on("[data-need]");
      if (needed) {
        const found = (item?.needs_catalogs || []).find((one) => one.id === needed.dataset.catalog);
        return needed.dataset.need === "retry" ? tryAgain(needed.dataset.catalog) : found && addCatalog(found);
      }
      if (on("[data-star]")) return toggleStar(item);
      const pick = on("[data-steps]");
      if (pick) return choosePage(item, pick);
      const where = on("[data-place]");
      if (where) return choosePlace(item, where);
      const fix = on("[data-fix]");
      if (fix) return answer(item, fix.dataset.fix);
      const drop = on("[data-delete-place]");
      if (drop && !drop.disabled) return deletePlace(item, drop.dataset.deletePlace);
      // Its own settings, or those of an add-on that a line of the checks names
      const settings = on("[data-settings]");
      if (settings) return openSettings(store.item(...splitKey(settings.dataset.settings)));
      const button = on("[data-act]");
      if (button && !button.disabled) ACTIONS[button.dataset.act](item);
    });
    // What the host needs may be there since (an app started, a model pulled): the checks are read again when the
    // window comes back from another application. Not when it comes back from a frame of the page: it leaves for a UI,
    // and returns, at every click between the UIs and the guide.
    let away = false;
    addEventListener("blur", () => setTimeout(() => { away ||= !document.hasFocus(); }));
    addEventListener("focus", () => {
      if (away && !root.hidden && shown) loadChecks();
      away = false;
    });
  },

  show([id, page]) {
    if (id !== shown) checks = null;
    // Read again, what the checks say may have changed since the last visit; not for another page of the guide
    if (!(stepping && id === shown)) settled = undefined;
    stepping = false;
    shown = id;
    step = page || "";
    timer = setInterval(tick, 1000);
    clearInterval(refresher);
    refresher = setInterval(readPlaces, PLACES);
  },

  // Another page of the guide hides then shows the page at once; any other address leaves it
  hide() {
    clearInterval(timer);
    clearInterval(refresher);
    refresher = null;
    stepping = true;
    queueMicrotask(() => (stepping = false));
  },

  render,
};
