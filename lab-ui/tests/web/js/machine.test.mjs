// Your machine (web/js/dialogs/machine.js), what the first step of the welcome draws, run for real over a small DOM and a stub of what the server
// answers: each line says its state, what it is and its fix (a link, a command to copy, or one the Lab UI runs here); the whole says in one line
// whether the platform can start; asked again on a timer while it is watched, and no longer once it is not.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const row = (id, state, detail, actions = []) => ({ id, state, title: id[0].toUpperCase() + id.slice(1), detail, actions });
const READY = { ready: true, problems: 0, checks: [row("docker", "ok", "Docker Desktop 29.8.2, Compose 5.6.0"), row("git", "ok", "Git 2.43.2")] };
const LOOK = { ready: true, problems: 1, checks: [row("docker", "ok", "Docker Desktop 29.8.2, Compose 5.6.0"), row("memory", "warn", "6 GB: give it 8 GB in the settings of Docker Desktop")] };
const DOWN = { ready: false, problems: 3, checks: [
  row("docker", "error", "Docker Desktop is not running", [{ id: "open-docker", label: "Open Docker Desktop", primary: true }]),
  row("memory", "idle", "Known once Docker runs"),
  row("ports", "warn", "27017 (MongoDB) is used by another program", [{ id: "start-docker", label: "Copy the command", primary: true, copy: "sudo systemctl start docker" }]),
  row("git", "error", "Git is missing", [{ id: "git", label: "Install Git", primary: true, href: "https://git-scm.com/downloads" }]),
] };

const server = { machine: READY, calls: [] };
const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  server.calls.push({ path, method: options.method || "GET" });
  return reply(path.startsWith("/api/machine") ? server.machine : {});
} });
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
const { machine, machineBlocked, machineToFix, machineRowsHtml, machineSummary, refreshMachine, watchMachine, unwatchMachine } =
  await import("../../../web/js/dialogs/machine.js");
const settle = async () => { for (let i = 0; i < 4; i += 1) await flush(); };
const draw = (markup) => {
  const list = page.doc.createElement("ul");
  list.innerHTML = markup;
  return list;
};

test("before the server answers, each need is said as being checked", () => {
  const rows = draw(machineRowsHtml()).querySelectorAll(".machine-check");
  assert.deepEqual(rows.map((r) => [r.getAttribute("data-state"), text(r.querySelector("strong"))]),
    [["working", "Docker"], ["working", "Memory for Docker"], ["working", "Disk space"], ["working", "Ports"], ["working", "Git"]]);
  assert.equal(text(draw(machineSummary().html)), "Checking your machine");
});

test("each line says its state, what it is and its fix: a button that runs here, a command to copy shown whole, a link in a tab of its own", async () => {
  server.machine = DOWN;
  await refreshMachine(true);
  assert.deepEqual([machineBlocked(), machineToFix()], [true, 3]);
  const rows = draw(machineRowsHtml()).querySelectorAll(".machine-check");
  assert.deepEqual(rows.map((r) => r.getAttribute("data-state")), ["error", "idle", "warn", "error"]);
  assert.equal(rows[0].querySelector(".machine-state").getAttribute("aria-label"), "Missing");
  assert.deepEqual(rows[0].querySelectorAll(".machine-text > *, .machine-fix > *").map(text), ["Docker", "Docker Desktop is not running", "Open Docker Desktop"]);
  assert.equal(rows[0].querySelector("[data-act]").getAttribute("data-act"), "open-docker");
  assert.equal(text(rows[2].querySelector("code")), "sudo systemctl start docker");
  assert.equal(rows[2].querySelector("[data-copy]").getAttribute("data-copy"), "sudo systemctl start docker");
  const link = rows[3].querySelector("a");
  assert.deepEqual([link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")], ["https://git-scm.com/downloads", "_blank", "noopener"]);
});

test("the whole says in one line whether the platform can start", async () => {
  assert.deepEqual([machineSummary().tone, text(draw(machineSummary().html))], ["err", "The platform cannot start until the 2 lines in red are fixed"]);
  server.machine = LOOK;
  await refreshMachine(true);
  assert.deepEqual([machineBlocked(), machineToFix()], [false, 1]);
  assert.deepEqual([machineSummary().tone, text(draw(machineSummary().html))], ["warn", "Ready to run, one line needs a look"]);
  server.machine = READY;
  await refreshMachine(true);
  assert.deepEqual([machineSummary().tone, text(draw(machineSummary().html))], ["ok", "Your machine is ready"]);
});

test("a fix on its way keeps the platform waiting and the whole says what it waits for", async () => {
  server.machine = { ready: false, problems: 2, checks: [row("docker", "working", "Docker Desktop is starting"),
    row("disk", "warn", "14 GB free: the first start downloads about 15 GB")] };
  await refreshMachine(true);
  assert.equal(machineBlocked(), true, "Continue and Start wait for Docker");
  assert.deepEqual([machineSummary().tone, text(draw(machineSummary().html))], ["", "Docker Desktop is starting"]);
  server.machine = READY;
  await refreshMachine(true);
});

test("an answer that is not a report keeps the last one", async () => {
  server.machine = { error: "busy" };
  await refreshMachine(true);
  assert.equal(machine.report, READY);
  server.machine = READY;
});

test("while it is watched it is asked again on its own, each answer drawn, and once it is not it stops", async () => {
  let drawn = 0;
  server.calls.length = 0;
  watchMachine(() => { drawn += 1; });
  await settle();
  assert.deepEqual(server.calls.map((c) => c.path), ["/api/machine?fresh=1"], "asked at once, fresh");
  mock.timers.tick(3000);
  await settle();
  assert.deepEqual(server.calls.map((c) => c.path), ["/api/machine?fresh=1", "/api/machine"]);
  assert.equal(drawn, 2);
  unwatchMachine();
  server.calls.length = 0;
  mock.timers.tick(9000);
  await settle();
  assert.deepEqual(server.calls, []);
});
