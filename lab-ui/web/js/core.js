// What every module builds on: DOM helpers, the API, the state of the lab, the drafts and the router

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ENTITIES[c]);
}

// A drawing, never a word: a screen reader skips it, the label of its button or the text beside it is what it hears
export function icon(name) {
  return `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

// Writes an element only when its content changed: the refresh every few seconds keeps what is under the mouse
// (hover, focus, an open menu) instead of replacing it
export function setHTML(el, html) {
  if (!el || el._html === html) return false;
  el.innerHTML = html;
  el._html = html;
  return true;
}

export function loading(text) {
  return `<span class="loading"><span class="spinner"></span>${esc(text)}</span>`;
}

// How long ago a date was, in one short unit: "3 d", "2 mo"; nothing without a date
export function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!iso || isNaN(s)) return "";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  if (s < 86400 * 60) return `${Math.round(s / 86400)} d`;
  if (s < 86400 * 730) return `${Math.round(s / (86400 * 30))} mo`;
  return `${Math.round(s / (86400 * 365))} y`;
}

// The tone of the platform's state, as the class of the sidebar's state chip; a state without one is muted. Its dot
// takes the state's key itself. Partly running runs: some of its containers were stopped on purpose.
const TONE = { running: "ok", partly: "ok", starting: "warn", stopping: "warn", issue: "err" };

// Where an item of the catalog is shown: its row in its list, opened; a playground's own page. A target without an id is
// the page of its kind ({kind: "platform"}: #/cloud, the page of the Gravitee Cloud accounts; {kind: "catalog"}: #/catalogs, the
// page of the catalogs, a row of which opens with its namespace).
const PAGE_OF = { platform: "cloud", catalog: "catalogs" };
export const hrefOf = ({ kind, id }) => `#/${PAGE_OF[kind] || `${kind}s`}${id ? `/${id}` : ""}`;

// The id of an item as an address writes it, for a pattern to take it whole: a bare id (the first catalog's), `local/<name>` (this
// machine's) or `@<namespace>/<name>` (any other catalog's). Every page builds its routes with it, none spells `local/`. It is the
// syntax of the id alone: that a namespace is one the Lab reserves, or an item exists, is the server's to say.
export const ID_PATTERN = "(?:local/|@[a-z][a-z0-9-]{0,22}[a-z0-9]/)?[a-z0-9-]+";

// A key of the page, "<kind>:<id>" ("playground:@acme-hub/bank", "catalog-op:acme-hub"), as its kind and its id: the first colon
// alone splits it, so that what follows is whole. A key without a colon is its kind alone.
export const splitKey = (key) => {
  const text = String(key ?? ""), at = text.indexOf(":");
  return at < 0 ? [text] : [text.slice(0, at), text.slice(at + 1)];
};

// What a tag ends with to name the system its image is built on (its copy in dashboard.py, which a test keeps equal)
export const VARIANTS = ["chainguard-fips", "chainguard", "debian", "noble", "jammy", "alpine"];
// A tag without the system it is built on, as a version is said: "4.13.0-alpha.4-debian" is 4.13.0-alpha.4
export const bareTag = (tag = "") => {
  const variant = VARIANTS.find((v) => tag.endsWith(`-${v}`));
  return variant ? tag.slice(0, -variant.length - 1) : tag;
};

// Versions newest first: by their numbers, a release before its pre-releases (4.12.0 before 4.12.0-alpha.1)
export function compareVersions(a, b) {
  const x = (a.match(/\d+/g) || []).map(Number), y = (b.match(/\d+/g) || []).map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (y[i] ?? -1) - (x[i] ?? -1);
    if (d) return d;
  }
  return (a.includes("-") ? 1 : 0) - (b.includes("-") ? 1 : 0);
}

// A script of a CDN, loaded once asked: a library the page needs only in some places. `integrity` is the hash of
// the file (sha384, "sha384-<base64>"): the browser runs the script only when the CDN serves that very file.
export function loadScript(src, integrity) {
  return new Promise((resolve, reject) => document.head.append(Object.assign(document.createElement("script"),
    { src, integrity, crossOrigin: "anonymous", onload: resolve, onerror: () => reject(new Error(`${src} did not load`)) })));
}

// A stylesheet of a CDN, loaded once asked, with the hash of its file as loadScript has it
export function loadStyle(href, integrity) {
  return new Promise((resolve, reject) => document.head.append(Object.assign(document.createElement("link"),
    { rel: "stylesheet", href, integrity, crossOrigin: "anonymous", onload: resolve, onerror: () => reject(new Error(`${href} did not load`)) })));
}

// What the browser keeps from one visit to the next; nothing when its storage is unavailable
export function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

