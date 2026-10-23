// The shell around the pages: health and activity chips, the banner, theme and collapse

import { $, esc, icon, setHTML, store, router, remember, recall } from "../core.js";
import { actionButton, paintActions, showing, licenseForm, behindWhy, tipHtml, noticeHtml } from "./ui.js";
import { runPlatform, jobTitle } from "../activity/jobs.js";
import { openFile } from "../dialogs/editor.js";
import { License } from "../dialogs/license.js";
import { offersStart } from "../pages/logs.js";

// The theme and the collapsed menu as they were left are set by the page before it draws (index.html): here, their
// buttons
export function initSidebar() {
  const root = document.documentElement, button = $("#theme");
  // The button shows the theme that applies: the system's (half a circle), dark (a moon) or light (a sun). A click
  // goes on to the next one, back to the system's after light: what follows the system again is no theme kept.
  const THEMES = { system: ["auto", "System theme", "dark"], dark: ["moon", "Dark theme", "light"],
    light: ["sun", "Light theme", "system"] };
  // Light or dark when one is chosen; any other value is no theme, the system's applies
  const chosen = () => (root.dataset.theme === "light" || root.dataset.theme === "dark" ? root.dataset.theme : "system");
  if (chosen() === "system") delete root.dataset.theme;
  const paintTheme = () => {
    const [glyph, label] = THEMES[chosen()];
    button.innerHTML = icon(glyph);
    button.dataset.tip = button.ariaLabel = label;
  };
  button.addEventListener("click", () => {
    const next = THEMES[chosen()][2];
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    remember("theme", next === "system" ? null : next);
    paintTheme();
  });
  paintTheme();

  // A narrow window always has the collapsed menu, without its toggle (the width of base.css and index.html); wider
  // again, the menu is as it was left
  const toggle = $("#sidebar-toggle"), narrow = matchMedia("(max-width: 1100px)");
  const collapse = (collapsed) => {
    document.body.classList.toggle("nav-collapsed", collapsed);
    toggle.dataset.tip = collapsed ? "Expand the menu" : "Collapse the menu";
    toggle.setAttribute("aria-label", toggle.dataset.tip);
  };
  collapse(document.body.classList.contains("nav-collapsed"));
  narrow.addEventListener("change", () => collapse(narrow.matches || recall("sidebar") === "collapsed"));
  toggle.addEventListener("click", () => {
    const collapsed = !document.body.classList.contains("nav-collapsed");
    collapse(collapsed);
    remember("sidebar", collapsed ? "collapsed" : "open");
  });

  // A file that could not be read opens in the editor; the license's line opens the form that takes a new one
  $("#banner").addEventListener("click", (e) => {
    const on = (selector) => e.target.closest(selector), file = on("[data-file]"), action = on("[data-platform]");
    if (file) openFile(file.dataset.file);
    else if (on("[data-license]")) License.show({ form: true });
    else if (action) runPlatform(action.dataset.platform);
  });
}

// The one place of the platform's state on every page: the state chip of the sidebar, which leads to Containers. Its words are its
// tooltip, which the collapsed menu needs; an issue's popover says what each group has. A screen reader hears it change.
function renderHealth() {
  const { tone, text, title, why = [] } = store.health(), health = $("#health");
  $("#health-state").className = `state ${tone}`;
  $("#health-text").textContent = text;
  health.dataset.tip = title ? `${text}: ${title}` : text;
  health.setAttribute("aria-label", text);   // its name, while the collapsed menu hides its words
  health.toggleAttribute("data-tip-collapsed", !title && !why.length);   // its words alone: a tooltip only while the menu shows icons
  if (why.length) health.dataset.tipCard = JSON.stringify({ title: text, rows: [{ lines: why.map((line) => [line]) }] });
  else delete health.dataset.tipCard;
  if ($("#health-said").textContent !== text) $("#health-said").textContent = text;
}

// A file that could not be read, and why; one the editor opens is a link to it
function problem({ file, message, editable }) {
  const name = editable ? `<button class="text-link" data-file="${esc(file)}">${esc(file)}</button>` : esc(file);
  return { tone: "err", title: `${name}: ${esc(message)}` };
}

// The platform's Start and Apply, on their notices: the one primary action of the lab where a page's head does not have it, as it is
// in the heads that have it. Each shows its action at work, then how it went.
const START = actionButton({ key: "platform", verb: "start", label: "Start", icon: "play", cls: "primary",
  attrs: 'data-busy data-platform="start"' });
const APPLY = actionButton({ key: "platform", verb: "apply", label: "Apply", icon: "apply", cls: "primary",
  attrs: 'data-busy data-platform="apply" data-tip="Recreate the containers that changed"' });

let applying = [];   // what the Apply notice named last: it names it alone while its button says how the apply went

// The pages whose head has the platform's Start and Apply: the Containers' and the Dashboard's, which draw one head; and those whose
// head has an Apply of their own, which is the platform's while nothing is chosen there (Images, whose rows say what differs; Plugins,
// whose head says it): the notice of an Apply is not drawn there, so that a page has one Apply
const PLATFORM_HEAD = ["stack", "dashboard"];

