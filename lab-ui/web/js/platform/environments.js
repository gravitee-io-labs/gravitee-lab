// What the Lab says of an environment of a platform, as plain words: whether a gateway of it runs on this machine, whether the Lab
// may deploy playgrounds into it, what it deployed there, and the one action to offer next. Rules only, read from the platform's
// last check and from the state, so that the Gravitee Cloud page and the Dashboard say the same thing. A hybrid gateway is a local
// gateway that gets its APIs from the environment; a Cloud Token is what deploys into it.

import { store } from "../core.js";
import { dockerDown, statusHtml, noteCard, contentLead } from "../components/ui.js";

export const hybrid = (gateway) => gateway.type === "HYBRID";

// The add-on that runs the hybrid gateways of a platform here. The server chooses it (`state.platform.gateways`, an add-on id by kind of
// platform, null when none can: one that is blocked, needs a catalog or is not compatible is not a candidate), so that the page never
// scans. A state that does not say, as before the catalogs, has it looked for: the add-on that declares a gateway for its kind.
export function addonOf(platform) {
  const chosen = store.state?.platform?.gateways;
  if (chosen && typeof chosen === "object") return store.item("addon", chosen[platform.kind]);
  return store.state?.addons?.find((addon) => addon.gateway?.platform === platform.kind);
}

// Why no add-on can be named for a kind of platform, when the server says that none runs it: null while one does, and for a state that does
// not say. {ready, why, extra}: with the content of the catalogs there, `why` is "Needs a gateway add-on"; with it on its way or missing it
// is the reason the lists give (downloading, the failure line, no catalog) and `extra` the button that mends it, which is never put as a
// lack of add-on for a catalog that simply is not there.
const NEEDS_ADDON = "Needs a gateway add-on";
export function gatewayGap(kind) {
  const chosen = store.state?.platform?.gateways;
  if (!chosen || typeof chosen !== "object" || chosen[kind]) return null;
  const { ready, why, extra } = contentLead(store.state);
  return ready ? { ready, why: NEEDS_ADDON, extra: "" } : { ready, why: why || NEEDS_ADDON, extra };
}

// Whether the add-on runs, or is on its way to or from it: what a Stop is for. An add-on that runs without being on (started outside the
// Lab UI) runs all the same.
export const runs = (addon) => ["running", "partly", "starting", "stopping"].includes(addon?.state.key);

// The Stop of the add-on: its primary action when that is one, else the one in its menu (an add-on that runs without being on offers
// Turn on first)
export const stopOf = (addon) => (addon?.action?.do === "stop" ? addon.action : addon?.secondary?.do === "stop" ? addon.secondary : null);

// The hybrid gateways of an environment, in the order the Lab prefers them: the one that runs here, the ones whose token the Lab
// keeps, those Cloud says are connected, then the rest. Only the first three are of use here: another one is none of the Lab's.
function gatewaysIn(check, env) {
  const rank = (gateway) => (gateway.here ? 0 : gateway.kept ? 1 : gateway.connection === "CONNECTED" ? 2 : 3);
  return (check?.gateways || []).filter((gateway) => hybrid(gateway) && gateway.environmentId === env.id)
    .sort((a, b) => rank(a) - rank(b));
}

const DAY = 86400000;
const SOON = 14;   // days before a Cloud Token expires that the Lab says so

// When a token expires: from its claim (seconds since 1970) or as a date, in milliseconds
const when = (expires) => new Date(typeof expires === "number" ? expires * 1000 : expires).getTime();
const day = (expires) => new Date(when(expires)).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

