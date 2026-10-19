// Images: the version of every component and, for the Gravitee ones, the registry it comes from.
// Changes are a draft until they are saved.

import { $, esc, icon, setHTML, loading, ago, compareVersions, api, store, drafts, recall, remember, VARIANTS, ofPlatform } from "../core.js";
import { toast, emptyHtml, applyBadge, dockerDown, paintProgress, paintBusy, refocus, actionButton,
  paintActions, statusHtml, noteBadge, progressBar, setOff, behindTip, behindWhy } from "../components/ui.js";
import { runJob, runPlatform, busy, confirmApply, savedOnly } from "../activity/jobs.js";
import { Combo } from "../components/combo.js";

// What can be typed as a registry: a host or a namespace, then a path, as the server checks it when it saves
const REGISTRY = /^[a-z0-9]+([._-][a-z0-9]+)*(:\d+)?(\/[a-z0-9]+([._-][a-z0-9]+)*)*$/;
const TAG = /^[A-Za-z0-9._-]+$/;   // what can be typed as a tag
const PER_GROUP = 80;   // the tags of a group listed until a search finds the others

let images = null;      // the components, and the registries in use
let registry = null;    // the registry that asks for a login, as /api/registry says it
let draft = {};         // the variables changed and not saved yet
const tags = {};        // the tags of a component on a registry, once listed
// The sections that fold: the platform and its add-ons open, the third-party components folded. The browser keeps the choice.
const KEPT = { platform: "platformOpen", addons: "addonsOpen", third: "thirdPartyOpen" };
const open = { platform: recall(KEPT.platform) !== "0", addons: recall(KEPT.addons) !== "0", third: recall(KEPT.third) === "1" };
let onlyRunning = false;   // the Running filter
let stale = true;       // to be read again: the page opens, or a job ended
let ended = null;       // the last job seen ended
let target = null;      // the component an address names (#/images/APIM_VERSION), shown once the components are read
let reading = false;    // the components are being read: what is drawn meanwhile is what was read before

// The value a variable has in its .env (its default when it has none), the value of the draft, and what a value
// gives once the ${VARIABLE} references it holds are followed
const saved = (key) => images.components
  .flatMap((c) => [[c.registry_key, c.registry], [c.version_key, c.version]]).find(([k]) => k === key)?.[1];
const valueOf = (key) => (key in draft ? draft[key] : saved(key));
function resolve(value, depth = 0) {
  if (!value || depth > 5) return value || "";
  return value.replace(/\$\{(\w+)\}/g, (_, key) => resolve(valueOf(key), depth + 1));
}
const verOf = (c) => resolve(valueOf(c.version_key));
const regOf = (c) => (c.kind === "gravitee" ? resolve(valueOf(c.registry_key)) : null);
// A component follows another one when its version is the reference to that one's; its registry follows too when
// it is the reference to that one's registry
const followOf = (c) => c.follows.find((f) => f.version === valueOf(c.version_key));
const sharesRegistry = (c, follow) => !!follow?.registry && valueOf(c.registry_key) === follow.registry;
const componentOf = (el) => images.components.find((c) => c.id === el.closest("tr").dataset.component);
// A component of several services goes by what owns them, with the services under it
const titleOf = (c) => (c.services.length > 1 ? c.detail : c.label);
const subOf = (c) => (c.services.length > 1 ? c.label : c.detail !== c.label ? c.detail : "");
const followedTitle = (follow) => titleOf(images.components.find((c) => c.id === follow.id));
// What a field says after its value when that value follows another component's: whose it is, in muted words that give way first,
// its tooltip the value and whose it is in full
const sameAs = (follow) => `<span class="follow-of">Same as ${esc(followedTitle(follow))}</span>`;
const fieldTip = (value, follow) => esc(follow ? `${value}, same as ${followedTitle(follow)}` : value);
// A registry as the page names it: the server's name for the ones in use, the value itself for one typed here
const registryName = (registry) => images.registries.find((r) => r.value === registry)?.label || registry;
// An image as Docker lists the ones it holds, without its tag
const nameOf = (image) => image.replace(/^docker\.io\/(library\/)?/, "").replace(/:[^:/]+$/, "");
// What owns a component, as the state holds it and names its progress: a group of the platform, an add-on or a
// playground
const ownerKey = (c) => `${c.owner.kind === "platform" ? "group" : c.owner.kind}:${c.owner.id}`;
const ownerOf = (c) => (c.owner.kind === "platform" ? store.state.platform.groups.find((g) => g.id === c.owner.id)
  : store.item(c.owner.kind, c.owner.id));

