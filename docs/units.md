# The format of add-ons, setups and playgrounds

The rules a unit follows, whatever catalog it lives in: its files, its keys, its labels and what the Lab UI reads. A unit is
an add-on (`addons/<name>/compose.yml`), a setup (`setups/<name>.yml`) or a playground (`playgrounds/<name>/compose.yml`).
Its folder, or a setup's file, is its `x-gravitee.name` in lower case with every run of characters other than `a-z` and `0-9`
one dash (*Acme Console* is `addons/acme-console/`): that is its id ([Writing a catalog](writing-a-catalog.md#the-structure)).
What the Lab promises to a unit is in [the contract](contract.md); how a catalog is made is in
[Writing a catalog](writing-a-catalog.md).

Here `make check` is the check of a catalog: run it in the catalog's repository, or `python3 lab-ui/catalog.py check <catalog id or folder>`
from the Lab. The engine's own `make check` reads the core.


## Add-ons

An add-on is one capability on top of the core: a folder `addons/<id>/` with its `compose.yml`. Compose includes it
from its folder, so its paths start there and it reads the `.env` of its folder besides the core's. Read the
[root guide](../AGENTS.md) first: it holds the rules every file follows, and what personal and shared mean.

```
addons/<id>/
  compose.yml     the add-on
  README.md       what it adds and how to use it (required once shared: `make check`)
  .env            its own secrets and settings (git-ignored, written by the Lab UI)
  certs/, ...     files it mounts, with paths relative to the folder
  secrets/        credential files (git-ignored)
```

Depth: a personal add-on, in `.lab/catalogs/local/addons/<id>/`, and an add-on of a catalog sit at the same depth, so the same
paths work in both. An add-on of the same catalog is `../<id>/compose.yml`; one of another catalog, such as the default one, is
`../../../<catalog id>/addons/<id>/compose.yml`. An item of a catalog includes only its own catalog's and the catalogs it
declares in `requires.catalogs`.

### compose.yml

```yaml
x-gravitee:
  kind: addon
  name: Acme Console                      # how the Lab UI names it
  category: Other                         # one of x-gravitee.categories in docker-compose.yml
  description: Browser for what the gateway sends to Acme   # a noun phrase: what it adds
  conflicts:                              # optional: the add-ons it cannot run with, and why
    acme-tracing: both take the gateway's OpenTelemetry exporter
  requires:
    license: [apim-reporter-tcp]          # optional: license features it needs

include:
  - ../acme-broker/compose.yml            # optional: the add-ons it needs, Compose brings each once

services:

  acme-console:
    image: acme/console:${ACME_CONSOLE_VERSION:-1.4.2}
    container_name: gio-acme-console
    restart: unless-stopped
    ports:
      - "127.0.0.1:8095:8080"             # Web UI
    environment:
      ACME_API_KEY: ${ACME_CONSOLE_API_KEY}   # Acme > Settings > API keys
      ACME_ADMIN_PASSWORD: ${ACME_CONSOLE_ADMIN_PASSWORD:-admin}   # Password of the console's admin user
      ACME_REGION: ${ACME_CONSOLE_REGION:-}   # Acme region, when not the default one
    labels:
      io.gravitee.name: Acme Console
      io.gravitee.description: Shows what the gateway sends to Acme
      io.gravitee.console.acme-console.name: Acme Console
      io.gravitee.console.acme-console.url: http://localhost:8095
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/health"]
      interval: 5s
      retries: 20
    networks:
      - internal

  apim-gateway:                           # a core service: only the keys the add-on needs
    environment:
      gravitee_reporters_acme_enabled: "true"
```

- **Image**: a default version in the file, `${NAME_VERSION:-tag}`, and nothing in code: the Images page finds the
  component from the `image:` line and writes another version to the add-on's `.env`. Services that share a version
  variable are one component. A Gravitee image whose registry can change is
  `${NAME_REGISTRY:-graviteeio}/repository:${NAME_VERSION:-tag}`. A version follows another component when its default
  is that component's variable: `${KIOSK_VERSION:-${ELASTICSEARCH_VERSION:-9.5.2}}`. That variable is the core's or
  the unit's own: another unit's `.env` does not reach this file.
- **An image built here** (its vendor publishes none) is
  `<images>/<unit>-<service>:latest`, where `<images>` is the `images` prefix of the catalog's `catalog.yml`
  (`ghcr.io/<owner>/<repository>`), with `pull_policy: ${<ID>_PULL_POLICY:-build}` and the comment
  `build: build it here at each start; never: build it here once; always: pull it, build it here if it cannot be
  pulled`. The unit's name is not repeated when the service already starts with it, and `catalog.py check` says the name
  it expects. The catalog's CI builds and publishes every such image under that one prefix, so a new add-on
  adds nothing to it, and `always` pulls what CI published. A remote build context pins a commit. See
  [built here, or pulled](configuration.md#built-here-or-pulled).
- **Secrets and settings**: each is described by a comment on its line or heading its block; the settings form shows
  it, and puts a name with `TOKEN`, `SECRET`, `PASSWORD`, `LICENSE` or `_KEY` under Secrets
  ([settings and secrets](configuration.md#settings-and-secrets)). Their names follow
  [the naming rule](../AGENTS.md#rules). `${VAR}` with no default is required: the add-on's Start waits and says which
  until its `.env` gives it a value, and its key button opens them. An optional one carries an empty default, `${VAR:-}`. The core's
  `GRAVITEE_LICENSE`, `INTERNAL_API_PASSWORD` and `REDIS_PASSWORD` come from the core's `.env`: reference them with the
  core's defaults and never copy their values. A setting is never written into a `CMD-SHELL` test or a `command` written
  as one line, where a value with a space, an `&`, a `$` or a quote is split, run or expanded: a check reads the
  container's own variable as `"$${VAR}"`, in double quotes, a command is a list, and `redis-cli` takes its password
  from `REDISCLI_AUTH`.
- **No hardcoded password**: a password, secret, token or key is a setting, `${NAME:-default}` with a public demo value
  as the default, or `${NAME}` when the person must provide it, never a literal, and `make check` refuses one in a
  shared add-on ([the rule](../AGENTS.md#rules)). That includes a password inside a URI, as in
  `postgres://acme:${ACME_DB_PASSWORD:-acme}@acme-db:5432/acme`, and a service that
  shares a password with another reads the one setting. A script that puts a secret in a file reads the setting too,
  from the environment or the add-on's `.env` (a `generate.sh` in its folder). The README says when a new value
  needs more than a restart: a database keeps the password it was created with, and a keystore keeps its own.
- **Host access**: a service that takes more of the machine than its container does (privileged, the host's network, the
  Docker socket, a folder outside the project) says why in `x-gravitee.host_access`, a service name to the reason, and
  `make check` refuses one that does not ([the rule](playgrounds.md#host-access)).
- **Category**: one of `x-gravitee.categories`. "Gravitee Platform" is for a Gravitee product that runs next to the
  core (a federation agent, a gateway that runs a remote platform's), not for the core itself.
- **Conflicts**: add-on id to reason, in a few words. One side declares it and it holds both ways; `make check` and the
  Lab UI show the reason. Two add-ons that set the same key on a core service conflict: declare it.
- **License features** in `requires.license` are ids of the license model, not names of packs. The Lab UI says so on
  the row and keeps Start grayed while the license lacks one: the add-on would not work.
- **A gateway of a platform**: an add-on that runs, on this machine, the gateway that a remote platform manages (the
  Hybrid gateway for Gravitee Cloud) says so with `gateway`, and the Lab UI finds it by this key alone:

  ```yaml
  x-gravitee:
    gateway:
      platform: cloud                  # the kind of platform it serves (a key of KINDS in platforms.py)
      service: apim-hybrid-gateway     # its service, which publishes a port
      token: HYBRID_GATEWAY_CLOUD_TOKEN  # the required setting that takes the gateway's token, written by the Gravitee Cloud page
  ```

  The domains of the gateway are derived, never listed: `localhost:<first published port>` for a browser and
  `<service>:<container port>` for a container of the lab. Only one add-on says it for a kind. A deploy to a platform
  brings this add-on without the core, so it runs on its own services (the Hybrid gateway has its own Redis), changes no
  core service and depends on none: `make check` refuses either, in it and in the add-ons it includes.
- **Labels**, on each service:

  | Label | What it does |
  |---|---|
  | `io.gravitee.name` | The display name |
  | `io.gravitee.description` | What the service is, in one short line, shown with its name. Every service of a shared add-on has one (`make check`) |
  | `io.gravitee.console.<id>.name` / `.url` | A link to open, once per UI. `.legacy: "true"` lists it after the others, which are listed by name. `.username` and `.password` give its demo sign-in, shown next to it: a password that is a setting is written as that setting. A playground shows a service's first one in a tab when it lists the service in its `consoles` |
  | `io.gravitee.image.tags` | The Docker Hub repository that lists the image's tags, when its own registry lists them badly |
  | `io.gravitee.role: seed` | In a playground only: the service that creates its data in Gravitee |
  | `io.gravitee.license: required` | A service that exits without a usable license. The Lab UI does not start it then and shows **Needs a license** on its row |

- **Services**: `container_name: gio-<name>`, the `internal` network (`edge` too for a gateway that API consumers
  call) and a healthcheck when the image allows one. `restart: unless-stopped` for what keeps running: it starts again
  after a crash, and a container stopped from the Containers page stays stopped when Docker restarts. `make check` refuses
  `restart: always` in a shared file. A service with no `restart` (or `"no"`) and no published port runs once, as a
  migration does, and shows as Done when it exits with 0.
- **Ports**: a host port that neither the core nor anything that can run with the add-on publishes (`make check`). In a
  shared add-on every port binds `127.0.0.1`, and a comment says what it is.
- **Core services**: change only the keys the add-on needs. The Lab UI restarts a core service the add-on configures
  when the add-on is turned on or off, and says so first.
- **Data**: a named volume `data-<id>` keeps it across restarts and switches.
- **Catalog**: add a row to README.md, written from the `x-gravitee` block as `make check` prints it:
  `| <name> | <description> |`.
- **README**: `addons/<id>/README.md` when a person needs more than the settings say: what it adds and where to open
  it, what it needs, a table of its settings and what stays after removal.

Then run `make check`, start the add-on from the Lab UI's Add-ons page ([what Start does](lab-ui.md#start-and-stop-an-item))
and look at it on the Containers and Images pages. To share it, see
[Personal and shared](../AGENTS.md#personal-and-shared).


## Setups

A setup is a named set of add-ons: a compose file that includes them and nothing else. It is a shortcut, so it has no
service, volume or setting of its own. Read the [root guide](../AGENTS.md) first: it holds the rules every file
follows, and what personal and shared mean. What a setup's Start and Stop do is in
[the Lab UI documentation](lab-ui.md#start-and-stop-an-item).

The file is `setups/<id>.yml`. Depth: a personal setup, in `.lab/catalogs/local/setups/<id>.yml`, and a setup of a catalog sit at
the same depth. An add-on of the same catalog is `../addons/<id>/compose.yml`; one of another catalog is
`../../<catalog id>/addons/<id>/compose.yml`.

```yaml
x-gravitee:
  kind: setup
  name: Acme Streams                     # short: it is the name of its row
  category: Event                           # one of x-gravitee.categories in docker-compose.yml; required in a catalog
  # A noun phrase: it is the text of its row in the catalog
  description: A broker with its console and the gateway's broker support

include:
  - ../addons/acme-broker/compose.yml        # what an add-on needs comes with it
```

- **Add-ons only**, each once. Two setups may include the same add-on: Compose brings it once. A playground may
  include a setup as it includes an add-on.
- **No conflicts inside**: a setup cannot include add-ons that conflict with each other (`make check` says which).
- **Name** a setup after what it demonstrates, not after what it contains.
- **Catalog**: add a row to README.md, written from the `x-gravitee` block as `make check` prints it:
  `| <name> | <description> |`.

Then run `make check`. To share a personal setup, see [Personal and shared](../AGENTS.md#personal-and-shared); it
needs a `category` first.


## Playgrounds

A playground is a use case started in one click: the add-ons it needs, its own apps, sample data in Gravitee and a
guide. Read the [root guide](../AGENTS.md) first: it holds the rules every file follows, and what personal and shared
mean. [docs/playgrounds.md](playgrounds.md) is the reference for people writing one, and this file is the
checklist. The playgrounds of the default catalog are examples.

What the folder holds: [the folder](playgrounds.md#the-folder). A playground in a catalog has a
`README.md` for what a user needs before and after: what it teaches, what it needs, how to start it, its accounts and
URLs, what stays after a removal.

The folder name is its id. Its settings, the ones read from the `.env` of its folder and the shell, follow
[the naming rule](../AGENTS.md#rules): `ACME_SHOP_PULL_POLICY` and `ACME_SHOP_PROTECTIONS` start with its id, and
`OLLAMA_URL` keeps the common name of the host service. Depth: a personal playground, in
`.lab/catalogs/local/playgrounds/<id>/`, and a playground of a catalog sit at the same depth. It includes an add-on of its
catalog as `../../addons/<id>/compose.yml`, a setup as `../../setups/<id>.yml`, and one of another catalog as
`../../../<catalog id>/addons/<id>/compose.yml`; its seed builds `../../../../../seeder`.

### compose.yml

Acme Shop is a fictional playground that shows each part.

```yaml
x-shop-secrets:   # each secret is defined once, here, before x-gravitee: an alias comes after its anchor
  customer-password: &customer-password ${ACME_SHOP_CUSTOMER_PASSWORD:-HelloWorld@123}   # Password of Dana
  customer-key: &customer-key ${ACME_SHOP_CUSTOMER_KEY:-acme-shop-key}                   # API key of the shop's customers
  apim-password: &apim-password ${ACME_SHOP_APIM_PASSWORD:-admin}                         # Password of user admin in API Management

x-gravitee:
  kind: playground
  name: Acme Shop
  category: API                             # one of x-gravitee.categories in docker-compose.yml; required when shared
  description: Protection of a shop's REST API and AI assistant with Gravitee, one lesson at a time   # a noun phrase
  tags: [API, AI Agents]                    # required when shared: at least one
  source: https://github.com/acme/shop      # optional: the repository its apps are built from
  duration: 20 min                          # to go through the guide, as 20 min or 1 h 30 min; required when shared
  first_start: 5 min                        # its first start, said next to Start until it is set up; required when shared
  requires:                                 # images, plugins, license and host: nothing else
    images:                                 # the versions it needs at least, by the variable the Images page sets
      APIM_VERSION: "4.12"
    plugins:
      apim: [gravitee-policy-interrupt]     # installed at start from download.gravitee.io
    license:                                # license feature ids, as a list or each with the lesson that needs it
      apim-reporter-tcp: Lesson 4
    host:                                   # checked from this machine before Start, with the fix
      - name: Ollama with qwen3:0.6b
        check: http://localhost:11434/api/tags
        expect: qwen3:0.6b                  # text the answer must hold
        help: Install Ollama, then run `ollama pull qwen3:0.6b`
  consoles: [shop-api, gamma-ui]            # its UIs next to the guide, a tab each, in this order
  accounts:                                 # shown on its page with copy buttons, with the settings as they are now
    - for: "Acme Shop: Dana (customer)"
      username: dana@acme-shop.test
      password: *customer-password
    - for: "Acme Shop API: customer key"
      key: *customer-key

include:
  - ../../addons/acme-console/compose.yml  # the add-ons and setups it needs
  - ../../setups/acme-streams.yml

services:

  shop-api:                                 # an app, built from its own folder
    image: ghcr.io/acme/hub/acme-shop-shop-api:latest
    build: ./apps/shop-api
    pull_policy: ${ACME_SHOP_PULL_POLICY:-build}   # build: build it here at each start; always: pull it, build it here if it cannot be pulled
    container_name: gio-shop-api
    restart: unless-stopped
    ports:
      - "127.0.0.1:8120:8000"               # The shop's REST API
    environment:
      ALLOWED_HOSTS: shop-api,localhost,127.0.0.1,[::1]   # The host names it answers to
    labels:
      io.gravitee.name: Acme Shop API
      io.gravitee.description: The shop's REST API, which the gateway protects
      io.gravitee.console.shop-api.name: Acme Shop        # what its tab and its Open are called
      io.gravitee.console.shop-api.url: http://localhost:8120
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 5s
      retries: 20
    networks:
      - internal

  shop-seed:                                # the seed: runs once, then exits
    image: ghcr.io/gravitee-io-labs/gravitee-lab/seeder:latest
    build: ../../../../../seeder
    pull_policy: ${ACME_SHOP_PULL_POLICY:-build}
    container_name: gio-shop-seed
    restart: "no"
    depends_on:
      apim-management-api:
        condition: service_healthy
    volumes:
      - ./seed:/seed:ro
      - ./.state:/state                     # What the seed created, to remove it later
    environment:                            # endpoints and credentials: seed files use ${VAR}, never a container name
      APIM_MANAGEMENT_URL: http://apim-management-api:8083
      APIM_USERNAME: admin
      APIM_PASSWORD: *apim-password
    labels:
      io.gravitee.name: Playground seed
      io.gravitee.description: Sets up the shop's APIs and applications on the platform
      io.gravitee.role: seed                # how the Lab UI knows the seed from the apps
    networks:
      - internal
```

What the reference says of each part: [`include`](playgrounds.md#include), [`requires`](playgrounds.md#requires),
[accounts and secrets](playgrounds.md#accounts-and-secrets), [image names and pull policy](playgrounds.md#apps),
[`consoles`](playgrounds.md#consoles), [`terminal`](playgrounds.md#terminal) and
[deploying to Gravitee Cloud](playgrounds.md#deploying-to-gravitee-cloud), which takes no declaration.

### Apps

An app is built from its folder (`build: ./apps/<app>`) or from a repository (`build: https://github.com/...git#<commit>:path`,
the commit pinned). It follows the rules of an add-on's services: [addons/AGENTS.md](units.md#add-ons).
The apps of a playground follow these conventions:

- **Folder**: `Dockerfile`, `.dockerignore`, `requirements.txt`, `README.md`, `server.py` as the entry point, and
  `tests/`. Modules are named for what they do (`settings.py`, `security.py`), without the playground's prefix.
- **`requirements.txt`**: every package the app imports directly, pinned exactly with `==`. A package that only the
  tests use says so in a comment.
- **Dockerfile**: the same for every app. Add a `COPY` for any other file the app serves.

  ```dockerfile
  FROM python:3.15-slim
  ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install -r requirements.txt
  COPY *.py ./
  # The tests ship in the image and run there with the app's own dependencies.
  COPY tests ./tests
  CMD ["python", "server.py"]
  ```

- **Server**: `GET /health` answers `{"status": "ok"}`, and the healthcheck calls it. `HTTP_PORT` (default `8000`) is
  the port it listens on. An app with a published port answers only to the host names in `ALLOWED_HOSTS` (421
  otherwise) and refuses what a web page on another site could send: a POST that is not JSON (415), and, for a web
  app, a request that does not come from its own page (403). `/health` is exempt. An app that serves only public,
  read-only data may skip this and says so in its README (`shop-api`).
- **Logs**: one line per step, and never a secret, a token or a request's arguments.
- **Tests**: in `tests/` (a package), run in the image with `python -m unittest discover -s tests -t .`. `make ci` of a catalog
  builds each image and runs them with no network, and runs the JavaScript tests of a page (`tests/js/*.test.mjs`) with
  Node. Tests are named after what they test: a module (`test_settings.py` for `settings.py`) or a feature
  (`test_delegation.py`).
- **README**: `# <app>` with the sections `## What it is`, `## Endpoints` (or `## Tools`, `## API`: what it offers),
  `## Environment` (a table: variable, meaning, default), `## Run` and `## Tests`, and more for what is specific to the
  app.
- **Copied helpers**: an app is its own image and shares no code with another, so a small helper that two apps need is
  copied. When copies must stay equal, a test in each app pins them (Acme Shop's rate and branch tables in `shop-api`
  and `shop-mcp` share one digest).

A running playground shows its apps in frames of the Lab UI, and an app may be driven from the guide. What an app sends
and listens for: [what an app does when it runs in the Lab UI](playgrounds.md#what-an-app-does-when-it-runs-in-the-lab-ui).

### The seed and the guide

Each part has its section in the reference: [the seed](playgrounds.md#the-seed), [its files](playgrounds.md#files),
[environments of its own](playgrounds.md#environments-of-its-own), [what it records](playgrounds.md#what-it-records),
[controls](playgrounds.md#controls-the-protections-a-lesson-switches-on),
[refills](playgrounds.md#refills-what-a-lesson-spends-put-back) and [the guide](playgrounds.md#the-guide). Running a seed twice
must be safe: [seeder/AGENTS.md](../seeder/AGENTS.md).

**A one-shot service writes to the core's database only when no API can.** No management API creates an environment, so the
playground has a service with `restart: "no"` and no port that writes each one into the core's MongoDB from the seed's own
files, mounted read only, and the seed waits for it with `depends_on: {condition: service_completed_successfully}`. It reads each
`seed/environments/<id>/environment.yaml`, and passes the values through its environment, never the command line. Anything an
API can do, the seed does. The Lab UI cannot see what such a service changes, so the playground's README says it.

### Catalog and Lab UI

Add a row to README.md, written from the `x-gravitee` block as `make check` prints it:
`| <name> | <description> | <duration> |`.

Then run `make check` and start the playground from its page. What a playground's page shows, and what its Start,
Re-seed, Stop and Reset do: [the playground page](lab-ui.md#the-playground-page). To share a personal one, see [Personal and shared](../AGENTS.md#personal-and-shared).