// What a person needs to know about the terms Cloud has for what the Lab asks of it, as the lines of a tooltip (a title, then what it
// says): the Gravitee Cloud page and its dialogs use these, so that each is said once
export const TERMS = {
  gateway: ["Hybrid gateway", "A gateway that runs on this machine and gets its APIs from this Cloud environment, so that traffic is served here."],
  deploys: ["Deploys", "Whether the Lab can create the APIs of a playground in this environment. It needs a Cloud Token of the environment, created in Gravitee Cloud: Settings, Cloud Tokens, type Automation."],
  token: ["Cloud Token", "Lets the Lab create APIs in this environment for a playground. Create one in Gravitee Cloud: Settings, Cloud Tokens, type Automation."],
  account: ["Account Token", "Lets the Lab read this account, its environments and its gateways, and create gateways. It deploys nothing: that is the Cloud Token of each environment. In Gravitee Cloud: Settings, Account Tokens."],
  accountId: ["Account ID", "The ID Gravitee Cloud shows next to its Account Tokens: Settings, Account Tokens. An Account Token cannot look up its own account, so the Lab asks for its ID too."],
};

// --- The gateway ----------------------------------------------------------------------

// What the container of the add-on says while it does not run: [dot, word]; one that is off or stopped is a ring, as a stopped gateway is
// everywhere
const CONTAINER = { starting: ["starting", "Starting"], stopping: ["stopping", "Stopping"], stopped: ["stopped", "Stopped here"], off: ["stopped", "Stopped here"] };
// Cloud's own words while a gateway is not deployed
const CLOUD = { PENDING: ["starting", "Pending"], DELETING: ["stopping", "Deleting"], FAILED: ["issue", "Failed"] };

const lines = (gateway, ...more) => [gateway.name, ...more].filter(Boolean);

// Whether the add-on cannot start for want of a license: its own warn or error note leads to the form that takes one. No gateway of the
// platform can start then, whichever it is.
const unlicensed = (addon) => !!addon?.note && ["warn", "err"].includes(addon.note_tone) && !!addon.note_file;

// The address a person calls on this machine: the first domain of a gateway that is not the name of its container, which only the
// other containers of the lab know
const addressOf = (gateway, addon) => gateway.domains.find((domain) => domain.split(":")[0] !== addon?.gateway?.service) || "";

// The state of a gateway on this machine: {key, dot, word, tip} where `dot` is the class of the dot, `word` what it says and `tip` the
// lines of its tooltip (a title, then what it adds). The gateway that runs here is the add-on's: its own warn or error note (a missing
// license is its own state), else its container's state while it does not run; then Cloud's word. A gateway Cloud says is connected and
// does not run here is connected elsewhere. While Docker does not answer, what runs here is not known: only what Cloud says is. `late`:
// it has run here for long without Cloud seeing it.
function gatewayState(gateway, addon, late) {
  const connected = gateway.connection === "CONNECTED";
  if (gateway.here && addon && dockerDown()) {
    return { key: "unknown", dot: "", word: connected ? "Connected" : "Not connected", tip: lines(gateway, "Docker is not reachable") };
  }
  if (gateway.here && addon) {
    const { key } = addon.state;
    // Its own note says what keeps it from running as it should, in its tone: a license it lacks, settings it lacks (its action is
    // then Settings), a failure
    if (addon.note && ["warn", "err"].includes(addon.note_tone)) {
      const own = unlicensed(addon) ? "license" : addon.note_causes?.some((cause) => cause.kind === "unset") ? "settings" : "issue";
      const more = addon.note_title && addon.note_title !== addon.note ? [addon.note_title] : [];
      // The card the add-on's own badge opens, wherever its note is said: the settings it lacks as chips, where they are set
      return { key: own, dot: addon.note_tone === "err" ? "issue" : "warn", word: addon.note, tip: noteCard(addon) || (more.length ? [addon.note, ...more] : lines(gateway)) };
    }
    if (CONTAINER[key]) {
      const [dot, word] = CONTAINER[key];
      return { key: key === "off" ? "stopped" : key, dot, word, tip: lines(gateway) };
    }
    if (connected) return { key: "running", dot: "healthy", word: "Running here", tip: lines(gateway) };
    return late ? { key: "late", dot: "warn", word: "Not connected", tip: lines(gateway, "Gravitee Cloud has not seen it for 2 minutes", "Its logs say why") }
      : { key: "connecting", dot: "starting", word: "Connecting", tip: lines(gateway, "Running here, waiting for Gravitee Cloud to see it") };
  }
  if (CLOUD[gateway.status]) return { key: "issue", dot: CLOUD[gateway.status][0], word: CLOUD[gateway.status][1], tip: [] };
  if (connected) return { key: "elsewhere", dot: "stopped", word: "Connected elsewhere", tip: lines(gateway, "Another machine runs it") };
  return { key: "stopped", dot: "stopped", word: "Stopped here", tip: lines(gateway) };
}

