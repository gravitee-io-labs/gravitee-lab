// What is asked of a person about a Gravitee Cloud account: to connect it, to rename it, to replace its Account Token, to create a hybrid
// gateway in one of its environments, to give an environment its Cloud Token, and to start using an environment with both in one
// dialog. Connecting is a wizard of two steps in one large dialog, the account then a first environment, beside the pictures of the
// console that show where each value is; the second step alone is the dialog that sets up an environment. The others are short forms in
// modal dialogs, opened from the Gravitee Cloud page, the Dashboard and the Before you start lines of a playground. A platform is a
// Gravitee Cloud, the only kind there is, which the server takes when none is sent. The Account ID is asked because an Account Token
// reaches its account by that id alone: no call lists accounts. The token of a hybrid gateway is never asked or shown: the server keeps
// it. Cancel and Escape drop what was typed. A form stays open until the server answered, so that a refusal shows in it, and its secrets
// are cleared as it closes. A form opened for one environment says so in its title and does not ask which one.

import { $, $$, esc, icon, api, store, setHTML } from "../core.js";
import { toast, tipHtml } from "../components/ui.js";
import { TERMS } from "../platform/environments.js";
import { dialogOf, formDialog, field, note } from "./form.js";

// The kinds of platform there are: what each is called, where it lives and what its credentials are called, which the
// forms use
export const KINDS = { cloud: { name: "Gravitee Cloud", url: "https://eu.cloud.gravitee.io", token: "Account Token", account: "Account ID" } };
const [FIRST] = Object.values(KINDS);   // there is one kind: nothing to choose

// A secret is hidden as it is typed and has its eye, which shows it while a person checks what they pasted
const eye = (shown = false) => `<button type="button" class="icon-btn" data-reveal-secret aria-label="${shown ? "Hide" : "Show"}" data-tip="${shown ? "Hide" : "Show"}">${icon(shown ? "eye-off" : "eye")}</button>`;
const input = (id, attrs = "") => `<div class="search"><input id="${id}" spellcheck="false" ${attrs}>${attrs.includes("data-secret") ? eye() : ""}</div>`;
const secret = 'type="password" autocomplete="off" required data-secret';
const select = (id) => `<select id="${id}" required></select>`;
const options = (environments, chosen) => environments.map((env) =>
  `<option value="${esc(env.id)}" ${env.id === chosen ? "selected" : ""}>${esc(env.name)}</option>`).join("");
// The environments of a platform, as its page lists them: the ones given, else asked of the platform. Nothing when it
// cannot be asked, which is said in a toast.
async function environmentsOf(platform, given) {
  if (given) return given;
  try {
    return (await api(`/api/platforms/${platform.id}/check`, {})).environments;
  } catch (err) {
    toast(err.message, true);
    return null;
  }
}

// What a gateway made for an environment is called: its own, so that two of them are told apart in Gravitee Cloud. The Cloud takes
// letters, digits, spaces, dashes, dots and underscores.
function gatewayName(environment) {
  const part = (environment?.name || "").replace(/[^A-Za-z0-9\s\-_.]/g, "").trim();
  return part ? `Gravitee Lab - ${part}`.slice(0, 128) : "Gravitee Lab";
}

// What starting a gateway does to the one that runs here: one runs at a time
const takes = (replaces) => (replaces ? `The gateway of ${esc(replaces)} runs here now: starting this one stops it` : "");

// --- Rename ---------------------------------------------------------------------------

let naming = null;   // the platform renamed, and what runs once it is
const openName = formDialog("rename", field("rename-name", "Name", input("rename-name", 'maxlength="60" required')),
  { key: "rename:platform", verb: "save", label: "Save" },
  () => api(`/api/platforms/${naming.platform.id}/name`, { name: $("#rename-name").value.trim() }),
  () => {
    toast("Renamed");
    naming.done();
  }, () => ($("#rename-name").value.trim() === naming.platform.name ? "The name is the same" : ""));

// The name the Lab shows for an account: its id, its tokens and its gateways stay. `done()` runs once it is saved.
export function openRename({ platform, done }) {
  naming = { platform, done };
  openName(`Rename ${platform.name}`, () => { $("#rename-name").value = platform.name; });
}

// --- Replace Account Token ------------------------------------------------------------

