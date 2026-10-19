// The divider between the guide and the UIs of a running playground: dragged with the mouse or a finger, moved with the
// arrows, put back by a double click. The width of the guide is kept in this browser; what is drawn stays within the
// limits of the card, which may shrink under it. The rules are in split.js.

import { $, recall, remember } from "../core.js";
import { limits, guideWidth, nudge, edge } from "../workspace/split.js";

const KEPT = "split:guide";   // the width of the guide's panel the person chose: one for every playground

let card = null;     // the card with the divider; null before the first
let wanted = null;   // the width the person gave; what is drawn stays within the limits

const now = () => guideWidth(wanted, card.clientWidth);

// The width drawn and said to assistive technology; the cursor of each end shows which way the divider can go
function place() {
  const { min, max } = limits(card.clientWidth), at = now(), bar = $(".pg-divider", card);
  card.style.setProperty("--guide-w", `${at}px`);
  for (const [name, value] of [["min", min], ["max", max], ["now", at], ["text", `${at} px`]]) {
    bar.setAttribute(`aria-value${name}`, value);
  }
  bar.dataset.edge = edge(at, card.clientWidth);
}

// The width the person gave, kept in this browser; null puts it back to its default
function resize(to) {
  wanted = to;
  remember(KEPT, to === null ? null : String(to));
  place();
}

function wire() {
  const bar = $(".pg-divider", card);
  let drag = null;   // where the pointer went down, and how wide the guide was
  const end = () => {
    if (!drag) return;
    drag = null;
    card.classList.remove("dragging");
    resize(wanted);
  };
  bar.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    bar.setPointerCapture(e.pointerId);
    bar.focus();   // pressed, it has the keyboard: the arrows work next
    drag = { x: e.clientX, from: now() };
    card.classList.add("dragging");   // the frames let the pointer through: it stays with the divider
    e.preventDefault();
  });
  bar.addEventListener("pointermove", (e) => {
    if (!drag) return;
    wanted = guideWidth(drag.from + e.clientX - drag.x, card.clientWidth);
    place();
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) bar.addEventListener(event, end);
  bar.addEventListener("keydown", (e) => {
    const to = nudge(e.key, e.shiftKey, now(), card.clientWidth);
    if (to === null) return;
    e.preventDefault();
    resize(to);
  });
  bar.addEventListener("dblclick", () => resize(null));
}

export const Divider = {
  // A card that has one: wired, at the width kept
  attach(next) {
    card = next;
    wanted = recall(KEPT);
    wire();
    place();
  },

  // Drawn again: the card may have shrunk or grown since
  place,
};
