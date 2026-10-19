# __CATALOG_NAME__

A catalog for Gravitee Lab. The layout and the format of an item are in the Lab's guides, which are the source of truth:

- [Writing a catalog](https://github.com/__LAB_REPOSITORY__/blob/main/docs/writing-a-catalog.md): the layout, the format and the CI of a catalog
- [Reading a failed check](https://github.com/__LAB_REPOSITORY__/blob/main/docs/catalog-rules.md): every rule `make check` can print, with its fix

## Rules for an agent working here

- Run `make check` before you say a change is done, and fix what it prints. `make explain CODE=ST05` says more about a code.
- A file whose first line says "Managed by the Lab catalog standard" is not edited by hand. `make upgrade` changes it, in a pull request a maintainer reads.
- Never write a secret, a `.env` file, a key, a customer name or an internal host in a file or in a commit. Give a setting a name and keep its value in `.env`.
- Never run `make`, a script or an editor task inside a checkout of someone else's pull request. To read one, run `python3 -I ../gravitee-lab/lab-ui/catalog.py check --pr NUMBER`.
- Write the title of a pull request as a plain imperative sentence: it becomes the commit message, and the body is not kept.
