// Where API Management runs: This machine (Docker runs the whole platform, as it always did) or one environment of a Gravitee Cloud
// account the Lab is linked to (it keeps its Cloud Token or a hybrid gateway of it), with its hybrid gateway on this machine. Only API
// Management can run in the Cloud: the rest of the platform stays here. Its group of the platform is the server's (`state.core.group`),
// read from data, never named here. One setting of this machine (`state.core`, POST /api/core), chosen in the welcome dialog, on API
// Management's row of the Dashboard and in the head of the Gravitee Cloud page. It is the place proposed first for what deploys (places.js),
// and what the Dashboard's Start all and Stop all follow. Choosing it starts and stops nothing.
//
// Choosing Gravitee Cloud goes where it can be done: with no account, Connect Gravitee Cloud; with an account and no environment linked,
// the Set up of its first one; with several linked environments, a picker of them. The choice is kept once an environment is linked.
//
// A gateway for the Cloud is run by an add-on that only a catalog provides, so every control that needs one says why it cannot be used when
// none can (gatewayWhy), in the words of the lists while the catalogs are on their way or missing.

import { esc, icon, api, store } from "../core.js";
import { toast, confirmPlan, tipAttrs } from "../components/ui.js";
import { runJob, busy } from "../activity/jobs.js";
import { Combo } from "../components/combo.js";
import { MACHINE, forgetPlace } from "./places.js";
import { connectAccount, perform } from "./environment-actions.js";
import { gatewayGap } from "./environments.js";

export const coreOf = () => store.state?.core || { place: MACHINE, chosen: false, group: null };
export const onCloud = () => coreOf().place !== MACHINE;
// The group of the platform that can run in the Cloud, {id, name}; null when the platform has none, and then there is no choice
export const movable = () => coreOf().group || null;
const value = () => (onCloud() ? "cloud" : "machine");

// Why the Cloud cannot be chosen, or its Start all, its Create gateway, used now: that no add-on runs a gateway of that kind of platform
// ("Needs a gateway add-on"), or, while the catalogs are downloading, failed or none, the reason the lists give. "" when it can, and for a
// state that does not say (environments.js).
export const gatewayWhy = (kind = "cloud") => gatewayGap(kind)?.why ?? "";

// The two choices: their names, the icon of each, the words of its card in the welcome dialog, what it changes in its tooltip, said of
// the product by its name. What a platform runs, and in which version, changes with time: it is never said here.
export const CHOICES = {
  machine: { label: "This machine", card: "On this machine", icon: "laptop",
    text: (name) => `Docker runs ${name} here, with the rest of the platform.`,
    tip: (name) => `Docker runs ${name} here. Start all starts it with the rest of the platform.` },
  cloud: { label: "Gravitee Cloud", card: "Gravitee Cloud", icon: "cloud",
    text: (name) => `${name} runs in your Cloud environment, with a hybrid gateway here. The rest of the platform stays on this machine.`,
    tip: (name) => `${name} runs in your Cloud environment. Start all starts its hybrid gateway and the rest of the platform here, not the local ${name}, and playgrounds deploy there first.` },
};
// What the control is, for a screen reader and on the Gravitee Cloud page
const question = () => `Where ${movable()?.name || ""} runs`;

// An environment the Lab is linked to: it keeps its Cloud Token, or a hybrid gateway of it
const linked = (place) => !!place.environment && (place.token?.state !== "unset" || !!place.gateway);
export const linkedPlaces = () => store.places.filter(linked);
// Of several environments, Development first, as Connect proposes it
const preferred = (list) => list.find((place) => /^dev/i.test(place.environment?.name || "")) || list[0];

// --- Drawn -------------------------------------------------------------------------------

// The choice as a segmented control, each segment saying what it changes. On the Dashboard's row it is its two icons (`words` false), the
// row having the room of nothing more; in a page's head, its question then its two names. Nothing without a group that can move.
export function runsControl({ words = false } = {}) {
  const group = movable();
  if (!group) return "";
  const on = value(), why = gatewayWhy();
  // The Cloud cannot be chosen while nothing can run its gateway: it keeps the pointer and the keyboard, and says why instead of what it changes
  const segments = Object.entries(CHOICES).map(([key, choice]) => `<button type="button" role="radio" aria-checked="${key === on}" data-core="${key}"${
    words ? "" : ` aria-label="${choice.label}"`}${key === "cloud" && why ? ` aria-disabled="true" data-why="${esc(why)}"`
      : tipAttrs("", { title: choice.label, text: choice.tip(group.name) })}>${words ? choice.label : icon(choice.icon)}</button>`).join("");
  const control = `<span class="segmented${words ? "" : " runs-icons"}" role="radiogroup" aria-label="${esc(question())}">${segments}</span>`;
  return words ? `<span class="runs-control"><span class="runs-label" aria-hidden="true">${esc(question())}</span>${control}</span>` : control;
}

