// Jobs: starting one (an item's Start or Stop, the platform's Start and Apply, an apply of a draft), following its
// output, and the activity drawer that shows it; what a click waits for meanwhile

import { $, esc, setHTML, api, store, router } from "../core.js";
import { toast, confirmPlan, confirmEffects, planRows, actionButton, paintActions, flash, follow } from "../components/ui.js";
import { pullStatus, totals } from "./pulls.js";

let stream = null, tick = null, watched = null;   // the job in the drawer: its output stream, its timer, its id
let asking = false;   // an action is on its way: a click before the state follows is the same click
const SEEN = 600;     // a button stays on its working face at least this long: a quick action is still seen to work

// Compose progress ("Container gio-redis Started") becomes one row per image or container, at its latest state;
// every other line stays in the output
const JobView = (() => {
  const LINE = /^\s*(Image|Container|Network|Volume)\s+(\S+)\s+(.+?)\s*$/;
  // BuildKit output ("#12 [bakery-agent 3/4] RUN pip install ."): one row per service built. BuildKit's own steps
  // ("#1 [internal] load local bake definitions") name no service.
  const BUILD = /^#\d+ \[([a-z0-9._-]+) [^\]]+\]/;
  const BUILT = /^#\d+ naming to (?:docker\.io\/)?(\S+?)(?::[^\s/]+)? done/;
  const DONE = /^(Pulled|Built|Created|Recreated|Started|Healthy|Running|Stopped|Removed|Exited|Skipped)/i;
  const BAD = /(error|failed|unhealthy)/i;
  // A registry refusing a pull for want of a login: Compose then runs the image it has, or builds one, so the job
  // goes on. The row says which registry wants a login ("ghcr.io"), Docker Hub's when the image names none
  const REFUSED = /unauthorized|access denied|authentication required/i;
  const registry = (image) => {
    const [first, ...rest] = image.split("/");
    return rest.length && /[.:]/.test(first) && first !== "docker.io" ? first : "Docker Hub";
  };
  // An image is named after its service, behind the project's name ("gravitee-bakery-agent")
  const untagged = (image) => (image || "").replace(/^docker\.io\//, "").replace(/:[^\s/]+$/, "");
  const names = (image, service) => image === service || image.endsWith(`-${service}`) || image.endsWith(`/${service}`);
  let steps = new Map(), lines = [], pulls = null;   // pulls: how far the images are downloaded, as the server says

  const label = (kind, name) => {
    if (kind !== "Container") return name.replace(/^docker\.io\//, "");
    const svc = Object.entries(store.state?.services || {}).find(([, s]) => s.container === name);
    return svc ? `${svc[1].label} (${svc[0]})` : name;
  };

  function reset() {
    steps = new Map(); lines = []; pulls = null;
    render();
  }

  // How far each image is downloaded and how fast, the whole of it each time
  function pulled(view) {
    pulls = view;
    render();
  }

  function add(newLines) {
    for (const line of newLines) {
      const m = line.match(LINE), b = line.match(BUILD), built = line.match(BUILT);
      if (m) {
        const refused = m[1] === "Image" && REFUSED.test(m[3]);
        steps.set(`${m[1]}:${m[2]}`, { kind: m[1], name: label(m[1], m[2]), image: m[2], warn: refused,
                                       status: refused ? `Not logged in to ${registry(m[2])}` : m[3] });
      }
      if (b && !steps.get(`Build:${b[1]}`)?.status.startsWith("Built")) steps.set(`Build:${b[1]}`, { kind: "Build", name: b[1], status: "Building" });
      if (built) {
        // The services whose image it is: several can share one (every playground's seed builds the seeder), and the image
        // then says nothing of the service's name
        const builds = [...steps.keys()].filter((k) => k.startsWith("Build:"));
        const shared = builds.filter((k) => untagged(store.state?.services?.[k.slice(6)]?.image) === built[1]);
        // Otherwise the longest service it ends with is its own: "bakery-agent" rather than "agent"
        const [named] = builds.filter((k) => names(built[1].split("/").pop(), k.slice(6))).sort((a, z) => z.length - a.length);
        for (const key of shared.length ? shared : named ? [named] : []) steps.get(key).status = "Built";
      }
      // Build lines stay out of the output, except errors
      lines.push({ text: line, step: !!m || (/^#\d+ /.test(line) && !/error/i.test(line)) });
    }
    render();
  }

  function render() {
    const list = $("#job-steps");
    list.hidden = steps.size === 0;
    const groups = ["Build", "Image", "Network", "Volume", "Container"].map((kind) => [kind, [...steps.values()].filter((s) => s.kind === kind)]);
    const download = (s) => s.kind === "Image" && !s.warn && /^Pulling/.test(s.status)
      && pulls?.images.find((found) => found.image === s.image && found.state !== "pulled" && found.state !== "failed");
    // The rows are kept from one render to the next and only what changed in them is written: a row that is drawn again would
    // start its spinner over at every byte that arrives, and it would never turn
    const rows = list._rows || (list._rows = new Map());
    const wanted = [];
    for (const [kind, items] of groups) {
      if (!items.length) continue;
      const note = kind === "Image" && pulls && totals(pulls) ? `<span class="job-group-note">${esc(totals(pulls))}</span>` : "";
      wanted.push({ key: `group:${kind}`, cls: "job-group", html: `${kind}s${note}` });
      for (const s of items) {
        const cls = s.warn ? "warn" : BAD.test(s.status) ? "failed" : DONE.test(s.status) ? "ok" : "", going = download(s);
        wanted.push({ key: `step:${kind}:${s.name}`, cls: "job-step", state: cls, name: s.name,
          status: going ? pullStatus(s.name, going) : `<span class="job-step-status ${cls}">${esc(s.status)}</span>` });
      }
    }
    const keys = new Set(wanted.map((row) => row.key));
    for (const [key, li] of rows) if (!keys.has(key)) { li.remove(); rows.delete(key); }
    const piece = (html) => { const t = document.createElement("template"); t.innerHTML = html; return t.content.children[0]; };
    wanted.forEach((row, at) => {
      let li = rows.get(row.key);
      if (!li) {
        li = document.createElement("li");
        li.className = row.cls;
        li.innerHTML = row.state === undefined ? row.html : `<span class="job-state"></span><span class="job-step-name"></span>${row.status}`;
        rows.set(row.key, li);
        li._status = row.status;
      }
      if (row.state === undefined) setHTML(li, row.html);
      else {
        const [state, name, status] = li.children;
        const want = `job-state ${row.state}`.trim();
        if (state.className !== want) state.className = want;
        if (name.textContent !== row.name) name.textContent = row.name;
        if (li._status !== row.status) { status.replaceWith(piece(row.status)); li._status = row.status; }
      }
      if (list.children[at] !== li) list.insertBefore(li, list.children[at] || null);
    });
    const raw = $("#job-raw").checked;
    const shown = lines.filter((l) => raw || !l.step).map((l) => l.text);
    // The head holds "All lines" (something to show only with steps) and Cancel (only while the job runs)
    $("#job-raw").parentElement.hidden = steps.size === 0;
    $("#job-log-head").hidden = steps.size === 0 && $("#job-cancel").hidden;
    const out = $("#job-output");
    const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 24;
    out.hidden = shown.length === 0 && steps.size > 0;
    out.textContent = shown.join("\n");
    if (atBottom) out.scrollTop = out.scrollHeight;
  }

  return { reset, add, pulled, render };
})();

// The stream is over: the drawer keeps the output, with the state of the job
function settle(state) {
  stream.close(); stream = null;
  clearInterval(tick);
  $("#job-state").className = `job-state ${state}`;
  $("#job-cancel").hidden = $("#job-progress").hidden = true;
  JobView.render();
}

// What a click waits for, from the click on: its button says so (paintActions) until the function this answers is
// called, and at least SEEN after the click. Called with `ok`, the button then says for a moment how it went; without,
// it is back at rest and every page draws again: a button kept while it waited makes way for what the state offers.
// A key is held by the last click on it (`holders`): an earlier click on the same key and verb that ends first lets it go
// only if it still holds it, so that the click that waits for its own answer keeps its button at work.
const holders = new Map();   // key -> the token of the click that holds it
function pend(key, verb) {
  const since = Date.now(), token = {};
  holders.set(key, token);
  store.pending.set(key, verb);
  paintActions();
  return (ok) => setTimeout(() => {
    if (holders.get(key) === token) {
      holders.delete(key);
      store.pending.delete(key);
    }
    if (ok === undefined) store.notify();
    else flash(key, verb, ok);
  }, Math.max(0, since + SEEN - Date.now()));
}

// Runs an action: `ask` sends the request that answers with a job. A refusal of the server is said in a toast and in
// the error tone of the button; either way the state follows. `key` and `verb` name what the job acts on and what it
// does ({key: "addon:<id>", verb: "stop"}): its button says so from the click on, then the job's own `for` and `verb`
// keep it at work until the job ends and what it started or stopped got there, and its face says how it went.
export async function runJob(ask, { key, verb } = {}) {
  if (asking) return;
  asking = true;
  const release = key ? pend(key, verb) : null;
  let refused;
  try {
    const job = await ask();
    follow(job);
    watchJob(job);
  } catch (err) {
    toast(err.message, true);
    refused = true;
  }
  await store.refresh();
  asking = false;
  release?.(refused ? false : undefined);
}

// A request without a job (a save, a create, a delete): the button of `key` says `verb` until it answers, then for a
// moment how it went. `quiet`: a success says nothing, for a first step that another one follows (the check of a
// share, before its confirm). Answers the request's own promise, as it comes.
export function busy(key, verb, promise, { quiet = false } = {}) {
  const release = pend(key, verb);
  promise.then(() => release(quiet ? undefined : true), () => release(false));
  return promise;
}

// Starts or stops an item, by its primary action or the one given (the Turn off of its menu): Start, Stop, Turn on
// or Turn off. What else its plan starts, stops, turns off, keeps or restarts is said first; the server makes the plan
// again and refuses to stop or turn off anything that was not shown.
export async function runAction(x, action = x.action) {
  const accept = await confirmPlan(`${action.label} ${x.name}`, action.label, action.dialog);
  if (!accept) return;
  runJob(() => api(`/api/${x.kind}s/${x.id}/${action.do}`, { accept }), { key: `${x.kind}:${x.id}`, verb: action.do });
}

// The platform's actions that have a plan, by their verb: their button's word, the title of their dialog
const PLANNED = { start: "Start", apply: "Apply" };

// The platform's Start and Apply, wherever they are offered: the Stack's head, the banner, the welcome page, and the
// Apply of Images and of the editor while nothing is unsaved. What its plan does is said first when the server gives
// it a dialog ("Starts: …", "Restarts: …"): the Start's when it starts more than the core or makes anything again, the
// Apply's always. Then its job runs. Answers whether it went ahead.
export async function runPlatform(verb) {
  const label = PLANNED[verb], { dialog } = store.state.platform[verb];
  if (dialog && !(await confirmPlan(label, label, dialog))) return false;
  runJob(() => api(`/api/stack/${verb}`, {}), { key: "platform", verb });
  return true;
}

// What applying a draft makes again and starts again, asked of the server (POST /api/effects) before it is confirmed,
// as it would be once the draft is saved ({path, content}, {file, values}, {images}), whose changes come first
// (`changes`, rows). Asked on the button clicked, which says so meanwhile, on the platform's key. Resolves with
// {apply: true} once confirmed; with {apply: false, runs} without asking when it makes nothing again: nothing of the
// platform runs (applied on next start), or no running container changes; with null when canceled, or refused (said
// in a toast).
export async function confirmApply(title, body, changes = []) {
  let found;
  try {
    found = await busy("platform", "apply", api("/api/effects", body), { quiet: true });
  } catch (err) {
    toast(err.message, true);
    return null;
  }
  if (!found.runs || (!found.restarts.length && !found.starts.length)) return { apply: false, runs: found.runs };
  // The rows of a plan; a change Compose cannot read before it is saved names every running container it may make
  // again
  const rows = planRows(found).map((row) => (row.label === "Restarts" && !found.exact ? { ...row, label: "May restart" } : row));
  return (await confirmEffects(title, [...changes, ...rows])) ? { apply: true } : null;
}

// What a Save & apply that makes nothing again says it did
export const savedOnly = ({ runs }) => (runs ? "Saved: no running container changes" : "Saved: applied on next start");

// A job as the activity chip and the drawer name it: its title, and how it ended when it did not end well
export const jobTitle = (job) => `${job.title}${!job.done || job.ok ? "" : job.error === "Canceled" ? " canceled" : " failed"}`;

// Follows a job in the drawer, which opens by itself only when the job fails, unless the page on screen shows that
// failure itself: the page of the playground it ran on. A canceled job has nothing to show. An older job comes as
// {id}: its title and time end its output.
function watchJob(job, open = false) {
  const running = job.done === false;
  stream?.close();
  clearInterval(tick);
  watched = job.id;
  if (open) openDrawer();
  if (running) say(`${job.title}: running`);
  $("#job-title").textContent = job.title ? jobTitle(job) : "";
  $("#job-state").className = "job-state";
  $("#job-time").textContent = "";
  // Cancel and the bar under the head are the running job's: an older job shown meanwhile has neither
  $("#job-cancel").hidden = $("#job-progress").hidden = !running;
  JobView.reset();
  if (running) tick = setInterval(() => { $("#job-time").textContent = `${Math.round(Date.now() / 1000 - job.started)} s`; }, 500);
  stream = new EventSource(`/api/jobs/${job.id}`);
  stream.onmessage = (e) => JobView.add(JSON.parse(e.data));
  stream.addEventListener("pulls", (e) => JobView.pulled(JSON.parse(e.data)));
  stream.addEventListener("done", (e) => {
    const res = JSON.parse(e.data);
    settle(res.ok ? "ok" : res.error === "Canceled" ? "idle" : "failed");
    $("#job-title").textContent = jobTitle(res);
    say(res.ok ? `${res.title}: done` : `${jobTitle(res)}${res.error && res.error !== "Canceled" ? `: ${res.error}` : ""}`);
    $("#job-time").textContent = `${Math.round(res.ended - res.started)} s`;
    const { page, params } = router.current || {};
    const shown = page?.id === "playground" && res.for === `playground:${params[0]}`;
    if (!res.ok && res.error !== "Canceled" && !shown) openDrawer(false);
    store.refresh();
  });
  // The output is gone (e.g. the Lab UI restarted): stop waiting for it. The job is no longer the one
  // followed: when it still runs, the next state follows it again.
  stream.onerror = () => {
    if (!stream) return;
    settle("idle");
    watched = null;
    JobView.add(["Output no longer available"]);
  };
}

// Cancel, from the drawer or from the page that follows the job itself: a button of the key "job" says it meanwhile
export function cancelJob(jobId) {
  busy("job", "cancel", api(`/api/jobs/${jobId}/cancel`, {})).catch((err) => toast(err.message, true));
}

export function showOutput(jobId) {
  const job = store.state?.job;
  if (watched === jobId) openDrawer();
  else watchJob(job?.id === jobId ? job : { id: jobId }, true);
}

// What a job does, said to a screen reader as it starts and ends (a polite live region): the page says it on the button that started it
function say(words) {
  const said = $("#job-said");
  if (said) said.textContent = words;
}

// The drawer opens on its close button when a person opens it (the keyboard is in it, as in a dialog), and gives the keyboard back to
// what opened it as it closes; one that opens by itself (a job that failed) leaves the keyboard where it is
let opener = null;
function openDrawer(take = true) {
  const drawer = $("#drawer");
  if (take) opener = document.activeElement;
  drawer.hidden = false;
  if (take) $("#drawer-close").focus();
}
function closeDrawer() {
  const drawer = $("#drawer"), inside = drawer.contains(document.activeElement);
  drawer.hidden = true;
  if (inside && opener?.isConnected) opener.focus();
  opener = null;
}

function showIdle() {
  $("#job-title").textContent = "Activity";
  $("#job-state").className = "job-state idle";
  $("#job-time").textContent = "";
  JobView.reset();
  $("#job-output").textContent = "No activity";
  openDrawer();
}

export function initJobs() {
  // The drawer's Cancel is the button of an action: it says "Canceling" until the job answers
  $("#job-cancel").outerHTML = actionButton({ key: "job", verb: "cancel", label: "Cancel",
    attrs: 'id="job-cancel" hidden' });
  $("#activity").addEventListener("click", () => {
    const drawer = $("#drawer"), job = store.state?.job;
    if (!drawer.hidden) closeDrawer();
    else if (job) showOutput(job.id);
    else showIdle();
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  // Escape closes it too, once nothing above it takes the key: an open dialog, menu or picker closes first. Asked
  // before their own handlers hide them.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#drawer").hidden && !$("dialog[open], .menu-pop:not([hidden]), .combo-pop:not([hidden])")) closeDrawer();
  }, true);
  $("#job-raw").addEventListener("change", JobView.render);
  $("#job-cancel").addEventListener("click", () => cancelJob(watched));
  router.onLeave(() => { $("#drawer").hidden = true; });
  // A job that was not started here (another window, or before this page loaded) is followed too,
  // unless the drawer is open on another output
  store.subscribe((state) => {
    const job = state?.job;
    if (job && !job.done && job.id !== watched && $("#drawer").hidden) watchJob(job);
  });
}
