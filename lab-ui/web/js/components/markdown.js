// Markdown as the page shows it: rendered and sanitized, its links opening next to the lab, its action links
// (`[Ask Pip](action:ask-pip)`) and its links to a page of one of its UIs drawn as actions (a button alone on its line, a link of the
// text in a sentence), its account links (`[Nora](account:nora.pine)`) drawn as the sign-in with copy buttons, its copy fields
// (`[ORD-5004](copy:)`) drawn as the text with a copy button, and its blocks to run (```bash run) given a Run button. A playground's
// guide on its page and in the editor's preview.

import { copyButton, credHtml, esc, icon, loadScript, withoutDefine } from "../core.js";
import { actionOf, inTab, isLook } from "../workspace/split.js";

// The user name of an account link, `account:<username>`, as a playground's `accounts` write it; null for any other address
const USERNAME = /^[A-Za-z0-9][\w.@+-]{0,127}$/;
function accountOf(href) {
  const found = typeof href === "string" ? href.match(/^account:(.*)$/s) : null;
  return found && USERNAME.test(found[1]) ? found[1] : null;
}

// A copy field, `[the text to copy](copy:)`: the address is `copy:` and nothing else, the text of the link is what is copied
const isCopy = (href) => typeof href === "string" && href.trim() === "copy:";

// Each library at one version, with the sha384 hash of its file: the browser refuses a file that differs. Another
// version needs its hash: curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
const MARKED = "https://cdnjs.cloudflare.com/ajax/libs/marked/18.1.1/lib/marked.umd.min.js";
const MARKED_HASH = "sha384-7sjOQLpS2mL+qbjUOYVEk350vVNi+nPTKMQFruUWMDTzgcxzTJiwye3rHDJCJ9ZL";
const PURIFY = "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.17/purify.min.js";
const PURIFY_HASH = "sha384-P0JNneQf81HOl9VwNXK76cQ9VMD6Nfsnepxb0cCOg8+UzEdzgPHAPV4wVQrvYQSx";
const MERMAID = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.15.0/mermaid.min.js";
const MERMAID_HASH = "sha384-yQ4mmBBT+vhTAwjFH0toJXNYJ6O4usWnt6EPIdWwrRvx2V/n5lXuDZQwQFeSFydF";

// What a guide cannot carry besides what DOMPurify removes already: a form (it could send what is typed in it anywhere), a style,
// element or attribute (it could cover the page, or hide part of what a field copies), and any data-* attribute: the page's own
// handlers answer them, so a guide's could press its controls
const SANITIZE = { FORBID_TAGS: ["form", "style"], FORBID_ATTR: ["style", "formaction"], ALLOW_DATA_ATTR: false };

let libs = null;      // the two libraries, loaded once
let diagrams = null;  // Mermaid, loaded once a text has a diagram
let drawn = 0;        // the diagrams drawn so far: each has its own id

async function loadLibs() {
  await withoutDefine(() => Promise.all([loadScript(MARKED, MARKED_HASH), loadScript(PURIFY, PURIFY_HASH)]));
  marked.use({ renderer: { code: runBlock } });
  DOMPurify.addHook("afterSanitizeAttributes", (n) => {
    if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener"); }
  });
  // The three addresses of another scheme a link may have: `action:<id>`, which is no address but the id of what the
  // playground's app does (split.js), `account:<username>`, one of the playground's accounts, and `copy:`, a text to copy. Any
  // other `action:`, `account:` or `copy:` is dropped as every unknown scheme is.
  DOMPurify.addHook("uponSanitizeAttribute", (n, data) => {
    if (n.tagName === "A" && data.attrName === "href" && (actionOf(data.attrValue) || accountOf(data.attrValue) || isCopy(data.attrValue))) {
      data.forceKeepAttr = true;
    }
  });
}

