# Lab UI

The Lab UI is a local web page that runs the lab without the terminal. It reads the project's Compose files, shows what
is on and what runs, turns add-ons, setups and playgrounds on and off, and starts and stops containers with
`docker compose`. What it does is in [docs/lab-ui.md](../docs/lab-ui.md).

```bash
make ui
```

It opens http://localhost:9000, on its Dashboard, and listens on `127.0.0.1` only. It needs Python 3.9 or later and nothing else: the
server uses the standard library, and the page is plain ES modules and CSS with no build step.

## Options

`make ui` runs `python3 lab-ui/server.py`. Run that yourself to pass an option:

| Option | Does |
|---|---|
| `UI_PORT=9001 make ui`, or `--port 9001` | Listens on another port. The UIs of a playground only show on port 9000 |
| `--no-open` | Does not open the browser |
| `--check` | Checks the core and the agent wiring, then exits. `make check` runs it |
| `--list` | Prints what is on and the URL of every console, then exits. `make urls` runs it |

## Test

`make test` runs the unit tests with no Docker. The page tests need Node ([Set up your machine](../CONTRIBUTING.md#set-up-your-machine))
and are skipped without it. `make smoke` checks the Lab UI against the running stack and changes nothing.

## Layout

| Path | What it holds |
|---|---|
| `server.py`, `catalog.py` | The Lab UI, and the command of the catalogs |
| `lab_ui/` | The Python package: `docker/`, `gravitee/`, `library/`, `server/` and `catalogs/` |
| `frames/` | The script a framed console runs to tell the page which address it is on |
| `templates/` | What a new catalog is made from |
| `web/` | The page, served as it is: `index.html`, `css/` and `js/` |
| `tests/` | The unit tests, `js/` for the page's rules, and `smoke.py` |

To change the Lab UI, read [AGENTS.md](AGENTS.md): its rules, its modules, and the checks to click through.
