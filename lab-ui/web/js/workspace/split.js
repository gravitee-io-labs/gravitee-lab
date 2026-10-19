// The rules of the page of a running playground that shows its guide and its UIs, with no DOM: how wide the guide may be,
// when the two panels are rows instead of columns, which tab shows, when a UI is looked at, where a link or a message of an
// app is meant to load, and the two messages by which the guide's actions reach the app of the first console. The page that
// draws them is workspace.js.

const GUIDE_MIN = 600;     // the guide is never narrower, and starts at this width: the UIs have the rest
const FRAMES_MIN = 480;    // the UIs are not either: a large phone, the least a console can be used in
const DIVIDER = 8;
const BORDER = 2;          // the card's own, which the panels are inside of
const SPLIT_MIN = GUIDE_MIN + DIVIDER + FRAMES_MIN + BORDER;
const MARGINS = 24;        // what the page leaves of its container when it holds both panels: 12 px a side (base.css)
const STEP = 16;           // an arrow moves the divider by this, with Shift by BIG_STEP
const BIG_STEP = 64;
const OPEN = "gravitee-lab:open";   // what an app inside a tab posts to ask for an address to load there
const ACTION = "gravitee-lab:action";   // what the page posts to the app of the first console: run the action of the guide
const LOCATION = "gravitee-lab:location";   // what a UI posts, with its address, each time it is on another page (lab-ui/frames/location.js)
const READY = "gravitee-lab:ready";     // what that app posts once it listens for actions
const ACTION_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

// How wide the guide's panel may be in a card whose inside is `width` wide: from its minimum to what leaves the UIs theirs
export function limits(width) {
  return { min: GUIDE_MIN, max: Math.max(GUIDE_MIN, width - DIVIDER - FRAMES_MIN) };
}

// Whether both panels fit side by side with their minimums in the space the page has: `width` is its container's. Narrower, the
// same two panels are rows, the guide over the UIs.
export const fits = (width) => width - MARGINS >= SPLIT_MIN;

// The width of the guide's panel: the one asked for, or kept (a number, or the text the browser kept it as), brought
// within the limits of the container; its minimum when there is none
export function guideWidth(wanted, width) {
  const { min, max } = limits(width), asked = wanted === null || wanted === "" ? NaN : Number(wanted);
  return Math.min(max, Math.max(min, Number.isFinite(asked) ? Math.round(asked) : min));
}

// Which end of its travel the divider is at, for its cursor: it can only go the other way
export function edge(now, width) {
  const { min, max } = limits(width);
  return now <= min ? "min" : now >= max ? "max" : "";
}

// What a key does to the divider: the new width of the guide's panel, or null for a key that is not its own.
// Left and Right move it by a step, Shift by a bigger one; Home and End take it to its limits.
export function nudge(key, shift, current, width) {
  const { min, max } = limits(width), step = shift ? BIG_STEP : STEP;
  const to = { ArrowLeft: current - step, ArrowRight: current + step, Home: min, End: max }[key];
  return to === undefined ? null : Math.min(max, Math.max(min, to));
}

// The tab to show, among `tabs` ({id}, the first console first, the details last): the one kept for this playground when it
// is still there, else the first
export const chooseTab = (tabs, kept) => (tabs.find((tab) => tab.id === kept) || tabs[0]).id;

// Whether the UI of a tab is looked at (asked of the server, framed): the first time its tab is shown and again while it
// shows a notice
export const looksAt = (panel) => !(panel.opened && panel.status === "framed");