// Its colors are the page's, set in its stylesheet: they follow the theme; its text is measured in the page's font. Strict: a label is
// text, never HTML or a link that runs code.
async function loadMermaid() {
  await withoutDefine(() => loadScript(MERMAID, MERMAID_HASH));
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base",
    fontFamily: getComputedStyle(document.body).fontFamily });
}

// An action link, the only one the sanitizer let keep its `action:`, as a button with the text of the link, disabled: whoever shows
// the guide enables it, as the playground's page does for what it can send. Its icon and its look are withLines'. One whose id is
// none lost its address, and one with no text has nothing to click: both stay a text.
function withActions(page) {
  for (const link of page.content.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href").trim();   // the sanitizer read the address trimmed, and kept it as it was written
    if (!href.startsWith("action:")) continue;
    const id = actionOf(href);
    if (!id || !link.textContent.trim()) {
      link.replaceWith(...link.childNodes);
      continue;
    }
    const button = Object.assign(document.createElement("button"), { type: "button", className: isLook(id) ? "guide-action look" : "guide-action",
      disabled: true, title: "Not available here" });
    button.dataset.action = id;
    button.append(...link.childNodes);
    link.replaceWith(button);
  }
}

// The account an account link names: the one with its user name; when several have it, the one whose `for` is the link's title
// (`[Sign in](account:admin "Gamma console")`), and none when the title names no single one of them, rather than the sign-in of another
function accountNamed(accounts, name, title) {
  const same = name ? accounts.filter((a) => a.username === name) : [];
  const named = same.filter((a) => a.for === title);
  return same.length > 1 ? (named.length === 1 ? named[0] : null) : same[0] || null;
}

// An account link, the sign-in a step asks for, as a card: the text of the link, then the user and its password (or its key),
// each with a copy button, from the playground's `accounts`, where a password is a setting and never a literal of the guide.
// A link to no known account (in the editor's preview, which has none) shows its text and the user name.
function withAccounts(page, accounts) {
  for (const link of page.content.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href").trim();   // the sanitizer read the address trimmed, and kept it as it was written
    if (!href.startsWith("account:")) continue;
    const name = accountOf(href);
    const account = accountNamed(accounts, name, link.getAttribute("title") || "");
    const card = Object.assign(document.createElement("span"), { className: "guide-account" });
    card.innerHTML = `${icon("login")}<span class="guide-account-name"></span>` + (account
      ? credHtml(account.username, "User") + credHtml(account.password, "Password") + credHtml(account.key, "Key")
      : name ? `<span class="pg-cred"><code>${esc(name)}</code></span>` : "");
    card.querySelector(".guide-account-name").append(...link.childNodes);
    link.replaceWith(card);
  }
}

// A copy field, a question to ask or an id to paste, as the text of the link, as it was written (its code spans too), in a field
// with a copy button that copies that text, its spaces as they read. The field holds only what shows: its code, bold and italics,
// with no attribute, and the text of any other element, so nothing hidden is copied. One with no text has nothing to copy: it
// stays a text.
const SHOWN = "code, strong, em";
function withCopies(page) {
  for (const link of page.content.querySelectorAll("a[href]")) {
    if (!isCopy(link.getAttribute("href"))) continue;
    for (const el of [...link.querySelectorAll("*")].reverse()) {   // the deepest first
      if (!el.matches(SHOWN)) el.replaceWith(...el.childNodes);
      else for (const { name } of [...el.attributes]) el.removeAttribute(name);
    }
    const text = link.textContent.replace(/\s+/g, " ").trim();
    if (!text) {
      link.replaceWith(...link.childNodes);
      continue;
    }
    const field = Object.assign(document.createElement("span"), { className: "guide-copy",
      innerHTML: `<span class="guide-copy-text"></span>${copyButton(text, "Copy the text")}` });
    field.firstElementChild.append(...link.childNodes);
    withWholeWords(field.firstElementChild);
    link.replaceWith(field);
  }
}