// What was kept as an object (JSON); an empty one for anything else: nothing kept, text that is no JSON, a list or a
// bare value
export function recallObject(key) {
  try {
    const value = JSON.parse(recall(key));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

// null forgets it
export function remember(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* storage unavailable */ }
}

// The theme on screen: the one chosen with the button, else the system's
export const theme = () => document.documentElement.dataset.theme
  || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

// --- API ------------------------------------------------------------------------

// A refusal or a failure of the server is thrown with the server's own words, and its `code` (the page shows the line and
// branches on the code alone, `ticket_expired`, `held`, `bad_address*`); undefined when the answer has none
export async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Gravitee-Lab-UI": "1" },
    body: JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new Error("Lab UI offline");
  }
  const data = (await res.json().catch(() => ({}))) ?? {};
  if (!res.ok) {
    const refusal = new Error(data.error || `${res.status} ${res.statusText}`);
    if (typeof data.code === "string") refusal.code = data.code;
    throw refusal;
  }
  return data;
}

// --- State ----------------------------------------------------------------------

const subscribers = [];
let etag = "", asked = 0, answered = 0, placesAsked = 0, placesTimer = null, placesSought = false;
const PLACES_AGAIN = 2000;   // how soon the places are asked again while a platform is still being read

export const store = {
  state: null,      // the last /api/state body
  offline: false,   // the Lab UI did not answer the last refresh
  error: "",        // why the Lab UI failed the last refresh, in its words
  // What was clicked and is not answered yet, key → verb ("addon:<id>" → "stop"): a job asked for, until it is in
  // the state; a request without a job (a save, a delete), until it answers. Its button says so meanwhile.
  pending: new Map(),

  // Subscribers hear about it only when the state, or how the Lab UI answered, changed
  async refresh() {
    const turn = ++asked;
    let state = null, tag = "", offline = false, error = "";
    try {
      const res = await fetch("/api/state", { headers: etag ? { "If-None-Match": etag } : {} });
      if (res.status === 304) await res.text();   // empty, but left unread the browser lists the request as canceled
      else if (!res.ok) error = (await res.json().catch(() => ({}))).error || `${res.status} ${res.statusText}`;
      else {
        state = await res.json();
        tag = res.headers.get("ETag") || "";
      }
    } catch {
      offline = true;
    }
    // Two refreshes can overlap (the timer and the end of an action): the one asked last has the last word
    if (turn < answered) return;
    answered = turn;
    if (!state && offline === store.offline && error === store.error) return;
    if (state) { store.state = state; etag = tag; }
    // A page shown before the first state asked for the places with no platform: they are read once the state lists one
    if (state && store.platforms.length && !placesSought) store.refreshPlaces();
    store.offline = offline;
    store.error = error;
    store.notify();
  },

  // Everything draws again. Also called by a module that drew from something the state does not hold (the license,
  // a group that was expanded): the page and the shell follow.
  notify() {
    for (const fn of subscribers) {
      try { fn(store.state); } catch (err) { console.error(err); }
    }
  },

  // Why nothing can be started now, as the server says it and the tooltip of what waits; "" when it can. While the
  // Lab UI does not answer, what the page last heard is no longer true: everything waits for it.
  wait() { return store.offline ? "Lab UI offline" : store.error ? "Lab UI error" : store.state?.wait || ""; },

  // The lab as the health chip and the Stack say it: {tone, text, title, why}, the Lab UI, Docker and the core file first,
  // then the platform, named. "busy" pulses while the platform is on its way. `why`: the lines that say what the issue is
  // (what its groups say of themselves: "API Management: Gateway exited (1)"), for the chip of a platform with one.
  health() {
    if (store.offline) return { tone: "err", text: "Lab UI offline" };
    if (store.error) return { tone: "err", text: "Lab UI error", title: store.error };
    const { docker, platform, blind, wait } = store.state;
    if (docker.error) return { tone: "err", text: docker.title || "Docker is not reachable" };
    if (blind) return { tone: "err", text: wait };   // docker-compose.yml does not load: what runs is not known
    const { key, label } = platform.state, moving = key === "starting" || key === "stopping";
    const why = key === "issue" ? platform.groups.filter((g) => g.note && !g.note_apply && ["err", "warn"].includes(g.note_tone))
      .map((g) => `${g.name}: ${g.note}`) : [];
    return { tone: `${TONE[key] || ""}${moving ? " busy" : ""}`, text: `Platform ${label.toLowerCase()}`, why };
  },

  // How far something is, from 0 to 1; null when nothing measures it, or while the Lab UI does not answer.
  // `key`: "job" (the running job), "phase" (its running phase), or what starts and stops (unit): the server
  // measures it while it is on its way with the last job, as the job runs and, once it ended well, until it got where
  // the job took it
  progress(key) {
    if (store.offline) return null;
    const job = store.state?.job, running = job && !job.done ? job : null;
    const measured = key === "job" ? running : key === "phase" ? running?.phases.at(-1) : store.unit(key);
    return measured?.progress ?? null;
  },

  // What starts and stops, by its key, with its state and how far it is: the platform ("platform"), one of its groups
  // ("group:<id>"), an add-on, a setup or a playground ("addon:<id>"…), or a container ("service:<name>", its state
  // only); a catalog being downloaded ("catalog:<namespace>") and the content the lists wait for ("content"), for their
  // progress alone; undefined for any other key
  unit(key) {
    const [kind, id] = splitKey(key), platform = store.state?.platform;
    if (kind === "platform") return platform;
    if (kind === "group") return platform?.groups.find((g) => g.id === id);
    if (kind === "service") return store.service(id);
    // A catalog by its namespace, whose download has a progress; "content" is what the lists wait for while one is on its way
    if (kind === "catalog") return store.state?.catalogs?.find?.((c) => c.id === id);
    if (kind === "content") return store.state?.content;
    return ["addon", "setup", "playground"].includes(kind) ? store.item(kind, id) : undefined;
  },

  // Whether the server measures something on its way: a job runs, or what the last one started or stopped is still
  // going, its health checks running after Compose returned, or a catalog is downloading
  measuring() {
    const { job, platform, addons = [], setups = [], playgrounds = [], catalogs } = store.state || {};
    const units = platform ? [platform, ...platform.groups, ...addons, ...setups, ...playgrounds] : [];
    return (!!job && !job.done) || [...units, ...(Array.isArray(catalogs) ? catalogs : [])].some((unit) => typeof unit.progress === "number");
  },

  // What each Gravitee Cloud platform's last check answered (`checks`, by platform id) and when (`at`, in milliseconds): one record for the
  // Dashboard and the Gravitee Cloud page, so that what one read or changed is what the other says
  cloud: { checks: new Map(), at: new Map() },

  // The platforms registered with this lab, as the state lists them: none until one is added
  get platforms() { return store.state?.platforms || []; },

  // Every place a playground can be deployed to besides this machine, as GET /api/places says it (the environments of the
  // platforms, with what each lacks); [] until it is read, and for a lab with no platform, which never asks. A place that
  // changed redraws the pages. A platform the server has not read yet is one place that says "Loading": it is asked for
  // again shortly, until it has said. `placesRead`: the list was read once, with a platform registered; a place a page kept
  // is unknown before.
  places: [],
  placesRead: false,
  async refreshPlaces() {
    const turn = ++placesAsked;
    let found = [];
    placesSought ||= !!store.platforms.length;
    if (store.platforms.length) {
      try {
        const answer = await api("/api/places");
        found = Array.isArray(answer) ? answer : answer.places || [];
      } catch (err) {
        console.error(err);   // the list read last stands
        return;
      }
    }
    if (turn < placesAsked) return;
    if (store.platforms.length) store.placesRead = true;
    clearTimeout(placesTimer);
    placesTimer = found.some((p) => p.reason === "Loading") ? setTimeout(store.refreshPlaces, PLACES_AGAIN) : null;
    if (JSON.stringify(found) !== JSON.stringify(store.places)) {
      store.places = found;
      store.notify();
    }
  },

  subscribe(fn) { subscribers.push(fn); },
  item(kind, id) { return store.state?.[`${kind}s`]?.find((x) => x.id === id); },
  service(name) { return store.state?.services[name]; },
};