// What the cell says while no add-on can run a gateway here (`gap`, gatewayGap). With the catalogs there, that none has one, and the way
// to a catalog that has; with them on their way or missing, that, in the words of the lists, with the button that mends it: never "No gateway
// add-on" for a catalog that simply is not there. The platform is named by its record.
function gapState(platform, gap) {
  if (gap.ready) {
    return { key: "noaddon", dot: "", word: "No gateway add-on", extra: "",
      tip: ["No gateway add-on", `No catalog has an add-on that runs a gateway for ${platform.name}.`, "Add a catalog that has one."] };
  }
  const waiting = store.state.content?.key === "downloading";
  return { key: waiting ? "downloading" : "nocontent", dot: waiting ? "starting" : "", word: waiting ? "Downloading" : gap.why, tip: [gap.why], extra: gap.extra };
}

// What the add-on still holds when Cloud deleted its gateway: its token, which no longer opens anything
const orphanState = (addon) => ({ key: "orphan", dot: "issue", word: "Removed in Cloud",
  tip: ["Removed in Gravitee Cloud", runs(addon) ? "It still runs here, with a token Cloud no longer accepts" : "Its token is still in the settings of the Hybrid gateway"] });

// --- Deploys --------------------------------------------------------------------------

// The state of the Cloud Token of an environment, as the platform's last check read it
const TOKENS = { unset: ["", "Needs a Cloud Token"], expired: ["issue", "Cloud Token expired"], refused: ["issue", "Cloud Token refused"] };

// Whether a deploy can go through this gateway: one the Lab keeps the token of, which is how a place is ready
const usable = (gateway) => !!gateway && (gateway.here || gateway.kept);

// "Expires today", "Expires tomorrow", "Expires in 5 days": nothing while the token has time
function soon(expires) {
  const days = Math.floor((when(expires) - Date.now()) / DAY);
  return days >= SOON ? "" : days <= 0 ? "Expires today" : days === 1 ? "Expires tomorrow" : `Expires in ${days} days`;
}

// What the Lab may do in an environment: {key, dot, word, note, tip, expires}. A Cloud Token that is set and valid is no use without a
// gateway of the environment on this machine, which a deploy goes through: the word says what is missing and the note that the token is
// set; with a gateway that another machine runs it says it needs one here. `note` is the line under the word, the expiry in amber when it is near.
function deployState(token, gateway) {
  const state = TOKENS[token.state] ? token.state : token.state === "valid" ? "valid" : "unset", expires = token.expires || null;
  const dated = expires ? `${state === "expired" ? "Expired on" : "Expires"} ${day(expires)}` : "";
  const near = state === "valid" && expires ? soon(expires) : "";
  if (state === "valid") {
    return usable(gateway) ? { key: "ready", dot: near ? "warn" : "healthy", word: "Can deploy", note: near, soon: !!near, tip: dated ? [dated] : [], expires }
      : { key: "gateway", dot: "stopped", word: gateway ? "Needs a gateway here" : "Needs a gateway", note: near ? `Cloud Token set, ${near.toLowerCase()}` : "Cloud Token set", soon: !!near,
        tip: ["A deploy goes through a gateway of this environment that runs here", ...(dated ? [dated] : [])], expires };
  }
  const [dot, word] = TOKENS[state];
  return { key: state, dot, word, note: "", soon: false, tip: dated ? [dated] : [], expires };
}