// A word with a hyphen in a copy field, an id such as ORD-5007, is one word to read, in bold or in code too: it moves to the next
// line whole, and breaks only when it is longer than the field (pages.css). What the button copies is the text, unchanged.
const HYPHENATED = /\S*\w-\w\S*/g;
function withWholeWords(text) {
  for (const node of [...text.childNodes]) {
    if (node.nodeType === 1) withWholeWords(node);
    if (node.nodeType !== 3 || !node.data.match(HYPHENATED)) continue;
    const words = document.createElement("span");
    words.innerHTML = esc(node.data).replace(HYPHENATED, (word) => `<span class="guide-word">${word}</span>`);
    for (const part of [...words.childNodes]) text.insertBefore(part, node);
    node.remove();
  }
}

// A link to a page of one of the UIs beside the guide (`tabs`, the playground's), which a click loads in its tab: an action that only
// shows something, with an eye (withLines), and still a link, which the keyboard follows and a click with a key held opens in a
// browser tab. Any other link stays a link.
function withLooks(page, tabs) {
  for (const link of page.content.querySelectorAll("a[href]")) {
    if (inTab(tabs, link.getAttribute("href").trim()) && link.textContent.trim()) link.className = "guide-action look";
  }
}

// An action's line is a paragraph, an item of a list or a cell. What may stand beside it there for it to be a button: other actions
// (a row of them), the lists and blocks under an item, and space.
const LINE = "p, li, td, th";
const BESIDE = ".guide-action, ul, ol, p, pre, div, blockquote, table, figure, br";
function alone(action) {
  const line = action.parentElement;
  return !!line?.matches(LINE) && [...line.childNodes].every((node) => node.nodeType === 3 ? !node.data.trim()
    : node.nodeType !== 1 || node.matches(BESIDE));
}

// Its icon, a play for what does something and an eye for what only shows something, before its text. Alone on its line, or in a
// row of actions, an action is the Lab's button. In a sentence it is drawn as a link of the text, in the height of the line. An
// action stays a button, which the browser lays out as one box: it moves to the next line whole. A link to a UI flows as text: its
// icon is glued to its first word, so that the two move to the next line together, and a name of up to three words (SHORT) moves
// to the next line whole, as a name is read, unless it is longer than a line (pages.css).
const SHORT = 3;
function withLines(page) {
  const actions = [...page.content.querySelectorAll(".guide-action")];
  const buttons = actions.filter(alone);   // all decided first: an action turned into a button stays an action of its row
  for (const action of actions) {
    const svg = icon(action.classList.contains("look") ? "eye" : "play");
    if (buttons.includes(action) || action.matches("button")) {   // one box: its icon, then its text
      if (buttons.includes(action)) action.className = `btn ${action.className}`;
      const text = document.createElement("span");
      text.append(...action.childNodes);
      action.innerHTML = svg;
      action.append(text);
      continue;
    }
    if (action.textContent.trim().split(/\s+/).length <= SHORT) action.classList.add("short");
    const glue = Object.assign(document.createElement("span"), { className: "guide-glue", innerHTML: svg });
    const first = action.firstChild;
    if (first?.nodeType === 3) {
      const [, word, rest] = first.data.match(/^(\s*\S*)([\s\S]*)$/);
      glue.append(word);
      first.data = rest;
      action.prepend(glue);
    } else {
      action.prepend(glue);
      if (first && !/\s/.test(first.textContent.trim())) glue.append(first);   // a word in code or in bold, glued as a word is
    }
  }
}

// A block to run, ```bash run: its fence says the language and then `run`, and marked keeps the language only. The block is
// drawn in a wrapper with a button that types it at the shell of the playground, disabled until whoever shows the guide enables
// it (as for an action link). One that is none stays a block.
function withRuns(page) {
  for (const code of page.content.querySelectorAll("pre > code.lab-run")) {
    const pre = code.parentElement, wrap = document.createElement("div");
    wrap.className = "guide-code";
    const button = Object.assign(document.createElement("button"), { type: "button", className: "btn guide-run", disabled: true,
      title: "Not available here", innerHTML: `${icon("play")}<span>Run</span>` });
    pre.replaceWith(wrap);
    wrap.append(pre, button);
  }
}

