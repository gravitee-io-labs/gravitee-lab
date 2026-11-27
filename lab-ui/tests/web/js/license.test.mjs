// The packs of the License dialog (web/js/dialogs/license.js), which no other test draws. A feature is in several packs: a pack that
// is not in the license may have every feature of it through others. Each pack says how many of its features the license includes,
// a pack not in it whose features all are reads Covered, a feature of a pack not in it names the packs of the license that give it,
// and Included and Not included sort the packs by what can be used: every feature of the pack, or not. A feature is a line that says
// how many installed plugins need it, and shows them once it is opened, or while a search is typed.
import assert from "node:assert/strict";
import { mock, test } from "node:test";

import { Ev, flush, installDom, reply, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });

// A model of five packs: Agent mesh holds part of Agent management, Enterprise is Enterprise policy and one feature the license lacks,
// OEM's one feature the license lists itself; and a feature in no pack, which the license lists too
const MODEL = { tiers: { universe: ["enterprise-policy"] }, packs: {
  "agent-management": ["a2a-proxy", "llm-proxy", "mcp-proxy"], "agent-mesh": ["a2a-proxy", "llm-proxy"],
  "enterprise-policy": ["xslt"], enterprise: ["xslt", "secret-manager"], oem: ["white-label"] } };
const LICENSE = { present: true, readable: true, valid: true, expired: false, expires: "2027-12-31T00:00:00+00:00", days_left: 399,
  tier: "universe", tier_known: true, tier_packs: ["enterprise-policy"], extra_packs: ["agent-management"],
  packs: ["agent-management", "enterprise-policy"], extra_features: ["white-label", "audit"],
  features: ["a2a-proxy", "audit", "llm-proxy", "mcp-proxy", "white-label", "xslt"], model: MODEL, model_error: null, pull: null };

// The plugins API Management loads that need a feature of the license, by the name of their file
const PLUGINS = { "gravitee-apim-endpoint-a2a-proxy": "a2a-proxy", "gravitee-apim-entrypoint-a2a-proxy": "a2a-proxy", "gravitee-apim-policy-xslt": "xslt" };

installDom({ fetch: async (path) => reply(path.startsWith("/api/license") ? LICENSE
  : path.startsWith("/api/plugins/installed?product=apim") ? { features: PLUGINS } : {}) });
const { store } = await import("../../../web/js/core.js");
store.state = { license: { status: "valid", expires: LICENSE.expires, days_left: 399, applied: true }, products: [{ id: "apim" }],
  platform: { license_var: "GRAVITEE_LICENSE", apply: { needed: false } } };
const { License } = await import("../../../web/js/dialogs/license.js");
const dialog = document.querySelector(".license-dialog");
const $ = (selector) => dialog.querySelector(selector);

// Each pack as its row reads: its mark, its name, its pill, how many of its features are included, and whether that is all of them
const rows = () => $("#license-packs").querySelectorAll(".pack-row").map((row) => [
  row.querySelector(".lic").classList.contains("yes") ? "✓" : "✕", text(row.querySelector(".pack-name")),
  text(row.querySelector(".pill") || { textContent: "" }), text(row.querySelector(".pack-meta")),
  row.querySelector(".pack-meta").classList.contains("full")]);
const filter = (value) => $(`#license-filter [data-filter="${value}"]`).click();
const unfold = (name) => $("#license-packs").querySelectorAll(".pack-row").find((row) => text(row.querySelector(".pack-name")) === name).click();
const packOf = (name) => $("#license-packs").querySelectorAll(".pack").find((li) => text(li.querySelector(".pack-name")) === name);
// A feature of an open pack: its line and the plugins shown under it
const line = (name, feature) => packOf(name).querySelectorAll(".feature").find((li) => text(li.querySelector("code")) === feature);
const pluginsOf = (li) => li.querySelectorAll(".feature-plugins .pill").map(text);
// The features of an open pack: its id, its mark, and the packs said to give it
const features = (name) => {
  const pack = $("#license-packs").querySelectorAll(".pack").find((li) => text(li.querySelector(".pack-name")) === name);
  return pack.querySelectorAll(".feature").map((li) => [text(li.querySelector("code")), li.querySelector(".lic").classList.contains("yes"),
    text(li.querySelector(".feature-via") || { textContent: "" })]);
};

