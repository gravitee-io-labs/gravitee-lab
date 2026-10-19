// The router (web/js/core.js): the page that opens with no address, or one that is none, is the Dashboard; the old address of the Containers
// (#/stack) leads to them, in place of its own entry of the history, so that Back does not come back to it. The ids that an address holds (the
// first catalog's bare, this machine's `local/<name>`, any other catalog's `@<namespace>/<name>`), the keys the page splits at their first colon,
// and what the store knows of a catalog.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installDom } from "./dom-stub.mjs";
import { catalog, content, lab } from "./fixtures.mjs";

const page = installDom({});
const { ID_PATTERN, hrefOf, router, splitKey, store } = await import("../../../web/js/core.js");

const shown = [];   // the pages the router showed, in order
for (const [id, pattern] of [["dashboard", /^#\/dashboard$/], ["stack", /^#\/containers$/], ["logs", /^#\/logs$/]]) {
  router.register({ id, pattern, show: () => shown.push(id) });
}
store.state = {};
const hashes = () => page.session.entries.map((entry) => entry.hash);

test("the page that opens with no address is the Dashboard", () => {
  router.start();
  assert.deepEqual(shown, ["dashboard"]);
  assert.deepEqual(hashes(), ["#/dashboard"], "its address is written in place of the empty one");
});

test("the old address of the Containers leads to them, in place of its own entry", () => {
  location.hash = "#/stack";
  assert.deepEqual(shown, ["dashboard", "stack"]);
  assert.deepEqual(hashes(), ["#/dashboard", "#/containers"]);
  assert.equal(location.hash, "#/containers");
});

test("an address that is no page leads to the Dashboard too, the old address of this page among them", () => {
  location.hash = "#/logs";
  location.hash = "#/nowhere";
  assert.equal(shown.at(-1), "dashboard");
  assert.equal(location.hash, "#/dashboard");
  location.hash = "#/logs";
  location.hash = "#/over" + "view";   // what the page was named, and may be bookmarked as
  assert.equal(shown.at(-1), "dashboard");
  assert.equal(location.hash, "#/dashboard");
});

// --- The id of an item in an address -----------------------------------------------------------------------------------------

const ID = new RegExp(`^${ID_PATTERN}$`);

test("an id is a bare one, this machine's local/<name> or another catalog's @<namespace>/<name>", () => {
  for (const id of ["kafka", "acme-bakery", "4", "a", "x-1-y", "local/x", "local/my-demo", "@acme-hub/x", "@acme-hub/mock-bank", "@ab/x", "@a1/b2",
    `@a${"b".repeat(22)}c/x`]) {
    assert.ok(ID.test(id), id);
  }
});

test("what an id is not: a path out of the catalog, a namespace too short or too long, another case, a colon, a line, a second slash", () => {
  const namespace = "b".repeat(23);   // two to 24 characters is a namespace
  for (const id of ["@acme-hub/../kafka", "@acme-hub/", "@/x", "@a/x", `@a${namespace}c/x`, "@acme-hub/x/y", "@acme_hub/x", "@ACME/x", "@acme-hub", "@",
    "local/../kafka", "local/", "/local/x", "local//x", "Local/x", "LOCAL/x", "local/X", "local/x/y", "x/y", "../kafka", "kafka/", "kafka/..",
    "kaf ka", "kafka:x", "local/x:y", "kafka\n", "kafka\u0000", "@acme-hub/x\n", "\nkafka", "", "kaf_ka", "kafka.", "kafka%2Fx", "kafka\\x",
    "@-ab/x", "@acme-/x", "@1ab/x", "local@acme-hub/x"]) {
    assert.ok(!ID.test(id), JSON.stringify(id));
  }
  // That a namespace is one the Lab reserves (@local/x, @official/x), or that an id names the first catalog by its namespace, is the server's
  // to refuse: the pattern is the syntax of an id, and such an address leads to an item that does not exist
  assert.ok(ID.test("@local/x"));
});

// The routes of the pages, as the pages build them with the pattern
const LIST = (kind) => new RegExp(`^#/${kind}s(?:/(${ID_PATTERN}))?$`);
const PLAYGROUND = new RegExp(`^#/playgrounds/(${ID_PATTERN})(?:/([a-z0-9-]+))?$`);

test("a list opens a row of any id, and the page of a playground takes its guide's step after the id, whichever its form", () => {
  const found = (pattern, hash) => pattern.exec(hash)?.slice(1);
  assert.deepEqual(found(LIST("addon"), "#/addons"), [undefined]);
  assert.deepEqual(found(LIST("addon"), "#/addons/kafka"), ["kafka"]);
  assert.deepEqual(found(LIST("setup"), "#/setups/local/ai-agents"), ["local/ai-agents"]);
  assert.deepEqual(found(LIST("addon"), "#/addons/@acme-hub/mock-bank"), ["@acme-hub/mock-bank"]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/acme-bakery"), ["acme-bakery", undefined]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/acme-bakery/3"), ["acme-bakery", "3"]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/local/demo"), ["local/demo", undefined], "the folder of this machine is no id and a step");
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/local/demo/2"), ["local/demo", "2"]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/@acme-hub/bank"), ["@acme-hub/bank", undefined]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/@acme-hub/bank/3"), ["@acme-hub/bank", "3"]);
  assert.deepEqual(found(PLAYGROUND, "#/playgrounds/@acme-hub/bank/getting-started"), ["@acme-hub/bank", "getting-started"]);
  for (const hash of ["#/playgrounds/@acme-hub/../kafka", "#/playgrounds/@acme-hub/", "#/playgrounds/@a/bank", "#/playgrounds/@acme-hub/bank/3/4",
    "#/playgrounds/local/", "#/playgrounds/", "#/playgrounds/@ACME/bank", "#/playgrounds/acme-bakery/Step", "#/playgrounds/acme-bakery/3/",
    "#/playgrounds/%40acme-hub/bank"]) {
    assert.equal(found(PLAYGROUND, hash), undefined, hash);
  }
  for (const hash of ["#/addons/@acme-hub/", "#/addons/@acme-hub/x/y", "#/addons/../x", "#/addons/Kafka", "#/addons/local/"]) {
    assert.equal(found(LIST("addon"), hash), undefined, hash);
  }
});

test("the router hands a page the id it names, whole, with its step", () => {
  const seen = [];
  router.register({ id: "playground", pattern: PLAYGROUND, show: (params) => seen.push(params) });
  router.register({ id: "addons", pattern: LIST("addon"), show: (params) => seen.push(params) });
  location.hash = "#/playgrounds/@acme-hub/bank/3";
  location.hash = "#/addons/@acme-hub/mock-bank";
  location.hash = "#/playgrounds/local/demo";
  assert.deepEqual(seen, [["@acme-hub/bank", "3"], ["@acme-hub/mock-bank"], ["local/demo", undefined]]);
});

test("the address of a catalog is the page of the catalogs, with its namespace once it is a row, and of an item whatever its id", () => {
  assert.equal(hrefOf({ kind: "catalog", id: "acme-hub" }), "#/catalogs/acme-hub");
  assert.equal(hrefOf({ kind: "catalog", id: null }), "#/catalogs", "a note that needs a catalog not yet added leads to the page");
  assert.equal(hrefOf({ kind: "catalog" }), "#/catalogs");
  assert.equal(hrefOf({ kind: "platform" }), "#/cloud");
  assert.equal(hrefOf({ kind: "playground", id: "@acme-hub/bank" }), "#/playgrounds/@acme-hub/bank");
  assert.equal(hrefOf({ kind: "addon", id: "local/whoami" }), "#/addons/local/whoami");
});

// --- A key of the page --------------------------------------------------------------------------------------------------------

test("a key is split at its first colon, so that what follows is whole", () => {
  assert.deepEqual(splitKey("playground:@acme-hub/bank"), ["playground", "@acme-hub/bank"]);
  assert.deepEqual(splitKey("addon:kafka"), ["addon", "kafka"]);
  assert.deepEqual(splitKey("addon:local/whoami"), ["addon", "local/whoami"]);
  assert.deepEqual(splitKey("catalog-op:acme-hub"), ["catalog-op", "acme-hub"]);
  assert.deepEqual(splitKey("service:a:b:c"), ["service", "a:b:c"], "the first colon alone");
  assert.deepEqual(splitKey("platform"), ["platform"], "a key without a colon is its kind");
  assert.deepEqual(splitKey("group:"), ["group", ""]);
  assert.deepEqual(splitKey(":x"), ["", "x"]);
  assert.deepEqual(splitKey(""), [""]);
  assert.deepEqual(splitKey(null), [""]);
  assert.deepEqual(splitKey(undefined), [""]);
  const [kind, id] = splitKey("platform");
  assert.deepEqual([kind, id], ["platform", undefined], "as the split of old gave it to a destructuring");
});

// --- What the store knows of a catalog ---------------------------------------------------------------------------------------

test("a catalog is a unit of the store by its namespace, and so is the content: for their progress", () => {
  store.state = lab({ catalogs: [catalog({ id: "acme-hub", progress: 0.4 }), catalog({ id: "other", progress: null })], content: content("downloading", { progress: 0.42 }) });
  assert.equal(store.unit("catalog:acme-hub").name, "Acme Hub");
  assert.equal(store.progress("catalog:acme-hub"), 0.4);
  assert.equal(store.progress("catalog:other"), null);
  assert.equal(store.unit("catalog:nowhere"), undefined);
  assert.equal(store.progress("content"), 0.42);
  assert.equal(store.unit("content"), store.state.content);
  // The units of old are what they were, an id with a slash among them
  store.state = lab({ addons: [{ kind: "addon", id: "@acme-hub/x", name: "X" }, { kind: "addon", id: "local/y", name: "Y" }],
    playgrounds: [{ kind: "playground", id: "@acme-hub/bank", name: "Bank" }] });
  assert.equal(store.unit("addon:@acme-hub/x").name, "X");
  assert.equal(store.unit("addon:local/y").name, "Y");
  assert.equal(store.unit("playground:@acme-hub/bank").name, "Bank");
  assert.equal(store.unit("addon:nothing"), undefined);
  assert.equal(store.unit("something:else"), undefined);
  assert.equal(store.unit(""), undefined);
  assert.equal(store.unit(null), undefined);
  // The request key of an action on a catalog is none of the units: the page tells it apart by its kind
  assert.equal(store.unit("catalog-op:acme-hub"), undefined);
  // A state that lacks the catalogs, or has them wrong, knows no catalog and breaks nothing
  for (const catalogs of [undefined, null, "none", 5, {}]) {
    store.state = { ...lab(), catalogs };
    assert.equal(store.unit("catalog:acme-hub"), undefined);
    assert.equal(store.measuring(), false);
  }
  store.state = null;
  assert.equal(store.unit("catalog:acme-hub"), undefined);
  assert.equal(store.measuring(), false);
});

test("the page asks again every second while a catalog is being downloaded, and no longer once it is", () => {
  store.state = lab({ catalogs: [catalog(), catalog({ id: "second", progress: 0.1 })] });
  assert.equal(store.measuring(), true);
  store.state = lab({ catalogs: [catalog({ progress: 0 })] });
  assert.equal(store.measuring(), true, "a download that has just begun has a progress of 0");
  store.state = lab({ catalogs: [catalog(), catalog({ id: "second" })] });
  assert.equal(store.measuring(), false);
  store.state = lab({ catalogs: [catalog({ progress: "0.5" }), catalog({ id: "second", progress: undefined })] });
  assert.equal(store.measuring(), false, "a progress is a number");
  store.state = lab({ job: { id: "ab12cd34", done: false } });
  assert.equal(store.measuring(), true, "a job is measured as it was");
});
