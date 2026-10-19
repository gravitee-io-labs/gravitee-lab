# Lab UI

The Lab UI (`make ui`, http://localhost:9000) is a local page that runs the lab without the terminal. It reads the
project's Compose files, shows what is on and what runs, turns add-ons, setups and playgrounds on and off, and starts or
stops containers with `docker compose`. It edits the same files a person would, and listens on `127.0.0.1` only. It is a
small helper tool: when in doubt, do less.

| Read | For |
|---|---|
| [DESIGN.md](DESIGN.md) | The interface conventions, the state model, the checks to click through |
| [README.md](README.md) | How to run it, its options and its tests |
| [docs/lab-ui.md](../docs/lab-ui.md) | What it does for a person: update it with the behavior |

## Layout

The module docstring (Python) or the header comment (JavaScript) of a file is the detail. `server.py` is the entry point, and
`catalog.py` the command of the catalogs. The code is the `lab_ui/` package, a package for each domain.

| `lab_ui/` | What it holds |
|---|---|
| `paths.py` | The project's layout: the folder decides what a file is and gives its id; the API definitions of a seed, in `seed/apim/` and in each `seed/environments/<id>/` (`seed_apis`, which the checks, the license and the plugins a Cloud environment needs all read); `write_atomic` |
| `e2e.py` | `make e2e`: runs this server on a free port and starts one item from nothing through it, as the page does; says whether it works (the start and its seed, the containers up, the consoles answering) and removes what it made. Refuses a machine that holds a lab. Exit 0 it works, 1 it does not, 2 it cannot be tried here |

`lab_ui/docker/`: What the Lab UI asks of Docker and of the files around it.

| Module | What it holds |
|---|---|
| `compose.py` | Runs `docker compose` and reads its answers: files as JSON (a `pull_policy` variable is read as its default, which Compose 5.5.1 and later refuse), containers, `config --hash`, images; a cache |
| `envfiles.py` | `.env` files, read and changed the way Compose reads them |
| `files.py` | The editor's files (read, check, save) and a `.env` as a form |
| `images.py` | Components read from `image:` lines, their versions, registry tags and login |
| `jobs.py` | Long-running actions: one at a time, cancelable, with their output and progress |
| `plugins.py` | The plugin catalog, what each product loads, install and remove |
| `probe.py` | What a UI answers, and whether the page may frame it |
| `pulls.py` | The progress of the images a job pulls, layer by layer, read from Compose's JSON progress |
| `terminal.py` | The shell of a playground's terminal: `docker exec -it` on a pseudo-terminal that the Lab UI keeps, one for each playground, read from an offset as an event stream and written to by POST |

`lab_ui/gravitee/`: The Gravitee platform and where it runs.

| Module | What it holds |
|---|---|
| `cloud.py` | Gravitee Cloud: the account API called with an Account Token, Cloud Gate called with a Cloud Token, and what a token says of itself |
| `core.py` | Where API Management runs (`.lab/core.json`): This machine or a linked environment of a Gravitee Cloud account, chosen and checked; its group of the platform (`group`, the product of the Cloud's gateway scope); with it in the Cloud, what the Dashboard's Start all starts (its hybrid gateway, the rest of the platform with what it depends on, what is on; not API Management's services nor what only they use, read from `depends_on`) and Stop all stops, and what the state says of it |
| `frames.py` | The UIs a playground shows next to its guide |
| `license.py` | The license decoded, read only, and its status without Docker |
| `places.py` | Where a playground deploys besides This machine: the places of the platforms (read in the background, kept a minute), what each lacks, what its environment runs and which playgrounds cannot go there, the record of each, the settings of a seed |
| `platforms.py` | The remote platforms of this machine (`.lab/platforms.json`), their tokens (`PLATFORM_<ID>_*` in `.env`), the hybrid gateways created there and the swap of the one that runs here, the gateway that runs here and the platform no longer lists (`orphan`), and what an environment runs, read from its gateways (`capabilities`) |

`lab_ui/library/`: The add-ons, setups and playgrounds: how they are read, checked, laid out and turned on.

| Module | What it holds |
|---|---|
| `project.py` | The project as its Compose files describe it (`build()` is pure; `saved()` is what each service would be made as), what each playground asks of a platform, from its files (`asks()`), and why it cannot go to an environment that runs this (`judge()`) |
| `layers.py` | The on file, needs and conflicts, the plan and the job of a start or a stop, and the plan of a deploy to a place, which has no service of the core |
| `model.py` | What `/api/state` answers: state, note and action of every item. Pure |
| `reasons.py` | Why a service runs behind what is saved, as one plain line, and the line of several services or of an owner's grouped by cause. Pure |
| `drift.py` | Which settings and labels of a container differ from what Compose would set it now, the settings by name, from digests; never a value |
| `check.py` | `make check` |
| `personal.py` | Creating, sharing and deleting items of this machine; finding the skills |
| `starred.py` | What is starred on this machine (`.lab/starred.json`) |
| `tested.py` | What a playground started on here: the image versions that worked, kept in `.lab/tested.json`, so that starting it again on them is no warning |
| `playgrounds.py` | A playground's checks, guide, start, re-seed and stop, on This machine or on a place: its deploy, its delete from one place and its records |
| `layout.py` | The migration of a machine that ran the Lab before the catalogs to their layout (`make update`), and the guard that stops a command that would fail on the old one |
| `moves.py` | What moves things on this machine, shared by the migration, the working copy of a catalog, Share and the adoption of held settings: it never overwrites |

`lab_ui/server/`: The pages of the Lab UI.

| Module | What it holds |
|---|---|
| `api.py` | The HTTP handler, route table, event streams, static files, the command line |
| `dashboard.py` | What the Dashboard reads besides the state: the products' versions against the latest of their channel, the gateway of a platform against the latest stable one, Docker and the machine, the live load of the lab's containers |
| `stack.py` | The Containers page's actions on containers |

`lab_ui/catalogs/`: the catalogs, how they are installed, checked and kept ([Catalogs](../docs/catalogs.md)). `cli.py` is the command of the catalogs (`catalog.py` is its entry script), and `explain.py` writes `docs/catalog-rules.md`
(`WRITE_RULES_DOC=1 python3 lab-ui/tests/run.py tests.catalogs.test_explain_document` writes it again).

| `web/` | What it holds |
|---|---|
| `index.html`, `css/` | The shell (sidebar, one section per page, dialogs, icon sprite); colors and layout, components, one page's needs |
| `js/main.js`, `boot.js`, `core.js` | Boot and refresh; DOM helpers, `api()`, `store` (with `store.cloud`, the checks of the Gravitee Cloud accounts that the Dashboard and the Gravitee Cloud page share), drafts, the hash router, the systems of a tag (`VARIANTS`, `bareTag`) |
| `js/pages/` | One file per page: `dashboard.js`, `list.js` (the three lists), `catalogs.js`, `playground.js`, `stack.js` (Containers), `logs.js`, `images.js`, `plugins.js`, `platforms.js` (a card for each Gravitee Cloud account) |
| `js/dialogs/` | `editor.js`, `create.js`, `platform.js`, `license.js`, `welcome.js`, `catalog.js`, `layout.js`, `form.js` |
| `js/components/` | The pieces the pages share: `ui.js` (dialogs, each with its cross at the top right, made when it opens, rows, state, `actionButton`, `setOff` for a control that waits and says why, pager, the tooltip `tipHtml` and the `applyBadge`, the menu), `combo.js` (the picker, and a form's select chosen with it, `selectField`), `agent.js` (the buttons that hand a prompt to an AI agent: Claude, Claude Code, Codex, Cursor, and a copy), `popup.js` (where every dropdown opens and how big it is), `sidebar.js` (chips, banner, theme), `markdown.js` (Markdown rendered and sanitized: Mermaid diagrams, action links), `divider.js` |
| `js/platform/` | `environments.js` and `environment-actions.js` (what the Lab says of an environment of a platform, `environmentStatus`: its gateway on this machine, whether it can be deployed to, what was deployed there, the one action, the environments in use, every hybrid gateway of an account, and what a person does to one), `core-choice.js` (where API Management runs: its control `runsControl`, the environment picker, Start all and Stop all with it in the Cloud), `places.js` (where a playground deploys: the place chosen for every playground, `chosenPlace`, and the Start of a playground on a place) |
| `js/activity/` | `jobs.js` (running jobs and the activity drawer) and `pulls.js` (the progress of the images a job pulls) |
| `js/workspace/` | The page of a running playground: `split.js` (its rules, no DOM, the terminal's too), `workspace.js` (the tabs and frames), `termpane.js` (the terminal's panel: divider, bar, fold) and `terminal.js` (the shell in it: xterm.js on the output stream, input and size by POST) |

`tests/` mirrors `lab_ui/`: a folder for each package, a file for each module (`tests/library/test_project.py` tests `lab_ui/library/project.py`).
`tests/web/` holds the page's tests: `test_static.py` for its files, `js/` for its rules (`node --test`, run by `test_page.py`; `dom-stub.mjs`
is a small DOM, which `images.test.mjs` uses to run the Images page) and `gallery/`. `tests/run.py` runs them in parallel, and in shards for the CI.
The rest of `tests/` is what they share: `fixtures.py` (catalogs on disk), `fakedocker.py`, `gitfixture.py`, `data/`, `smoke.py`, and `fake_cloud.py`, a Gravitee
Cloud with its Cloud Gate to try the platforms without an account (`python3 lab-ui/tests/fake_cloud.py --port 9990`). Their cases share
`Case` (`tests/__init__.py`): `self.patch` replaces something until the test ends, `self.folder(*modules, files=...)` and `self.write` give a
test its own project folder, and `self.started(jobs)` reads the job a mocked `JOBS` started.

`tests/web/js/lists.main.txt` is the DOM that the three lists, the banner and Containers draw with no platform registered, and
`lists.test.mjs` compares it with what the pages draw now. After a deliberate change of what a lab without a platform
sees, write it again, never to make a failing test pass:

```bash
cd lab-ui/tests/web/js && WRITE_MAIN=1 node --test lists.test.mjs
```

## Rules

- **Standard library only** on the server, Python 3.9 or later: no `match`, no `X | Y` annotations. The page is plain ES
  modules and CSS: no build step, no framework, no new dependency.
- **Files are UTF-8 with LF line ends**, and the paths written in project files are POSIX (`posixpath`). A file is replaced
  whole with `paths.write_atomic`.
- **One responsibility per module**, said in its docstring. Small functions. `model.py` and `project.build()` are pure,
  so they are tested without Docker.
- **The Compose files hold the product knowledge**: `x-gravitee` blocks, labels, `include`, `image:` lines, mounts. No
  list of products, add-ons or images in `lab_ui/` or `web/`. The two explicit exceptions are the tag suffixes of
  registries (`VARIANTS` in `js/core.js`, which every page reads, and its copy in `dashboard.py`, which a test keeps equal) and the
  acronyms of license packs (`ACRONYMS` in `js/dialogs/license.js`).
- **The server computes, the page draws.** `/api/state` carries the state, note and action of every item, in the order
  they are shown.
- **Errors are never swallowed.** A file that does not load is reported in `problems` and the others still load. A failed
  request answers `{"error": message}` through the one mapping in `api.py`; a failure nothing expects is a 500 that names
  its type.
- **The terminal runs in a container, never on this machine.** The only thing the Lab UI runs that a person typed is the shell of
  a playground's `terminal` service, by `docker exec` in that container. The page reaches it through the same checks as every
  call (a local name, the page's own header on a POST).
- **Never click then error.** A disabled action says why. An action that does more than its item says so first.
- **An action does only what it says.** An item's Start or Stop acts on the item, what it needs and the core services it
  changes. Nothing is removed as an orphan.
- **The Lab UI is never framed**: every answer says `frame-ancestors 'none'`, because it frames the consoles of a
  playground and holds the keys to the lab.
- **One way for each thing.** Find the component that does it before you add one, and reuse a class of `components.css`
  before you add a class. The conventions are in [DESIGN.md](DESIGN.md#one-way-for-each-thing).
- **One rule, no exception.** What the Lab UI does is the same for every playground, state and width. When two cases
  differ, the information differs, never a special case written for one.
- **Without a platform, nothing changes.** A person with no platform registered sees the lab as it was before
  platforms: no picker, no mark, no tooltip, no line of a deploy. What a platform adds is drawn from data that exists
  only once one is registered (`state.platforms`), and `lists.main.txt` proves it.
- **The catalog never says where it deploys.** No playground or setup names a platform, and an add-on names a kind only
  to say it runs that kind's gateway here (`x-gravitee.gateway`). The Lab computes whether a playground can go
  to an environment from its files (`project.asks`), against what the gateways of that environment run
  (`platforms.capabilities`): no version or product of a platform is written in the code.
- **A platform's secret is kept, never said.** An Account Token, a Cloud Token or a gateway token is written to a `.env`
  and appears in no answer of a platform or place route, error, job line, command or record. The seed that needs a Cloud
  Token gets it by name (`-e NAME`), from the environment of that one command. The settings form of the Hybrid gateway
  add-on shows its token masked, like every secret setting.
- **Control sizes.** A button, a search, a select and a segmented control are 32 px high; a choice inside one (a segment, a
  toggle chip, an option of a search) is 26 px, like the icon buttons of a row; one text size; no small variant. A choice
  of one among several is a segmented control, an independent option is a loose toggle chip.
- **Terse UI copy**: sentence case, no helper sentences, no counters except where the number is data. The welcome dialog
  (`js/dialogs/welcome.js`) is the one place with prose. The copy makes no claim about a particular AI tool: it says "your
  agent".
- **Checked as files** (`test_static.py`): every export of `web/js` is imported, every icon of the sprite is drawn, every
  class of the stylesheets is used, every script of a CDN has the hash of its file.

## Runtime libraries

The page loads these from a CDN when it needs them, each at a pinned version with a Subresource Integrity hash
(`loadScript` in `core.js` passes it to the browser, which refuses a file that differs). For Monaco the hash covers its
loader alone: the loader fetches the editor's other files without one.

| Library | Version | From | Loaded | Without internet |
|---|---|---|---|---|
| marked, DOMPurify | 18.0.14, 3.4.16 | cdnjs.cloudflare.com | The first time a guide is shown (`js/components/markdown.js`) | The guide shows as plain text |
| Mermaid | 11.15.0 | cdnjs.cloudflare.com | When a guide has a `mermaid` block (`js/components/markdown.js`) | The block stays as written |
| Monaco | 0.52.2 | cdn.jsdelivr.net | When the editor opens (`js/dialogs/editor.js`) | A plain text area |
| xterm.js, its fit addon | 6.0.0, 0.11.0 | cdn.jsdelivr.net | When a playground's terminal is shown (`js/workspace/terminal.js`) | The panel says it needs the internet |

To change a version, compute its hash as the comment in `js/components/markdown.js` says, and update the constants of the file that
loads it.

## Run and test

```bash
make ui         # http://localhost:9000; UI_PORT=9001 make ui for another port (the UIs of a playground show only on 9000)
make test       # the Lab UI's tests (stdlib, Python 3.9+), the tooling's, then the seeder's when its packages import. No Docker
make smoke      # the Lab UI against the running stack: the state's shape, every page's data, the static files. Read only
make check      # the project's Compose files and agent wiring: must print "All good"
make e2e ID=playground:acme-shop   # starts an item from nothing on a machine with no lab, checks it, removes it (CI runs it)
```

- `python3 lab-ui/server.py --no-open` does not open a browser. `--check` and `--list` are `make check` and `make urls`.
- `python3 -m unittest discover -s lab-ui/tests -t lab-ui` runs the Lab UI's tests alone. The page's rules need Node
  ([Set up your machine](../CONTRIBUTING.md#set-up-your-machine)) and are skipped without it: a skip did not pass.
- `make check` needs the Docker CLI with Compose 5 or later, not a running daemon. `lab_ui/library/check.py` holds what it covers:
  each add-on, setup and playground (metadata, what a shared playground says of itself (tags, duration, first start) and its README,
  includes, conflicts, image defaults, labels, restart policies, ports, literal secrets, what a service takes of this machine beyond its
  container and says why (`host_access`), what a playground requires, its guide and its seed: the API definitions, and the id and
  name of each environment it adds),
  then the project (catalog rows, image names, and the wiring of AI agents).
- A change in `lab_ui/` needs a restart of `make ui`. The page's files are read at each load.
- Tests do not see the page. After a change to it, with the platform running, go through the checks of
  [DESIGN.md](DESIGN.md#manual-checks) that cover the change, in light and dark.
