# Seeder

The seeder is the engine every playground's seed runs. Through the products' management APIs it creates the playground's
data in Gravitee (OpenFGA stores, an Access Management domain, APIs, applications and subscriptions, Gamma objects) and
records what it created so that it can delete exactly that. Read the [root guide](../AGENTS.md) first. How a playground
runs it: [README.md](README.md). The seed files and the settings:
[the seed](../docs/playgrounds.md#the-seed).

## Module contract

A module is one file of `lab_seeder/`, registered in `lab_seeder/seed.py`.

| Name | Role |
|---|---|
| `SECTION` | The prefix of its log lines (`[section]`) and the `product` of its records |
| `ORDER` | The kinds it records, dependents first: the order of a removal |
| `seed()` | Creates its objects. Returns before it reads a setting or connects when its folder of `seed/` is absent (`am.seed(stores)` takes the OpenFGA stores) |
| `remove(records)` | Deletes what `owned(records, SECTION, ORDER)` returns, and `forget`s each record as soon as its object is deleted. What the platform refuses to delete (a user that owns an application) is not forgotten: it stays in the record, and the rest is removed |
| `FOLDERS`, `GAMMA` | For a module that fills Gamma: the folders or files of `seed/` it reads, and the Gamma modules it needs (`aim`, `authz`, `platform`). `seed.py` checks that the platform serves them before it changes anything |
| `PATHS` | Optional and local to a module: the API path of each kind it records. The engine does not read it |

A new module is imported in `lab_seeder/seed.py` and added to `AFTER_AM`, which fixes where it runs; a removal runs the modules in
reverse. Its tests go in `tests/test_<module>.py`, using `Seed` from `lab_seeder/testkit.py`.

## Idempotency and the record

The seed runs when a Start sets the playground up, on Re-seed, and again with any command that names no service
(`make all`, a plain `docker compose up`), so a second run must change nothing.

- Look each object up by name, slug or id, and write it only when it differs.
- Record what you create with `record(SECTION, kind, label, name, id)`. `.state/created.json` is a list of
  `{product, kind, label, name, id}` objects, plus the keys a module adds (an API keeps a `digest`). `label` is the plural
  the Lab UI shows when it asks whether to delete ("3 APIs").
- Make an object inside `with making(SECTION, kind, label, name):`, which notes it before the request goes (no id yet, a
  `count` of 0 that the Lab UI counts as nothing), and record it inside the block. A create whose answer is lost (no answer in
  time, a seed stopped in the middle of it) leaves the note, and where you would stop on an object of that name that you did
  not create, `adopted(SECTION, kind, name, id)` takes it as the seed's when the note names it. A 4xx takes the note back.
  An object you delete to make it again loses its record right after the delete (`forget`): `making` notes nothing under a
  name the record still holds, so that an update keeps its record. A create after a lookup that found nothing passes
  `missing=True`: the record may name an object deleted in a console, and the note goes beside it.
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
  a seed file may use them with no setting, and `seedlint` does not call them unset. A page's own `${...}` is written
  `$${...}`.
- **What the files show is refused before a module's first change**: an environment folder that is no id, is the platform's
  own or whose `environment.yaml` names another id, an API or application that names a group its folder lacks, two
  applications of one name, a key or a token that YAML reads as a number or a boolean, a listing that names an API or a
  location its folder lacks, a second portal, an AsyncAPI document that no listed API owns or that the viewer cannot read, an asset
  that is missing or too large. A group member that `users.yaml` lacks is not refused here (the platform may know the user, and
  `enroll` looks it up): `seedlint` checks it.
- **Portals** (`apim_portal.py`) follow `apim.py` and read `portal/` of `seed/apim/` and of each environment. The module's docstring
  says what goes through which API and why, and [what the platform does](../docs/playgrounds.md#environments-and-developer-portals)
  is in the playground reference.
- **A removal skips an environment that is gone** (`apim.py` and `apim_portal.py` both ask once, and the platform answers 400 to
  every route of one it lacks), with one line for each record.

## The Lab UI contract

- A seed that stops on a `SeedError` prints `[seed] ✗ <why>` as its last line and exits with an error. The Lab UI shows
  `<why>` as the reason the start failed, so keep it to one line (`excerpt()` in `common.py`). Any other error prints its
  traceback, then one plain `[seed] ✗` line, so that the reason is never a line of a traceback.
- `seed.py --remove` deletes the recorded items from the last module to the first, each record dropped as its object goes, so
  that a removal that fails keeps the records of what is still there, and a seed that records meanwhile keeps its own. The Lab
  UI reads the record to say what a delete removes.
- The record is read and written by one process at a time (a lock file beside it), each write whole and synced. A record that
  cannot be read, or is no list of records, stops the seed and is never written over; one that cannot be written stops it too.
- Log with `log(section, message)`, and never a secret, a token or a password.

## Endpoints, not containers

Every address, credential and id comes from the environment (`setting()` in `common.py`), and seed files hold `${VAR}` for
what depends on the target (`render()`). No module and no seed file names a container or writes an address: the
playground's seed service sets the variables. `seedlint`, which the *Seed files* job of a catalog's CI runs, fails on a variable no seed
service sets.

## Behavior to keep

- **Releases are read, builds are asked.** `versions.py` reads the versions at the start of a seed. A module never compares a
  version itself: for a change between releases it asks `versions.since(name)` of a row of `CHANGES` (True, False, or None for
  a build that is no release), and each row is one of [Versions of Gravitee](../docs/playgrounds.md#versions-of-gravitee),
  with a test of both sides. A difference between builds that are no release is handled, in this
  order of preference: the seed file writes what its build takes and the module sends it on; the module adapts to what the
  platform answers (a status, a field, a code, what it serves), never to a sentence meant for a person; when neither can
  work, the seed stops with a line that says what to write instead. Each difference is a row of
  [What differs from one build to another](../docs/playgrounds.md#what-differs-from-one-build-to-another), added with its
  code and removed with it. The code of a row has a test for each case the row names.
- **An import that fails after the platform made the API** ("API imported with error", the API kept, stopped) is recorded
  with no digest before the error is raised, so that the next run finds it as its own and updates it. The same for the
  import that follows a replacement.
- **A seed file is read as a document** (`render_json` and `render_yaml` in `common.py`): a variable is replaced in each string
  of it, its keys too, and a string that is itself JSON text gets the value escaped for that text. A plain YAML scalar that holds
  a variable is then read as YAML reads its value (`port: ${PORT}` is a number, `port: !!str ${PORT}` a string), but a value
  never becomes a mapping, a list or a merge key. Keep a variable of a file inside a string, so that a value with a quote or a backslash cannot break the file; a
  file that is no document until its variables are replaced is replaced as text. A file that is no YAML says its line and
  never its text, where a password may be.
- **A renamed API is one record**: an update drops the record that held the id under its old name.
- **LLM, MCP and A2A proxy APIs** cannot be updated from a definition, so they are replaced. A control's steps are
  switched in place, with an update of the API entity and a deployment. The API is imported again only when the platform
  answers 404, 405 or 501 to that update (`UNSUPPORTED` in `apim.py`).
- **One policy for every request** (`Api.call` in `common.py`). A platform that cannot answer now (no connection, no answer
  in time, a 5xx, a 429) is asked again `RETRIES` times, `RETRY_PAUSE` seconds apart, when a second request cannot make a
  second object: a GET, a PUT or a DELETE, a POST its caller marks `idempotent=True` (a deployment, a start), and any request
  the platform did not take (no connection made, a 429, a 503). A POST or a PATCH whose answer is lost is sent once: Gamma
  keeps no name unique, and a second create would make a second object. Any other refusal (400, 401, 403, 409, 422...) stops
  the seed at once: replacing an API because the platform is busy, or because the seed sent something wrong, costs it its
  keys (`apim.py`). What the seed says of a request with no answer is in words (`trouble`), never the library's text. A
  module names a request with `what=` and its client with its section; it adds no retry of its own.
- **Waiting** is `wait_until`: it looks again after 0.5, 1, 2 and then every 3 seconds up to `READY_TIMEOUT`, and a probe that
  raises `Gone` ends it at once. Inside a wait a request is sent once: the wait asks again. The first wait for a platform passes
  `final=REFUSED`, so a wrong password, a revoked token or a wrong environment id ends it at once. Gamma's modules: a
  `not_configured` is final, and a 404 once it lasts `UNSERVED` seconds (`gamma.py`). Any other wait goes through `pause`,
  which stops when another step has failed.
- **Parallel** is `common.parallel` and `common.together` for what a module knows to be independent, and `SEED_WORKERS` for how
  many requests are in flight; the modules themselves run one after the other. A step shared by several threads keeps its state
  in `common.record`, never in a read-modify-write of its own; see the README (Order and parallelism).
- **A refill** deletes the Redis hashes `ratelimit:*:cost-rl` whose `subscription` field is the member's. Redis trouble is
  a `[refills] !` line and never stops the seed (`refills.py`).

## Tests

Run `make setup`, then `make test`. `make setup` makes `.venv` with `requests`, `pyyaml` and `redis` (Python 3.10 or
later). `make test` runs the Lab UI's tests and the tooling's, then these when those three import; otherwise it prints
"Seeder tests skipped", and a skip is not a pass. The tests run against a fake platform (`lab_seeder/testkit.py`) and check
the sample seed files. Run directly (`python -m unittest discover -s seeder/tests -t seeder`), the tests
that hold the fake Redis to the real `redis` library skip when it is missing.

The seed files of a playground are checked where the playground lives: the *Seed files* job of its catalog's CI runs `seedlint` on them, and
`tests/fixtures/samples/` holds sample seeds, with invented names, that these tests run the engine against. A demo skill under a
playground's `seed/aim/skills/<name>/` is data for the seed, which `aim_catalog.py` zips and uploads to Gamma's catalog. It is not a skill for
this repository's AI tooling, which lives in `.agents/skills/`.
