// The shell of a playground in the page: xterm.js on the output the server streams (terminal.py keeps the shell while it runs,
// one for each playground), what is typed or run from the guide sent back by POST, in order. The libraries come from a CDN the
// first time a terminal is shown; without them the panel says so. The rules are in split.js, the panel around it is termpane.js.

import { api, loadScript, loadStyle } from "../core.js";
import { bytesOf, commandOf } from "./split.js";

// Each library at one version, with the sha384 hash of its file: the browser refuses a file that differs. Another version needs its
// hash: curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
const XTERM = "https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/lib/xterm.js";
const XTERM_HASH = "sha384-f/1U6Z9wM4D71a5eRXEZnyOTMOvjqxr2XLwh+Go1OvIl3L3tOcvUrzudnhbECwl4";
const XTERM_CSS = "https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/css/xterm.css";
const XTERM_CSS_HASH = "sha384-n2n7twoohnW+d3myBKaUgl7DSiwidw6MkQy9oesGzkPpMjejKRR3XlnD+5yCdtBD";
const FIT = "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/lib/addon-fit.js";
const FIT_HASH = "sha384-txoiwu4RR2GD3qySbaj+BbzibkLbSJRcfqGYMu6z1EqHil4A2dyBiBW5dlacG6OR";
const RETRY = 2000;   // how long after the stream was lost it is opened again, while the shell is wanted

let libs = null;      // the libraries, loaded once

// The libraries give globals. Once the editor has been opened, its loader offers `define` to every script and they would
// register with it instead: it is put aside while they load.
async function loadLibs() {
  const define = window.define;
  if (define) window.define = undefined;
  try {
    await Promise.all([loadStyle(XTERM_CSS, XTERM_CSS_HASH), loadScript(XTERM, XTERM_HASH), loadScript(FIT, FIT_HASH)]);
  } finally {
    if (define) window.define = define;
  }
}

// The terminal's colors are the page's, read when it is made and again when the theme changes
function colors() {
  const style = getComputedStyle(document.body), color = (name) => style.getPropertyValue(name).trim();
  return { background: color("--surface"), foreground: color("--text"), cursor: color("--accent"),
    selectionBackground: color("--surface-3"), selectionForeground: color("--text") };
}

// One terminal on screen: `body` is the element it fills, `id` the playground
export class Shell {
  constructor(body, id, notice) {
    Object.assign(this, { body, id, notice, term: null, fit: null, source: null, offset: 0, ended: false, wanted: false, timer: null,
      chain: Promise.resolve(), pending: "", sending: null, observer: null, themes: null });
  }

  // The shell on screen, once its libraries are there; false when they are not
  async open() {
    if (this.term) return true;
    try {
      await (libs ||= loadLibs().catch((err) => { libs = null; throw err; }));
    } catch {
      this.notice("The terminal needs the internet to load");
      return false;
    }
    if (!this.body.isConnected) return false;
    const term = this.term = new Terminal({ fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace', fontSize: 12.5,
      cursorBlink: true, scrollback: 5000, theme: colors(), allowProposedApi: false });
    this.fit = new FitAddon.FitAddon();
    term.loadAddon(this.fit);
    term.open(this.body);
    term.onData((data) => this.type(data));
    term.onResize(({ rows, cols }) => this.post({ size: [rows, cols] }));
    this.observer = new ResizeObserver(() => this.refit());
    this.observer.observe(this.body);
    this.themes = new MutationObserver(() => { term.options.theme = colors(); });
    this.themes.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { term.options.theme = colors(); });
    this.refit();
    return true;
  }

  // The terminal as big as its element: not while the element has no size (the panel is folded or hidden)
  refit() {
    if (this.term && this.body.clientWidth > 0 && this.body.clientHeight > 0) this.fit.fit();
  }

  focus() {
    this.term?.focus();
  }

  // Reads the shell's output from where it left off, for as long as it is wanted: a stream that is lost is opened again
  async connect() {
    this.wanted = true;
    if (this.source || !(await this.open())) return;
    this.notice("");
    const source = this.source = new EventSource(`/api/playgrounds/${this.id}/terminal?from=${this.offset}`);
    source.addEventListener("out", (e) => {
      const { at, data } = JSON.parse(e.data);
      this.offset = at;
      this.term.write(bytesOf(data));
    });
    source.addEventListener("exit", () => {
      this.ended = true;
      this.close();
      this.term.write("\r\n\x1b[2m[The shell ended. Press Enter to start another.]\x1b[0m\r\n");
    });
    source.onopen = () => this.post({ size: [this.term.rows, this.term.cols] });
    source.onerror = () => {
      this.close();
      if (this.wanted && !this.ended) this.timer = setTimeout(() => this.connect(), RETRY);
    };
  }

  // The stream closed, the shell kept: `wanted` says whether it is opened again
  close() {
    this.source?.close();
    this.source = null;
    clearTimeout(this.timer);
  }

  // What the page no longer shows: its stream and its terminal are gone, and the shell goes on
  dispose() {
    this.wanted = false;
    this.close();
    this.observer?.disconnect();
    this.themes?.disconnect();
    this.term?.dispose();
    this.term = null;
  }

  // What is typed goes to the shell, in the order it was typed; after the shell ended, a key starts a new one
  type(data) {
    if (this.ended) return void this.restart();
    this.pending += data;
    this.sending ||= setTimeout(() => {
      const data = this.pending;
      this.pending = "";
      this.sending = null;
      this.post({ data });
    }, 0);
  }

  post(body) {
    this.chain = this.chain.then(() => api(`/api/playgrounds/${this.id}/terminal`, body)).catch(() => {});
    return this.chain;
  }

  // A new shell in the place of the one that ended
  async restart() {
    this.ended = false;
    this.offset = 0;
    await api(`/api/playgrounds/${this.id}/terminal/close`, {}).catch(() => {});
    this.term?.reset();
    this.connect();
  }

  // A command of the guide, typed and run at the prompt
  async run(text) {
    if (this.ended) await this.restart();
    await this.connect();
    this.focus();
    this.post({ data: commandOf(text) });
  }
}
