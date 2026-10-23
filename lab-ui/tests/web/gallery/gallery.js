// Every component of the Lab UI, drawn by the page's own code (web/js/components/ui.js) in every tone and state, once on the light tokens and
// once on the dark ones, which are read from base.css itself so that the gallery never drifts from the page.
import { icon, esc } from "../../../web/js/core.js";
import { tipHtml, applyBadge, behindWhy, noteBadge, noticeHtml, statusHtml, actionButton, starButton, emptyHtml, copyButton } from "../../../web/js/components/ui.js";

// The sprite of the page, so that every icon draws
const page = await (await fetch("../../../web/index.html")).text();
document.getElementById("sprite").innerHTML = page.slice(page.indexOf("<svg class=\"sprite\""), page.indexOf("</svg>", page.indexOf("<svg class=\"sprite\"")) + 6);

// The dark tokens, as base.css gives them to :root[data-theme="dark"], set on the dark half
const dark = [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules]).find((rule) => rule.selectorText === ':root[data-theme="dark"]');
const darkHalf = document.querySelector('[data-gallery="dark"]');
for (const name of dark.style) darkHalf.style.setProperty(name, dark.style.getPropertyValue(name));
darkHalf.style.colorScheme = "dark";

const TAG = { kind: "tag", saved: "4.12.20", running: "4.13.0-alpha.4", older: true, services: [] };
const SETTINGS = { kind: "settings", settings: ["GRAVITEE_LICENSE", "INTERNAL_API_PASSWORD"], services: [] };
const BEHIND = [
  { name: "API Management", reason: "API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs.", count: 5, causes: [TAG] },
  { name: "Alert Engine", reason: "Alert Engine: its settings changed since it started.", count: 1, causes: [SETTINGS] },
  { name: "Databases", reason: "Databases: ...", count: 2, causes: [{ kind: "tag", saved: "9.5.2", running: "9.5.3", older: true, services: ["Elasticsearch"] }, { kind: "config", services: ["Redis"] }] },
];
const NEWER = { note: "Needs newer versions", note_tone: "warn", note_target: { kind: "image", id: null }, note_title: "Needs Access Management 4.13 or later",
  note_causes: [{ kind: "needs", name: "Access Management", version: "4.13" }, { kind: "needs", name: "API Management", version: "4.13.0-alpha.3" }] };
const UNSET = { kind: "addon", id: "hybrid", note: "Needs settings", note_tone: "warn", note_title: "Not set: HYBRID_CLOUD_TOKEN",
  note_causes: [{ kind: "unset", settings: ["HYBRID_CLOUD_TOKEN"] }] };
const OUTSIDE = { note: "Started by hand", note_title: "Not from the Lab UI, so the platform does not start it." };
const UNUSED = { title: "57.5 GB unused", text: "What no container uses, hatched in the bar",
  rows: [{ lines: [[{ who: "Images" }, "58.9 GB, 42.1 GB unused"], [{ who: "Containers" }, "2.2 GB, all in use"], [{ who: "Volumes" }, "6.0 GB, 5.5 GB unused"], [{ who: "Build cache" }, "9.8 GB, 9.8 GB unused"]] }],
  foot: "Unused volumes can hold data: removing one loses it." };
const block = (title, html, cls = "") => `<div class="gal-block ${cls}"><h2>${esc(title)}</h2>${html}</div>`;
const row = (html) => `<div class="gal-row">${html}</div>`;
const swatch = (name) => `<span class="gal-swatch"><span style="background: var(${name})"></span><span>${name}</span></span>`;

