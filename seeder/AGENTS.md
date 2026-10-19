# Seeder

The seeder is the engine every playground's seed runs. Through the products' management APIs it creates the playground's
data in Gravitee (OpenFGA stores, an Access Management domain, APIs, applications and subscriptions, Gamma objects) and
records what it created so that it can delete exactly that. Read the [root guide](../AGENTS.md) first. How a playground
runs it and which folder each module reads: [README.md](README.md). The seed files and the settings:
[the seed](../docs/playgrounds.md#the-seed).

## Module contract

A module is one file of `lab_seeder/`, registered in `lab_seeder/seed.py`.

| Name | Role |
|---|---|
| `SECTION` | The prefix of its log lines (`[section]`) and the `product` of its records |
| `ORDER` | The kinds it records, dependents first: the order of a removal |
| `seed()` | Creates its objects. Returns before it reads a setting or connects when its folder of `seed/` is absent (`am.seed(stores)` takes the OpenFGA stores) |
| `remove(records)` | Deletes what `owned(records, SECTION, ORDER)` returns. It may return the records of what the platform refused to delete (a user that owns an application): the seed keeps those in the record and removes the rest |
| `FOLDERS`, `GAMMA` | For a module that fills Gamma: the folders or files of `seed/` it reads, and the Gamma modules it needs (`aim`, `authz`, `platform`). `seed.py` checks that the platform serves them before it changes anything |
| `PATHS` | Optional and local to a module: the API path of each kind it records. The engine does not read it |

A new module is imported in `lab_seeder/seed.py` and added to `AFTER_AM`, which fixes where it runs; a removal runs the modules in
reverse. Its tests go in `tests/test_<module>.py`, using `Seed` from `tests/__init__.py`.

## Idempotency and the record

The seed runs when a Start sets the playground up, on Re-seed, and again with any command that names no service
(`make all`, a plain `docker compose up`), so a second run must change nothing.

- Look each object up by name, slug or id, and write it only when it differs.
- Record what you create with `record(SECTION, kind, label, name, id)`. `.state/created.json` is a list of
  `{product, kind, label, name, id}` objects, plus the keys a module adds (an API keeps a `digest`). `label` is the plural
  the Lab UI shows when it asks whether to delete ("3 APIs").
- Replace or delete only what the record says the seed created. Stop with a `SeedError` that names an object you find
  and did not create. What is updated and never deleted: [what it records](../docs/playgrounds.md#what-it-records).

## Environments

`apim.py` seeds the platform's own environment (`seed/apim/`, `APIM_ENV`) and each folder of `seed/environments/`, in that
order. An environment of its own is looked up and never created or deleted, because no API does either: the playground's
one-shot service writes it. [The folder's layout](../docs/playgrounds.md#environments-of-its-own) and
[what the platform does](../docs/playgrounds.md#environments-and-developer-portals) are in the playground reference.
`apim_portal.py` follows `apim.py` and is removed before it, because portal objects name APIs.

- **A record carries its environment.** The name of an object of an environment of its own is scoped, `<name> [<env>]`, and the
  record has `env`: the same API name in two environments is two records. The platform's own environment keeps plain names,
  so that an existing `.state/created.json` stays valid. A removal builds each record's URLs from its `env`.
- **Each target runs in one order**: the seed's login must have a role in the environment (`membership`), then its users, its
  settings, its groups, its APIs, its applications, its subscriptions. The platform's own environment goes first, then each
  folder of `seed/environments/` in name order. Every environment is read and looked up before the first change, and the
  wait for the gateway comes once, at the end, over the APIs of every environment.
- **The engine provides `ENV_ID` and `ENV_NAME`** while it renders the files of an environment (`PROVIDED` in `common.py`):
  a seed file may use them with no setting, and `make test` does not call them unset. A page's own `${...}` is written
  `$${...}`.
- **The users hook** (`users` in `apim.py`) looks a user up and registers it when the platform can: a user that exists is
  adopted, never recorded, never changed. A memory user has no row before its first sign-in, so its group gets it
  (`enroll`: the member is added from the identity provider's search, as the Console does) and the user is recorded then.
  A group is checked after it is written, because the Automation API drops a member it does not know without an error: one
  still missing stops the seed with a line that names the user.
- **`membership`** gives the seed's login the environment's administrator role in each environment that the Console does not list
  for it (every one inserted in the database: the login acts in it all the same, but the data steward would not find it in the
  environment picker), records it, and a removal takes it back. An environment that lists it is left alone.
- **What the files show is refused before a module's first change**: an environment folder that is no id, is the platform's
  own or whose `environment.yaml` names another id, an API or application that names a group its folder lacks, two
  applications of one name, a key or a token that YAML reads as a number or a boolean, a listing that names an API or a
  location its folder lacks, a second portal, an AsyncAPI document that no listed API owns or that the viewer cannot read, an asset
  that is missing or too large. A group member that `users.yaml` lacks is not refused here (the platform may know the user, and
  `enroll` looks it up): `make test` checks it.
- **Portals** (`apim_portal.py`) follow `apim.py` and read `portal/` of `seed/apim/` and of each environment. The theme,
  portal, pages and links go through the Automation API, which keeps each by hrid; a listing goes through the Console's
  navigation items. The module's docstring says why, and what the platform does with each.
- **The Overview page of each listed API** is asked of the platform (`_default-pages`), published, and written from
  `portal/api-overview.md` when the folder has it. An API with a file in `portal/api-docs/` (`<feed>.yaml`, the end of its
  crossId `<environment id>-<feed>`) gets an AsyncAPI page after it, made, written, then published. The platform checks no
  AsyncAPI text and the portal's viewer shows an error in its place, so `apim_portal.py` reads the document first
  (`ASYNCAPI_VERSIONS`, an `info` with a title and a version, `channels`) and refuses a file that names no listed API.
- **The pages of the API itself** are what the page an API card opens reads (`/catalog/api/<id>`), and are made through
  `.../apis/{apiId}/pages`: a Markdown homepage from `portal/api-details.md` (the platform fills its template with the name,
  version and description only and refuses the components of Gravitee Markdown) and an AsyncAPI page from the document. They are
  found by type and name, never taken when the seed did not make them, recorded as soon as they exist, and deleted when their
  file goes. A `PUT` of such a page resets its order and its homepage, so the seed sends both.
- **The listing is recorded first, then its pages** as one `portal-api-page` record that counts them (`items`: the pages of the
  menu, `legacy`: those of the APIs, `{api, page}` each). A removal needs no call for the pages of the menu, because the API
  items of the listing go with their children, and deletes the pages of the APIs by id, which the platform would delete with the API.
- **A removal skips an environment that is gone** (`apim.py` and `apim_portal.py` both ask once, and the platform answers 400 to
  every route of one it lacks), with one line for each record.

## The Lab UI contract

- A seed that stops on a `SeedError` prints `[seed] ✗ <why>` as its last line and exits with an error. The Lab UI shows
  `<why>` as the reason the start failed, so keep it to one line (`excerpt()` in `common.py`).
- `seed.py --remove` deletes the recorded items from the last module to the first, then empties the record. The Lab UI
  reads the record to say what a delete removes.
- Log with `log(section, message)`, and never a secret, a token or a password.

## Endpoints, not containers

Every address, credential and id comes from the environment (`setting()` in `common.py`), and seed files hold `${VAR}` for
what depends on the target (`render()`). No module and no seed file names a container or writes an address: the
playground's seed service sets the variables. `make test` fails on a literal address and on a variable no seed service sets.

## Behavior to keep

- **An import that fails after the platform made the API** ("API imported with error", the API kept, stopped) is recorded
  with no digest before the error is raised, so that the next run finds it as its own and updates it. The same for the
  import that follows a replacement.
- **A JSON seed file is read as a document** (`render_json` in `common.py`): a variable is replaced in each string of it, and a
  string that is itself JSON text gets the value escaped for that text. Keep a variable of a file inside a string, so that a
  value with a quote or a backslash cannot break the file; a file that is no document until its variables are replaced is
  replaced as text.
- **A renamed API is one record**: an update drops the record that held the id under its old name.
- **LLM, MCP and A2A proxy APIs** cannot be updated from a definition, so they are replaced. A control's steps are
  switched in place, with an update of the API entity and a deployment. The API is imported again only when the platform
  answers 404, 405 or 501 to that update (`UNSUPPORTED` in `apim.py`).
- **A platform that cannot answer now** (no connection, a timeout, a 5xx, a 429) is asked again `RETRIES` times,
  `RETRY_PAUSE` seconds apart, and then the seed stops with what it said. Any other refusal (400, 401, 403, 409, 422...)
  stops it at once: replacing an API because the platform is busy, or because the seed sent something wrong, costs it its
  keys (`apim.py`). The Gamma client waits out a management API that reloads; a 404 or `not_configured` is final
  (`gamma.py`).
- **Waiting** is `wait_until`: it looks again after 0.5, 1, 2 and then every 3 seconds up to `READY_TIMEOUT`, and a probe that raises `Gone` ends it at once. Any other wait goes through `pause`, which stops when another step has failed.
- **Parallel** is `schedule.py` for the modules (their `DEPENDS`), `common.parallel` and `common.together` for what a module knows to be independent, `SEED_WORKERS` for how many requests are in flight. A step shared by several threads keeps its state in `common.record`/`common._state`, never in a read-modify-write of its own; see the README (Order and parallelism).
- **A refill** deletes the Redis hashes `ratelimit:*:cost-rl` whose `subscription` field is the member's. Redis trouble is
  a `[refills] !` line and never stops the seed (`refills.py`).

## Tests

Run `make setup`, then `make test`. `make setup` makes `.venv` with `requests`, `pyyaml` and `redis` (Python 3.10 or
later). `make test` runs the Lab UI's tests and the tooling's, then these when those three import; otherwise it prints
"Seeder tests skipped", and a skip is not a pass. The tests run against a fake platform (`tests/__init__.py`) and check
the sample seed files. Run directly (`python -m unittest discover -s seeder/tests -t seeder`), the tests
that hold the fake Redis to the real `redis` library skip when it is missing.

The seed files of a playground are checked where the playground lives: the `seed` job of its catalog's CI runs `seedlint` on them, and
`tests/fixtures/samples/` holds sample seeds, with invented names, that these tests run the engine against. A demo skill under a
playground's `seed/aim/skills/<name>/` is data for the seed, which `aim_catalog.py` zips and uploads to Gamma's catalog. It is not a skill for
this repository's AI tooling, which lives in `.agents/skills/`.
