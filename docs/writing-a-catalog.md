# Writing a catalog

A catalog is a Git repository that holds add-ons, setups and playgrounds. This page is for the person who makes one: how
to start, what the Lab reads, how to check it before anyone installs it, and what the repository does by itself. The format
of the items is in [the format of add-ons, setups and playgrounds](units.md) and what the Lab promises them in
[the contract](contract.md). For the person who uses catalogs, see [Catalogs](catalogs.md).

1. [Start](#start)
2. [The structure](#the-structure)
3. [catalog.yml](#catalogyml)
4. [Check it before you publish](#check-it-before-you-publish)
5. [Try it in the Lab](#try-it-in-the-lab)
6. [The CI of a catalog](#the-ci-of-a-catalog)
7. [Securing the repository](#securing-the-repository)
8. [Releases](#releases)
9. [Keeping in step](#keeping-in-step)

## Start

```bash
python3 lab-ui/catalog.py new ../my-catalog --id acme --name "Acme hub" --maintainers @your-handle
```

This renders the common structure: a `catalog.yml`, a sample add-on, the managed files (the workflows and the make targets
that run the Lab's own checks) and the seeded documents (`README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `REVIEWING.md`).
It prints the next steps. A repository made this way passes `catalog.py check` on day one, with no network and no Docker
daemon, which is the point: you start from something that works. Keep the repository **private** until you decide to
publish it.

## The structure

```
catalog.yml        what the Lab reads to show the catalog
addons/<name>/compose.yml
setups/<name>.yml
playgrounds/<name>/compose.yml      (with guide.md, seed/, apps/ ...)
.github/           managed: the CI. Edit it by upgrade, not by hand
.catalog/          managed: the make targets
```

Everything the Lab installs is under the three folders. A file the Lab never installs (a symlink, a submodule, a `.env`,
`secrets/`, `.state/`, key files, an agent's folder) is dropped and reported by `catalog.py check`, so that nothing private
leaves your machine in an installed copy. A unit folder is a unit only with its `compose.yml`.

## catalog.yml

```yaml
x-gravitee-catalog:
  schema: 1
  id: acme            # the name the Lab shows in lists and uses to key what you set
  name: Acme hub
  description: Demos for Gravitee Lab
  version: 0.1.0      # the version of the next release: make release raises it
  lab: 1              # the contract level of the Lab this catalog was checked against
  license: AGPL-3.0-only
```

Optional keys: `requires.catalogs` (other catalogs it needs, with their address and minimum version), `shared_settings`
(settings every item may read, such as an API key the person gives once), `images` (the registry prefix of the images it
publishes), `canary` (the items the end-to-end test always tries). `host_access: true` says that some of its items take more of the machine than a container does (a privileged cluster, say), each with its reason in `x-gravitee.host_access`: `check` then judges them by their reasons, as the Lab does once the person has allowed the catalog at Add. The Lab still asks that person: nothing is allowed by the file. `lab` is the contract level: the Lab reads a catalog
written for a level it reads, and tells the person when it does not.

## Check it before you publish

```bash
python3 lab-ui/catalog.py check ../my-catalog
```

`check` reads the folder where it is, links nothing and writes nothing. It runs the rules of the standard (structure,
secrets, commits, guides, Dockerfiles, images, workflows) and then the Lab's own policy on every unit: what the Lab would
block and why. Each finding has a code and a line that says what to do; `catalog.py explain CODE` gives the long form, and
[Reading a failed check](catalog-rules.md) has every code.
`--format github` prints annotations, `--format json` a list, `--base REF` reports only what a change added, `--history`
and `--online` add the secrets of earlier commits and the audience of the repository. Compose 5 is needed; without it, `check`
says `Not checked: Docker Compose 5 not found` and never prints `All good`.

Things the policy refuses, so that you can avoid them: `privileged`, host networking, the Docker socket, bind mounts outside
the unit's folder, builds from an unpinned source, `env_file` and `label_file`, an include from outside the catalog, a
setting read from another item. A unit that really needs one of these says why in `x-gravitee.host_access`, and the person
who adds the catalog decides ([Items that use your machine](catalogs.md#items-that-use-your-machine)).

## A catalog that needs another

An item can include an item of another catalog, and `catalog.yml` says which catalog it needs (`requires.catalogs`). `check`
reads such a catalog together with the one it needs, and refuses to read it alone. In CI, while that other catalog is private,
`.github/standard.json` names it and the commit to read:

```json
"needs": {"gravitee": {"repository": "owner/catalog", "ref": "<a full commit>"}}
```

The **Checks** job fetches it with `catalog.py ci needs`, using the secret `NEEDS_DEPLOY_KEY`: a read-only deploy key of that
repository, stored like `LAB_DEPLOY_KEY` in the Actions and the Dependabot stores. `make settings` audits both. Move the commit
by hand when the other catalog moves.

## Try it in the Lab

```bash
python3 lab-ui/catalog.py link ../my-catalog
```

links the folder, so the Lab lists its items as they are, with no copy: edit, reload, try. `unlink` reverses it. In the Lab
UI, **Library > Catalogs > Add catalog** takes the same folder.

## The CI of a catalog

The managed workflows run on every pull request and every push to `main`:

| Job | What it proves |
|---|---|
| Checks | The rules of the standard and the Lab's policy (`catalog.py check`), the secrets in every commit, what the change touches, and that every image it pulls exists |
| Seed files | The seed files of the playgrounds, and the catalog's own tests |
| Apps | The images a unit builds, built for the architectures listed in `catalog.yml`, the tests of its apps, and the units that do not need the platform, which start and answer their health check |
| Catalog CI result | The one check a merge waits for: it fails when any job it needs failed or was skipped |

A pull request runs with no secret and a read-only token, from the contributor's own copy of the workflows being refused if
it differs from the managed ones: a contribution cannot change the CI that judges it. Two more workflows run apart from the
pull requests:

| Workflow | When | What it does |
|---|---|---|
| `nightly.yml` | Every night, on `main` | The newest Lab still reads the catalog, the standard files are the newest Lab's, the links of the guides answer, the rules of `main` are still the standard's, and the published images have no fixable critical flaw. A run that fails opens an issue, or adds to the one that is open |
| `publish.yml` | A push to `main` and a release tag | Builds and publishes the images that changed, and a GitHub release on a tag. It exists only once `catalog.yml` sets `images`: until then nothing is published |

`catalog.py new --with e2e` adds `e2e.yml`, the end-to-end run, which needs a license. When the standard moves,
`catalog.py upgrade` brings the new workflows in as a reviewed pull request.

You can run the same stages on your machine: `make check`, `make ci`, `make index` (see `.catalog/standard.mk`). Never run
`make` or a script from a contributor's checkout: `catalog.py check --pr N` reads the pull request's Git objects instead.

## Securing the repository

`python3 lab-ui/catalog.py repo-settings` audits the repository's GitHub settings against the standard and says what is still
to set: branch rules, required check, review by the code owners, no force push, no outside fork write access, signed
squash merges, immutable releases. `--apply` prints the requests that set it and `--apply --yes` makes them (it needs
administration rights, never deletes anything, and a plan without rules, such as a free private repository, reports
`n.a.` for what GitHub refuses). `--as public|private|internal` audits for the audience you intend.

The standard asks for no CLA. Commits are squashed on merge, with the pull request title as the message and no trailer. A
commit that names a tool as its author is refused (`CR01`).

## Releases

The Lab offers people the releases of your catalog, not its main branch ([Versions](catalogs.md#versions)). A pull request you
merge reaches nobody until you release it: merge as often as you like, and release when what is on main is worth an update.
A catalog that has never made a release is followed commit by commit, which suits its first days; the first tag ends that.

A release takes two steps, because the rules of `main` forbid a commit pushed to it:

```bash
make release VERSION=1.1.0
```

makes the branch `release/1.1.0` with `version:` raised in `catalog.yml` and nothing else, and prints the lines that push it and
open its pull request (`python3 lab-ui/catalog.py release 1.1.0 --pr` pushes the branch and opens it with `gh`). It refuses a dirty tree, a branch
that is behind `origin/main` and a version that is not greater than the last tag. Once that pull request is merged:

```bash
make release VERSION=1.1.0 TAG=1
```

makes the annotated tag `v1.1.0` on `origin/main`, after checking that `catalog.yml` there says `1.1.0`, and prints the line that
pushes it. An administrator pushes it. With `images` set, `publish.yml` then publishes the images and a GitHub release whose
notes list the items that are new, changed (with the titles of the pull requests that changed them) and removed. Every Lab sees
the release at its next check, and its Update dialog says *1.0.0 → 1.1.0* with those same lines as What's new: write pull request
titles for the people who will read them there.

A release is a promise that `v1.1.0` is always the same files. Never move or delete a tag that was pushed: a Lab that installed
it says the catalog was rewritten, and its people must confirm an update they cannot judge. Fix a release with the next one.

To see what is merged and not released yet, from the catalog's folder with the Lab beside it (`LAB` of the Makefile):

```bash
python3 ../gravitee-lab/lab-ui/catalog.py ci unreleased .
```

says which items changed since the last tag and the command that releases them: `make release VERSION=1.1.0`, or
`make release VERSION=1.1.0 TAG=1` once the release pull request is merged and only its tag is left. `--format json` gives the
same for a script (`next`, `tag_only`, and the items), `--ci` the summary of a workflow, with the notes the next release would
have. It always exits 0: what you release, and when, is your choice.

## Keeping in step

The Lab pins the standard's files by commit in `.github/standard.json`; when the standard moves,

```bash
python3 lab-ui/catalog.py upgrade .
```

prints the plan (`--write` applies it on a branch), and `upgrade --fleet OWNER --lab-ref COMMIT` opens one pull request per
catalog of an owner. A managed file that someone edited by hand is reported (`SD02`) and never overwritten silently.
