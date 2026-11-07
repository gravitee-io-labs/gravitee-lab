# Lab UI

The Lab UI (`make ui`, http://localhost:9000) is a local page that runs the lab without the terminal. It reads the
project's Compose files, shows what is on and what runs, turns add-ons, setups and playgrounds on and off, and starts or
stops containers with `docker compose`. It edits the same files a person would, and listens on `127.0.0.1` only. It is a
small helper tool: when in doubt, do less.

| Read | For |
|---|---|
| [DESIGN.md](DESIGN.md) | The interface conventions, the state model, the checks to click through |
| [docs/lab-ui.md](../docs/lab-ui.md) | What it does for a person: update it with the behavior |

## Layout

The module docstring (Python) or the header comment (JavaScript) of a file is the detail. `server.py` is the entry point,
`catalog.py` the command of the catalogs, `frames/` the script a framed console runs to tell the page which address it is on, and
`templates/` what a new catalog is made from.

- `lab_ui/` holds `paths.py` (the project's layout), `products.py` (what the engine knows itself of Gravitee's products), `e2e.py`
  (`make e2e`), `compat.py` (the versions of Gravitee a playground is tried on) and a package for each domain:
  - `docker/`: what the Lab UI asks of Docker and of the files around it. It imports nothing of the library.
  - `gravitee/`: the Gravitee platform and where it runs.
  - `library/`: the add-ons, setups and playgrounds: how they are read, checked, laid out and turned on.
  - `server/`: the HTTP handler, the routes and what a page reads besides the state.
  - `catalogs/`: the catalogs, how they are installed, checked and kept ([Catalogs](../docs/catalogs.md)). `cli.py` is their command
    (`catalog.py` is its entry script), and `explain.py` writes `docs/catalog-rules.md`
    (`WRITE_RULES_DOC=1 python3 lab-ui/tests/run.py tests.catalogs.test_explain_document` writes it again).
- `web/` is the page, served as it is: `index.html`, `css/` and `js/`, with `main.js`, `boot.js` and `core.js` at its top, `pages/` (one
  file per page), `dialogs/`, `components/` (the pieces the pages share), `platform/` (what the Lab says of an environment of a
  platform, where a playground deploys), `activity/` (the running jobs) and `workspace/` (the page of a running playground).

`tests/` mirrors `lab_ui/`: a folder for each package, a file for each module (`tests/library/test_project.py` tests `lab_ui/library/project.py`).
`tests/web/` holds the page's tests: `test_static.py` for its files, `js/` for its rules (`node --test`, run by `test_page.py`; `dom-stub.mjs`
is a small DOM, which `images.test.mjs` uses to run the Images page). `tests/run.py` runs them in parallel, and in shards for the CI.
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
- **Imports go one way**: `paths.py` and `products.py`, then `docker/`, then `library/` and `gravitee/` with the
  catalogs they read, then `server/`. A module imports from its own stage or one before it, at the top of its file: a
  function that imports what imports its module hides a cycle. The few imports a function makes say why in
  `tests/test_imports.py`, which holds the order. What a lower module needs of a higher one is passed to it (what is on,
  to `images.state`).
- **The Compose files hold the product knowledge**: `x-gravitee` blocks, labels, `include`, `image:` lines, mounts. No
  list of products, add-ons or images in `lab_ui/` or `web/`. The explicit exceptions each have one place: what the engine
  knows itself is in `lab_ui/products.py` (the folders of a seed and the product each fills, which is the seeder's layout;
  the scopes of a Cloud gateway; the gateway's image; the Azure registries; the tag suffixes of registries, `VARIANTS`,
  which `js/core.js` copies for every page and a test keeps equal; a test also holds that no other module names them),
  and the acronyms of license packs are in `js/dialogs/license.js` (`ACRONYMS`).
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
| marked, DOMPurify | 18.1.1, 3.4.17 | cdnjs.cloudflare.com | The first time a guide is shown (`js/components/markdown.js`) | The guide shows as plain text |
| Mermaid | 11.15.0 | cdnjs.cloudflare.com | When a guide has a `mermaid` block (`js/components/markdown.js`) | The block stays as written |
| Monaco | 0.52.2 | cdn.jsdelivr.net | When the editor opens (`js/dialogs/editor.js`) | A plain text area |
| xterm.js, its fit addon | 6.0.0, 0.11.0 | cdn.jsdelivr.net | When a playground's terminal is shown (`js/workspace/terminal.js`) | The panel says it needs the internet |

To change a version, compute its hash as the comment in `js/components/markdown.js` says, and update the constants of the file that
loads it.

## Run and test

`make ui` opens http://localhost:9000. `UI_PORT=9001 make ui`, or `--port 9001`, listens on another port, where the UIs of a
playground do not show. [Commands](../AGENTS.md#commands) lists what verifies a change.

- `python3 lab-ui/server.py --no-open` does not open a browser. `--check` and `--list` are `make check` and `make urls`.
- `python3 -m unittest discover -s lab-ui/tests -t lab-ui` runs the Lab UI's tests alone. The page's rules need Node
  ([Set up your machine](../CONTRIBUTING.md#set-up-your-machine)) and are skipped without it: a skip did not pass.
- `make check` needs the Docker CLI with Compose 5 or later, not a running daemon. `lab_ui/library/check.py` holds what it covers: the
  core file, the skills and their stubs, and the `CLAUDE.md` next to each `AGENTS.md`. What a catalog holds is `catalog.py check`'s.
- A change in `lab_ui/` needs a restart of `make ui`. The page's files are read at each load.
- Tests do not see the page. After a change to it, with the platform running, go through the checks of
  [DESIGN.md](DESIGN.md#manual-checks) that cover the change, in light and dark.
