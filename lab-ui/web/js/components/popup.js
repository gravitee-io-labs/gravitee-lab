// Where a list that opens from a control goes, and how big it is: the one rule of every dropdown of the Lab UI, the picker
// (combo.js, a form's select too) and the menu (ui.js). `place` decides from sizes alone, so that it is tested without a
// browser (tests/web/js/popup.test.mjs); `attach` applies it to an element and keeps it so while it is open.
//
// The list opens below its control when it fits there whole, else above when it fits there, else on the side with more room,
// and takes nearly all of it: as high as its content, at most the room of that side less MARGIN from the window's edge. A side
// with less than MIN is not used while the other has more; when neither has MIN (a window a few rows high), the list lies over
// its control within the window. It is as wide as its control at least, as wide as its longest line when that is wider, up to
// MAX_WIDTH, and never wider than the window less its margins. Narrower windows than SHEET have it as a sheet along the bottom.

const MARGIN = 16;      // between the list and an edge of the window
const GAP = 6;          // between the list and its control
const MIN = 240;        // the least room a side is used with
const MAX_WIDTH = 640;  // the widest a list grows to fit its lines
const SHEET = 480;      // at this width of window and narrower, a sheet along the bottom

/* trigger: {top, bottom, left, right} of the control, in the window. size: {width, height} of the list with nothing cut, its
   natural size. win: {width, height} of the window. align: "start" (its left edge on the control's, a picker) or "end" (its
   right edge on the control's, a menu). minWidth: the least width of its kind. side: the side it is on, kept while its content
   still fits there (a search that narrows the list does not move it). Answers {sheet, side, top, bottom, left, width, maxHeight,
   tight}, `tight` being whether the lines are wider than the room the list can have: they are then drawn in two lines each:
   `top` for a list below its control or over it, `bottom` (from the window's bottom edge) for one above, so that it grows away
   from its control; the other is null. */
function place(trigger, size, win, { align = "start", minWidth = 0, side: kept = null } = {}) {
  const natural = Math.min(Math.max(size.width, minWidth), MAX_WIDTH);
  const width = Math.max(0, Math.min(Math.max(trigger.right - trigger.left, natural), win.width - 2 * MARGIN));
  const tight = size.width > Math.min(MAX_WIDTH, win.width <= SHEET ? win.width : win.width - 2 * MARGIN);
  if (win.width <= SHEET) {
    return { tight, sheet: true, side: "sheet", top: null, bottom: 0, left: 0, width: win.width,
      maxHeight: Math.max(0, Math.min(size.height, win.height - MARGIN)) };
  }
  const room = { below: win.height - MARGIN - (trigger.bottom + GAP), above: trigger.top - GAP - MARGIN };
  const fits = (side) => size.height <= room[side];
  const more = room.below >= room.above ? "below" : "above";
  const side = kept && kept !== "over" && fits(kept) ? kept : fits("below") ? "below" : fits("above") ? "above"
    : room[more] >= MIN ? more : "over";
  const start = align === "end" ? trigger.right - width : trigger.left;
  const left = Math.max(MARGIN, Math.min(start, win.width - MARGIN - width));
  if (side === "over") {
    // Neither side has room enough: over the control, within the window
    const maxHeight = Math.max(0, Math.min(size.height, win.height - 2 * MARGIN));
    const top = Math.max(MARGIN, Math.min(trigger.bottom + GAP, win.height - MARGIN - maxHeight));
    return { tight, sheet: false, side, top, bottom: null, left, width, maxHeight };
  }
  const maxHeight = Math.min(size.height, room[side]);
  return side === "below"
    ? { tight, sheet: false, side, top: trigger.bottom + GAP, bottom: null, left, width, maxHeight }
    : { tight, sheet: false, side, top: null, bottom: win.height - (trigger.top - GAP), left, width, maxHeight };
}

// The window the list is placed in: the part of it that is seen, which a phone's keyboard shortens (not a zoom)
function seenWindow() {
  const seen = window.visualViewport, height = window.innerHeight;
  return { width: document.documentElement.clientWidth || window.innerWidth,
    height: seen && Math.abs(seen.scale - 1) < 0.01 ? Math.min(height, seen.offsetTop + seen.height) : height };
}

