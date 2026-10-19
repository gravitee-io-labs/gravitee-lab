# Security

This catalog holds demos for Gravitee Lab. It is not meant for production.

## What to expect

Gravitee Lab refuses what it can prove it must refuse before an item starts: privileged containers, the Docker socket, files of your machine outside the item's folder, ports open to your network. It does not promise that a container is harmless. Read an item's files before you start it: the Lab shows them.

## Report a problem

- A vulnerability in an item, in this repository or in an image published from it: __SECURITY_CONTACT__. Do not open a public issue.
- A secret that was committed here: report it the same way, and revoke it first.
- A vulnerability in a Gravitee product: report it through Gravitee's security process, not here.

## Scope

The items (Compose files, Dockerfiles, seeds, guides), the workflows and the published images. Only the current `main` branch is supported. The checks of this repository's CI protect its maintainers and its contributors; they are not a promise about what an item does.
