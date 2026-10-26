# Fixture catalogs

Synthetic catalogs for the tests of the Lab UI, and the helpers in `../catalogs.py` that put them in a temporary
project folder. Nothing in this folder is real: no item, no address, no organization. Addresses end in `.invalid`,
accounts in `.test`, images are `busybox` or `registry.example.invalid/...`.

The engine's tests read no real content. They use these catalogs, and the checks that need the real items run in the
default catalog's own CI.

## What is here

```
catalogs/
  fixture/    id fixture, Public, level 1: the default catalog of the tests, so its ids are bare
  partner/    id partner, version 2.0.0, Private by its lock: one add-on that fixture includes
  lab-new/    level 99: a catalog written for a Lab that does not exist yet (one add-on)
  lab-old/    level 0: a catalog written for a Lab that is no longer read (one add-on)
  hostile/    one unit per rule of the trust policy (owned by the trust tests, not described here)
local/        the personal area of a machine: an add-on that includes a unit of fixture, and a playground
expected.json what the trust policy and the resolver must say of every unit of fixture and partner
```

### Units of `fixture`

| Unit | Id | What it is for |
|---|---|---|
| add-on `echo` | `echo` | Publishes 127.0.0.1:8191, has a console label and a setting with its prefix |
| add-on `echo-core` | `echo-core` | Includes `echo` and overlays the core's gateway (`environment`, `depends_on`) |
| add-on `echo-twin` | `echo-twin` | Wants port 8191 too and declares a conflict with `echo` |
| add-on `port-core` | `port-core` | Wants port 8082, which a core service publishes |
| add-on `gateway` | `gateway` | Declares `x-gravitee.gateway` for the platform kind `cloud`, with a required token setting |
| add-on `future` | `future` | Asks for Lab level 99, so it is blocked at unit level |
| add-on `cluster` | `cluster` | Runs privileged and says why in `x-gravitee.host_access` (class H) |
| setup `pair` | `pair` | Includes `echo` and, from another catalog, the `ledger` of partner |
| playground `shop` | `shop` | A full playground: web app with a Dockerfile, seed for API Management and Access Management, three-step guide with one `action:try-it` link. It cannot go to a platform |
| playground `tour` | `tour` | API Management only, so it can go to a platform |

`partner` holds the add-on `@partner/ledger`. `lab-new` holds `@lab-new/newer` and `lab-old` holds `@lab-old/older`.
`local/` holds the add-on `local/scratch` and the playground `local/sandbox`.

Every unit loads with the real core under Compose 5 (`docker compose config`, no daemon). A seed service has
`image: __SEEDER_IMAGE__`: the helpers write there the `x-gravitee.seeder.image` of the core file.

## The helpers

`lab-ui/tests/fixtures.py`. Copies, never links: a container writes a `.env` or a `.state` beside a unit, and a test must
not touch the repository. Python 3.9, standard library only.

```python
from . import Case, catalogs as fixtures

class Example(Case):
    def test_it(self):
        fixtures.project(self, paths, compose)        # core, anchors, catalogs.default.json, fixture installed
        fixtures.install(self, "partner")             # a folder catalog; kind="git" for a bare remote and a manifest
        fixtures.local(self, "scratch")               # a unit of this machine
        fixtures.lock(self, "partner", audience="private")
```

| Helper | What it does |
|---|---|
| `project(case, *modules, ns="fixture", kind="folder")` | A project folder: the real core, `seeder/`, `plugins/apim`, `plugins/am`, the default file, `ns` installed. `modules` are the ones whose `ROOT` is replaced. With `kind="git"` the default file lists the address of the bare repository, because the address of a listed catalog is the tracked one |
| `default(case, ns="fixture", also=(), **entry)` | `catalogs.default.json`. Give `url=` to point it at a bare remote |
| `install(case, ns, kind="folder"\|"git", **patch)` | The catalog on disk, its entry in `catalogs.json`, its entry in `catalogs.lock`. Git: also a bare repository, the manifest `.installed.json` and the read-only files the Lab leaves. Folders are `0755` whatever the umask |
| `remote(case, ns, **patch)` | The bare repository of a catalog, and nothing installed. For the Add and the first download |
| `declare(case, ns, **patch)` | A catalog added and never downloaded: an entry, a lock with `pin: null` |
| `lock(case, ns, audience="public", lab=None, version=None, **fields)` | The lock entry. `lab` and `version` also go to the installed `catalog.yml` and to the manifest, where the lock's cache comes from |
| `local(case, name)` | A unit of `local/` copied to `.lab/catalogs/local/` |
| `render(root, *files)` | `docker compose config --format json` of the core with unit files on top. Never a daemon |
| `expected()`, `units(ns)`, `closure(...)`, `unit_id(...)`, `unit_of(path)` | The oracle, the units of a fixture catalog, and the ids of the units |

