// The rows of the job drawer (web/js/activity/jobs.js) are kept while the downloads go on: only what changed in a row is written.
// A row drawn again would start its spinner over at every byte that arrives, and the spinner would never turn. The job comes the way
// the page gets one that runs: in the state, followed by its stream of lines and of downloads.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids });

const streams = [];
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.listeners = {}; streams.push(this); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  close() {}
};
const running = (id) => ({ job: { id, title: "Start the stack", done: false, started: 0 }, playgrounds: [], setups: [], addons: [], platform: {}, services: {} });
globalThis.fetch = async () => ({ status: 200, ok: true, headers: { get: () => "" }, json: async () => running("j1") });

const { store } = await import("../../../web/js/core.js");
const { initJobs } = await import("../../../web/js/activity/jobs.js");
page.el("drawer").hidden = true;
initJobs();
await store.refresh();
await flush();

const stream = streams.at(-1);
const send = (lines) => stream.onmessage({ data: JSON.stringify(lines) });
const pulls = (...rows) => stream.listeners.pulls({ data: JSON.stringify({ speed: 4e6, images: rows.map(([image, state, current, total]) => ({ image, state, current, total })) }) });
const steps = () => page.el("job-steps");

test("the job is followed on its stream", () => {
  assert.equal(stream.url, "/api/jobs/j1");
});

test("a row keeps its spinner while the bytes of its image change", () => {
  send(["Image redis:8 Pulling", "Image mongo:8 Pulling"]);
  pulls(["redis:8", "downloading", 10, 100], ["mongo:8", "downloading", 5, 100]);
  const before = [...steps().querySelectorAll(".job-state")];
  assert.equal(before.length, 2);
  pulls(["redis:8", "downloading", 60, 100], ["mongo:8", "downloading", 40, 100]);
  assert.deepEqual([...steps().querySelectorAll(".job-state")], before, "the same elements, so the animation goes on");
  assert.match(steps().textContent, /60%/);
  assert.match(steps().textContent, /40%/);
});

test("a row that is done turns its spinner into its state, in the same element", () => {
  const [spinner] = steps().querySelectorAll(".job-state");
  assert.ok(!spinner.classList.contains("ok"));
  send(["Image redis:8 Pulled"]);
  pulls(["redis:8", "pulled", 100, 100], ["mongo:8", "downloading", 70, 100]);
  assert.equal(steps().querySelectorAll(".job-state")[0], spinner);
  assert.ok(spinner.classList.contains("ok"));
  assert.match(steps().textContent, /Pulled/);
});

test("a container is listed after the images", () => {
  send(["Container gio-redis Started"]);
  const names = [...steps().querySelectorAll(".job-group")].map((el) => el.textContent.trim());
  assert.equal(names.length, 2);
  assert.ok(names[0].startsWith("Images") && names[1].startsWith("Containers"), names.join(" | "));
});