function set(key, value) {
  if (value === saved(key)) delete draft[key]; else draft[key] = value;
}

// The section a component is listed in: the Gravitee ones of a group of the platform (the add-on's gateway too, it is a
// gateway of its group), what the platform's own add-ons run, and the rest, which is not Gravitee's (the databases, Kafka, a playground's)
const sectionOf = (c) => (c.kind === "gravitee" && c.group ? "platform" : ofPlatform(store.item("addon", c.owner.id)) && c.owner.kind === "addon" ? "addons" : "third");

const dirty = () => Object.keys(draft).length > 0;

// The image a component runs changes: its version, or the registry of a Gravitee one, once references are followed.
// A link to another component that gives the same image changes what .env says, not what runs.
const regChanged = (c) => c.kind === "gravitee" && regOf(c) !== c.registry_resolved;
const imageChanged = (c) => verOf(c) !== c.version_resolved || regChanged(c);

function isChanged(c) {
  const raw = valueOf(c.version_key) !== c.version || (c.kind === "gravitee" && valueOf(c.registry_key) !== c.registry);
  return raw || imageChanged(c);
}

// The components that run, also a container that keeps restarting, and whose image changes: what applying the draft
// recreates
const runs = (c) => c.running.length > 0 || c.services.some((name) => store.service(name)?.state.running);
const restarted = () => images.components.filter((c) => imageChanged(c) && runs(c));

// What runs behind what is saved: another image than the one saved, or containers the server says an apply makes
// again (a newer image under the same tag, another setting).
const behind = (c) => !c.running.every((i) => c.configured.includes(i)) || c.services.some((name) => store.service(name)?.behind);
// Why, in the server's words: the reasons of the services of the component that run behind, else that the image run is not
// the one saved
const reasonOf = (c) => [...new Set(c.services.map((name) => store.service(name))
  .filter((service) => service?.behind && service.note_apply).map((service) => service.note_title))].join(" ")
  || "The saved image differs from the one that runs.";
// ... and as the causes its popover draws, each once across the services of the component
function causesOf(c) {
  const found = new Map();
  for (const service of c.services.map((name) => store.service(name))) {
    if (service?.behind) for (const cause of service.note_causes || []) found.set(JSON.stringify(cause), cause);
  }
  return [...found.values()];
}
const tagsKey = (c) => `${c.id}:${regOf(c) ?? ""}`;

// The tags of a component on its registry. A registry that does not answer is asked again the next time.
async function tagsOf(c) {
  const key = tagsKey(c);
  if (!tags[key]) {
    const registry = c.kind === "gravitee" ? `&registry=${encodeURIComponent(regOf(c))}` : "";
    try {
      tags[key] = await api(`/api/tags?component=${encodeURIComponent(c.id)}${registry}`);
    } catch (err) {
      return { error: err.message };
    }
    draw();   // the row says when its tag is not among them
  }
  return tags[key];
}

// A tag's variant (the system its image is built on, "default" for none), the tag without it, and what kind of tag
// it is: one that moves (latest, 4.9), a release (4.9.2), or a build
const variantOf = (tag) => VARIANTS.find((v) => tag.endsWith(`-${v}`)) || "default";
const baseOf = (tag) => { const v = variantOf(tag); return v === "default" ? tag : tag.slice(0, -(v.length + 1)); };
function kindOf(tag) {
  const base = baseOf(tag);
  if (/latest/.test(base) || /^\d+(\.\d+)?$/.test(base)) return "moving";
  if (/^v?\d+\.\d+\.\d+/.test(base)) return "release";
  return "build";
}
const newest = (a, b) => (b.updated || "").localeCompare(a.updated || "");

