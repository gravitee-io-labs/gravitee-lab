// Where a playground deploys: This machine, or one environment of a platform registered with this lab, a place ("machine", or
// "<platform id>/<environment id>"). A playground goes to a place only when it can: its state says the kinds of platform its
// files let it go to (`deploys_to`), and each place the first reason a playground does not go there (`cannot`), judged by the
// server against what the gateways of its environment run. The place a person chose
// is the default of every playground, kept in this browser; without one, where API Management runs (This machine, or its
// environment in the Cloud, `state.core`): `chosenPlace` is the one function that decides it. The Start of a
// playground on a place is here too, because its page and its row in the list share it: a remote place is always confirmed
// first, in a dialog that names it.

import { api, store, recall, remember } from "../core.js";
import { toast, confirmPlan } from "../components/ui.js";
import { busy, runAction, runJob } from "../activity/jobs.js";
import { confirmStart } from "../pages/playground.js";

export const MACHINE = "machine";
export const THIS_MACHINE = { kind: "platform", id: MACHINE, name: "This machine" };

// Once a platform is registered every playground says where it deploys; before, nothing of this is drawn
export const registered = () => store.platforms.length > 0;

// A place as its platform and its environment read together: "Development (My Account)"
const placeLabel = (environment, platform) => `${environment} (${platform})`;

// Why a playground does not go to a place, as the place says it; "" when it goes
// A playground is named by its id in `cannot`, an add-on or a setup by its kind and its id
export const whyNot = (item, place) => place.cannot?.[item.kind === "playground" ? item.id : `${item.kind}:${item.id}`] || "";
export const goes = (item, place) => !whyNot(item, place);

// A place by its id. A platform that cannot be used (still being read, refusing its Account Token, not answering) is one place
// of its own id: it answers for the places it had, with its reason.
export const entry = (places, id) => places.find((p) => p.id === id || (p.state === "unusable" && id.startsWith(`${p.id}/`)));

// --- The place chosen ------------------------------------------------------------------------

// The place chosen on any page is the default of every playground, under one key: a person who works in one environment finds
// every playground set to it. What the browser keeps; where it keeps nothing (a private window, storage blocked), what was chosen
// since the page loaded.
const KEY = "deploy";
let held = null;
const kept = () => recall(KEY) ?? held;
// The place proposed first: the one kept, else where API Management runs
const proposed = () => kept() ?? store.state?.core?.place ?? null;

// An add-on or a setup is on the place chosen for it, which is its own: it runs on one platform or the other whatever the others
// do. Kept as the place of a playground is, under a key of its own; without one, where API Management runs.
const own = (item) => `deploy:${item.kind}:${item.id}`;
const ownHeld = new Map();
const proposedFor = (item) => (item.kind === "playground" ? proposed() : recall(own(item)) ?? ownHeld.get(own(item)) ?? store.state?.core?.place ?? null);

export function keepPlace(id) {
  remember(KEY, id);
  held = recall(KEY) === id ? null : id;
}

// An add-on or a setup chosen a place: This machine too, which is a choice of its own (the key holds "machine")
export function keepItemPlace(item, id) {
  remember(own(item), id);
  if (recall(own(item)) === id) ownHeld.delete(own(item));
  else ownHeld.set(own(item), id);
}

// Where API Management runs, chosen again, is the default of every playground from then on: the place kept is forgotten
export function forgetPlace() {
  remember(KEY, null);
  held = null;
}

// The place a playground is on: the one proposed (kept, else where API Management runs), as the places say it, while the playground can go there;
// else This machine (null): Acme Bakery, which needs Gamma, is on This machine with API Management in the Cloud. A platform that cannot be
// used answers for the place proposed, with its reason: it is not given up for This machine.
export function chosenPlace(item) {
  const id = registered() ? proposedFor(item) : null;
  const place = id && entry(store.places, id);
  return place && goes(item, place) ? place : null;
}

export const chosenId = (item) => chosenPlace(item)?.id || MACHINE;