// What the playgrounds deployed in an environment, from the records the Lab keeps for each place: [{id, name, created}] and the line
// that says it ("Acme Hostels API deployed", "2 playgrounds deployed")
function deployedIn(platform, env) {
  const place = `${platform.id}/${env.id}`;
  return (store.state?.playgrounds || []).flatMap((pg) => (pg.deployed || [])
    .filter((record) => record.place === place && record.created.length).map((record) => ({ id: pg.id, name: pg.name, created: record.created })));
}

const said = (deployed) => (deployed.length === 1 ? `${deployed[0].name} deployed` : deployed.length ? `${deployed.length} playgrounds deployed` : "");

// --- The action -----------------------------------------------------------------------

// The one action to offer for an environment, the first that applies, the most serious first, as the row's words say it: a license to
// add, settings the gateway lacks, a Cloud Token that expired or is refused (in red in its Deploys), a gateway to create or replace, a
// gateway to start (the add-on's own Start swaps the one that runs here; with no license it cannot start, so the license comes first),
// then the Cloud Token to add, then the gateway to stop. Nothing to run gateways means no gateway action, and with the catalogs there, Add
// catalog. An environment that is not in use is offered to set up.
function actionOf(used, main, gateway, deploy, addon, gap) {
  const act = (name, label, disabled = false, reason = "") => ({ do: name, label, disabled, reason });
  if (!used) return act("set-up", "Set up");
  // Nothing can run a gateway here, and the catalogs are there: a catalog that has such an add-on is what it takes, after a token that
  // cannot be used. While they are on their way or missing, the cell has the button that mends it.
  if (gap?.ready) return ["expired", "refused"].includes(deploy.key) ? act("replace-token", "Replace Cloud Token") : act("add-catalog", "Add catalog");
  if (addon) {
    const license = () => act("add-license", /expired|not in/i.test(addon.note) ? "Replace license" : "Add license");
    if (gateway.key === "license") return license();
    if (gateway.key === "settings") return act("settings", "Settings");
    if (["expired", "refused"].includes(deploy.key)) return act("replace-token", "Replace Cloud Token");
    if (!usable(main)) return act("create-gateway", main || gateway.key === "orphan" ? "Replace gateway" : "Create gateway");
    if (!(main.here && runs(addon))) return unlicensed(addon) ? license() : act("start-gateway", "Start gateway");
  }
  if (["unset", "expired", "refused"].includes(deploy.key)) {
    return deploy.key === "unset" ? act("add-token", "Add Cloud Token") : act("replace-token", "Replace Cloud Token");
  }
  if (addon && main?.here) {
    const stop = stopOf(addon);
    return act("stop-gateway", "Stop gateway", !stop?.enabled, stop && !stop.enabled ? stop.reason || "" : "");
  }
  return null;
}