let renewing = null;   // the platform whose Account Token is replaced, and what runs once it is
const openReplace = formDialog("account-token", field("account-token", FIRST.token, input("account-token", secret), TERMS.account),
  { key: "replace:account-token", verb: "replace", label: "Replace" },
  () => api(`/api/platforms/${renewing.platform.id}/token`, { token: $("#account-token").value.trim() }),
  () => {
    toast("Replaced");
    renewing.done();
  });

// The platform keeps its record, its Cloud Tokens and its gateways' tokens. `done()` runs once it is replaced.
export function openReplaceToken({ platform, done }) {
  renewing = { platform, done };
  openReplace(`Replace ${(KINDS[platform.kind] || FIRST).token}`, () => {
    $("#account-token").value = "";
  });
}

// --- Create hybrid gateway ------------------------------------------------------------

let creating = null;   // the platform whose gateway is created, and what runs once it is
const gatewayFields = `<div class="field" id="gateway-choice"><label for="gateway-environment">Environment</label>${select("gateway-environment")}</div>`
  + field("gateway-name", "Name", input("gateway-name", 'minlength="2" maxlength="128" required'))
  + note("gateway-replaces", "", true);
const openGateway = formDialog("gateway", gatewayFields, { key: "new:gateway", verb: "create", label: "Create" },
  () => api(`/api/platforms/${creating.platform.id}/gateways`, { environmentId: $("#gateway-environment").value,
    name: $("#gateway-name").value.trim() }),
  (answer) => {
    toast("Created");
    creating.done(answer);
  });

// A hybrid gateway in one of the environments of `platform` ({id, name}), `environmentId` first; `environments`
// ({id, name} each) are asked of the platform when they are not given. With an `environmentId` the form is for that
// environment alone: its title names it and it does not ask. `done(answer)` runs once it is created, with the gateway
// ({gateway: {id}}). The title is the label of what opened it, `label` the word of the button, `replaces` the name of the
// environment whose gateway runs here now, when one does.
export async function openCreateGateway({ platform, environments, environmentId, title = "Create hybrid gateway", label = "Create", replaces = "", done }) {
  const list = await environmentsOf(platform, environments);
  if (!list) return;
  creating = { platform, done };
  const chosen = list.find((env) => env.id === environmentId);
  openGateway(chosen ? `${title} for ${chosen.name}` : title, () => {
    $("#gateway-environment").innerHTML = options(list, environmentId);
    $("#gateway-choice").hidden = !!chosen;
    $("#gateway-name").value = gatewayName(chosen);
    $("#gateway-replaces").hidden = !replaces;
    $("#gateway-replaces").innerHTML = takes(replaces);
  }, label);
}

// --- Add Cloud Token ------------------------------------------------------------------

let tokening = null;   // the platform whose environment is given a Cloud Token, and what runs once it has
// Adding a token and replacing one are two forms, each with its own button and its own words at work
function tokenDialog(id, action, said) {
  const fields = `<div class="field" id="${id}-choice"><label for="${id}-environment">Environment</label>${select(`${id}-environment`)}</div>`
    + field(`${id}-secret`, "Cloud Token", input(`${id}-secret`, secret), TERMS.token);
  const open = formDialog(id, fields, action,
    () => {
      const environment = encodeURIComponent($(`#${id}-environment`).value);
      return api(`/api/platforms/${tokening.platform.id}/environments/${environment}/token`, { token: $(`#${id}-secret`).value.trim() });
    },
    () => {
      toast(said);
      tokening.done();
    });
  return (title, list, chosen, known) => open(title, () => {
    $(`#${id}-environment`).innerHTML = options(list, chosen);
    $(`#${id}-choice`).hidden = !!known;
    $(`#${id}-secret`).value = "";
  });
}
const openAddToken = tokenDialog("cloud-token", { key: "new:cloud-token", verb: "add", label: "Add" }, "Added");
const openReplaceCloudToken = tokenDialog("replace-cloud-token", { key: "replace:cloud-token", verb: "replace", label: "Replace" }, "Replaced");

// The Cloud Token of one environment of `platform` ({id, name}), `environmentId` first, as Add Cloud Token or, with
// `replacing`, as Replace Cloud Token; `environments` ({id, name} each) are asked of the platform when they are not
// given. With an `environmentId` the form is for that environment alone: its title names it and it does not ask. `done()`
// runs once it is kept.
export async function openCloudToken({ platform, environments, environmentId, replacing = false, done }) {
  const list = await environmentsOf(platform, environments);
  if (!list) return;
  tokening = { platform, done };
  const chosen = list.find((env) => env.id === environmentId);
  (replacing ? openReplaceCloudToken : openAddToken)(`${replacing ? "Replace" : "Add"} Cloud Token${chosen ? ` for ${chosen.name}` : ""}`, list, environmentId, !!chosen);
}