// The tags a component can be given, from the list of its registry: the tags of the repository of its first image. The
// others take the version too, and a variant tag of the first (-debian) may not exist for them, as a component made of
// it by adding to it does not ("${APIM_VERSION}-debian": the gateway's): such a component is chosen among the plain tags.
// Images for Windows are never offered.
function choices(c, components, listed) {
  const shared = new Set(c.configured.map(nameOf)).size > 1
    || components.some((o) => o.follows.some((f) => f.id === c.id && f.version !== `\${${c.id}}`));
  return listed.filter((t) => !/nanoserver|windowsservercore/.test(t.name) && (!shared || variantOf(t.name) === "default"));
}

// A tag chosen here that its registry does not list: a registry lists its latest tags only, so it is a warning.
// The followed component is checked on its own row.
function tagMissing(c) {
  if (followOf(c) || !isChanged(c)) return false;
  const listed = tags[tagsKey(c)];
  return !!listed && !listed.tags.some((t) => t.name === verOf(c));
}

const pill = (tone, text) => `<span class="pill ${tone}">${text}</span>`;
const note = (text, tone = "") => `<span class="list-note ${tone}" data-tip="${esc(text)}" data-tip-cut>${esc(text)}</span>`;

// The State cell, as on a list: the state component (a dot and its word) and what it adds on its line. A change of the draft says
// what it will do, as a pill; while a job starts or stops what owns the component, its state says how far it is under its word; what
// is off or stopped is a muted word; what runs behind what is saved says "Apply needed", and the tag it runs when that is not the one
// saved. What runs without its owner being on has the badge its owner's row has (`outside`). While Docker does not answer, nothing is
// said of what runs or is pulled.
function status(c) {
  const changed = isChanged(c), key = ownerKey(c);
  let html;
  if (changed && !imageChanged(c)) html = pill("warn", followOf(c) ? "Will link" : "Will unlink");
  else if (changed && tagMissing(c)) html = pill("warn", "Will change") + note("Tag not listed", "warn");
  else if (changed) html = pill("warn", "Will change") + note(`from ${c.version_resolved}${regChanged(c) ? ` (${c.registry_resolved})` : ""}`);
  else if (store.progress(key) !== null) {
    const { key: dot, label } = ownerOf(c).state;
    html = statusHtml(dot, label, { extra: progressBar(key) });
  } else if (!c.enabled && !c.running.length) html = statusHtml("off", "Off", { quiet: true, tip: [`${ownerOf(c).name} is off`] });
  else if (dockerDown()) html = "";
  else {
    // What runs behind, also a container that keeps restarting on the image it runs: the badge says which tag runs, so that
    // what follows it keeps only what it does not say
    html = (behind(c) ? applyBadge(reasonOf(c), "pill warn", causesOf(c)) : !c.running.length ? statusHtml("stopped", "Stopped", { quiet: true })
      : statusHtml("running", "Running")) + (c.outside ? noteBadge(c.outside) : "");
  }
  return `<div class="list-state">${html}</div>`;
}

// A Gravitee component chooses its registry in a field, as its version: one that follows another component's says whose it is
// after it (a registry chosen there is its own from then on, as a tag of its own is for a version)
function registryCell(c) {
  if (c.kind !== "gravitee") return `<span class="field-text source-label">${esc(c.source)}</span>`;
  const follow = followOf(c), registry = regOf(c), shared = sharesRegistry(c, follow) ? follow : null;
  return `<button class="combo-btn ${valueOf(c.registry_key) !== c.registry ? "changed" : ""}" data-registry data-tip="${fieldTip(registry, shared)}">
    <span class="source-label">${esc(registryName(registry))}</span>${shared ? sameAs(shared) : ""}${icon("chevron")}</button>`;
}

