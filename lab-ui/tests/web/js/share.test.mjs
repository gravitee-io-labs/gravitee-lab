// What is made and shared from the Lab UI (web/js/dialogs/create.js). The dialog of a new item keeps it on this machine or puts it in a catalog
// that is a folder of the person's, sent as `catalog` and drawn only when there is one that can be written; its checklists say where each item
// comes from and why they are empty. And sharing an item of this machine to a catalog: the effects dialog "Share Acme Bakery" with an option for each
// catalog that takes contributions, the audience measured as the hint of each, and nothing chosen from two destinations so that Enter can never
// send private material to a public place; one destination is no choice; none offers New catalog and Add catalog. A destination that cannot take
// the item says why. What a catalog that is a repository gets is a branch (with a pull request to open when its host has one), a folder gets
// a copy. A problem that every destination finds is a notice of what to fix first, with the prompt that has an agent fix it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { El, Ev, bubble, flush, installDom, text, isOff, offWhy } from "./dom-stub.mjs";
import { FIRST, FOLDER, HOSTILE, NOT_ADDED, PERSONAL, catalog, content, item, lab, origin } from "./fixtures.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

// What the server answers: the destinations, and what sharing to each would do or did; every call is kept
const server = { calls: [], destinations: [], checks: {}, done: {}, refuse: "", rejects: {} };
const reply = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : undefined;
  if (path === "/api/state") return reply(store.state);
  server.calls.push({ path, body });
  if (/^\/api\/(addons|setups|playgrounds)$/.test(path)) return server.refuse ? reply({ error: server.refuse }, 400) : reply({ file: `${path.slice(5, -1)}s/${body.name}/compose.yml`, id: body.name });
  if (!path.endsWith("/share")) return reply({});
  // The first check, with no `to`: the destinations; an answer that has none is today's {file, problems, changes}
  if (body.check && !body.to) return reply(server.destinations ? { destinations: server.destinations } : { file: "playgrounds/local/bank/compose.yml", problems: [], changes: [] });
  if (body.check && server.rejects[body.to]) return reply({ error: server.rejects[body.to] }, 400);
  if (body.check) return typeof server.checks[body.to] === "function" ? reply(server.checks[body.to]()) : reply(server.checks[body.to] || { problems: [], changes: [], identity: null });
  if (server.refuse) return reply({ error: server.refuse }, 400);
  return reply(server.done[body.to]);
} });
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
const { store } = await import("../../../web/js/core.js");
const { share, openCreate } = await import("../../../web/js/dialogs/create.js");
for (const form of page.doc.querySelectorAll("dialog form")) form.checkValidity = () => true;   // a browser tells whether the required fields are filled

const SKILL = ".agents/skills/share-item/SKILL.md";
const mine = item("playground", "local/bank", "Acme Bakery", { personal: true, held: { share: "", delete: "" }, file: ".lab/catalogs/local/playgrounds/bank/compose.yml" });
const state = (more = {}) => lab({ playgrounds: [mine], skills: { share: SKILL }, root: "/lab",
  catalogs: [FIRST({ address: "github.com/example/demos" }), catalog({ address: "github.com/acme/lab-hub" }), FOLDER()], ...more });
store.state = state();

const dest = (id, name, more = {}) => ({ id, name, kind: "git", writable: true, audience: "public", reason: "", ...more });
const PUBLIC = dest("gravitee", "Gravitee public"), PRIVATE = dest("acme-hub", "Acme Hub", { audience: "private" });
const CHECK = (more = {}) => ({ problems: [], changes: ["compose.yml"], identity: null, ...more });
const DONE = { branch: "lab/bank-20261016", commit: "9f1c2ab", repository: "acme/lab-hub", pull_request: "https://github.com/acme/lab-hub/compare/lab/bank-20261016?expand=1" };

// The dialog of what an action does (ui.js `effectsDialog`), and what a browser knows of its radios, which the small DOM does not
const choice = page.ids.choice, picks = page.ids["choice-list"], ok = page.ids["choice-ok"];
const radios = () => picks.querySelectorAll("input");
const on = (radio) => (radio.checked === undefined ? radio.hasAttribute("checked") : radio.checked);
picks.querySelector = (selector) => (selector === "input:checked" ? radios().find(on) : El.prototype.querySelector.call(picks, selector));
const rowOf = (row) => [text(row.querySelector(".choice-row-label")), row.querySelectorAll(".pill").map(text), row.querySelectorAll(".muted").map(text)];
const options = () => picks.querySelectorAll(".choice").map((label) => ({ name: text(label.querySelector("strong")), hint: label.querySelector(".sub") ? text(label.querySelector(".sub")) : "",
  disabled: label.classList.contains("disabled"), rows: label.querySelectorAll(".choice-row").map(rowOf) }));