const html = (theme) => `<h1>${theme === "light" ? "Light" : "Dark"}</h1>
${block("Surfaces, text, status", row(["--bg", "--surface", "--surface-2", "--surface-3", "--border", "--border-strong", "--text", "--text-2", "--muted"].map(swatch).join(""))
  + row(["--ok", "--ok-soft", "--warn", "--warn-soft", "--err", "--err-soft", "--info", "--info-soft", "--accent"].map(swatch).join("")))}
${block("Type", `<div class="gal-type"><span style="font-size: var(--fs-5); line-height: var(--lh-5); font-weight: var(--fw-3)">Page title 20</span>
  <span style="font-size: var(--fs-4); line-height: var(--lh-4); font-weight: var(--fw-3)">Card title, figure 15</span>
  <span style="font-weight: var(--fw-3)">Name 13.5 strong</span><span>Body 13.5</span>
  <span class="muted" style="font-size: var(--fs-2)">Secondary 13</span><span class="list-head" style="border: 0; height: auto">Caption, column head 12</span>
  <code class="chip">4.13.0-alpha.4</code></div>`)}
${block("Pill", row(`<span class="pill">Personal</span><span class="pill ok">Running</span><span class="pill warn">Will change</span>
  <span class="pill err">Failed</span><span class="pill ok">Connected</span><span class="pill warn">License not saved</span><span class="state ok">Platform running</span>
  <span class="state warn busy">Platform starting</span><span class="state err">Platform issue</span><span class="pill icon-pill">${icon("cloud")}</span>`))}
${block("Apply needed: the badge", row(applyBadge(BEHIND[0].reason, "pill warn", [TAG])))}
${block("Note badges", row(noteBadge({ note: "Needs a license", note_tone: "warn", note_title: "No license saved", note_file: ".env" })
  + noteBadge({ note: "via Kafka Desk", note_target: { kind: "addon", id: "kafka-desk" } })
  + noteBadge({ note: "Started by hand", note_title: "Not from the Lab UI, so the platform does not start it." })
  + noteBadge({ note: "Needs a license", note_tone: "warn", note_title: "Uses: Observability", note_file: ".env", note_causes: [{ kind: "license", features: ["Enterprise authorization engine", "Observability"] }] })
  + noteBadge(NEWER) + noteBadge(UNSET)
  + noteBadge({ note: "Kafka UI exited (1)", note_tone: "err" }) + noteBadge({ note: "Partly on" })))}
${block("State", row(["running", "starting", "stopped", "off", "issue", "warn", "done", ""].map((dot) => statusHtml(dot,
  { running: "Running", starting: "Starting", stopped: "Stopped here", off: "Off", issue: "Failed", warn: "Update available", done: "Up to date", "": "Not known" }[dot],
  { quiet: ["stopped", "off", "done", ""].includes(dot), tip: dot === "running" ? ["Running here", "lab-development"] : [] })).join("")))}
${block("Tooltip and popovers, open", row(tipHtml(`Refresh${icon("info")}`, "Read 1 min ago", "", "Refresh")
  + tipHtml(`Gateway${icon("info")}`, "A gateway that runs on this machine and gets its APIs from Gravitee Cloud.", "", "Gateway")
  + tipHtml(`Images${icon("info")}`, ["Images", "58.9 GB, 42.7 GB unused"], "", "Images")
  + tipHtml(`57.5 GB unused${icon("info")}`, UNUSED)
  + tipHtml("Update available", { title: "Update available", columns: ["Running", "Latest pre-release"], rows: [{ lines: [[{ versions: { running: "4.13.0-alpha.4", saved: "4.13.0-alpha.9" } }]] }], foot: "Choose it on the Images page." })
  + applyBadge(BEHIND[0].reason, "pill warn", [TAG]) + applyBadge("x", "pill warn", [SETTINGS])
  + applyBadge(BEHIND.map((one) => one.reason), "pill warn", behindWhy({ behind: BEHIND }))), "gal-open")}
${block("Notices", noticeHtml({ tone: "warn", title: "Apply needed", text: tipHtml(`<span>8 containers differ from what is saved</span>${icon("info")}`, { ...behindWhy({ behind: BEHIND }), title: "" }, "", "8 containers differ from what is saved"), action: `<button class="btn primary">${icon("apply")}Apply</button>` })
  + noticeHtml({ tone: "calm", title: "Gravitee Platform is stopped", action: `<button class="btn primary">${icon("play")}Start</button>` })
  + noticeHtml({ tone: "warn", title: "License expires in 5 days", action: '<button class="btn">Set license</button>' })
  + noticeHtml({ tone: "err", title: "Docker is not reachable", text: "Cannot connect to the Docker daemon" })
  + noticeHtml({ tone: "info", title: "A hybrid gateway runs here" }))}
${block("Buttons and controls", row(`<button class="btn primary">${icon("apply")}Apply</button><button class="btn">${icon("play")}Start</button>
  <button class="btn danger">Remove</button><button class="btn" aria-disabled="true" data-why="Needs settings: HYBRID_CLOUD_TOKEN">${icon("play")}Start</button>
  <button class="btn" data-tip="Start it with the platform from now on">Turn on</button><button class="icon-btn" aria-label="Count storage again" data-tip="Count again, counted 1 min ago">${icon("restart")}</button>
  <span class="segmented" role="radiogroup" aria-label="Shared or personal"><button role="radio" aria-checked="true">All</button><button role="radio" aria-checked="false">Shared</button><button role="radio" aria-checked="false">Personal</button></span>
  <label class="toggle-chip"><input type="checkbox">Running</label><label class="toggle-chip"><input type="checkbox" checked>Starred</label>
  <span class="levels"><label class="toggle-chip lv-error"><input type="checkbox" checked>${icon("eye")}<span>Error</span><b>3</b></label></span>
  <label class="search">${icon("search")}<input type="search" placeholder="Search…"></label><label class="search">${icon("search")}<input type="search" placeholder="Search…" value="auth"></label>
  <button class="combo-btn" style="width: 190px"><span class="value">All categories</span>${icon("chevron")}</button><button class="combo-btn open" style="width: 190px"><span class="value">All kinds</span>${icon("chevron")}</button>
  <button class="combo-btn" style="width: 220px"><span class="value">4.12.20-debian</span><span class="follow-of">Same as API Management</span>${icon("chevron")}</button>
  <input type="checkbox" checked aria-label="Installed"><input type="checkbox" aria-label="Not installed">
  <span class="loading"><span class="spinner"></span>Loading</span>`))}
${block("Table: the platform's card and a Gravitee Cloud card, one grid", `<div class="gal-stack"><div class="card dash-card gal-card"><div class="card-head"><h2>Gravitee Platform</h2><span class="dash-license"><button type="button" class="pill warn"><span>License not saved</span>${icon("arrow-right")}</button></span><span class="dash-tools"><a class="btn">Containers<span class="muted">12</span></a><a class="btn">Change versions</a></span></div>
  <div class="dash-heads"><span>Product</span><span>Version</span><span>Status</span><span></span></div>
  <div class="dash-row dash-product"><span class="dash-name"><span class="dash-dot"><span class="dot-status running"></span></span><strong class="list-name">API Management</strong></span>
    <span class="dash-ver"><span class="s-version">4.13.0-alpha.4</span></span><span class="dash-upd">${applyBadge("x", "pill warn", [TAG])}</span>
    <span class="dash-open"><span class="dash-compact"><button class="btn">Open${icon("chevron")}</button></span></span></div>
  <div class="dash-row dash-product"><span class="dash-name"><span class="dash-dot"><span class="dot-status running"></span></span><strong class="list-name">Access Management</strong></span>
    <span class="dash-ver"><span class="s-version">4.13.0-alpha.7</span></span><span class="dash-upd">${statusHtml("done", "Up to date", { quiet: true })}</span>
    <span class="dash-open"><span class="dash-compact"><a class="btn">${icon("external")}Open</a></span></span></div>
  <div class="dash-row dash-product"><span class="dash-name"><span class="dash-dot"><span class="dot-status stopped"></span></span><strong class="list-name">Alert Engine</strong></span>
    <span class="dash-ver"><span class="s-version">3.0.1</span></span><span class="dash-upd">${statusHtml("warn", "Update available")}</span><span class="dash-open"></span></div></div>
  <div class="card dash-card gal-card"><div class="card-head"><h2>Gravitee Cloud<span class="dash-account">My Account</span></h2><span class="dash-tools"><a class="btn">${icon("external")}Open console</a><a class="btn">Manage</a></span></div>
  <div class="dash-heads"><span>Environment</span><span>Gateway</span><span>Deploys</span><span></span></div>
  <div class="dash-row dash-env"><span class="dash-name"><strong class="list-name">Development</strong></span><span class="dash-gw"><span class="dash-cell-label">Gateway</span>${statusHtml("healthy", "Running here")}</span>
    <span class="dash-say"><span class="dash-cell-label">Deploys</span>${statusHtml("issue", "Cloud Token expired")}</span><span class="dash-open"><button class="btn">Replace Cloud Token</button></span></div>
  <div class="dash-row dash-env"><span class="dash-name"><strong class="list-name">Test</strong></span><span class="dash-gw"><span class="dash-cell-label">Gateway</span>${statusHtml("warn", "Needs settings")}</span>
    <span class="dash-say"><span class="dash-cell-label">Deploys</span>${statusHtml("", "Needs a Cloud Token", { quiet: true })}</span><span class="dash-open"><button class="btn">Settings</button></span></div>
  <div class="dash-row dash-env dash-skeleton"><span class="dash-name"><span class="skel"></span></span><span class="dash-gw"><span class="skel"></span></span><span class="dash-say"><span class="skel"></span></span><span class="dash-open"></span></div>
  <div class="dash-row dash-env dash-more"><a class="dash-more-link">3 more environments${icon("chevron")}</a></div></div></div>`)}
${block("Catalog rows: a list's, the Dashboard's", `<div class="card list-card gal-card"><div class="list-table"><div class="list-head list-grid"><span></span><span>Playground</span><span class="col-from">Catalog</span><span class="col-cat">Category</span><span>State</span><span></span></div>
  <ul class="list-rows">${[["Acme Bakery", "off", "Off", NEWER, "Start"], ["Acme Hostels API", "running", "Running", OUTSIDE, "Turn on"], ["Kafka", "starting", "Starting", {}, "Stop"]].map(([name, key, word, note, label]) => `<li class="list-item">
    <div class="list-row list-grid"><button type="button" class="s-toggle" aria-expanded="false" aria-label="${name}">${icon("chevron")}</button>
    <span class="list-main"><strong class="list-name">${name}</strong><span class="list-marks">${starButton({ name, starred: key === "off" }, true)}</span>
    <span class="list-sub"><span class="list-desc">What it is, one muted line</span></span></span>
    <span class="list-col col-from">Gravitee public</span><span class="list-col col-cat">AI</span><span class="list-state">${statusHtml(key, word, { quiet: key === "off" })}${noteBadge(note)}</span>
    <span class="list-actions"><a class="btn">Open</a>${actionButton({ key: `gallery:${name}`, label, icon: { Start: "play", Stop: "stop" }[label] || "", cls: "act" })}
    <span class="row-icons"><button class="icon-btn" aria-label="Edit">${icon("file")}</button><button class="icon-btn" aria-label="Settings">${icon("key")}</button><button class="icon-btn" aria-label="More">${icon("more")}</button></span></span></div></li>`).join("")}</ul></div>
  <div class="pager"><span>1–25 of 31</span><button class="icon-btn prev" aria-label="Previous page" disabled>${icon("chevron")}</button><button class="icon-btn next" aria-label="Next page">${icon("chevron")}</button></div></div>
  <div class="card dash-card gal-card gal-narrow"><div class="dash-row dash-item"><span class="dash-kind">${icon("flask")}</span><span class="dash-nameline"><a class="name-link list-name">Acme Hostels API</a></span>
    <span class="dash-sub-line">${statusHtml("running", "Running")}<span class="list-marks dash-marks">${noteBadge(OUTSIDE)}</span></span>
    <span class="dash-slot dash-act">${actionButton({ key: "gallery:hotels", label: "Turn on", cls: "act" })}</span><span class="dash-slot dash-set"><button class="icon-btn" aria-label="Settings">${icon("key")}</button></span></div></div>`)}
${block("Containers: its head, the core's band, a group, a container", `<div class="card gal-card"><div class="cols stack-head"><span></span><span>Service</span><span>State</span><span>Version</span><span>Ports</span><span>Uptime</span><span></span></div>
  <ul class="groups"><li class="stack-sep core-sep cols"><span class="core-name">Gravitee Platform<button type="button" class="pill warn"><span>License not saved</span>${icon("arrow-right")}</button></span>
    <span class="row-icons"><span class="icon-gap"></span><button class="icon-btn" aria-label="Edit">${icon("file")}</button><button class="icon-btn" aria-label="Settings">${icon("key")}</button><span class="icon-gap"></span><span class="icon-gap"></span><span class="icon-gap"></span></span></li>
  <li class="group open"><div class="group-row cols opens"><button type="button" class="s-toggle" aria-expanded="true" aria-label="API Management">${icon("chevron")}</button>
    <span class="s-name"><span class="g-name">API Management</span>${applyBadge("x", "pill warn", [TAG])}</span><span class="s-state">${statusHtml("running", "Running")}</span>
    <span class="s-version">4.13.0-alpha.4</span><span class="s-ports"><span>8082</span><span>+5</span></span><span class="s-uptime">34 h</span>
    <span class="row-icons"><button class="icon-btn" aria-label="Plugins">${icon("plug")}</button><span class="icon-gap"></span><span class="icon-gap"></span><button class="icon-btn" aria-label="Logs">${icon("logs")}</button><button class="icon-btn" aria-label="Restart">${icon("restart")}</button><button class="icon-btn" aria-label="Stop">${icon("stop")}</button></span></div>
    <ul class="containers"><li class="container cols"><span></span><span class="s-name"><span class="c-name">Gateway</span><span class="c-id">apim-gateway</span></span><span class="s-state">${statusHtml("healthy", "Healthy")}</span>
      <span class="s-version">4.13.0-alpha.4</span><span class="s-ports"><span>8082</span></span><span class="s-uptime">34 h</span><span class="row-icons"></span></li></ul></li>
  <li class="stack-sep">Add-ons</li></ul></div>`)}
${block("A table of Images and Plugins: a change, a state and its badge, a field and a value that cannot be chosen, the third-party band", `<div class="card gal-card"><div class="table-wrap"><table class="table"><thead><tr><th>Component</th><th>State</th><th>Version</th></tr></thead>
  <tbody><tr class="pending"><td><span class="list-name">API Management</span></td><td><span class="list-state"><span class="pill warn">Will change</span><span class="list-note">from 4.13.0-alpha.4</span></span></td><td><button class="combo-btn changed"><span class="value">4.12.20</span>${icon("chevron")}</button></td></tr>
  <tr><td><span class="list-name">Hybrid gateway</span></td><td><span class="list-state">${statusHtml("running", "Running")}${noteBadge(OUTSIDE)}</span></td><td><button class="combo-btn"><span class="value">4.12.21-debian</span><span class="follow-of">Same as API Management</span>${icon("chevron")}</button></td></tr>
  <tr><td><span class="list-name">ai-token-compression</span><div class="plugin-artifact">gravitee-policy-ai-token-compression</div></td><td><span class="list-state"><span class="pill">Bundled</span></span></td><td><span class="field-text">1.0.0-beta.2</span></td></tr>
  <tr class="section"><td colspan="3"><button class="stack-sep" aria-expanded="false"><span class="s-toggle">${icon("chevron")}</span>Third-party<span class="fold-summary"><span>Elasticsearch <code>9.5.2</code></span><span>MongoDB <code>8.0.29</code></span></span></button></td></tr></tbody></table></div></div>`)}
${block("The table of a Gravitee Cloud account: in use, not set up", `<div class="card plat-card gal-card"><div class="plat-table"><div class="plat-grid plat-heads"><span>Environment</span><span>Gateway</span><span>Deploys</span><span></span></div>
  <div class="stack-sep">In use</div>
  <div class="plat-grid plat-row"><strong class="plat-env">Development</strong><span class="plat-gateway"><span class="plat-cell">${statusHtml("running", "Running here")}</span></span><span class="plat-deploys"><span class="plat-cell">${statusHtml("issue", "Cloud Token refused")}</span></span><span class="plat-actions"><button class="btn plat-primary">Replace Cloud Token</button><button class="btn plat-more-btn">More${icon("chevron")}</button></span></div>
  <div class="stack-sep">Not set up</div>
  <div class="plat-grid plat-row plat-quiet"><span class="plat-env">Test</span><span class="plat-actions"><button class="btn plat-primary">Set up</button><span class="plat-more-gap"></span></span></div></div></div>`)}
${block("Consoles, as Containers lists them", `<div class="card gal-card"><div class="card-head"><h2>Consoles</h2></div><div class="links"><div class="link-group"><span class="link-section">API Management</span>
  <a class="link"><span class="dot-status running"></span><strong>Developer Portal</strong><span class="link-port">:8085/next</span>${icon("external")}</a>
  <a class="link"><span class="dot-status running"></span><strong>Classic Developer Portal</strong><span class="link-port">:8085</span>${icon("external")}</a>
  <a class="link off" aria-disabled="true"><span class="dot-status stopped"></span><strong>Gamma Console</strong><span class="link-port">:8086</span>${icon("external")}</a></div></div></div>`)}
${block("Logs: its two rows of tools", `<div class="card logs-card gal-card"><div class="list-tools"><label class="search grow">${icon("search")}<input type="search" placeholder="Search…" value="error"><label class="toggle-chip"><input type="checkbox"><span>.*</span></label><label class="toggle-chip"><input type="checkbox" checked><span>Only matches</span></label></label>
  <div class="list-filters"><button class="combo-btn"><span class="value">Gateway (API Management)</span>${icon("chevron")}</button><button class="combo-btn"><span class="value">Last 500</span>${icon("chevron")}</button></div></div>
  <div class="list-tools"><div class="levels"><label class="toggle-chip"><input type="checkbox" checked>${icon("eye")}<span>All</span></label><label class="toggle-chip lv-error"><input type="checkbox" checked>${icon("eye")}<span>Error</span><b>3</b></label><label class="toggle-chip lv-warn"><input type="checkbox">${icon("eye")}<span>Warn</span><b>12</b></label></div>
  <div class="log-controls"><label class="toggle-chip"><input type="checkbox" checked><span>Follow</span></label><label class="toggle-chip"><input type="checkbox"><span>Wrap</span></label><button class="icon-btn" data-tip="Pause the stream" aria-label="Pause the stream">${icon("pause")}</button><button class="icon-btn" data-tip="Clear" aria-label="Clear">${icon("trash")}</button></div></div></div>`)}
${block("Settings of an item: a form of rows", `<div class="card gal-card"><form class="env-form"><section class="env-section"><h3>Secrets</h3>
  <div class="env-row"><label><span class="env-what">Password of Alice, Bob and Tina</span><code class="env-key">ACME_BAKERY_DEMO_PASSWORD</code></label><div class="search masked"><input value="example"><button type="button" class="icon-btn" aria-label="Show">${icon("eye")}</button>${copyButton("example")}</div></div>
  <div class="env-row"><label><span class="env-what">Token of the hybrid gateway, written by the Gravitee Cloud page</span><code class="env-key">HYBRID_CLOUD_TOKEN</code></label><div class="search masked"><input placeholder="Not set"><span class="pill">Required</span>${copyButton("")}</div></div></section></form></div>`)}
${block("Menu and picker, open", row(`<div class="menu-pop gal-pop" role="menu"><button class="menu-item">Show logs</button><button class="menu-item">Copy address<span class="menu-hint">localhost:8090</span></button>
  <div class="menu-sep"></div><button class="menu-item danger">Remove Cloud Token<span class="menu-hint">Deploys stop</span></button><button class="menu-item danger">Stop using Development<span class="menu-hint">Removes its token and gateway</span></button></div>
  <div class="combo-pop plain gal-pop"><div class="combo-list" role="listbox"><div class="combo-option selected"><span class="check">${icon("check")}</span><span class="name">All kinds</span><span class="age"></span></div>
  <div class="combo-option active"><span class="check"></span><span class="name">Playgrounds</span><span class="age">3</span></div><div class="combo-option"><span class="check"></span><span class="name">Add-ons</span><span class="age">16</span></div></div></div>
  <div class="combo-pop plain multi gal-pop"><div class="combo-list" role="listbox"><div class="combo-group">API Management</div>
  <div class="combo-option selected"><span class="check">${icon("check")}</span><span class="dot-status running"></span><span class="name">Gateway</span><span class="age">apim-gateway</span></div>
  <div class="combo-option"><span class="check"></span><span class="dot-status running"></span><span class="name">Hybrid gateway</span><span class="age">apim-hybrid-gateway</span></div>
  <div class="combo-option"><span class="check"></span><span class="dot-status running"></span><span class="name">Hybrid gateway Redis</span><span class="age">hybrid-redis</span></div></div></div>`))}
${block("A dialog's form", `<div class="gal-dialog create-dialog platform-dialog"><h2>Connect account</h2>
  <div class="field"><span class="field-label"><label>Account Token</label>${tipHtml(icon("info"), ["Account Token", "Lets the Lab read this account."], "plat-info", "Account Token")}</span><div class="search"><input type="password" value="secret"></div></div>
  <div class="field"><span class="field-label"><label>Account ID</label>${tipHtml(icon("info"), ["Account ID", "Next to the Account Tokens."], "plat-info", "Account ID")}</span><div class="search"><input value="abc" aria-invalid="true"></div></div>
  <p class="field-note err">The Account ID is a UUID, as the Account Tokens page shows it</p>
  <details class="more-options" open><summary>${icon("chevron")}More options</summary><div class="field"><span class="field-label"><label>URL</label></span><div class="search"><input value="https://eu.cloud.gravitee.io"></div></div></details>
  <div class="dialog-actions"><button class="btn">Cancel</button><button class="btn primary">Connect</button></div></div>`)}
${block("The head of a Gravitee Cloud account", `<div class="card plat-card gal-card"><div class="plat-head"><span class="plat-mark">${icon("cloud")}</span><span class="plat-id"><strong class="plat-name">My Account</strong><span class="plat-host">eu.cloud.gravitee.io</span></span>
  <span class="plat-state"><span class="pill ok">Connected</span></span><span class="plat-tools"><a class="btn">${icon("external")}Open console</a><button class="btn plat-more-btn">More${icon("chevron")}</button></span></div></div>`)}
${block("Metric strip", `<div class="card dash-resources" style="--cell: 330px"><h2 class="dash-res-label">Resources</h2>
  <div class="dash-cell"><span class="dash-label"><span class="dash-weigh">${tipHtml(`CPU<span class="dash-mark" aria-hidden="true">${icon("info")}</span>`, { title: "Heaviest containers", rows: [{ lines: [[{ who: "Elasticsearch" }, "9.0%"], [{ who: "Gateway" }, "2.0%"]] }] }, "", "CPU", { short: true })}</span></span><span class="dash-figure"><span class="dash-value">2.6%</span><span class="dash-of muted">of 6 cores</span></span></div>
  <div class="dash-cell"><span class="dash-label">Memory</span><span class="dash-figure"><span class="dash-value" data-level="warn">6.8 GB</span><span class="dash-of muted">of 7.7 GB</span></span></div>
  <div class="dash-cell dash-storage"><span class="dash-label">Storage</span><span class="dash-figure"><span class="dash-value">85.8 GB</span><span class="dash-of muted">in Docker</span></span>
    <span class="dash-tail"><span class="dash-unused muted">${tipHtml(`57.5 GB unused<span class="dash-mark" aria-hidden="true">${icon("info")}</span>`, UNUSED)}</span><button class="icon-btn" aria-label="Count storage again" data-tip="Count again, counted 1 min ago">${icon("restart")}</button></span></div>
  <div class="dash-cell dash-engine"><span class="dash-label">Docker</span><span class="dash-figure"><span class="dash-value">29.7.2</span><span class="dash-of muted">Compose 5.4.0</span></span></div></div>`)}
${block("Empty and loading: one block, its words then its action", `<div class="card"><div class="empty">${emptyHtml({ filtered: true })}</div><div class="empty"><span>Choose the services to read</span><button class="btn">Choose services</button></div>
  <div class="empty"><span class="loading"><span class="spinner"></span>Loading</span></div><div class="plat-skel"><span></span><span></span><span></span></div></div>`)}
${block("Toasts", row('<div class="toast">Created</div><div class="toast error" role="alert"><span>Docker is not reachable</span><button type="button" class="icon-btn toast-close" aria-label="Close">' + icon("close") + "</button></div>"))}`;

for (const half of document.querySelectorAll("[data-gallery]")) half.innerHTML = html(half.dataset.gallery);
document.body.dataset.ready = "1";