// A place was proposed and the places are not read yet: it is not known which one a click would deploy to. A playground that goes
// to no platform is on This machine whatever was kept.
export const holding = (item) => registered() && (item.deploys_to || []).length > 0 && !store.placesRead && ![null, MACHINE].includes(proposedFor(item));

// Why the Start, Deploy and Re-seed of the chosen place cannot be used now, in the words of the places; "" when they can, and
// for This machine
export function barred(item) {
  if (holding(item)) return "Loading";
  const place = chosenPlace(item);
  return place && place.state !== "ready" ? place.reason : "";
}

// Every place of the platforms, by platform, each platform's environments in the order of its account
export const placesOf = () => [...store.places].sort((a, b) => a.platform.name.localeCompare(b.platform.name));

// A record that a seed left in a place, named as its place is; when the place is gone with its platform, by the platform's name and
// the environment's that the record kept
export function recordName({ place, environment }) {
  const found = store.places.find((p) => p.id === place);
  if (found) return found.name;
  const id = place.split("/")[0];
  return placeLabel(environment, store.platforms.find((p) => p.id === id)?.name ?? id);
}

// --- Start on a place -----------------------------------------------------------------------

// Start, Deploy or Re-seed on a remote place always ask first, the place named in the last step ("Deploys to"): what else the action
// does is said with it, as the checks of that place say it, read now. The job is the same for all three, `route` being the one that
// starts it. It sends the place back, and what the dialog showed.
export async function deployTo(item, place, { route, verb, label }) {
  const key = `${item.kind}:${item.id}`;
  let read;
  try {
    read = await busy(key, verb, api(`/api/${item.kind}s/${item.id}/check?place=${encodeURIComponent(place.id)}`), { quiet: true });
  } catch (err) {
    return toast(err.message, true);
  }
  const dialog = { deploys_to: [{ kind: "environment", id: place.id, name: place.name }], ...read.dialog };
  // A start that stops a playground asks what to do with what it created, as that one's Stop does
  const chosen = route === "start" && item.kind === "playground" ? await confirmStart(item, label, dialog) : undefined;
  if (chosen === null) return;
  const accept = chosen ? chosen.accept : await confirmPlan(`${label} ${item.name}`, label, dialog);
  if (!accept) return;
  runJob(() => api(`/api/${item.kind}s/${item.id}/${route}`, { place: place.id, accept, ...(chosen?.remove ? { remove: true } : {}) }), { key, verb });
}

// The Start of a playground, on its page and on its row. On This machine it is what it always was, and sends no place; its dialog,
// when it opens (`dialog`: the page's, which the checks read, else the state's), names This machine in its last step once a platform
// is registered. On a remote place it deploys there.
export function startPlayground(item, dialog = item.action.dialog) {
  const place = chosenPlace(item);
  if (place) return deployTo(item, place, { route: "start", verb: "start", label: item.action.label });
  const shown = dialog && registered() ? { ...dialog, deploys_to: [THIS_MACHINE] } : dialog;
  return startHere(item, shown);
}

// The Start on This machine: on images it was not tested with, its dialog is the start that switches to the versions it was tested with
// (`offer`), or starts without; when it stops a playground that created something in Gravitee, it asks to keep it or to delete it; else
// it is what it always was. The offer names This machine in its last step as the dialog does.
async function startHere(item, dialog) {
  const offer = item.action.offer && { ...item.action.offer, ...(dialog?.deploys_to ? { deploys_to: dialog.deploys_to } : {}) };
  const chosen = await confirmStart(item, item.action.label, dialog, { offer });
  if (chosen === undefined) return runAction(item, { ...item.action, dialog });
  if (chosen === null) return;
  runJob(() => api(`/api/playgrounds/${item.id}/start`, { accept: chosen.accept, ...(chosen.remove ? { remove: true } : {}),
    ...(chosen.switch ? { switch: true } : {}) }),
    { key: `playground:${item.id}`, verb: "start" });
}