const pick = (index) => {
  radios().forEach((radio, i) => { radio.checked = i === index; });
  picks.dispatchEvent(Object.assign(new Ev("change"), { target: radios()[index] }));
  picks.onchange?.();
};
const answer = async (confirmed) => {
  choice.returnValue = confirmed ? "ok" : "";
  choice.close();   // a browser closes it, and tells what was answered
  choice.onclose();
  await settle();
};
const settle = async () => {
  await flush();
  mock.timers.tick(600);
  await flush();
  await flush();
};
const notice = () => page.doc.querySelectorAll("dialog").find((el) => el.getAttribute("aria-labelledby") === "share-title");
const toasts = () => page.doc.querySelector("#toasts").children.map(text);
const shares = () => server.calls.filter((call) => call.body && !call.body.check).map((call) => [call.path, call.body]);
const opened = async () => {
  server.calls = [];
  const done = share(mine);
  await settle();
  return done;
};

test("from two destinations nothing is chosen: each says who can read it, and Share waits for a choice, with its reason", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK({ identity: "you@corp.com", audience: "public" }), "acme-hub": CHECK({ audience: "private" }) };
  const done = opened();
  await settle();
  assert.equal(text(page.ids["choice-title"]), "Share Acme Bakery");
  assert.deepEqual(options().map(({ name, hint, disabled }) => [name, hint, disabled]), [["Gravitee public", "Public", false], ["Acme Hub", "Private", false]]);
  assert.deepEqual(radios().map(on), [false, false], "no option is preselected: Enter cannot send private material to a public place");
  assert.deepEqual([text(ok), isOff(ok), offWhy(ok)], ["Share", true, "Choose where to share"]);
  pick(1);
  assert.deepEqual([isOff(ok), text(ok)], [false, "Share"], "once one is chosen it can be shared");
  await answer(false);
  await done;
  assert.deepEqual(shares(), [], "nothing was sent");
  assert.deepEqual([isOff(ok), ok.hasAttribute("data-tip")], [false, false], "the button is the other dialogs' too: it is left as they find it");
});

test("what sharing to a destination does is said under it: where a branch opens, what changes, what stays, what is left out, and under whom it commits when everyone can read it", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK({ identity: "you@corp.com", audience: "public" }), "acme-hub": CHECK({ audience: "private", changes: ["compose.yml", "README.md"] }) };
  const done = opened();
  await settle();
  const [first, second] = options();
  assert.deepEqual(first.rows, [["Opens", ["A branch in example/demos"], []], ["Changes", ["compose.yml"], []], ["Keeps", ["Your copy and its settings"], []],
    ["Leaves out", ["Your settings and data"], []], ["Commits as", ["you@corp.com"], ["Visible publicly"]]]);
  assert.deepEqual(second.rows, [["Opens", ["A branch in acme/lab-hub"], []], ["Changes", ["compose.yml", "README.md"], []], ["Keeps", ["Your copy and its settings"], []],
    ["Leaves out", ["Your settings and data"], []]], "a private destination commits as nobody in particular: the line is not drawn");
  await answer(false);
  await done;
});

test("a single destination is no choice: its rows, and Share ready", async () => {
  server.destinations = [PRIVATE];
  server.checks = { "acme-hub": CHECK({ audience: "private" }) };
  const done = opened();
  await settle();
  assert.equal(picks.classList.contains("single"), true);
  assert.equal(radios().length, 1);
  assert.deepEqual([isOff(ok), text(ok)], [false, "Share"]);
  assert.deepEqual(picks.querySelectorAll(".choice-row").map((row) => rowOf(row)[0]), ["Opens", "Changes", "Keeps", "Leaves out"]);
  assert.equal(picks.querySelectorAll(".sub").length, 0, "no line of who can read it under nothing: the rows say where it goes");
  server.done = { "acme-hub": DONE };
  await answer(true);
  await done;
  assert.deepEqual(shares(), [["/api/playgrounds/local/bank/share", { to: "acme-hub" }]]);
});

