# __CATALOG_NAME__

__DESCRIPTION__

This is a catalog for [Gravitee Lab](https://github.com/__LAB_REPOSITORY__): add-ons, setups and playgrounds that the Lab shows in its pages and starts with Docker Compose.

## What it holds

| Folder | Holds |
|---|---|
| `addons/` | One folder for each add-on: a `compose.yml` and a README |
| `setups/` | One file for each setup: `<name>.yml` |
| `playgrounds/` | One folder for each playground: a `compose.yml`, a README, a guide and a seed |

`addons/hello` is a sample that works as it is. Delete it when the first real item lands.

## Develop

Clone this repository next to a clone of Gravitee Lab (`../gravitee-lab`, or set `LAB=/path/to/gravitee-lab`), then:

| Command | Does |
|---|---|
| `make dev` | Opens this catalog in the Lab |
| `make check` | Runs the rules CI runs, on the files and on the commits since `origin/main` |
| `make ci` | Also builds, tests and starts the apps on this machine (needs Docker) |
| `make explain CODE=ST05` | Says more about a rule |

## Contribute

Open a pull request. [CONTRIBUTING.md](CONTRIBUTING.md) says how, [REVIEWING.md](REVIEWING.md) is what a maintainer reads, and [SECURITY.md](SECURITY.md) says where to report a problem.
