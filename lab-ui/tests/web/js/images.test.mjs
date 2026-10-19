// The Images page (web/js/pages/images.js), run for real over a small DOM: the tags its pickers offer. A version that
// other images take is chosen among the plain tags, so that no choice makes a tag that does not exist for them (the
// gateway's is the version and "-debian"); the tags of a component that stands alone come in their variants.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// A component as /api/images lists it: `configured` are the images its services run, `follows` what its version is made of
function component(id, label, configured, { follows = [], kind = "gravitee" } = {}) {
  return {
    id, label, detail: label, kind, version_key: id, version_default: "1", registry_key: kind === "gravitee" ? `${id}_REGISTRY` : null,
    group: kind === "gravitee" ? "api-management" : null, registry_default: "graviteeio", host: null, repository: configured[0].replace(/:[^:/]+$/, ""), source: null, services: [id.toLowerCase()],
    owner: { kind: "platform", id: "api-management", name: "API Management" }, follows, version: "1", registry: "graviteeio",
    version_resolved: "1", registry_resolved: "graviteeio", configured, running: [], pulled: true, enabled: true,
  };
}
const COMPONENTS = [
  component("APIM_VERSION", "Management API", ["graviteeio/apim-management-api:1", "graviteeio/apim-management-ui:1"]),
  component("APIM_GATEWAY_VERSION", "Gateway", ["graviteeio/apim-gateway:1-debian"],
    { follows: [{ id: "APIM_VERSION", version: "${APIM_VERSION}-debian", registry: "${APIM_REGISTRY}" }] }),
  component("AM_VERSION", "Access Management", ["graviteeio/am-gateway:1", "graviteeio/am-management-ui:1"]),
  component("REDIS_VERSION", "Redis", ["redis:1-alpine"], { kind: "third-party" }),
  component("ELASTICSEARCH_VERSION", "Elasticsearch", ["elasticsearch:1"], { kind: "third-party" }),
  component("KIOSK_VERSION", "Kiosk", ["kiosk:1"], { kind: "third-party", follows: [{ id: "ELASTICSEARCH_VERSION", version: "${ELASTICSEARCH_VERSION}" }] }),
];

const LISTED = ["4.13.0-alpha-debian", "4.13.0-alpha.4-debian", "4.13.0-alpha.4-chainguard", "4.13.0-alpha.4", "4.12.21", "latest-debian", "latest",
  "8.10.2-alpine", "8.10.2", "ltsc2022-windowsservercore", "9.5.3"];
const PLAIN = ["4.12.21", "4.13.0-alpha.4", "8.10.2", "9.5.3", "latest"];

const page = installDom({
  ids,
  stored: { thirdPartyOpen: "1" },   // the third-party section is unfolded
  fetch: async (path) => {
    const body = path.startsWith("/api/images") ? { components: COMPONENTS, registries: [], azure_in_use: false }
      : path.startsWith("/api/tags") ? { tags: LISTED.map((name) => ({ name, updated: null })), local: [] } : { host: null };
    return { ok: true, status: 200, json: async () => body };
  },
});
page.el("combo-pop").innerHTML = '<div class="combo-search"><input id="combo-input"></div><div id="combo-bar"></div><div id="combo-list"></div><div id="combo-foot"></div>';
const { store } = await import("../../../web/js/core.js");
const { imagesPage } = await import("../../../web/js/pages/images.js");
store.state = { blind: false, wait: "", docker: { error: null }, services: {}, platform: { name: "Gravitee Platform", groups: [], apply: { needed: false } }, playgrounds: [], addons: [], setups: [] };

const section = page.doc.createElement("section");
page.doc.body.append(section);
imagesPage.init(section);
imagesPage.show([]);
imagesPage.render(store.state);
await flush();

// What a component's picker lists, and the systems it offers to narrow the list to
async function offered(id) {
  const button = section.querySelector(`tr[data-component="${id}"] [data-version]`);
  button.click();
  await flush();
  const tags = page.doc.querySelectorAll("#combo-list .combo-option .name").map((name) => text(name));
  const systems = page.doc.querySelectorAll("#combo-bar [data-segment]").map((segment) => segment.getAttribute("data-segment"));
  button.click();   // closes it
  return { tags: tags.sort(), systems };
}

test("a version that other images take is chosen among the plain tags", async () => {
  // The gateway is made of it and "-debian": a tag of its own system would make "4.13.0-alpha.4-debian-debian"
  assert.deepEqual(await offered("APIM_VERSION"), { tags: PLAIN, systems: [] });
});

test("so is a version that images of several repositories take, with no other component made of it", async () => {
  assert.deepEqual(await offered("AM_VERSION"), { tags: PLAIN, systems: [] });
});

test("a component that stands alone keeps its systems, and is never offered images for Windows", async () => {
  const everything = LISTED.filter((name) => !/windowsservercore/.test(name));
  const systems = ["all", "chainguard", "debian", "alpine", "default"];
  // The gateway's picker also offers to take the version of Management API, with its own system added
  assert.deepEqual(await offered("APIM_GATEWAY_VERSION"), { tags: [...everything, "Management API"].sort(), systems });
  assert.deepEqual(await offered("REDIS_VERSION"), { tags: everything.sort(), systems });
});

