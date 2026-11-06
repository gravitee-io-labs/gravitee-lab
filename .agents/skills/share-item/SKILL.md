---
name: share-item
description: Shares a personal Gravitee Lab add-on, setup or playground to a catalog, as a branch to propose or a copy into a folder.
---

# Share a personal item

A personal add-on, setup or playground lives in `.lab/catalogs/local/` (`addons/`, `setups/` and `playgrounds/`), which git
ignores: name its folder and do not search the tree by pattern, which skips it. Sharing it is a copy into a catalog the
person can write to. The personal item stays, keeps running and keeps its settings. The Lab UI does it with "Share" in the
item's ⋯ menu: a catalog that is a repository gets a branch, which the person proposes with a pull request, and a catalog
that is a folder gets the files. This is the same by hand. The rules are in `docs/units.md`.

1. Read `AGENTS.md` and `docs/units.md`.
2. Choose where it goes: `python3 -I lab-ui/catalog.py list` lists the catalogs of this machine. A repository takes it when its
   `catalog.yml` does not say `contribute: none` and the person can push to it. A folder always does. None fits: create a
   catalog with the `new-catalog` skill.
3. Before anything is copied, fix what keeps it from being shared. The Lab UI's Share says what first; in a folder catalog
   `python3 -I lab-ui/catalog.py check <catalog id>` says it of the copy.

   Nothing checks the next ones, so look: the catalog has no item of that name yet, a comment on each published port says
   what it is, the description is a noun phrase without a leading article or final period, the item's own settings start
   with its id, a secret under a name the check does not know (`AUTH_KEY`) is a setting too, and no real token, password
   or key is in a tracked file (move it to a `.env`). Nothing that names a private catalog goes to a public one.
4. Copy it. Only these files go, and nothing else: `compose.yml`, `README.md`, `guide.md`, `seed/`, `apps/`, and the
   files the compose file names by a relative path inside the folder. Never `.env`, `.state/`, `secrets/`, a key, a
   certificate or a scratch file. A personal item and a catalog item sit at the same depth, so the paths of the files stay
   as they are, except an include of an item of the target catalog itself:

   | Kind | From | To | In its files |
   |---|---|---|---|
   | Add-on | `.lab/catalogs/local/addons/<id>/` | `<catalog>/addons/<id>/` | `../../../<catalog id>/addons/kafka/compose.yml` becomes `../kafka/compose.yml` |
   | Setup | `.lab/catalogs/local/setups/<id>.yml` | `<catalog>/setups/<id>.yml` | `../../<catalog id>/addons/kafka/compose.yml` becomes `../addons/kafka/compose.yml` |
   | Playground | `.lab/catalogs/local/playgrounds/<id>/` | `<catalog>/playgrounds/<id>/` | `../../../<catalog id>/addons/kafka/compose.yml` becomes `../../addons/kafka/compose.yml` |

5. A catalog that is a repository gets it as a branch named `lab/<id>-<yyyymmdd>` from the tip of the catalog, with the
   person's own name and email on the commit and a plain subject ("Add Acme Shop"): never a tool as author, never a
   trailer or a credit, never a force push, never the default branch. Use the `prepare-pull-request` skill before the
   person proposes it. A catalog that a person cannot push to is not a destination: the person asks for access.
6. Once the catalog has the item and the person has updated it, the Lab UI shows the personal one as also in that
   catalog, and "Use the catalog version" moves its settings to the catalog's item, keeps its star and deletes the personal
   copy; what else its folder holds (data, notes) is set aside in `.lab/migration/kept/`, and nothing is deleted while
   anything cannot be moved there. Until then both are listed, and the Lab starts one only while the other is off. It is
   refused while the catalog's item is blocked or needs a newer Gravitee Lab: the personal copy is the one that runs.

A catalog's item is not made personal again: it would leave the catalog for everyone who updates it. For a private
variant, create a personal item with `new-addon`, `new-setup` or `new-playground`.