test("Enter with nothing chosen does nothing, and does not fail: the dialog reads the option that is chosen, and there is none", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK({ identity: "you@corp.com", audience: "public" }), "acme-hub": CHECK({ audience: "private" }) };
  const done = opened();
  await settle();
  const press = (key) => {
    const seen = { prevented: false };
    picks.onkeydown({ key, preventDefault() { seen.prevented = true; } });
    return seen.prevented;
  };
  assert.deepEqual(radios().map(on), [false, false]);
  assert.equal(press("Enter"), true, "it is the person's to choose, not Enter's");
  assert.equal(press("ArrowDown"), false, "the other keys go on as they do");
  pick(1);
  assert.equal(press("Enter"), false, "once one is chosen Enter is the dialog's own: it confirms what is not dangerous");
  await answer(false);
  await done;
  assert.deepEqual(shares(), []);
});

test("a single destination that everyone can read says so in its rows, not in a line of its own; with every destination held, Share says why of the first", async () => {
  server.destinations = [PUBLIC];
  server.checks = { gravitee: CHECK({ identity: "you@corp.com", audience: "public" }) };
  const done = opened();
  await settle();
  assert.equal(picks.querySelectorAll(".sub").length, 0, "no unlabelled word under nothing");
  assert.deepEqual(picks.querySelectorAll(".choice-row").map(rowOf).at(-1), ["Commits as", ["you@corp.com"], ["Visible publicly"]]);
  await answer(false);
  await done;
  const [a, b] = [dest("a", "A", { reason: "You cannot push to A. Ask for access." }), dest("b", "B", { reason: "You cannot push to B. Ask for access." })];
  server.destinations = [a, b];
  server.checks = {};
  const held = opened();
  await settle();
  assert.deepEqual([isOff(ok), offWhy(ok)], [true, "You cannot push to A. Ask for access."], "there is nothing to choose, so it does not say to choose");
  await answer(false);
  await held;
  assert.deepEqual(shares(), []);
});

test("an answer to the first check that lists no destination does not hide Share: the catalogs added are asked in turn, and each says if it cannot take the item", async () => {
  server.checks = { gravitee: CHECK({ identity: "you@corp.com", audience: "public" }), "my-catalog": CHECK({ audience: "private" }) };
  server.rejects = { "acme-hub": "Acme Hub does not take contributions from the Lab." };
  // (the fetch of an Add that is on its way is carried among the catalogs, and is none of them)
  store.state = state({ catalogs: [FIRST({ address: "github.com/example/demos" }), PERSONAL(), NOT_ADDED({ id: "listed" }), { id: "check", kind: "progress", progress: 0.4 },
    catalog({ address: "github.com/acme/lab-hub" }), FOLDER()] });
  for (const answered of [undefined, "boom", { gravitee: 1 }]) {
    server.destinations = answered;
    const done = opened();
    await settle();
    assert.deepEqual(options().map(({ name, hint, disabled }) => [name, hint, disabled]),
      [["Gravitee public", "Public", false], ["Acme Hub", "Acme Hub does not take contributions from the Lab.", true], ["My catalog", "Private", false]],
      "a repository or a folder, never this machine's row nor one that is not added; the first catalog first");
    assert.deepEqual(server.calls.filter((call) => call.body?.check).map((call) => call.body.to ?? null), [null, "gravitee", "acme-hub", "my-catalog"]);
    await answer(false);
    await done;
  }
  // With no catalog added there is nothing to share to: the two ways to one
  store.state = state({ catalogs: [PERSONAL(), NOT_ADDED()] });
  server.destinations = undefined;
  await opened();
  assert.equal(choice.hasAttribute("open"), false);
  assert.deepEqual(notice().querySelectorAll(".dialog-actions .btn").map(text), ["New catalog", "Add catalog", "Close"]);
  notice().close();
  server.rejects = {};
  store.state = state();
});

