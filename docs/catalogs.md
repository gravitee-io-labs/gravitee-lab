# Catalogs

The Lab runs the Gravitee platform and nothing else. Everything you can turn on top of it, the add-ons, the setups and the
playgrounds, comes from **catalogs**: Git repositories that follow one structure. The Lab is set up with one catalog,
Gravitee public, and you can add others.

1. [What a catalog is](#what-a-catalog-is)
2. [The first start](#the-first-start)
3. [Add, update, go back, remove](#add-update-go-back-remove)
4. [Versions](#versions)
5. [What blocked means](#what-blocked-means)
6. [Items that use your machine](#items-that-use-your-machine)
7. [Private catalogs](#private-catalogs)
8. [Your own items](#your-own-items)
9. [Offline, privacy and starting over](#offline-privacy-and-starting-over)
10. [Updating a Lab from before catalogs](#updating-a-lab-from-before-catalogs)
11. [Errors and what to do](#errors-and-what-to-do)

## What a catalog is

A catalog is a Git repository with a `catalog.yml` and three folders: `addons/`, `setups/` and `playgrounds/`. The Lab
installs it whole, as a copy in `.lab/catalogs/<name>/`, and never changes a file of it afterwards: an update replaces them,
and **Go back** puts the previous copy back. What is yours (the `.env`, `secrets/` and `.state/` of each item, which sit
beside its files, your stars and what is on) is never touched, and survives every update.

In the lists, the *About* of an item says the catalog it comes from as soon as there are two catalogs (with one, there is
nothing to tell apart), and an item of this machine says Personal. Each list then has a picker of the catalogs, to keep the
items of one, and the Dashboard has a button that counts them. **Library > Catalogs** lists the catalogs, what each holds and
its state.

You never need Git to use a catalog from the page. The Lab runs Git for you, without ever asking for a password in the
background: a private catalog asks you to sign in with Git on this machine first
([Private catalogs](#private-catalogs)).

## The first start

On its first start the Lab downloads the default catalog, Gravitee public. While it downloads, the lists say so and the rest of
the Lab works. If it cannot download, the lists say why and offer **Try again**; the Lab never blocks on it. From a terminal,
`make catalogs` does the same, with progress lines.

The Lab contacts the address of each catalog you added, and nothing else for catalogs: no account, no statistics. See
[Offline, privacy and starting over](#offline-privacy-and-starting-over).

## Add, update, go back, remove

All four are on **Library > Catalogs**, and each shows what it will do before it does it.

**Add.** **Add catalog** takes `owner/repo` (GitHub), a Git address or a folder on this machine. The preview says what the
catalog adds (playgrounds, setups, add-ons), where it comes from, what runs on this machine, what it builds, the ports it
publishes, what it changes of the Lab's core and what it needs from other catalogs. **Cannot run** counts the items the
Lab refuses to start and says why ([What blocked means](#what-blocked-means)). Adding is your consent to what the preview
shows. A catalog that needs another one says which; add that one first.

**Update.** Catalogs are checked in the background, five seconds after the Lab UI starts and then every six hours. The row says
*Update available* when a newer version exists ([Versions](#versions)), with a dot on the Catalogs entry of the menu. **Update**
is the only way a copy is replaced, except for a catalog the Lab can no longer read, which is brought forward by itself. A catalog
with something to decide opens a dialog that says what changes first: the version, from the one you have to the new one
(*Unreleased* when the catalog makes no releases, *Unchanged* when a release was moved), every item it updates, adds, removes or
breaks with its kind, what changed in the folder of each item it updates (`README.md`, `compose.yml`, `seed/`), what is new in
them, an item that is on (turn it off first, and the button says so), more asked of your machine, or a history that was rewritten
or a release that was moved, which asks for one more confirmation (**Update anyway**) because the new version is not a
continuation of the one you have. A catalog with nothing to decide is updated by the click, and **Go back** restores the previous
copy. From a terminal: `python3 lab-ui/catalog.py update ID`.

**Go back.** Puts the previous copy back, once. It exists because an update can be wrong.

**Remove.** Takes the copy away. Choose **Keep my settings** (what you set and what its playgrounds created, if you add it
again) or **Delete them too**. An item that is on blocks the removal until you turn it off.

From a terminal: `python3 lab-ui/catalog.py add|update|undo|remove|list`.

## Versions

A version of a catalog is a **release**: a tag `vX.Y.Z` that its owners made with `catalog.py release`
([Releases](writing-a-catalog.md#releases)). What is merged into the catalog's branch after its last release waits for the next
one: the Lab never offers it as an update, so a version number always names the same files.

- **Which tags count.** A tag `vX.Y.Z` (three numbers, nothing after them) on a commit of the branch the Lab follows, whose
  `catalog.yml` says `version: X.Y.Z`. A tag on a commit the branch does not contain, a tag of another form (`v2`, `v1.0.0-rc1`)
  and a tag its `catalog.yml` disagrees with are not releases. Add, the first download and Update take the greatest release
  this Lab reads; when a newer release needs a newer Lab, the row says so, as for any version.
- **A catalog with no release** is followed commit by commit: Add and Update take the newest commit of its branch. The row says
  *Unreleased* beside its version, and so does the Version line of the Update dialog. Its first release ends that: from then on
  only releases are offered.
- **A copy that is newer than the last release** (installed before that catalog made releases, or from a catalog that made none
  then) stays as it is: an older release is never offered in its place. Its row says *Unreleased* until the next release.
- **Tags can be moved; releases cannot.** The Lab fetches the tags that start with `v` with the branch and keeps them apart from
  everything else: they never run, and only the commit they name is installed. It remembers the commit of the release you have.
  When the owners move that tag to another commit and an update would install it, the row says *Rewritten*, as for a history
  that was rewritten, and **Update anyway** is the only way to take it. A tag that was deleted is no longer offered; the copy
  you have stays.
- **A branch named at Add** is followed the same way: its releases are the tags of its own commits, and while it has none the
  Lab follows its newest commit. A branch made from a released main holds that release, so it is what the Lab offers until the
  branch has a release of its own. To try what is on a branch before it is released, link a working copy instead
  ([Writing a catalog](writing-a-catalog.md#try-it-in-the-lab)).
- **What's new** is what the notes of a release say: for each item the update changes, adds or removes, the subjects of the
  commits that touched it, five at most. The Lab reads them from the catalog's Git history, from the version you have to the
  one offered, with the same function that writes the notes of the release on GitHub (`ci release-notes`), so an update from one
  release to the next says the words of that release, and nothing is asked of GitHub.

## What blocked means

Before an item can start, the Lab judges the Compose files it brings. The policy is deny by default: what the Lab does not
recognize is refused. A refused item still shows in the lists, with its reason, and cannot start. Nothing is run to decide.

| Reason | What it means | What to do |
|---|---|---|
| Runs with full privileges, Reaches Docker from inside a container, Mounts the Docker socket, Runs a program on this machine, Uses the host network, Adds Linux capabilities, Uses devices of this machine, Lowers container isolation, Mounts files outside its folder | The item asks for a power over this machine. Some items need one legitimately, for example a Kubernetes cluster | See [Items that use your machine](#items-that-use-your-machine) |
| Uses a Compose feature the Lab does not check | A key the Lab has no rule for | Ask the catalog's owner to remove it |
| Writes to its own folder, Reads files outside its folder, Mounts a volume that is not its own, Joins a network that is not the Lab's, Publishes a port to the network | The item reaches beyond itself | Ask the owner |
| Builds from outside its folder, Builds from an unpinned source, Builds with extra access | A build that is not contained | Ask the owner to pin or move it |
| Includes content from outside the catalog, Depends on private content | A file the catalog does not own | Ask the owner |
| Reads settings that are not its own | The item reads a setting of another item, for example a license | Ask the owner |
| Same name as another item, Changes the core beyond adding settings | A clash with what is already there | Ask the owner |
| Needs {name}, which is blocked | It depends on a blocked item | Fix the one it needs |
| Needs Lab level N, Written for an older Lab | The catalog and the Lab do not read the same contract | Update the Lab, or ask for another version of the catalog |
| Cannot be read | A file that does not parse | Ask the owner |

An item that only has notes (**Brings its own seed code**, **Seed data reads a token**) starts, and says what to watch.

## Items that use your machine

Some items legitimately need more than a container: a Kubernetes cluster runs privileged, a console reads a socket. In the
catalog that comes with the Lab (the listed one), they are allowed because the Lab's owners vouch for them. For any other
catalog, an item that uses this machine is blocked until you allow it.

The **Add** preview has a row **Uses this machine** with two choices: **Keep them blocked** (the default) or **Allow them to
use this machine**. You can change your mind later, in the catalog's menu: **Allow use of this machine** or **Stop allowing
use of this machine** (refused while one of its items is on). An allowance is yours alone, stored on this machine. An update that asks for more shows it in its preview, before it replaces
the copy.

## Private catalogs

A private catalog is a repository you have access to. Sign in with Git on this machine (`gh auth login`, an SSH key or a
credential helper), then add it by its address. The Lab asks Git as you; it never stores a password and never opens a
prompt. If Git cannot sign in, the row says **No access** and what to try. A catalog that anyone can read is **Public**, one
that needs a sign-in is **Private**; the Lab measures it with an anonymous request, and when in doubt says Private.

An employee's list of private catalogs can be given to the Lab without editing anything: `python3 lab-ui/catalog.py suggest
ADDRESS --name NAME` writes a suggestion, which then appears in **Library > Catalogs** as a catalog to add in one click.

## Your own items

You can make items for yourself and keep them in **Personal**: they live in `.lab/catalogs/local/` and no catalog ever
touches them. The **New** buttons of the lists create there. When one is ready to be shared, **Share** puts it in a catalog
you can write to, as a branch with a pull request link, and a copy stays in Personal until the catalog has it
(**Use the catalog version** then replaces the copy and keeps your settings and stars). What it includes that only this machine
has (your own add-ons and setups, or those of an imported playground) goes with it, in the same branch or copy: the dialog says
which under **Also shares**. What the catalog has already, from a share of before, is the one it includes there (**Uses the
catalog's**). What it includes of another catalog must be one that the target declares. An item of a catalog reads the settings named for its
catalog: the settings of its own that are named for another place (Personal, or the catalog it was copied or imported from, as
GRAVITEE_INTERNAL_GKO_LOG_LEVEL) are named for the target in the copy (TEAM_DEMOS_GKO_LOG_LEVEL), and the dialog says which under
**Renames**. Your values follow them: **Use the catalog version** gives them the new names. A playground is shared once it has started well on this machine, its seed ended without an error included: the
versions it started on go into the copy as its `tested` when its file declares none, and a playground that never ran is told to start first. A catalog you create with **New
catalog** is a folder on this machine that you edit freely.

**Copy to Personal**, in the menu of an item of a catalog, makes your own version of it, to change as you like. The copy keeps
the names of the original's services, which its neighbours and its seed call it by, and knows whose copy it is: neither blocks
the other, only one of the two runs at a time (the Lab says which to turn off), and **Back to the catalog version** drops your
copy, with your settings kept. A catalog's item that includes the original cannot be changed to include your copy: choose
**<item> and what uses it** to copy those too, up to the playgrounds, and your own items that used one of them then use the
copy. The copy takes the original's settings and secrets, not what its seed recorded.

## A playground in a file

A playground made for one person (a customer, a workshop) does not have to go into a catalog. **Export**, in the menu of a
playground (its row or its page), saves a `.zip` file that holds the playground and every setup and add-on it includes that the
other Lab may not have: everything but the items of the default catalog, which every Lab has, and which the file names as needed.
It never holds settings, keys or a license: the person who imports it adds their own license in the Lab UI.

What comes from a catalog of Git goes in the file too, so the person who receives it needs no access to that catalog. A
private one is named under **Private** before the file is made, and the button says **Export with private
content**: what comes from it leaves with the file. An image the other Lab will pull from a registry that asks for a login is
said under **Needs access**, with that registry: the person who receives the file needs access to it too.

Before the file is made, the export is judged as the import will be, with the rules a Lab applies to a catalog that is not
listed. What the other Lab would block is said, and keeps the file from being made: fix it first. What it would ask to use that
machine (a privileged container, say) is said too: the person who imports it is asked, as for any catalog.

On the other Lab, **Import** on the Playgrounds page takes the file: the preview is the one of an Add, and once imported the
playground's page opens. **Import to** chooses where it goes:

- **Personal**, the default: the playground and what it brings are yours, like what you make with the New buttons, and you edit
  them freely. An item of Personal with the same name is replaced (your settings stay) unless it is the same, which is then
  used. The Lab no longer judges what is in Personal: what uses this machine is shown before you import, and an item the preview
  says cannot run cannot go there.
- **Imported**: the file is kept as a catalog of its own, which the Lab keeps judging. An item that cannot run stays visible,
  blocked. Every file imported this way is under one row, **Imported**, on the Catalogs page, which lists each with its menu
  (**Remove**, the allowance of what uses this machine), and the lists say Imported as where their items come from.
  **Move to Personal**, in the menu of an imported item, makes it yours later: a playground alone, or with the setups and
  add-ons it brings that nothing else uses. What another item still uses stays in Imported, and the dialog says which and why.
  Its settings, secrets, star and what its seed recorded come along; the import whose items all moved is removed. As for an
  import, what goes to Personal is no longer judged: an item blocked only for what it takes of this machine moves once you
  allow it (**Allow and move**), and one blocked for anything else does not move. An item the file holds that this Lab has already, from the same catalog, is not imported a second time:
the playground uses this Lab's own, and the preview says so under **Uses yours**. A playground this Lab has already is not
imported at all. The Lab keeps the files under `.lab/imports/`, and the playground shows under a catalog named after it.
To import a newer file of the same playground, remove that catalog on the Catalogs page first: its files go with it.

## A playground for Docker Compose alone

**Export for Docker Compose**, in the menu of any playground (its row or its page), the default catalog's included, saves a
`.zip` file for someone who will not install the Lab: a folder that runs the playground with `docker compose up -d`.

| In the folder | What it is |
|---|---|
| `docker-compose.yml` | The one Compose file: the playground's services and those of the add-ons it brings, the products of the platform it uses, and what they depend on. A product it does not use is left out (the Alert Engine, often), with its link. The versions are those it was tested with |
| `.env.example` | The settings a person may change, each with what it is: the license, the platform's passwords, the playground's own settings. Copied to `.env`, which Compose reads |
| `README.md` | The guide, as Markdown any viewer shows, after what to have first (Docker, the license and its features, what the playground asks of the machine) and how to start it, with its consoles and accounts |
| The playground's folder, `addons/`, `seeder/` | What its services build or mount: its apps and seed, the add-ons' files, the seeder that runs the seed |
| `plugins/`, `certs/mtls/` | The plugins it asks for, from those this Lab downloaded (one it does not have is said under **Needs**), and demo certificates made for this file |

It never holds this machine's settings, keys or license. What only the Lab UI does is not in it: Start and Stop of one item,
Re-seed, the change of versions and the updates. An action button of the guide is the name of the button of the playground's
app; an account link is the user and its password. What comes from a private catalog leaves with the file, and the dialog says
so under **Private**, as an export does.

## Offline, privacy and starting over

With no network, the Lab keeps working with the copies it has: checks fail quietly and the rows say **Unreachable**.

The Lab's first start contacts the default catalog's address, once, and the Lab then checks the catalogs you added about
every few hours, each at its own address, with Git. Nothing about you is sent beyond what Git sends. To turn the background
check off for one catalog: its menu, **Stop checking automatically**. To start over, as if the Lab had just been installed:
`python3 lab-ui/catalog.py reset` (and `reset --empty` to have no catalog at all, which is a Lab with the platform only).

## Updating a Lab from before catalogs

An update moves nothing by surprise. Stop the platform, pull, and run the one command:

```bash
make stop
```

```bash
make update
```

`make update` is `git pull --ff-only` then `make catalogs`: it downloads the default catalog and moves your settings,
your stars and what you had in the old folders to the new layout. What moves, where, and why is told in one notice on the next
page load, **Lab updated**. Your own add-ons, setups and playgrounds (the ones under `local/`) become Personal. Anything you
changed inside a shared item is kept aside in `.lab/migration/kept`. `.lab/migration/undo.sh` puts the previous layout
back; a file that changed since is kept beside the restored one with the suffix `.after-update`.

Until you run it, `make start`, `make ps` and the other targets that read what is on say `This Lab was updated: run make
update to move your settings.` and stop. `make stop`, `make down` and `make reset` keep working.

## Errors and what to do

| The Lab says | What it means | What to do |
|---|---|---|
| Docker cannot share this folder | A folder catalog outside the folders Docker Desktop shares | Move it under your home folder |
| No access to the catalog | Git could not sign in | Sign in with Git on this machine, then **Try again** |
| Unreachable | No network, or the host is down | Try again later; the copy you have still works |
| Another catalog already uses the name … | Two catalogs claim one name | Remove one, or ask its owner to rename it |
| Rewritten | The catalog's history was rewritten | Read what changed; **Update anyway** if you trust it |
| Damaged | A file of the copy differs from what was installed | **Repair** |
| Needs a catalog | An item needs another catalog | Add it |
| Waiting for Lab level N | The catalog asks a newer Lab | Update the Lab |

If a line is not here, it names its own fix. `python3 lab-ui/catalog.py explain CODE` gives the long form of a rule code.