// --- Connect Gravitee Cloud, and set up an environment ----------------------------------

// One large dialog, two steps: the account, then a first environment, which a person may skip. The second step is also the dialog that
// sets up an environment of the Gravitee Cloud page (Set up): the same panel alone, for that environment, without the steps. Beside the
// fields of each step is where the console shows what they ask: the numbered lines of the left are the numbers of its pictures.

// What an Account Token reaches is the paths of its own account, by its id: no call of the Cloud lists the accounts of a token or says
// whose it is, and the token, a UUID, carries no claim, so the Account ID is asked, next to the token, both from the one page of the
// console that shows them; the information mark of each field says what it is and where it is. The name of the account is read from the
// Cloud once it is connected, and renamed from the account's menu. The address is the Cloud of Europe, the only one with a known
// address; another (the fake Cloud, a Cloud of one's own) is under More options, and the link to the console follows it.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const REGISTER = "https://eu.cloud.gravitee.io/?createUser=true";   // a new Gravitee Cloud account
// Where the Cloud Tokens of a connected account are made: its own page of the Cloud, else the Cloud's address
const cloudTokensUrl = (platform) => {
  const base = (platform.url || FIRST.url).replace(/\/+$/, ""), hrid = platform.account?.hrid;
  return hrid ? `${base}/cloud/accounts/${encodeURIComponent(hrid)}/settings/cloud-tokens` : base;
};
const consoleLink = (id) => `<a class="btn" id="${id}" href="${esc(FIRST.url)}" target="_blank" rel="noopener">${icon("external")}Open Gravitee Cloud</a>`;
// What to do in the console, numbered as its pictures number it; a line that has a picture of its own shows it once clicked (`shot`)
const todo = (id, lines) => `<ol class="steps connect-steps" id="${id}">${lines.map(([line, shot]) =>
  `<li class="step"${shot === undefined ? "" : ` data-shot="${shot}"`}><span class="step-num" aria-hidden="true"></span><span class="step-body">${line}</span></li>`).join("")}</ol>`;
// The pictures of the console, one at a time: a segmented control above them chooses among several. Under the fields on a narrow
// screen, folded on a phone.
const guide = (id, shots) => `<figure class="connect-guide">
    <button type="button" class="connect-toggle" aria-expanded="false" aria-controls="${id}-shots">${icon("chevron")}Show where to find it</button>
    <div class="connect-shots" id="${id}-shots">${shots.length < 2 ? "" : `<div class="segmented" role="radiogroup" aria-label="Picture">${shots.map((shot, i) =>
      `<button type="button" role="radio" aria-checked="${i === 0}" data-shot="${i}">${i + 1} ${shot.name}</button>`).join("")}</div>`}
      <div class="connect-shot">${shots.map((shot, i) =>
        `<img src="/img/${shot.file}" width="${shot.width}" height="${shot.height}" alt="${esc(shot.alt)}" data-shot="${i}"${i ? " hidden" : ""}>`).join("")}</div></div>
  </figure>`;

const connectFields = `<div class="connect-body"><div class="connect-fields">
    <section class="connect-section">
      <div class="connect-open"><p class="connect-ask">Do you already have a Gravitee Cloud account?</p>${consoleLink("platform-open")}
        <p class="connect-register">If not, <a class="text-link" href="${REGISTER}" target="_blank" rel="noopener">start a 14-day trial</a></p></div>
      ${todo("platform-todo", [["In Settings, Account Tokens, generate an Account Token and copy it"], ["Copy the Account ID, at the top of the same page"]])}
    </section>
    <section class="connect-section">
      ${field("platform-token", FIRST.token, input("platform-token", secret), TERMS.account)}
      ${field("platform-account", FIRST.account, input("platform-account", 'required aria-describedby="platform-account-wrong"')
        + '<p class="field-note err" id="platform-account-wrong" role="status" hidden>The Account ID is a UUID, as the Account Tokens page shows it</p>'
        + '<p class="field-note" id="platform-account-known" role="status" hidden></p>', TERMS.accountId)}
      <details class="more-options" id="platform-more"><summary>${icon("chevron")}More options</summary>
        ${field("platform-url", "URL", input("platform-url", 'type="url" required'))}</details>
    </section>
  </div>${guide("platform", [{ file: "cloud-account-token.png", width: 1194, height: 721,
    alt: "Gravitee Cloud, Settings, Account Tokens: 1, generate and copy an Account Token; 2, copy the Account ID shown at the top of the page" }])}</div>`;

