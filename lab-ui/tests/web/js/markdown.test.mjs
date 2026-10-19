// The markdown of a guide as the page renders it (web/js/components/markdown.js), over a small DOM: marked and DOMPurify are the two libraries the page loads
// from a CDN, so here they are stand-ins that record what the page asks of them. What is under test is what the page adds: the one extra address
// its sanitizer lets a link keep, `action:<id>`, and the buttons its action links become.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installDom } from "./dom-stub.mjs";

const page = installDom();
const hooks = {};
let source = "";   // what `marked` turns the markdown into
let sanitized = [];
const extensions = [];   // what the page gave `marked.use`
globalThis.marked = { parse: () => source, use: (extension) => { extensions.push(extension); } };
let configs = [];
globalThis.DOMPurify = {
  addHook: (name, hook) => { (hooks[name] ||= []).push(hook); },
  sanitize: (html, config) => { sanitized.push(html); configs.push(config); return html; },
};
// A script of a CDN loads at once
const scripts = [];   // the scripts the page asked for
page.doc.head.append = (script) => { scripts.push(script); queueMicrotask(() => script.onload()); };
globalThis.mermaid = { initialize() {}, parse: async () => true, render: async () => ({ svg: "<svg></svg>" }) };

const { markdownHtml } = await import("../../../web/js/components/markdown.js");
const render = async (html) => {
  source = html;
  const out = page.doc.createElement("div");
  out.innerHTML = await markdownHtml("# the markdown is not read: marked is a stand-in");
  return out;
};

test("the sanitizer lets a link keep `action:` and an id, and no other address of that scheme", async () => {
  await render("<p>x</p>");
  const [keep] = hooks.uponSanitizeAttribute;
  const kept = (tag, attrName, attrValue) => {
    const data = { attrName, attrValue, forceKeepAttr: undefined };
    keep({ tagName: tag }, data);
    return data.forceKeepAttr === true;
  };
  for (const id of ["ask-ace-accounts", "leak-an-iban", "a", "0", "move-600-through-ace"]) assert.equal(kept("A", "href", `action:${id}`), true, id);
  for (const value of ["action:", "action:Ask-Ace", "action:ask ace", "action:ask_ace", "action:-a", "action:a:b", "Action:ask", "javascript:alert(1)",
    "https://example.com", "http://localhost:8110", "#/playgrounds/acme-bakery/2", "data:text/html,x", "", "action:ask\n"]) {
    assert.equal(kept("A", "href", value), false, value);
  }
  assert.equal(kept("IMG", "src", "action:ask-ace"), false, "only a link");
  assert.equal(kept("A", "title", "action:ask-ace"), false, "only its address");
  assert.equal(kept("FORM", "action", "action:ask-ace"), false);
});

test("an action link becomes a button with its play icon and the text of the link, disabled until a page enables it", async () => {
  const out = await render('<p>Click <a href="action:leak-an-iban">Leak <strong>an</strong> IBAN</a>.</p>');
  const [button] = out.querySelectorAll("button");
  assert.ok(button, "a button");
  assert.equal(out.querySelectorAll("a").length, 0, "no link is left");
  assert.equal(button.dataset.action, "leak-an-iban");
  assert.equal(button.type, "button", "it submits nothing");
  assert.equal(button.className, "btn guide-action");
  assert.equal(button.disabled, true);
  assert.ok(button.title, "and it says why: here nothing runs");
  assert.equal(button.querySelector("svg use").getAttribute("href"), "#i-play");
  assert.equal(button.querySelector("span").innerHTML, "Leak <strong>an</strong> IBAN", "the text of the link as it was written, its emphasis too");
  assert.equal(out.textContent, "Click Leak an IBAN.");
});

test("every action link of a page is a button, and the other links are left alone", async () => {
  const out = await render(`<ul><li><a href="action:ask-ace-accounts">Ask Ace</a> and <a href="action:ask-ace-direct">without the gateway</a></li>
    <li><a href="http://localhost:8086" target="_blank" rel="noopener">Gamma</a></li><li><a href="#/playgrounds/acme-bakery/2">Next</a></li></ul>`);
  assert.deepEqual(out.querySelectorAll("button").map((button) => button.dataset.action), ["ask-ace-accounts", "ask-ace-direct"]);
  assert.deepEqual(out.querySelectorAll("a").map((link) => link.getAttribute("href")), ["http://localhost:8086", "#/playgrounds/acme-bakery/2"]);
  assert.equal(out.querySelector('a[href^="http"]').getAttribute("target"), "_blank");
});

