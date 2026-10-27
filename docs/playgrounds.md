# Writing a playground

A playground is a use case you start in one click and explore with a guide. The platform is configured for it, its
demo apps run, its APIs, applications and users exist, and the guide tells you what to try.

This page is the reference for building one. [The format of the units](units.md#playgrounds) is the checklist an AI
agent follows, and the `new-playground` skill walks through it. What a person sees when one runs is in
[The playground page](lab-ui.md#the-playground-page). The examples below are excerpts of a fictional playground, Acme Shop, as in [the format of add-ons, setups and playgrounds](units.md); the playgrounds of
the default catalog follow the same rules.

## Layers

| Layer | What it is | Lives in | Example |
|---|---|---|---|
| Platform | APIM, AM, Alert Engine and their databases. Always the base | `docker-compose.yml` | Management API, gateway, MongoDB |
| Add-ons | A reusable capability, useful on its own and in any use case | `addons/<id>/compose.yml` of a catalog | A broker, a tracing backend, a mail catcher, an authorization service |
| Playgrounds | A use case: what is specific to one story | `playgrounds/<id>/` | Acme Shop |

The test for add-on or playground: would someone turn it on without this use case? A broker, a tracing backend or a live
view of agent traffic: yes, those are add-ons. The bank's apps, its APIs and users, and its guide: no, they belong to
the playground.

A playground declares what it needs from the layers below. It never redefines them.

## The folder

```text
playgrounds/<id>/
  compose.yml     metadata (x-gravitee), the add-ons and setups it includes, its apps, its seed, changes on core services
  README.md       what it teaches, what it needs, how to start it, its accounts and URLs, its settings
  apps/<app>/     its apps, one Dockerfile each, when it ships them
  guide.md        the lessons, shown next to the running playground
  seed/           what to create in Gravitee, in each product's own format: apim/, environments/<id>/, am/, openfga/,
                  aim/, authz/, controls.yaml, refills.yaml
  .state/         what the seed created, on this machine and in each Gravitee Cloud environment it deployed to (git-ignored)
```

The folder name is its id: lowercase letters, digits and dashes. Prefix the playground's own settings with it
(`ACME_SHOP_*`): [Settings and secrets](configuration.md#settings-and-secrets) has the rule. `seeder/` is the shared
seeding engine, not a playground. See [`seeder/README.md`](../seeder/README.md).

## compose.yml

A playground is standard Compose. Its metadata sits in the top-level `x-gravitee` extension, which Compose keeps, so
the Lab UI reads it with `docker compose config` like everything else. Add-ons and setups describe themselves in the
same block.

```yaml
x-shop-secrets:   # each secret is defined once, here, before x-gravitee: an alias comes after its anchor
  demo-password: &demo-password ${ACME_SHOP_DEMO_PASSWORD:-HelloWorld@123}   # Password of Alice, Bob and Tina
  dev-llm-key: &dev-llm-key ${ACME_SHOP_DEV_LLM_API_KEY:-acme-dev-llm-key}   # API key of the Developers plan on the LLM proxy
  # ...

x-gravitee:
  kind: playground
  name: Acme Shop
  category: AI
  description: Protection of an AI agent in production with Gravitee Gamma, one lesson at a time
  tags: [Gamma, AI Agents, LLM Proxy, MCP, Guard Rails, Authorization, Budgets, Tracing]
  duration: 35 min
  first_start: 10 min
  requires:
    images:
      APIM_VERSION: "4.13.0-alpha.5"
      AM_VERSION: "4.13.0-alpha.9"
    license:
      - gamma-aim-module
      - apim-mcp-proxy-reactor              # the MCP proxy, and its mcp-acl policy
      # ...
    host:
      - name: Ollama with qwen3:1.7b
        check: http://localhost:11434/api/tags
        expect: qwen3:1.7b
        help: Install Ollama (ollama.com/download), then run `ollama pull qwen3:1.7b`; the containers must be able to reach it (Troubleshooting, Ollama)
      # ...
  consoles: [shop-web, gamma-ui]
  accounts:
    - for: "Acme Shop: Alice (customer)"
      username: alice@acme-shop.test
      password: *demo-password
    # ...
    - for: "Acme Shop LLM proxy: Developers key"
      key: *dev-llm-key
```

`make check` rejects a key outside this list, because nothing would read it.

| Key | What it says |
|---|---|
| `kind` | `playground` |
| `name` | How the Lab UI names it |
| `description` | A phrase with no leading article and no final period: what it shows. Required. Its row in the `playgrounds/README.md` of the catalog repeats it exactly |
| `category` | One of `x-gravitee.categories` in `docker-compose.yml`. Required for what is shared |
| `tags` | Short labels, shown on its page. Required for what is shared: at least one, none twice |
| `duration` | How long the guide takes, as `35 min` or `1 h 30 min`. Required for what is shared |
| `first_start` | How long the first start takes, said next to Start until it is set up, written like `duration`. Required for what is shared |
| `source` | The repository its apps are built from, shown as a *Source* link |
| `requires` | What it needs: `images`, `plugins`, `license` and `host`, below |
| `tested` | The versions it was built and checked on: a list of sets, the latest last, each `{VERSION_VARIABLE: "version"}`, the version in quotes. See [Tested versions](#tested-versions) |
| `consoles` | Its UIs next to the guide, below |
| `terminal` | The service whose container holds the shell under its UIs, where the guide's commands run: one of its own, or of an add-on it brings, not its seed. See [`terminal`](#terminal) |
| `accounts` | The sign-ins its guide uses, shown with copy buttons in *Details* and where the guide links to one ([accounts in a guide](#accounts-in-a-guide)): `for`, then `username` and `password`, or `key`. A `password` and a `key` are settings, never literals: [accounts and secrets](#accounts-and-secrets) |
| `host_access` | A service that takes more of this machine than its container does, mapped to why: see [Host access](#host-access) |
| `conflicts` | Read for add-ons only, which map each add-on they cannot run with to the reason |
| `gateway` | Read for add-ons only: the gateway of a remote Gravitee, Gravitee Cloud today, that the add-on runs on this machine. See [Deploying to Gravitee Cloud](#deploying-to-gravitee-cloud) |

### `requires`

| Key | Shape | What the Lab UI does |
|---|---|---|
| `images` | `{VERSION_VARIABLE: "minimum"}`, the version in quotes | Warns under *Before you start*, with a link to the Images page, and blocks Start while the saved version is older. Start names every image that is too old in one sentence. A pre-release comes before its release, as in semantic versioning: `4.13.0-alpha.5` is not a `4.13`, so a minimum in release form (`4.13`, `4.13.0`) is met by a release only, or by a version the playground lists in `tested`. Ask for a pre-release (`4.13.0-alpha.3`) to accept it and the later ones. A version that is not a release (`major.minor.patch`: `4.13.0-alpha.5`, `amp-demo-latest`, `latest`) is never taken as safe, see [Tested versions](#tested-versions) |
| `plugins` | `{apim: [plugin id]}`, or `am` | Checks the plugins, and installs the missing ones from download.gravitee.io at Start |
| `license` | A list of license feature ids, or `{feature id: "Lesson 4"}` | Warns when the license lacks one: `Needs a license`, `License expired` or `Not in the license`. With the mapping, the row says `License needed for lesson 4` instead, and its tooltip names the packs that give the features |
| `host` | A list of `name`, `check`, `expect` and `help` | Calls `check` from your machine before Start. `expect` is text that must appear in the answer. `help` says what to do |

- The keys of `images` are version variables of the Images page. `make check` says when one is not.
- A license feature is an id of the license model, not the name of a pack. `make check` says when it is not.
- A `check` is an http or https URL, and the Lab UI opens nothing else. It runs from your machine, because the browser
  cannot reach it.
- Add-ons and setups are included, never listed in `requires`: a setup stands for the add-ons it includes.

### Host access

A playground runs containers with your Docker access. A service that takes more of your machine than its own container does must say why, so that a reviewer and a user read it before it runs. `make check` asks for it, for a shared file:

```yaml
x-gravitee:
  host_access:
    k3s: Runs containerd and the kubelet inside its container, which only a privileged container can do
```

What counts: a privileged container, the network, the processes, the shared memory, the host name or the users of the host, added capabilities, devices, a security profile that is `unconfined`, the Docker socket, and a folder of your machine outside the project. A folder of the project, a named volume and a port do not. What a file includes is the included file's to say. A declaration for a service that takes nothing is an error too, so that the list stays true.

### Tested versions

```yaml
x-gravitee:
  tested:                                  # the versions it was built and checked on, the latest set last
    - APIM_VERSION: "4.13.0-alpha.3"      # older sets stay tested: add a set, do not replace one
    - APIM_VERSION: "4.13.0-alpha.5"
      APIM_REGISTRY: graviteeio            # a tag is in one registry: a set may name it, and a switch goes to it
      AM_VERSION: "4.13.0-alpha.9"
```

A playground says the versions it was built and checked on, not the ones it asks for at least (`requires.images`). A version it lists
there is also enough for its minimum. Only a release (`4.13.7`) is taken as it is: any other version (a pre-release such as
`4.13.0-alpha.5`, a demo build, `latest`) is not tested unless the playground names that very version, in `tested` or as the
`requires.images` it asks for. Without a tested version to go to, the warning says to choose a release and Start offers only
*Start anyway*. Gravitee changes things between versions, whatever its numbers say, and a playground that is not run on its versions may end on its
seed after the whole setup. Each item is a set: what was checked together. The saved images are tested when they match
every image of one set:

- A version it was tested with matches itself, whatever the image is built on (`-debian`).
- A release matches the releases of its minor version: tested on `4.13.0`, `4.13.7` is fine and `4.14.0` is not.
- A preview (`4.13.0-alpha.3`), a demo build (`4.13.0-amp-demo-SNAPSHOT`) and a tag without numbers (`latest`,
  `amp-demo-latest`, `master-latest`) match only themselves: they say nothing of what comes after them, and a moving one
  nothing of what it holds today.

What started well on this machine counts too. Once a start ended well, seed included, the Lab UI keeps the versions of the
images the playground is judged on, and the image each one was, in `.lab/tested.json` (not shared, 20 per playground): starting it
again on them is not a warning. A tag that is not a release (a demo build, `latest`) is kept with its image, since it is rebuilt under
the same name: once it moved, it is not tested any more. These are what worked here, not what its authors checked: to declare
them, add a set to `tested` in its file, or share the playground, which writes the latest of them into its copy when the file declares none.
A playground that judges no image keeps its start all the same, as a run with no version: Share asks for one. A playground that declares no set and asks no minimum of an image is not judged. One that asks a minimum is judged on that image: a version that is not a release, and that it does not name, is not tested, unless it started well here.

Its Start then offers the start that goes to the versions it was tested with, as when the images saved are older than it asks: the
warning beside Start shows each image as it is (*Current*) and as it was *Tested*, and the dialog has the same steps, *Changes versions*
(or *Goes back*, in the danger tone, when a version would go back to an older one: the data a newer version wrote, Mongo or an application
type, may not open on it), then what restarts. *Switch and start* saves the images of the latest set that differ and starts; *Start
without switching* starts on the images as they are. *Before you start* says it on the line of each image (*Can go to … at start*). An image that neither a set nor `requires.images` names is not judged. Add a set when the playground is checked on other versions, and leave the
older ones: they stay tested. `make check` says when a name is not the version variable of an image.

### `include`

```yaml
include:
  - ../../addons/jade/compose.yml                 # Jade
```

A playground includes the add-ons and setups it needs. Starting it turns them on, and stops or turns off what conflicts
with them, after saying so. A playground can include add-ons and setups only. A playground of a catalog never includes a
personal one.

### `terminal`

`terminal: logistics-kubectl` names the service whose container gets a shell, under the UIs, in the page of the playground. The
Lab UI starts `sh` there with `docker exec -it`, so everything the shell has comes from the container: its image, its
working directory (`working_dir`), its environment, its mounts. A container that only waits for the shell (`entrypoint: ["sleep",
"infinity"]`) is how Acme Freight has one, with its lessons' files mounted and `ENV` naming a file of aliases that `sh` reads
at its start (`k` for `kubectl`). The terminal is for what the playground teaches to do from a command line; a shell on this
machine is not what it opens.

`make check` proves that the service is one the playground runs and is not its seed. Whether the container runs is the Lab UI's
to say: the panel and the guide's *Run* buttons say why they cannot be used.

### `consoles`

`consoles` is the one key that names the UIs of a playground: the services of its own apps, of the add-ons it brings and
of the platform.

- Each service is shown by its first console, from the `io.gravitee.console.<id>.name` and `.url` labels of the
  service. The current consoles come before the ones labeled `legacy`, then they go by name.
- Each is a tab, in this order, then *Details*, which is always last. The first is where the guide sends the person,
  and what *Open* in the page head opens.
- A console is a service the playground runs, listed once, that can be shown in a frame. `make check` proves what a
  file can say: the service is there with a console, and a platform one has the command that lets the Lab UI frame it
  (`x-ui-frames` in `docker-compose.yml`). An app answers for its own headers, which only the page's look at it sees:
  the tab then says `cannot be shown here`.
- A UI that cannot be framed is not listed. Access Management's console is one: it stays under *Details*, with all the
  platform's consoles.
- A playground that lists none has its guide beside *Details* alone.

### Deploying to Gravitee Cloud

A playground is compatible with an environment of Gravitee Cloud when it asks of it nothing that environment does not run.
The Lab works that out from the files of the playground, against what the gateways of the environment report: it declares
nothing, and `make check` asks for no declaration. On the page of a playground, *Deploy to* lists *This machine* and each
environment of the Gravitee Cloud accounts you connected. An environment the playground cannot go to is disabled, with the
reason as its tail. With [API Management in the Cloud](lab-ui.md#where-api-management-runs), its environment is the place
proposed first; a playground that cannot go there proposes *This machine*, and its page says why.

A deploy to Gravitee Cloud starts the gateway add-on of its kind and the apps, and runs the seed apart from the core
(`docker compose run --rm --no-deps`), so that it starts, restarts and recreates nothing of the core.

What an environment runs comes from its gateways, as the Cloud lists them: each has a scope (`APIM` or `AM`) and a version,
and the environment runs the product of each scope at the version of its oldest gateway. The product is named as the core's
group whose plugins folder it is (`apim` is API Management). An environment with no gateway runs nothing the Lab can compare:
its reason is *Set up a gateway in <environment> first*, and only the reasons that do not depend on it are given. The hybrid
gateway mounts what the Hybrid gateway add-on mounts. The Lab gives the first reason that applies, in this order:

| Reason | When | What to change |
|---|---|---|
| Nothing to deploy | There is no seed service, or `seed/` holds nothing | A seed service, and `seed/apim/` for the API Management it fills |
| Needs Gamma | `seed/` holds `aim/` or `authz/`, and the account's plan does not enable Gamma | Nothing, when the playground is about Gamma |
| Needs API Management 4.13 | `requires.images` asks for a later minor version of API Management than the oldest gateway of the environment runs | Ask for the version the environment runs, which the Gravitee Cloud page shows in the gateway's popover |
| Changes this machine's Management API | The playground, or an add-on or a setup it brings, changes a service of API Management's control plane (its Management API, its consoles): Gravitee Cloud runs them | Nothing of the control plane can change in Cloud: put the change on the gateway, which is the platform's |
| Its apps use this machine's Management API | An app, or a service of an add-on it brings, has `depends_on` the Management API or a console of API Management, or an app calls one in a URL of its `environment` or `command` | Make the apps backends, which the gateway of Gravitee Cloud calls |
| Seeds more than API Management and Access Management | `seed/` holds another entry than `apim/`, `am/` and `openfga/`: `aim/`, `authz/`, `environments/`, `controls.yaml`, `refills.yaml` | The seed of a deploy fills API Management in the platform, and Access Management and OpenFGA here. No management API creates the [environments of its own](#environments-of-its-own) a seed names |

- What a playground needs of this machine's platform, other than API Management's gateway and control plane, starts with
  the deploy, with what it depends on (Access Management and its database): the apps and the seed use them as they do for a
  start on this machine.
- What a playground, or an add-on it brings, sets on `apim-gateway` (a setting, a port, a volume) is set on the hybrid
  gateway of the platform it is deployed to: the Lab writes a copy of its file for that platform, with the gateway and
  the hosts that name it written as the platform's, and includes the copy in place of the file. Nothing changes on the
  gateway of this machine. A host such as `http://apim-gateway:8082` in an app therefore reaches the gateway of the
  platform the app runs on.
- An entry of `seed/` whose name starts with a dot is ignored.
- The seed service may depend on the core: it runs apart from it for Gravitee Cloud, and its `depends_on` are for *This
  machine*.
- A deploy gives the seed the seeder's [settings](#settings) for that place, in the environment of that one run, over
  those its service sets: `APIM_MANAGEMENT_URL`, `APIM_MANAGEMENT_PATH`, `APIM_TOKEN`, `APIM_ORG`, `APIM_ENV`,
  `APIM_GATEWAY_URL` (`http://localhost:8090`), `GATEWAY_INTERNAL_URL` (`http://apim-hybrid-gateway:8082`) and
  `STATE_DIR`. The token goes by name (`-e APIM_TOKEN`), never as a value on the command line.
- The seed service mounts `./.state:/state`, as every seed does: the record of each place is kept under it, apart from
  *This machine*'s ([What it records](#what-it-records)).
- The guide names both gateways in its calls, `localhost:8082` for *This machine* and `localhost:8090` for Gravitee Cloud,
  and writes `localhost`, never `127.0.0.1`: the hybrid gateway answers only a host name registered in Cloud.

An add-on declares that it runs the gateway of a remote Gravitee with `gateway`. In files and code that remote Gravitee is
a *platform* and Gravitee Cloud is its one kind; the Lab UI says Gravitee Cloud and account:

```yaml
x-gravitee:
  gateway:
    platform: cloud                  # the kind of remote platform it serves
    service: apim-hybrid-gateway     # its service, which publishes a port: the domains come from it
    token: HYBRID_GATEWAY_CLOUD_TOKEN  # the setting that takes the gateway's token, without a default
```

`make check` refuses a kind that does not exist, a `service` that is not the add-on's own or publishes no
port, a `token` that is not a setting of the add-on without a default, a second add-on for the same kind, and a change
to a service of the core or a dependency on one in the add-on and in the add-ons it includes, because a deploy brings
them without the core.

### Accounts and secrets

A password, secret, token or key of a playground is a setting, never a literal: `${NAME:-default}`, whose default may be
a public demo value, or `${NAME}` when the person must provide it. In a shared playground `make check` refuses a
literal, the `password` and `key` of an account included ([the rule](../AGENTS.md#rules)).

- **Define each secret once**, as a YAML anchor in an `x-<id>-secrets` block placed before `x-gravitee`, because an alias
  must come after its anchor. The comment on the line describes the setting in the settings form. The accounts and the
  services alias the anchor (`password: *demo-password`, `DEV_LLM_API_KEY: *dev-llm-key`), so the name and the default
  are written once.
- **The platform's own passwords are settings too.** The seed and the apps sign in to the platform as `admin`. Acme Shop
  reads those passwords from `ACME_SHOP_APIM_PASSWORD` and `ACME_SHOP_AM_PASSWORD`, so a lab whose consoles have other
  passwords still works.
- **The Lab UI shows an account as it is now.** It replaces each `${NAME:-default}` with the setting from the Lab UI's
  environment (the shell that ran `make ui`), else from the root `.env`, else from the `.env` of its folder, else the
  default. This is the order Compose uses, which gives the root `.env` precedence over the `.env` of an included
  playground. A `${NAME}` that is set nowhere shows as written.
- **Say in the README** which settings to set before the first start: the seed writes a password, a secret or a key into
  the products when it creates them, and keeps what it finds on later runs.

### Platform settings

A playground changes core services in overlays, exactly as an add-on does, so `docker compose config` shows the merged
result. Name the core service and give only the keys the playground needs:

```yaml
  apim-gateway:
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Ollama on this machine, also on Linux
    volumes:
      - data-acme-shop-gateway-models:/opt/graviteeio-gateway/models   # The AI models downloaded on first start (129 MB)
```

Use the management APIs for settings the products allow there, such as portal settings, not container environment
variables.

## Apps

An app is a service the playground builds and runs. Two ways to build one:

- **From its folder**: `build: ./apps/<app>`, with one `Dockerfile`. Its tests ship in the image and run there with
  `make ci` of its catalog.
- **From a repository**: a Compose remote build context, with the commit pinned so that the image does not change
  unannounced.

An add-on that builds from a repository:

```yaml
  agent-lens:
    build: https://github.com/acme/agent-lens.git#99fe20daed1d5d13d51a10c23abe4f1b05cb04fa:viewer   # Pinned to an upstream commit, so the image does not change unannounced
```

A playground can build its apps from a repository too. An app that needs the whole repository as its context names
its Dockerfile:

```yaml
  acme-hostels-agent:
    build:
      context: https://github.com/acme/hostels.git#99fe20daed1d5d13d51a10c23abe4f1b05cb04fa
      dockerfile: hotel-agent/Dockerfile
```

An app follows the rules of an add-on's services, `restart: unless-stopped` included. See
[`addons/AGENTS.md`](units.md#add-ons). This is Acme Shop's open REST API:

```yaml
  shop-api:
    image: ghcr.io/acme/hub/acme-shop-shop-api:latest
    build: ./apps/shop-api
    pull_policy: ${ACME_SHOP_PULL_POLICY:-build}
    container_name: gio-shop-api
    restart: unless-stopped
    ports:
      - "127.0.0.1:8113:8000"   # The bank's open REST API, which MCP Studio can turn into tools
    labels:
      io.gravitee.name: Acme Shop open API
      io.gravitee.description: The bank's open REST API, which MCP Studio can turn into tools
    # ...
    networks:
      - internal
```

- **Names**: service names are global to the project. Prefix them with the playground's (`shop-api`, not `api`), or two
  playgrounds merge into one service.
- **Image and pull policy**: what it builds is built here or pulled, as the playground's own setting says. Each such
  service has `image: <images>/<unit>-<service>:latest` (the `images` prefix of its catalog) and `pull_policy: ${<ID>_PULL_POLICY:-build}`. The comment on the first use of the variable is the setting's label in the settings form, its name under it: `# build: build it here at each
  start; always: pull it, build it here if it cannot be pulled`. `make check` asks for the pull policy. The
  repository's CI finds every image the add-ons and playgrounds build and publishes it: nothing to add for a new
  playground. No variable goes in these image names: they are the lab's own apps, not versions to choose on the Images
  page. The Lab UI never forces a build. [Built here, or pulled](configuration.md#built-here-or-pulled) says what the
  policies do.
- **Two services, one image**: only one has the `build:`. The other has the same `image:` and `pull_policy: never`, or
  Update tries to pull it. Acme Shop's `shop-agent-direct` runs the image of `shop-agent` that way.
- **Ports**: published on `127.0.0.1` only, as `"127.0.0.1:8113:8000"`.
- **Labels**: `io.gravitee.name` and `io.gravitee.description` name and describe a service in the Lab UI. A service
  becomes a console with two more labels, `io.gravitee.console.<id>.name` and `.url`, and `consoles` lists it:

```yaml
  shop-web:
    # ...
    labels:
      io.gravitee.name: Acme Shop web app
      io.gravitee.description: The bank's web app, where customers chat with Ace
      io.gravitee.console.shop-web.name: Acme Shop
      io.gravitee.console.shop-web.url: http://localhost:8110
```

## The seed

The seed creates what the use case needs in the platform, once the management APIs are healthy. One generic container,
the seeder, runs it. It talks to the platform only through its management APIs, and it is idempotent, so seeding again
is safe. [`seeder/README.md`](../seeder/README.md) describes the engine.

```yaml
  shop-seed:
    image: ghcr.io/gravitee-io-labs/gravitee-lab/seeder:latest
    build: ../../seeder
    pull_policy: ${ACME_SHOP_PULL_POLICY:-build}
    container_name: gio-shop-seed
    restart: "no"
    volumes:
      - ./seed:/seed:ro
      - ./.state:/state   # What the seed created, to remove it later
    environment:
      <<: *platform-login
      APIM_MANAGEMENT_URL: http://apim-management-api:8083
      AM_MANAGEMENT_URL: http://am-management-api:8093
      # ...
    labels:
      io.gravitee.name: Playground seed
      io.gravitee.description: Sets up the bank's APIs, applications and identity on the platform
      io.gravitee.role: seed
    networks:
      - internal
```

- **The label is how the Lab UI knows the seed**, not its name: `io.gravitee.role: seed`, with `restart: "no"`. A
  playground with a `seed/` folder has exactly one such service.
- **When it runs**: the Lab UI runs it when a Start sets the playground up, and on Re-seed. It never runs from the
  Containers page. A command that names no service (`make all`, `make start`, `make restart`, a plain
  `docker compose up`) runs it again, which is why running it twice must be safe.
- **Endpoints, never container names.** A seed talks to endpoints. Its files hold no address and no container name: they
  use `${VAR}`, and the seed service sets each one, to a container name in the lab. The seeder reads every endpoint
  from its settings, so the same files can seed another platform. the seed check of a catalog (`make ci`) fails on a literal address in a seed
  file, and on a variable no seed service sets.

### Files

Seed files use each product's own format, so they come from an export and go back the same way. They are `.yaml`,
except API exports, which are `.json` (`seed/apim/apis/*.json`, and the `apis/*.json` of an environment).

| Folder | Format | Created through |
|---|---|---|
| `seed/apim/apis/*.json` | APIM API export (v4 definition) | `_import/definition` |
| `seed/apim/settings.yaml`, `applications/*.yaml`, `subscriptions/*.yaml` | Environment settings, applications, subscriptions | APIM Management API |
| `seed/environments/<id>/environment.yaml` | The id, name and description of an environment of its own | Nothing: the seed [never creates it](#environments-of-its-own) |
| `seed/environments/<id>/settings.yaml`, `apis/*.json`, `applications/*.yaml`, `subscriptions/*.yaml` | As `seed/apim/`, for that environment | APIM Management API |
| `seed/environments/<id>/users.yaml`, `groups/*.yaml` | The users that groups name, and groups with their members | APIM Management API, Automation API |
| `seed/environments/<id>/portal/`, `seed/apim/portal/` | The developer portal of the environment: theme, structure, pages, links, API listings, and the text of the page of each listed API. Images in `assets/` | Automation API, and the Console's navigation items for the listings and the pages of their APIs |
| `seed/am/domain.yaml`, `applications/*.yaml`, `protected-resources/*.yaml` | Domain with its scopes, users, applications, MCP servers | AM Management API |
| `seed/am/gamma-link.yaml` | The link from Gamma to the AM domain | Gamma and AM APIs |
| `seed/openfga/*.yaml` | OpenFGA store file (`fga store import`) | OpenFGA API |
| `seed/aim/<resource>/*.yaml` | Agent Management: credentials, providers, models, MCP servers, knowledge, skills, agents (an agent file may also carry its `identity` and its `access` to the plan of a studio), API tools (only the `operations` listed, tailored when asked), workspaces, MCP proxies, studios, dashboards, governance (approval rules and settings, compliance); a file with `needs: NAME` is skipped while the setting NAME is empty | Gamma and Automation APIs |
| `seed/authz/entities.yaml`, `policies/*.yaml`, `schema.yaml`, `schemas/*.yaml` | Authorization Management: what the policies are about, GAPL policies, the schema of the editor | Gamma API |
| `seed/k8s/*.yaml` | Kubernetes manifests: a namespace, a Secret, a `ManagementContext` for the Gravitee Kubernetes Operator. One manifest to a file, in name order | Server-side apply on the cluster of `KUBECONFIG` |
| `seed/controls.yaml` | The protections the lessons switch on | The APIs of the files above, aligned to the mode on every run |
| `seed/refills.yaml` | What the lessons spend, given back on every run | The counters the gateway keeps in the core's Redis |

Seed files use `${VAR}` for everything that depends on the target: service URLs, the Redis password of a vector store
resource, the gateway and AM addresses shown to clients. Acme Shop's seed files use, among others, `${AM_GATEWAY_URL}`,
`${SHOP_MCP_URL}`, `${OLLAMA_URL}` and `${REDIS_URL}`. The API definitions are JSON objects: the checks read the plugins
they use, to tell which license features the playground needs, in `seed/apim/` and in each environment of its own.

### Environments of its own

A playground for several tenants gives each one an environment of API Management, with its own APIs, applications, groups,
users and developer portal. An environment is a folder of `seed/environments/` that repeats the layout of `seed/apim/`,
which stays the platform's own environment (`APIM_ENV`) and may be absent:

```text
seed/environments/<id>/
  environment.yaml      id (the folder's name), name, description
  settings.yaml         merged into the environment's settings, as seed/apim/settings.yaml is
  users.yaml            the users that groups name
  groups/<hrid>.yaml    one group each
  apis/*.json           API exports
  applications/*.yaml
  subscriptions/*.yaml
  portal/               theme.yaml, portal.yaml, pages/*.yaml, content/*, links/*.yaml, listings/*.yaml, api-overview.md,
                        api-details.md, api-docs/<feed>.yaml
  assets/               the images that portal/theme.yaml names
```

- **The id** is the folder's name, the environment's id and its hrid: lowercase letters, digits and dashes, not `local` or `default`,
  and never the platform's own (`APIM_ENV`), which the seed refuses. `environment.yaml` repeats the id and holds a `name`, and
  `make check` says when they are missing or differ. The id, the name and the description are one line of plain or quoted
  text with no ` #` in it: the init script, `make check` and the seed read the file in three ways, and agree on that. `${ENV_ID}` and `${ENV_NAME}` stand for the id and the name in every
  file of the folder: the engine sets them, the seed service sets every other variable.
- **The seed never creates or deletes an environment**, because [no API does](#environments-and-developer-portals). It looks
  the environment up by id and stops with `Environment <id> does not exist on this platform` when it is missing. The playground
  makes them with a one-shot service that runs before the seed and writes one document per `environment.yaml` into the core's
  MongoDB, so that the id, the name and the description are written once: `<id>-envs` is the usual name of that service. A playground with environments stays on *This machine*: [Deploying to Gravitee
  Cloud](#deploying-to-gravitee-cloud) says why.
- **Users** are memory users: `users.yaml` lists `source: memory`, `sourceId` (the username), `email`, `firstname` and
  `lastname`. A memory user signs in only when the Management API's configuration holds its username and a bcrypt hash of its
  password, so the playground sets both on `apim-management-api`: the Lab UI says it changes the Management API before a
  Start. The platform has no row for a memory user before its first sign-in, so the seed gets the row by adding the user to the
  group that names it, as the Console does, and records it. A user that exists is used as it is.
- **Groups** are the Automation API's group (`hrid`, `name`, `notifyMembers`, `members` with `source`, `sourceId` and `roles`)
  with an optional `defaults` that the seed applies through the Management API: `roles` (the roles a new member gets),
  `lockApiRole`, `lockApplicationRole`, `maxInvitation`, `systemInvitation`, `emailInvitation`,
  `disableMembershipNotifications` and `eventRules`. The file name is the hrid. A member is a user of `users.yaml`, or carries
  `external: true` for one the platform has already (`admin`), and the seed check says which is neither. A member that is no
  user of the platform, and that no identity provider names, stops the seed, where the platform would ignore it.
- **APIs and applications name groups by hrid**: `api.groups` and an application's `groups` hold hrids of the folder, and the
  seed sends the ids. An export carries no `id` and no plan id, because the same API is published in several environments
  and an id is global. Name each API once in its environment, and give each a context path no other environment uses: the
  gateway serves them all.
- **Subscriptions** are as in `seed/apim/`, with `pending: true` to leave one for a person to accept (on a plan that asks for
  approval, and with no `apiKey`: the one who accepts chooses it), `comment` for the request message such a plan asks for, and
  `consumerConfiguration` for the callback of a push plan (`entrypointId` and `entrypointConfiguration`). A subscription that
  has `pending` or `comment` is made through the Portal API. Quote a variable that holds a key or a token (`apiKey: '${KEY}'`):
  YAML reads an unquoted `12345` as a number and `true` as a boolean, and the seed stops on a key it would have sent changed.
- **The seed's login** acts in an environment that was inserted in the database, because it is an administrator of the
  organization, but the Console lists only the environments where a user has a role. The seed gives its login the administrator
  role in each environment that the Console does not list for it (every one made in the database), so that the data steward
  finds it in the environment picker, records it, and a removal takes it back. An environment that lists it is left alone.
- **The portal** is one `portal.yaml` for an environment, which is one portal, with a homepage at most. Its pages, links and
  listings name only what the folder holds: a `location` is a path of `portal.yaml`, an `activeThemeHrid` a theme of the
  folder, an `apiHrid` the `crossId` of an API export of `apis/`. A path is written as people read it
  (`Event feeds/Manufacturing`). A key a file does not have stops the seed. The platform's own environment takes the same
  folder as `seed/apim/portal/`.

  | File | Holds |
  |---|---|
  | `portal/theme.yaml` | The portal theme of the Automation API (`hrid`, `name`, `definition` with `color` and `font`), and `assets:` with the `logo`, `favicon` and `backgroundImage` as paths relative to the file (an image under 256 KB), which the seed sends as data URIs. The page and card colors are `color.background.page` and `color.background.card`; the Console's `pageBackground` and `cardBackground` are accepted and written that way |
  | `portal/portal.yaml` | `hrid`, `name`, `activeThemeHrid` and `structure.topNavbar` (`path`, `displayName`, `visibility`) |
  | `portal/pages/*.yaml` | A page: `hrid`, `name`, `type`, `area`, `location`, `order`, `visibility`, and `file:` in place of `content`, the path of its text |
  | `portal/content/*` | The text of pages: Gravitee Markdown, AsyncAPI, OpenAPI. Never read as a page. Rendered with the seed's variables, so a page's own `${...}` is written `$${...}` |
  | `portal/links/*.yaml` | A link: `hrid`, `name`, `href`, `location`, `order`, `visibility` |
  | `portal/listings/*.yaml` | The APIs shown and where: `hrid` and `apis` of `apiHrid`, `location`, `order`, `visibility`. The `order` is a position among the items of the location, 0 first, which is its default; an API that moves to another `location` in the file moves in the portal |
  | `portal/api-overview.md` | Optional. The Gravitee Markdown of the Overview page of every API a listing shows, rendered with the seed's variables and with `${ENV_ID}` and `${ENV_NAME}`. The platform's own expressions are written `$${...}` (`$${api.name}`, `$${api.description}`), and the platform refuses a text whose expression it cannot evaluate, which stops the seed with its message. Without the file, the pages keep the platform's default text |
  | `portal/api-docs/<feed>.yaml` | Optional. The AsyncAPI document (YAML, version 2.0 to 2.6, 3.0 or 3.1), or the OpenAPI document (an `openapi` key), of the listed API whose `crossId` is `<environment id>-<feed>`: the file name is the end of the `crossId`. Rendered with the seed's variables and with `${ENV_ID}` and `${ENV_NAME}`, `$${` standing for a literal `${`. A file for an API no listing shows, two files for one API (`.yaml` and `.yml`), a version the portal does not show, and a document with no `info.title`, `info.version`, or `channels` (AsyncAPI) or `paths` (OpenAPI) stop the seed before its first change |
  | `portal/api-details.md` | Optional. The plain Markdown of the Details tab of the page of every API a listing shows. Written like `api-overview.md`, but the platform fills the template of such a page with `api.name`, `api.version` and `api.description` only, and refuses the components of Gravitee Markdown (`<gmd-card>`): it is not the Overview text |

- **An API shown in a listing has an Overview page**, which a portal opens when someone chooses the API in its menu or on its
  catalog card. The platform makes none by itself, so the seed asks for the default page of each API item, publishes it, and
  writes `api-overview.md` in it when the folder has the file.
- **An API that has a document in `api-docs/` has an AsyncAPI page after its Overview**, which the portal's viewer renders: the
  channel (the topic, with its parameters), the direction of each operation (`receive` for a customer who reads, `send` for the
  publish-only feed) and its messages, each with its payload schema and its example. Write the document in AsyncAPI 3.0: the
  viewer labels an operation `RECEIVE` or `SEND`, where a 2.x `subscribe` shows as `SUB`. The page is made, written, then
  published, so that the platform's sample document is never shown. A document that is gone from the folder takes its page.
  An OpenAPI document gets an OpenAPI page in the same place, which the viewer draws as the paths and their operations.
- **The page of an API itself is the other way to it.** `/catalog/api/<id>` is not the menu: its Details tab shows the page of
  the API that is its homepage, and its Documentation tab the others, which the Console keeps in the API's Documentation. The
  seed makes there a Markdown homepage from `api-details.md` and an AsyncAPI page (a `SWAGGER` page for an OpenAPI document) from the document, each published and found
  again by its type and name. A catalog card of an API that a listing shows opens the menu's page, not this one, so these two
  matter for the direct address and for a link to it.
- **The pages are recorded with their listing** (`API pages`, one record that counts the pages of the menu and those of the
  APIs) and removed with it: the pages of the menu go with their API items, and the pages of an API are deleted by id. A page
  of an API that someone else made under the same type and name stops the seed: it never takes it.
- **A JSON file is read as a document before its variables are replaced**, and a string of it that is itself JSON text (an
  endpoint's `sharedConfiguration`) gets the value of a variable escaped for that text. A password with a quote or a
  backslash therefore arrives in the API definition as it was set.

### Settings

The seed service's environment is the seeder's settings. A required one that is missing stops the seed with `<NAME> is
not set`, and a variable a seed file uses with no value stops it with the file's name.

| Variable | Meaning | Default |
|---|---|---|
| `APIM_MANAGEMENT_URL` | The APIM management API | Required to seed APIM or Gamma |
| `APIM_ORG`, `APIM_ENV` | Organization and environment ids. `APIM_ENV` is the environment of `seed/apim/`: those of `seed/environments/` carry their own | `DEFAULT` |
| `APIM_MANAGEMENT_PATH` | The path before the management routes: `/apim/rest` on the Cloud Gate of Gravitee Cloud, where `APIM_TOKEN` is a Cloud Token | `/management` |
| `APIM_PORTAL_PATH` | Where the Portal API is served, which the seed calls for a subscription that is pending or has a comment | `/portal` |
| `APIM_TOKEN` | A token for APIM and Gamma. Without it, `APIM_USERNAME` and `APIM_PASSWORD` | |
| `AM_MANAGEMENT_URL` | The AM management API | Required for `seed/am/` |
| `AM_ORG`, `AM_ENV` | Organization and environment ids | `DEFAULT` |
| `AM_TOKEN` | A token for AM. Without it, `AM_USERNAME` and `AM_PASSWORD` | |
| `OPENFGA_API_URL` | The OpenFGA HTTP API | Required for `seed/openfga/` |
| `AIM_GAMMA_URL` | Where `/gamma` is served | `APIM_MANAGEMENT_URL` |
| `AIM_AUTOMATION_URL` | Where `/automation` is served: Gamma's objects, and the groups and portals of an environment of its own | `APIM_MANAGEMENT_URL` |
| `GAMMA_AM_URL` | The AM management API, as the APIM management API reaches it | `AM_MANAGEMENT_URL` |
| `AM_PUBLIC_URL` | The AM gateway, as a browser reaches it | Required for `seed/am/gamma-link.yaml` |
| `AM_GATEWAY_URL` | The AM gateway, as the API gateway reaches it. Used by seed files | |
| `APIM_GATEWAY_URL` | The API gateway, as a user reaches it. Used by seed files | |
| `GATEWAY_INTERNAL_URL` | The API gateway, as the seed reaches it. With `SEED_WAIT_GATEWAY=1` the seed waits until it serves what was imported, in every environment; without it, it only uses it to wait for a replaced API to leave it | Unset: no wait |
| `SEED_WAIT_GATEWAY` | `1` makes the seed wait for the gateway to take what it deployed: every API served, the protections and the budgets in force, a chat through every LLM proxy (`LLM_PROBE_KEY`). By default it does not: the seed ends when the management API holds everything, and the gateway takes it within seconds (an AI model downloads at its first deployment, which takes longer) | `0` |
| `GATEWAY_READY_TIMEOUT` | Seconds in all that the seed waits for the gateway when it waits. The AI models of an LLM proxy download at its first deployment | `300` |
| `LLM_PROBE_KEY` | An API key of the plan whose LLM proxies have a semantic cache. The seed chats through every LLM proxy it imported, asks the same question again, and ends with an error unless the proxy answers it from its cache. Leave it unset when no LLM proxy caches | Unset: no probe |
| `SEED_PROTECTIONS` | `on` or `off`: the state the [controls](#controls-the-protections-a-lesson-switches-on) are brought to | `on` |
| `GATEWAY_SYNC_SECONDS` | How often the gateway synchronizes. With `SEED_WAIT_GATEWAY=1` the seed waits two of them after it deploys a control | `5` |
| `REDIS_URL`, `REDIS_PASSWORD` | The core's Redis, for [refills](#refills-what-a-lesson-spends-put-back) | Required for `seed/refills.yaml` |
| `KUBECONFIG` | Kubeconfig of the cluster for `seed/k8s/`; the Kubernetes add-on writes `secrets/kubeconfig-internal.yaml`, which a seed mounts read only | Required for `seed/k8s/` |
| `READY_TIMEOUT` | Seconds the seed waits for a management API to answer | `300` |
| `SEED_WORKERS` | How many requests the seed has in flight at once. Steps that do not depend on one another run together ([Order and parallelism](../seeder/README.md#order-and-parallelism)); `1` runs everything in order | `4` |
| `SEED_DIR`, `STATE_DIR` | Where the seed reads its files and records what it creates | `/seed`, `/state` |

### What it records

The seed records what it creates, never what existed, in `.state/created.json` of its folder (git-ignored). It
replaces or deletes only what that record says it created:

- An API at the same path as one of its own, an APIM application, a Gamma object, a policy or a dashboard with the same
  name as one of its own, that it did not create, is an error. So is an authorization engine configured otherwise,
  which is a warning when the engine is marked `optional`.
- An AM domain, an AM application or an OpenFGA store that it finds by name or client ID is updated, never deleted.
- An AM scope the domain already has is left as it is, and the seed deletes only the scopes it made.
- The approval settings of the environment are the one thing it replaces that it did not create: it records the values they had, and a removal puts them back unless someone changed them since.
- The entities of an `entities:` list are recorded once for each type, with their ids.
- An [environment of its own](#environments-of-its-own) is never created, changed or deleted. A group, a portal, its theme,
  a page or a link with the hrid of one of its own, that it did not create, is an error, and so is a page of an API itself with
  the type and name of one of its own (`AsyncAPI`, `Overview`), and an API that a listing would
  put in a folder where someone else already put it. A user that exists is
  used as it is: never changed, never deleted. The seed's login is recorded when it had to be made an administrator of an
  environment, and a removal takes that back.
- What it makes in an environment is recorded with the environment's id, so that the same API in two environments is two
  records (`Batch status [beam]`), and a removal deletes each from its own environment. The records of `seed/apim/` keep their
  plain names.
- An API that the platform made although its import failed (it answers `API imported with error` and keeps the API, stopped) is
  recorded with no digest, so that the next run updates it from its file. A renamed API is one record, under its new name.
- With `.state/created.json` lost, the next seed stops on what it made. Delete those objects, or Reset.
- `seed.py --remove` deletes the recorded items in dependency order: the portal's objects, then the subscriptions, applications
  and APIs, then groups and users. An AM domain or an API that existed before stays, and so does every environment. What the
  platform refuses to delete, a user that is the primary owner of an application of its own, stays in the record with a `!` line,
  and the rest is removed; a later removal tries it again. What an environment that is gone held counts as deleted, one line each.

The Lab UI reads the file to offer *Delete what it created* and to list what that deletes. It shows the last line of the
seed's output, `[seed] ✗ <why>`, as the reason a start failed.

A deploy to Gravitee Cloud records in a folder of its own, `.state/<platform id>/<environment id>/`, which the seed reaches
as `STATE_DIR=/state/<platform id>/<environment id>`. Each place keeps its record, and a Re-seed, a Delete or a Reset of
*This machine* never reads or empties another one. Removing an account in the Lab UI deletes its folders and leaves
what the seed created in Gravitee Cloud.

## Controls: the protections a lesson switches on

A playground whose lessons teach a protection can start with it switched off: the person meets the problem, opens the
configuration that is already there, switches the protection on in the console, and tries again. It is
a choice of the playground: one that shows a finished configuration declares no control.

`seed/controls.yaml` declares the protections, once each, and a mode setting of the playground decides the state each
run brings them to. The seed files themselves say the protected state: their steps enabled, their policy deployed.

```yaml
pii:                                # lesson 2: an IBAN reaches the model in clear
  api: Acme Shop LLM
  flow: Prompt
  steps:
    request: [PII filtering (financial_account)]
```

```yaml
cap:                                # lesson 5: an agent moves any amount while the policy is not deployed
  policy: Agents move at most EUR 500
```

A control targets an `api` (the steps of a flow), an `mcp-proxy` (the steps of a flow), a `workspace` (the steps of its
router), or a `policy`.

- **Names are those of the seed files**, not always the console's. A step is named by its `name` in the file, a policy
  by its `name`, a target by the name of its API, proxy or workspace.
- **The console labels differ.** Policy Studio labels a step by its policy ("PII Filtering"), whatever the step is
  called in the file, and its *Description* field holds the file's `description`. A workspace's router and the
  authorization policies show the names of their files, and a router step has no *Description* (the platform drops it).
  A guide sends the person to a step of a flow by what the console shows, and an app names a protection by that
  label, never by the name in `controls.yaml`.
- **A name that matches nothing stops the seed.** A target, flow or step that matches nothing in the seed files is an
  error, so a control never silently switches nothing. So is a control no module found its target for.
- **The mode is a setting of the playground.** Acme Shop's `ACME_SHOP_PROTECTIONS` is `off` by default, or `on` for
  everything protected from the start. Its compose file passes it to the seed as `SEED_PROTECTIONS`. Without the
  variable the seed protects, and any other value is an error. The comment on the setting's line describes it in the
  settings form, and says what applies a change.
- **Who reads the mode decides how a change applies.** Re-seed recreates the seed and nothing else, so it brings the
  protections to the new mode. A service that reads the mode itself when its container is made (Acme Shop's traffic
  generator) sees a new mode once *Save & apply* in the settings form recreates it. A change of mode is *Save &
  apply*, then *Re-seed*.
- **Re-seed starts the lessons over.** Every run brings each control to the state of the mode, even one the person
  switched or edited in the console, and only the controls. An LLM proxy is updated in place, its steps and then a
  deployment, because an import would replace it, drop its keys and make the gateway reload. An MCP proxy and a
  workspace's router are written with the mode. A policy is deployed or taken out of service. The rest of an API stays
  imported once, with its digest recorded. The digest is the file's, never the mode's.
- **A busy platform does not replace an API, and neither does a wrong update.** The API is imported again from its file
  only when the platform says it lacks the in-place update of an LLM proxy (404, 405 or 501). A platform that cannot
  answer now, whether the connection fails or times out or it answers a 5xx or a 429, is asked again a few times, 8 s
  apart, and then the seed stops with what it said. Any other refusal (a 400, 401, 403, 409, 422...) stops the seed at
  once with the platform's message, because the update would be refused again, and replacing the API would hide that and
  cost it its keys.
- **A policy is a draft only the first time.** A policy that was deployed goes out of service with `disable` (the
  console's "Undeploy") and shows `Disabled`: nothing makes it a draft again. A Re-seed takes a policy the person
  deployed out of service in just that way. *Deploy to PDP* deploys a draft and a disabled policy alike, so a guide names
  the action, not the badge.
- **A control is what the platform can switch and the gateway honors.** A step has `enabled`, and a policy is deployed or
  not. Test it on the platform before you declare one, and with the steps that stay on: name only those whose absence the
  person meets.
- **Checks that follow the mode say it in the file.** The number of tools a role is offered while its control is off is
  written `tools: {protected: 7, unprotected: 8}`.

## Refills: what a lesson spends, put back

A lesson that makes the person spend something, such as a sandbox budget run to its limit to meet the 429, has to be
playable again after a Re-seed. Otherwise "Re-seed starts the lessons over" is not true for it. `seed/refills.yaml`
declares what a run puts back, once, by the names of the workspace file, whatever the mode is.

```yaml
hackathon:                          # lesson 6: a click on "Spend the Hackathon budget" uses most of the sandbox's hour
  workspace: Acme Shop Teams
  member: team-hackathon            # the source id of the member, as the workspace file lists it
  counters: cost                    # what it spent of its cost budget
```

- **It deletes counters, nothing else.** The gateway keeps what a member spent of a budget in the core's Redis, one hash
  per budget (`ratelimit:<workspace>:<application>:cost-rl`), and the hash says whose it is in its `subscription` field.
  A budget is a rolling period, so only time gives it back. A refill finds the hashes whose `subscription` is the
  member's and deletes them: the member's next call opens a new period with the whole budget. Another member's counter,
  a token counter and anything of the semantic cache stay, and so does the same application's counter in another
  workspace. A member with no subscription id is refused, not matched with the counters that name none. No platform call
  is made.
- **It runs when the workspace is ready**, after its deployment and once the gateway serves it, so nothing the seed does
  after puts back what it took out. The seed needs `REDIS_URL` and `REDIS_PASSWORD` (its service already has them for the
  LLM proxy's semantic cache, and Compose's `${REDIS_PASSWORD:-redispassword}` is the core's own default) and the `redis`
  library (`seeder/requirements.txt`).
- **It is a convenience, never a requirement.** A member the platform does not have yet, a Redis nobody can reach, or an
  error Redis answers with is a line in the seed's output (`[refills] ! ...`, with the error named and the password
  never in it), and the seed goes on. A lab whose budget is not given back is not a broken lab: the period ends. A wrong
  declaration stops the seed, as for a control: a key that is not one of the three, a `counters` that is not known, a
  member the workspace file does not list (before anything is changed), a workspace no seed file has (after the modules
  ran).
- **The refill is the one thing the seed does outside the management APIs.** It reaches the core's Redis. If it cannot,
  it says so and goes on.
- **Test it** with `make test` (`seeder/tests/test_refills.py` and the refill tests of `test_aim_workspaces.py`: a fake
  Redis and the real files of Acme Shop), and on the running lab: spend the budget, Re-seed, and call the workspace
  again.

## The guide

`guide.md` is Markdown, shown next to the live state of the playground, one page at a time.

- It has an introduction, then one `## 1. Title` section per lesson, numbered 1, 2, 3 in order (`make check` enforces
  it), then any other `## ` section, such as `## Start again` or `## If something does not work`.
- The page lists the lessons and shows the guide a page per section: the introduction, each lesson, then the other
  sections, with previous and next and a picker of every page. Keep each section readable on its own.
- Each page has an address (`#/playgrounds/acme-shop/4`), and the guide opens on the page read last.
- A diagram is a ```` ```mermaid ```` block, never drawn with characters. The page draws it in its own colors, light and
  dark alike, and so does GitHub.
- What only matters before a start, such as memory, the first start's time and what to fix, is not in the guide. The
  page says it under *Before you start*, from the checks and `first_start`.

### Commands to run

A block of the guide whose fence says `run` after the language, as in ```` ```bash run ````, gets a *Run* button, which types the block
at the prompt of the playground's [terminal](#terminal) and presses Enter after its last line. It reads as a block of the
language in any other viewer. The button is disabled, with the reason, while the playground has no terminal or does not run. Write
a block as what a person would paste: one command a line, a long one continued with a backslash. A block meant to be read, such as
a manifest or an output, is a plain fence.

### Accounts in a guide

A link whose address is `account:` and a user name of the playground's `accounts` is drawn where it is written as that sign-in:
the text of the link, then the user and its password (or its key), each with a copy button.

```md
1. Open the portal and sign in as Maya: [Maya Chen](account:maya.chen)
```

The values are the accounts' as the Lab UI shows them, so a password stays a setting and is never written in the guide. Put one
where a step says to sign in. `make check` refuses a link to a user that `accounts` lacks; any other viewer shows a link.

### Actions in a guide

A guide can hold buttons that do in the playground's app what the app's own buttons do, so a lesson says "click this"
and the person clicks it where they read. It is optional: a guide without them is a guide, and an app that does not
listen for them is an app.

An action is a Markdown link whose address is `action:` and an id:

```md
- Click [Ask Ace about my accounts](action:ask-ace-accounts), with nobody signed in.
```

An id is 1 to 64 lowercase letters, digits and hyphens, and starts with a letter or a digit. It is a plain link, so it
reads as one in any Markdown viewer. The Lab UI draws it as a small button with the text of the link and a play icon, or an
eye when the id starts with `open-`: the play is for what the app does (a message sent, a policy switched on), the eye for
what only takes the person to look at something (a page of a console). A click shows the first console of the playground
and sends it the action. What follows `action:` and is not an id is no action: the link loses its address and stays text,
and `make check` says so.

The button is disabled, with a tooltip that says why, while it cannot work: the playground does not run (before its
first start too), it lists no console, or its first console cannot be used (it is not running, does not answer, or
refuses to be shown in a frame). It is never clicked to fail.

What an app does is two messages, `window.postMessage`, each checked on both sides for the origin and for the window it
comes from:

| Message | From, to | Means |
|---|---|---|
| `{type: "gravitee-lab:action", id}` | The Lab UI, to the frame of the first console, at the origin of its UI (never `*`) | Run the action `id` |
| `{type: "gravitee-lab:ready"}` | The app, to its parent, at the Lab UI's origin | The listener is installed |
| `{type: "gravitee-lab:location", url}` | The UI, to its parent | The page it is on now (see below) |

The app installs a `message` listener that accepts a message only from `parent`, from the Lab UI's origin
(`http://localhost:9000` or `http://127.0.0.1:9000`, the pages its `frame-ancestors` lists), of type
`gravitee-lab:action`, for an `id` it knows. It runs the action as its own button would, answers nothing, and ignores
any other message. Once the listener is installed it posts `gravitee-lab:ready`, again each time its page loads.

The Lab UI sends nothing before that. An action clicked while the app loads waits, with those clicked after it, and is
sent in its order when the app says that it listens, and at once afterwards. One that waits is not queued twice. The Lab
UI forgets that the app listens when it loads an address in its frame itself (*Reload*, *Back to start*, a link of the guide
under the app's address) and when the app goes away, so an action clicked meanwhile waits for the page that comes. An address
with a `#` may only move within the page it is on, which does not say it again, so it changes nothing. What waited for
an app that went away is dropped.

```js
const LAB_UIS = ["http://localhost:9000", "http://127.0.0.1:9000"];   // where the Lab UI is served from
addEventListener("message", (e) => {
  if (e.source === parent && LAB_UIS.includes(e.origin) && e.data?.type === "gravitee-lab:action") run(e.data.id);
});
for (const origin of LAB_UIS) parent.postMessage({ type: "gravitee-lab:ready" }, origin);   // the browser delivers it where the origin is the parent's
```

The Lab UI never says which page of the guide is read, and the app never says where the person is. The app offers the
actions it has, the guide chooses which to put in front of the person, and an `id` the app does not know is for it to
ignore, or to say so calmly.

## What an app does when it runs in the Lab UI

A running playground shows its page in a frame of the Lab UI, so what the app sends must let the Lab UI do that.

- **Allow the Lab UI's pages in `frame-ancestors`.** Send `frame-ancestors http://localhost:9000 http://127.0.0.1:9000`
  in the `Content-Security-Policy`, and no `X-Frame-Options`, which says `DENY` or `SAMEORIGIN` to everything else. The
  page asks the Lab UI's server, which reads these two headers of each console, and shows a notice in place of a UI the
  browser would refuse. The platform's consoles do that at start: see `x-ui-frames` in `docker-compose.yml`, which `make
  check` reads. The headers of an app are its own and are not in a file: the look at its address says when one refuses.
- **The Lab UI has to be on port 9000.** That origin is what `x-ui-frames` and each app's `frame-ancestors` name, on
  purpose, so that no other page may frame them. With `UI_PORT=9001 make ui` every UI shows as refused.
- **Only the address of a tab is looked at.** A UI that sends its frame to a page that refuses meets the browser's own
  refusal there, and no look at its address can tell. That is the case for the AM console, which goes to the sign-in
  page of its management API, and for an app whose sign-in leaves for Access Management's login page, which sends
  `X-Frame-Options: DENY`. Such a console is not listed in `consoles`, and its link stays in the guide, which opens it in
  the browser. An app that signs in this way does it in a popup, a window of its own that the page does not frame, as
  Acme Shop's web app does.
- **It may ask for an address to load in a tab**, so that a link inside the app can open in another tab of the page, for
  example the Gamma console. It posts `{type: "gravitee-lab:open", url}` to its parent:

  ```js
  parent.postMessage({ type: "gravitee-lab:open", url: "http://localhost:8086/environments/default/aim/agents" }, "http://localhost:9000");
  ```

  The Lab UI accepts it only from the frame of one of the playground's tabs, from the origin of its UI, for a web address
  under the origin of the UI of one of them (any). It loads the address in the tab of that origin and shows it.
  Anything else is ignored, silently. A link that is not under a tab opens in the browser, as any link does.
- **It may say which page it is on**, so that *Reload* loads that page and *Open in a new tab* opens it: a page cannot read
  the address of a page served on another port. It posts `{type: "gravitee-lab:location", url}` to its parent each time it
  is on another page, and once when it loads. The Lab UI accepts it only from the frame of a tab, from the origin of its UI,
  for an address under that origin, and forgets it when it loads an address itself. A UI that says nothing is reloaded at
  the address the Lab UI loaded last. The platform's consoles say it from `lab-ui/frames/location.js`, which `x-ui-frames` in
  `docker-compose.yml` adds to the page they serve.
- **It is told nothing of the guide but the action a button of it asks for.** The Lab UI is generic: it shows the guide
  a page at a time and the UIs in tabs, and keeps an app out of it. No message goes to the app, whichever page the guide
  shows. The one thing it may hear is `{type: "gravitee-lab:action", id}`, when the person clicks an action link and
  the app has said that it listens. What an app offers the person using it, such as ready-made questions and actions, is
  its own and does not follow the guide.
- **It adds nothing to the page's history that the guide could be undone by.** The page and its frames share one session
  history, so what an app does by itself (a route of its own, a hash) adds entries that Back goes back through. An app
  that runs in the Lab UI keeps its views out of that history (`location.replace`, or `history.replaceState`), so that its
  Back never goes through them. The Lab UI turns the guide's pages in place, so its own turns are never among the
  entries, and an address it loads in a tab is an entry of that UI's own.
- **It follows the system's light or dark mode**, not the Lab UI's own switch: a frame of another origin cannot be told.

## Personal and shared

A playground can be personal first, in `.lab/catalogs/local/playgrounds/<id>/` (git-ignored), with the id `local/<id>`. It is
tagged *Personal* in the Lab UI. *New playground* writes either kind, from a form or with an AI agent.

- A personal playground and a playground of a catalog sit at the same depth, so the same paths work in both: it includes an
  add-on of its catalog as `../../addons/<id>/compose.yml`, a setup as `../../setups/<id>.yml`, one of another catalog as
  `../../../<catalog id>/addons/<id>/compose.yml`, and its seed builds `../../../../../seeder`.
- A playground of a catalog never includes a personal add-on or setup, and Share says so. A personal one includes what it
  wants. A playground of a catalog stands alone, so it runs on any machine.

To share one, start it here until its seed ends without an error, then choose *Share* in its ⋯ menu. The Lab UI says first
what keeps it from being shared, for each catalog you can write to: it includes a personal add-on or setup, it has not run
yet, its name is taken, or the catalog's rules find something in the copy. Then it copies the folder into the catalog: a
branch of a repository to propose with a pull request, or a copy in a folder. The versions it ran on go into the copy as its
`tested` set when its file declares none. Your personal playground stays and runs until *Use the catalog version* replaces it.
Without the Lab UI, the `share-item` skill has the same steps. A playground of a catalog is not made personal again: create a
personal one for a private variant.

Deleting a personal playground says what its seed created and leaves in Gravitee (*Stays in Gravitee*).

## Before you share

In the folder of a catalog, or in its repository:

```bash
make check
```

```bash
make ci
```

`make check` (or `python3 lab-ui/catalog.py check <catalog id or folder>` from the Lab) covers the metadata, includes, conflicts,
image defaults, restart policies, descriptions, ports, what a playground requires, and the API definitions its seed imports. It
also checks that the catalog row of the playground reads as its metadata says, and prints the row it expects. `make ci` also
checks the seed files of the playgrounds, builds the apps and runs their tests in their images. Then start the playground from
its page and go through its guide. Share runs the same rules on the copy before anything leaves your machine.

To run a playground without the Lab UI, put `.lab/catalogs/<catalog>/playgrounds/<id>/compose.yml` first in the `include` list of
`.lab/on.yml`, add its plugins to `plugins/apim/` or `plugins/am/`, set the image versions its `requires.images` names
in `.env`, then run `make all`.

## How APIM and Gamma behave: what the seeder relies on

- API exports carry no plan ids. An update maps plans by name to the live ones, or the import tries to replace them.
- The Management API checks an endpoint's shared configuration against the schema of its plugin. API Management 4.12
  names "no store" `NONE` and refuses `{"type": ""}` for a TLS key store or trust store, which a 4.11 export holds:
  leave the store out.
- LLM and A2A proxy APIs cannot be updated from a definition (`Unsupported API type`), so they are replaced.
- A deployment answers 202.
- An LLM proxy refuses an import over itself but takes the update of its entity: `PUT /apis/{id}` with the API as `GET`
  gives it, then a deployment. That is how a control's steps are switched without replacing the API. A step has
  `enabled`, and the gateway skips a disabled one. The Automation API (MCP proxies) takes `enabled` on a step too,
  replaces the flows on every apply and deploys what it changes. A workspace's router takes it as well (`PUT
  .../router`, then `_deploy`).
- A step's `description` is kept by the v4 API definition and by the Automation API, and the console shows it in the
  step's panel, under the name of its policy. A workspace's router step has no such field: the platform drops it, and the
  router shows the step's own name.
- An API's tracing is `analytics.tracing` (`enabled`, `verbose`), with `analytics.logging` and `analytics.otelLogs` for
  the payloads of the spans. The Automation API has no field for them: an MCP proxy's are set on its API entity.
- Gamma has no upsert and keeps no name unique. A module looks each object up by name, slug or id, writes it only when it
  differs, records only what it created, and stops, naming it, when it finds one it did not create.

### Environments and developer portals

What a seed of [environments of its own](#environments-of-its-own) relies on, as read from the platform of this lab
(API Management 4.13, a pre-release):

- **No management API creates or deletes an environment.** The routes of an environment answer `GET`, `HEAD` and `OPTIONS`
  only, and the Automation API has no environment resource: only Gravitee Cloud makes one through an API. An environment is one
  document of the `environments` collection of the core's MongoDB (`_id`, `hrids`, `name`, `description`, `organizationId` and
  `_class`), `DEFAULT` being the one there is. The Management API reads a new document at once.
- **An environment is named by its id.** The v1 routes and the Automation API accept the id or the hrid. The v2 routes and the
  Portal API accept the id only, and answer `400 Invalid organization or environment` for an hrid (`default`), as every
  route does for an unknown one.
- **The list of environments leaves out one inserted in the database**, and `admin` reads its APIs and settings all the
  same. Look an environment up by its id, never in the list.
- **A memory user of the Management API's configuration can have no row in the platform's users**: a default user that never
  signed in has none, though the identity provider's search finds it
  (`GET /management/organizations/{org}/search/users?q=`, whose answer carries a `reference`, and an `id` once the user has
  a row).
- **The Automation API** creates or updates by `hrid` with `PUT`, and reads or deletes by `hrid`. Its resources are APIs,
  applications, groups, dictionaries, shared policy groups, subscriptions, API documentations and links, portals (with their
  listings, documentations and links) and portal themes. It has no users, settings or navigation items. Its description of a
  group says that a member that is no user of the platform is ignored.
- **A new environment's top bar has an empty `APIs` folder** (4.13.0), which the Automation API did not make. A portal with a path
  that such an item holds is refused (`400 Navigation path [/apis] is already occupied by content not managed by the Automation
  API`), so before it makes a portal the seed removes an empty folder at one of its paths.
- **A portal's visibility is `PUBLIC` or `PRIVATE`**, never a group. `activeThemeHrid` of a portal makes its theme the one
  enabled, and disables every other next-gen portal theme of the environment.
- **An API item of the navigation has no page.** `POST .../portal-navigation-items/_default-pages` with `{"ids": [...]}` makes the
  default `Overview` page of each item that has none (a child of type `PAGE`, unpublished, with the visibility of its item, and
  a template of Gravitee Markdown with `${api.name}`, `${api.description}` and the like), and does nothing for one that has it.
  The text of a page is `PUT .../portal-page-contents/{id}`, which renders the FreeMarker of the text first and answers `400` for
  an expression it cannot evaluate. A child is published by its own `PUT` with its `parentId`; unpublishing an item
  unpublishes its children, and publishing one with `propagatePublishToChildren` does not publish the children of one that is
  already published. The Portal API answers the rendered text at `GET .../portal-navigation-items/{pageItemId}/content`.
- **An AsyncAPI page is a navigation page whose content type is `ASYNCAPI`** (`OPENAPI` for an OpenAPI page), fixed when it is made
  (`POST .../portal-navigation-items` with `contentType`), with a sample document in it and the next order under its API item,
  so after the Overview. `PUT .../portal-page-contents/{id}` with `{"type": "ASYNCAPI", "content": ...}` renders no template and
  checks nothing in it: a document that is not YAML, or a version the viewer does not know, is stored, and the viewer shows
  `Error: There are errors in your Asyncapi document` in its place. The viewer reads 2.0.0 to 2.6.0, 3.0.0 and 3.1.0, and says
  `Version "1.2.0" is not supported` for another. It validates the document against the schema of its own version: a 2.6 document
  with `asyncapi: 2.0.0` shows the errors of the fields that version lacks.
- **An API has pages of its own**, under the Console's Documentation of the API (`GET`, `POST .../apis/{apiId}/pages`,
  `PUT` and `DELETE .../pages/{pageId}`, `POST .../pages/{pageId}/_publish`), which the Portal API answers at
  `GET .../apis/{apiId}/pages?homepage=true|false`. The platform adds a system folder (`Aside`) to every API, and deletes the
  pages with the API. A page is `MARKDOWN` or `ASYNCAPI` here (the documentation of the route says Markdown only), made
  unpublished, and its text is kept as written. A Markdown page is checked on every write: its template is filled with the name,
  the version and the description of the API only (`Invalid expression api.entrypoints`), and a tag of Gravitee Markdown is
  refused (`The page content does not follow security policy`). A `PUT` leaves the page published, and gives back the defaults of
  what it does not send: the order goes to 0 and the homepage to `false`, so send both. An update of the API from an import leaves
  its pages alone.
- **The order of an item is its position among its siblings.** The platform keeps an item that is alone in its folder at 0, whatever
  order it is given, and moves an item that is put under another parent to the position it finds there.
- **A portal UI serves one environment**: its `PORTAL_API_URL` ends with `/portal/environments/<id>`, which the container
  writes into `assets/config.json` at its start, and the Portal API takes no portal id. One portal for each environment
  is therefore one container of `apim-portal-ui` for each, each with its own port. `DEFAULT_PORTAL: next` serves the next-gen
  portal at the root. The Portal API accepts any origin, so a new port needs no setting.
- **The next-gen portal's navigation** holds items of type `PAGE`, `FOLDER`, `LINK`, `API` and `API_PRODUCT`, in the area
  `TOP_NAVBAR` or `HOMEPAGE`. A page's content is `GRAVITEE_MARKDOWN`, `OPENAPI` or `ASYNCAPI`. Raw HTML and a `<style>`
  block render in a Gravitee Markdown page, as Acme Shop's home page shows.
- **A portal UI's content security policy allows no remote script or font**, so a theme's font shows only when the browser has
  it.
- **A theme's colors** are `pageBackground` and `cardBackground` in the management routes, and `background.page` and
  `background.card` in what the portal reads.
- **Two settings of every environment are read only**: `portal.entrypoint` (the gateway address a portal shows) and
  `portal.next.access.enabled`, which the core sets from its environment (`metadata.readonly` lists them with `email.*`).
- **One gateway serves every environment of the organization**: the lab sets no `organizations` or `environments` filter
  in the gateway's `gravitee.yml`.

## Limits

- One playground is on at a time. Two use cases would fight over gateway settings and ports, so starting another one
  stops the first, or turns it off.
- A playground that uses Enterprise features needs a license that includes them. A seed that fills Gamma needs the Gamma
  modules of API Management 4.13 or later, and a playground can need pre-release builds of both API Management
  and Access Management, chosen on the Images page. The page says what is missing before the start.
- The UIs of a playground are framed only by the Lab UI on port 9000.
- A seed stops on an object it finds and did not create. It never adopts, changes or deletes one.
- A refill reaches the core's Redis. Without it, a spent budget stays spent until its period ends.
