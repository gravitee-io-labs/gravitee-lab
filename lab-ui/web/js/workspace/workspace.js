// The page of a running playground: its guide, a divider, and a tab for each console it lists, in its order, and one for the
// details. The guide is on the left and the tabs on the right; too narrow for that, the same two panels are rows. A UI is a
// frame, made when its tab is first shown and kept: nothing a refresh draws, no other tab, page of the guide or job reloads
// it. One that cannot be framed, or does not answer, shows a notice in its place, and comes back by itself. An app in a frame
// may ask for an address to load in a tab, and the app of the first tab may say that it listens for the actions of the
// guide, which the page then sends it; it is told nothing else. The rules are in split.js, the divider is divider.js.

import { $, $$, esc, icon, loading, api, setHTML, recall, remember } from "../core.js";
import { unopened, stateDot, dockerDown, setOff } from "../components/ui.js";
import { Divider } from "../components/divider.js";
import { TermPane } from "./termpane.js";
import { fits, chooseTab, looksAt, linkTarget, opening, originOf, isReady, located, actionOf, actionMessage, actionReason, newPage,
  terminalReason } from "./split.js";

const DETAILS = "@details";    // the last tab, which is not a UI: its id is none a service has
const RUNS = "button.guide-run";       // the buttons markdown.js makes of the guide's blocks to run: they type at the shell's prompt
const ACTIONS = "button.guide-action[data-action]";   // the buttons markdown.js makes of a guide's action links: nothing else sends an action
const POLL = 5000;             // how often a tab that shows a notice looks again

let root = null;       // the page's section
let card = null;       // the card on screen; null when the page shows none
let item = null;       // the playground, as the last state says
let tabs = [];         // its UIs
let panels = new Map();   // what each tab holds, by tab
let shown = null;      // the tab on screen
let guide = null;      // the guide on screen, whose action links the page enables or not: {el, name, live}
let observed = false;

const tabKey = () => `tab:${item.id}`;
// What the page has to hold both panels: its container's width (the page's own depends on the layout it is in), less a scroll bar
const available = (page) => page.parentElement.clientWidth + innerWidth - document.documentElement.clientWidth;

// --- Layout -----------------------------------------------------------------------------

// The two panels are columns, or rows when the page has not the room for both side by side (the window less the menu decides):
// the same panels either way, only their place changes
function fit() {
  card.classList.toggle("rows", !fits(available(root)));
}

// The window changed, the menu was opened or closed: the card may be narrower than the guide the person gave it
function layout() {
  // The page is hidden while another is shown: nothing to measure, and nothing to move before it is back
  if (!card?.isConnected || root.hidden) return;
  fit();
  Divider.place();
  TermPane.place();
}

// --- Tabs -------------------------------------------------------------------------------

// What the buttons and the panels show: the tab on screen
function paint() {
  for (const button of $$('[role="tab"]', card)) {
    const on = button.dataset.tab === shown;
    button.setAttribute("aria-selected", on);
    button.tabIndex = on ? 0 : -1;
  }
  for (const [id, panel] of panels) panel.el.hidden = id !== shown;
  // What applies to a UI is offered once it shows: a notice carries its own button, and has nothing to go back to
  const panel = panels.get(shown), framed = shown !== DETAILS && panel.status === "framed";
  for (const tool of $$(".pg-tools > *", card)) tool.hidden = !framed;
  if (framed) linkOpen(panel);
  paintGuide();
}

// Open in a new tab opens the page the UI is on, when it said which, else the address it was last sent to
function linkOpen(panel) {
  if (panel.tab.id === shown) $("[data-open]", card).href = panel.current || panel.url;
}

// --- The actions of the guide -----------------------------------------------------------

// The UI an action goes to: the first tab, its first console
const target = () => (card ? panels.get(tabs[0]?.id) : null);

