# Configuration

Everything you can set or look up: the `make` targets, the license, image versions, ports, certificates, plugins, where
data lives, what needs the internet, and the demo logins. The lab is for local demos and learning, not for production.

## Make targets

Run `make help` to list them. A target that needs a tool the machine lacks stops with one line that says so.

| Target | What it does | Needs |
|---|---|---|
| `make ui` | Opens the Lab UI on http://localhost:9000. `UI_PORT=9001 make ui` uses another port, and then the UIs of a playground do not show next to the guide | Python 3.9 or later, `openssl`, Docker with Compose 5.0 or later. Docker does not have to run |
| `make all` | Creates and starts the core and what is on | Docker running |
| `make init` | Creates what a fresh clone lacks: `.env`, the demo certificates, the plugin folders and `.lab/on.yml` | Python 3.9 or later, `openssl`, Docker with Compose 5.0 or later. Docker does not have to run |
| `make start` | Starts stopped containers | Docker running |
| `make stop` | Stops all containers, keeps data | Docker running |
| `make restart` | Restarts containers (`SVC=name` for one service) | Docker running |
| `make down` | Removes all containers and networks, keeps the data volumes | Docker running |
| `make reset` | Removes containers and data volumes, and empties what the seeds recorded (`.lab/catalogs/<catalog>/playgrounds/<id>/.state/created.json`). Asks you to type `yes` first | Docker running |
| `make ps` | Shows container status and health | Docker running |
| `make logs` | Follows the logs (`SVC=name` for one service) | Docker running |
| `make config` | Validates the core and what is on. `SVC=name` prints that service's final configuration | Compose 5.0 or later |
| `make pull` | Pulls the images in use, which refreshes moving tags such as `latest` | Docker running |
| `make mongo` | Opens a `mongosh` shell in the MongoDB container | Docker running |
| `make certs` | Regenerates the TLS and mTLS demo certificates in `certs/mtls/` | `openssl` |
| `make urls` | Prints what is on and the URL of every console | Python 3.9 or later, Compose 5.0 or later |
| `make setup` | Makes `.venv` with the seeder's requirements and `ruff`. `PYTHON3=python3.12 make setup` picks the Python | Python 3.10 or later with `venv`, internet |
| `make check` | Checks the core (images, restart policies, labels, comments, literal secrets, loopback ports) and the agent wiring. An installed catalog is checked with `python3 lab-ui/catalog.py check ID` | Python 3.9 or later, Compose 5.0 or later |
| `make lint` | Checks the Python code with `ruff` | `make setup` first |
| `make test` | Runs the unit tests of the Lab UI, of the tooling (`.github/tests`) and of the seeder | Python 3.9 or later |
| `make e2e` | Starts an add-on, a setup or a playground from nothing, checks it works, and removes what it made: `make e2e ID=playground:acme-shop`, and `TESTED=0` for the first set of versions it was tested with. CI runs it; it refuses a machine that holds a lab | Docker running, a machine with no lab, the license in `.env` for what needs it |
| `make smoke` | Checks the Lab UI against the running stack, read only | Python 3.9 or later, the platform running |

Notes:

- `make ui` and `make all` run `make init` first. It checks the tools, then creates what a fresh clone lacks: `.env`
  from `.env.example`, the demo certificates (`certs/mtls`), the plugin folders and
  `.lab/on.yml`. Run it again whenever you like: it creates only what is missing.
- `make stop` and `make down` go by the project name, so they also reach the containers of add-ons you turned off that
  still run. What `make stop` stops stays stopped when Docker restarts.
- `make reset` also deletes the data of add-ons that are off. The Containers page's *Reset* does the same, and also
  turns off the playground that is on.
