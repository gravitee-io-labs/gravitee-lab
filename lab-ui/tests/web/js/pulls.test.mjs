// What the drawer says of an image that is being downloaded (web/js/activity/pulls.js): its bytes in Docker's units, how far it
// is, what all the downloads come to.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installDom } from "./dom-stub.mjs";

installDom({ ids: [] });
const { totals, pullStatus } = await import("../../../web/js/activity/pulls.js");

test("bytes are said in decimal units, as Docker says them, and a percentage waits for the size", () => {
  const status = (current, total) => pullStatus("x", { state: "downloading", current, total });
  const said = (count) => status(count, 2e9).match(/pull-size">([^<]*) \//)[1];
  assert.deepEqual([0, 999, 1000, 412_000, 1_049_000, 12_600_000, 412_000_000, 1_800_000_000].map(said),
    ["0 B", "999 B", "1 kB", "412 kB", "1.0 MB", "13 MB", "412 MB", "1.8 GB"]);
  assert.match(status(256, 1000), /pull-percent">25%</);
  assert.match(status(2000, 1000), /pull-percent">100%</);
});

test("the downloads add up, with their speed while something goes down", () => {
  const images = [
    { image: "a", state: "downloading", current: 100_000_000, total: 400_000_000 },
    { image: "b", state: "downloading", current: 300_000_000, total: 600_000_000 },
    { image: "c", state: "pulled", current: 5_000_000, total: 5_000_000 },
    { image: "d", state: "extracting", current: 90_000_000, total: 90_000_000 },
  ];
  assert.equal(totals({ images, speed: 18_400_000 }), "400 MB of 1.0 GB at 18 MB/s");
  assert.equal(totals({ images, speed: null }), "400 MB of 1.0 GB");
  assert.equal(totals({ images: images.slice(2), speed: 0 }), "");
});

test("an image going down has a bar, a percentage and its bytes, one unpacking says so", () => {
  const down = pullStatus("graviteeio/apim-gateway:4.12.0", { state: "downloading", current: 412_000_000, total: 640_000_000 });
  assert.match(down, /aria-valuenow="64"/);
  assert.match(down, /style="width: 64%"/);
  assert.match(down, /<span class="pull-percent">64%<\/span><span class="pull-size">412 MB \/ 640 MB<\/span>/);
  const unpacking = pullStatus("x", { state: "extracting", current: 5, total: 5 });
  assert.match(unpacking, /aria-valuenow="100"/);
  assert.match(unpacking, /<span class="pull-percent"><\/span><span class="pull-size">Extracting<\/span>/);
  assert.match(pullStatus("x", { state: "downloading", current: 0, total: 0 }), /<span class="pull-size">Pulling<\/span>/);
});
