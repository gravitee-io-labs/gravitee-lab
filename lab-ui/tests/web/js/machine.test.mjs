// Your machine (web/js/dialogs/machine.js), run for real over a small DOM and a stub of what the server answers: a first visit looks at the
// machine and opens it only when something needs a look, then what comes next once it is closed; each line says its state, what it is and
// its fix (a link, a command to copy, or one the Lab UI runs here); a line in red keeps the fix the one primary button; asked again on a timer
// while it is open.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mock, test } from "node:test";

import { flush, installDom, text } from "./dom-stub.mjs";

mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
const html = readFileSync(new URL("../../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([\w-]+)"/g)].map((found) => found[1]).filter((id) => !/^i-|^gio-g$|^combo-(?!pop)/.test(id));

const row = (id, state, detail, actions = []) => ({ id, state, title: id[0].toUpperCase() + id.slice(1), detail, actions });
const READY = { ready: true, problems: 0, checks: [row("docker", "ok", "Docker Desktop 29.8.2, Compose 5.6.0"), row("git", "ok", "Git 2.43.2")] };
const DOWN = { ready: false, problems: 2, checks: [
  row("docker", "error", "Docker Desktop is not running", [{ id: "open-docker", label: "Open Docker Desktop", primary: true }]),
  row("memory", "idle", "Known once Docker runs"),
  row("ports", "warn", "27017 (MongoDB) is used by another program", [{ id: "start-docker", label: "Copy the command", primary: true, copy: "sudo systemctl start docker" }]),
  row("git", "error", "Git is missing", [{ id: "git", label: "Install Git", primary: true, href: "https://git-scm.com/downloads" }]),
] };

const server = { machine: READY, calls: [] };
const reply = (body) => ({ ok: true, status: 200, headers: { get: () => "" }, json: async () => body });
const page = installDom({ ids, fetch: async (path, options = {}) => {
  server.calls.push({ path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : undefined });
  if (path === "/api/machine/act") return reply(server.machine);
  if (path.startsWith("/api/machine")) return reply(server.machine);
  return reply({});
} });
globalThis.getComputedStyle = () => ({ position: "static", getPropertyValue: () => "0" });
const { firstLook } = await import("../../../web/js/dialogs/machine.js");
const dialog = page.doc.querySelector(".machine-dialog");
const settle = async () => { for (let i = 0; i < 4; i += 1) await flush(); };

test("a first visit with a ready machine goes straight on, and the browser keeps that it looked", async () => {
  let next = 0;
  await firstLook(() => { next += 1; });
  assert.equal(next, 1);
  assert.equal(dialog.open, false);
  assert.equal(page.stored["machine:checked"], "1");
  server.machine = DOWN;
  await firstLook(() => { next += 1; });
  assert.equal(next, 2, "looked at once already: it goes on whatever the machine says");
  assert.equal(dialog.open, false);
});

test("a first visit with something to fix opens it, each line with its state and its fix, and Continue goes on", async () => {
  delete page.stored["machine:checked"];
  server.machine = DOWN;
  let next = 0;
  await firstLook(() => { next += 1; });
  await settle();
  assert.ok(dialog.open);
  assert.equal(text(dialog.querySelector("#machine-summary")), "The platform cannot start until the 2 lines in red are fixed");
  const rows = [...dialog.querySelectorAll(".machine-check")];
  assert.deepEqual(rows.map((r) => r.getAttribute("data-state")), ["error", "idle", "warn", "error"]);
  assert.equal(text(rows[0].querySelector("strong")), "Docker");
  assert.match(text(rows[0]), /Docker Desktop is not running Open Docker Desktop$/);
  // A command to copy is shown whole, with its copy button; a link opens in a tab of its own
  assert.equal(text(rows[2].querySelector("code")), "sudo systemctl start docker");
  assert.equal(rows[2].querySelector("[data-copy]").getAttribute("data-copy"), "sudo systemctl start docker");
  const link = rows[3].querySelector("a");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("href"), "https://git-scm.com/downloads");
  // A line in red: its fix is the one primary button, Continue is not
  const done = dialog.querySelector("[data-done]");
  assert.equal(text(done), "Continue");
  assert.equal(done.classList.contains("primary"), false);
  done.click();
  assert.equal(next, 1, "what comes next opens once it is closed");
  assert.equal(page.stored["machine:checked"], "1");
});

test("a fix that runs here is asked of the server, and what it answers is drawn", async () => {
  delete page.stored["machine:checked"];
  server.machine = DOWN;
  await firstLook(() => {});
  await settle();
  server.calls.length = 0;
  server.machine = { ...READY, checks: [row("docker", "working", "Docker Desktop is starting"), row("git", "ok", "Git 2.43.2")] };
  dialog.querySelector('[data-act="open-docker"]').click();
  await settle();
  assert.deepEqual(server.calls.filter((c) => c.method === "POST").map((c) => [c.path, c.body]), [["/api/machine/act", { id: "open-docker" }]]);
  assert.equal(dialog.querySelector(".machine-check").getAttribute("data-state"), "working");
  dialog.close();
});

test("while it is open it asks again on its own, and once it is closed it stops", async () => {
  delete page.stored["machine:checked"];
  server.machine = DOWN;
  await firstLook(() => {});
  await settle();
  server.calls.length = 0;
  server.machine = READY;
  mock.timers.tick(3000);
  await settle();
  assert.ok(server.calls.some((c) => c.path === "/api/machine"), "asked again");
  assert.equal(text(dialog.querySelector("#machine-summary")), "Your machine is ready");
  assert.ok(dialog.querySelector("[data-done]").classList.contains("primary"));
  dialog.close();
  server.calls.length = 0;
  mock.timers.tick(9000);
  await settle();
  assert.deepEqual(server.calls, []);
});