- `make test` runs the page tests only when Node is recent enough (see [Set up your machine](../CONTRIBUTING.md#set-up-your-machine)),
  and the seeder's tests only when `requests`, `pyyaml` and `redis` import, which `make setup` installs.
- When `.venv` exists, `make` uses its Python. Otherwise it uses `python3`, or the program `PYTHON3` names.
- `make setup` stops when its Python is older than 3.10. It removes the `.venv` it made and says to run it again with a
  newer `python3`. A `.venv` that was there before is left alone: remove it yourself.

## License

A Gravitee Enterprise license unlocks the Enterprise features of the products, in tiers and packs. An item that needs
one says so on its page. Without a license the Community Edition runs API
Management, Access Management and Gamma's community features; the Alert Engine needs an Enterprise license, and the Lab
UI does not start it without one. Whatever else needs a license says so. To get a license, see
[What needs a license](installation.md#what-needs-a-license).

The license lives in `.env` as `GRAVITEE_LICENSE`, a single line of base64. The APIM and AM Management APIs and
gateways and the Alert Engine read it. The hybrid gateway reads only its own, `HYBRID_GATEWAY_LICENSE`, in its add-on's
settings. `.env` is git-ignored and readable by you only.

The Alert Engine stops at once without a license. The Lab UI does not start it and shows *Needs a license* on its row on
the Containers page; one that you start by hand keeps restarting, because Docker starts it again. See
[Alert Engine keeps restarting](troubleshooting.md#alert-engine-keeps-restarting).

To add it in the Lab UI:

1. Open the License dialog: choose the *Community Edition* button on the core line of the Containers page, or any *Set license* button.
2. Choose *Add license*, or *Change license* when one is set.
3. Drop the file, choose it (the file window of the system lists `.txt` and `.key` files), or paste its text, then choose *Save*.

To add it by hand, put the license text on one line in `.env`. If your license is a binary file, encode it first:

```bash
base64 < license.key | tr -d '\n'
```

Saving changes `.env` only. The containers read the license when they are created, so apply it: the heads of
Containers and the Dashboard, the banner and the rows say *Apply needed* until you choose *Apply*.

The Lab UI only reads the license, on your machine, and sends it nowhere. It checks:

| Check | What it tells you |
|---|---|
| Readable | A text that is no license is *Unreadable* |
| Signature | Only a license signed by GraviteeSource is *Valid*. Any other is *Invalid signature* |
| Expiry | An expired license is *Expired* and Enterprise features are off. The banner warns 30 days before |
| Tier, packs, features | The license model inside the product images says what the license gives. Until an image is pulled, the dialog says `Not known until <image> is pulled` and offers *Pull images* |

The products enforce the license. The Lab UI uses what it reads to warn before a start: *Needs a license*,
*License expired* or *Not in the license* on a row, with the packs that give the feature. Start waits when all of what
the row runs needs it, and says why; when only a step of a playground needs it, the row names the step and still starts.
When the license cannot say what it allows, nothing is refused. The dialog hides the company,
the contact and the license ID until you ask, so you can share your screen.

## Images and versions

Every image has a default version in the compose file that runs it. A `.env` only overrides it, and holds a line only
for a value that differs from the default: the core's `.env` for the platform, and for an add-on or a playground the `.env` of its folder in the catalog's install
(`.lab/catalogs/<catalog>/addons/<id>/.env`), which the Lab UI writes.

The core's variables:

| Component | Version | Registry | Default |
|---|---|---|---|
| API Management (Management API, Console, Portal, Gamma Console) | `APIM_VERSION` | `APIM_REGISTRY` | 4.13.1 |
| API gateway | `APIM_GATEWAY_VERSION` | `APIM_GATEWAY_REGISTRY` | the APIM version with `-debian`, and the APIM registry |
| Access Management | `AM_VERSION` | `AM_REGISTRY` | 4.13.1 |
| Alert Engine | `AE_VERSION` | `AE_REGISTRY` | 3.0.3 |
| MongoDB | `MONGODB_VERSION` | | 8.0.34 |
| Elasticsearch | `ELASTICSEARCH_VERSION` | | 9.5.5 |
| Redis | `REDIS_VERSION` | | 8.10.2-alpine |

The default registry of the Gravitee images is `graviteeio`, on Docker Hub. Each Gravitee component has its own
registry variable, so components can come from different registries. A component can follow another instead of having
its own version: the gateway follows API Management, and a viewer of Elasticsearch follows it, until you set its own variable.

### The Images page

The Images page picks the version of every component from the tags its registry lists, and the registry of the Gravitee
ones. It writes the `.env` files for you.

- The components are listed by product, the platform's first. The image of an add-on that runs a gateway of a product comes
  right after that product's own, with the product under its name: the Hybrid gateway's, *API Management*. Its version is
  the add-on's own (`HYBRID_GATEWAY_VERSION`, in its own `.env`).
- **Save** writes `.env` only. **Save & apply** says which running containers it recreates, then recreates them.
- A version that is saved and not applied says *Apply needed* until you choose *Apply*.
- A version that other images take, such as API Management's and Access Management's, lists plain tags only, with no
  *Variant* control: the images made from it, such as the gateway's `-debian`, add their own suffix to the tag.
- A playground can need a later version than the default, such as pre-release builds of API Management and Access
  Management, chosen here. Its Start button names every image that is too old, in one
  sentence, so one visit is enough.
- **Pull images** pulls the images in use. A tag that moves is refreshed with `make pull && make all`, or with *Update*
  on the Containers page.
- Switching versions on existing data can fail on migrations. `make reset` starts clean and deletes the data.

### Settings and secrets

The key button of an add-on or a playground opens its settings as a form. The form comes from the compose file:

- Every `${VAR}` of the file is a field. A comment on its line, or heading its block, is the description you see.
- A name with `TOKEN`, `SECRET`, `PASSWORD`, `LICENSE` or `_KEY` goes under *Secrets*, the others under *Settings*.
- A variable without a default is *Required*. An add-on that lacks one shows *Settings* instead of *Start*.
- Every demo password, secret, token and key is one of these variables, with its public demo value as the default: [Logins](#logins) lists them. Changing one is a line in a `.env`.
- Image versions and registries stay on the Images page.

Names follow one rule. Prefix an item's own settings with its id (`ACME_SHOP_*`, `FEDERATION_BRAVO_*`). Image version and registry variables follow the component (`OTEL_COLLECTOR_VERSION`,
`SMTP4DEV_VERSION`), and a setting that names a host service keeps its common name (`OLLAMA_URL`, `GEMINI_API_KEY`).

### Built here, or pulled

What a playground builds (its apps and its seed), and the add-ons that no vendor publishes an image for, are built on
your machine by default. A setting per item, `<ID>_PULL_POLICY`, switches it to the images that CI builds; the item's README says its default and
which of its images are built from another project's source:

- `build` builds the image at each start, in seconds from Docker's cache.
- `never` builds it once and reuses it.
- `always` pulls the image CI built.

The Lab UI never forces a build.

The CI of a catalog builds each image its add-ons and playgrounds build, for `linux/amd64` and `linux/arm64`, after a change
on `main`, and pushes them to the registry prefix its `catalog.yml` names (`images`) as `latest` and `sha-<commit>`. The
Lab's own CI does the same for the seeder. If a pull is refused, the lab builds the image here.

### Gravitee's private registry

Gravitee's private registry on Azure, `graviteeio.azurecr.io`, is open to Gravitee employees. An item that pulls
from it says so in its README, and does not start without access.

1. Install the Azure CLI and run `az login` with your Gravitee account.
2. Start the item. When Docker is refused, its row says *Not logged in to Azure*, and its ⋯ menu has *Log in*.
3. On the Images page, a component that is on and pulls from an Azure registry shows the registry and *Log in*. *Log
   in* runs `az acr login`, so that Docker can pull. The page lists the tags of an Azure registry with your `az login`
   session.

To pull other Gravitee components from that registry, pick or type it in the registry field of the component on the
Images page.

## Ports

Every port is published on `127.0.0.1`, so only this machine reaches it. Nothing is reachable from other machines. To
open a port to the network, edit its line in the file of the service, for example from `"127.0.0.1:8083:8083"` to
`"8083:8083"`. The demo logins are public, so do this only on a network you trust. the check of a catalog refuses it in
an item of a catalog.

`–` means the lab sets no login. A login is the default one: [Logins](#logins) names the setting that changes it, when there is one.

| Port | Service | What it is | Login |
|---|---|---|---|
| 6379 | `redis` | Redis: rate limit counters, caches, vector stores | Password `redispassword` |
| 8072 | `apim-management-api` | Integration controller that API Federation agents connect to | A personal access token of APIM |
| 8081 | `am-management-ui` | AM Console | `admin` / `adminadmin` |
| 8082 | `apim-gateway` | API gateway, HTTP entrypoint | – |
| 8083 | `apim-management-api` | APIM Management API, which also serves Gamma | `admin` / `admin` |
| 8084 | `apim-management-ui` | APIM Console | `admin` / `admin` |
| 8085 | `apim-portal-ui` | Developer Portal | `admin` / `admin` |
| 8086 | `gamma-ui` | Gamma Console | `admin` / `admin` |
| 8092 | `am-gateway` | AM gateway: sign-in pages, OAuth 2.0 and OpenID Connect | – |
| 8093 | `am-management-api` | AM Management API | `admin` / `adminadmin` |
| 8443 | `apim-gateway` | API gateway, HTTPS and mTLS entrypoint | A client certificate, when the API asks |
| 9000 | Lab UI | The Lab UI, started by `make ui` | – |
| 9092 | `apim-gateway` | The gateway's Kafka entrypoint, TLS | Depends on the API |
| 9200 | `elasticsearch` | Elasticsearch HTTP API | – |
| 27017 | `mongodb` | MongoDB | – |

The ports of an add-on or a playground are in its README and on its page in the Lab UI. The services that are not listed
there, such as the Alert Engine and the databases of add-ons, listen on Docker's internal network only.

A start fails with `port is already allocated` or `address already in use` when another program holds one of these.
See [A port is already allocated](troubleshooting.md#a-port-is-already-allocated). The lab's ports are fixed because the
consoles call each other at these addresses.

## TLS and mTLS

`make init` creates demo certificates the first time, and `make certs` creates new ones. Both write to
`certs/mtls/`, valid for 10 years. The password of the two keystores is the setting `MTLS_KEYSTORE_PASSWORD`, `gravitee`
by default:

| File | What it is |
|---|---|
| `ca.pem`, `ca-key.pem` | The local certificate authority, "Gravitee Lab Local CA" |
| `server-cert.pem`, `server-key.pem`, `server.p12` | The gateway's certificate for `localhost` and `127.0.0.1`, and its keystore |
| `client-cert.pem`, `client-key.pem`, `client.p12` | A client certificate for mTLS |

`make certs` replaces them. Restart the gateway to load the new ones: `make restart SVC=apim-gateway`.

To change the keystore password, set `MTLS_KEYSTORE_PASSWORD` in `.env` or in the shell, run `make certs`, which reads
it from there, then run `make all`: it recreates the gateway with the new password. A restart keeps the old one.

The gateway listens on `https://localhost:8443`. It asks for a client certificate but does not require one: use the
mTLS plan or policy of an API to enforce it.

```bash
curl --cacert certs/mtls/ca.pem https://localhost:8443/my-api
```

```bash
curl --cacert certs/mtls/ca.pem --cert certs/mtls/client-cert.pem --key certs/mtls/client-key.pem https://localhost:8443/my-api
```

To stop a browser from warning you, import `certs/mtls/ca.pem` as a trusted authority.

## Plugins

The Plugins page shows, for APIM and for AM, the catalog of download.gravitee.io next to what the product's images
bundle and what you added.

- Tick a plugin and choose its version, then choose *Apply*. Apply says first what it installs, removes and restarts.
- The page downloads everything first and checks the SHA-1 when one is published. Only then does it stop the product,
  replace the files and clear the unpack cache. The product loads the plugins when it starts again.
- *Refresh* reads the catalog from the download site again.
- A plugin that needs a license feature shows *Needs a license*, *License expired* or *Not in the license*.
- A plugin is of the Enterprise Edition, the *EE* tag after its name and under the *Enterprise* filter, when download.gravitee.io
  publishes it in its Enterprise folder or when it needs a license feature, which it declares itself and is read once it is installed: every tier of the license
  is an Enterprise one. Any other plugin is under *Community*.
- A playground that requires plugins installs the missing ones when you start it.

Added plugins are zip files in `plugins/apim/` and `plugins/am/` (git-ignored). Both folders are mounted into the
product's containers. Without the Lab UI, drop a zip in the folder and run `make restart`.

A plugin found twice, bundled in the image and added in the folder, loads from the most recent file, not from the
highest version. The page marks an added plugin *Added, ignored* when the bundled one is newer.

`make init` creates the plugin folders writable by everyone, because the containers unpack plugins there as user 1001.

## Data and volumes

| What | Where | Back up or clear |
|---|---|---|
| Core data: MongoDB, Elasticsearch, Redis | Docker volumes `gravitee_data-mongo`, `gravitee_data-elasticsearch`, `gravitee_data-redis` | `make reset` deletes all |
| Add-on data | Docker volumes named `gravitee_data-<add-on>` | `make reset` deletes all |
| What is on, and your stars | `.lab/on.yml`, `.lab/starred.json` | Copy `.lab/` |
| The core's versions, license and passwords | `.env` | Copy the file |
| Add-on and playground settings | `.env` in the folder of each item, `.lab/catalogs/<catalog>/addons/<id>/.env` | Copy `.lab/` |
| Credential files | `secrets/` in the folder of an item, such as a service account key | Copy `.lab/` |
| Added plugins | `plugins/apim/`, `plugins/am/` | Copy the folders |
| The catalogs and your own items | `.lab/catalogs/`, your own in `.lab/catalogs/local/` | Copy `.lab/` |
| Settings of a catalog you removed and kept | `.lab/held/` | Copy `.lab/` |
| Demo certificates | `certs/mtls/` | `make certs` makes new ones |
| What a seed created | `.lab/catalogs/<catalog>/playgrounds/<id>/.state/created.json` | `make reset` empties it |

All of these are git-ignored. Never commit them.

To back up a volume, stop the lab first, then copy the volume to a file. This one writes `mongo.tgz` in your home
folder, outside the clone, so that a database never ends up in a commit. Use another folder by changing `"$HOME"`. On
Linux the file belongs to root.

```bash
make stop
```

```bash
docker run --rm -v gravitee_data-mongo:/data:ro -v "$HOME":/backup alpine tar czf /backup/mongo.tgz -C /data .
```

To restore it into an empty volume, swap the direction:

```bash
docker run --rm -v gravitee_data-mongo:/data -v "$HOME":/backup alpine tar xzf /backup/mongo.tgz -C /data
```

To clear one volume, remove the containers, then the volume:

```bash
make down
```

```bash
docker volume rm gravitee_data-mongo
```

The database hosts come from `gravitee_ds_mongodb_host` and `gravitee_ds_elastic_host` in the core file. Every
repository URI is derived from them.

## What needs the internet

| What | From | When |
|---|---|---|
| Container images | Docker Hub, `docker.elastic.co`, and `ghcr.io` for the images CI builds | The first start, `make pull`, *Update* |
| Image tags | `hub.docker.com` and the registries' own APIs | The Images page, and the Dashboard for the latest Gravitee versions |
| Plugins | `download.gravitee.io` | The Plugins page, and a playground that requires a plugin |
| AI models of the gateway | `huggingface.co` | An item that uses them, when the gateway first deploys the LLM proxy. A volume of the item keeps them |
| Packages and source for local builds | PyPI, npm, Docker Hub, and GitHub for the apps that build from a repository | When an app or the seeder is built here |
| Gravitee's private registry | `graviteeio.azurecr.io`, with `az login` | An item that pulls from it |
| Gravitee Cloud | `eu.cloud.gravitee.io` for the Gravitee Cloud page, `eu.cloudgate.gravitee.io` for the hybrid gateway, for a Cloud Token and for the seed of a deploy | An account on the Gravitee Cloud page, the Hybrid gateway add-on, a playground deployed to Cloud: [Use Gravitee Cloud with the Lab](cloud.md) |
| The Lab UI's libraries, in your browser | `cdnjs.cloudflare.com` and `cdn.jsdelivr.net` | A guide with diagrams, the file editor |
| Hosts an item calls | Named in its README | Only when you use the item, or set the key it asks for |

Ollama models are pulled by `ollama pull`, outside the lab.

The license is checked on your machine. The Lab UI, the seeder and the playground apps send no analytics or usage data,
and the apps of the Lab's own catalog load nothing from outside, unless their README says so. The Gravitee products and other images the lab runs keep their own
behavior.

Once the images are pulled and the apps are built, the platform runs without internet. See
[Corporate proxy or offline use](troubleshooting.md#corporate-proxy-or-offline-use).

## Logins

These logins are public: the Gravitee images ship them as defaults, or the compose files hold them as the defaults of
settings. They are for your machine only. Do not reuse them anywhere else.

| Where | User | Password | Setting |
|---|---|---|---|
| APIM Console, Developer Portal, Gamma Console, APIM Management API | `admin` | `admin` | |
| Other APIM users | `user`, `api1`, `application1` | `password`, `api1`, `application1` | |
| AM Console, AM Management API | `admin` | `adminadmin` | |
| Internal APIs of the Gravitee components (health checks, inside the containers only) | `admin` | `adminadmin` | `INTERNAL_API_PASSWORD` |
| Redis | | `redispassword` | `REDIS_PASSWORD` |

The `admin` of the APIM and AM consoles comes from the products' images, not from a setting. If you change that password
in a console, set the sign-ins that the items use to match: each item's README lists its own settings, such as
`<ID>_APIM_PASSWORD`.

### Other demo secrets

Secrets that are not a sign-in. Each has a default and a setting that changes it.

| Where | Value | Setting |
|---|---|---|
| Alert Engine connection (the gateway and the Management API log in to it) | `admin` / `adminadmin` | `INTERNAL_API_PASSWORD` |
| Keystores of the demo certificates (`certs/mtls/server.p12`, `client.p12`) | `gravitee` | `MTLS_KEYSTORE_PASSWORD` |

MongoDB and Elasticsearch have no login. The logins and secrets of the add-ons and the playgrounds of a catalog are in
their READMEs, and the page of each item in the Lab UI lists its accounts, with copy buttons.

### Change a password

Set the setting in the shell, in `.env` for the core, or in the `.env` of the add-on or the playground it belongs to
(the key button of an item writes that one), then apply it with *Save & apply* or `make all`. A restart keeps the old
value, because a container reads its settings when it is created.

- **A database keeps its password.** Set it before the first start: a database or a broker stores the credentials it
  was created with in the volume of its add-on. For a later value, remove the add-on's volume, which deletes its data:
  see [Data and volumes](#data-and-volumes).
- **A keystore password is inside the keystore.** Make the certificates again: see [TLS and mTLS](#tls-and-mtls) for the
  gateway's, and the README of the add-on that has its own. The certificate scripts
  read the password from `.env` with a simple parser: it must not contain quotes, backslashes, `$` or an inline comment.
- **A playground can write its passwords, secrets and keys into the products** when its seed creates them. Set them
  before the first start, or reset: the Settings of its README say which.

## For Gravitee employees

Set `GRAVITEE_EMPLOYEE=true` in `.env` and the License dialog no longer invites you to a Gravitee Cloud account or a license request.
