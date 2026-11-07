// How far the running job is comes beside the state, in a header of each of its answers (api.state), not in its body: the
// body keeps its tag while a pull brings bytes, and the page draws only the bars and the buttons at work as the measures move.
// A measure that comes or goes draws every page, as a state does.
import assert from "node:assert/strict";
import { test } from "node:test";

import { flush, installDom } from "./dom-stub.mjs";

const page = installDom();
const JOB = { id: "j1", for: "addon:kafka", verb: "start", title: "Start Kafka", done: false, ok: null, phases: [{ name: "Pull", started: 0 }], started: 0 };
const KAFKA = { kind: "addon", id: "kafka", name: "Kafka", state: { key: "starting", label: "Starting" }, progress: "job" };
const STATE = { job: JOB, addons: [KAFKA], setups: [], playgrounds: [], platform: null, services: {} };
let answer = { status: 200, live: null };
const header = (live) => (live ? JSON.stringify(live) : "");
globalThis.fetch = async () => ({
  status: answer.status, ok: true, headers: { get: (name) => (name === "X-Gravitee-Lab-Job" ? header(answer.live) : "") },
  text: async () => "", json: async () => STATE,
});

const { store } = await import("../../../web/js/core.js");
const { paintProgress } = await import("../../../web/js/components/ui.js");
const drawn = [], measured = [];
store.subscribe(() => drawn.push(1));
store.onMeasure(() => measured.push(1));

const live = (progress, phase = progress, detail = `Pulling ${Math.round(progress * 100)}%`) => ({ id: JOB.id, progress, phase, detail });

test("the job, its phase and the item it is for are as far as the measures beside the state say", async () => {
  answer = { status: 200, live: live(0.25, 0.5) };
  await store.refresh();
  assert.deepEqual([store.progress("job"), store.progress("phase"), store.progress("addon:kafka")], [0.25, 0.5, 0.25]);
  assert.equal(store.running().detail, "Pulling 25%");
  answer = { status: 200, live: { ...live(0.3), id: "another" } };   // the measures of another job are not this one's
  await store.refresh();
  assert.deepEqual([store.progress("job"), store.running()], [null, null]);
});

test("measures that move with the state unchanged draw the bars and the buttons, not the pages", async () => {
  answer = { status: 200, live: live(0.1) };
  await store.refresh();
  drawn.length = measured.length = 0;
  for (const progress of [0.2, 0.3, 0.4]) {
    answer = { status: 304, live: live(progress) };
    await store.refresh();
  }
  assert.deepEqual([drawn.length, measured.length, store.progress("job")], [0, 3, 0.4]);
  answer = { status: 304, live: live(0.4) };
  await store.refresh();
  assert.deepEqual([drawn.length, measured.length], [0, 3], "the same measures draw nothing");
});

test("a measure that comes or goes draws every page, as a state does", async () => {
  answer = { status: 200, live: { id: JOB.id, progress: null, phase: null, detail: "" } };
  await store.refresh();
  drawn.length = measured.length = 0;
  answer = { status: 304, live: live(0.1) };
  await store.refresh();
  assert.deepEqual([drawn.length, measured.length], [1, 0]);
  answer = { status: 304, live: null };
  await store.refresh();
  assert.deepEqual([drawn.length, store.progress("job"), store.progress("addon:kafka")], [2, null, null]);
});

test("the bars and the line of the running job are painted from the measures", async () => {
  answer = { status: 200, live: live(0.6, 0.8, "Image redis:8 Pulling") };
  await store.refresh();
  const root = page.doc.createElement("div");
  root.innerHTML = `<span class="progress" data-progress="phase" hidden><span></span></span><div data-job-line></div>`;
  paintProgress(root);
  const [bar, line] = [root.querySelector("[data-progress]"), root.querySelector("[data-job-line]")];
  assert.deepEqual([bar.hidden, bar.getAttribute("aria-valuenow"), bar.firstElementChild.style.width, line.textContent],
    [false, "80", "80%", "Image redis:8 Pulling"]);
  await flush();
});
