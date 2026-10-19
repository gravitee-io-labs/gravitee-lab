---
name: new-catalog
description: Creates a Gravitee Lab catalog, a folder of add-ons, setups and playgrounds to keep or to share, when someone has built something and has nowhere to put it.
---

# New catalog

A catalog is a folder (a Git repository once it is published) that holds add-ons, setups and playgrounds and a `catalog.yml`
that says what it is. A person who builds something to keep, or to share with a team, makes one: the items in it are the
same as personal ones, at the same depth, and the Lab shows them as the catalog's. The Lab UI's New catalog on the Catalogs
page does the same from one field, the name. The rules of the format are in `docs/writing-a-catalog.md`.

1. Read `AGENTS.md` and `docs/writing-a-catalog.md`.
2. Settle what the request does not say. Infer what you can, ask the rest:
   - **Its name**, as people read it (at most 40 characters), and its id: 2 to 24 lowercase letters, digits and dashes, the
     first a letter. The id is the namespace of every item (`@<id>/<name>`) and is stable forever. It is not a word the Lab
     reserves (`catalog.py new` refuses one and says so) and not the id of a catalog the person has
     (`python3 -I lab-ui/catalog.py list` lists them).
   - **A description**: one line, at most 90 characters.
   - **Its license**, as an SPDX identifier (`Apache-2.0`). Without one the catalog says `UNLICENSED`, a stub that asks
     for a choice.
   - **Its folder**: under the home folder, so that Docker can share it, and not there yet. `~/gravitee-lab-catalogs/<id>`
     when the person has no preference.
3. Render it from the Lab's folder, then write the description in the `catalog.yml` of the new folder:

   ```sh
   python3 -I lab-ui/catalog.py new <folder> --id <id> --name "<Name>" --license <SPDX>
   ```

   Link it, so that the Lab shows it on the Catalogs page and offers it where an item is created and shared:

   ```sh
   python3 -I lab-ui/catalog.py link <folder>
   ```

4. Check it: `python3 -I lab-ui/catalog.py check <id>` prints "All good" for a catalog that is ready. Add items with the
   `new-addon`, `new-setup` and `new-playground` skills, choosing this catalog, and share a personal item into it with the
   `share-item` skill.
5. Tell the person where the folder is. Nothing ran `git init`: to publish it, they make a repository of the folder, commit
   and push it, and anyone adds it by its address from the Catalogs page. `docs/catalogs.md` says how.
