# Reviewing a pull request

Starting an item runs its containers, Dockerfiles and seeds with the Docker access of whoever starts it. You approve what every
person who updates this catalog will run. Read the change before you read the green check: CI proves the rules hold, not that the
change is what it says.

Never run `make`, a script or an editor task inside a checkout of a pull request, and do not open its folder in an editor that runs
tasks on its own: a `Makefile` is code, and so are a devcontainer, a task file and a hook. To look at a pull request without running
any of it, use `python3 -I ../gravitee-lab/lab-ui/catalog.py check --pr NUMBER`. Do not approve a run of a workflow for a pull
request that changes `.github/` until you have read that change. Pushing someone else's commit to a branch of this repository is the
same as approving every workflow in it: do not. To try a pull request with the license, run the workflow `E2E` from `main` with its
number and the commit you read.

## 1. The shape of the change

- [ ] One item (a folder of `addons/`, `playgrounds/` or a file of `setups/`), its README, and nothing else. A change to `.github/`,
      `.catalog/`, `catalog.yml`, `Makefile`, `CODEOWNERS` or another item is a different review: only a maintainer approves it, and
      reads the `Makefile` and everything under `.github/` and `.catalog/` as code.
- [ ] No binary, archive, minified or generated file you cannot read. Ask for the source.
- [ ] The author, the branch and the title say what the files do. A first-time contributor is not a reason to refuse, it is a reason
      to read slowly.

## 2. What the item asks of the machine (the Lab refuses most of it; read it anyway)

- [ ] `compose.yml`: every `command`, `entrypoint` and healthcheck; every `volumes` source; every published port; every `environment`
      set on a core service; every `image` (registry, tag, a digest when it can be).
- [ ] No service of the item itself reads `GRAVITEE_LICENSE`: only an overlay on a core service may. Any other reference to the
      license variable is a reject.
- [ ] `build`: a remote context is pinned to a commit. Nothing builds from a branch.
- [ ] `x-gravitee.host_access`, if it is there, is true and the reason is the real one.

## 3. What it runs

- [ ] Each `Dockerfile`: the base image (an official or a known one, pinned), every `RUN` (no `curl | sh`, no `ADD` of a URL, no
      package source nobody knows), `USER`, build arguments.
- [ ] The app's source: calls to hosts that are not the lab's, reads of the environment or of the disk, `eval`, encoded blobs,
      install scripts and new dependencies (a name close to a known one is a typo or an attack).
- [ ] The seed: URLs and hosts it calls, tokens it reads, anything that is not data. A seed brings its own code only on this
      machine's behalf: say no if it needs a token.
- [ ] The guide: links (shorteners, look-alike domains), HTML, a step that asks the reader to paste a token or run a command from
      the internet.

## 4. What it leaves behind

- [ ] No secret, key, license, customer or employer name, internal host or registry, in the files or the history of the pull request
      (CI scans the commits too; you read what a scanner cannot).
- [ ] The license of what it copies allows it; the credit is there.
- [ ] `x-gravitee`: name and description say what it is, `requires` is complete.

## 5. Then

- [ ] `Catalog CI result` is green on the last commit you read. A push after your review needs a new review (the rules ask).
- [ ] A change to `.github/standard.json` moves the Lab that judges every pull request and runs with the registry token on `main`:
      open the compare link in the commit message and read the Lab changes it brings.
- [ ] Squash. The title is the pull request's, the body is empty.
- [ ] After the merge: the images were published (if the catalog publishes them), and Update shows the change as a person will see it.

Say no when: it needs host access for no reason you can name; it reaches out to a host you cannot explain; you cannot read it; the
author cannot say what a part of it does.