// Why an action of the guide cannot be sent now, "" when it can (split.js)
const blocked = () => actionReason({ live: guide.live, name: guide.name, tab: target()?.tab || null, status: target()?.status || "" },
  unopened);

// The action links of the guide as they can be used now: each enabled, or disabled with the reason, as a button that cannot
// work says why. `live`: the playground runs. Next to no UI (or before a start) nothing can be sent.
function paintGuide() {
  if (!guide?.el.isConnected) return;
  const reason = blocked();
  for (const button of $$(ACTIONS, guide.el)) setOff(button, !!reason, reason);
  const shut = runBlocked();
  for (const button of $$(RUNS, guide.el)) setOff(button, !!shut, shut);
}

// Why a block of the guide cannot be run at the shell now, "" when it can (split.js)
const runBlocked = () => terminalReason({ live: guide.live, name: guide.name, terminal: item?.terminal || null });

// What the app of the first tab has been sent, in the order it was asked: the actions waiting for it to say that it listens
function flush(panel) {
  const origin = originOf(panel.tab.url);
  for (const id of panel.queue.splice(0)) panel.frame.contentWindow.postMessage(actionMessage(id), origin);
}

// A tab on screen. `chosen`: by the person, who is then kept to it; its UI is looked at the first time, and again while
// it shows a notice.
function select(id, chosen = true) {
  shown = id;
  if (chosen) remember(tabKey(), id);
  paint();
  look(panels.get(id));
}

// Looks at the UI of a tab, if it is to be looked at (split.js)
function look(panel) {
  if (!panel.tab || !looksAt(panel)) return;
  panel.opened = true;
  check(panel);
}

// An address loaded in a frame. By default a new entry of the history, the UI's own: its Back (Gamma's "Back to logs") goes
// back to the page it was on, and never past it to the page that holds it. `replace`: in the place of the page the frame
// holds, which is gone (a UI that came back). The page it held does not say that it listens any more: the one that comes
// says it again (an address with a fragment may be the same page, which still does).
function load(panel, url, { replace = false } = {}) {
  const { frame } = panel;
  panel.current = null;   // the page that comes says where it is
  linkOpen(panel);
  if (newPage(url)) panel.ready = false;
  try {
    if (replace) frame.contentWindow.location.replace(url);
    else frame.contentWindow.location.href = url;
  } catch {
    frame.src = url;
  }
}

// An address loaded in a tab, which is shown. A UI that is not there yet starts at its start address (makeFrame) and goes
// on to this one once it has loaded.
function open(id, url) {
  const panel = panels.get(id);
  panel.url = url;
  if (panel.started && panel.status === "framed") load(panel, url);
  select(id);
}

// The tab's UI back at its start address, whatever it was sent to since
function home(panel) {
  panel.url = panel.tab.url;
  if (panel.started && panel.status === "framed") load(panel, panel.url);
  paint();
}

// The page of the UI loaded again, as a browser's Reload does: the page the person is on. The lab can reach it only when the UI is
// on its own port; a UI on another one does not let it read or reload its page, so it loads the address the UI said it is on
// (lab-ui/frames/location.js), and the one it loaded last when the UI says nothing.
function reload(panel) {
  if (!panel.started || panel.status !== "framed") return;
  panel.ready = false;   // the page that goes does not listen any more: the one that comes says it again
  try {
    panel.frame.contentWindow.location.reload();
  } catch {
    load(panel, panel.current || panel.url, { replace: true });
  }
}

// --- A UI -------------------------------------------------------------------------------

// What the notice in the place of a UI says. A UI that does not run says what its service is, as the details do: its
// dot, its name and its state. One that runs and does not answer says so. One that refuses says by what.
function noticeHtml({ tab, status, reason, url }) {
  if (status === "refused") {
    return `<p>${esc(tab.label)} cannot be shown here</p>
      <p class="sub">${esc(reason)}</p>
      <a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${icon("external")}Open in a new tab</a>`;
  }
  // What runs and does not answer warns: its dot says it as its word does
  const word = tab.up ? "Does not answer" : dockerDown() ? "" : tab.state.label;
  const dot = tab.up ? '<span class="dot-status warn"></span>' : stateDot(tab.state);
  return `<p class="pg-service" data-tip="${esc(reason)}">${dot}<span>${esc(tab.label)}</span><span class="sub">${esc(word)}</span></p>`;
}