test("the branch is the result: its name, to copy, and the pull request to open when the host gives one; the item stays where it was", async () => {
  server.destinations = [PRIVATE, PUBLIC];
  server.checks = { "acme-hub": CHECK(), gravitee: CHECK() };
  server.done = { "acme-hub": DONE };
  const done = opened();
  await settle();
  pick(0);
  await answer(true);
  await done;
  assert.deepEqual(shares(), [["/api/playgrounds/local/bank/share", { to: "acme-hub" }]]);
  const result = notice();
  assert.ok(result.hasAttribute("open"));
  assert.equal(text(result.querySelector("h2")), "Share Acme Bakery");
  assert.equal(text(result.querySelector("dt")), "Branch");
  assert.equal(text(result.querySelector("dd code")), "lab/bank-20261016");
  assert.equal(result.querySelector("dd [data-copy]").getAttribute("data-copy"), "lab/bank-20261016", "the name is copied whole");
  const link = result.querySelector(".dialog-actions a");
  assert.deepEqual([text(link), link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")], ["Open pull request", DONE.pull_request, "_blank", "noopener"]);
  assert.deepEqual(result.querySelectorAll(".dialog-actions .btn").map(text), ["Open pull request", "Close"]);
  assert.ok(toasts().includes("Shared"));
  result.close();
  // A host that has no address for a pull request gives the branch alone
  server.done = { "acme-hub": { ...DONE, pull_request: null } };
  const again = opened();
  await settle();
  pick(0);
  await answer(true);
  await again;
  assert.deepEqual(notice().querySelectorAll(".dialog-actions .btn").map(text), ["Close"]);
  notice().close();
});

test("an address that is not a web address is never offered to open", async () => {
  server.destinations = [PRIVATE];
  server.checks = { "acme-hub": CHECK() };
  server.done = { "acme-hub": { ...DONE, pull_request: "javascript:boom()" } };
  const done = opened();
  await settle();
  await answer(true);
  await done;
  assert.equal(notice().querySelectorAll(".dialog-actions a").length, 0);
  notice().close();
});

test("a destination that cannot take the item cannot be chosen, and says why; with only that one Share waits too", async () => {
  const refused = dest("acme-hub", "Acme Hub", { audience: "private", writable: false, reason: "You cannot push to Acme Hub. Ask for access." });
  server.destinations = [PUBLIC, refused];
  server.checks = { gravitee: CHECK() };
  const done = opened();
  await settle();
  assert.deepEqual(options().map(({ name, hint, disabled }) => [name, hint, disabled]), [["Gravitee public", "Public", false], ["Acme Hub", "You cannot push to Acme Hub. Ask for access.", true]]);
  assert.equal(radios()[1].hasAttribute("disabled"), true);
  assert.ok(!server.calls.some((call) => call.body?.to === "acme-hub"), "it is not asked what sharing there would do");
  await answer(false);
  await done;
  server.destinations = [refused];
  const alone = opened();
  await settle();
  assert.deepEqual([isOff(ok), offWhy(ok)], [true, "You cannot push to Acme Hub. Ask for access."]);
  await answer(false);
  await alone;
  assert.deepEqual([isOff(ok), ok.hasAttribute("data-tip")], [false, false]);
});

test("a destination that fails the check says what failed, and the others stay: a catalog that does not answer is not the end of sharing", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK(), "acme-hub": () => { throw new Error("boom"); } };
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    return body.to === "acme-hub" && body.check ? reply({ error: "Cannot reach github.com. Check your connection, then try again." }, 502) : original(path, options);
  };
  const done = opened();
  await settle();
  globalThis.fetch = original;
  assert.deepEqual(options().map(({ name, hint, disabled }) => [name, hint, disabled]), [["Gravitee public", "Public", false], ["Acme Hub", "Cannot reach github.com. Check your connection, then try again.", true]]);
  await answer(false);
  await done;
});

test("a problem that every destination finds is a notice of what to fix first, with the prompt that has an agent fix it; the dialog of destinations does not open", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK({ problems: ["compose.yml holds a secret", "It includes Kafka, which is not shared"] }), "acme-hub": CHECK({ problems: ["compose.yml holds a secret"] }) };
  await opened();
  assert.equal(choice.hasAttribute("open"), false);
  const result = notice();
  assert.ok(result.hasAttribute("open"));
  assert.deepEqual(result.querySelectorAll("dt").map(text), ["Fix first", "", "Prompt"]);
  assert.deepEqual(result.querySelectorAll("dd").slice(0, 2).map(text), ["compose.yml holds a secret", "It includes Kafka, which is not shared"], "each problem once, in the order found");
  assert.equal(result.querySelector("dd code").textContent, `Use the share-item skill (${SKILL}) to share the personal playground Acme Bakery.`);
  assert.deepEqual(result.querySelectorAll(".dialog-actions .btn").map(text),
    ["Copy prompt", "Open in Claude app", "Open in Claude Code", "Open in Codex", "Open in Cursor", "Close"], "the agents of the creation dialog, then Close");
  assert.ok(result.querySelector('.dialog-actions a[href^="claude://code/new?q="]'));
  result.close();
  assert.deepEqual(shares(), []);
});