// How far a line of the list is cut, in px: the most of what its names and tails (they end in an ellipsis) have more than room
const CLIPPED = ".name, .age, .menu-item";
function clipped(pop) {
  let most = 0;
  for (const line of [...pop.querySelectorAll(CLIPPED)].slice(0, 400)) most = Math.max(most, line.scrollWidth - line.clientWidth);
  return most;
}

/* Places `pop` (a list of position: fixed) at its control, `trigger`, and keeps it there while it is open: on a resize of the window,
   a scroll of the page or of what holds the control, and a change of what it holds (a list that loads, a search that narrows it).
   It is put in the dialog its control is in, if any (a modal dialog makes the rest of the page inert), else in the body. `away()`
   runs when the control is no longer in the window (drawn again, or scrolled out of it). `scroller` is what scrolls in it when it
   is cut (the picker's list; the menu itself): its natural height is read from it, so that a placing never moves what was
   scrolled to. Answers {update(), stop()}. */
function attach(pop, trigger, { align = "start", minWidth = 0, scroller = pop, away = () => {} } = {}) {
  const host = trigger.closest("dialog[open]") || document.body;
  if (pop.parentElement !== host) host.append(pop);
  let side = null, queued = false, open = true;
  const update = () => {
    if (!open) return;
    if (!trigger.isConnected) return away();
    const at = trigger.getBoundingClientRect(), win = seenWindow();
    if (at.bottom < 0 || at.top > win.height) return away();
    // Its natural size, in the look it will have (a sheet has its own padding): as wide as its longest line, as high as all it
    // holds (what is cut of what scrolls added to its height)
    pop.classList.toggle("sheet", win.width <= SHEET);
    const measure = (tight) => {
      pop.classList.toggle("tight", tight);
      pop.style.width = "max-content";
      return { width: pop.offsetWidth, height: pop.offsetHeight - scroller.clientHeight + scroller.scrollHeight };
    };
    const where = { align, minWidth, side };
    const apply = (placed) => {
      // `bottom` is from the bottom of the layout, under a phone's keyboard when one is open
      const under = window.innerHeight - win.height;
      Object.assign(pop.style, {
        left: `${placed.left}px`, width: `${placed.width}px`, maxHeight: `${placed.maxHeight}px`,
        top: placed.top === null ? "auto" : `${placed.top}px`, bottom: placed.bottom === null ? "auto" : `${placed.bottom + under}px`,
      });
    };
    const size = measure(false);
    let placed = place(at, size, win, where);
    apply(placed);
    // What is cut, in the width it has now, is room it still needs: it grows by that, as far as it can
    for (let tries = 0, cut = clipped(pop); cut && !placed.tight && tries < 4; tries++, cut = clipped(pop)) {
      placed = place(at, { ...size, width: Math.max(size.width, placed.width) + cut }, win, where);
      apply(placed);
    }
    // Lines wider than the room it can have are drawn in two, which is as wide as the longer of them
    if (placed.tight) {
      placed = { ...place(at, measure(true), win, where), tight: true };
      apply(placed);
    }
    side = placed.side;
  };
  // Once a frame at most, after what changed has been drawn
  const later = (e) => {
    if (queued || (e?.type === "scroll" && pop.contains(e.target))) return;   // its own list scrolls: it stays
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      update();
    });
  };
  const watch = new MutationObserver(() => later());
  watch.observe(pop, { childList: true, subtree: true, characterData: true });
  addEventListener("resize", later);
  addEventListener("scroll", later, true);
  for (const type of ["resize", "scroll"]) window.visualViewport?.addEventListener(type, later);
  update();
  return {
    update,
    stop() {
      open = false;
      watch.disconnect();
      removeEventListener("resize", later);
      removeEventListener("scroll", later, true);
      for (const type of ["resize", "scroll"]) window.visualViewport?.removeEventListener(type, later);
    },
  };
}

export const Popup = { place, attach, MARGIN, GAP, MIN, MAX_WIDTH, SHEET };