// What to say of an environment of a platform, from the platform ({id, kind}), the check of it ({gateways, orphan}), the environment
// ({id, name, token}) and, for the page that polls, `late`: the gateway that runs here has not connected for long.
//   used      the Lab has something here: a gateway of the environment, any (even one it cannot use, so that it can be removed), a
//             gateway it lost in Cloud, a Cloud Token, or a deploy
//   gateway   {key, dot, word, tip, details, version, address, name, id, here, kept, others, count}: key is none, running, connecting, late,
//             starting, stopping, stopped, elsewhere, issue, license, orphan, unknown, or, with no add-on to run it, noaddon, downloading or
//             nocontent (`extra` the button that mends it); `details` the words under the state (its
//             version and, while it runs here, `address`, the one to call; the name of a gateway of another machine; what is left of
//             one Cloud deleted); `others` are the gateways beyond the first one, `count` how many hybrid gateways the environment
//             has; `license` is set when the word of an issue is a license that is missing, which the action then adds
//   deploy    {key, dot, word, note, tip, expires, soon}: key is ready, gateway, unset, expired or refused; `note` the line under the word
//   deployed  [{id, name, created}] what playgrounds deployed there, and `summary` the line that says it
//   action    {do, label, disabled, reason} the one action to offer, do being set-up, add-license, create-gateway, start-gateway,
//             add-token, replace-token, stop-gateway or add-catalog (the Catalogs page); null when there is none
export function environmentStatus(platform, check, env, { late = false } = {}) {
  const addon = addonOf(platform), gap = addon ? null : gatewayGap(platform.kind), found = gatewaysIn(check, env);
  const main = found.find((gateway) => gateway.here || gateway.kept || gateway.connection === "CONNECTED") || null;
  const lost = check?.orphan?.environmentId === env.id && !main ? check.orphan : null;
  const own = main ? gatewayState(main, addon, late) : lost ? orphanState(addon) : { key: "none", dot: "", word: "Not created", tip: [] };
  // With nothing to run a gateway here, what another machine runs is still said; the rest is the lack of an add-on
  const state = gap && own.key !== "elsewhere" ? gapState(platform, gap) : own, bare = state !== own;
  // It answers where it runs: not while it is stopped, or waits for a license
  const address = main?.here && ["running", "connecting", "late"].includes(state.key) ? addressOf(main, addon) : "";
  const details = bare ? [] : !main ? (lost ? [runs(addon) ? "Still runs here" : "Its token is still set"] : []) : state.key === "elsewhere" ? [main.name, main.version] : [main.version, address];
  const gateway = { ...state, details: details.filter(Boolean), version: bare ? "" : main?.version || "", address,
    name: main?.name || "", id: main?.id || lost?.gateway || "", here: !!main?.here || !!lost, kept: !!main?.kept,
    others: found.filter((other) => other !== main), count: found.length };
  const deploy = deployState(env.token || {}, main), deployed = deployedIn(platform, env);
  const used = found.length > 0 || !!lost || deploy.key !== "unset" || deployed.length > 0;
  return { id: env.id, name: env.name, used, gateway, deploy, deployed, summary: said(deployed), action: actionOf(used, main, gateway, deploy, addon, gap) };
}

// The environments of an account split the way the Lab uses them: `used` those it has something in (see `used` of environmentStatus),
// `idle` the others, each as {env, st} with the status of environmentStatus.
export function splitEnvironments(platform, check, options) {
  const all = (check?.environments || []).map((env) => ({ env, st: environmentStatus(platform, check, env, options) }));
  return { used: all.filter(({ st }) => st.used), idle: all.filter(({ st }) => !st.used) };
}

// --- The hybrid gateways of an account ---------------------------------------------------

// The state of one gateway of the account, in the words of an environment's Gateway: the one that runs here and the ones whose token the
// Lab keeps are said as there; one that is no one's here is Cloud's word, Connected elsewhere, or Not connected, gray: nothing runs it.
function listState(gateway, addon, late) {
  if (gateway.here || gateway.kept || CLOUD[gateway.status] || gateway.connection === "CONNECTED") return gatewayState(gateway, addon, late);
  return { key: "idle", dot: "off", word: "Not connected", tip: lines(gateway, "Nothing runs it") };
}

