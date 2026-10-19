// Where an item runs (web/js/platform/places.js): a playground is on the place chosen on any page, an add-on or a setup is on the place
// chosen for it; each says why a place is not for it by the place's own words, by its id or by its kind and its id.
import assert from "node:assert/strict";
import { test } from "node:test";

import { readFileSync } from "node:fs";

import { installDom } from "./dom-stub.mjs";

const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));
installDom({ ids });
const { store } = await import("../../../web/js/core.js");
const places = await import("../../../web/js/platform/places.js");

const DEV = { id: "my-account/dev", name: "Development (My Account)", state: "ready", cannot: { "addon:kafka-desk": "Changes this machine's Management API", bank: "Needs Gamma" } };
const TEST = { id: "my-account/test", name: "Test (My Account)", state: "ready", cannot: {} };
const state = (place) => ({ platforms: [{ id: "my-account", name: "My Account" }], core: { place } });   // `platforms` comes from the state
store.places = [DEV, TEST];
store.placesRead = true;
store.state = state(null);

const kafka = { kind: "addon", id: "kafka", deploys_to: ["cloud"] };
const console_ = { kind: "addon", id: "kafka-desk", deploys_to: [] };
const bank = { kind: "playground", id: "bank", deploys_to: [] };
const hotels = { kind: "playground", id: "hotels", deploys_to: ["cloud"] };

test("a place says why an item does not go there by its own key: a playground's id, an add-on's kind and id", () => {
  assert.equal(places.whyNot(console_, DEV), "Changes this machine's Management API");
  assert.equal(places.whyNot(bank, DEV), "Needs Gamma");
  assert.equal(places.whyNot(kafka, DEV), "");
  assert.equal(places.goes(kafka, DEV), true);
  assert.equal(places.goes(console_, DEV), false);
  // An add-on named like a playground is not the playground
  assert.equal(places.whyNot({ kind: "addon", id: "bank" }, DEV), "");
});

test("an add-on is on This machine until a place is chosen for it, whatever the playgrounds do", () => {
  assert.equal(places.chosenPlace(kafka), null);
  places.keepPlace(DEV.id);   // chosen on a playground's page
  assert.equal(places.chosenPlace(hotels).id, DEV.id);
  assert.equal(places.chosenPlace(kafka), null, "the choice of a playground is not the add-on's");
  places.keepItemPlace(kafka, TEST.id);
  assert.equal(places.chosenPlace(kafka).id, TEST.id);
  assert.equal(places.chosenPlace(hotels).id, DEV.id, "and the add-on's is not the playground's");
  places.keepItemPlace(kafka, places.MACHINE);
  assert.equal(places.chosenPlace(kafka), null, "This machine is a choice too");
});

test("an add-on goes where API Management runs when it was chosen nothing, if it can", () => {
  places.forgetPlace();
  store.state = state(DEV.id);
  places.keepItemPlace(kafka, null);
  assert.equal(places.chosenPlace(console_), null, "it cannot go there: it stays here");
  places.keepItemPlace(console_, null);
  assert.equal(places.chosenPlace({ kind: "addon", id: "kiosk", deploys_to: ["cloud"] }).id, DEV.id);
});