`install` takes a `patch`. The entry: `url`, `branch`, `auto`, `host`, `added`, `shadows`. The catalog file and the lock's
cache of it: `name`, `version`, `lab`. The lock: `audience`, `pin`, `previous`, `tip`, `roots`, `installed`, `checked`,
`error`, `pending`, `via`, `shadowed`. The content: `drop` (paths left out) and `files` (`{path: text}` added or
replaced, which is how a hostile file gets in). How it is put there: `link` (a folder catalog as a link), `readonly`,
`store` (the Lab's own bare store, `.lab/cache/git/<ns>.git`, made the way `store.init` and `store.fetch` make one).

A patch that cannot be a state of the Lab is a `ValueError`, and nothing is written: an unknown key; a value of the wrong
type (`lab` is a whole number, `version` is `1.4.0`, `name`, `url`, `branch` and `added` are texts, `auto`, `host`,
`link`, `readonly`, `store` and `shadowed` are true or false, `shadows` is a dict); `link` for a git catalog; a name of
`catalog.yml` to write while `catalog.yml` is dropped; a path that is absolute, empty, or holds `..`.

A shadow is a folder catalog with `store=True`: `install(case, "partner", link=True, store=True)`. It is a link
to a folder, with a bare repository behind it, the Lab's store holding that commit, a lock that names the commit and says
`shadowed`, and an entry that says what it `shadows`.

Dates are fixed (2026-10-16 plus one minute per entry), and a git catalog's commit id is the same on every run.

`catalogs.json` and `catalogs.lock` are written mode 0600, through
`paths.write_atomic`. `.installed.json` holds what an install made. The tree digest of a unit is the sha256 of the sorted lines
`<mode> <oid> <path>` of its files.

### One call for each case of a missing catalog

`pair` includes a unit of `partner`, so the state of `partner` makes every case of "Needs a catalog":

| Case | Call |
|---|---|
| Missing | Do not add `partner` |
| Downloading | `declare(case, "partner")` |
| Failed | `declare(case, "partner", error="unreachable")` |
| Gone | `install(case, "partner", drop=["addons/ledger"])` |
| Old | `install(case, "partner", version="0.9.0")`; `fixture` asks for 1.0.0 |
| Depends on private content | The default `install(case, "partner")`: partner is Private and fixture is Public. `lock(case, "partner")` makes partner Public |

### Without a tool

Git and Compose are optional. A test that needs one and finds none calls `need_git()` or `need_compose()`, which print a
`SKIP` line on standard error and skip the test. A skip is not a pass.

## `expected.json`

One entry per unit of `fixture` and `partner`, keyed `kind:id` (`addon:echo`, `setup:pair`, `addon:@partner/ledger`).
It is written for this context: `fixture` is the default catalog (listed, so class H is allowed when declared) and
Public, `partner` is another catalog and Private, the core says contract 1 and contract_min 1, no `.env` sets anything.

| Field | Meaning |
|---|---|
| `verdict` | `ok` or `blocked` |
| `reasons` | The reasons the trust policy gives, in the order of the rules; empty when `ok` |
| `notes` | The lines of N01 to N05 |
| `surface` | The ten keys of the surface. Own services only. A service with a `build` is under `builds` and not under `images`, the official seed's build included. `core` lists the core services a unit sets keys on |
| `closure` | The unit first, then the setups it includes, then its add-ons, each sorted by id |
| `ports` | The host ports published by the services of the closure, the core's own services left out |
| `lint` | What `catalog.py check` is expected to say of the unit besides its Blocked reasons, which are errors and which `reasons` already holds |
| `variants` | Where another context changes the answer: `setup:pair` is `ok` when partner is Public |

Two units are blocked: `future` (`Needs a newer Gravitee Lab`) and `pair` (`Depends on private content`).

`port-core` is the only finding known today. It publishes a port that the core publishes, which `make check` reports as
`publishes port 8082, like docker-compose.yml`. Section 17.1 says `catalog.py check` reports nothing on `fixture` and
`partner`; that cannot hold for `port-core` unless the check leaves the core's ports to the test at start,
and not for `future`, whose Blocked reason is an error. Which of these the check says depends on WP9, and the entry of
`lint` changes with it:

| Unit | What WP9 decides |
|---|---|
| `port-core` | Whether the port of a core service is a finding of the check, or only of the start |
| `future` | Section 12.5 makes a Blocked reason an error, so the check should print T26 for it: then the line is added here |
| `pair` | Blocked (T24) only when the catalog is measured as Public; a folder catalog is measured as Private (6.4), so the check should not block it |
| `cluster` | Class H: allowed when the catalog is listed or linked with `--allow-machine`, blocked otherwise |

Nothing compares `lint` with anything yet. The rest of `expected.json` is compared with the Lab's own policy by the checks
below.

## Checking the fixtures

```bash
cd lab-ui && python3 -m unittest tests.catalogs -v
```

The file is not named `test_*`, so `make test` leaves it out. Run it after a change to a fixture or to a helper; a
one-line `tests/catalogs/test_fixture_catalogs.py` (`from .catalogs import FixtureCatalogs  # noqa: F401`) is what puts it in
`make test`, and belongs to the package that owns the test plan. It checks:

- the units of each catalog, the shape of every `catalog.yml`, includes at the fixed depth, one-line
  READMEs, guides, descriptions, and settings that are described and prefixed;
- that the files are ASCII with LF line ends, and name no real address, organization or image. The nightly job points
  `LAB_FIXTURE_DENYLIST` at the default catalog's `catalog.json` or at a text with one item id on each line,
  and the check refuses each id as a word, in the files of the fixtures (the catalogs but the hostile one, `local/`,
  `expected.json` and this README). A variable that names no file, or a file with no id, fails the check instead of
  guarding nothing; unset, the denylist is empty;
- `expected.json`: its shape, its closures, and with Compose present its ports, images, builds, overlays, seed and
  privileged services against the rendered files; its blocked units against the levels and audiences; and, once
  `trust.judge` and `resolve` exist, the verdict, the reasons, the notes and the whole surface of every unit against what
  the policy says of it, fed as the project feeds it;
- the declarations other packages rely on, one by one: the console of `echo`, the conflict of `echo-twin`, the gateway
  of `gateway`, the level of `future`, the includes;
- every unit of `fixture`, `partner`, `lab-new`, `lab-old` and `local` rendered with the real core;
- each helper, its files, its modes, its refusals and its idempotence, and a git install against a bare repository;
- against the Lab's own modules: `paths.unit_of`, `resolve.closure`, `store.selected`, `store.unit_digests`,
  `store.init` and `trust.judge` give what the fixtures state. A check is skipped, with a `SKIP` line, only while its
  module does not exist; once it does, a name it lost or an error inside it fails the check.

## Adding a unit

A unit of `fixture` or `partner` follows the rules of `docs/units.md` and is written for the fixed depth: `../../addons/...`
for a unit of the same catalog, `../../../partner/addons/...` for another, `../../../../../seeder` for the seeder.

1. Name it so that it cannot be a real item, and prefix its services and settings with the catalog (`fixture-`,
   `FIXTURE_`).
2. Give an add-on or a playground a one-line `README.md` and a playground a `guide.md` and a `seed/`.
3. Run the checks above. They fail when the unit has no entry in `expected.json`, so add its entry, with the closure and
   the ports its files give and the verdict the policy must give.

A unit for one rule of the trust policy goes to `catalogs/hostile/`, with its line in `expected-hostile.json`: those
belong to the trust tests and are not described here.