// Every hybrid gateway of an account, the one that runs here first, then the connected ones, then the others, each in the account's order
// of environments: [{id, name, env, envName, st, here, kept, version, domains, address, orphan}]. `st` is the state of a gateway
// ({key, dot, word, tip}); a gateway Cloud deleted while it runs here (`check.orphan`) is a row of its own, Removed in Cloud, with no
// name but its id. The Gravitee Cloud page lists them, the Dashboard counts them.
export function gatewayList(platform, check, { late = false } = {}) {
  const addon = addonOf(platform), environments = check?.environments || [];
  const envOf = (id) => environments.find((env) => env.id === id) || null;
  const rows = (check?.gateways || []).filter(hybrid).map((gateway) => {
    const st = listState(gateway, addon, late), env = envOf(gateway.environmentId);
    const address = gateway.here && ["running", "connecting", "late"].includes(st.key) ? addressOf(gateway, addon) : "";
    return { id: gateway.id, name: gateway.name || gateway.id, env, envName: env?.name || "", st, here: !!gateway.here, kept: !!gateway.kept,
      version: gateway.version || "", domains: gateway.domains || [], address, orphan: false };
  });
  const lost = check?.orphan;
  if (lost && !rows.some((row) => row.id === lost.gateway)) {
    const env = envOf(lost.environmentId);
    rows.push({ id: lost.gateway, name: lost.gateway, env, envName: env?.name || "", st: orphanState(addon), here: true, kept: false,
      version: "", domains: [], address: "", orphan: true });
  }
  const rank = (row) => (row.here && ["running", "connecting", "late"].includes(row.st.key) ? 0 : row.st.key === "running" || row.st.key === "elsewhere" ? 1 : 2);
  const place = (row) => environments.indexOf(row.env);
  return rows.map((row, i) => ({ row, i })).sort((a, b) => rank(a.row) - rank(b.row) || place(a.row) - place(b.row) || a.i - b.i).map(({ row }) => row);
}

// How many gateways a number is: "No gateway", "1 gateway", "2 gateways"
export const gatewayCount = (n) => (n ? `${n} ${n === 1 ? "gateway" : "gateways"}` : "No gateway");

// --- The cells ------------------------------------------------------------------------
//
// The gateway and the deploys of an environment, each one state on one line wherever a row of an environment is drawn (the Gravitee
// Cloud page, the Dashboard): a dot and a word, and what else is known of it in its popover, never on a second line.

// The gateway of an environment on this machine: its state, its popover its name, its version and the address it answers on here. Not
// created and stopped are quiet; while Docker does not answer nothing is known of what runs here: a dot with no color. `extra` follows the
// word on its line: on the Gravitee Cloud page, how many gateways the environment has when it has several, which leads to their list.
export function gatewayCell(g, extra = "") {
  const dot = g.key === "unknown" ? "" : g.dot;
  return statusHtml(dot, g.word, { tip: gatewayTip(g), quiet: !dot || ["stopped", "off"].includes(dot), extra: extra + (g.extra || "") });
}

// What the popover of a gateway's state says: its name as its title, its version and, while it runs here, its address, in monospace. A
// card of its own (what the add-on lacks) has the gateway as a row under it.
function gatewayTip(g) {
  const facts = [["Version", g.version], ["Address", g.address]].filter(([, value]) => value);
  const named = facts.length && g.name ? { subject: g.name, lines: facts.map(([label, value]) => [{ who: label }, { code: value }]) } : null;
  return !Array.isArray(g.tip) ? (named ? { ...g.tip, rows: [...g.tip.rows, named] } : g.tip)
    : named ? { title: g.name, text: g.tip.slice(1).join(". "), rows: [{ lines: named.lines }] } : g.tip;
}

// Whether the Lab may deploy into it, as one state: what the Lab deployed there ("Acme Hostels API deployed"), else that its Cloud Token
// is about to expire, else the state of the token ("Can deploy", "Needs a Cloud Token", "Cloud Token expired"). With no gateway of the
// environment, which its own column says, it is that the token is set. The popover says the rest: the state of the token, its expiry,
// what each playground created.
export function deployCell(st, created = () => []) {
  const { deploy, deployed, gateway } = st, items = deployed.map((d) => [d.name, created(d).join(", ")].filter(Boolean).join(": "));
  const missing = deploy.key === "gateway" && ["none", "orphan"].includes(gateway.key);
  const word = deployed.length ? st.summary : deploy.soon || missing ? deploy.note : deploy.word;
  const rest = [deploy.word !== word && deploy.word, ...deploy.tip, ...items].filter((line, i, all) => line && all.indexOf(line) === i);
  const tip = deploy.key === "unset" && !deployed.length ? TERMS.token : rest.length ? [word, ...rest] : [];
  return statusHtml(deploy.dot, word, { tip, quiet: ["unset", "gateway"].includes(deploy.key) && !deployed.length });
}
