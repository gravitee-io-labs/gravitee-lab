# Contributing

You do not need to ask first. Open a pull request.

1. Fork this repository (or create a branch if you have write access) and clone it next to a clone of Gravitee Lab:
   `git clone https://github.com/__LAB_REPOSITORY__ ../gravitee-lab`
2. `make dev` links this catalog into that Lab, so you see your change in the Lab UI.
3. Add or change an item under `addons/`, `setups/` or `playgrounds/`. [Writing a catalog](https://github.com/__LAB_REPOSITORY__/blob/main/docs/writing-a-catalog.md) describes the layout.
4. `make check` runs the rules CI runs, with the same messages, and reads the commits you added as well as the files, so a secret you committed and then removed is found before you push. Each message says what to change. `make explain CODE=ST05` says more. It needs Docker Compose 5 to read the items; without it, it says what it did not check.
5. `make ci` also builds and starts the apps on this machine (it needs Docker).
6. Open a pull request. Write its title as a plain imperative sentence: it becomes the commit message. The body is not kept.

## What review looks like

A maintainer reads the change (Compose files, Dockerfiles, seeds, guides) before anything with a license or a registry login runs. A change to `.github/`, `catalog.yml` or `Makefile` is read by a maintainer only. Merges are squash merges.

## Licensing and sign-off

There is no CLA and no sign-off line. By opening a pull request you license your contribution under this catalog's license (see `catalog.yml`), and the last box of the pull request template says you may.

## Review before you run

Starting someone else's pull request runs its containers, Dockerfiles and seeds with your Docker access. Read the change first. Never run `make`, a script or an editor task inside a checkout of a pull request: a `Makefile` is code. `python3 -I ../gravitee-lab/lab-ui/catalog.py check --pr NUMBER` reads the change and runs nothing of it.
