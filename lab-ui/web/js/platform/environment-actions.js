// What a person does to an environment of a platform: set it up, create or start or stop its hybrid gateway, add a license, add or replace
// its Cloud Token. One implementation for the Gravitee Cloud page and the Dashboard, each saying how to read the platform again once it is done
// (`done`). A gateway runs here as an add-on, and the same job shows on the add-on's own buttons wherever it was started.

import { api, store, router } from "../core.js";
import { toast, confirmEffects, planRows, accepted } from "../components/ui.js";
import { runJob, busy, runAction } from "../activity/jobs.js";
import { environmentStatus, addonOf, hybrid, runs, stopOf } from "./environments.js";
import { License } from "../dialogs/license.js";
import { openSettings } from "../dialogs/editor.js";
import { openConnect, openCreateGateway, openCloudToken, openUseEnvironment } from "../dialogs/platform.js";

// The name of the environment whose gateway runs here now, when another one than `envId`'s does: a start of this one stops it
export function holder(platform, check, envId) {
  const here = (check?.gateways || []).filter(hybrid).find((gateway) => gateway.here && gateway.environmentId !== envId);
  return here && runs(addonOf(platform)) ? (check.environments || []).find((env) => env.id === here.environmentId)?.name || here.name : "";
}

// Starts a gateway, which swaps the one that runs here when it is another. The server says first what that does (`plan`, with the
// environment it serves and the one it stops serving); the same job shows on the add-on's own buttons. `done` reads the platform again.
export async function startGateway(platform, gateway, done) {
  const addon = addonOf(platform);
  if (!gateway?.id || !addon) return;
  const key = `addon:${addon.id}`;
  let plan;
  try {
    plan = await busy(key, "start", api(`/api/platforms/${platform.id}/gateways/${gateway.id}/plan`), { quiet: true });
  } catch (err) {
    return toast(err.message, true);
  }
  const dialog = plan?.dialog ?? plan;   // null: the server has nothing to say first
  const rows = dialog ? planRows(dialog) : [], asks = rows.some((r) => r.items.length);
  if (asks && !(await confirmEffects(`Start ${gateway.name}`, rows, "Start"))) return;
  await runJob(() => api(`/api/platforms/${platform.id}/gateways/${gateway.id}/start`, { accept: asks ? accepted(dialog) : [] }), { key, verb: "start" });
  done?.();
}

// Stops the add-on, with what it says of its own Stop
async function stopGateway(platform, done) {
  const addon = addonOf(platform), stop = stopOf(addon);
  if (!stop) return;
  await runAction(addon, stop);
  done?.();
}

// A gateway made here runs here: once the platform is read again it is started, unless the add-on lacks a license, which its row says
async function started(platform, last, id, again) {
  await again();
  const gateway = id && last().gateways.find((found) => found.id === id);
  if (gateway && !addonOf(platform)?.note_file) startGateway(platform, gateway, again);
}

// The form or the job of an environment's action (`what`: set-up, create-gateway, add-token, replace-token, add-license, settings (of the
// add-on that runs the gateway, which lacks some), start-gateway or stop-gateway; add-catalog, which leads to the Catalogs page: the
// add-on that runs a gateway only a catalog provides), from the platform's last check. `reread` reads that check again and answers when it has; `last` answers the check read.
export function perform(platform, last, env, what, reread) {
  const found = last(), environments = found.environments, done = () => reread();
  const st = environmentStatus(platform, found, env), replaces = holder(platform, found, env.id);
  if (what === "set-up") openUseEnvironment({ platform, environment: env, runs: !!addonOf(platform), replaces, done: (made) => started(platform, last, made?.gatewayId, reread) });
  else if (what === "create-gateway") {
    openCreateGateway({ platform, environments, environmentId: env.id, title: st.action?.label || "Create gateway", label: "Create and start", replaces,
      done: (made) => started(platform, last, made?.gateway?.id, reread) });
  } else if (what === "add-token") openCloudToken({ platform, environments, environmentId: env.id, done });
  else if (what === "replace-token") openCloudToken({ platform, environments, environmentId: env.id, replacing: true, done });
  else if (what === "add-license") License.show({ form: true });
  else if (what === "settings") openSettings(addonOf(platform));
  else if (what === "start-gateway") startGateway(platform, found.gateways.find((gateway) => gateway.id === st.gateway.id), done);
  else if (what === "stop-gateway") stopGateway(platform, done);
  else if (what === "add-catalog") router.go("#/catalogs");
}

// Connecting an account is the first step of two, in one dialog: once it is connected the pages show it (`done(platform)` reads the
// platforms again), and the second step links a first environment (a gateway that runs here, the Cloud Token that allows deploys), which
// a person may skip. A gateway made there runs here, as from the page.
export function connectAccount(done) {
  openConnect({
    added: done,
    runs: (platform) => !!addonOf(platform),
    done: (platform, check, made) => {
      let latest = check;
      const reread = async () => {
        latest = await api(`/api/platforms/${platform.id}/check`, {});
        done(platform);
      };
      started(platform, () => latest, made?.gatewayId, reread);
    },
  });
}

// Anything that offers to connect an account asks for it by this event (the License dialog, which this module imports: no import back); the
// account is shown on the Gravitee Cloud page once it is connected
document.addEventListener("lab:connect-cloud", () => connectAccount(() => {
  store.refresh();
  location.hash = "#/cloud";
}));
