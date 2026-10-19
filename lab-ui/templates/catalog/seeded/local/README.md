# __CATALOG_NAME__

__DESCRIPTION__

A catalog of this machine for Gravitee Lab: a folder that the Lab reads as a catalog. Add-ons, setups and playgrounds go under `addons/`, `setups/` and `playgrounds/`, one folder or file for each.

`addons/hello` is a sample that works as it is. Delete it when the first real item lands.

## Develop

The Lab shows this folder as a catalog already. From a terminal, with the Lab next to this folder (`../gravitee-lab`, or set `LAB=/path/to/gravitee-lab`):

| Command | Does |
|---|---|
| `make dev` | Links this folder into the Lab |
| `make check` | Says what a person would see when they add this catalog |

## Publish

This folder is not a git repository yet. To share it, run `catalog.py new` for a catalog with the standard files, copy the items into it, and push it to a repository.
