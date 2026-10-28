# Writing a catalog

A catalog is a Git repository that holds add-ons, setups and playgrounds. This page is for the person who makes one: how
to start, what the Lab reads, how to check it before anyone installs it, and what the repository does by itself. The format
of the items is in [the format of add-ons, setups and playgrounds](units.md) and what the Lab promises them in
[the contract](contract.md). For the person who uses catalogs, see [Catalogs](catalogs.md).

1. [Start](#start)
2. [The structure](#the-structure)
3. [catalog.yml](#catalogyml)
4. [Renaming an item](#renaming-an-item)
5. [Check it before you publish](#check-it-before-you-publish)
6. [Try it in the Lab](#try-it-in-the-lab)
7. [The CI of a catalog](#the-ci-of-a-catalog)
8. [Securing the repository](#securing-the-repository)
9. [Releases](#releases)
10. [Keeping in step](#keeping-in-step)

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

The folder of an add-on or a playground, and the file of a setup, is named after the item's `x-gravitee.name`: in lower case,
every run of characters other than `a-z` and `0-9` becomes one dash, with no dash at either end. *Event Streams Management with
Kafka* is `playgrounds/event-streams-management-with-kafka/`, *Kafka streams* is `setups/kafka-streams.yml`. The Lab UI's **New
add-on**, **New setup** and **New playground** name the folder so, and `catalog.py check` refuses an item that is named otherwise
(`ST23`). The folder is the item's id, so a name you change later is a rename of the folder too: see
[Renaming an item](#renaming-an-item).

## catalog.yml

```yaml
x-gravitee-catalog:
  id: acme            # the name the Lab shows in lists and uses to key what you set
  name: Acme hub
  description: Demos for Gravitee Lab
  version: 0.1.0      # the version of the next release: make release raises it
  license: AGPL-3.0-only
```

Optional keys: `requires.catalogs` (other catalogs it needs, with their address and minimum version), `shared_settings`
(settings every item may read, such as an API key the person gives once), `images` (the registry prefix of the images it
publishes), `canary` (the items the end-to-end test always tries), `renamed` (what it renamed: [Renaming an item](#renaming-an-item)). `host_access: true` says that some of its items take more of the machine than a container does (a privileged cluster, say), each with its reason in `x-gravitee.host_access`: `check` then judges them by their reasons, as the Lab does once the person has allowed the catalog at Add. The Lab still asks that person: nothing is allowed by the file. The catalog's own runs on `main`, the end-to-end one and `versions.yml`, allow it, since what is on `main` was reviewed; the run of a pull request never does.

There is no compatibility number to write. A Lab uses what it understands: when a version of your catalog uses a key, a label
or a seed folder that someone's Lab does not read yet, that Lab keeps the newest version it reads and tells them to update
Gravitee Lab. `check` runs with the Gravitee Lab release that `.github/standard.json` pins, so a catalog that passes it works on
that release and every later one; to use something a newer release adds (its release notes list it under *For catalogs*), move
the pin with `catalog.py upgrade --lab-ref latest`. The `schema` and `lab` lines that `catalog.py` writes, with their comment,
are for older Gravitee Labs: leave them to `catalog.py`, which keeps them true (`check` says when one is not, and
`upgrade --write` fixes it).

## Renaming an item

An item's folder is named after its name: lowercase, with each run of other characters as one dash (`Kiosk browser` is
`addons/kiosk-browser`). To rename an item, rename its folder, its name and its own settings, and say what you renamed in
`catalog.yml`:

```yaml
x-gravitee-catalog:
  renamed:
    items:              # kind:old: kind:new
      addon:kiosk: addon:kiosk-browser
    settings:           # OLD: NEW, in the .env of the catalog's items
      KIOSK_TOKEN: KIOSK_BROWSER_TOKEN
```

A person who updates to that release keeps everything under the new name: the item's `.env`, `secrets/` and `.state/` move to
its new folder, its settings take their new names with their values, and its star, its line in what is on, the includes of their
own items and their copies of it follow. The update waits while the item has containers, running or stopped: the person turns it
off first, so nothing is moved under a container. An item that is on and has none stays on, unless an item of another catalog
that is on includes it: that one breaks, and the update waits until it is off. Without the declaration, the Lab sees one item
removed and another added: the update waits until the item is turned off, and the new item starts with nothing.

- **Only what people may have.** A pair is needed for an item that a person may have installed: one that was in a release, or,
  for the Labs that follow a branch of your catalog, one that was on that branch. An item that never left your working copy, or a
  catalog that makes releases and renames an item before its first release, needs none: rename the folder and that is all.
- **Keep every pair.** A person may skip releases: the Lab reads the declaration of the release it installs, so a name renamed
  again is a chain (`addon:a: addon:b`, then `addon:b: addon:c` beside it), never a pair you replace.
- **A kind stays a kind**, a name is never renamed to itself or in a circle, and two names become one only along a chain: two
  items renamed into one would put the settings of both in one folder.
- **What is renamed away is gone.** `check` refuses an old item that the catalog still has, and a chain that does not end at an
  item it has.
- **Keep the volume names.** A volume is named in the Compose project of the person's Lab, not in the item's folder: a volume
  with another name is a new, empty one, and their data stays in the old one. `check` reads your history and refuses a rename
  whose commit drops a volume the old item had. Each pair is judged across the commit that made it: what a later release changes
  is a change like any other.
- **Map every setting the item read.** The Lab renames the settings of the map in the `.env` of every item of the catalog, so
  `check` refuses an old name that an item still reads, and, from your history, a setting the old item read that the new one no
  longer reads and that the map leaves out (its version, its pull policy): the value a person set would be left behind. A
  setting of the core and a shared setting are the person's, in the Lab's own `.env`: they keep their names, and `check`
  refuses them in the map.
- **Services keep their names when they can.** A file of a person's that names the old service (`depends_on`) is one Compose
  refuses until they change it. Another catalog that includes the item breaks until it includes the new one.
- **A new name is a new name.** `check` refuses a rename into an item the release before had: the Lab keeps two items apart, and
  would remove the old one instead of renaming it. It warns of a rename into a name an earlier release gave an item since
  removed: a person who updates from that release has both, and the same happens there.
- **Go back** renames back what the release it leaves renamed.

`renamed` came with a release of Gravitee Lab ([Keys of catalog.yml](contract.md#keys-of-catalogyml)): a Lab released before it
keeps the newest version of your catalog it reads and tells its person to update Gravitee Lab. Move `lab.ref` to that release
first (`catalog.py upgrade --lab-ref latest`), and let `catalog.py upgrade --write` keep the lines for older Gravitee Labs true.

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
| Checks | The rules of the standard and the Lab's policy (`catalog.py check`), the secrets in every commit, what the change touches, and that every image it pulls exists. On a push to `main`, it also says what is merged and not released yet ([Releases](#releases)) |
| Seed files | The seed files of the playgrounds, and the catalog's own tests |
| Apps | The images a unit builds, built for the architectures listed in `catalog.yml`, the tests of its apps, the units that do not need the platform, which start and answer their health check, and the scan of the images it built ([The scan of the images](#the-scan-of-the-images)) |
| Catalog CI result | The one check a merge waits for: it fails when any job it needs failed or was skipped |

A pull request runs with no secret and a read-only token, from the contributor's own copy of the workflows being refused if
it differs from the managed ones: a contribution cannot change the CI that judges it. Two more workflows run apart from the
pull requests:

| Workflow | When | What it does |
|---|---|---|
| `nightly.yml` | Every night, on `main` | The Lab's `main`, ahead of its next release, still reads the catalog (an early warning), the standard files are the newest Lab's, the links of the guides answer, the rules of `main` are still the standard's, and the published images have no fixable critical flaw. A run that fails opens an issue, or adds to the one that is open |
| `publish.yml` | A push to `main` and a release tag | Builds and publishes the images that changed, and a GitHub release on a tag. It exists only once `catalog.yml` sets `images`: until then nothing is published |

### The scan of the images

The scanner the Lab pins (Trivy, by digest, in `lab-ui/lab_ui/catalogs/ci/tools/Dockerfile` of the Lab) reads every image twice. A pull
request's **Apps** job scans the images it has just built, on each architecture (`catalog.py ci scan-built`), before anything is merged;
the night scans the images that `main` published, at `:latest` (`catalog.py ci scan-published`). The two judge alike:

- A critical flaw that has a fixed version fails (`SC01`), with the package, its version and the version that fixes it.
- The rest is said and fails nothing: one warning for each image (`SC02`) counts its high flaws and the critical ones that have no fix
  yet, and names the first.

The scan of a pull request needs no secret and no registry: it reads the images from the runner's Docker, so a fork's pull request is
scanned like any other. It scans only what the change builds, the units it touches: a flaw that appears in a base image fails the
pull requests that change that unit, never an unrelated one, and the night finds it in what was published. The scanner fetches its
databases once for each architecture of the job, which adds about a minute; the scan of an image takes seconds. `make ci` runs the same
scan on your machine.

`catalog.py new --with e2e` adds `e2e.yml`, the end-to-end run, which needs a license, and `versions.yml`, which keeps what each
playground says it was tested with up with Gravitee's releases. When the standard moves, `catalog.py upgrade` brings the new
workflows in as a reviewed pull request.

### Gravitee versions

Every night `versions.yml` tries each playground on the releases of Gravitee it does not say it was tested with yet: the newest
release of each of the five newest minor versions, and a pre-release above them all. A version below what the playground requires
(`requires.images`) is never tried, nor a set it has already, nor a playground whose sets are builds of its own (`amp-demo-latest`,
a registry other than Docker Hub's). Each one starts on a machine with no lab, with the license, as the end-to-end run does.

What works is written in the playground's `tested`: a newer release replaces the set of its minor version, a minor version that
falls out of the five newest goes, and a pre-release goes once a release of its minor version is there. The workflow commits that to
`main` and tags it as the next patch version, so that every Lab sees it at its next check. What does not work opens an issue,
labeled `versions`, or adds to the one that is open.

The commit and the tag go past the rules of `main` and of the tags with a deploy key: the rulesets of the standard let a deploy key
through, and two more, `history.json` and `tag-history.json`, which no deploy key passes, keep it from deleting or rewriting `main`
or a tag. In the job that records, only the step that pushes holds the key, and it runs `git` alone. `repo-settings` checks that
the key of `versions.yml` is the one deploy key that writes (`rules.deploy_key`). To give it the key, in the repository's folder:

```bash
ssh-keygen -t ed25519 -N '' -C versions -f versions-key
gh repo deploy-key add versions-key.pub --allow-write --title versions
gh secret set VERSIONS_DEPLOY_KEY --env versions < versions-key
rm versions-key versions-key.pub
```

The environment `versions` deploys from `main` only, as `repo-settings` checks; `make settings APPLY=1` applies the rulesets again.

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
same for a script (`next`, `tag_only`, and the items). The **Checks** job runs it with `--ci` on every push to `main`: a warning on
the run, and the notes the next release would have in its summary. It always exits 0, and the step never fails the run: what
you release, and when, is your choice.

## Keeping in step

The Lab pins the standard's files by commit in `.github/standard.json`; when the standard moves,

```bash
python3 lab-ui/catalog.py upgrade .
```

prints the plan (`--write` applies it on a branch), and `upgrade --fleet OWNER --lab-ref COMMIT` opens one pull request per
catalog of an owner. A managed file that someone edited by hand is reported (`SD02`) and never overwritten silently.

The pin, `lab.ref`, is the commit of a release of the Lab (a tag `vX.Y.Z` on its `main`), and CI refuses any other (`SD11`): a
catalog is checked with a Lab that people run, so what passes its CI works on that release and every later one. To use what a
newer release adds (its notes list it under *For catalogs*), move the pin to the latest release:

```bash
python3 lab-ui/catalog.py upgrade . --lab-ref latest --write
```

`new` pins the latest release too. The release must ship the standard the catalog is written at, since its workflows call what
that standard added: while no release of the Lab ships it, `new` and `upgrade` refuse and say so (release the Lab first).
