// Markdown as the page shows it: rendered and sanitized, its links opening next to the lab, its action links
// (`[Ask Ace](action:ask-ace)`) drawn as buttons, and its blocks to run (```bash run) given a Run button. A playground's guide on its page and in the editor's preview.

import { esc, icon, loadScript } from "../core.js";
import { actionOf, isLook } from "../workspace/split.js";

// Each library at one version, with the sha384 hash of its file: the browser refuses a file that differs. Another
// version needs its hash: curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
const MARKED = "https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.14/lib/marked.umd.min.js";
const MARKED_HASH = "sha384-J1VD5X/E72gORmmXiADelqS192/84qho9IALLC3d5oxjp1RgEN3He4ANyjqXyz10";
const PURIFY = "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.16/purify.min.js";
const PURIFY_HASH = "sha384-a7SzOxErzJ3ZpQz0zJ32d67dSitNzPcbfybc/ykU9KJhMgZkwqfSxlhhdJRS+XGL";
const MERMAID = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.15.0/mermaid.min.js";
const MERMAID_HASH = "sha384-yQ4mmBBT+vhTAwjFH0toJXNYJ6O4usWnt6EPIdWwrRvx2V/n5lXuDZQwQFeSFydF";

// What a guide cannot carry besides what DOMPurify removes already: a form (it could send what is typed in it anywhere), a style
// (it could cover the page), and the `data-action` that only an action link, once sanitized, is given (withActions)
const SANITIZE = { FORBID_TAGS: ["form"], FORBID_ATTR: ["data-action", "style", "formaction"] };

let libs = null;      // the two libraries, loaded once
let diagrams = null;  // Mermaid, loaded once a text has a diagram
let drawn = 0;        // the diagrams drawn so far: each has its own id

// The two libraries give globals. Once the editor has been opened, its loader offers `define` to every script and
// they would register with it instead: it is put aside while they load.
async function loadLibs() {
  const define = window.define;
  if (define) window.define = undefined;
  try {
    await Promise.all([loadScript(MARKED, MARKED_HASH), loadScript(PURIFY, PURIFY_HASH)]);
  } finally {
    if (define) window.define = define;
  }
  marked.use({ renderer: { code: runBlock } });
  DOMPurify.addHook("afterSanitizeAttributes", (n) => {
    if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener"); }
  });
  // The one address of another scheme a link may have: `action:<id>`, which is no address but the id of what the
  // playground's app does (split.js). Any other `action:` is dropped as every unknown scheme is.
  DOMPurify.addHook("uponSanitizeAttribute", (n, data) => {
    if (n.tagName === "A" && data.attrName === "href" && actionOf(data.attrValue)) data.forceKeepAttr = true;
  });
}

// The same loader dance as loadLibs. Its colors are the page's, set in its stylesheet: they follow the theme; its
// text is measured in the page's font. Strict: a label is text, never HTML or a link that runs code.
async function loadMermaid() {
  const define = window.define;
  if (define) window.define = undefined;
  try {
    await loadScript(MERMAID, MERMAID_HASH);
  } finally {
    if (define) window.define = define;
  }
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base",
    fontFamily: getComputedStyle(document.body).fontFamily });
}

// An action link, the only one the sanitizer let keep its `action:`, as a button with its play icon (an eye for an id that starts
// with `open-`) and the text of the link, disabled: whoever shows the guide enables it, as the playground's page does for what it can send. One whose id is
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
    const look = isLook(id);   // an eye for what only shows something, a play for what does something
    const button = Object.assign(document.createElement("button"), { type: "button", className: look ? "btn guide-action look" : "btn guide-action",
      disabled: true, title: "Not available here", innerHTML: `${icon(look ? "eye" : "play")}<span></span>` });
    button.dataset.action = id;
    button.lastElementChild.append(...link.childNodes);
    link.replaceWith(button);
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

// A fenced block whose info string is a language and then `run` (```bash run) is marked for withRuns, with the class `lab-run`; marked
// keeps the language alone in its own class, so the block is written here. Any other block is marked's own (false).
function runBlock({ text, lang }) {
  const [language = "", ...words] = String(lang || "").trim().split(/\s+/);
  if (!words.includes("run")) return false;
  const classes = [language && `language-${esc(language)}`, "lab-run"].filter(Boolean).join(" ");
  return `<pre><code class="${classes}">${esc(text)}\n</code></pre>\n`;
}

// The HTML of a markdown text, its diagrams drawn. Without the libraries (no network) the text shows as it is
// written; a load that failed is tried again the next time.
export async function markdownHtml(markdown) {
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
  withRuns(page);
  return withDiagrams(page);
}