// The environment where API Management runs, chosen among the linked ones when there are several; nothing otherwise. `short`: it says the
// environment alone, its account in its tooltip, where a row has the room of a version.
export function environmentControl({ short = false } = {}) {
  if (!onCloud() || linkedPlaces().length < 2) return "";
  const { name, environment } = coreOf(), said = short ? environment?.name || name : name;
  return `<button class="combo-btn core-env" data-core-env aria-haspopup="dialog" aria-label="Environment where ${esc(movable()?.name || "")} runs: ${esc(name)}"${
    short ? ` data-tip="${esc(name)}"` : ""}><span class="value">${esc(said)}</span>${icon("chevron")}</button>`;
}

// --- Chosen ------------------------------------------------------------------------------

// The choice kept, read again in the state: the default of every playground from then on. What it does is said in its tooltip.
async function save(place) {
  try {
    const answer = await busy("core", "save", api("/api/core", { place }), { quiet: true });
    forgetPlace();
    toast(`${movable()?.name || ""}: ${place === MACHINE ? "This machine" : answer.name}`);
  } catch (err) {
    toast(err.message, true);
  }
  await store.refresh();
}

// An environment among the linked ones, at the control that asked
export function pickEnvironment(anchor) {
  Combo.open(anchor, {
    plain: true, current: coreOf().place,
    groups: () => [{ items: linkedPlaces().map((place) => ({ value: place.id, name: place.name })) }],
    choose: (place) => { if (place !== coreOf().place) save(place); },
  });
}

// An account just connected, or an environment just set up: its first linked environment is where API Management runs
async function adopt(platform) {
  await store.refresh();
  await store.refreshPlaces();
  const mine = linkedPlaces().filter((place) => place.platform.id === platform.id);
  if (mine.length) await save(preferred(mine).id);
}

// An account with no environment linked: the Set up of its first one (Development first), whose end makes it the choice
async function setUpFirst(platform, connected) {
  let check;
  try {
    check = await api(`/api/platforms/${platform.id}/check`, {});
  } catch (err) {
    return toast(err.message, true);
  }
  store.cloud.checks.set(platform.id, check);
  const environments = check.state === "ok" ? check.environments || [] : [];
  if (!environments.length) return toast(check.message || `No environment in ${platform.name}`, true);
  const env = environments.find((found) => /^dev/i.test(found.name)) || environments[0];
  const reread = async () => {
    check = await api(`/api/platforms/${platform.id}/check`, {});
    store.cloud.checks.set(platform.id, check);
    connected?.(platform);
    await adopt(platform);
  };
  perform(platform, () => check, env, "set-up", reread);
}

// A choice of the control, of a card or of the picker. This machine is kept at once. Gravitee Cloud is kept once an environment is linked:
// with no account, through Connect Gravitee Cloud; with one linked environment, at once; with several, chosen among them; with none, through
// the Set up of the first one. `anchor`: where the picker opens; `connected(platform)`: what the page reads again once an account changed.
export async function chooseCore(choice, { anchor = null, connected = null } = {}) {
  if (choice === "machine") {
    if (onCloud() || !coreOf().chosen) await save(MACHINE);
    return;
  }
  if (onCloud() || gatewayWhy()) return;   // a control that cannot be used says why; this is for a key that reached it all the same
  if (!store.platforms.length) {
    return connectAccount((platform) => {
      connected?.(platform);
      adopt(platform);
    });
  }
  await store.refreshPlaces();
  const found = linkedPlaces();
  if (found.length === 1) return save(found[0].id);
  if (found.length > 1 && anchor) return pickEnvironment(anchor);
  if (found.length > 1) return save(preferred(found).id);
  return setUpFirst(store.platforms[0], connected);
}

// --- Start all and Stop all --------------------------------------------------------------

// With API Management in the Cloud, the Dashboard's Start all starts what runs here: the hybrid gateway of its environment, the rest of
// the platform and what is on, without the local API Management. Its dialog always opens, the platform read again, and names the
// environment it serves and what it does not start. Stop all stops those.
export async function runCore(verb) {
  if (verb === "stop") return runJob(() => api("/api/stack/stop", { core: true }), { key: "platform", verb });
  let plan;
  try {
    plan = await busy("platform", "start", api("/api/core/plan"), { quiet: true });
  } catch (err) {
    return toast(err.message, true);
  }
  const accept = await confirmPlan(`Start all for ${coreOf().name}`, "Start", plan.dialog);
  if (!accept) return;
  runJob(() => api("/api/stack/start", { core: true, accept }), { key: "platform", verb: "start" });
}