test("a problem that one destination finds leaves the others: that one is held with the problem as its reason", async () => {
  server.destinations = [PUBLIC, PRIVATE];
  server.checks = { gravitee: CHECK(), "acme-hub": CHECK({ problems: ["It includes Kafka (Partner Bank), which Acme Hub does not declare."] }) };
  const done = opened();
  await settle();
  assert.deepEqual(options().map(({ name, disabled, hint }) => [name, disabled, hint]), [["Gravitee public", false, "Public"], ["Acme Hub", true, "It includes Kafka (Partner Bank), which Acme Hub does not declare."]]);
  await answer(false);
  await done;
});

test("with no catalog to share to the dialog offers New catalog and Add catalog, which open the Catalogs page's dialogs, and no Share", async () => {
  server.destinations = [];
  await opened();
  assert.equal(choice.hasAttribute("open"), false, "no option to choose");
  const result = notice();
  assert.ok(result.hasAttribute("open"));
  assert.equal(text(result.querySelector("h2")), "Share Acme Bakery");
  assert.equal(result.querySelector("dl").hidden, true, "no option, and no line in its place: the two buttons are all it says");
  assert.equal(text(result.querySelector("dl")), "");
  assert.deepEqual(result.querySelectorAll(".dialog-actions .btn").map(text), ["New catalog", "Add catalog", "Close"]);
  assert.equal(result.querySelectorAll(".dialog-actions .primary").length, 0, "no primary: nothing is shared");
  const shown = () => page.doc.querySelectorAll("dialog").filter((el) => el.hasAttribute("open") && el !== result).map((el) => text(el.querySelector("h2")));
  result.querySelectorAll("[data-share-catalog]")[0].click();
  assert.equal(result.hasAttribute("open"), false, "this dialog closes first");
  assert.deepEqual(shown(), ["New catalog"]);
  page.doc.querySelectorAll("dialog").forEach((el) => el.close());
  result.showModal();
  result.querySelectorAll("[data-share-catalog]")[1].click();
  assert.deepEqual(shown(), ["Add catalog"]);
  page.doc.querySelectorAll("dialog").forEach((el) => el.close());
  assert.deepEqual(shares(), []);
});

test("what only this machine has goes with the item, and what the catalog has already is the one it uses: both are said", async () => {
  const folder = dest("my-catalog", "My catalog", { kind: "folder", audience: "private" });
  server.destinations = [folder];
  server.checks = { "my-catalog": { ...CHECK(), carries: ["Notes"], reuses: ["Echo"] } };
  const done = opened();
  await settle();
  assert.deepEqual(picks.querySelectorAll(".choice-row").map(rowOf), [["Copies to", ["My catalog"], []], ["Also shares", ["Notes"], []],
    ["Uses the catalog's", ["Echo"], []], ["Keeps", ["Your copy and its settings"], []]]);
  await answer(false);
  await done;
});

test("a catalog that is a folder gets a copy: where it goes and what stays, then Copied and no notice", async () => {
  const folder = dest("my-catalog", "My catalog", { kind: "folder", audience: "private" });
  server.destinations = [folder];
  server.checks = { "my-catalog": CHECK() };
  server.done = { "my-catalog": { copied: "My catalog" } };
  const done = opened();
  await settle();
  assert.deepEqual(picks.querySelectorAll(".choice-row").map(rowOf), [["Copies to", ["My catalog"], []], ["Keeps", ["Your copy and its settings"], []]]);
  await answer(true);
  await done;
  assert.deepEqual(shares(), [["/api/playgrounds/local/bank/share", { to: "my-catalog" }]]);
  assert.ok(toasts().includes("Copied"));
  assert.equal(notice().hasAttribute("open"), false);
});