test("a link whose `action:` the sanitizer dropped is left as the text it was: no button, no address", async () => {
  // what DOMPurify gives for [Not an id](action:Not-An-Id): the link without its href
  const out = await render('<p>A broken one: <a>Not an id</a>.</p>');
  assert.equal(out.querySelectorAll("button").length, 0);
  assert.equal(out.textContent, "A broken one: Not an id.");
});

test("the markdown goes through the sanitizer before anything is made of it", async () => {
  sanitized = [];
  await render('<p><a href="action:leak-an-iban">x</a></p>');
  assert.deepEqual(sanitized, ['<p><a href="action:leak-an-iban">x</a></p>']);
});

test("an address with a space around it in the markup is the same action to the button as to the sanitizer, which read it trimmed", async () => {
  const out = await render('<p><a href=" action:leak-an-iban\n">Leak</a></p>');
  assert.deepEqual(out.querySelectorAll("button").map((button) => button.dataset.action), ["leak-an-iban"]);
});

test("a link that is none, or has nothing to click, stays a text: never a button for an id that is none", async () => {
  // a link the hook kept (so it reads as an action) whose id is none once read as the button reads it, and one with no text
  const out = await render('<p>a <a href="action:Not An Id">not</a> b <a href="action:leak-an-iban"></a> c <a href="action:leak-an-iban"> </a> d</p>');
  assert.equal(out.querySelectorAll("button").length, 0);
  assert.equal(out.querySelectorAll("a").length, 0);
  assert.equal(out.textContent, "a not b  c   d");
});

test("the sanitizer is told what a guide cannot carry: a form, a style, and a data-action of its own, which only the buttons made after it have", async () => {
  configs = [];
  await render("<p>x</p>");
  assert.deepEqual(configs.at(-1).FORBID_TAGS, ["form"]);
  for (const attr of ["data-action", "style", "formaction"]) assert.ok(configs.at(-1).FORBID_ATTR.includes(attr), attr);
  const out = await render('<p><a href="action:leak-an-iban">x</a></p>');
  assert.equal(out.querySelector("button").dataset.action, "leak-an-iban", "the button is given its data-action after the sanitizer");
});

test("each library comes from the address of its version, with the hash of its file, which the browser checks", async () => {
  await render('<pre><code class="language-mermaid">graph TD; A-->B</code></pre>');
  assert.deepEqual(scripts.map((script) => script.src), [
    "https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.14/lib/marked.umd.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.16/purify.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.15.0/mermaid.min.js",
  ]);
  for (const script of scripts) {
    assert.match(script.integrity, /^sha384-[A-Za-z0-9+/]{64}$/, script.src);
    assert.equal(script.crossOrigin, "anonymous", `${script.src} is fetched with CORS, as a hash needs`);
  }
});

// --- The blocks to run -----------------------------------------------------------------------

const block = (text, lang) => extensions.at(-1).renderer.code({ text, lang });

test("a fence of a language and `run` is a block to run: its language kept, its text escaped, and nothing else of the fence", async () => {
  await render("<p>x</p>");
  assert.equal(block("k get pods\n", "bash run"), '<pre><code class="language-bash lab-run">k get pods\n\n</code></pre>\n');
  assert.equal(block('echo "<b>" && k get ns', "sh   run"), '<pre><code class="language-sh lab-run">echo &quot;&lt;b&gt;&quot; &amp;&amp; k get ns\n</code></pre>\n');
});

test("any other fence is left to marked", async () => {
  await render("<p>x</p>");
  for (const lang of ["bash", "yaml", "mermaid", "", undefined, "run", "bash running", "bash norun", "bash --run"]) assert.equal(block("x", lang), false, String(lang));
});

test("a block to run becomes a block with a Run button, disabled until a page enables it, and a plain block is left alone", async () => {
  const out = await render('<pre><code class="language-bash lab-run">k get pods\n</code></pre><pre><code class="language-bash">k get ns\n</code></pre>');
  const [button] = out.querySelectorAll("button");
  assert.equal(out.querySelectorAll("button").length, 1, "one button: the plain block has none");
  assert.equal(button.className, "btn guide-run");
  assert.equal(button.type, "button");
  assert.equal(button.disabled, true);
  assert.ok(button.title, "and it says why: here nothing runs");
  assert.equal(button.querySelector("svg use").getAttribute("href"), "#i-play");
  assert.equal(button.textContent, "Run");
  const wrap = button.closest(".guide-code");
  assert.ok(wrap, "in a wrapper of its own");
  assert.equal(wrap.querySelector("pre code").textContent, "k get pods\n", "the block it runs is the one beside it");
});