License.show();
await flush();
await flush();

test("each pack says how many of its features the license includes; one not in it whose features all are reads Covered, after those in it", () => {
  assert.deepEqual(rows(), [
    ["✓", "Additional features", "Added", "1 of 1 included", true],
    ["✓", "Agent management", "Added", "3 of 3 included", true],
    ["✓", "Enterprise policy", "Tier", "1 of 1 included", true],
    ["✕", "Agent mesh", "Covered", "2 of 2 included", true],
    ["✕", "OEM", "Covered", "1 of 1 included", true],
    ["✕", "Enterprise", "", "1 of 2 included", false],
  ]);
  const pill = (name) => $("#license-packs").querySelectorAll(".pack-row").find((row) => text(row.querySelector(".pack-name")) === name)
    .querySelector(".pill").getAttribute("data-tip");
  assert.equal(pill("Agent mesh"), "Not in the license: its features come with Agent management");
  assert.equal(pill("OEM"), "Not in the license: the license lists its features itself");
});

test("a feature of a pack not in the license names the packs of the license that give it; one of a pack in it does not", () => {
  for (const name of ["Agent mesh", "Enterprise", "OEM", "Agent management"]) unfold(name);
  assert.deepEqual(features("Agent mesh"), [["a2a-proxy", true, "via Agent management"], ["llm-proxy", true, "via Agent management"]]);
  assert.deepEqual(features("Enterprise"), [["xslt", true, "via Enterprise policy"], ["secret-manager", false, ""]]);
  assert.deepEqual(features("OEM"), [["white-label", true, "added to the license"]]);
  assert.deepEqual(features("Agent management").map(([, , via]) => via), ["", "", ""]);
});

test("a feature is one line that says how many plugins need it; a click shows them under it, in that pack only, and a search shows them", () => {
  const a2a = line("Agent management", "a2a-proxy");
  assert.equal(text(a2a.querySelector(".feature-meta")), "2 plugins");
  assert.deepEqual(pluginsOf(a2a), [], "folded at first");
  assert.equal(a2a.querySelector(".feature-row").getAttribute("aria-expanded"), "false");
  const llm = line("Agent management", "llm-proxy");
  assert.deepEqual([llm.querySelector(".feature-meta"), llm.querySelector("[data-feature]")], [null, null], "no plugin needs it: nothing to open");
  a2a.querySelector(".feature-row").click();
  assert.deepEqual(pluginsOf(line("Agent management", "a2a-proxy")), ["APIM endpoint-a2a-proxy", "APIM entrypoint-a2a-proxy"]);
  assert.equal(line("Agent management", "a2a-proxy").querySelector(".feature-row").getAttribute("aria-expanded"), "true");
  assert.deepEqual(pluginsOf(line("Agent mesh", "a2a-proxy")), [], "the same feature in another pack stays folded");
  line("Agent management", "a2a-proxy").querySelector(".feature-row").click();
  assert.deepEqual(pluginsOf(line("Agent management", "a2a-proxy")), []);
  // A search also reads the names of the plugins: what it finds is shown, with nothing to open
  $("#license-search").value = "policy-xslt";
  $("#license-search").dispatchEvent(new Ev("input"));
  assert.deepEqual(rows().map(([, name]) => name), ["Enterprise policy", "Enterprise"]);
  assert.deepEqual(pluginsOf(line("Enterprise", "xslt")), ["APIM policy-xslt"]);
  assert.equal(line("Enterprise", "xslt").querySelector("[data-feature]"), null);
  $("#license-search").value = "";
  $("#license-search").dispatchEvent(new Ev("input"));
});

test("Included keeps the packs whose features all can be used, Not included those that lack one", () => {
  filter("included");
  assert.deepEqual(rows().map(([, name]) => name), ["Additional features", "Agent management", "Enterprise policy", "Agent mesh", "OEM"]);
  filter("excluded");
  assert.deepEqual(rows().map(([, name]) => name), ["Enterprise"]);
  filter("all");
  assert.equal(rows().length, 6);
});
