// The pieces of a form in a dialog, shared by the dialogs that ask something of a person: the dialog of one form or of the steps of a wizard
// (dialogOf), the short form (formDialog), a field with its label and the information mark of its term (field) and the line under the
// fields (note). A form stays open until the server answered, so that a refusal shows in it, and Cancel stays live while a request is at
// work; what was typed as a secret goes as the dialog closes. The Gravitee Cloud dialogs (platform.js) and those of the catalogs
// are made of them.

import { $, $$, esc, icon, store } from "../core.js";
import { actionButton, paintActions, tipHtml, setOff } from "../components/ui.js";
import { busy } from "../activity/jobs.js";
import { selectField } from "../components/combo.js";

// The steps of a wizard, in the head of its dialog: a number each, a check once it is done, the current one marked (aria-current)
const stepper = (names) => `<ol class="wizard-steps" aria-label="Steps">${names.map((name, i) =>
  `<li><span class="wizard-num" aria-hidden="true">${i + 1}</span><span class="sr-only" hidden>Done: </span>${esc(name)}</li>`).join("")}</ol>`;

// A dialog of forms, one shown at a time: a short form, or the steps of a wizard, which stay in one dialog that is never closed between
// them. A panel is a form: its fields, the line of a refusal, Cancel and the button of its action, which says it at work (`action`:
// `key`, `verb`, `label`). `submit()` sends it and throws the server's words on a refusal; once it is sent the dialog closes, unless the
// panel `stays` (a step that leads to the next), and `after(answer)` runs. `lacks()` says what it lacks beyond its required fields, in
// the words of the button's title, or nothing. `closed()` runs however the dialog closed. A `large` dialog has the head of the large
// ones (its title, the `steps` of a wizard, the cross) and one footer under a body that takes the height left.
export function dialogOf(id, panels, { large = false, steps = [], closed = () => {} } = {}) {
  const dialog = document.createElement("dialog");
  dialog.className = large ? "platform-dialog platform-wizard" : "create-dialog platform-dialog";
  dialog.setAttribute("aria-labelledby", `${id}-title`);
  const problem = '<p class="create-problem" role="alert" hidden></p>';
  const actions = ({ action }) => `<div class="dialog-actions"><button class="btn" type="button" data-cancel>Cancel</button>
    ${actionButton({ ...action, cls: "primary" })}</div>`;
  const title = `<h2 id="${id}-title"></h2>`;
  dialog.innerHTML = large
    ? `<div class="dialog-head">${title}${steps.length ? stepper(steps) : ""}
        <button class="icon-btn" type="button" data-close aria-label="Close" data-tip="Close">${icon("close")}</button></div>
      <p class="sr-only" id="${id}-said" role="status"></p>
      ${panels.map((panel) => `<form autocomplete="off">${panel.fields}<div class="dialog-foot">${problem}${actions(panel)}</div></form>`).join("")}`
    : `<form autocomplete="off">${title}${panels[0].fields}${problem}${actions(panels[0])}</form>`;
  document.body.append(dialog);
  $$("select", dialog).forEach(selectField);   // chosen with the picker, as every list that opens
  const parts = $$("form", dialog).map((form, i) => ({ lacks: () => "", ...panels[i], form, problem: $(".create-problem", form),
    go: $("[data-act-for]", form) }));
  const list = $(".wizard-steps", dialog), shut = () => $$("[data-cancel], [data-close]", dialog);
  let sending = false;   // a second click or Enter is the same request

  // The button waits for what the form lacks, and says it
  const ready = (part) => {
    const why = part.lacks(), off = !part.form.checkValidity() || !!why;
    setOff(part.go, off, why || "A required field is empty");
  };
  for (const part of parts) {
    part.form.addEventListener("input", () => {
      part.problem.hidden = true;
      ready(part);
    });
    part.form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (sending) return;
      sending = true;
      for (const b of shut()) b.disabled = true;
      try {
        const answer = await busy(part.action.key, part.action.verb, part.submit());
        if (!part.stays) dialog.close();
        store.refresh();   // a platform, a token or a gateway changed what the pages say
        part.after(answer);
      } catch (err) {
        part.problem.hidden = false;
        part.problem.textContent = err.message;
      } finally {
        sending = false;
        for (const b of shut()) b.disabled = false;
      }
    });
  }
  dialog.addEventListener("click", (e) => {
    if (e.target.closest?.("[data-cancel], [data-close]")) dialog.close();
    const reveal = e.target.closest?.("[data-reveal-secret]");
    if (reveal) {
      const field = $("input", reveal.parentElement), shown = field.getAttribute("type") === "password";
      field.setAttribute("type", shown ? "text" : "password");
      reveal.innerHTML = icon(shown ? "eye-off" : "eye");
      reveal.dataset.tip = shown ? "Hide" : "Show";
      reveal.setAttribute("aria-label", reveal.dataset.tip);
    }
  });
  dialog.addEventListener("cancel", (e) => { if (sending) e.preventDefault(); });
  // However it closes, what was typed as a secret goes
  dialog.addEventListener("close", () => {
    $$("input[data-secret]", dialog).forEach((el) => { el.value = ""; el.setAttribute("type", "password"); });
    $$("[data-reveal-secret]", dialog).forEach((el) => { el.innerHTML = icon("eye"); el.dataset.tip = "Show"; el.setAttribute("aria-label", "Show"); });
    closed();
  });

  // The panel `at` in place of the one shown, its step marked in the head, the keyboard on its first field (not on the information mark
  // of its label, whose popover would open with it)
  function show(at) {
    parts.forEach((part, i) => { part.form.hidden = i !== at; });
    if (list) {
      $$("li", list).forEach((li, i) => {
        li.classList.toggle("done", i < at);
        if (i === at) li.setAttribute("aria-current", "step");
        else li.removeAttribute("aria-current");
        $(".wizard-num", li).innerHTML = i < at ? icon("check") : String(i + 1);
        $(".sr-only", li).hidden = i >= at;
      });
    }
    const part = parts[at];
    part.problem.hidden = true;
    ready(part);
    paintActions(dialog);
    $$("input, select, .combo-btn", part.form).find((el) => !el.disabled && !el.closest("[hidden], details:not([open])"))?.focus();
  }
  return {
    forms: parts.map((part) => part.form),
    // Opens it on the panel `at`, titled `title`, with the steps of the head or without them (`steps`). `fill()` sets the fields, `said`
    // is the word of its button when it is not its own.
    open(title, { at = 0, steps: drawn = false, fill = () => {}, said = parts[at].action.label } = {}) {
      $("h2", dialog).textContent = title;
      if (list) list.hidden = !drawn;
      if (large) $(`#${id}-said`).textContent = "";   // what it said the last time is not said again
      $(".pbtn-face span", parts[at].go).textContent = said;
      fill();
      if (!dialog.open) dialog.showModal();
      show(at);
    },
    // The next step of a wizard, in the same dialog, which a screen reader hears after what led to it (`done`)
    next(at, done = "") {
      show(at);
      $(`#${id}-said`).textContent = `${done ? `${done}. ` : ""}Step ${at + 1} of ${steps.length}, ${steps[at]}`;
    },
    close: () => dialog.close(),
  };
}

// A dialog of one form, the size of the create form. Answers what opens it: a title, `fill`, which sets the fields, and the word of the
// button when it is not its own.
export function formDialog(id, fields, action, submit, after, lacks = () => "", closed = () => {}) {
  const { open } = dialogOf(id, [{ fields, action, submit, after, lacks }], { closed });
  return (title, fill, said) => open(title, { fill, said });
}

// A field: its label, with the information mark of its term when it has one (what it is, where it is made), then its control
// The information mark of a label says what its term means: its sentence, titled with the term only when the label says another word
export const field = (id, label, control, term = null) => `<div class="field"><span class="field-label"><label for="${id}">${label}</label>${
  term ? tipHtml(icon("info"), term[0] === label ? term.slice(1) : term, "plat-info", label) : ""}</span>${control}</div>`;

// A line under the fields: where a value comes from, or what the action does to what runs. Hidden while it has nothing to say.
export const note = (id, html = "", hidden = false) => `<p class="field-note" id="${id}" ${hidden ? "hidden" : ""}>${html}</p>`;
