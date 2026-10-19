// The edition of a plugin on the Plugins page (web/js/pages/plugins.js), run for real over a small DOM with the plugins of API
// Management as the download site and the images of 4.13 gave them. A plugin is of the Enterprise Edition when the site publishes it in
// its Enterprise folder or when it needs a feature of the license (every tier of the license model is an Enterprise one): the authz
// plugins of Gamma that the images bundle, which the catalog does not list, are, and so is an XSLT policy of the Community folder that
// needs a feature. A plugin of the Enterprise folder that needs none is still one. Any other is Community, one the catalog does not list
// too. Each says so with its tag after its name and under the Enterprise or the Community filter, never under both or neither.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const EE = "graviteeio-ee/apim/plugins", CE = "graviteeio-apim/plugins";
// The catalog: the folder that publishes each plugin says its edition
const CATALOG = [
  { name: "gravitee-policy-oauth2-token-exchange", category: "policies", edition: "enterprise", path: `${EE}/policies/gravitee-policy-oauth2-token-exchange/` },
  { name: "gravitee-policy-ai-routing", category: "policies", edition: "enterprise", path: `${EE}/policies/gravitee-policy-ai-routing/` },
  { name: "gravitee-policy-xslt", category: "policies", edition: "community", path: `${CE}/policies/gravitee-policy-xslt/` },
  { name: "gravitee-policy-oauth2", category: "policies", edition: "community", path: `${CE}/policies/gravitee-policy-oauth2/` },
  { name: "gravitee-resource-oauth2-provider-am", category: "resources", edition: "community", path: `${CE}/resources/gravitee-resource-oauth2-provider-am/` },
];
// What the images bundle, and the license feature each plugin declares in its plugin.properties
const FEATURES = {
  "gravitee-entrypoint-authz": "gamma-authz-reactor", "gravitee-reactor-authz": "gamma-authz-reactor",
  "gravitee-service-authz-pdp": "gamma-authz-service-pdp", "gravitee-policy-authz-pdp-authzen": "gamma-authz-policy-authzen-pdp",
  "gravitee-policy-authz-pep": "gamma-authz-policy-pep", "gravitee-gamma-module-authz": "gamma-authz-module",
  "gravitee-policy-oauth2-token-exchange": "apim-policy-token-exchange", "gravitee-policy-xslt": "apim-policy-xslt",
};
const BUNDLED = [...Object.keys(FEATURES), "gravitee-policy-ai-routing", "gravitee-policy-oauth2", "gravitee-resource-oauth2-provider-am",
  "gravitee-apim-gateway-services-sync"];
const NEEDS = { note: "Needs a license", note_tone: "warn", note_title: "Uses: Authorization management", note_target: null, note_file: ".env",
  note_causes: [{ kind: "license", features: ["Authorization management"] }] };
const INSTALLED = {
  bundled: Object.fromEntries(BUNDLED.map((name) => [name, { "apim-gateway": "1.0.0", "apim-management-api": "1.0.0" }])),
  bundled_error: null, pull: null, ext: {}, services: ["apim-gateway", "apim-management-api"], features: FEATURES,
  feature_by_id: {}, licensed: Object.fromEntries(Object.keys(FEATURES).map((name) => [name, NEEDS])),
};

const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const page = installDom({
  ids,
  fetch: async (path) => reply(path.startsWith("/api/plugins/catalog") ? { plugins: CATALOG }
    : path.startsWith("/api/plugins/installed") ? INSTALLED : path.startsWith("/api/plugins/versions") ? { versions: [] } : {}),
});
page.el("combo-pop").innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { store } = await import("../../../web/js/core.js");
const { pluginsPage } = await import("../../../web/js/pages/plugins.js");
store.state = {
  blind: false, wait: "", docker: { error: null }, job: null, license: { status: "none", expires: null },
  products: [{ id: "apim", name: "API Management" }], services: {}, playgrounds: [], addons: [], setups: [],
  platform: { groups: [{ id: "api-management", name: "API Management", product: "apim" }], apply: { needed: false }, file: "docker-compose.yml" },
};