test("a push that is refused leaves the item where it was and says why, in a toast that stays up", async () => {
  server.destinations = [PRIVATE];
  server.checks = { "acme-hub": CHECK() };
  server.refuse = "You cannot push to Acme Hub. Ask for access.";
  const done = opened();
  await settle();
  await answer(true);
  await done;
  server.refuse = "";
  const error = page.doc.querySelector("#toasts").children.find((toast) => text(toast).startsWith("You cannot push"));
  assert.ok(error && error.getAttribute("role") === "alert");
  assert.equal(notice().hasAttribute("open"), false);
});

test("the check is said on the item's button as Sharing; a server that cannot be asked says so, and no dialog opens", async () => {
  server.destinations = [PRIVATE];
  server.checks = { "acme-hub": CHECK() };
  const pending = share(mine);
  assert.equal(store.pending.get("playground:local/bank"), "share", "the button of the row says Sharing from the click");
  await settle();
  await answer(false);
  await pending;
  assert.equal(store.pending.has("playground:local/bank"), false);
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("fetch failed"); };
  await share(mine);
  globalThis.fetch = original;
  assert.ok(toasts().includes("Lab UI offline"));
});

test("a catalog's name and a destination's are text, never markup", async () => {
  server.destinations = [dest("acme-hub", HOSTILE), dest("other", "Other")];
  server.checks = { "acme-hub": CHECK({ changes: [HOSTILE] }), other: CHECK() };
  const done = opened();
  await settle();
  assert.equal(picks.querySelectorAll("img").length, 0);
  assert.equal(text(picks.querySelector("strong")), HOSTILE);
  await answer(false);
  await done;
  server.destinations = [PRIVATE];
  server.checks = { "acme-hub": CHECK() };
  server.done = { "acme-hub": { ...DONE, branch: HOSTILE } };
  const again = opened();
  await settle();
  await answer(true);
  await again;
  assert.equal(notice().querySelectorAll("img").length, 0);
  assert.equal(text(notice().querySelector("dd code")), HOSTILE);
  notice().close();
});

// --- The dialog of a new item ------------------------------------------------------------------------------------------------------------------

const create = page.doc.querySelectorAll("dialog").find((el) => el.getAttribute("aria-labelledby") === "create-title");
const inCreate = (selector) => create.querySelector(selector);
create.querySelector("form").checkValidity = () => true;   // a browser tells whether the required fields are filled
const where = () => inCreate('[data-choice="catalog"]');
const kept = () => where().querySelectorAll("button").map((button) => [text(button), button.getAttribute("aria-checked")]);
const catalogs = (...more) => [FIRST(), catalog(), ...more];
const lab2 = (more = {}) => lab({ skills: { addon: ".agents/skills/new-addon/SKILL.md", setup: ".agents/skills/new-setup/SKILL.md", playground: ".agents/skills/new-playground/SKILL.md", share: SKILL },
  root: "/lab", categories: ["Observability"], running: { explicit: [] }, addons: [item("addon", "kafka", "Kafka", { conflicts: {}, needs: [], members: undefined })],
  setups: [], playgrounds: [], catalogs: catalogs(), content: content("ready"), ...more });
const open2 = (kind, more = {}) => {
  for (const el of create.querySelectorAll("input, textarea")) el.removeAttribute("value");   // what a person typed in the last one (the small DOM reads the attribute first)
  store.state = lab2(more);
  openCreate({ kind, mode: "new" });
};
const send = async () => {
  server.calls = [];
  inCreate("#create-name").setAttribute("value", "Mine");
  inCreate("#create-desc").setAttribute("value", "Something");
  inCreate("#create-image").setAttribute("value", "repo:tag");
  create.querySelector("form").dispatchEvent(new Ev("submit"));
  await settle();
  return server.calls.filter((call) => call.body && /^\/api\/(addons|setups|playgrounds)$/.test(call.path));
};

test("with no catalog that is a folder of the person's, Where it is kept is not drawn, and what is made is personal: no catalog is sent", async () => {
  for (const rows of [catalogs(), catalogs(catalog({ id: "git-two", name: "Git two" })), [FIRST()], catalogs(FOLDER({ writable: false, id: "locked" })), []]) {
    open2("addon", { catalogs: rows });
    assert.equal(where().hidden, true, JSON.stringify(rows.map((c) => [c.id, c.kind])));
    assert.equal(where().children.length, 0);
    const [request] = await send();
    assert.deepEqual(request.body.catalog, undefined, "a request that does not name a catalog is personal");
    assert.ok(!("shared" in request.body), "nothing is shared with the project any more");
    create.close();
  }
});

