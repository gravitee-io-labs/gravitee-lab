// The services picker of Logs (web/js/pages/logs.js), run for real over a small DOM: its sections are the platform's groups, then the
// playground and the add-ons that are on or run. The gateway of an add-on belongs to the group that runs its image (the state's
// `gateway.group`, which the server tells from the images): the Hybrid gateway and its Redis are listed in API Management, after its
// own services, the gateway first, and in no section of their own; another add-on keeps its own.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
const page = installDom({ ids });
page.el("combo-pop").innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
globalThis.EventSource = class { close() {} addEventListener() {} };
// The stub's selectors do not know :checked, which the page asks of its levels
const all = page.doc.querySelectorAll;
page.doc.querySelectorAll = (selector) => all(selector.replace(":checked", ""));

const { store } = await import("../../../web/js/core.js");
const { Combo } = await import("../../../web/js/components/combo.js");
const { logsPage, selectLogs } = await import("../../../web/js/pages/logs.js");
Combo.init();

const RUNNING = { key: "running", label: "Running", running: true };
const OFF = { key: "absent", label: "Not created", running: false };
const service = (label, state = RUNNING, registry = "graviteeio") => ({ label, name: label, state, registry });
const group = (id, name, services) => ({ id, name, services });
const addon = (id, name, services, more = {}) => ({ kind: "addon", id, name, services, on: false, up: true, gateway: null, ...more });
const HYBRID = addon("hybrid", "Hybrid gateway", ["hybrid-redis", "apim-hybrid-gateway"], {
  gateway: { platform: "cloud", service: "apim-hybrid-gateway", token: "HYBRID_CLOUD_TOKEN", domains: [], group: "api-management" } });

function state(addons) {
  store.state = {
    blind: false, wait: "", docker: { error: null },
    platform: { groups: [group("api-management", "API Management", ["apim-gateway", "apim-management-api"]),
      group("databases", "Databases", ["mongodb", "redis"])], start: { needed: false }, apply: { needed: false } },
    playgrounds: [{ kind: "playground", id: "acme-hostels-api", name: "Acme Hostels API", services: ["acme-hostels-api"], on: false, up: true }],
    setups: [], addons,
    services: {
      "apim-gateway": service("Gateway"), "apim-management-api": service("Management API"),
      mongodb: service("MongoDB", RUNNING, null), redis: service("Redis", RUNNING, null),
      "acme-hostels-api": service("Acme Hostel API", RUNNING, null),
      "apim-hybrid-gateway": service("Hybrid gateway"), "hybrid-redis": service("Hybrid gateway Redis", RUNNING, null),
      jade: service("Jade", RUNNING, null), "jade-off": service("Jade agent", OFF, null),
    },
  };
}

const section = page.doc.createElement("section");
page.doc.body.append(section);
logsPage.init(section);

// The picker as it lists: each section's title and the ids of its services, in order
async function sections() {
  section.querySelector("#log-services").click();
  await flush();
  const listed = [];
  for (const el of page.doc.querySelector("#combo-list").children) {
    if (el.classList.contains("combo-group")) listed.push([text(el), []]);
    else if (el.classList.contains("combo-subgroup")) listed.at(-1)[1].push(`# ${text(el)}`);   // a title inside the section
    else if (el.classList.contains("combo-option")) listed.at(-1)[1].push(text(el.querySelector(".age")));
  }
  Combo.close();
  return listed;
}

test("the Hybrid gateway and its Redis are a part of the one API Management section, under a title of their own, its gateway first, and in no section of their own", async () => {
  state([HYBRID, addon("jade", "Jade", ["jade", "jade-off"])]);
  logsPage.render(store.state);
  assert.deepEqual(await sections(), [
    ["API Management", ["apim-gateway", "apim-management-api", "# Hybrid gateway", "apim-hybrid-gateway", "hybrid-redis"]],
    ["Databases", ["mongodb", "redis"]],   // the Redis of the hybrid gateway follows its gateway, not the image it runs
    ["Acme Hostels API", ["acme-hostels-api"]],
    ["Jade", ["jade"]],   // an add-on of no group keeps its section; a service with no container is not offered
  ]);
});

test("its names are the services' own, so that the two gateways of API Management are told apart", async () => {
  state([HYBRID]);
  section.querySelector("#log-services").click();
  await flush();
  const names = page.doc.querySelectorAll("#combo-list .combo-option .name").map(text);
  Combo.close();
  assert.deepEqual(names.slice(0, 4), ["Gateway", "Management API", "Hybrid gateway", "Hybrid gateway Redis"]);
});

test("an add-on that is off and runs nothing is not listed, in its group or anywhere; one whose service was chosen still is", async () => {
  const off = { ...HYBRID, up: false };
  state([off]);
  assert.deepEqual((await sections())[0], ["API Management", ["apim-gateway", "apim-management-api"]]);
  assert.ok(!(await sections()).flat(2).some((one) => /Hybrid gateway/.test(one)));
  selectLogs(["hybrid-redis"]);   // chosen before it stopped: it stays, to be unticked, where it belongs
  assert.deepEqual((await sections())[0], ["API Management", ["apim-gateway", "apim-management-api", "# Hybrid gateway", "apim-hybrid-gateway", "hybrid-redis"]]);
  selectLogs([]);
});

test("the section of the gateway says whom it serves, the account and the environment, when a gateway runs for one", async () => {
  state([HYBRID]);
  store.state.platforms = [{ id: "cloud-eu", name: "Acme Cloud", kind: "cloud", url: "https://eu.cloud.gravitee.io" }];
  store.cloud.checks.set("cloud-eu", { state: "ok", environments: [{ id: "e1", name: "Development" }, { id: "e2", name: "Test" }],
    gateways: [{ id: "g0", name: "other", environmentId: "e2", here: false }, { id: "g1", name: "lab", environmentId: "e1", here: true }] });
  assert.deepEqual((await sections())[0], ["API Management", ["apim-gateway", "apim-management-api", "# Hybrid gateway (Acme Cloud, Development)", "apim-hybrid-gateway", "hybrid-redis"]]);
  // No gateway runs here: the section names the add-on alone
  store.cloud.checks.set("cloud-eu", { state: "ok", environments: [], gateways: [{ id: "g1", name: "lab", environmentId: "e1", here: false }] });
  assert.equal((await sections())[0][1][2], "# Hybrid gateway");
  store.cloud.checks.delete("cloud-eu");
});

test("a gateway whose image no group runs keeps the add-on's own section", async () => {
  state([{ ...HYBRID, gateway: { ...HYBRID.gateway, group: null } }]);
  const listed = await sections();
  assert.deepEqual(listed[0], ["API Management", ["apim-gateway", "apim-management-api"]]);
  assert.deepEqual(listed.at(-1), ["Hybrid gateway", ["hybrid-redis", "apim-hybrid-gateway"]]);
});
