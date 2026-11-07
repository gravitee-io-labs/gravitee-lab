---
name: new-setup
description: Creates a Gravitee Lab setup, personal or shared, when someone wants add-ons that start and stop as one.
---

# New setup

A setup is a compose file that includes add-ons, and nothing else: they start and stop as one. The rules are in
`docs/units.md`.

1. Read `AGENTS.md` and `docs/units.md`. Start from a file of the `setups/` folder of a catalog. The catalogs are in
   `.lab/catalogs/`, which git ignores: name the folder (`.lab/catalogs/<catalog id>/setups/`, the id of the default catalog
   is in `catalogs.default.json`) and do not search the tree by pattern, which skips it.
2. Settle what the request does not say. Infer what you can, ask the rest:
   - **Its name**, and its id: the name in lower case, with every run of characters other than a-z and 0-9 one dash and
     no dash at either end (*Acme stack* is `acme-stack`). The id names its file, and the check refuses another
     (ST23). Not `local`, and not the id of a setup of any catalog.
   - **Its add-ons**: the folders of `addons/` of the catalogs and of `.lab/catalogs/local/addons/`. The `x-gravitee` block
     of each `compose.yml` says what it adds and what it cannot run with. One is missing: create it first, with the
     `new-addon` skill.
   - **Its category and a description**: a noun phrase without a leading article or final period that says what it
     turns on, such as "Acme broker with its console and its gateway".
   - **Where it is kept**: Personal, on this machine only, or a catalog that is a folder of the person's (a folder
     catalog: the Lab UI's New catalog, or the `new-catalog` skill). Personal unless the person names a catalog.
3. Write it where it belongs:

   | Kept | File |
   |---|---|
   | Personal | `.lab/catalogs/local/setups/<id>.yml` |
   | In a folder catalog | `<the folder of the catalog>/setups/<id>.yml` |
4. Check it. For an item of a folder catalog, run `python3 -I lab-ui/catalog.py check <catalog id>` from the project's
   folder and fix what it says until it prints "All good". A personal item is read by Compose when the Lab UI shows
   it: the page says why one does not load.
5. In a catalog that keeps a table in `setups/README.md`, add its row (the check prints the exact row).
6. Tell the person where the file is, and that the setup shows on the Setups page of the Lab UI (`make ui`), where it is
   started.

To share a personal setup later, use the `share-item` skill.