function makeFrame(panel) {
  const el = Object.assign(document.createElement("iframe"), { title: panel.tab.label, src: panel.tab.url, allow: "clipboard-write" });
  const wait = Object.assign(document.createElement("div"), { className: "pg-wait", innerHTML: loading("Loading") });
  // A person on the keyboard who opened the tab keeps it: the UI may take the focus as it loads (Gamma does)
  const keys = card.contains(document.activeElement) && document.activeElement.matches('[role="tab"]:focus-visible');
  el.addEventListener("load", () => {
    wait.remove();
    // The first page of a UI is its start: what was asked for before it was there comes after it, an entry of its own, so
    // that a Back inside the UI goes to its start and not out of this page
    if (!panel.started) {
      panel.started = true;
      // Not in the load itself: a browser makes an address loaded before the load is over take the place of the page
      if (panel.url !== panel.tab.url) setTimeout(() => load(panel, panel.url), 0);
    }
    if (keys && document.activeElement === el) $(`[data-tab="${CSS.escape(panel.tab.id)}"]`, card)?.focus();
  });
  panel.el.prepend(wait);
  panel.el.insertBefore(el, panel.notice);
  return el;
}

// The notice, as the state says it now
function paintNotice(panel) {
  panel.notice.hidden = panel.status === "framed";
  setHTML(panel.notice, panel.status === "framed" ? "" : noticeHtml(panel));
}

// What the panel shows once it is known whether the UI answers and may be framed. A frame that was there stays, hidden
// while a notice shows, and loads its address again once the UI is back: the page it held is gone.
function draw(panel) {
  const framed = panel.status === "framed";
  if (framed && !panel.frame) panel.frame = makeFrame(panel);
  else if (framed && panel.fell) load(panel, panel.url, { replace: true });
  // What went with its page is gone: the app that comes back says again that it listens, and what waited for it does not
  if (!framed || panel.fell) Object.assign(panel, { ready: false, queue: [] });
  panel.fell = !framed && !!panel.frame;
  if (panel.frame) panel.frame.hidden = !framed;
  paintNotice(panel);
}

// Whether the UI runs, answers and may be framed: the state says whether it runs, the server what it answers. What
// shows a notice looks again by itself, as long as its tab is on screen.
async function check(panel) {
  const turn = ++panel.turn, { tab } = panel;
  clearTimeout(panel.timer);
  panel.timer = null;
  if (!panel.status) {   // the first look: nothing to say yet
    panel.notice.hidden = false;
    setHTML(panel.notice, loading("Loading"));
  }
  let status = "down", reason = tab.up ? "" : unopened({ label: tab.label });
  if (tab.up) {
    try {
      const found = await api(`/api/playgrounds/${item.id}/frame?url=${encodeURIComponent(tab.url)}`);
      status = !found.answers ? "down" : found.frameable ? "framed" : "refused";
      reason = found.reason;
    } catch (err) {
      reason = err.message;
    }
  }
  if (turn !== panel.turn || !panel.el.isConnected) return;
  panel.status = status;
  panel.reason = reason;
  draw(panel);
  if (shown === tab.id) paint();
  else paintGuide();
  again(panel);
}

function again(panel) {
  if (!panel.status || panel.timer || panel.status === "framed" || shown !== panel.tab.id) return;
  panel.timer = setTimeout(() => {
    panel.timer = null;
    if (panel.el.isConnected && shown === panel.tab.id && !root.hidden) check(panel);
  }, POLL);
}