// A ```mermaid block, drawn: a figure with its diagram. Without Mermaid (no network) the block stays as it is
// written; a diagram Mermaid cannot read too, with why under it.
async function withDiagrams(page) {
  const blocks = page.content.querySelectorAll("pre > code.language-mermaid");
  if (!blocks.length) return page.innerHTML;
  try {
    await (diagrams ||= loadMermaid().catch((err) => { diagrams = null; throw err; }));
  } catch {
    return page.innerHTML;
  }
  for (const code of blocks) {
    const id = `diagram-${++drawn}`;
    try {
      await mermaid.parse(code.textContent);
      const figure = document.createElement("figure");
      figure.className = "diagram";
      figure.innerHTML = (await mermaid.render(id, code.textContent)).svg;
      // One picture for a screen reader, named by its own title (accTitle) or by what the guide says just before it
      const svg = figure.querySelector("svg"), before = code.parentElement.previousElementSibling?.textContent?.trim() || "";
      const named = svg?.querySelector("title")?.textContent?.trim() || before.slice(0, 160) || "Diagram";
      figure.setAttribute("role", "img");
      figure.setAttribute("aria-label", named);
      svg?.setAttribute("aria-hidden", "true");
      code.parentElement.replaceWith(figure);
    } catch (err) {
      code.parentElement.insertAdjacentHTML("afterend", `<p class="err-text">${esc(err.message || String(err))}</p>`);
    } finally {
      document.getElementById(`d${id}`)?.remove();   // what Mermaid drew in the page to measure it
    }
  }
  return page.innerHTML;
}

// A quote that opens with `[!NOTE]`, GitHub's alert syntax, is a note: the marker goes and the quote takes the class `guide-note`
function withNotes(page) {
  for (const quote of page.content.querySelectorAll("blockquote")) {
    const text = quote.firstElementChild?.firstChild;
    const marker = text?.nodeType === 3 && /^\s*\[!NOTE\]\s*/.exec(text.textContent);
    if (!marker) continue;
    text.textContent = text.textContent.slice(marker[0].length);
    quote.classList.add("guide-note");
  }
}

// A fenced block whose info string is a language and then `run` (```bash run) is marked for withRuns, with the class `lab-run`; marked
// keeps the language alone in its own class, so the block is written here. Any other block is marked's own (false).
function runBlock({ text, lang }) {
  const [language = "", ...words] = String(lang || "").trim().split(/\s+/);
  if (!words.includes("run")) return false;
  const classes = [language && `language-${esc(language)}`, "lab-run"].filter(Boolean).join(" ");
  return `<pre><code class="${classes}">${esc(text)}\n</code></pre>\n`;
}

// The HTML of a markdown text, its diagrams drawn, its account links filled from `accounts` and its links to a page of a UI drawn
// from `tabs` (a playground's). Without the libraries (no network) the text shows as it is written; a load that failed is tried
// again the next time.
export async function markdownHtml(markdown, { accounts = [], tabs = [] } = {}) {
  let html;
  try {
    await (libs ||= loadLibs().catch((err) => { libs = null; throw err; }));
    html = DOMPurify.sanitize(marked.parse(markdown), SANITIZE);
  } catch {
    return `<pre>${esc(markdown)}</pre>`;
  }
  const page = document.createElement("template");
  page.innerHTML = html;
  // A guide sits under the title of its page: its headings are one level below it for a screen reader, as they look
  for (const heading of page.content.querySelectorAll("h1, h2, h3, h4, h5, h6")) heading.setAttribute("aria-level", String(Math.min(6, Number(heading.tagName[1]) + 1)));
  withActions(page);
  withNotes(page);
  withAccounts(page, accounts);
  withCopies(page);
  withLooks(page, tabs);
  withLines(page);
  withRuns(page);
  return withDiagrams(page);
}