// Four cells, in the order of the header: the name as on a list, the state before the field that changes it. The
// registry is under the version too, shown there when its column folds on a narrow page (pages.css).
function row(c) {
  const follow = followOf(c), sub = subOf(c);
  // Whether the image is on this machine is said with the version, as the list of versions says it of each: nothing while Docker does not say
  const version = `<span class="value">${esc(verOf(c))}</span>${c.pulled === false ? pill("", "Not pulled") : ""}`;
  return `
    <tr class="${isChanged(c) ? "pending" : ""}" data-component="${esc(c.id)}">
      <td><div class="list-name" data-tip="${esc(titleOf(c))}" data-tip-cut>${esc(titleOf(c))}</div>
        ${sub ? `<div class="list-sub"><span data-tip="${esc(sub)}" data-tip-cut>${esc(sub)}</span></div>` : ""}</td>
      <td>${registryCell(c)}</td>
      <td>${status(c)}</td>
      <td><button class="combo-btn ${valueOf(c.version_key) !== c.version ? "changed" : ""}" data-version data-tip="${fieldTip(verOf(c), follow)}">${version}${follow ? sameAs(follow) : ""}${icon("chevron")}</button>
        <div class="registry-under">${registryCell(c)}</div></td>
    </tr>`;
}

// A section folds: its name is a button. Folded, it sums up what is on and says when it hides a change. A search lists
// every section open, its name then no button.
function band(id, label, hidden, { sub = false, plain = false } = {}) {
  const cls = `stack-sep${sub ? " sub" : ""}`;
  if (plain) return `<tr class="section"><td colspan="4"><div class="${cls}">${esc(label)}</div></td></tr>`;
  const on = hidden.filter((c) => c.enabled).map((c) => `<span>${esc(titleOf(c))} <code>${esc(verOf(c))}</code></span>`).join("");
  const changed = hidden.some(isChanged) ? pill("warn", "Will change") : "";
  return `<tr class="section"><td colspan="4"><button class="${cls}" data-fold="${id}" aria-expanded="${open[id]}">
    <span class="s-toggle">${icon("chevron")}</span>${esc(label)}${open[id] ? "" : `<span class="fold-summary">${on}${changed}</span>`}</button></td></tr>`;
}

// The platform's components, then under a band of their own those of its add-ons, then what is not Gravitee's
function sections(shown, q) {
  const of = (id) => shown.filter((c) => sectionOf(c) === id);
  const platform = of("platform"), addons = of("addons"), third = of("third");
  const name = store.state.platform.name;
  let html = "";
  if (platform.length || addons.length) {
    html += band("platform", name, [...platform, ...addons], { plain: !!q });
    if (q || open.platform) {
      html += platform.map(row).join("");
      if (addons.length) html += band("addons", "Add-ons", addons, { sub: true, plain: !!q }) + (q || open.addons ? addons.map(row).join("") : "");
    }
  }
  if (third.length) html += band("third", "Third-party", third, { plain: !!q }) + (q || open.third ? third.map(row).join("") : "");
  return html;
}

function draw() {
  if (!images) return;
  const found = target && !reading && images.components.find((c) => c.id === target);
  if (found) uncover(found);
  const q = $("#images-search").value.trim().toLowerCase();
  $("#images-running").checked = onlyRunning;
  const shown = images.components.filter((c) => (!onlyRunning || c.running.length > 0)
    && [c.label, c.detail, c.id, c.repository, c.source].join(" ").toLowerCase().includes(q));
  setHTML($("#images-rows"), sections(shown, q));
  paintProgress($("#images-rows"));
  const empty = $("#images-empty");
  empty.hidden = shown.length > 0;
  setHTML(empty, emptyHtml({ filtered: true, running: onlyRunning && !images.components.some((c) => c.running.length) }));
  paintRegistry();   // a registry chosen in the draft may be one to log in to
  renderActions();
  if (found) reveal(found);
  if (!reading) target = null;
}

// The component an address names shows, whatever the filters hid, its section unfolded
function uncover(c) {
  $("#images-search").value = "";
  onlyRunning = false;
  const id = sectionOf(c);
  open[id] = true;
  if (id === "addons") open.platform = true;   // the add-ons are under the platform's band
}

// ...in view, its version picker open, once the router has set the page's scroll
function reveal(c) {
  requestAnimationFrame(() => {
    const button = $(`#images-rows tr[data-component="${CSS.escape(c.id)}"] [data-version]`);
    if (!button) return;
    button.scrollIntoView({ block: "center" });
    button.focus({ preventScroll: true });
    pickTag(button, c);
  });
}

