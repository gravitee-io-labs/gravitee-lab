// The notice of the move to catalogs: "Lab updated", on the first page load after the Lab moved a machine's items to where catalogs keep them
//. Normal case: nothing at all. The server computes everything (`state.layout`: its phase, and the notice with its title, its
// rows and its button); this only draws it, in the rows of every effects dialog, and follows the state while it is open: the phase waiting
// (one row, the settings are not changed yet) becomes the notice of what moved, or nothing, when the move is done. It is never drawn for a
// notice that has no row to say (the server's notice also exists when items moved and nothing needs the person), and a person is never
// trapped in it: Escape closes it for this page load, Close (once the move is done) for good, and a move that failed offers Try again beside.

import { $, esc, loading, setHTML, api, store } from "../core.js";
import { toast, effectRows } from "../components/ui.js";

// What the person closes the notice with, and what asks the move again: the routes of the server
const CLOSE = "/api/layout/close";
const RETRY = "/api/layout/retry";

// The order of the rows, whichever order the server's rows come in: the row about what runs (Restart needed) is
// computed where the state is, and comes after the others
const ORDER = ["settings", "personal", "kept", "turned_off", "restart", "not_moved"];
const place = (row) => (ORDER.includes(row.key) ? ORDER.indexOf(row.key) : ORDER.length);
const words = (value) => (typeof value === "string" ? value : "");

// The notice as it is drawn, from `state.layout`; null when there is nothing to draw: no notice, or one with no row (items that moved and
// need nothing of the person) once the move is done. A move that waits has its row; one that failed has its line and Try again.
function wanted(layout) {
  const notice = layout?.notice;
  if (!notice || typeof notice !== "object") return null;
  const phase = layout.phase === "waiting" || layout.phase === "failed" ? layout.phase : "ready";
  const rows = (Array.isArray(notice.rows) ? notice.rows : [])
    .filter((row) => row && typeof row === "object" && Array.isArray(row.items))
    .map((row) => ({ key: words(row.key), label: words(row.label), items: row.items.filter((name) => typeof name === "string" && name), hint: words(row.hint), note: true }))
    .filter((row) => row.items.length)
    .sort((a, b) => place(a) - place(b));
  if (phase !== "failed" && !rows.length) return null;
  return { phase, id: notice.id ?? "", title: words(notice.title), text: words(notice.text), rows, button: words(notice.button) };
}

const dialog = document.createElement("dialog");
dialog.className = "choice-dialog platform-dialog";
dialog.setAttribute("aria-labelledby", "layout-title");
dialog.innerHTML = `
  <form autocomplete="off">
    <h2 id="layout-title"></h2>
    <p class="create-problem" role="alert" hidden></p>
    <div class="choice-list single" id="layout-rows"></div>
    <div class="dialog-actions" id="layout-actions"></div>
  </form>`;
document.body.append(dialog);

const rowsEl = $("#layout-rows", dialog), actionsEl = $("#layout-actions", dialog), textEl = $(".create-problem", dialog);
let current = null;     // the notice on screen: its phase and its id
let dismissed = null;   // the one the person closed in this page load: it does not open again (a notice that changed in kind does)
let working = "";       // "close" or "retry" while that request is on its way
let shown = null;       // what is drawn, for the form's submit

const keyOf = (notice) => `${notice.phase}:${notice.id}`;

// A dialog that is not open while the notice is on screen was closed by the person (Escape), by the router or by a button. Its `close` event
// comes a task later, and a state can come before it: the dialog is read as it is, not as it was told.
function closedMeanwhile() {
  if (dialog.open || current === null) return;
  dismissed = current;
  current = null;
}

// A button that waits for its request, saying so
const waiting = (label) => `<button class="btn primary" type="submit" aria-disabled="true" data-why="${esc(label)}" data-primary>${loading(label)}</button>`;

// Close and Try again. Close is the one button of a notice; a move that failed has Try again as the one Enter presses, and Close beside it,
// which does not ask the server anything: nothing was moved, so there is nothing to close for good.
function buttons(notice) {
  const failed = notice.phase === "failed", word = notice.button || (failed ? "Try again" : "Close");
  if (failed) {
    return `<button class="btn" type="button" data-dismiss>Close</button>${working === "retry" ? waiting("Trying again")
      : `<button class="btn primary" type="submit" data-primary data-retry>${esc(word)}</button>`}`;
  }
  return working === "close" ? waiting("Closing") : `<button class="btn primary" type="submit" data-primary data-close>${esc(word)}</button>`;
}

function draw(notice) {
  shown = notice;
  $("#layout-title", dialog).textContent = notice.title;
  textEl.hidden = !notice.text;
  textEl.textContent = notice.text;
  rowsEl.hidden = !notice.rows.length;
  setHTML(rowsEl, effectRows(notice.rows));
  setHTML(actionsEl, buttons(notice));
}

// The notice follows the state: it opens once for what there is to say, is drawn again as the phase and the rows change under it, and goes
// when there is nothing left to say (the move finished and needs nothing of the person, or it was closed from another window)
function follow() {
  closedMeanwhile();
  const notice = wanted(store.state?.layout);
  if (!notice) {
    if (dialog.open) {
      current = null;   // the Lab took it away: closing it is not the person's choice
      dialog.close();
    }
    shown = null;
    return;
  }
  if (dialog.open) {
    current = keyOf(notice);
    draw(notice);
    return;
  }
  if (keyOf(notice) === dismissed) return;
  current = keyOf(notice);
  draw(notice);
  dialog.showModal();
  $("[data-primary]", dialog)?.focus();
}

// Closes the notice for good, once the move is done; the server forgets it, and the state no longer carries it
async function close() {
  working = "close";
  draw(shown);
  try {
    await api(CLOSE, {});
    dialog.close();   // the notice is the person's to forget from here: what closed it is read as dismissed (closedMeanwhile)
    await store.refresh();
  } catch (err) {
    toast(err.message, true);
  } finally {
    working = "";
    if (shown && dialog.open) draw(shown);
  }
}

// Asks the move again; what it does is read in the state that follows (the phase becomes waiting, then the notice, or nothing)
async function retry() {
  working = "retry";
  draw(shown);
  try {
    await api(RETRY, {});
    await store.refresh();
  } catch (err) {
    toast(err.message, true);
  } finally {
    working = "";
    if (shown && dialog.open) draw(shown);
  }
}

dialog.addEventListener("click", (e) => {
  if (e.target.closest("[data-dismiss]")) dialog.close();
});
// Enter presses the one button: Close, or Try again for a move that failed. A move that waits has nothing to close for good.
$("form", dialog).addEventListener("submit", (e) => {
  e.preventDefault();
  if (working || !shown) return;
  if (shown.phase === "failed") retry();
  else if (shown.phase === "waiting") dialog.close();
  else close();
});
// Escape, the router (the address changed) and the buttons all end here: what the person closed is not drawn again in this page load
dialog.addEventListener("close", closedMeanwhile);

export const LabUpdated = {
  init() {
    store.subscribe(follow);
    follow();
  },
};