// What the form lacks beyond its required fields, in plain words: an Account ID that is no UUID (said once it is whole or left, and the
// field marked invalid), an address that is no web address (whose options then open)
let registered = [];   // the accounts registered, read when the form opens: the state does not carry their Account ID
function connectLacks() {
  const account = $("#platform-account"), id = account.value.trim(), url = $("#platform-url").value.trim();
  const wrong = !!id && !UUID.test(id) && (document.activeElement !== account || id.length >= 36);
  $("#platform-account-wrong").hidden = !wrong;
  if (wrong) account.setAttribute("aria-invalid", "true");
  else account.removeAttribute("aria-invalid");
  // An account that is connected already is told by its Account ID, before anything is sent: its name, and the way to give it
  // another Account Token
  const known = UUID.test(id) ? registered.find((p) => p.account?.id?.toLowerCase() === id.toLowerCase()) : null;
  const said = $("#platform-account-known");
  said.hidden = !known;
  said.dataset.platform = known?.id || "";
  setHTML(said, known ? `Already connected as ${esc(known.name)}. <button type="button" class="text-link" data-replace-token>Replace its Account Token</button>` : "");
  if (known) return `Already connected as ${known.name}`;
  if (url && !/^https?:\/\/[^\s/]+/.test(url)) {
    $("#platform-more").open = true;
    return "The URL starts with https://";
  }
  return id && !UUID.test(id) ? "The Account ID is a UUID" : "";
}

// The account is registered, then its environments are read, in the same wait of the button: what could not be read is no refusal,
// since the account is connected, and ends the wizard with a toast
async function connect() {
  const platform = await api("/api/platforms", { url: $("#platform-url").value.trim(), token: $("#platform-token").value.trim(),
    account: $("#platform-account").value.trim() });
  let check;
  try {
    check = await api(`/api/platforms/${platform.id}/check`, {});
  } catch (err) {
    check = { state: "error", message: err.message };
  }
  return { platform, check };
}

let connecting = null;   // what opened Connect: {added(platform), runs(platform), done(platform, check, made)}
function connected({ platform, check }) {
  $("#platform-token").value = "";   // kept by the Lab now: the dialog holds it no longer
  connecting.added(platform);   // the pages show the account behind the dialog
  const environments = check.state === "ok" ? check.environments || [] : [];
  if (!environments.length) {
    wizard.close();
    toast(`${platform.name} connected${check.state === "ok" ? ", with no environment yet" : ""}`);
    if (check.state !== "ok") toast(check.message || "Its environments cannot be read", true);
    return;
  }
  // The step it leaves is checked in the head, and the page behind shows the account: no toast says it again
  setUp({ platform, list: environments, runs: connecting.runs(platform), done: (made) => connecting.done(platform, check, made) });
}

// The two optional parts of starting to use an environment, in the order a person thinks of them: a gateway that runs here, then the
// Cloud Token that allows deploys. Each is a box to tick, ticked, its fields shown while it is; a part that is done stays done if the
// other one is refused, so that sending it again does only what is left, and the page reads the platform again however the dialog
// closes. The token is sent first, because it is proved before it is kept and asks nothing of Cloud, while a gateway is made in Cloud.
// The gateway is made here and the page starts it, through the same plan and confirmation as a Start.
let using = null;   // the environment being set up: {platform, environment, list, done, finished, reported}
const useStep = (id, label, term, fields) => `<fieldset class="connect-section" id="${id}-step">
    <div class="use-head"><label class="use-pick"><input type="checkbox" id="${id}"><span>${label}</span></label>
      ${tipHtml(icon("info"), term, "plat-info")}<span class="muted" id="${id}-done" hidden>Done</span></div>
    <div class="use-fields" id="${id}-fields">${fields}</div>
  </fieldset>`;