// Whether an add-on is the platform's own: Gravitee's, in the category that the core's `x-gravitee` names after itself, as against the
// ones of other makers (Kafka, Jade). Containers and Images list it with the platform and the others apart.
export const ofPlatform = (addon) => !!addon && addon.category === store.state?.platform?.name;

// The rows of the catalogs among the entries of `state.catalogs`: a repository, a folder, this machine's and a suggestion. Any other entry is
// no row. The progress of the fetch of an Add rides there as {id: "check", kind: "progress", progress} (`check` is a reserved namespace, so it
// is never a catalog) for the bar of the Add dialog (`store.unit("catalog:check")`) and for `measuring()`, and nothing else reads it: whoever
// lists the catalogs asks for the rows.
const ROW_KINDS = new Set(["git", "folder", "machine", "suggested"]);
export const catalogRows = (state = store.state) => (Array.isArray(state?.catalogs) ? state.catalogs : []).filter((row) => ROW_KINDS.has(row?.kind));

// --- Drafts ---------------------------------------------------------------------

const unsaved = [];

// Changes made here and not saved yet: the files of the editor, the versions of Images, the plugins of Plugins.
// Closing or reloading the window asks first while one holds changes; so does leaving the page (ui.js).
export const drafts = {
  // isDirty() says whether the draft holds changes, discard() drops them
  add(isDirty, discard) { unsaved.push({ isDirty, discard }); },

  // The drafts that hold changes
  dirty() { return unsaved.filter((draft) => draft.isDirty()); },
};

