# Fixture catalogs

Synthetic catalogs for the tests of the Lab UI. The helpers in [`../fixtures.py`](../fixtures.py) put them in a temporary
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

`rules/`, `settings/`, `standard/` and `trust/` have a README of their own. `regen.py` writes the goldens of `trust/` and
`expected-hostile.json`, the oracle of the hostile catalog.

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

## `expected.json`

One entry per unit of `fixture` and `partner`, keyed `kind:id` (`addon:echo`, `setup:pair`, `addon:@partner/ledger`).
It is written for this context: `fixture` is the default catalog (listed, so class H is allowed when declared) and
Public, `partner` is another catalog and Private, no `.env` sets anything.

| Field | Meaning |
|---|---|
| `verdict` | `ok` or `blocked` |
| `reasons` | The reasons the trust policy gives, in the order of the rules; empty when `ok` |
| `notes` | The lines of N01 to N05 |
| `surface` | The seven keys of the surface. Own services only. A service with a `build` is under `builds`, the official seed's build included. `core` lists the core services a unit sets keys on |
| `closure` | The unit first, then the setups it includes, then its add-ons, each sorted by id |
| `ports` | The host ports published by the services of the closure, the core's own services left out |
| `lint` | What `catalog.py check` is expected to say of the unit besides its Blocked reasons, which are errors and which `reasons` already holds |
| `variants` | Where another context changes the answer: `setup:pair` is `ok` when partner is Public |

Two units are blocked: `future` (`Needs a newer Gravitee Lab`) and `pair` (`Depends on private content`).

`port-core` is the only unit with a `lint`: it publishes a port that the core publishes.

## Adding a unit

A unit of `fixture` or `partner` follows the rules of `docs/units.md` and is written for the fixed depth: `../../addons/...`
for a unit of the same catalog, `../../../partner/addons/...` for another, `../../../../../seeder` for the seeder.

1. Name it so that it cannot be a real item, and prefix its services and settings with the catalog (`fixture-`,
   `FIXTURE_`).
2. Give an add-on or a playground a one-line `README.md` and a playground a `guide.md` and a `seed/`.
3. Run `tests/library/test_check.py`. It fails when the unit has no entry in `expected.json`, so add its entry, with the
   closure and the ports its files give and the verdict the policy must give.

A unit for one rule of the trust policy goes to `catalogs/hostile/`, with its line in `expected-hostile.json`: those
belong to the trust tests and are not described here.