// The pages with a Start of their own that runs the platform: those, a playground's page, whose Start brings the
// platform with the playground, and Logs while nothing runs
function ownStart() {
  const id = router.current?.page.id;
  return PLATFORM_HEAD.includes(id) || id === "playground" || (id === "logs" && offersStart());
}

// The card of the notice's popover has no title: the summary beside it says it
const untitled = (card) => card && { ...card, title: "" };

// How many containers differ from what is saved, the summary of the Apply notice; its popover says which and why
// What the notices about Docker offer: the machine's checks, each with its fix
const MACHINE = '<button class="btn" data-machine>Check your machine</button>';

const behindSaid = (behind) => {
  const n = behind.reduce((sum, row) => sum + (row.count || 1), 0);
  return `${n} ${n === 1 ? "container differs" : "containers differ"} from what is saved`;
};

// What the banner says of the lab once Docker answers and the core loads, a notice for each fact that applies: the platform
// stopped, with its Start, but where a page has its own, and when a remote platform is registered and something else
// runs; the license when something is wrong with it, with the form that takes a new one; what runs behind what is
// saved, with Apply, but where the head has it; Docker's memory. The notice of a Start or an Apply stays while
// its button shows its action at work, and until it has said how it went (showing): once the platform no longer needs
// it, the notice names what it was about alone.
function lab({ docker, platform, license, addons, playgrounds }) {
  const notices = [], stopped = platform.state.key === "stopped", late = platform.apply.needed;
  // With a remote platform registered, the core stopped while something else runs is how the lab is used (a deploy
  // there runs none of the core), not where it begins; with API Management in the Cloud, Start all starts its gateway and the rest
  const apart = stopped && ((store.platforms.length > 0 && [...addons, ...playgrounds].some((x) => x.up))
    || (store.state.core?.place || "machine") !== "machine");
  if (((stopped && !apart) || showing("platform", "start")) && !ownStart()) {
    // Stopped is where the lab begins, not a problem: said calmly
    notices.push({ tone: "calm", icon: "info", title: `${esc(platform.name)}${stopped ? " is stopped" : ""}`, action: START });
  }
  if (license?.problem) {
    notices.push({ tone: "warn", title: esc(license.problem), action: `<button class="btn" ${licenseForm()}>Set license</button>` });
  }
  if (late) applying = platform.behind;
  else if (!showing("platform", "apply")) applying = [];
  if (applying.length && router.current?.page.id !== "stack") {
    // One summary, its popover a row for each product, add-on or playground with why; once applied, the names alone
    const summary = behindSaid(applying);
    const text = late ? tipHtml(`<span>${esc(summary)}</span>${icon("info")}`, untitled(behindWhy(platform)) || platform.behind.map((row) => row.reason), "", summary,
      { said: platform.behind.map((row) => row.reason).join(" ") })
      : esc(applying.map((row) => row.name).join(", "));
    notices.push({ tone: "warn", title: "Apply needed", text, action: APPLY });
  }
  if (docker.warning) notices.push({ tone: "warn", title: esc(docker.warning), action: MACHINE });
  return notices;
}

// What is wrong with the lab as a whole, the most blocking first, then every file that could not be read. A
// failure of the Lab UI itself comes first: the rest is what it said last. Docker not answering is said alone, and so
// is docker-compose.yml not loading, by its problem notice: nothing else is known.
function banner(state) {
  // Before the first state, the page says it in its place (core.js): the banner says nothing yet
  if (!state) return { html: "", calm: true };
  const notices = store.error ? [{ tone: "err", title: esc(store.error) }]
    : store.offline ? [{ tone: "err", title: "Lab UI offline", text: "What the page shows is what it last heard" }] : [];
  if (state) {
    if (state.docker.error) notices.push({ tone: "err", title: esc(state.docker.title || "Docker is not reachable"), text: esc(state.docker.error), action: MACHINE });
    else if (!state.blind) notices.push(...lab(state));
    notices.push(...state.problems.map(problem));
  }
  // Calm when it only says the platform is stopped
  return { html: notices.map(noticeHtml).join(""), calm: notices.every((one) => one.tone === "calm") };
}

// Called after the pages have drawn
export function renderShell() {
  const { state } = store;
  if (state || store.offline || store.error) renderHealth();
  const { html, calm } = banner(state), el = $("#banner");
  el.hidden = !html;
  el.classList.toggle("calm", calm);
  if (setHTML(el, html)) paintActions(el);
  if (!state) return;

  // While the Lab UI does not answer, what it said last of its job is no longer known to be true: the chip
  // says nothing of it. It shows while a job runs and once one failed or was canceled, which it says; a job that ended
  // well leaves nothing behind, and with nothing to say there is no chip.
  const job = store.offline ? null : state.job, busy = !!job && !job.done, canceled = job?.error === "Canceled";
  const activity = $("#activity");
  activity.hidden = !job || (job.done && job.ok);
  activity.className = "activity meter" + (!job ? "" : busy ? " running" : job.ok ? " ok" : canceled ? "" : " failed");
  $("#activity-text").textContent = activity.hidden ? "" : jobTitle(job);
  if (activity.hidden) {
    delete activity.dataset.tip;
    activity.removeAttribute("aria-label");
  } else {
    activity.dataset.tip = jobTitle(job);
    activity.setAttribute("aria-label", jobTitle(job));   // its name, while the collapsed menu hides its words
  }
}
