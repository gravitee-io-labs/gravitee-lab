# Gravitee Lab

Gravitee Lab runs the Gravitee platform (API Management, Access Management, Alert Engine, Gamma and their databases)
on Docker Compose, with ready-made scenarios on top, started from a web page, the Lab UI. It is for anyone who tries,
demos or learns Gravitee on a laptop, not for production. This file is the contract for everyone who changes the
project, people and AI agents alike. The process (branches, pull requests, CI) is in
[CONTRIBUTING.md](CONTRIBUTING.md#workflow).

## Layout

| Path | What it is |
|---|---|
| `docker-compose.yml` | The core: the platform and its databases. See [The core](#the-core) |
| `.env.example`, `Makefile` | The template that `make init` copies to `.env`; every command (`make help`) |
| `install.sh` | macOS and Linux from nothing to the Lab UI: the tools (Apple's command line tools, or the package manager's), the clone, the command `gravitee-lab` on the PATH |
| `install.ps1` | Windows from nothing to the Lab UI: WSL, a distribution and its tools, Docker Desktop serving it, the clone, a Start menu shortcut |
| `bin/gravitee-lab` | The command `install.sh` links into `~/.local/bin`: alone it opens the Lab UI, with a word it runs that target of the Makefile |
| `.lab/catalogs/<id>/` | Machine-local. A catalog installed whole (`addons/`, `setups/`, `playgrounds/`, `catalog.yml`), read only: an update replaces it. The default one is Gravitee public; [Catalogs](docs/catalogs.md) |
| `seeder/` | The engine that creates a playground's data in Gravitee: the `lab_seeder/` package, its image and its tests |
| `lab-ui/` | The Lab UI: `server.py`, `catalog.py` (the command of the catalogs), the `lab_ui/` package (Python standard library), the page in `web/` (no build step), `templates/` (what a new catalog is made from) and `tests/` |
| `certs/mtls/` | `generate.sh`, which makes the TLS and mTLS demo certificates (`make certs`); the files it writes are git-ignored |
| `docs/` | For people: [installation.md](docs/installation.md) (prerequisites, first start, licenses, stop and remove), [lab-ui.md](docs/lab-ui.md), [configuration.md](docs/configuration.md), [playgrounds.md](docs/playgrounds.md) (writing a playground), [troubleshooting.md](docs/troubleshooting.md), [maintaining.md](docs/maintaining.md) (moving or forking the repository, the one-time GitHub settings), [cloud.md](docs/cloud.md) (using Gravitee Cloud with the Lab) |
| `README.md`, `CONTRIBUTING.md`, `SECURITY.md` | What the lab is and where to start; the contribution process; the vulnerability policy and what the lab exposes |
| `LICENSE`, `NOTICE` | AGPL-3.0, and the attribution term that NOTICE adds |
| `.github/` | CI, the end-to-end workflow (`e2e.yml`, with the scripts that plan its jobs and check that images exist), the workflow and script that publish the images the lab builds, the Compose action, issue and pull request templates, Dependabot, and `tests/` for the Makefile, the scripts, the workflows' rules and the Compose conventions |
| `.agents/skills/<name>/SKILL.md` | A skill: what an AI agent follows to create an add-on, a setup or a playground, to share a personal one, or to prepare a pull request |
| `.claude/skills/<name>/SKILL.md` | The stub that Claude Code reads: the skill's `name` and `description`, then one line that sends to the skill |
| `AGENTS.md`, `CLAUDE.md`, `.gemini/settings.json` | The guide of a folder; next to each, a `CLAUDE.md` holding the single line `@AGENTS.md`; the setting that makes Gemini CLI read `AGENTS.md` |
| `ruff.toml`, `.editorconfig`, `.gitattributes`, `.gitignore` | Lint settings, editor settings, LF line endings, what git ignores |
| `.env`, and the `.env` of each installed item (beside its copy) | Machine-local. The core's secrets and the image versions chosen here, and the tokens of the registered platforms (`PLATFORM_<ID>_*`: the Account Token, a Cloud Token for each environment, a token for each gateway the Lab created); the settings of an add-on, among them the token of the hybrid gateway that runs here (`HYBRID_GATEWAY_CLOUD_TOKEN`), and of a playground |
| `.lab/` | Machine-local. The Lab UI's files: the on file `on.yml` (an `include` list of what is on, read by Compose with the core through `COMPOSE_FILE` in `.env`), `starred.json`, `platforms.json` (the registered Gravitee platforms: name, kind, address and account, with no secret; their tokens are `PLATFORM_<ID>_*` settings in `.env`) and `core.json` (where API Management runs: This machine or a Gravitee Cloud environment) |
| `.lab/catalogs/local/` | Machine-local. The personal items of this machine, which no catalog touches |
| `plugins/`, `.venv/`, and `secrets/` and `.state/` in the folder of an installed item | Machine-local. Plugins mounted into the products; the environment `make setup` makes; an add-on's credential files; what a seed created (`created.json`, and one per platform environment it deployed to, in `.state/<platform id>/<environment id>/`) |

## Catalogs

Items are in catalogs, not here. Before you change what a unit may do or how the Lab judges one, read
[the contract](docs/contract.md), [the format of the units](docs/units.md) and [Catalogs](docs/catalogs.md).

## Personal and shared

An item is personal or it is in a catalog. A personal add-on, setup or playground is kept on this machine, in
`.lab/catalogs/local/` (`addons/`, `setups/`, `playgrounds/`), which git ignores. It shows as Personal in the Lab UI and has
the id `local/<id>`, so `local` is not an id. An item of a catalog is read only here, and its id is the catalog's: bare for
the default catalog, `@<catalog>/<id>` for another.

- A personal item and a catalog item sit at the same depth, so the same relative paths work in both. An add-on includes an
  add-on of its catalog as `../kafka/compose.yml`, a setup as `../addons/kafka/compose.yml` and a playground as
  `../../addons/kafka/compose.yml`; one of another catalog, such as the default one, is
  `../../../<catalog id>/addons/kafka/compose.yml` from an add-on; a playground's seed builds `../../../../../seeder`.
  [The format of the units](docs/units.md) gives the depth of each kind.
- An item of a catalog includes what its catalog holds, or what the catalogs it declares in `requires.catalogs` hold, and never
  a personal item, because what a catalog holds runs on any machine. A personal item includes what it wants.
- Create an item from the Lab UI ("New add-on", "New setup", "New playground", by form or with an AI agent) or with the
  skills `new-addon`, `new-setup` and `new-playground`, in Personal or in a catalog that is a folder of yours.
- Share a personal one with the Lab UI's Share or the `share-item` skill: it is copied into a catalog you can write to, as a
  branch to propose with a pull request or as a copy into a folder. The personal item stays and runs until "Use the catalog
  version" replaces it. A playground is shared once it has started well here. An item of a catalog is not made personal again;
  create a personal one for a private variant.
- The Lab UI runs your own git only to push that branch, with your name and email. It never commits to your work.

## Rules

- **Standard Compose only.** `include`, `x-` extensions, labels and `${VAR:-default}` carry everything, so a person
  running `docker compose` gets what the Lab UI gets. The Lab UI knows no product: no list of products, add-ons or
  images in its code.
- **One way for each thing.** Before you add a piece, find the one that does it already.
- **Comments say why.** A comment describes a setting or a non-obvious reason, never history or authorship. The comment
  at the end of the line that uses a variable, or heading its block, is the help the Lab UI shows next to that setting.
- **No secrets in git.** `.env` files, `.lab/` (the catalogs, with the machine files of each item) and `plugins/` are ignored. A
  tracked file holds demo defaults only, never a real token, password, key or license.
- **No hardcoded password.** Every password, secret, token and key in a compose file is a setting, never a literal:
  `${NAME:-default}`, whose default may be a public demo value, or `${NAME}` when the user must provide it. In a
  file of a catalog, its check refuses a literal in a key whose name contains `PASSWORD`, `PASSWD`, `PASSPHRASE`, `SECRET` or
  `API_KEY` or ends in `_TOKEN`, in the `password` and `key` of `x-gravitee.accounts`, and in a URI
  (`postgres://user:password@host`). It cannot see a secret under another name: make it a setting too. A script that
  puts a secret in a file, as the `generate.sh` of the certificates does, reads the same setting, from the environment
  or the `.env`. The one literal it accepts is the demo sign-in a console shows (`io.gravitee.console.<id>.password`),
  which sets nothing: the core's consoles say the password their image comes with. A console whose password is a
  setting says that setting.
- **Seeds use endpoints, not container names.** Seed files hold `${VAR}` for every address and the seed service sets
  them, because the seeder reads every endpoint from its settings. `make test` fails on a literal address.
- **Names.** An id is lowercase letters, digits and dashes. Service names are global to the project, because services
  of one name in two files merge: a playground's services carry its prefix (`shop-api`, not `api`), and an add-on's the
  name of what they run (`kafka-ui`, `smtp4dev`). Prefix an item's own settings with its id (`ACME_SHOP_*`,
  `FEDERATION_BRAVO_*`, `MCP_LENS_*`). Image version and registry variables follow the component
  (`OTEL_COLLECTOR_VERSION`, `SMTP4DEV_VERSION`), and a setting that names a host service keeps its common name
  (`OLLAMA_URL`, `GEMINI_API_KEY`).
- **Descriptions.** An item's `x-gravitee.description` is a noun phrase without a leading article or final period that
  says what the item adds ("Browser for what the gateway sends to Acme"): it is the text of its row in the catalog.
- **File types.** Seed data is `.yaml`, except API exports (`seed/apim/apis/*.json` and `seed/environments/<id>/apis/*.json`). Compose and configuration files
  are `.yml`, one `compose.yml` per item folder.
- **Short copy.** Lab UI text is sentence case, with no helper sentence that restates the obvious. Documentation is
  American English with sentence-case headings and imperative sentences; diagrams are Mermaid.
- **Portable.** Scripts are POSIX `sh` with LF line endings and use no GNU-only option on the host (macOS ships BSD
  tools). The Makefile runs with make 3.81. `install.sh` and `bin/gravitee-lab` are POSIX `sh` too, and `shellcheck -s sh`
  says nothing of them. Windows is supported through WSL 2 only: `install.ps1`, in ASCII for Windows PowerShell 5.1, sets it
  up and runs the rest in the distribution.

## The core

`docker-compose.yml` is the platform and its databases. Every image and secret is a setting with a default, so it runs
without `.env`: [Logins](docs/configuration.md#logins) lists the secrets. Add-ons, setups and playgrounds are included
on top of it and change its services only by the keys they need.

- `x-gravitee` holds `name`, `groups` (the sections of the Containers page, in order) and `categories` (the values an item's
  `category` may take).
- A service has the labels `io.gravitee.name`, `io.gravitee.description` and `io.gravitee.group` (one of `groups`);
  `make check` asks for the last two. A UI to open adds `io.gravitee.console.<id>.name` and `.url`.
- An image is `${NAME_VERSION:-tag}`, and a Gravitee image `${NAME_REGISTRY:-graviteeio}/repository:${NAME_VERSION:-tag}`.
  The Images page finds each component from these lines and writes the chosen version to `.env`, so a version never goes
  in code. The variables: [images and versions](docs/configuration.md#images-and-versions).
- `x-ui-frames` is the `command` of the consoles that the Lab UI shows in frames next to a guide. It removes
  `X-Frame-Options` and lets `http://localhost:9000` frame the page. A platform service listed in a playground's
  `consoles` needs it, or a setting of its own that names `http://localhost:9000` and `http://127.0.0.1:9000` as the pages that
  may frame it (the Developer Portal's `ALLOWED_FRAME_ANCESTOR_URLS`), which is better when the image has one.
- Every published port binds `127.0.0.1` and has a comment that says what it is. `edge` carries what users and API
  consumers reach, `internal` the databases and supporting services.

## Commands

`make help` lists every target, and [docs/configuration.md](docs/configuration.md#make-targets) says what each does
and needs. These verify a change:

| Command | What it runs | What it needs |
|---|---|---|
| `make setup` | Makes `.venv` with the seeder's requirements and ruff | Python 3.10 or later with `venv`, the internet. `PYTHON3=python3.12 make setup` picks the Python |
| `make check` | The core file (images, restart policies, labels, comments, literal secrets, loopback ports) and the agent wiring. A catalog: `python3 lab-ui/catalog.py check ID or PATH`. Must print "All good" | Python 3.9 or later, the Docker CLI with Compose 5 or later (no running daemon) |
| `make lint` | ruff on the Python code | `make setup` |
| `make test` | The Lab UI's unit tests, then the tooling's (`.github/tests`), then the seeder's | Python 3.9 or later. Page tests: Node ([Set up your machine](CONTRIBUTING.md#set-up-your-machine)). Seeder tests: `make setup`. Each is skipped without its need |
| `make e2e` | `ID=playground:acme-shop [TESTED=0]`: starts the item from nothing, checks it works, removes what it made. CI runs it | Docker, a machine with no lab: it refuses one that holds a lab |
| `make smoke` | The Lab UI against the running stack, read only | The stack running |

`make check` prints what is wrong file by file; `lab-ui/lab_ui/library/check.py` is the list of what it enforces. A skip line in
a test run is not a pass. Try what runs from the Lab UI (`make ui`) on a fresh clone, as a new user would:
[CONTRIBUTING.md](CONTRIBUTING.md#test-your-change-like-a-new-user).

## Working as an agent

- **Ask before you delete data**: `make reset`, `make down` and `docker compose down -v` remove the containers or the
  data of a lab that may be running. `make check`, `make test` and `make lint` need no running stack.
- **Never print or commit** `.env`, a license, a token or anything under `secrets/`. Do not edit machine-local files
  (`.env`, `.lab/`, `plugins/`) unless asked: the Lab UI writes them.
- **Read before you run** a pull request, a personal item or a playground that someone else wrote: starting it runs
  their containers, Dockerfiles, seeds and Makefile code with the person's Docker access
  ([Review before you run](CONTRIBUTING.md#review-before-you-run)).
- **Commit only when asked**, with a plain imperative subject, and add no tool-generated trailer or credit to a commit,
  a pull request or a file.