// The origin of a web address, null for what has none (`about:blank`, `javascript:`) or leads nowhere a tab can show
// (`blob:` and `data:` hold the origin of whoever made them)
export function originOf(url) {
  try {
    const { origin, protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:" ? origin : null;
  } catch {
    return null;
  }
}

// The tab whose UI an address belongs to: the first with the same origin
function tabOf(tabs, url) {
  const origin = originOf(url);
  return origin ? tabs.find((tab) => originOf(tab.url) === origin) || null : null;
}

// Whether a click is the plain one, which opens a link where it leads: a key held, or a button other than the first
// (the middle one), asks for the browser's own way
export const plainClick = (e) => e.button === 0 && !(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey);

// What a click on a link of the guide does: the tab it loads and the address, when it is the plain click and the
// address is under the UI of a tab; null leaves the link to the browser
export function linkTarget(tabs, href, e) {
  const tab = plainClick(e) ? tabOf(tabs, href) : null;
  return tab && { tab, url: new URL(href).href };
}

// What an app inside a tab asked for by posting {type: "gravitee-lab:open", url} to its parent: accepted only from the
// origin of the UI of a tab, for an address under the origin of the UI of one (any); answers the tab to load it in and
// the address, null for anything else
export function opening(tabs, origin, data) {
  if (!data || data.type !== OPEN || typeof data.url !== "string") return null;
  if (!originOf(origin) || !tabs.some((tab) => originOf(tab.url) === origin)) return null;
  const tab = tabOf(tabs, data.url);
  return tab && { tab, url: new URL(data.url).href };
}

// The address a UI says it is on, by posting {type: "gravitee-lab:location", url} to its parent: only from the origin of the UI of the
// tab (that it comes from the tab's frame is for the caller to check), for an address under that same origin. Answers the address,
// null for anything else.
export function located(tab, origin, data) {
  if (!tab || !data || data.type !== LOCATION || typeof data.url !== "string" || originOf(tab.url) !== origin) return null;
  return originOf(data.url) === origin ? new URL(data.url).href : null;
}

// --- The actions of a guide ---------------------------------------------------------------

// The id an action link of the guide names, `action:<id>` with an id of 1 to 64 lowercase letters, digits and hyphens; null for
// any other link or for an id that is none
export function actionOf(href) {
  const found = typeof href === "string" ? href.match(/^action:(.*)$/s) : null;
  return found && ACTION_ID.test(found[1]) ? found[1] : null;
}

// Whether an address loaded in a frame makes a new page, which says again that it listens: one with a fragment may only move
// within the page it is on
export const newPage = (url) => !String(url).includes("#");

// What the page posts to the app of the first console for an action of the guide
export const actionMessage = (id) => ({ type: ACTION, id });

// Whether what an app posted is its word that it listens for actions: the message, from the origin of the UI of that tab. That
// it comes from the tab's frame is for the caller to check.
export const isReady = (tab, origin, data) => !!tab && !!data && data.type === READY && originOf(tab.url) === origin
  && originOf(origin) !== null;

// Why the actions of the guide cannot be sent now, "" when they can. `live`: the playground runs; `tab`: its first console,
// null without one; `status`: what the page knows of that UI once it looked ("framed", "refused", "down", "" before). `down`
// says what a UI that does not run is.
export function actionReason({ live, name, tab, status }, down = (ui) => `${ui.label} is not running`) {
  if (!live) return `${name} is not running`;
  if (!tab) return `${name} shows no UI beside its guide`;
  if (!tab.up) return down(tab);
  if (status === "refused") return `${tab.label} cannot be shown here`;
  if (status === "down") return `${tab.label} does not answer`;
  return "";
}

// --- The terminal ---------------------------------------------------------------------------
// The shell of a playground sits under the UIs, in a panel of its own that the person makes taller or shorter, or folds away.
// `height` below is what the UIs and the terminal share: the right-hand panel less its bar of tabs.

const TERM_MIN = 120;      // the terminal is never shorter than this when it is open
const TERM_START = 240;    // and is this tall the first time
const UIS_MIN = 160;       // the UIs keep at least this
const TERM_STEP = 16;      // an arrow moves its divider by this, with Shift by TERM_BIG_STEP
const TERM_BIG_STEP = 64;

// How tall the terminal may be in a panel that has `height` to share: from its minimum to what leaves the UIs theirs
export function termLimits(height) {
  return { min: TERM_MIN, max: Math.max(TERM_MIN, height - UIS_MIN) };
}

// The height of the terminal: the one asked for, or kept (a number, or the text the browser kept it as), brought within the
// limits; its start height when there is none
export function termHeight(wanted, height) {
  const { min, max } = termLimits(height), asked = wanted === null || wanted === "" ? NaN : Number(wanted);
  return Math.min(max, Math.max(min, Number.isFinite(asked) ? Math.round(asked) : TERM_START));
}

// Which end of its travel the divider is at, for its cursor: it can only go the other way
export function termEdge(now, height) {
  const { min, max } = termLimits(height);
  return now <= min ? "min" : now >= max ? "max" : "";
}

// What a key does to the terminal's divider: the new height, or null for a key that is not its own. Up makes the terminal
// taller, Down shorter (the divider moves with the arrow); Home and End take it to its limits.
export function termNudge(key, shift, current, height) {
  const { min, max } = termLimits(height), step = shift ? TERM_BIG_STEP : TERM_STEP;
  const to = { ArrowUp: current + step, ArrowDown: current - step, Home: max, End: min }[key];
  return to === undefined ? null : Math.min(max, Math.max(min, to));
}

// Why the shell cannot be written to now, "" when it can. `live`: the playground runs; `terminal`: the state's, null for
// a playground that has none.
export function terminalReason({ live, name, terminal }) {
  if (!terminal) return `${name} has no terminal`;
  if (!live) return `${name} is not running`;
  if (!terminal.up) return `${terminal.label} is not running`;
  return "";
}

// What a block of the guide types into the shell: its lines, the last one ended too, so that it runs
export const commandOf = (text) => String(text).replace(/\s+$/, "") + "\n";

// The bytes the server sends of the shell's output, as it sends them (base64), as the text of bytes for the terminal
export function bytesOf(encoded) {
  const text = atob(encoded), bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes;
}
