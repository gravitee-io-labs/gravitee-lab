// The terminal's panel under the UIs of a running playground: a divider that makes it taller or shorter (dragged, moved with the
// arrows up and down, put back by a double click), a bar with a button that folds it away, and the shell (terminal.js) in what
// is left. Its height and whether it is folded are kept in this browser, one for every playground. The rules are in split.js.

import { $, esc, icon, recall, remember } from "../core.js";
import { termHeight, termLimits, termEdge, termNudge } from "./split.js";
import { Shell } from "./terminal.js";

const KEPT = "term:height";    // the height the person chose
const FOLDED = "term:folded";  // "1" while the person has it folded

let card = null;      // the card the panel is in; null before the first
let pane = null;      // the panel
let shell = null;     // the shell on screen
let wanted = null;    // the height the person gave; what is drawn stays within the limits
let item = null;      // the playground, as the last state says

// What the UIs and the terminal share: the right-hand panel less its bar of tabs
const room = () => $(".pg-uis", card).clientHeight - $(".pg-bar", card).offsetHeight;
const folded = () => pane.dataset.folded === "true";
const now = () => termHeight(wanted, room());

export const TermPane = {
  // The panel's markup, for the card
  html() {
    return `<section class="pg-term" aria-label="Terminal">
      <div class="pg-term-divider" role="separator" aria-orientation="horizontal" aria-label="Resize the terminal" data-tip="Resize the terminal" tabindex="0"></div>
      <div class="pg-term-bar">
        <span class="pg-term-title">${icon("terminal")}<span>Terminal</span><span class="sub" data-term-name></span></span>
        <button type="button" class="icon-btn" data-term-fold aria-expanded="true" data-tip="Fold the terminal" aria-label="Fold the terminal">${icon("chevron")}</button>
      </div>
      <div class="pg-term-body"><div class="pg-term-screen"></div><div class="pg-notice pg-term-notice" hidden></div></div>
    </section>`;
  },

  // A card that has one: wired, at the height and the fold kept
  attach(next, playground) {
    const first = card !== next;
    card = next;
    pane = $(".pg-term", card);
    item = playground;
    if (first) {
      shell?.dispose();
      shell = null;
      wanted = recall(KEPT);
      pane.dataset.folded = recall(FOLDED) === "1" ? "true" : "false";
      wire();
    }
    $("[data-term-name]", pane).textContent = playground.terminal.label;
    this.place();
    this.paint();
  },

  // Drawn again: the card may have shrunk or grown since
  place() {
    if (!pane?.isConnected) return;
    const bar = $(".pg-term-divider", pane), at = now(), { min, max } = termLimits(room());
    pane.style.setProperty("--term-h", `${at}px`);
    for (const [name, value] of [["min", min], ["max", max], ["now", at], ["text", `${at} px`]]) bar.setAttribute(`aria-value${name}`, value);
    bar.dataset.edge = termEdge(at, room());
    shell?.refit();
  },

  // What the panel shows now: folded or not, and the shell, or why there is none. The shell is made once the panel is open and
  // its container runs, and stays while it is.
  paint() {
    if (!pane?.isConnected) return;
    const off = !item.terminal.up, shut = folded();
    const button = $("[data-term-fold]", pane);
    pane.classList.toggle("folded", shut);
    button.setAttribute("aria-expanded", String(!shut));
    const label = shut ? "Open the terminal" : "Fold the terminal";
    button.dataset.tip = label;
    button.setAttribute("aria-label", label);
    button.classList.toggle("up", shut);
    const notice = $(".pg-term-notice", pane);
    notice.hidden = !off;
    notice.innerHTML = off ? `<p>${esc(item.terminal.label)} is not running</p>` : "";
    if (off) {
      shell?.dispose();
      shell = null;
    } else if (!shut) {
      shell ||= new Shell($(".pg-term-screen", pane), item.id, (text) => {
        notice.hidden = !text;
        notice.innerHTML = text ? `<p>${esc(text)}</p>` : "";
      });
      shell.connect().then(() => shell?.refit());
    }
  },

  // Opens the panel if it is folded, and runs the command at the shell's prompt
  async run(text) {
    if (folded()) fold(false);
    shell ||= new Shell($(".pg-term-screen", pane), item.id, () => {});
    await shell.run(text);
  },

  // The page shows no card: the shell on screen is let go, the shell of the container goes on
  leave() {
    shell?.dispose();
    shell = null;
    card = pane = null;
  },
};

function fold(shut) {
  pane.dataset.folded = String(shut);
  remember(FOLDED, shut ? "1" : null);
  TermPane.place();
  TermPane.paint();
  if (!shut) shell?.focus();
}

// The height the person gave, kept in this browser; null puts it back to its default
function resize(to) {
  wanted = to;
  remember(KEPT, to === null ? null : String(to));
  TermPane.place();
}

function wire() {
  const bar = $(".pg-term-divider", pane);
  let drag = null;   // where the pointer went down, and how tall the terminal was
  const end = () => {
    if (!drag) return;
    drag = null;
    card.classList.remove("dragging-term");
    resize(wanted);
  };
  bar.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || folded()) return;
    bar.setPointerCapture(e.pointerId);
    bar.focus();
    drag = { y: e.clientY, from: now() };
    card.classList.add("dragging-term");   // the frames let the pointer through: it stays with the divider
    e.preventDefault();
  });
  bar.addEventListener("pointermove", (e) => {
    if (!drag) return;
    wanted = termHeight(drag.from - (e.clientY - drag.y), room());   // dragged up, it grows
    TermPane.place();
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) bar.addEventListener(event, end);
  bar.addEventListener("keydown", (e) => {
    const to = folded() ? null : termNudge(e.key, e.shiftKey, now(), room());
    if (to === null) return;
    e.preventDefault();
    resize(to);
  });
  bar.addEventListener("dblclick", () => resize(null));
  pane.addEventListener("click", (e) => {
    if (e.target.closest("[data-term-fold]")) fold(!folded());
  });
}