const useFields = `<div class="connect-body"><div class="connect-fields">
    <section class="connect-section" id="use-choice"><div class="field"><label for="use-environment">Environment</label>${select("use-environment")}</div></section>
    ${useStep("use-gateway", "Run a gateway here", TERMS.gateway, field("use-gateway-name", "Name", input("use-gateway-name", 'minlength="2" maxlength="128" required'))
      + note("use-replaces", "", true))}
    ${useStep("use-deploys", "Allow deploys", TERMS.token, `<div class="connect-open">${consoleLink("use-open")}</div>`
      + todo("use-todo", [["In Settings, Cloud Tokens, generate a Cloud Token", 0],
        ['Choose the type Automation and the environment <span id="use-env-name"></span>, confirm, then copy the token', 1]])
      + field("use-token", "Cloud Token", input("use-token", secret)))}
  </div>${guide("use", [
    { name: "Generate", file: "cloud-token-generate.png", width: 1336, height: 695, alt: "Gravitee Cloud, Settings, Cloud Tokens: the Generate Cloud Token button" },
    { name: "Choose the environment", file: "cloud-token-environment.png", width: 1336, height: 768,
      alt: "The Generate Cloud Token dialog: a name, the type Automation and the target environment" }])}</div>`;

const STEPS = [["use-gateway", "use-gateway-name", "gateway"], ["use-deploys", "use-token", "token"]];
// The environment of the wizard can be chosen until a part is done for it: the select and its picker
const pick = (on) => [$("#use-environment"), $("#use-choice .combo-btn")].forEach((el) => { if (el) el.disabled = !on; });
// What is left to do: the parts ticked that were not done yet
const wanted = () => STEPS.filter(([box, , name]) => $(`#${box}`).checked && !using.finished.has(name)).map(([, , name]) => name);

// The fields of a part show while it is ticked
function sync() {
  for (const [box, input] of STEPS) {
    const on = $(`#${box}`).checked;
    $(`#${input}`).disabled = !on;
    $(`#${box}-fields`).hidden = !on;
  }
}

async function useEnvironment() {
  if (using.list) using.environment = using.list.find((env) => env.id === $("#use-environment").value) || using.environment;
  const { platform, environment, finished } = using, left = wanted();
  const mark = (name) => {
    finished.add(name);
    const [box] = STEPS.find((entry) => entry[2] === name);
    $(`#${box}`).checked = false;
    $(`#${box}`).disabled = true;
    $(`#${box}-done`).hidden = false;
    sync();
    pick(false);   // what is left is for the environment that part was done for
  };
  const made = {};
  if (left.includes("token")) {
    await api(`/api/platforms/${platform.id}/environments/${encodeURIComponent(environment.id)}/token`, { token: $("#use-token").value.trim() });
    mark("token");
  }
  if (left.includes("gateway")) {
    made.gatewayId = (await api(`/api/platforms/${platform.id}/gateways`, { environmentId: environment.id, name: $("#use-gateway-name").value.trim() })).gateway?.id;
    mark("gateway");
  }
  return made;
}

const STEP_NAMES = ["Account", "Environment"];
const wizard = dialogOf("connect", [
  { fields: connectFields, action: { key: "new:platform", verb: "connect", label: "Connect" }, submit: connect, after: connected,
    lacks: connectLacks, stays: true },
  { fields: useFields, action: { key: "use:environment", verb: "setup", label: "Set up" }, submit: useEnvironment,
    after: (made) => {
      using.reported = true;
      toast(`${using.environment.name} is set up`);
      using.done(made);
    }, lacks: () => (wanted().length ? "" : "Choose a step") },
], { large: true, steps: STEP_NAMES,
  // A part that was done is done even when the dialog is closed without the other: the page says so
  closed: () => {
    if (!using?.finished.size || using.reported) return;
    using.reported = true;
    store.refresh();
    using.done({});
  } });
const [connectForm, useForm] = wizard.forms;

// The note of an account that is connected already leads to the form that replaces its Account Token
$("#platform-account-known").addEventListener("click", (e) => {
  if (!e.target.closest("[data-replace-token]")) return;
  const platform = registered.find((p) => p.id === $("#platform-account-known").dataset.platform);
  if (!platform) return;
  wizard.close();
  openReplaceToken({ platform, done: () => connecting?.added(platform) });
});
// An Account ID is judged once it is left
$("#platform-account").addEventListener("blur", (e) => e.target.dispatchEvent(new Event("input", { bubbles: true })));
// The link to the console follows the address typed under More options
$("#platform-url").addEventListener("input", (e) => {
  const url = e.target.value.trim();
  $("#platform-open").setAttribute("href", /^https?:\/\/[^\s/]+/.test(url) ? url : FIRST.url);
});

