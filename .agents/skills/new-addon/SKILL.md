---
name: new-addon
description: Creates a Gravitee Lab add-on, personal or shared, when someone wants to add a service or a tool to the lab.
---

# New add-on

An add-on is one capability on top of the core platform: a folder with its `compose.yml`. The rules are in
`docs/units.md`.

1. Read `AGENTS.md` and `docs/units.md`. Start from the add-on of a catalog that is closest to the request. The catalogs
   are in `.lab/catalogs/`, which git ignores: name the folder (`.lab/catalogs/<catalog id>/addons/`, the id of the default
   catalog is in `catalogs.default.json`) and do not search the tree by pattern, which skips it.
2. Settle what the request does not say. Infer what you can, ask the rest:
   - **Its name**, and its id: the name in lower case, with every run of characters other than a-z and 0-9 one dash and
     no dash at either end (*Acme Console* is `acme-console`). The id names its folder, and the check refuses another
     (ST23). Not `local`, and not the id of an add-on of any catalog or the name of a service the project has.
   - **What it adds**: the image and its version, the port to open, the settings and secrets it needs, the keys it sets
     on core services, the add-ons it needs or cannot run with. Its own settings start with its id (`ACME_CONSOLE_*`).
   - **Its category and a description**: a noun phrase without a leading article or final period that says what it
     adds, such as "Browser for what the gateway sends to Acme".
   - **Where it is kept**: Personal, on this machine only, or a catalog that is a folder of the person's (a folder
     catalog: the Lab UI's New catalog, or the `new-catalog` skill). Personal unless the person names a catalog.
3. Write it where it belongs. A personal item and a catalog item sit at the same depth, so the same paths work in both:

   | Kept | Folder |
   |---|---|
   | Personal | `.lab/catalogs/local/addons/<id>/` |
   | In a folder catalog | `<the folder of the catalog>/addons/<id>/` |

   | It includes | Written as |
   |---|---|
   | An add-on of the same catalog | `../kafka/compose.yml` |
   | An add-on of the default catalog, from any other place | `../../../<default catalog id>/addons/kafka/compose.yml` |

   An item of a catalog includes the add-ons of its own catalog and of the catalogs it declares in `requires.catalogs`
   of its `catalog.yml`, never one of this machine. A personal item includes what it wants.
4. Write every password, secret, token and key as a setting, never a literal: `${NAME:-default}` with a public demo
   value as the default, or `${NAME}` when the person must provide it. The check refuses a literal in the item of a
   catalog. Put the real values in `.env`, in its folder, never in `compose.yml`.
5. Check it. For an item of a folder catalog, run `python3 -I lab-ui/catalog.py check <catalog id>` from the project's
   folder and fix what it says until it prints "All good". A personal item is read by Compose when the Lab UI shows
   it: the page says why one does not load.
   A service that takes more of the machine than its container does (privileged, the host's network, the Docker socket, a
   folder outside the project) says why in `x-gravitee.host_access`; the check asks for it in the item of a catalog.
6. In a catalog, write `README.md` in the add-on's folder for what it adds and how to use it: the check asks for it. When
   the catalog keeps a table in `addons/README.md`, add the row too (the check prints the exact row).
7. Tell the person where the folder is, and that the add-on shows on the Add-ons page of the Lab UI (`make ui`), where
   it is started.

To share a personal add-on later, use the `share-item` skill.