// The draft's buttons, in the page head as in the editor, and the pill that says there is one. Save only writes
// .env: it waits for nothing. Applying recreates containers: there is nothing to apply when none of them changes.
// The second button is "Save & apply" while there is a draft, else Apply, the platform's, usable while something runs behind
// what is saved: one word for each, whatever is read yet. Then, as on Plugins and the Dashboard, the badge Apply needed beside the
// title says why. Save and "Save & apply" show their action at work: each is written again only when what it says changes, and the
// new one takes at once the look of its action (paintActions). Save says so on its own key; "Save & apply" and Apply
// on the platform's, where they apply.
function renderActions() {
  const changed = dirty(), wait = store.wait(), idle = changed && !restarted().length;
  const why = wait || (!changed ? "No changes" : idle ? "No running container changes" : "");
  const title = "Save, then recreate the containers that changed";
  $("#images-unsaved").hidden = !changed;
  setOff($("#images-discard"), !changed, "No changes");
  setHTML($("#images-save-slot"), actionButton({ key: "images", verb: "save", label: "Save",
    attrs: `id="images-save" ${changed ? 'data-tip="Write .env only"' : 'aria-disabled="true" data-why="No changes"'}` }));
  setHTML($("#images-apply-slot"), actionButton({ key: "platform", verb: ["apply", "save"],
    label: changed ? "Save & apply" : "Apply", icon: "apply", cls: "primary",
    attrs: `id="images-apply" ${why ? `aria-disabled="true" data-why="${esc(why)}"` : `data-tip="${title}"`}` }));
  paintActions($("#images-actions"));
}

function discard() {
  draft = {};
  draw();
}

// What the draft changes, as the first row of what applying it does: the versions, and the registries
const changes = () => ({ label: "Changes", items: images.components.filter(isChanged).map((c) => `${titleOf(c)} ${verOf(c)}`
  + (regChanged(c) ? ` (${registryName(regOf(c))})` : "")) });

