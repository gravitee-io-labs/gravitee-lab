---
name: new-playground
description: Creates a Gravitee Lab playground, personal or shared, when someone wants a use case with its apps, sample data and guide.
---

# New playground

A playground is a use case started in one click: the add-ons it needs, its apps, sample data in Gravitee and a guide.
The rules are in `docs/units.md`, and the authoring reference in `docs/playgrounds.md`.

1. Read `AGENTS.md`, `docs/units.md` and `docs/playgrounds.md`. Start from a complete playground of a catalog. The catalogs
   are in `.lab/catalogs/`, which git ignores: name the folder (`.lab/catalogs/<catalog id>/playgrounds/`, the id of the
   default catalog is in `catalogs.default.json`) and do not search the tree by pattern, which skips it.
2. Settle what the request does not say. Infer what you can, ask the rest:
   - **Its name**, and its id: the name in lowercase letters, digits and dashes. Not `local`, and not the id of a playground
     of any catalog. Its own settings start with its id (`ACME_SHOP_*`).
   - **The use case**: the add-ons and setups it needs, its apps and where they are built from (their own folder or a
     repository), what it creates in Gravitee (APIs, applications, users) and the lessons of its guide.
   - **Whether it also deploys to Gravitee Cloud**: nothing to declare, the Lab works it out. A seed of `seed/apim/`
     only, backend apps, no change to the core.
   - **Whether a lesson teaches a protection** that starts switched off (a
     [control](../../../docs/playgrounds.md#controls-the-protections-a-lesson-switches-on)) or spends a budget that
     Re-seed puts back (a [refill](../../../docs/playgrounds.md#refills-what-a-lesson-spends-put-back)).
   - **Its category, a description, its tags and the time the guide takes** (`20 min`, `1 h 30 min`), and how long the first
     start takes: a playground kept in a catalog has them all, and the check asks for them. The description is a noun
     phrase without a leading article or final period, such as "Protection of a shop's REST API and AI assistant, one
     lesson at a time".
   - **Where it is kept**: Personal, on this machine only, or a catalog that is a folder of the person's (a folder
     catalog: the Lab UI's New catalog, or the `new-catalog` skill). Personal unless the person names a catalog.
3. Write its folder where it belongs. A personal item and a catalog item sit at the same depth, so the same paths work in
   both:

   | | Personal | In a folder catalog |
   |---|---|---|
   | Folder | `.lab/catalogs/local/playgrounds/<id>/` | `<the folder of the catalog>/playgrounds/<id>/` |
   | It includes an add-on of the same catalog as | `../../addons/kafka/compose.yml` | `../../addons/kafka/compose.yml` |
   | It includes a setup of the same catalog as | `../../setups/event-streams.yml` | `../../setups/event-streams.yml` |
   | It includes an add-on of the default catalog, from any other place, as | `../../../<default catalog id>/addons/kafka/compose.yml` | `../../../<default catalog id>/addons/kafka/compose.yml` |
   | Its seed builds | `../../../../../seeder` | `../../../../../seeder` |

   An item of a catalog includes the add-ons and setups of its own catalog and of the catalogs it declares in
   `requires.catalogs` of its `catalog.yml`, never one of this machine. A personal item includes what it wants.

   - `compose.yml`: the metadata, what it includes, its apps and its seed. `services: {}` when it has none.
   - Its secrets: every password, secret, token and key is a setting, never a literal, the `password` and `key` of
     each account included (the check refuses a literal in the item of a catalog). Define them once as anchors in
     an `x-<id>-secrets` block placed before `x-gravitee`, and alias them from the accounts and the services (the first
     lines of the `compose.yml` of a complete playground show it).
   - `guide.md`: a title, an introduction, then one `## 1. Title` section per lesson, in order.
   - `apps/<app>/`, with its `Dockerfile`: only when it ships an app.
   - `seed/`, with the seed service that reads it: only when it creates something in Gravitee.
4. An add-on it needs is missing: create it first, with the `new-addon` skill.
5. Check it. For an item of a folder catalog, run `python3 -I lab-ui/catalog.py check <catalog id>` from the project's
   folder and fix what it says until it prints "All good". A personal item is read by Compose when the Lab UI shows
   it: the page says why one does not load. Run the tests of an app it ships, in its image, when Docker runs.
6. In a catalog, write `README.md` in its folder for what a user needs to know before and after, and add its row to
   `playgrounds/README.md` when the catalog keeps that table (the check prints the exact row). Ask the person which
   versions they built it and checked it on, and write them as `x-gravitee.tested`: CI starts the playground on each set.
   Never write versions nobody ran it on.
7. Tell the person where the folder is, and that the playground shows on the Playgrounds page of the Lab UI
   (`make ui`). Ask them to start it from its page and go through the guide: starting changes their lab, so do not start
   it yourself.

To share a personal playground later, use the `share-item` skill.