function makePanel(tab) {
  const el = Object.assign(document.createElement("div"), { className: "pg-panel", hidden: true });
  const notice = Object.assign(document.createElement("div"), { className: "pg-notice", hidden: true });
  el.id = `pg-panel-${tab.id}`;
  el.setAttribute("role", "tabpanel");
  el.setAttribute("aria-labelledby", `pg-tab-${tab.id}`);
  el.append(notice);
  $(".pg-panels", card).insertBefore(el, panels.get(DETAILS).el);
  return { tab, el, notice, url: tab.url, frame: null, started: false, status: "", reason: "", opened: false, fell: false, turn: 0, timer: null, up: tab.up,
    ready: false, queue: [] };
}

// --- The state --------------------------------------------------------------------------

// The buttons of the tabs: the UIs, then the details, which carry a flag while something needs the person's attention
function barHtml(flag) {
  const alert = flag ? `<span class="tab-alert" role="img" data-tip="${esc(flag)}" aria-label="${esc(flag)}">${icon("alert")}</span>` : "";
  return [...tabs, { id: DETAILS, label: "Details" }].map(({ id, label }) =>
    `<button type="button" role="tab" id="pg-tab-${esc(id)}" data-tab="${esc(id)}" aria-controls="pg-panel-${esc(id)}" aria-selected="false" tabindex="-1"><span>${esc(label)}</span>${id === DETAILS ? alert : ""}</button>`).join("");
}

// The tabs as the state declares them, from one refresh to the next: a tab still declared keeps its frame
function sync(flag) {
  // A tab no longer declared, or declared at another address (a file edited while it runs), is another UI: made again
  for (const [id, panel] of [...panels]) {
    if (id !== DETAILS && !tabs.some((tab) => tab.id === id && tab.url === panel.tab.url)) {
      clearTimeout(panel.timer);
      panel.el.remove();
      panels.delete(id);
    }
  }
  for (const tab of tabs) {
    if (!panels.has(tab.id)) panels.set(tab.id, makePanel(tab));
    const panel = panels.get(tab.id);
    panel.tab = tab;
    // A UI that went down or came back is looked at once more, at once
    if (panel.opened && panel.up !== tab.up) check(panel);
    panel.up = tab.up;
    if (panel.status && panel.status !== "framed") paintNotice(panel);   // what its service is now
  }
  setHTML($(".segmented", card), barHtml(flag));
  // The tab to show: the one on screen, else the person's, kept for this playground, else the first console
  const list = [...tabs, { id: DETAILS }];
  const wanted = list.some((tab) => tab.id === shown) ? shown : chooseTab(list, recall(tabKey()));   // the card is new, or its UI is gone
  const panel = panels.get(wanted);
  if (wanted !== shown) {
    select(wanted, false);
  } else {
    paint();
    if (!panel.opened) look(panel);   // its UI was made again
  }
  if (panel.opened) again(panel);
}

// --- The page ---------------------------------------------------------------------------