// The version of a component: the components whose version it can take instead ("Same as", as "follow:<index>"),
// then its tags that move, its releases and its builds, those Docker holds marked "Pulled", in one column. Another tag can be
// typed.
function pickTag(button, c) {
  const follow = followOf(c), image = c.kind === "gravitee" ? `${regOf(c)}/${c.repository}` : nameOf(c.configured[0]);
  // A tag built on a given system opens on the tags built on the same one
  const built = variantOf(verOf(c));
  let listed = [], pulled = new Set(), variants = new Set(), variant = built === "default" ? "all" : built;
  // The tags of a group, the first ones only until a search
  const group = (label, tags, sort) => ({ label, more: tags.length > PER_GROUP,
    items: tags.sort(sort).slice(0, PER_GROUP).map((t) => ({ value: t.name, name: t.name, meta: ago(t.updated),
      badges: pulled.has(`${image}:${t.name}`) ? ["Pulled"] : [] })) });
  Combo.open(button, {
    current: follow ? `follow:${c.follows.indexOf(follow)}` : verOf(c),
    typed: TAG,
    async load() {
      const answer = await tagsOf(c);
      if (answer.error) throw new Error(answer.error);
      listed = choices(c, images.components, answer.tags);
      pulled = new Set(answer.local);
      variants = new Set(listed.map((t) => variantOf(t.name)));
      if (!variants.has(variant)) variant = "all";
    },
    // The variants to choose from, when the tags come in several
    segments: () => (variants.size < 2 ? null : {
      label: "Variant", value: variant, set: (v) => (variant = v),
      options: ["all", ...VARIANTS.filter((v) => variants.has(v)), ...(variants.has("default") ? ["default"] : [])]
        .map((v) => ({ value: v, label: v === "all" ? "All" : v === "default" ? "Plain" : v })),
    }),
    groups(q) {
      const tags = listed.filter((t) => (variant === "all" || variantOf(t.name) === variant) && t.name.toLowerCase().includes(q));
      const kind = (k) => tags.filter((t) => kindOf(t.name) === k);
      const follows = c.follows.map((f, i) => ({ value: `follow:${i}`, name: followedTitle(f), meta: resolve(f.version),
        icon: "link" }));
      return [
        { label: "Same as", items: follows.filter((it) => it.name.toLowerCase().includes(q)) },
        group("Moving tags", kind("moving"), newest),
        group("Releases", kind("release"), (a, b) => compareVersions(baseOf(a.name), baseOf(b.name))),
        group("Builds", kind("build"), newest),
      ];
    },
    choose(value) {
      const followed = c.follows[value.match(/^follow:(\d+)$/)?.[1]];
      if (followed) {
        if (followed.registry) set(c.registry_key, followed.registry);
        set(c.version_key, followed.version);
      } else {
        // A tag of its own ends the link to another component, registry included
        if (c.kind === "gravitee" && /^\$\{/.test(valueOf(c.registry_key) || "")) set(c.registry_key, regOf(c));
        set(c.version_key, value);
      }
      draw();
      refocusField(c, "version");
    },
  });
}

// The registries in use, the ones of the draft included; another one can be typed
function pickRegistry(button, c) {
  const offered = [...new Set([...images.registries.map((r) => r.value), ...images.components.map(regOf)])].filter(Boolean);
  Combo.open(button, {
    current: regOf(c),
    typed: REGISTRY,
    groups: (q) => [{ label: "In use", items: offered.filter((r) => `${r} ${registryName(r)}`.toLowerCase().includes(q))
      .map((r) => ({ value: r, name: r, meta: registryName(r) === r ? "" : registryName(r) })) }],
    async choose(registry) {
      set(c.registry_key, registry);
      draw();
      refocusField(c, "registry");
      // The row warns when the tag in use is missing from that registry, once its tags are read
      await tagsOf(c);
      refocusField(c, "registry");
    },
  });
}

// A row is drawn again whole when it changes: the keyboard goes back to the field it chose a value in, the one in
// view when the registry shows under the version (its own column folded to nothing)
function refocusField(c, field) {
  const cell = field === "registry" && $(".table.images th:nth-child(2)").offsetWidth ? 2 : 4;
  refocus(`#images-rows tr[data-component="${CSS.escape(c.id)}"] > td:nth-child(${cell}) [data-${field}]`);
}

// A component that is on takes its images from Azure, the draft's registry included: a login is needed to pull them.
// The server names the registry for any Azure one in the files, a default or what is off too. What is saved is the
// server's to say (`azure_in_use`) when it does; a draft is only known here.
const AZURE = /\.azurecr\.io$/;
const azureInUse = () => (!dirty() && typeof images.azure_in_use === "boolean" ? images.azure_in_use
  : images.components.some((c) => c.enabled && AZURE.test((regOf(c) || "").split("/")[0])));

// The registry that asks for a login, with its state and its button; nothing while no component that is on uses it
function paintRegistry() {
  const el = $("#images-registry"), r = registry;
  el.hidden = !r?.host || !images || !azureInUse();
  if (el.hidden) return;
  const text = r.registry_login ? r.account || "logged in" : "log in needed";
  setHTML(el, `<span class="state ${r.registry_login ? "ok" : "warn"}">${esc(r.label)}: ${esc(text)}</span>
    ${actionButton({ key: `registry:${r.host}`, verb: "login", label: r.registry_login ? "Log in again" : "Log in",
      icon: "login", attrs: `data-busy data-login="${esc(r.host)}" data-tip="Log in to ${esc(r.label)}"` })}`);
  // The button waits for Docker and for the running job like the others, and shows its login at work
  paintBusy(el);
  paintActions(el);
}

async function loadRegistry() {
  try {
    registry = await api("/api/registry");
  } catch {
    return;   // not known this time: what is shown stays
  }
  paintRegistry();
}

const login = (host) => runJob(() => api("/api/registry/login", { host }), { key: `registry:${host}`, verb: "login" });

async function load() {
  loadRegistry();
  reading = true;
  try {
    images = await api("/api/images");
  } catch (err) {
    reading = false;
    if (images) toast(err.message, true);
    else setHTML($("#images-empty"), `<span class="err-text">${esc(err.message)}</span>`);
    return;
  }
  reading = false;
  draw();
}

// Save, or Save & apply (`apply`) of the draft as it is when clicked. What applying recreates is said first, as the
// server counts it with the draft: when it is nothing, Save & apply only saves, and says why. With nothing unsaved,
// Apply is the platform's, which says first what it restarts.
async function save(apply) {
  if (apply && !dirty()) return runPlatform("apply");
  const sent = { ...draft };
  let applying = false, said = "Saved";
  if (apply) {
    const found = await confirmApply("Save & apply", { images: sent }, [changes()]);
    if (!found) return;
    applying = found.apply;
    if (!applying) said = savedOnly(found);
  }
  try {
    // The button clicked says "Saving" meanwhile: "Save & apply" says it on the platform's key, which it applies next
    images = await busy(apply ? "platform" : "images", "save", api("/api/images", { values: sent }));
  } catch (err) {
    toast(err.message, true);
    return;
  }
  // What was chosen while it saved stays in the draft
  for (const [key, value] of Object.entries(sent)) if (draft[key] === value) delete draft[key];
  draw();
  if (applying) {
    runJob(() => api("/api/stack/apply", {}), { key: "platform", verb: "apply" });
  } else {
    toast(said);
    store.refresh();   // the other pages show these versions from the state
  }
}

function init(section) {
  section.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h1>Images</h1><span class="pill warn" id="images-unsaved" hidden>Unsaved</span></div>
      <div class="page-actions" id="images-actions">
        ${actionButton({ key: "platform", verb: "pull", label: "Pull images", icon: "download", attrs: 'id="images-pull" data-busy' })}
        <button class="btn" id="images-discard" aria-disabled="true" data-why="No changes">Discard</button>
        <span class="act-slot" id="images-save-slot"></span>
        <span class="act-slot" id="images-apply-slot"></span>
      </div>
    </div>

    <div class="card list-card">
      <div class="list-tools">
        <label class="search grow">${icon("search")}<input id="images-search" type="search" placeholder="Search…" aria-label="Search the images" autocomplete="off"></label>
        <div class="list-filters">
          <label class="toggle-chip"><input type="checkbox" id="images-running">Running</label>
        </div>
        <div class="registry-login" id="images-registry" hidden></div>
      </div>
      <div class="table-wrap">
        <table class="table images">
          <thead><tr><th>Component</th><th>Registry</th><th>State</th><th>Version<span class="registry-under"> and registry</span></th></tr></thead>
          <tbody id="images-rows"></tbody>
        </table>
        <div class="empty" id="images-empty">${loading("Loading")}</div>
      </div>
    </div>`;

  renderActions();   // the draft's buttons are in the head before the components are read
  drafts.add(dirty, discard);   // leaving the page or the window with a draft asks first
  $("#images-search").addEventListener("input", draw);
  // Running is an option of its own: a toggle chip, as on the lists
  $("#images-running").addEventListener("change", (e) => {
    onlyRunning = e.target.checked;
    draw();
  });
  section.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if ("clear" in b.dataset) {
      $("#images-search").value = "";
      onlyRunning = false;
      draw();
    } else if ("version" in b.dataset) pickTag(b, componentOf(b));
    else if ("registry" in b.dataset) pickRegistry(b, componentOf(b));
    else if (b.dataset.login) login(b.dataset.login);
    else if (b.id === "images-pull") runJob(() => api("/api/stack/pull", {}), { key: "platform", verb: "pull" });
    else if (b.id === "images-discard") discard();
    else if (b.id === "images-save" || b.id === "images-apply") save(b.id === "images-apply");
    else if ("fold" in b.dataset) {
      const id = b.dataset.fold;
      open[id] = !open[id];
      remember(KEPT[id], open[id] ? "1" : "0");
      draw();
      $(`[data-fold="${id}"]`, section).focus();   // the row was drawn again
    }
  });
}

export const imagesPage = {
  id: "images",
  pattern: /^#\/images(?:\/(\w+))?$/,   // #/images/<version variable> opens at that component
  nav: "images",
  title: "Images",
  init,
  show([id]) {
    stale = true;
    target = id || null;
  },
  render(state) {
    // A job may have pulled images, recreated containers or logged in to a registry
    if (state.job?.done && state.job.id !== ended) { ended = state.job.id; stale = true; }
    if (stale) { stale = false; load(); }
    draw();   // the pill of what a job starts or stops follows it
  },
};