test("a component that another one takes as it is keeps its systems", async () => {
  // Kiosk's version is Elasticsearch's with nothing added to it: no tag of Elasticsearch makes one that does not exist
  const { tags } = await offered("ELASTICSEARCH_VERSION");
  assert.ok(tags.includes("8.10.2-alpine"));
});

test("what runs behind what is saved has the badge in the State cell, with the server's reason of its services, or what it can tell alone", async () => {
  const why = "4.12.20 is saved, 4.13.0-alpha.4 still runs.";
  const [api, , , redis] = COMPONENTS;
  api.services = ["apim-management-api"];
  Object.assign(api, { running: ["graviteeio/apim-management-api:1"] });
  redis.running = ["redis:2-alpine"];   // runs another image than the one saved, and no service says why
  store.state = { ...store.state, services: { "apim-management-api": { behind: true, note_apply: true, note_title: why, running_tag: "4.13.0-alpha.4", state: { running: true } } } };
  imagesPage.render(store.state);
  await flush();
  const badge = (id) => section.querySelector(`tr[data-component="${id}"] .tip-btn`);
  assert.equal(badge("APIM_VERSION").getAttribute("aria-label"), `Apply needed: ${why}`);
  assert.equal(badge("REDIS_VERSION").getAttribute("aria-label"), "Apply needed: The saved image differs from the one that runs.");
  assert.equal(badge("AM_VERSION"), null, "what runs as saved has none");
  // The State cell says no more of the image: whether it is on this machine is said with the version, as the list of versions says it
  const under = (id) => [...section.querySelectorAll(`tr[data-component="${id}"] .list-state .list-note`)].map(text);
  const pulled = (id) => [...section.querySelectorAll(`tr[data-component="${id}"] [data-version] .pill`)].map(text);
  assert.deepEqual([under("APIM_VERSION"), under("REDIS_VERSION"), pulled("APIM_VERSION")], [[], [], []]);
  api.pulled = false;
  imagesPage.render(store.state);
  await flush();
  assert.deepEqual([under("APIM_VERSION"), pulled("APIM_VERSION")], [[], ["Not pulled"]], "with the version, not in the State cell");
  assert.ok(![...section.querySelectorAll(".pill")].some((pill) => text(pill) === "Apply needed" && !pill.classList.contains("tip-btn")), "never the words alone");
});

test("the platform's components come first under its band, its own add-ons' under a band of their own, then what is not Gravitee's, and each band folds", async () => {
  const gravitee = { kind: "addon", id: "federation", name: "API Federation" }, other = { kind: "addon", id: "observability", name: "Jade" };
  const agent = { ...component("AGENT_VERSION", "Apigee X agent", ["graviteeio/agent:1"]), group: null, owner: gravitee };
  const tools = { ...component("TOOLS_VERSION", "Operator installer", ["tools:1"], { kind: "third-party" }), group: null, owner: gravitee };   // an image of another maker, run by the platform's add-on
  const jade = { ...component("JADE_VERSION", "Jade", ["jade:1"], { kind: "third-party" }), group: null, owner: other };
  const kafka = { ...component("KAFKA_VERSION", "Kafka UI", ["kafka:1"]), group: null, owner: other };   // Gravitee's image in another maker's add-on stays with that add-on's
  const hybrid = { ...component("HYBRID_VERSION", "Hybrid gateway", ["graviteeio/apim-gateway:1"]), owner: gravitee };   // an add-on's gateway: a gateway of its group
  COMPONENTS.push(agent, tools, jade, kafka, hybrid);
  const was = store.state;
  store.state = { ...was, addons: [{ id: "federation", category: "Gravitee Platform" }, { id: "observability", category: "Observability" }] };
  imagesPage.show([]);
  imagesPage.render(store.state);
  await flush();
  const lines = () => section.querySelectorAll("#images-rows tr").map((tr) => tr.getAttribute("data-component") || text(tr).trim());
  const platform = ["APIM_VERSION", "APIM_GATEWAY_VERSION", "AM_VERSION", "HYBRID_VERSION"];
  assert.deepEqual(lines(), ["Gravitee Platform", ...platform, "Add-ons", "AGENT_VERSION", "TOOLS_VERSION",
    "Third-party", "REDIS_VERSION", "ELASTICSEARCH_VERSION", "KIOSK_VERSION", "JADE_VERSION", "KAFKA_VERSION"]);
  // Folding the add-ons hides theirs; folding the platform hides the add-ons' band too
  section.querySelector('[data-fold="addons"]').click();
  // Folded, a band sums up what is on under it
  assert.deepEqual(lines().slice(5, 7), ["Add-onsApigee X agent 1Operator installer 1", "Third-party"]);
  section.querySelector('[data-fold="platform"]').click();
  assert.deepEqual(lines().slice(0, 2).map((line) => line.replace(/^(Gravitee Platform).+/, "$1")), ["Gravitee Platform", "Third-party"]);
  for (const id of ["platform", "addons"]) section.querySelector(`[data-fold="${id}"]`)?.click();
  assert.equal(lines()[1], "APIM_VERSION", "unfolded again");
  COMPONENTS.length -= 5;
  store.state = was;
});