const section = page.doc.createElement("section");
page.doc.body.append(section);
pluginsPage.init(section);
pluginsPage.show();
pluginsPage.render(store.state);
for (let i = 0; i < 5; i++) await flush();

// The rows shown, by their artifact name, with the tag after the name (what is seen, then what a screen reader hears) and the note in
// their State
const said = (el) => (el ? text(el) : "");
const shown = () => section.querySelectorAll("#plugins tr[data-name]").map((row) => ({
  name: row.getAttribute("data-name"),
  tag: [said(row.querySelector(".list-name .pill [aria-hidden]")), said(row.querySelector(".list-name .pill .sr-only"))].join("|"),
  note: said(row.querySelector(".list-state .note")),
}));
async function filter(edition, q = "") {
  const search = section.querySelector("#plugin-search");
  search.value = q;
  search.dispatchEvent(new Event("input"));
  section.querySelector(`#plugin-edition [data-edition="${edition}"]`).click();
  await flush();
  return shown();
}
const TAG = "EE|, Enterprise Edition";
const names = (rows) => rows.map((row) => row.name).sort();
const ENTERPRISE = [...Object.keys(FEATURES), "gravitee-policy-ai-routing"].sort();
const COMMUNITY = ["gravitee-apim-gateway-services-sync", "gravitee-policy-oauth2", "gravitee-resource-oauth2-provider-am"];

test("a plugin that needs a license is an Enterprise Edition plugin: the authz plugins the images bundle have the tag", async () => {
  const rows = await filter("all", "auth");
  for (const name of ["gravitee-entrypoint-authz", "gravitee-reactor-authz", "gravitee-service-authz-pdp", "gravitee-policy-authz-pdp-authzen",
    "gravitee-policy-authz-pep", "gravitee-gamma-module-authz", "gravitee-policy-oauth2-token-exchange"]) {
    const row = rows.find((r) => r.name === name);
    assert.equal(row.tag, TAG, `${name} is tagged, its words said to a screen reader`);
    assert.equal(row.note, "Needs a license", `${name} still says what the license lacks`);
  }
  for (const name of ["gravitee-policy-oauth2", "gravitee-resource-oauth2-provider-am"]) {
    assert.equal(rows.find((r) => r.name === name).tag, "|", `${name} needs no license: Community`);
  }
});

test("a plugin of the Community folder that needs a feature is Enterprise; one of the Enterprise folder that needs none still is", async () => {
  const rows = await filter("all");
  assert.equal(rows.find((r) => r.name === "gravitee-policy-xslt").tag, TAG);
  assert.equal(rows.find((r) => r.name === "gravitee-policy-ai-routing").tag, TAG);
  assert.equal(rows.find((r) => r.name === "gravitee-policy-ai-routing").note, "", "it needs no license: no note");
});

test("the Enterprise filter keeps every plugin with the tag and the Community filter every other, each plugin under one of them", async () => {
  const enterprise = await filter("enterprise"), community = await filter("community"), everything = await filter("all");
  assert.deepEqual(names(enterprise), ENTERPRISE);
  assert.deepEqual(names(community), COMMUNITY, "a bundled plugin the catalog does not list, and that needs no license, is Community");
  assert.ok(enterprise.every((row) => row.tag === TAG) && community.every((row) => row.tag === "|"));
  assert.deepEqual(names(everything), [...ENTERPRISE, ...COMMUNITY].sort(), "no plugin is under neither filter");
  assert.deepEqual(names(await filter("enterprise", "auth")), ["gravitee-entrypoint-authz", "gravitee-gamma-module-authz",
    "gravitee-policy-authz-pdp-authzen", "gravitee-policy-authz-pep", "gravitee-policy-oauth2-token-exchange", "gravitee-reactor-authz",
    "gravitee-service-authz-pdp"]);
  assert.deepEqual(names(await filter("community", "auth")), ["gravitee-policy-oauth2", "gravitee-resource-oauth2-provider-am"]);
  await filter("all");
});