addEventListener("beforeunload", (e) => { if (drafts.dirty().length) e.preventDefault(); });

// --- Router ---------------------------------------------------------------------

const pages = [], guards = [], leaving = [], scrolls = new Map();
let shown = null;   // the hash of the page on screen
let at = 0;         // the place of its entry in the history: every entry shown carries its number

// Before the first state, no page is drawn hollow: one line says that the lab is read, or, when the Lab UI does not answer, why and
// how to start it, with a way to try again
function boot() {
  const waiting = !store.state, el = $("#boot");
  document.body.classList.toggle("booting", waiting);
  if (!el) return waiting;
  const failed = store.offline || store.error;
  setHTML(el, !waiting ? "" : !failed ? `<p class="empty">${loading("Reading the lab")}</p>`
    : `<div class="card boot-card"><strong>${esc(store.error || "The Lab UI does not answer")}</strong>
      <p class="muted">${store.error ? "The Lab UI answered with an error." : "Start it again with make ui, then try again."}</p>
      <button class="btn" data-boot-retry>Try again</button></div>`);
  return waiting;
}
document.addEventListener("click", (e) => { if (e.target.closest?.("[data-boot-retry]")) store.refresh(); });

// The visible page follows the state; its title too (a playground's name)
function draw() {
  const { page, params } = router.current || {};
  if (boot() || !page) return;
  const title = typeof page.title === "function" ? page.title(params) : page.title;
  document.title = `${title} - Gravitee Lab`;
  // A page that fails to draw is said in the console: the menu and the other pages still work
  try { page.render?.(store.state); } catch (err) { console.error(err); }
}

// A page that was named otherwise: its old address, and every address under it, leads to where it is now, in place of the entry
const MOVED = { "#/stack": "#/containers", "#/platforms": "#/cloud" };

function route() {
  const hash = location.hash;
  const old = Object.keys(MOVED).find((from) => hash === from || hash.startsWith(`${from}/`));
  if (old) return router.replace(MOVED[old] + hash.slice(old.length));
  let match = null;
  const page = pages.find((p) => (match = hash.match(p.pattern)));
  if (!page) return router.replace("#/dashboard");
  const place = history.state?.n ?? at + 1;   // an entry without a number is a new one: the next
  if (hash !== shown && shown !== null) {
    // Refused (unsaved changes): the history goes back to the page on screen, which is left as it is. A page that
    // was replaced is gone: there is nothing to go back to, so nothing is asked
    const steps = place - at;
    if (steps && guards.some((fn) => fn(hash, () => history.go(steps)) === false)) { history.go(-steps); return; }
    scrolls.set(shown, scrollY);
    // What is open over the page closes with it. A page that fails as it is left is said in the console: the next
    // one still shows
    $$("dialog[open]").forEach((d) => d.close());
    leaving.forEach((fn) => fn());
    try { router.current.page.hide?.(); } catch (err) { console.error(err); }
  }
  at = place;
  history.replaceState({ n: at }, "");
  if (hash === shown) return;
  shown = hash;
  router.current = { page, params: match.slice(1) };
  $$("main > .page").forEach((s) => (s.hidden = s.id !== `page-${page.id}`));
  $$(".nav-item").forEach((a) => {
    if (a.dataset.nav === page.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  try { page.show?.(router.current.params); } catch (err) { console.error(err); }
  draw();
  scrollTo(0, scrolls.get(hash) || 0);
  // A page chosen in the menu takes the keyboard: the next Tab is in the page, and a screen reader reads it from its title
  if (document.activeElement?.closest?.(".nav")) $("#content")?.focus({ preventScroll: true });
}

export const router = {
  current: null,   // {page, params} of the visible page

  // Pages are mounted once, in their section, and stay there: what they hold survives navigation
  register(page) {
    page.init?.($(`#page-${page.id}`));
    pages.push(page);
  },

  start() {
    history.scrollRestoration = "manual";   // the scroll of each page is restored here
    addEventListener("hashchange", route);
    store.subscribe(draw);
    route();
  },

  go(hash) { location.hash = hash; },

  // Another page takes the place of this entry of the history: its address leads nowhere
  replace(hash) {
    history.replaceState(history.state, "", hash);
    route();
  },

  // fn(nextHash, again) runs before a page is left, and returns false to refuse the navigation;
  // again() navigates there once more, the same way (a link, Back or Forward)
  guard(fn) { guards.push(fn); },

  // fn() runs once a page is left, as it hides: what was open over it (a menu, the picker, the drawer) closes
  onLeave(fn) { leaving.push(fn); },
};
