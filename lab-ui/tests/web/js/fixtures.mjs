// What `GET /api/state` says of the catalogs, as the state contract freezes it, for the tests of the pages that draw it:
// a row for each kind of catalog, the field `content` in each of its four keys, the fields an item gains, and strings that are hostile on
// purpose. Every name here is made up. Nothing in it is read from a server: a test builds the state it needs from these and changes what
// it is about, so that the pages are built against the contract and not against a running backend. Not a test: node does not run it.

// The address a catalog the Lab lists is fetched from, as the server says it (host and repository)
export const ADDRESS = "github.com/example/catalog";

// What a person must never see run as markup, in any place a name, a description, an address or a reason is drawn
export const HOSTILE = '<img src=x onerror="boom()">';
export const HOSTILE_QUOTED = `"><script>boom()</script>'`;

// A state key: the server writes its word, tone, reason and next step. `STATE` is exported for the tests
// of the pages that draw a state, so that none writes its own shape of one.
export const STATE = (key, label, tone = "muted", reason = "", next = "") => ({ key, label, tone, reason, next, whatsnew: [] });

// Every key of the catalogs' states, the "machine" one apart (the row of Personal has it: PERSONAL), in the order of the table of 1.3
export const STATES = {
  uptodate: STATE("uptodate", "Up to date", "muted", "Version 1.4.0, checked 3 hours ago"),
  update: { ...STATE("update", "Update available", "warn", "Version 1.5.0"), whatsnew: ["Add the ledger"] },
  checking: STATE("checking", "Checking"),
  updating: STATE("updating", "Updating", "working"),
  downloading: STATE("downloading", "Downloading", "working", "From github.com/owner/repo"),
  unreachable: STATE("unreachable", "Unreachable", "warn", "Cannot reach github.com.", "Check your connection, then try again."),
  noaccess: STATE("noaccess", "No access", "error", "No access to owner/repo.", "Run gh auth login in a terminal, then try again."),
  needs_git: STATE("needs_git", "Needs Git", "error", "Git is not installed.", "Install Git, then try again."),
  wrong_catalog: STATE("wrong_catalog", "Wrong catalog", "error", "This address does not serve the catalog this Lab expects.", "Run make update, then try again."),
  rewritten: STATE("rewritten", "Rewritten", "error", "This catalog was rewritten or moved.", "Update only if you expected it."),
  lab_new: STATE("lab_new", "Needs a newer Lab", "warn", "A newer version needs a newer Lab. This is the newest one this Lab reads.", "Run make update, then restart the Lab."),
  lab_old: STATE("lab_old", "Written for an older Lab", "warn", "This version is too old for this Lab.", "Update it, or ask the owners of Acme Hub to update it."),
  damaged: STATE("damaged", "Damaged", "error", "Some installed files changed.", "Repair it from the menu."),
  linked: STATE("linked", "Linked", "muted", "The folder is linked."),
  missing_folder: STATE("missing_folder", "Folder not found", "error", "The folder is gone.", "Put it back or remove the catalog."),
  suggested: STATE("suggested", "Not added", "muted"),
};

// A row of `state.catalogs`: an example, a git catalog that is up to date, with what a person is shown
export const catalog = (more = {}) => ({
  id: "acme-hub", kind: "git", listed: false, name: "Acme Hub", description: "Banking demos for partner workshops",
  address: "github.com/acme/lab-hub", url: "https://github.com/acme/lab-hub.git", web_url: "https://github.com/acme/lab-hub",
  version: "1.4.0", commit: "9f1c2ab", updated: "2026-10-12T18:03:11Z", checked: "2026-10-16T09:12:30Z",
  audience: "private", counts: { playground: 14, setup: 2, addon: 9 },
  state: STATES.uptodate, progress: null, attention: null,
  auto: true, has_previous: false, writable: false, host: "no",
  held: { update: "", remove: "", undo: "No previous version" },
  blocked: [], uses_machine: [], ignored: 0, problems: [], needs: [], ...more,
});

// The first catalog the Lab lists, the one whose items have bare ids; this machine's own row; a listed catalog the person removed; a folder
export const FIRST = (more = {}) => catalog({ id: "gravitee", name: "Gravitee public", description: "The demos everyone has", listed: true,
  address: ADDRESS, url: `https://${ADDRESS}.git`, web_url: `https://${ADDRESS}`, audience: "public", host: "listed", ...more });
export const PERSONAL = (more = {}) => ({ id: "local", kind: "machine", listed: false, name: "Personal", description: "", counts: { playground: 1, setup: 0, addon: 2 },
  state: { key: "machine", label: "", tone: "muted", reason: "", next: "", whatsnew: [] }, progress: null, attention: null, ...more });
export const NOT_ADDED = (more = {}) => ({ id: "gravitee", kind: "suggested", listed: true, name: "Gravitee public", description: "The demos everyone has",
  address: ADDRESS, url: `https://${ADDRESS}.git`, state: STATES.suggested, progress: null, attention: null, ...more });