test("with one, it offers Personal first and each folder catalog by name, Personal chosen; the one chosen is sent as catalog", async () => {
  const folders = [FOLDER(), FOLDER({ id: "second", name: "Second" }), FOLDER({ id: "locked", name: "Locked", writable: false })];
  open2("addon", { catalogs: catalogs(...folders) });
  assert.equal(where().hidden, false);
  assert.deepEqual(kept(), [["Personal", "true"], ["My catalog", "false"], ["Second", "false"]], "a catalog that cannot be written is not offered");
  assert.equal(where().getAttribute("role"), "radiogroup");
  assert.equal(where().getAttribute("aria-label"), "Where it is kept");
  assert.deepEqual(where().querySelectorAll("button").map((b) => b.getAttribute("role")), ["radio", "radio", "radio"]);
  const [personal] = await send();
  assert.equal(personal.body.catalog, undefined);
  create.close();
  open2("addon", { catalogs: catalogs(...folders) });
  where().querySelectorAll("button")[2].click();
  assert.deepEqual(kept(), [["Personal", "false"], ["My catalog", "false"], ["Second", "true"]]);
  const [put] = await send();
  assert.equal(put.body.catalog, "second");
  assert.deepEqual([put.path, put.body.name, put.body.category], ["/api/addons", "Mine", ""]);
  where().querySelectorAll("button")[0].click();
  assert.equal((await send())[0].body.catalog, undefined);
  create.close();
  // What is kept at one opening is not kept for the next
  where().querySelectorAll("button")[1].click();
  open2("addon", { catalogs: catalogs(...folders) });
  assert.deepEqual(kept().map(([, on]) => on), ["true", "false", "false"]);
  create.close();
});

test("the same for a setup and a playground, which a catalog needs a category for; a name that is hostile is text", async () => {
  for (const kind of ["setup", "playground"]) {
    open2(kind, { catalogs: catalogs(FOLDER({ name: HOSTILE })) });
    assert.equal(where().querySelectorAll("img").length, 0);
    assert.equal(text(where().querySelectorAll("button")[1]), HOSTILE);
    const category = inCreate("#create-category");
    assert.equal(category.required, false, `${kind}: personal needs none`);
    where().querySelectorAll("button")[1].click();
    assert.equal(category.required, true, `${kind}: anything in a catalog has one`);
    assert.equal(category.firstElementChild.textContent, "Choose a category");
    create.close();
  }
});

test("the prompt for an agent says where the item goes: personal, or in the catalog by name", async () => {
  const skill = ".agents/skills/new-playground/SKILL.md";
  open2("playground", { catalogs: catalogs(FOLDER()) });
  inCreate('[data-value="agent"]').click();
  const prompt = () => text(inCreate("#create-prompt").querySelector("code.snippet"));
  assert.equal(prompt(), `Use the new-playground skill (${skill}) to create a personal playground.`);
  where().querySelectorAll("button")[1].click();
  assert.equal(prompt(), `Use the new-playground skill (${skill}) to create a playground in the catalog My catalog.`);
  // The shortcuts hand that same prompt to Claude, opened on the folder of the Lab; the task typed goes with it
  const links = () => inCreate("#create-agent-actions").querySelectorAll("a").map((link) => link.getAttribute("href"));
  const said = `Use the new-playground skill (${skill}) to create a playground in the catalog My catalog.`;
  assert.deepEqual(links().map((href) => href.split("?")[0]), ["claude://code/new", "claude-cli://open", "codex://new", "cursor://anysphere.cursor-deeplink/prompt"]);
  assert.ok(links()[0].startsWith(`claude://code/new?q=${encodeURIComponent(said)}&folder=`));
  assert.ok(links()[2].startsWith(`codex://new?prompt=${encodeURIComponent(said)}&path=`));
  inCreate("#create-task").value = "A bakery with an API";
  inCreate("#create-task").dispatchEvent(new Ev("input"));
  assert.ok(decodeURIComponent(links()[0].split("&folder=")[0]).endsWith("What it should do: A bakery with an API"));
  inCreate("#create-task").value = "";
  inCreate("#create-task").dispatchEvent(new Ev("input"));
  create.close();
  open2("addon", { catalogs: catalogs(FOLDER()) });
  inCreate('[data-value="agent"]').click();
  where().querySelectorAll("button")[1].click();
  assert.equal(prompt(), "Use the new-addon skill (.agents/skills/new-addon/SKILL.md) to create an add-on in the catalog My catalog.");
  create.close();
});

