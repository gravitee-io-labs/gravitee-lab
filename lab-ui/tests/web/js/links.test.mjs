// What to open (paintLinks in web/js/components/ui.js), as the Stack and the details of a running playground draw it, the one way: the consoles of the components
// under their product, the platform's groups in their order, the playground's own apps, then the categories of the add-ons; a link whose service does
// not run cannot be opened and says so. Over a small DOM.
import assert from "node:assert/strict";
import { test } from "node:test";

import { El, installDom } from "./dom-stub.mjs";

const page = installDom({ ids: ["confirm-cancel", "choice-cancel", "confirm", "choice", "toasts", "confirm-title", "confirm-text", "confirm-ok", "confirm-input"] });
const { store } = await import("../../../web/js/core.js");
const { paintLinks } = await import("../../../web/js/components/ui.js");

const link = (label, service, section, up = true, port = "8080") => ({ label, url: `http://localhost:${port}`, port, service, section, up });
const SERVICES = Object.fromEntries(["bakery-web", "gamma-ui", "am-management-ui", "apim-management-ui", "kiosk", "jade", "smtp4dev"].map((name) => [name,
  { label: name, state: { key: "healthy", label: "Healthy", running: true } }]));
const state = {
  blind: false, wait: "", docker: { error: null }, services: SERVICES,
  playgrounds: [{ id: "acme-bakery", services: ["bakery-web"] }],
  platform: { groups: [{ name: "Databases" }, { name: "Alert Engine" }, { name: "API Management" }, { name: "Access Management" }] },
  categories: ["Gravitee Platform", "AI", "Observability", "Other"],
};
store.state = state;
const LINKS = [
  link("Kiosk", "kiosk", "Observability", true, "5601"), link("Acme Bakery", "bakery-web", "Playground", true, "8110"),
  link("AM Console", "am-management-ui", "Access Management", true, "8081"), link("Gamma Console", "gamma-ui", "API Management", true, "8086"),
  link("APIM Console", "apim-management-ui", "API Management", true, "8084"), link("Mail", "smtp4dev", "Other", false, "5080"),
];

let made = 0;
const draw = (links) => {
  const el = page.el(`links-${++made}`);
  paintLinks(el, state, links);
  return el;
};
const sections = (el) => el.querySelectorAll(".link-group").map((group) => group.querySelector(".link-section").textContent);
const names = (group) => group.querySelectorAll(".link strong").map((strong) => strong.textContent);

test("what to open is under its product: the platform's groups in their order, then the playground, then the categories of the add-ons", () => {
  const el = draw(LINKS);
  assert.deepEqual(sections(el), ["API Management", "Access Management", "Playground", "Observability", "Other"]);
  const [api, access, playground] = el.querySelectorAll(".link-group");
  assert.deepEqual(names(api), ["Gamma Console", "APIM Console"], "in the order given: the state sorts them");
  assert.deepEqual(names(access), ["AM Console"]);
  assert.deepEqual(names(playground), ["Acme Bakery"]);
});

test("one order for every page that shows it, whatever the order the links are given in", () => {
  assert.deepEqual(sections(draw([...LINKS].reverse())), ["API Management", "Access Management", "Playground", "Observability", "Other"]);
});

test("a section with no link is not drawn, and a component that is no console of the platform's groups lands under its category", () => {
  const el = draw([link("Gamma Console", "gamma-ui", "API Management")]);
  assert.deepEqual(sections(el), ["API Management"]);
  assert.equal(draw([link("Jade", "jade", "Observability", true, "16686")]).querySelectorAll(".link-group").length, 1);
});

test("a console that runs opens in a browser tab of its own, one that does not says why and opens nothing", () => {
  const el = draw(LINKS);
  const kiosk = el.querySelectorAll(".link").find((anchor) => anchor.textContent.includes("Kiosk"));
  assert.equal(kiosk.getAttribute("href"), "http://localhost:5601");
  assert.equal(kiosk.getAttribute("target"), "_blank");
  assert.equal(kiosk.getAttribute("rel"), "noopener");
  assert.match(kiosk.textContent, /:5601/, "its port");
  const mail = el.querySelectorAll(".link").find((anchor) => anchor.textContent.includes("Mail"));
  assert.equal(mail.hasAttribute("href"), false);
  assert.equal(mail.getAttribute("aria-disabled"), "true");
  assert.equal(mail.getAttribute("data-tip"), "Mail is not running", "why, as a tooltip");
  assert.equal(mail.classList.contains("off"), true);
});

test("with nothing to open it says so, across the card", () => {
  const el = draw([]);
  assert.equal(el.querySelectorAll(".link-group").length, 0);
  assert.equal(el.querySelector(".empty").textContent, "Nothing running");
});

// A link is 8 px a character wide here: its dot, its name and its port
const width = (l) => `${l.label}:${l.port}`.replace(/\s+/g, "").length * 8;
Object.defineProperty(El.prototype, "offsetWidth", { get() { return this.textContent.replace(/\s+/g, "").length * 8; }, configurable: true });

test("the columns are as wide as the widest link, measured once the links change", () => {
  const el = page.el("links-fit");
  paintLinks(el, state, LINKS);
  const widest = Math.max(...LINKS.map(width));
  assert.equal(el.styleValues["--link-width"], `${widest}px`);
  const long = link("A much longer name for a console", "gamma-ui", "API Management", true, "8086");
  paintLinks(el, state, [long]);
  assert.equal(el.styleValues["--link-width"], `${width(long)}px`, "measured again for other links");
  paintLinks(el, state, LINKS);
  assert.equal(el.styleValues["--link-width"], `${widest}px`);
  for (const anchor of el.querySelectorAll(".link")) assert.equal(anchor.styleValues.width, undefined, "each is back to its own width once measured");
});

test("links drawn out of view are measured when they come into it, so that a pane is the same whether it was shown first or later", () => {
  const hidden = page.el("links-later");
  hidden.hidden = true;   // the tab that holds them is not the one on screen
  paintLinks(hidden, state, LINKS);
  assert.equal(hidden.styleValues["--link-width"], undefined, "nothing to measure while it is not shown");
  paintLinks(hidden, state, LINKS);   // the same links, drawn again by a refresh
  assert.equal(hidden.styleValues["--link-width"], undefined);
  hidden.hidden = false;
  page.resized.run();                 // it came into view: its size changed
  assert.equal(hidden.styleValues["--link-width"], `${Math.max(...LINKS.map(width))}px`);
  const before = hidden.styleValues["--link-width"];
  page.resized.run();
  assert.equal(hidden.styleValues["--link-width"], before, "once is enough");
});