// The picture shown, chosen above it or by the line of the left that it numbers; the Cloud Token field shows the first, where it is made
function shoot(at) {
  for (const choice of $$("[data-shot]", useForm)) {
    const on = choice.dataset.shot === String(at);
    if (choice.matches("button")) choice.setAttribute("aria-checked", String(on));
    else if (choice.matches("img")) choice.hidden = !on;
    else choice.classList.toggle("shown", on);
  }
}
useForm.addEventListener("click", (e) => {
  const choice = e.target.closest?.("button[data-shot], li[data-shot]");
  if (choice) shoot(choice.dataset.shot);
});
$("#use-token").addEventListener("focus", () => shoot(0));
for (const [box] of STEPS) {
  $(`#${box}`).addEventListener("input", () => {
    sync();
    if (box === "use-deploys" && $(`#${box}`).checked) shoot(0);
  });
}
// On a phone the pictures are folded under the fields
for (const toggle of $$(".connect-toggle", wizard.forms[0].closest("dialog"))) {
  toggle.addEventListener("click", () => toggle.setAttribute("aria-expanded", String(toggle.getAttribute("aria-expanded") !== "true")));
}
// The pictures folded again as a dialog opens (a phone shows them once asked)
const fold = (form) => $(".connect-toggle", form).setAttribute("aria-expanded", "false");
// Another environment is another gateway name, and the one the Cloud Token is for
$("#use-environment").addEventListener("change", () => {
  const env = using?.list?.find((e) => e.id === $("#use-environment").value);
  if (!env) return;
  $("#use-gateway-name").value = gatewayName(env);
  $("#use-env-name").textContent = env.name;
});

// The second step for `environment`, or for one of `list` to choose (Development first), in the wizard. `runs` is false when nothing
// here can run a gateway (no add-on for it): its part is not offered. `replaces` is the name of the environment whose gateway runs here
// now, when one does. `done({gatewayId})` runs once what was chosen is done, with the gateway made, if one was.
function setUp({ platform, environment = null, list = null, runs = true, replaces = "", done }) {
  const first = environment || list.find((env) => /^dev/i.test(env.name)) || list[0];
  using = { platform, environment: first, list, done, finished: new Set(), reported: false };
  for (const [box] of STEPS) {
    $(`#${box}`).checked = true;
    $(`#${box}`).disabled = false;
    $(`#${box}-done`).hidden = true;
  }
  $("#use-choice").hidden = !list;
  pick(true);
  $("#use-environment").innerHTML = options(list || [first], first.id);   // required, so never empty
  $("#use-environment").value = first.id;
  $("#use-env-name").textContent = first.name;
  $("#use-open").setAttribute("href", cloudTokensUrl(platform));
  $("[data-cancel]", useForm).textContent = list ? "Skip for now" : "Cancel";
  $("#use-gateway-step").hidden = !runs;
  $("#use-gateway").checked = runs;
  $("#use-gateway-name").value = gatewayName(first);
  $("#use-replaces").hidden = !replaces;
  $("#use-replaces").innerHTML = takes(replaces);
  $("#use-token").value = "";
  sync();
  shoot(0);
  fold(useForm);
  if (list) wizard.next(1, `${platform.name} connected`);
  else wizard.open(`Set up ${first.name}`, { at: 1 });
}

// Connect Gravitee Cloud: the account, then a first environment. `added(platform)` runs once it is registered, `runs(platform)` says
// whether a gateway can run here, and `done(platform, check, made)` once the environment is set up, with the check read and the gateway
// made ({gatewayId}).
export function openConnect({ added, runs, done }) {
  connecting = { added, runs, done };
  using = null;
  wizard.open("Connect Gravitee Cloud", { steps: true, fill: () => {
    $("#platform-url").value = FIRST.url;
    $("#platform-open").setAttribute("href", FIRST.url);
    $("#platform-more").open = false;
    $("#platform-token").value = "";
    $("#platform-account").value = "";
    $("#platform-account").removeAttribute("aria-invalid");
    $("#platform-account-wrong").hidden = true;
    $("#platform-account-known").hidden = true;
    registered = [];
    api("/api/platforms").then((answer) => { registered = answer.platforms || []; }, () => {});
    fold(connectForm);
  } });
}

// Set up one environment that is not used yet: the second step alone, for it. `runs`, `replaces` and `done` as in the wizard.
export function openUseEnvironment({ platform, environment, runs = true, replaces = "", done }) {
  setUp({ platform, environment, runs, replaces, done });
}