export const FOLDER = (more = {}) => catalog({ id: "my-catalog", kind: "folder", name: "My catalog", description: "", address: "~/lab/my-catalog", url: "", web_url: "",
  version: "", commit: "", writable: true, state: STATES.linked, ...more });

// The field `content`: key `ready` when any catalog has a pin or any folder catalog is there, `downloading` while a git entry has an attempt
// running, `failed` when one has a last error (`line`, its `code` and the `address` it was asked), else `none`, with `offer`, the first listed
// catalog that is not added
export const OFFER = { id: "gravitee", name: "Gravitee public" };
export const CONTENT = {
  ready: { key: "ready", line: "", code: null, progress: null, address: ADDRESS, offer: null },
  downloading: { key: "downloading", line: "", code: null, progress: 0.42, address: ADDRESS, offer: null },
  failed: { key: "failed", line: "Cannot reach github.com. Check your connection, then try again.", code: "unreachable", progress: null, address: ADDRESS, offer: null },
  none: { key: "none", line: "", code: null, progress: null, address: "", offer: OFFER },
};
export const content = (key, more = {}) => ({ ...CONTENT[key], ...more });

// What an item gains: the catalog it comes from (null for the first catalog's and this machine's), whether it can be edited, the path
// shown, why it is blocked, the catalogs it lacks, how it reads against this Lab, its twin, and what it uses of this machine
export const origin = (more = {}) => ({ id: "acme-hub", name: "Acme Hub", version: "1.4.0", ...more });
export const gains = (more = {}) => ({ origin: null, readonly: false, display_file: "", blocked: null, needs_catalogs: [], compat: "ok", personal: false, twin: null,
  uses_machine: [], ...more });
export const item = (kind, id, name, more = {}) => ({ kind, id, name, category: "Demo", description: `${name} for the demo`, file: `${kind}s/${id}/compose.yml`, settings: [],
  on: false, up: false, state: { key: "off", label: "", running: false }, progress: null, note: "", note_tone: "", note_title: "", note_target: null, note_file: null,
  starred: false, ...gains(), ...more });

// A note that says an item needs a catalog, for each reason the server has, and one that says why it is blocked
export const NEEDS = {
  missing: { kind: "catalog", name: "Partner Bank", min: "2.0.0", reason: "missing" },
  downloading: { kind: "catalog", name: "Partner Bank", min: "2.0.0", reason: "downloading" },
  failed: { kind: "catalog", name: "Partner Bank", min: "2.0.0", reason: "failed" },
  gone: { kind: "catalog", name: "Partner Bank", min: "2.0.0", reason: "gone", unit: "Ledger" },
  old: { kind: "catalog", name: "Partner Bank", min: "2.0.0", reason: "old" },
};
export const BLOCKED = { kind: "blocked", reasons: ["Mounts the Docker socket", "Runs with full privileges", "Publishes a port to the network"], owner: "Acme Hub", folder: false };

// The notice of the move to catalogs, as `layout.status()` says it: a row has names or is not there, and the words are the
// design's. Its row `restart` is computed where the state is (it needs what runs, and is late), the others come from the report of the move.
export const NOTICE_ROWS = {
  settings: ["Settings", ""], personal: ["Now personal", "Not in a catalog"], kept: ["Kept aside", "Your changes, in .lab/migration/kept"],
  turned_off: ["Turned off", "Needs a catalog"], restart: ["Restart needed", "Their folders moved"], not_moved: ["Not moved", "Files stay where they were"],
};
export const noticeRow = (key, items) => ({ key, label: NOTICE_ROWS[key][0], items, hint: NOTICE_ROWS[key][1] });
export const notice = (rows = [], more = {}) => ({ id: "2026-10-17T10:00:00Z", title: "Lab updated", text: "", rows, moved: [], button: "Close", ...more });
export const LAYOUT = {
  ready: (rows, more) => ({ phase: "ready", notice: notice(rows, more) }),
  waiting: () => ({ phase: "waiting", notice: notice([noticeRow("settings", ["Not changed"])], { id: null }) }),
  failed: () => ({ phase: "failed", notice: notice([], { id: null, text: "Could not move your settings. Nothing was deleted.", button: "Try again" }) }),
};

// The state around them: only what the pages that draw catalogs read. `more` replaces any of it.
export const lab = (more = {}) => ({
  blind: false, wait: "", docker: { error: null }, license: null, products: [], categories: [], links: [], problems: [], services: {}, job: null,
  playgrounds: [], setups: [], addons: [], git: { available: true, version: "2.43.2" }, catalogs: [], content: content("ready"),
  layout: { phase: "ready", notice: null },
  platform: { name: "Gravitee platform", state: { key: "stopped", label: "Stopped", running: false }, progress: null, groups: [], gateways: { cloud: null } }, ...more,
});