test("an item that is not shared yet cannot be part of what goes in a catalog, and is told so", async () => {
  const mineToo = item("setup", "local/own", "Own setup", { personal: true, members: [{ id: "kafka", name: "Kafka" }] });
  open2("playground", { setups: [mineToo], catalogs: catalogs(FOLDER()) });
  const problem = () => inCreate("#create-problem");
  inCreate('[data-pick="setup:local/own"]').checked = true;
  create.querySelector("#create-picks").dispatchEvent(Object.assign(new Ev("change"), { target: Object.assign(inCreate('[data-pick="setup:local/own"]'), { checked: true }) }));
  assert.equal(problem().hidden, true, "on this machine it is fine");
  where().querySelectorAll("button")[1].click();
  assert.deepEqual([problem().hidden, text(problem())], [false, "Own setup is of this machine: share it first"]);
  create.close();
});

test("a checklist says Personal for an item of this machine, and nothing for an item of a catalog: where it comes from is in its About", async () => {
  open2("setup", { addons: [item("addon", "kafka", "Kafka", { conflicts: {}, needs: [] }), item("addon", "@acme-hub/mock", "Mock bank", { conflicts: {}, needs: [], origin: origin(), readonly: true }),
    item("addon", "local/mine", "Mine", { conflicts: {}, needs: [], personal: true })] });
  const labels = () => create.querySelectorAll(".pick strong").map((el) => [el.firstChild.data.trim(), el.querySelectorAll(".pill").map(text)]);
  assert.deepEqual(labels(), [["Kafka", []], ["Mock bank", []], ["Mine", ["Personal"]]]);
  create.close();
});

test("with no add-on at all the checklist says why, in the words of the lists, and not that nothing matches: ready, downloading, failed, none", async () => {
  const nothing = (key, more) => open2("setup", { addons: [], content: content(key, more) });
  const status = () => create.querySelector("#create-picks").querySelectorAll('[role="status"]');
  nothing("ready");
  assert.equal(text(status()[0]), "No add-on yet");
  assert.ok(!/No matches/.test(text(inCreate("#create-picks"))));
  nothing("downloading", { progress: 0.3 });
  assert.equal(text(status()[0].querySelector(".loading .tip-btn")), "Downloading");
  nothing("failed");
  assert.equal(text(status()[0].firstElementChild), "Cannot reach github.com. Check your connection, then try again.");
  nothing("none");
  assert.deepEqual(status()[0].querySelectorAll("[data-content-act]").map((b) => [b.dataset.contentAct, text(b)]), [["add", "Add Gravitee public"]]);
  assert.equal(status()[0].querySelector("[data-content-act]").getAttribute("type"), "button", "a button of the form that does not send it");
  // A server that does not say: nothing exists, as it always was said
  store.state = lab2({ addons: [], content: undefined });
  openCreate({ kind: "setup", mode: "new" });
  assert.equal(text(status()[0]), "No add-on yet");
  create.close();
  // An add-on there, and a search that finds none: it is the filter that finds nothing
  open2("setup");
  inCreate("#create-search").setAttribute("value", "zzz");
  inCreate("#create-search").dispatchEvent(new Ev("input"));
  assert.equal(status().length, 0);
  assert.match(text(inCreate("#create-picks")), /No matches/);
  create.close();
});

test("the checklist follows the content of the catalogs while the dialog is open, drawn again only when that changes", async () => {
  open2("setup", { addons: [], content: content("downloading", { progress: 0.2 }) });
  const picks2 = inCreate("#create-picks"), first = picks2.firstElementChild;
  store.state = lab2({ addons: [], content: content("downloading", { progress: 0.6 }) });
  store.notify();
  assert.ok(picks2.firstElementChild === first, "a refresh that says the same leaves it where it is");
  store.state = lab2({ content: content("ready") });
  store.notify();
  assert.deepEqual(create.querySelectorAll(".pick strong").map((el) => el.firstChild.data.trim()), ["Kafka"], "the add-ons arrived");
  create.close();
  store.notify();   // closed, it draws nothing
});