export const Workspace = {
  // The card: the guide, the divider, then the tabs, the consoles it lists first and the details last. `side` is what the
  // details hold.
  html(next, side) {
    return `<div class="card pg-split" data-id="${esc(next.id)}">
      <div class="pg-pane"><article class="guide" id="pg-guide">${loading("Loading")}</article></div>
      <div class="pg-divider" role="separator" aria-orientation="vertical" aria-label="Resize the guide" data-tip="Resize the guide" tabindex="0"></div>
      <div class="pg-pane pg-uis">
        <div class="pg-bar">
          <div class="segmented" role="tablist" aria-label="UIs"></div>
          <span class="pg-tools">
            <button type="button" class="icon-btn" data-reload data-tip="Reload" aria-label="Reload">${icon("restart")}</button>
            <button type="button" class="icon-btn" data-home data-tip="Back to start" aria-label="Back to start">${icon("home")}</button>
            <a class="icon-btn" data-open target="_blank" rel="noopener" data-tip="Open in a new tab" aria-label="Open in a new tab">${icon("external")}</a>
          </span>
        </div>
        <div class="pg-panels">
          <div class="pg-panel pg-side" id="pg-panel-${DETAILS}" role="tabpanel" aria-labelledby="pg-tab-${DETAILS}" hidden>${side}</div>
        </div>
        ${next.terminal ? TermPane.html() : ""}
      </div></div>`;
  },

  // Once the card is written. The first time, what it holds is wired; drawn again as the same card, its tabs follow the
  // state. `attention` is what the details flag, if anything needs the person's attention. The page does not scroll: each
  // panel does (`fill`).
  attach(page, next, { attention }) {
    root = page;
    item = next;
    tabs = next.tabs;
    root.classList.add("fill");
    const el = $(".pg-split", root);
    if (el !== card) {
      for (const panel of panels.values()) clearTimeout(panel.timer);
      card = el;
      panels = new Map([[DETAILS, { el: $(".pg-side", card) }]]);
      shown = null;
      fit();
      Divider.attach(card);
      card.addEventListener("click", (e) => {
        const tab = e.target.closest('[role="tab"]');
        if (tab) select(tab.dataset.tab);
        else if (e.target.closest("[data-reload]")) reload(panels.get(shown));
        else if (e.target.closest("[data-home]")) home(panels.get(shown));
      });
    }
    sync(attention);
    if (next.terminal && $(".pg-term", card)) TermPane.attach(card, next);
    if (!observed) {
      observed = true;
      new ResizeObserver(layout).observe(root);
    }
  },

  // The page shows no card: nothing is kept, and the page scrolls
  leave(page) {
    page.classList.remove("fill");
    card = null;
    TermPane.leave();
  },

  // The guide on screen (`el`), whose action links are enabled while they can be sent; `live`: the playground runs
  actions(el, name, live) {
    guide = el ? { el, name, live } : null;
    paintGuide();
  },

  // An action link of the guide was clicked: the first tab is shown, and its app is sent the action, as soon as it has said
  // that it listens (`gravitee-lab:ready`), at once when it already did
  send(id) {
    const panel = target();
    if (!guide || !panel || typeof id !== "string" || !actionOf(`action:${id}`) || blocked()) return false;
    select(panel.tab.id);
    if (!panel.queue.includes(id)) panel.queue.push(id);   // one that waits is not asked twice
    if (panel.ready) flush(panel);
    return true;
  },

  // A run button of the guide was clicked: its block is typed at the shell's prompt, the terminal opened if it was folded
  run(button) {
    const code = button.closest(".guide-code")?.querySelector("code");
    if (!guide || !code || runBlocked()) return false;
    TermPane.run(code.textContent);
    return true;
  },

  // A click on a link of the guide: loaded in the tab of its UI, when it is under one. A key held or the middle button leaves it
  // to the browser, which opens a new tab. False for any other link.
  follow(e, anchor) {
    if (!card) return false;
    const hit = linkTarget(tabs, anchor.href, e);
    if (!hit) return false;
    e.preventDefault();
    open(hit.tab.id, hit.url);
    return true;
  },
};

// What an app inside a tab says to the page. It comes from the frame of a tab, and from the origin of its UI. It says that it
// listens for the actions of the guide (only the first tab's is ever sent any: what waited for it goes now), or asks for an
// address to load in a tab of this playground.
addEventListener("message", (e) => {
  const panel = card?.isConnected && [...panels.values()].find((it) => it.frame && it.frame.contentWindow === e.source);
  if (!panel) return;
  if (isReady(panel.tab, e.origin, e.data)) {
    panel.ready = true;
    flush(panel);
    return;
  }
  const where = located(panel.tab, e.origin, e.data);
  if (where) {
    panel.current = where;
    linkOpen(panel);
    return;
  }
  if (root.hidden) return;
  const hit = opening(tabs, e.origin, e.data);
  if (hit) open(hit.tab.id, hit.url);
});
